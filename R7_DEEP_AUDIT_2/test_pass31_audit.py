#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 31: Input validation audit.

Hunts for input validation issues:
1. Missing type checks on function parameters
2. Missing range checks on numeric parameters
3. Missing validation of string parameters
4. Missing validation of list/dict parameters
5. Trusting DB data without validation
6. Missing validation of env var values
7. Unsafe type coercion
8. Missing validation of file paths
"""
import os
import sys
import re
import ast
import subprocess

sys.path.insert(0, "/home/user/project/browser-runtime")

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}  {detail}")


print("=" * 60)
print("R7-DEEP-AUDIT-2 Pass 31: Input validation audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# IV-1: Type checks on public function parameters
# ============================================================================
print("\n[TEST] IV-1: Type checks on public function parameters")

# Count isinstance checks and type-hinted parameters
isinstance_count = sum(src.count("isinstance(") for src in _FILES.values())
check(
    f"isinstance checks: {isinstance_count}",
    isinstance_count >= 50,
    f"type validation is used throughout"
)


# ============================================================================
# IV-2: Range checks on numeric parameters
# ============================================================================
print("\n[TEST] IV-2: Range checks on numeric parameters")

# Look for patterns like `if x < 0` or `if x > max`
range_checks = 0
for src in _FILES.values():
    range_checks += len(re.findall(r'if\s+\w+\s*[<>]=?\s*\d+', src))
    range_checks += len(re.findall(r'raise ValueError.*[<>=]', src))
check(
    f"Range checks: {range_checks}",
    range_checks >= 20,
    f"numeric parameters should have range validation"
)


# ============================================================================
# IV-3: String parameter validation
# ============================================================================
print("\n[TEST] IV-3: String parameter validation")

# Look for string validation patterns
string_checks = 0
for src in _FILES.values():
    # Look for isinstance(..., str) patterns
    for m in re.finditer(r'isinstance\s*\(\s*\w+\s*,\s*str\s*\)', src):
        string_checks += 1
    # Look for raise ValueError with string context
    string_checks += len(re.findall(r'raise ValueError\([^)]*["\']', src))
check(
    f"String validation: {string_checks} patterns",
    string_checks >= 5,
    f"string parameters should be validated"
)


# ============================================================================
# IV-4: None checks before attribute access
# ============================================================================
print("\n[TEST] IV-4: None checks before attribute access")

none_checks = 0
for src in _FILES.values():
    none_checks += src.count("is None")
    none_checks += src.count("is not None")
check(
    f"None checks: {none_checks}",
    none_checks >= 100,
    f"None checks prevent AttributeError crashes"
)


# ============================================================================
# IV-5: DB data validation
# ============================================================================
print("\n[TEST] IV-5: DB data is validated after fetch")

orch_src = _FILES.get("hardened_orchestrator.py", "")
# Check for fp validation after DB fetch
has_fp_validation = "fp = {}" in orch_src or "_safe_fp_get" in orch_src
check(
    f"DB fingerprint validation present",
    has_fp_validation,
    f"DB data should be validated (JSONB can contain anything)"
)


# ============================================================================
# IV-6: Env var values are validated
# ============================================================================
print("\n[TEST] IV-6: Env var values are validated")

# Check for safe env var helpers
safe_helpers = sum(src.count("_safe_getenv_int") + src.count("_safe_getenv_float")
                   for src in _FILES.values())
check(
    f"Safe env var helpers: {safe_helpers} uses",
    safe_helpers >= 20,
    f"env var values should be validated before use"
)


# ============================================================================
# IV-7: No eval/exec on untrusted data
# ============================================================================
print("\n[TEST] IV-7: No eval/exec on untrusted data")

# Look for Python eval/exec (not subprocess_exec)
eval_count = 0
for src in _FILES.values():
    # Match eval( or exec( but NOT create_subprocess_exec
    for m in re.finditer(r'(?<!subprocess_)\beval\s*\(|^\s*exec\s*\(', src, re.MULTILINE):
        eval_count += 1
check(
    f"Python eval/exec calls: {eval_count}",
    eval_count == 0,
    f"eval/exec on untrusted data allows code execution"
)


# ============================================================================
# IV-8: File path validation
# ============================================================================
print("\n[TEST] IV-8: File paths are validated")

# Look for os.path.join with user input
path_validation = 0
for src in _FILES.values():
    # Look for os.path.realpath or os.path.abspath (path normalization)
    path_validation += src.count("os.path.realpath")
    path_validation += src.count("os.path.abspath")
check(
    f"Path validation: {path_validation} calls",
    path_validation >= 2,
    f"file paths should be normalized/validated"
)


# ============================================================================
# IV-9: Input sanitization for scraped content
# ============================================================================
print("\n[TEST] IV-9: Input sanitization for scraped content")

uses_sanitizer = "DefensiveInputSanitizer" in "\n".join(_FILES.values())
check(
    f"DefensiveInputSanitizer is used",
    uses_sanitizer,
    f"scraped content should be sanitized"
)


# ============================================================================
# IV-10: Runtime test - input validation
# ============================================================================
print("\n[TEST] IV-10: Runtime test of input validation")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# Test 1: Safe env var validates input
os.environ["TEST_INVALID"] = "not_a_number"
r = be._safe_getenv_int("TEST_INVALID", 99, min_value=1)
assert r == 99, f"invalid input not handled: {r}"
del os.environ["TEST_INVALID"]

# Test 2: NaN/inf rejected (EC-30 fix)
os.environ["TEST_NAN"] = "nan"
r = be._safe_getenv_float("TEST_NAN", 1.5, min_value=0.0)
assert r == 1.5, f"NaN not rejected: {r}"
del os.environ["TEST_NAN"]

# Test 3: Negative values clamped
os.environ["TEST_NEG"] = "-100"
r = be._safe_getenv_int("TEST_NEG", 10, min_value=1)
assert r == 1, f"negative not clamped: {r}"
del os.environ["TEST_NEG"]

print("OK: input validation works correctly")
'''
result = subprocess.run(["python3", "-c", test_script],
                       capture_output=True, text=True, timeout=30)
check("IV-10: input validation works",
      "OK: input validation" in result.stdout,
      f"output: {result.stdout[:200]}")


# ============================================================================
# Summary
# ============================================================================
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed > 0:
    sys.exit(1)
sys.exit(0)
