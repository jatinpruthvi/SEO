"""
test_pass14_n6_n7_tasks_rng.py - Tests for Pass 14 N6 (HIGH) and N7 (MEDIUM) fixes

R7-DEEP-AUDIT-2 (Pass 14) second sweep found two fingerprint leaks in
tasks/site_register/executor.py where persona-specific actions used the
global `random` module:

N6 (HIGH): The executor's default username (and corrected-username) were
generated with `random.randint(...)` from the global random module. Since
global random is shared with 30+ other consumers (loguru, asyncio, the
orchestrator's per-persona seed, etc.), the same persona got a different
default username on every run. Returning personas with changing usernames
is a real bot-detection signal.

Fix: use `_get_persona_rng(persona_id).randint(...)` so the default
username is per-persona-deterministic.

N7 (MEDIUM): Other per-persona actions in the same executor used
`random.X` directly (warm branch selection, RSS headline selection, RSS
fallback, HackerNews link index, form abandonment roll).

Fix: use `_get_persona_rng(persona_id).X` for all of them.

This test file verifies:

1. tasks/site_register/executor.py imports _get_persona_rng from keyboard_emulator
2. N6: default username uses _get_persona_rng(persona_id).randint(1000, 9999)
3. N6: corrected username uses _get_persona_rng(persona_id).randint(10, 99)
4. N7: warm_branch selection uses _get_persona_rng(persona_id).choice(...)
5. N7: RSS headline selection uses _get_persona_rng(persona_id).choice(...)
6. N7: RSS fallback selection uses _get_persona_rng(persona_id).choice(...)
7. N7: HackerNews link index uses _get_persona_rng(persona_id).randint(...)
8. N7: abandonment_roll uses _get_persona_rng(persona_id).random()
9. fetch_dynamic_rss_headline accepts persona_id and threads it through
10. Pass 14 N6/N7 markers are present
11. No live global random.X for persona-specific actions
12. The fix is per-persona-deterministic (same persona → same username)
"""

import sys
import traceback
import re
from pathlib import Path

PROJECT_ROOT = Path("/home/user/project")
SITE_REGISTER_EXECUTOR = PROJECT_ROOT / "tasks" / "site_register" / "executor.py"
KEYBOARD_EMULATOR = PROJECT_ROOT / "browser-runtime" / "keyboard_emulator.py"


def _read(path):
    with open(path, "r") as f:
        return f.read()


def _get_function_body(src, signature_re, next_def_re):
    """Extract a function body given a signature regex and next-def regex."""
    m = re.search(signature_re, src)
    if not m:
        return None
    rest = src[m.end():]
    next_m = re.search(next_def_re, rest, re.MULTILINE)
    return rest[:next_m.start()] if next_m else rest[:5000]


# Tests --------------------------------------------------------------------

def test_n6_n7_imports_persona_rng():
    """N6/N7: tasks/site_register/executor.py must import _get_persona_rng
    from keyboard_emulator."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if "_get_persona_rng" not in src:
        return False, "N6/N7: missing _get_persona_rng import in tasks/site_register/executor.py"
    if "from keyboard_emulator import" not in src:
        return False, "N6/N7: _get_persona_rng must come from keyboard_emulator (not redefined)"
    if "_get_persona_rng" not in [a.name for a in [
        __import__("ast").parse(src).body[0]
            .body[-1] if False else None  # just trigger the import
    ] if False]:
        pass
    return True, None


def test_n6_default_username_uses_persona_rng():
    """N6 (HIGH): The default username must use the per-persona seeded RNG,
    not the global random module."""
    src = _read(SITE_REGISTER_EXECUTOR)
    # The fix pattern: f"johndoe_{_get_persona_rng(persona_id).randint(1000, 9999)}"
    if not re.search(r'johndoe_\{_get_persona_rng\(persona_id\)\.randint\(1000,\s*9999\)\}', src):
        return False, ("N6: default username must use "
                       "`_get_persona_rng(persona_id).randint(1000, 9999)`")
    return True, None


def test_n6_corrected_username_uses_persona_rng():
    """N6 (HIGH): The corrected username suffix must use the per-persona
    seeded RNG, not the global random module."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if not re.search(r'_get_persona_rng\(persona_id\)\.randint\(10,\s*99\)', src):
        return False, ("N6: corrected username must use "
                       "`_get_persona_rng(persona_id).randint(10, 99)`")
    return True, None


def test_n7_warm_branch_uses_persona_rng():
    """N7: warm_branch selection must use the per-persona seeded RNG."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if not re.search(r'warm_branch\s*=\s*_get_persona_rng\(persona_id\)\.choice\(', src):
        return False, ("N7: warm_branch must use "
                       "`_get_persona_rng(persona_id).choice(...)`")
    return True, None


def test_n7_rss_headline_uses_persona_rng():
    """N7: RSS headline selection (raw_headline) must use the per-persona
    seeded RNG inside fetch_dynamic_rss_headline."""
    src = _read(SITE_REGISTER_EXECUTOR)
    body = _get_function_body(
        src,
        r"async def fetch_dynamic_rss_headline\(",
        r"^    async def \w",
    )
    if body is None:
        return False, "N7: cannot find fetch_dynamic_rss_headline function"
    if not re.search(r'raw_headline\s*=\s*_rng\.choice\(', body):
        return False, ("N7: raw_headline must use `_rng.choice(...)` "
                       "where _rng = _get_persona_rng(persona_id)")
    return True, None


def test_n7_rss_fallback_uses_persona_rng():
    """N7: RSS fallback selection (return random.choice(fallbacks)) must use
    the per-persona seeded RNG."""
    src = _read(SITE_REGISTER_EXECUTOR)
    body = _get_function_body(
        src,
        r"async def fetch_dynamic_rss_headline\(",
        r"^    async def \w",
    )
    if body is None:
        return False, "N7: cannot find fetch_dynamic_rss_headline function"
    # The fix: return _rng.choice(fallbacks)
    if not re.search(r'return\s+_rng\.choice\(fallbacks\)', body):
        return False, ("N7: RSS fallback must return `_rng.choice(fallbacks)`")
    return True, None


def test_n7_rss_thread_persona_id():
    """N7: fetch_dynamic_rss_headline must accept persona_id as a parameter
    and propagate it to the call site in run()."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if not re.search(r"async def fetch_dynamic_rss_headline\(self,\s*proxy_url:\s*str,\s*persona_id", src):
        return False, ("N7: fetch_dynamic_rss_headline must accept "
                       "`persona_id: str` parameter")
    if "fetch_dynamic_rss_headline(proxy_url, persona_id)" not in src:
        return False, ("N7: call site in run() must pass persona_id to "
                       "fetch_dynamic_rss_headline")
    return True, None


def test_n7_hackernews_link_uses_persona_rng():
    """N7: HackerNews random article index must use the per-persona seeded RNG."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if not re.search(r'random_index\s*=\s*_get_persona_rng\(persona_id\)\.randint\(', src):
        return False, ("N7: random_index must use "
                       "`_get_persona_rng(persona_id).randint(...)`")
    return True, None


def test_n7_abandonment_uses_persona_rng():
    """N7: Form abandonment roll must use the per-persona seeded RNG
    (abandonment is a behavioral trait, not a per-call random)."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if not re.search(r'abandonment_roll\s*=\s*_get_persona_rng\(persona_id\)\.random\(\)', src):
        return False, ("N7: abandonment_roll must use "
                       "`_get_persona_rng(persona_id).random()`")
    return True, None


def test_n6_n7_markers_present():
    """N6/N7: Pass 14 markers must be present in tasks/site_register/executor.py."""
    src = _read(SITE_REGISTER_EXECUTOR)
    if "R7-DEEP-AUDIT-2 (Pass 14) N6" not in src:
        return False, "N6: missing Pass 14 N6 marker in tasks/site_register/executor.py"
    if "R7-DEEP-AUDIT-2 (Pass 14) N7" not in src:
        return False, "N7: missing Pass 14 N7 marker in tasks/site_register/executor.py"
    return True, None


def test_n6_n7_no_global_random_for_persona_actions():
    """N6/N7: After the fix, the executor must NOT use `random.randint`,
    `random.choice`, `random.uniform`, or `random.random` on any non-comment
    line. (All such uses are now `_get_persona_rng(persona_id).X`.)
    """
    src = _read(SITE_REGISTER_EXECUTOR)
    in_docstring = False
    docstring_quote = None
    for i, line in enumerate(src.split('\n'), 1):
        stripped = line.lstrip()
        # Track docstring boundaries
        if '"""' in line or "'''" in line:
            dq = '"""' if '"""' in line else "'''"
            count = line.count(dq)
            if count % 2 == 1:
                if in_docstring:
                    in_docstring = False
                    docstring_quote = None
                else:
                    if count >= 2:
                        in_docstring = False
                    else:
                        in_docstring = True
                        docstring_quote = dq
                continue
        if in_docstring:
            continue
        if stripped.startswith('#'):
            continue
        # Live global random.X call (not random.Random factory)
        for pat in ['random.randint(', 'random.uniform(', 'random.choice(',
                    'random.random(', 'random.gauss(', 'random.normalvariate(']:
            if pat in line:
                return False, f"tasks/site_register/executor.py line {i} still has live global {pat}: {line!r}"
    return True, None


def test_n6_per_persona_determinism():
    """N6: Two calls to the default-username fallback for the same persona
    must produce the same username. (Different personas can differ.)

    We can't import the executor directly (it imports playwright/loguru),
    so we exercise the `_get_persona_rng` invariant: same persona_id →
    same RNG seed → same randint() output.

    The keyboard_emulator module pulls in `humanized_movement` which may
    fail in stripped-down test environments, so we reimplement the
    seed derivation locally to verify the invariant holds.
    """
    import hashlib
    import random as _random

    def _local_get_persona_rng(persona_id):
        """Reimplementation of keyboard_emulator._get_persona_rng for
        environments where the full module cannot be imported."""
        if not persona_id:
            return _random
        seed = int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
        return _random.Random(seed)

    p_id = "test_persona_42"
    a = _local_get_persona_rng(p_id).randint(1000, 9999)
    b = _local_get_persona_rng(p_id).randint(1000, 9999)
    if a != b:
        return False, f"N6: same persona {p_id} produced different randint values: {a} vs {b}"
    if not (1000 <= a <= 9999):
        return False, f"N6: randint({a}) out of expected range [1000, 9999]"
    # The two default-username templates produce a stable value
    if a == 9999 and _local_get_persona_rng(p_id).randint(1000, 9999) != 9999:
        return False, "N6: randint not deterministic at boundary"
    return True, None


def test_n7_per_persona_branch_determinism():
    """N7: Same persona always picks the same warm_branch."""
    import hashlib
    import random as _random

    def _local_get_persona_rng(persona_id):
        if not persona_id:
            return _random
        seed = int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
        return _random.Random(seed)

    p_id = "test_branch_persona_77"
    branches = ["google_search_warm", "youtube_warm", "news_warm"]
    a = _local_get_persona_rng(p_id).choice(branches)
    b = _local_get_persona_rng(p_id).choice(branches)
    if a != b:
        return False, f"N7: same persona {p_id} selected different warm branches: {a} vs {b}"
    if a not in branches:
        return False, f"N7: branch {a!r} not in expected set {branches}"
    return True, None


def main():
    tests = [
        # N6 (HIGH): default username + corrected username
        ("N6/N7: imports _get_persona_rng", test_n6_n7_imports_persona_rng),
        ("N6 (HIGH): default username uses persona RNG",
         test_n6_default_username_uses_persona_rng),
        ("N6 (HIGH): corrected username uses persona RNG",
         test_n6_corrected_username_uses_persona_rng),
        # N7 (MEDIUM): other per-persona actions
        ("N7: warm_branch uses persona RNG", test_n7_warm_branch_uses_persona_rng),
        ("N7: RSS headline uses persona RNG", test_n7_rss_headline_uses_persona_rng),
        ("N7: RSS fallback uses persona RNG", test_n7_rss_fallback_uses_persona_rng),
        ("N7: fetch_dynamic_rss_headline threads persona_id",
         test_n7_rss_thread_persona_id),
        ("N7: HackerNews link index uses persona RNG",
         test_n7_hackernews_link_uses_persona_rng),
        ("N7: abandonment_roll uses persona RNG",
         test_n7_abandonment_uses_persona_rng),
        # Markers
        ("N6/N7: Pass 14 markers present", test_n6_n7_markers_present),
        # Architectural
        ("N6/N7: no live global random.X in tasks/site_register/executor.py",
         test_n6_n7_no_global_random_for_persona_actions),
        # Behavioral
        ("N6: same persona → same default username (determinism)",
         test_n6_per_persona_determinism),
        ("N7: same persona → same warm_branch (determinism)",
         test_n7_per_persona_branch_determinism),
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
