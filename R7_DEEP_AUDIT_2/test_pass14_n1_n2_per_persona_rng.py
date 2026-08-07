"""
test_pass14_n1_n2_per_persona_rng.py - Tests for Pass 14 N1 (HIGH) and N2 (MEDIUM) fixes

R7-DEEP-AUDIT-2 (Pass 14) final sweep found two real fingerprint leaks
where persona-specific actions used the global `random` module:

N1 (HIGH): hci_reading_model.py used `random.X` for:
- Scroll-tremor micro-jitter (line 124)
- Paragraph selection for text-highlight fidget (line 492)
- Drag start/end coordinates (lines 499, 501-504)
- Sleep durations (lines 506, 510, 514, 520)
- Dismiss click coordinates (lines 527-528)

N2 (MEDIUM): keyboard_emulator.py used `random.X` for:
- Typo generation via _get_adjacent_key (line 264, called from 817, 865)
- Polyphonic key rollover timing (lines 730, 738)

Fix: Replace global `random.X` with per-persona seeded RNGs
(self.rng in hci_reading_model, _get_persona_rng(persona_id) in
keyboard_emulator).

This test file verifies (using a custom runner like other Pass 14 tests):

1. hci_reading_model.py no longer uses global random for persona actions
2. hci_reading_model.py uses self.rng for paragraph selection
3. hci_reading_model.py uses _tremor_rng for scroll tremor
4. keyboard_emulator.py uses _get_persona_rng for _get_adjacent_key
5. keyboard_emulator.py uses _get_persona_rng for polyphonic rollover
6. Both files have the Pass 14 N1/N2 markers
"""

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path("/home/user/project")
HCI_READING_MODEL = PROJECT_ROOT / "browser-runtime" / "hci_reading_model.py"
KEYBOARD_EMULATOR = PROJECT_ROOT / "browser-runtime" / "keyboard_emulator.py"


def _read(path):
    with open(path, "r") as f:
        return f.read()


# Source-pattern tests (no imports needed) ---------------------------------

def test_n1_hci_no_global_random_in_text_selection():
    """N1: hci_reading_model.py's execute_text_selection_fidget must not
    use the global random module for persona-specific actions.

    Verified by looking at the function body: every persona-specific
    action must use self.rng or _tremor_rng.
    """
    src = _read(HCI_READING_MODEL)
    import re
    m = re.search(r"async def execute_text_selection_fidget\(", src)
    if not m:
        return False, "N1: cannot find execute_text_selection_fidget function"
    func_start = m.end()
    # Get function body (until next def at same indent)
    rest = src[func_start:]
    # Find next method/function at indent 4 (4 spaces)
    next_func = re.search(r"^    (?:async )?def \w", rest, re.MULTILINE)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    # The function should NOT use global random in persona-specific actions
    # Look for `random.randint(`, `random.uniform(`, `random.choice(` on
    # non-comment, non-docstring lines
    for i, line in enumerate(func_body.split('\n'), 1):
        stripped = line.lstrip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if 'random.randint(' in line and 'self.rng' not in line:
            return False, f"N1: global random.randint found in execute_text_selection_fidget body line {i}: {line!r}"
        if 'random.uniform(' in line and 'self.rng' not in line:
            return False, f"N1: global random.uniform found in execute_text_selection_fidget body line {i}: {line!r}"
        if 'random.choice(' in line and 'self.rng' not in line:
            return False, f"N1: global random.choice found in execute_text_selection_fidget body line {i}: {line!r}"
    return True, None


def test_n1_hci_uses_self_rng_in_text_selection():
    """N1: execute_text_selection_fidget must use self.rng.X for paragraph
    selection, drag coordinates, and sleep durations.
    """
    src = _read(HCI_READING_MODEL)
    import re
    m = re.search(r"async def execute_text_selection_fidget\(", src)
    if not m:
        return False, "N1: cannot find execute_text_selection_fidget function"
    func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"^    (?:async )?def \w", rest, re.MULTILINE)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    # Must have self.rng.integers for paragraph selection
    if 'self.rng.integers(0, min(count - 1, 5) + 1)' not in func_body:
        return False, "N1: paragraph selection should use self.rng.integers(0, min(count-1, 5) + 1)"
    # Must have self.rng.integers for drag_start_x
    if 'self.rng.integers(10, 30 + 1)' not in func_body:
        return False, "N1: drag_start_x should use self.rng.integers(10, 30 + 1)"
    if 'self.rng.integers(50, 120 + 1)' not in func_body:
        return False, "N1: drag_end_x should use self.rng.integers(50, 120 + 1)"
    if 'self.rng.uniform(-2, 2)' not in func_body:
        return False, "N1: drag_end_y should use self.rng.uniform(-2, 2)"
    return True, None


def test_n1_hci_uses_tremor_rng_in_scroll():
    """N1: execute_smooth_momentum_scroll must use a per-persona
    _tremor_rng, not the global random module, for tremor_x.
    """
    src = _read(HCI_READING_MODEL)
    # The fix introduces a `_tremor_rng` instance seeded by persona_id+scroll_y
    if '_tremor_rng' not in src:
        return False, "N1: missing _tremor_rng (per-persona seeded RNG for scroll tremor)"
    if '_tremor_rng.choice([-1, 0, 1])' not in src:
        return False, "N1: tremor_x should use _tremor_rng.choice([-1, 0, 1])"
    # And the global random.choice for [-1, 0, 1] must NOT be in live code
    # (it's only in comments/docs)
    import re
    for i, line in enumerate(src.split('\n'), 1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        # The line `tremor_x = random.choice([-1, 0, 1])` must not be live
        if re.match(r'^\s*tremor_x\s*=\s*random\.choice\(', line):
            return False, f"N1: live code at line {i} still uses random.choice for tremor_x: {line!r}"
    return True, None


def test_n1_marker_present():
    """N1: Pass 14 N1 marker must be present in hci_reading_model.py."""
    src = _read(HCI_READING_MODEL)
    if "R7-DEEP-AUDIT-2 (Pass 14) HIGH (N1)" not in src:
        return False, "N1: missing Pass 14 HIGH (N1) marker in hci_reading_model.py"
    return True, None


def test_n2_keyboard_uses_persona_rng_for_typos():
    """N2: _get_adjacent_key must use a per-persona seeded RNG, not
    the global random module (when persona_id is provided).
    """
    src = _read(KEYBOARD_EMULATOR)
    # The fix: _get_adjacent_key accepts persona_id and uses _get_persona_rng
    import re
    m = re.search(r"def _get_adjacent_key\(", src)
    if not m:
        return False, "N2: cannot find _get_adjacent_key function"
    func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"^def \w", rest, re.MULTILINE)
    func_body = rest[:next_func.start()] if next_func else rest[:2000]
    # Must accept persona_id parameter
    if 'persona_id: str = None' not in func_body:
        return False, "N2: _get_adjacent_key must accept persona_id parameter"
    # Must use _get_persona_rng
    if '_get_persona_rng(persona_id).choice' not in func_body:
        return False, "N2: _get_adjacent_key must use _get_persona_rng(persona_id).choice"
    return True, None


def test_n2_keyboard_calls_pass_persona_id():
    """N2: Both call sites of _get_adjacent_key must pass persona_id."""
    src = _read(KEYBOARD_EMULATOR)
    # We need at least 2 occurrences of `persona_id=persona_id` near
    # `_get_adjacent_key` calls
    count = src.count("_get_adjacent_key(char, persona_id=persona_id)")
    if count < 2:
        return False, f"N2: expected 2 _get_adjacent_key call sites to pass persona_id, found {count}"
    return True, None


def test_n2_keyboard_uses_persona_rng_for_rollover():
    """N2: The polyphonic key rollover must use a per-persona seeded RNG
    for the rollover chance and overlap offset.
    """
    src = _read(KEYBOARD_EMULATOR)
    # The fix: profile.rng.random() instead of random.random() for rollover chance
    if 'profile.rng.random() < rollover_chance' not in src:
        return False, "N2: polyphonic rollover must use profile.rng.random()"
    # The fix: _get_persona_rng(persona_id).uniform(0.30, 0.70) for overlap offset
    if '_get_persona_rng(persona_id).uniform(0.30, 0.70)' not in src:
        return False, "N2: overlap offset must use _get_persona_rng(persona_id).uniform(0.30, 0.70)"
    return True, None


def test_n2_marker_present():
    """N2: Pass 14 N2 marker must be present in keyboard_emulator.py."""
    src = _read(KEYBOARD_EMULATOR)
    if "R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (N2)" not in src:
        return False, "N2: missing Pass 14 MEDIUM (N2) marker in keyboard_emulator.py"
    return True, None


def test_n1_n2_architectural_invariance():
    """N1 + N2: After the fix, NEITHER file should have a `random.randint`,
    `random.uniform`, or `random.choice` call on a non-comment line that
    is NOT in a defensive `if hasattr(...)` check, a documented fallback,
    or inside a docstring.
    """
    for path, name in [(HCI_READING_MODEL, "hci_reading_model"),
                        (KEYBOARD_EMULATOR, "keyboard_emulator")]:
        src = _read(path)
        # Track whether we're inside a docstring
        in_docstring = False
        docstring_quote = None
        for i, line in enumerate(src.split('\n'), 1):
            stripped = line.lstrip()
            if stripped.startswith('#'):
                continue
            # Track docstring boundaries (triple-quoted strings)
            if '"""' in line or "'''" in line:
                # Count unescaped triple-quote occurrences
                quotes = line.count('"""') + line.count("'''")
                if quotes % 2 == 1:
                    # Entering or exiting a docstring
                    if in_docstring:
                        # Exiting
                        in_docstring = False
                        docstring_quote = None
                    else:
                        # Entering (single-line docstring or start of multi-line)
                        if quotes >= 2:
                            # Single-line docstring (e.g., `"""text"""`)
                            in_docstring = False
                        else:
                            in_docstring = True
                            docstring_quote = '"""' if '"""' in line else "'''"
                    continue
            if in_docstring:
                continue
            # Skip defensive `if hasattr(...)` fallbacks
            if 'hasattr(' in line and 'else' in line:
                continue
            # Skip `random.Random(seed)` instantiations (these are
            # per-persona RNG factories, not calls)
            if 'random.Random(' in line:
                continue
            # Skip the documented fallback in _get_adjacent_key
            # (the line `return random.choice(...)` is the documented
            # fallback when persona_id is None — a defensive design
            # for legacy call sites)
            if 'return random.choice(' in line:
                # Look at the previous 5 lines for `if persona_id:`
                prev_block = '\n'.join(src.split('\n')[max(0, i-6):i])
                if 'if persona_id:' in prev_block and 'else' not in prev_block:
                    # Documented fallback pattern: `if persona_id: return ...; return fallback`
                    # Only valid if there's NO `else` clause (which would mean a real if/else)
                    continue
                return False, f"{name} line {i} has `return random.choice(...)` without documented fallback: {line!r}"
            # Detect live calls to global random module
            for pat in ['random.randint(', 'random.uniform(', 'random.choice(',
                        'random.gauss(', 'random.normalvariate(']:
                if pat in line and 'self.rng' not in line and '_get_persona_rng' not in line:
                    return False, f"{name} line {i} still has live global {pat}: {line!r}"
    return True, None


def main():
    tests = [
        # N1: hci_reading_model.py
        ("N1: hci_reading_model.py no global random in text selection",
         test_n1_hci_no_global_random_in_text_selection),
        ("N1: hci_reading_model.py uses self.rng in text selection",
         test_n1_hci_uses_self_rng_in_text_selection),
        ("N1: hci_reading_model.py uses _tremor_rng in scroll",
         test_n1_hci_uses_tremor_rng_in_scroll),
        ("N1: Pass 14 N1 marker present", test_n1_marker_present),
        # N2: keyboard_emulator.py
        ("N2: keyboard_emulator.py _get_adjacent_key uses persona RNG",
         test_n2_keyboard_uses_persona_rng_for_typos),
        ("N2: keyboard_emulator.py call sites pass persona_id",
         test_n2_keyboard_calls_pass_persona_id),
        ("N2: keyboard_emulator.py polyphonic rollover uses persona RNG",
         test_n2_keyboard_uses_persona_rng_for_rollover),
        ("N2: Pass 14 N2 marker present", test_n2_marker_present),
        # Cross-file architectural check
        ("N1+N2: no live global random.X for persona actions",
         test_n1_n2_architectural_invariance),
    ]
    results = []
    for name, test_fn in tests:
        print(f"\n[TEST] {name}")
        try:
            ok, msg = test_fn()
            results.append((name, ok, msg))
            if ok:
                print(f"  ✅ OK")
            else:
                print(f"  ❌ FAIL: {msg}")
        except Exception as e:
            print(f"  ❌ EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append((name, False, str(e)))
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} tests passed")
    for name, ok, _ in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
