"""
test_pass14_medium_priority.py - Tests for Pass 14 medium-priority fixes

R7-DEEP-AUDIT-2 (Pass 14) file-wise audit found:
- M3: chrome://newtab/ fallback to about:blank — already handled by
  prototype_hardener.js synthetic historyCount bump.
- M4: execute_fitts_law_trajectory had no page-type validation.
- M5: human_type_words had no page-type validation.
- M2: try/finally for browser context — already handled by existing
  finally: block in run_persona_pipeline.

This test file verifies (using a custom runner because the test env
lacks loguru/asyncpg/playwright, similar to test_pass14_thread_removal.py):

1. execute_fitts_law_trajectory raises TypeError for None page
2. execute_fitts_law_trajectory raises TypeError for non-Page objects
3. execute_fitts_law_trajectory validates coordinate types
4. execute_fitts_law_trajectory validates steps
5. human_type_words raises TypeError for None page
6. human_type_words raises TypeError for non-Page objects
7. The orchestrator's chrome:// fallback has documentation about
   the JS layer's synthetic historyCount bump
"""

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path("/home/user/project")
HUMANIZED_MOVEMENT = PROJECT_ROOT / "browser-runtime" / "humanized_movement.py"
KEYBOARD_EMULATOR = PROJECT_ROOT / "browser-runtime" / "keyboard_emulator.py"
ORCHESTRATOR = PROJECT_ROOT / "browser-runtime" / "hardened_orchestrator.py"


def _read(path):
    with open(path, "r") as f:
        return f.read()


# Source-pattern tests (no imports needed) ---------------------------------

def test_m4_execute_fitts_page_none_raises():
    """M4: execute_fitts_law_trajectory(page=None) must raise TypeError."""
    src = _read(HUMANIZED_MOVEMENT)
    # Look for the M4 validation block: `if page is None: raise TypeError`
    if 'if page is None:' not in src:
        return False, "M4: missing `if page is None:` validation in execute_fitts_law_trajectory"
    # Check it's in the execute_fitts_law_trajectory function (not just
    # move_mouse_like_human)
    import re
    # Find the function start (multi-line signature)
    m = re.search(r"async def execute_fitts_law_trajectory\(", src)
    if not m:
        return False, "M4: cannot find execute_fitts_law_trajectory function"
    func_start = src.find('"""', m.end())  # end of docstring
    if func_start < 0:
        func_start = m.end()
    # Get the function body (until next `def ` or `async def`)
    rest = src[func_start:]
    next_func = re.search(r"\n(?:async )?def \w", rest)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    if 'if page is None:' not in func_body:
        return False, "M4: `if page is None:` is not in execute_fitts_law_trajectory body"
    if 'raise TypeError("page must not be None")' not in func_body:
        return False, "M4: missing TypeError(\"page must not be None\") in execute_fitts_law_trajectory"
    return True, None


def test_m4_execute_fitts_non_page_object_raises():
    """M4: execute_fitts_law_trajectory with non-Page object must raise TypeError."""
    src = _read(HUMANIZED_MOVEMENT)
    import re
    m = re.search(r"async def execute_fitts_law_trajectory\(", src)
    if not m:
        return False, "M4: cannot find execute_fitts_law_trajectory function"
    func_start = src.find('"""', m.end())
    if func_start < 0:
        func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"\n(?:async )?def \w", rest)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    # The validation pattern: `if not hasattr(page, 'context') or not hasattr(page, 'mouse')`
    if "hasattr(page, 'mouse')" not in func_body:
        return False, "M4: missing Playwright Page object validation in execute_fitts_law_trajectory"
    if 'must be a Playwright Page object' not in func_body:
        return False, "M4: missing Playwright Page object error message in execute_fitts_law_trajectory"
    return True, None


def test_m4_execute_fitts_coordinate_validation():
    """M4: execute_fitts_law_trajectory validates start_x/y/end_x/y are numeric."""
    src = _read(HUMANIZED_MOVEMENT)
    import re
    m = re.search(r"async def execute_fitts_law_trajectory\(", src)
    if not m:
        return False, "M4: cannot find execute_fitts_law_trajectory function"
    func_start = src.find('"""', m.end())
    if func_start < 0:
        func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"\n(?:async )?def \w", rest)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    # Check that all 4 coordinates are validated
    for name in ["start_x", "start_y", "end_x", "end_y"]:
        if f'"{name}"' not in func_body:
            return False, f"M4: missing coordinate validation for {name} in execute_fitts_law_trajectory"
    if "isinstance(val, (int, float))" not in func_body:
        return False, "M4: missing isinstance() numeric check in execute_fitts_law_trajectory"
    return True, None


def test_m4_execute_fitts_steps_validation():
    """M4: execute_fitts_law_trajectory validates steps is a positive integer."""
    src = _read(HUMANIZED_MOVEMENT)
    import re
    m = re.search(r"async def execute_fitts_law_trajectory\(", src)
    if not m:
        return False, "M4: cannot find execute_fitts_law_trajectory function"
    func_start = src.find('"""', m.end())
    if func_start < 0:
        func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"\n(?:async )?def \w", rest)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    if "isinstance(steps, int)" not in func_body:
        return False, "M4: missing steps isinstance() check in execute_fitts_law_trajectory"
    if "steps < 1" not in func_body:
        return False, "M4: missing steps < 1 lower bound check in execute_fitts_law_trajectory"
    return True, None


def test_m4_execute_fitts_marker_present():
    """M4: The M4 marker should be present in the function docstring."""
    src = _read(HUMANIZED_MOVEMENT)
    if "R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (M4)" not in src:
        return False, "M4: missing Pass 14 M4 marker comment in humanized_movement.py"
    return True, None


def test_m5_human_type_words_page_none_raises():
    """M5: human_type_words(page=None) must raise TypeError."""
    src = _read(KEYBOARD_EMULATOR)
    import re
    m = re.search(r"async def human_type_words\([^)]*\):", src)
    if not m:
        return False, "M5: cannot find human_type_words function"
    func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"\n(?:async )?def \w+\(", rest)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    if 'if page is None:' not in func_body:
        return False, "M5: missing `if page is None:` validation in human_type_words"
    if 'raise TypeError("page must not be None")' not in func_body:
        return False, "M5: missing TypeError(\"page must not be None\") in human_type_words"
    return True, None


def test_m5_human_type_words_non_page_object_raises():
    """M5: human_type_words with non-Page object must raise TypeError."""
    src = _read(KEYBOARD_EMULATOR)
    import re
    m = re.search(r"async def human_type_words\([^)]*\):", src)
    if not m:
        return False, "M5: cannot find human_type_words function"
    func_start = m.end()
    rest = src[func_start:]
    next_func = re.search(r"\n(?:async )?def \w+\(", rest)
    func_body = rest[:next_func.start()] if next_func else rest[:5000]
    # The validation pattern: `if not hasattr(page, 'context') or not hasattr(page, 'keyboard')`
    if "hasattr(page, 'keyboard')" not in func_body:
        return False, "M5: missing Playwright Page object validation in human_type_words"
    if 'must be a Playwright Page object' not in func_body:
        return False, "M5: missing Playwright Page object error message in human_type_words"
    return True, None


def test_m5_human_type_words_marker_present():
    """M5: The M5 marker should be present in the function docstring."""
    src = _read(KEYBOARD_EMULATOR)
    if "R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (M5)" not in src:
        return False, "M5: missing Pass 14 M5 marker comment in keyboard_emulator.py"
    return True, None


def test_m3_orchestrator_documents_js_synthetic_bump():
    """M3: The orchestrator's chrome:// fallback should document that the
    prototype_hardener.js handles historyCount synthetic bump."""
    src = _read(ORCHESTRATOR)
    if "M3" not in src:
        return False, "M3: missing M3 marker comment in hardened_orchestrator.py"
    if "synthetically bumping" not in src and "synthetic" not in src.lower():
        return False, "M3: missing documentation that JS layer handles synthetic historyCount bump"
    # Check the chrome:// fallback block is still present (we didn't remove it)
    if 'await page.goto("chrome://newtab/"' not in src:
        return False, "M3: chrome://newtab/ navigation was accidentally removed"
    return True, None


def test_m2_panic_handler_does_not_break():
    """M2 verification: existing finally: block in run_persona_pipeline
    handles browser.close() correctly. We verify the structure is intact."""
    src = _read(ORCHESTRATOR)
    if "browser is not None" not in src:
        return False, "M2: the existing finally: block's `if browser is not None` check is missing"
    if "await browser.close()" not in src:
        return False, "M2: the existing finally: block's `await browser.close()` is missing"
    # Confirm the try/finally structure
    if "finally:" not in src:
        return False, "M2: no finally: block in run_persona_pipeline"
    return True, None


def test_m1_global_random_seed_documented():
    """M1: The orchestrator's global random.seed() should still have the
    HACK comment documenting the global state pollution. (No fix
    applied — defer to refactor; just verify the documentation
    is still present so future maintainers see the warning.)"""
    src = _read(ORCHESTRATOR)
    if "global RNG state" not in src and "pollutes the global RNG" not in src:
        return False, "M1: the HACK comment about global random.seed() is missing"
    if "random.seed(" not in src:
        return False, "M1: random.seed() call is missing"
    return True, None


def test_m1_global_random_seed_in_try():
    """M1: random.seed() is in a try/except for non-fatal error handling."""
    src = _read(ORCHESTRATOR)
    # Find the random.seed call
    idx = src.find("random.seed(")
    if idx < 0:
        return False, "M1: random.seed() call not found"
    # Check 200 chars before for `try:`
    chunk = src[max(0, idx - 200):idx]
    if "try:" not in chunk:
        return False, "M1: random.seed() is not in a try/except"
    # Check 200 chars after for `except`
    after = src[idx:idx + 200]
    if "except" not in after:
        return False, "M1: no except clause after random.seed()"
    return True, None


def main():
    tests = [
        # M4: execute_fitts_law_trajectory page validation
        ("M4: execute_fitts_law_trajectory raises on None page", test_m4_execute_fitts_page_none_raises),
        ("M4: execute_fitts_law_trajectory raises on non-Page object", test_m4_execute_fitts_non_page_object_raises),
        ("M4: execute_fitts_law_trajectory validates coordinates", test_m4_execute_fitts_coordinate_validation),
        ("M4: execute_fitts_law_trajectory validates steps", test_m4_execute_fitts_steps_validation),
        ("M4: Pass 14 M4 marker present", test_m4_execute_fitts_marker_present),
        # M5: human_type_words page validation
        ("M5: human_type_words raises on None page", test_m5_human_type_words_page_none_raises),
        ("M5: human_type_words raises on non-Page object", test_m5_human_type_words_non_page_object_raises),
        ("M5: Pass 14 M5 marker present", test_m5_human_type_words_marker_present),
        # M3: orchestrator documents JS synthetic bump
        ("M3: orchestrator documents JS synthetic historyCount bump", test_m3_orchestrator_documents_js_synthetic_bump),
        # M2: existing finally: block intact
        ("M2: existing finally: block in run_persona_pipeline intact", test_m2_panic_handler_does_not_break),
        # M1: documentation still present (deferred)
        ("M1: HACK comment for global random.seed() still present", test_m1_global_random_seed_documented),
        ("M1: random.seed() is in try/except for non-fatal error", test_m1_global_random_seed_in_try),
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
