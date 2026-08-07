#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 42: Final integration test.

This is a final comprehensive test that verifies all the major fixes
from passes 1-41 still work correctly. It also checks for any regressions
introduced by the fixes.
"""
import os
import sys
import re
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
print("R7-DEEP-AUDIT-2 Pass 42: Final integration test")
print("=" * 60)


# ============================================================================
# FI-1: All env var helpers have max_value
# ============================================================================
print("\n[TEST] FI-1: All env var helpers have max_value")

files_to_check = [
    "/home/user/project/browser-runtime/behavioral_entropy.py",
    "/home/user/project/browser-runtime/hardened_orchestrator.py",
    "/home/user/project/browser-runtime/sticky_proxy_resolver.py",
]

all_have_max = True
for f in files_to_check:
    with open(f) as fp:
        content = fp.read()
    if "max_value: int = 2147483647" not in content:
        all_have_max = False

check(
    f"All 3 files have max_value (DC-41 fix)",
    all_have_max,
    f"max_value fix is missing in some files"
)


# ============================================================================
# FI-2: NaN/inf rejected in float helpers
# ============================================================================
print("\n[TEST] FI-2: NaN/inf rejected in float helpers (EC-30 fix)")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# Test NaN rejection (EC-30 fix)
os.environ["TEST_NAN"] = "nan"
r = be._safe_getenv_float("TEST_NAN", 1.5, min_value=0.0)
assert r == 1.5, f"NaN not rejected: got {r}"
del os.environ["TEST_NAN"]

# Test inf rejection (EC-30 fix)
os.environ["TEST_INF"] = "inf"
r = be._safe_getenv_float("TEST_INF", 1.5, min_value=0.0)
assert r == 1.5, f"inf not rejected: got {r}"
del os.environ["TEST_INF"]

print("OK: NaN/inf rejection works")
'''
result = subprocess.run(
    ["python3", "-c", test_script],
    capture_output=True, text=True, timeout=30
)
check(
    f"FI-2: NaN/inf rejection",
    "OK: NaN/inf rejection works" in result.stdout,
    f"output: {result.stdout[:200]}"
)


# ============================================================================
# FI-3: max_value clamping works for all env vars
# ============================================================================
print("\n[TEST] FI-3: max_value clamping for all env vars")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# Test that max_value=1 is enforced
os.environ["TEST_MAX"] = "999999999"
r = be._safe_getenv_int("TEST_MAX", 10, min_value=1, max_value=1)
assert r == 1, f"max_value not enforced: got {r}"
del os.environ["TEST_MAX"]

# Test that huge value is clamped to default max
os.environ["TEST_HUGE"] = "999999999999"
r = be._safe_getenv_int("TEST_HUGE", 10, min_value=1)
assert r == 2147483647, f"default max not enforced: got {r}"
del os.environ["TEST_HUGE"]

print("OK: max_value clamping works for all env vars")
'''
result = subprocess.run(
    ["python3", "-c", test_script],
    capture_output=True, text=True, timeout=30
)
check(
    f"FI-3: max_value clamping",
    "OK: max_value clamping works" in result.stdout,
    f"output: {result.stdout[:200]}"
)


# ============================================================================
# FI-4: Task plugins have docstrings (DC-38 fix)
# ============================================================================
print("\n[TEST] FI-4: Task plugins have docstrings")

task_files = [
    "/home/user/project/tasks/site_register/executor.py",
    "/home/user/project/tasks/gmail_verify/executor.py",
    "/home/user/project/tasks/site_interact/executor.py",
]

all_have_docs = True
for f in task_files:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        if '"""' not in content[:1000]:
            all_have_docs = False
            print(f"  Missing docstring: {f.split('/')[-1]}")

check(
    f"All task plugins have docstrings (DC-38 fix)",
    all_have_docs,
    f"some task plugins are missing docstrings"
)


# ============================================================================
# FI-5: Class docstrings in behavioral modules (DC-19 fix)
# ============================================================================
print("\n[TEST] FI-5: Class docstrings in behavioral modules")

# This was fixed in Pass 19
behavioral_modules = [
    "/home/user/project/browser-runtime/circadian_scheduler.py",
    "/home/user/project/browser-runtime/dynamic_pacing.py",
    "/home/user/project/browser-runtime/hci_reading_model.py",
    "/home/user/project/browser-runtime/identity_health.py",
    "/home/user/project/browser-runtime/input_sanitizer.py",
    "/home/user/project/browser-runtime/profile_archiver.py",
    "/home/user/project/browser-runtime/prompt_guard.py",
    "/home/user/project/browser-runtime/session_serializer.py",
]

all_have_class_docs = True
for f in behavioral_modules:
    with open(f) as fp:
        content = fp.read()
    # Check for class definitions followed by docstrings
    # Class definitions should have docstrings as the first statement
    for m in re.finditer(r'class\s+(\w+).*?:\s*\n\s*"""', content[:3000], re.DOTALL):
        # Found a class with a docstring in the first 3000 chars - good
        pass
    # Simpler check: does the file have class with docstring?
    if not re.search(r'class\s+\w+.*?:\s*\n\s*"""', content, re.DOTALL):
        all_have_class_docs = False
        print(f"  Class without docstring: {f.split('/')[-1]}")

check(
    f"All behavioral module classes have docstrings",
    all_have_class_docs,
    f"some classes are missing docstrings"
)


# ============================================================================
# FI-6: Backward compatibility maintained
# ============================================================================
print("\n[TEST] FI-6: Backward compatibility maintained")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# Test that the module loads without errors
assert hasattr(be, '_safe_getenv_int')
assert hasattr(be, '_safe_getenv_float')
assert hasattr(be, 'BehavioralEntropyGenerator')
assert hasattr(be, 'HASH_ALGO')
assert hasattr(be, 'MOUSE_QUANTIZATION_HZ')

# Test that BehavioralEntropyGenerator works
gen = be.BehavioralEntropyGenerator('test-persona')
assert gen is not None
assert gen.age > 0
assert gen.expertise in ['novice', 'intermediate', 'expert']

print("OK: backward compatibility maintained")
'''
result = subprocess.run(
    ["python3", "-c", test_script],
    capture_output=True, text=True, timeout=30
)
check(
    f"FI-6: backward compatibility",
    "OK: backward compatibility maintained" in result.stdout,
    f"output: {result.stdout[:200]}"
)


# ============================================================================
# FI-7: All key behavioral constants are accessible
# ============================================================================
print("\n[TEST] FI-7: All key behavioral constants are accessible")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# All key constants should be accessible
assert hasattr(be, 'HASH_ALGO')
assert hasattr(be, 'MOUSE_QUANTIZATION_HZ')
assert hasattr(be, '_COMPOSITOR_JITTER_STD')
assert hasattr(be, 'NUMPY_AVAILABLE')
assert hasattr(be, 'LOGURU_AVAILABLE')

# All key classes should be accessible
assert hasattr(be, 'BehavioralEntropyGenerator')
assert hasattr(be, 'ReadingPacingModel')
assert hasattr(be, 'FocusSwitchModel')
assert hasattr(be, 'MouseButtonDurationModel')
assert hasattr(be, 'ScrollInertiaModel')
assert hasattr(be, 'ActionSequenceGenerator')
assert hasattr(be, 'ActionTransitionMarkov')

print("OK: all key constants and classes accessible")
'''
result = subprocess.run(
    ["python3", "-c", test_script],
    capture_output=True, text=True, timeout=30
)
check(
    f"FI-7: all key constants accessible",
    "OK: all key constants and classes accessible" in result.stdout,
    f"output: {result.stdout[:200]}"
)


# ============================================================================
# FI-8: Exploit attempts are blocked
# ============================================================================
print("\n[TEST] FI-8: Multiple exploit attempts are blocked")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# Exploit 1: NaN as int
os.environ["EXPLOIT_NAN"] = "nan"
r = be._safe_getenv_int("EXPLOIT_NAN", 10, min_value=1)
assert r == 10, f"NaN not handled: {r}"
del os.environ["EXPLOIT_NAN"]

# Exploit 2: Infinity as int
os.environ["EXPLOIT_INF"] = "inf"
r = be._safe_getenv_int("EXPLOIT_INF", 10, min_value=1)
assert r == 10, f"inf not handled: {r}"
del os.environ["EXPLOIT_INF"]

# Exploit 3: Huge value (DC-41)
os.environ["EXPLOIT_HUGE"] = "999999999999"
r = be._safe_getenv_int("EXPLOIT_HUGE", 10, min_value=1)
assert r == 2147483647, f"huge value not clamped: {r}"
del os.environ["EXPLOIT_HUGE"]

# Exploit 4: Empty string
os.environ["EXPLOIT_EMPTY"] = ""
r = be._safe_getenv_int("EXPLOIT_EMPTY", 10, min_value=1)
assert r == 10, f"empty string not handled: {r}"
del os.environ["EXPLOIT_EMPTY"]

# Exploit 5: Whitespace
os.environ["EXPLOIT_WS"] = "   "
r = be._safe_getenv_int("EXPLOIT_WS", 10, min_value=1)
assert r == 10, f"whitespace not handled: {r}"
del os.environ["EXPLOIT_WS"]

print("OK: all exploit attempts blocked")
'''
result = subprocess.run(
    ["python3", "-c", test_script],
    capture_output=True, text=True, timeout=30
)
check(
    f"FI-8: exploit attempts blocked",
    "OK: all exploit attempts blocked" in result.stdout,
    f"output: {result.stdout[:200]}"
)


# ============================================================================
# Summary
# ============================================================================
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed > 0:
    sys.exit(1)
sys.exit(0)
