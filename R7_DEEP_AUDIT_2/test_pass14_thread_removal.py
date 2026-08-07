"""
test_pass14_thread_removal.py - Tests for Pass 14 H1 fix

R7-DEEP-AUDIT-2 (Pass 14) file-wise audit found that
humanized_movement.py used `threading.Lock` in two places
(`_persona_np_rngs_lock`, `_profile_cache_lock`) despite
running under a single-threaded asyncio event loop. The
locks were dead code and a deadlock hazard if a future
maintainer moves behavioral code to a ThreadPoolExecutor.

This test file verifies (using a custom runner to match
test_pass14_issues.py style — the orchestrator uses a custom
runner because unittest can't import humanized_movement in
test envs that lack loguru/playwright):

1. `import threading` is removed from humanized_movement.py
2. `_profile_cache_lock` is removed (and the `with` block)
3. `_persona_np_rngs_lock` is removed (and the `with` block)
4. Changelog comments reference HM70 / Pass 14
5. The FIFO eviction (cap at 1000 entries) is preserved
6. All comments and docstrings document the removal correctly
"""

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path("/home/user/project")
HUMANIZED_MOVEMENT = PROJECT_ROOT / "browser-runtime" / "humanized_movement.py"


def _read(path):
    with open(path, "r") as f:
        return f.read()


# Static (no-import) tests: just read the source file and verify
# the pattern is present/absent. These are robust to missing
# dependencies like loguru.

def test_h1_threading_import_removed():
    """H1: `import threading` must be removed from humanized_movement.py."""
    src = _read(HUMANIZED_MOVEMENT)
    # The line should be `^import threading\s*(#.*)?$`
    import re
    if re.search(r"^import threading\s*(#.*)?$", src, re.MULTILINE):
        return False, "humanized_movement.py still imports threading (Bug H1)"
    return True, None


def test_h1_profile_cache_lock_removed():
    """H1: `_profile_cache_lock = threading.Lock()` is removed from live code."""
    src = _read(HUMANIZED_MOVEMENT)
    # Check the exact assignment is gone (it's now in comments only,
    # which we exclude by checking it's not in a non-comment context)
    import re
    # Look for `=` assignment on a non-comment line
    for i, line in enumerate(src.split('\n'), 1):
        if line.startswith('#'):
            continue
        if '_profile_cache_lock = threading.Lock()' in line:
            return False, f"Live code at line {i} still has _profile_cache_lock = threading.Lock(): {line!r}"
        if line.strip() == "with _profile_cache_lock:":
            return False, f"Live code at line {i} still has `with _profile_cache_lock:` block: {line!r}"
    return True, None


def test_h1_persona_np_rngs_lock_removed():
    """H1: `_persona_np_rngs_lock = threading.Lock()` is removed from live code."""
    src = _read(HUMANIZED_MOVEMENT)
    for i, line in enumerate(src.split('\n'), 1):
        if line.startswith('#'):
            continue
        if '_persona_np_rngs_lock = threading.Lock()' in line:
            return False, f"Live code at line {i} still has _persona_np_rngs_lock = threading.Lock(): {line!r}"
        if line.strip() == "with _persona_np_rngs_lock:":
            return False, f"Live code at line {i} still has `with _persona_np_rngs_lock:` block: {line!r}"
    return True, None


def test_h1_changelog_hm70_present():
    """H1: The HM70 changelog entry and Pass 14 marker must be present."""
    src = _read(HUMANIZED_MOVEMENT)
    if "HM70" not in src:
        return False, "Missing HM70 changelog entry"
    if "R7-DEEP-AUDIT-2 (Pass 14) HIGH" not in src:
        return False, "Missing Pass 14 HIGH marker"
    return True, None


def test_h1_superseded_hm9_hm36():
    """HM9 and HM36 should be marked as SUPERSEDED in the changelog."""
    src = _read(HUMANIZED_MOVEMENT)
    if "HM9  - SUPERSEDED by HM70" not in src:
        return False, "Missing HM9 SUPERSEDED marker"
    if "HM36 - SUPERSEDED by HM70" not in src:
        return False, "Missing HM36 SUPERSEDED marker"
    return True, None


def test_h1_fifo_eviction_preserved():
    """The 1000-entry FIFO eviction must be preserved (real protection)."""
    src = _read(HUMANIZED_MOVEMENT)
    if "len(_profile_cache) >= 1000" not in src:
        return False, "FIFO eviction at 1000 entries is missing"
    if "_profile_cache.pop(first_key)" not in src:
        return False, "FIFO eviction logic is missing"
    return True, None


def test_h1_changelog_lists_all_fixes():
    """HM70 changelog should mention: import removal, both lock removals, deadlock hazard."""
    src = _read(HUMANIZED_MOVEMENT)
    if "import threading" not in src:
        return False, "HM70 changelog doesn't mention import threading"
    if "_persona_np_rngs_lock" not in src:
        return False, "HM70 changelog doesn't mention _persona_np_rngs_lock"
    if "_profile_cache_lock" not in src:
        return False, "HM70 changelog doesn't mention _profile_cache_lock"
    if "deadlock" not in src.lower():
        return False, "HM70 changelog doesn't mention deadlock hazard"
    return True, None


def test_h1_marker_count():
    """The Pass 14 marker should appear in 4+ places (changelog + multiple comments)."""
    src = _read(HUMANIZED_MOVEMENT)
    count = src.count("R7-DEEP-AUDIT-2 (Pass 14) HIGH")
    if count < 4:
        return False, f"Expected 4+ Pass 14 HIGH markers, found {count}"
    return True, None


def test_h1_no_live_threading_lock_creation():
    """No live code should create a new `threading.Lock()` (i.e., the runtime creation)."""
    src = _read(HUMANIZED_MOVEMENT)
    # We allow the strings to appear in comments/docs but NOT in
    # `threading.Lock(` (with the open-paren) which would indicate
    # actual instantiation. The previous code had:
    #   _profile_cache_lock = threading.Lock()
    #   _persona_np_rngs_lock = threading.Lock()
    # We verify the function-call form is gone from non-comment lines.
    for i, line in enumerate(src.split('\n'), 1):
        if line.startswith('#'):
            continue
        if 'threading.Lock(' in line:
            return False, f"Live code at line {i} instantiates threading.Lock(): {line!r}"
    return True, None


def test_h1_import_section_clean():
    """The import block (between docstring and first def) should not have
    any threading references in non-comment lines."""
    src = _read(HUMANIZED_MOVEMENT)
    lines = src.split('\n')
    # Find the first `def ` line; imports are above it
    end = 0
    for i, line in enumerate(lines):
        if line.startswith('def ') or line.startswith('class '):
            end = i
            break
    # Walk the import region and check non-comment lines for 'threading'
    for i in range(end):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        if 'import threading' in line:
            return False, f"Line {i+1} still has `import threading`: {line!r}"
        if 'threading' in line and not stripped.startswith('#'):
            # Inline non-comment reference to threading in the import region
            return False, f"Line {i+1} references threading outside a comment: {line!r}"
    return True, None


def main():
    tests = [
        ("H1: threading import removed", test_h1_threading_import_removed),
        ("H1: _profile_cache_lock removed (live code)", test_h1_profile_cache_lock_removed),
        ("H1: _persona_np_rngs_lock removed (live code)", test_h1_persona_np_rngs_lock_removed),
        ("H1: HM70 changelog + Pass 14 marker", test_h1_changelog_hm70_present),
        ("H1: HM9 and HM36 marked SUPERSEDED", test_h1_superseded_hm9_hm36),
        ("H1: FIFO eviction at 1000 entries preserved", test_h1_fifo_eviction_preserved),
        ("H1: HM70 changelog lists all 3 fix points", test_h1_changelog_lists_all_fixes),
        ("H1: Pass 14 marker appears 4+ times", test_h1_marker_count),
        ("H1: No live `threading.Lock(` instantiation", test_h1_no_live_threading_lock_creation),
        ("H1: Import block has no threading references", test_h1_import_section_clean),
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
    for name, ok, msg in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
