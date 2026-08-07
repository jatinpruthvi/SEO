"""
Pass 11: Test the _safe_fp_get and _fp_get2 helpers for JSON null safety.
Validates that the orchestrator doesn't crash on null/None fingerprint values.
"""
import sys
import os
import json
import types
import importlib.util
from unittest.mock import MagicMock

# Set up env vars
os.environ['DB_PASSWD'] = 'test'
os.environ['ENCRYPTION_KEY'] = 'x' * 44
os.environ['TASK_TMPFS_SIZE_MB'] = '100'
os.environ['PERSONA_PID_BUDGET'] = '128'
os.environ['ORCH_TASK_TMPFS_DIR'] = '/tmp/orch_test_tmpfs'
os.makedirs('/tmp/orch_test_tmpfs', exist_ok=True)

# Mock third-party modules
asyncpg_mock = types.ModuleType('asyncpg')
asyncpg_mock.create_pool = MagicMock()
sys.modules['asyncpg'] = asyncpg_mock

loguru_mock = types.ModuleType('loguru')
loguru_mock.logger = MagicMock()
sys.modules['loguru'] = loguru_mock

psutil_mock = types.ModuleType('psutil')
psutil_mock.Process = MagicMock()
psutil_mock.NoSuchProcess = type('NoSuchProcess', (Exception,), {})
psutil_mock.AccessDenied = type('AccessDenied', (Exception,), {})
psutil_mock.process_iter = MagicMock()
psutil_mock.wait_procs = MagicMock(return_value=([], []))
sys.modules['psutil'] = psutil_mock

# Load orchestrator
spec = importlib.util.spec_from_file_location(
    "orch", "/home/user/project/browser-runtime/hardened_orchestrator.py"
)
orch = importlib.util.module_from_spec(spec)
sys.modules['orch'] = orch
spec.loader.exec_module(orch)

_safe_fp_get = orch._safe_fp_get
_fp_get2 = orch._fp_get2

print("=" * 60)
print("PASS 11: Safe fingerprint access helper tests")
print("=" * 60)

tests_run = 0
tests_passed = 0
tests_failed = 0

def check(name, condition, detail=""):
    global tests_run, tests_passed, tests_failed
    tests_run += 1
    if condition:
        tests_passed += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        tests_failed += 1
        print(f"  ✗ FAIL: {name}" + (f" — {detail}" if detail else ""))

# Test _safe_fp_get (1-level)
print("\n=== _safe_fp_get: None fp ===")
check("None fp with no default", _safe_fp_get(None, 'navigator') is None)
check("None fp with {} default", _safe_fp_get(None, 'navigator', {}) == {})
check("None fp with None default", _safe_fp_get(None, 'navigator', None) is None)
check("None fp with string default", _safe_fp_get(None, 'navigator', 'default') == 'default')

print("\n=== _safe_fp_get: Non-dict fp ===")
check("String fp", _safe_fp_get("not a dict", 'navigator') is None)
check("Int fp", _safe_fp_get(42, 'navigator') is None)
check("List fp", _safe_fp_get([1, 2, 3], 'navigator') is None)

print("\n=== _safe_fp_get: Missing key ===")
check("Missing key returns default", _safe_fp_get({}, 'navigator') is None)
check("Missing key returns {} default", _safe_fp_get({}, 'navigator', {}) == {})
check("Missing key returns string default", _safe_fp_get({}, 'navigator', 'default') == 'default')

print("\n=== _safe_fp_get: None value (CRITICAL) ===")
check("None navigator value", _safe_fp_get({"navigator": None}, 'navigator') is None)
check("None value with default", _safe_fp_get({"k": None}, 'k', 'default') == 'default')
check("None value with dict default", _safe_fp_get({"k": None}, 'k', {'x': 1}) == {'x': 1})

print("\n=== _safe_fp_get: Non-dict value ===")
check("List value returns default", _safe_fp_get({"navigator": [1, 2]}, 'navigator', {}) == {})
check("String value returns default", _safe_fp_get({"navigator": "string"}, 'navigator', {}) == {})
check("Int value returns default", _safe_fp_get({"navigator": 42}, 'navigator', {}) == {})
check("Bool value returns default", _safe_fp_get({"navigator": True}, 'navigator', {}) == {})
check("List value no default returns None (safe)", _safe_fp_get({"navigator": [1, 2]}, 'navigator') is None)

print("\n=== _safe_fp_get: Normal case ===")
check("Normal dict", _safe_fp_get({"navigator": {"platform": "Win32"}}, 'navigator') == {"platform": "Win32"})
check("Empty dict", _safe_fp_get({"a": {}}, 'a') == {})

# Test _fp_get2 (2-level)
print("\n=== _fp_get2: None fp ===")
check("None fp with default", _fp_get2(None, 'navigator', 'model', '') == '')
check("None fp with custom default", _fp_get2(None, 'navigator', 'model', 'foo') == 'foo')

print("\n=== _fp_get2: Non-dict fp ===")
check("String fp", _fp_get2("not a dict", 'navigator', 'model', '') == '')
check("Int fp", _fp_get2(42, 'navigator', 'model', '') == '')

print("\n=== _fp_get2: Missing outer key ===")
check("Missing outer", _fp_get2({}, 'navigator', 'model', '') == '')
check("Missing outer with custom default", _fp_get2({}, 'navigator', 'model', 'foo') == 'foo')

print("\n=== _fp_get2: None outer value (CRITICAL) ===")
check("None outer (was crash)", _fp_get2({"navigator": None}, 'navigator', 'model', '') == '')
check("None outer with custom default", _fp_get2({"navigator": None}, 'navigator', 'model', 'foo') == 'foo')

print("\n=== _fp_get2: Non-dict outer value ===")
check("List outer", _fp_get2({"navigator": [1, 2]}, 'navigator', 'model', '') == '')
check("String outer", _fp_get2({"navigator": "string"}, 'navigator', 'model', '') == '')

print("\n=== _fp_get2: Missing inner key ===")
check("Missing inner", _fp_get2({"navigator": {}}, 'navigator', 'model', '') == '')
check("Missing inner with custom default", _fp_get2({"navigator": {}}, 'navigator', 'model', 'foo') == 'foo')

print("\n=== _fp_get2: None inner value (CRITICAL) ===")
check("None inner (was bug)", _fp_get2({"navigator": {"model": None}}, 'navigator', 'model', '') == '')
check("None inner with custom default", _fp_get2({"navigator": {"model": None}}, 'navigator', 'model', 'foo') == 'foo')

print("\n=== _fp_get2: Normal case ===")
check("Normal model", _fp_get2({"navigator": {"model": "MacBook"}}, 'navigator', 'model', '') == "MacBook")
check("Normal platformVersion", _fp_get2({"navigator": {"platformVersion": "10.0"}}, 'navigator', 'platformVersion', '15.0.0') == "10.0")
check("Empty string is valid value", _fp_get2({"navigator": {"model": ""}}, 'navigator', 'model', 'foo') == '')

# Test f-string substitution patterns from the actual orchestrator
print("\n=== f-string simulation (the real bug fix) ===")
# The orchestrator's f-string uses _fp_get2 results directly in f-strings
# e.g., f"{_fp_get2(fp, 'navigator', 'model', '')}"

# Simulate the exact pattern
def simulate_fstring(fp):
    return f"model: \"{_fp_get2(fp, 'navigator', 'model', '')}\""

# Normal
r = simulate_fstring({"navigator": {"model": "iPhone"}})
check("Normal f-string model", r == 'model: "iPhone"')

# CRITICAL: was producing 'None' string
r = simulate_fstring({"navigator": {"model": None}})
check("Null model (was bug)", r == 'model: ""', detail=f"got {repr(r)}")
check("Null model doesn't produce 'None' string", "None" not in r)

# CRITICAL: was producing 'None' string
r = simulate_fstring({"navigator": None})
check("Null navigator (was bug)", r == 'model: ""', detail=f"got {repr(r)}")
check("Null navigator doesn't crash", "None" not in r)

# Empty
r = simulate_fstring({})
check("Empty fp", r == 'model: ""')

# None
r = simulate_fstring(None)
check("None fp (no crash)", r == 'model: ""')

# Test the screen case
def simulate_screen(fp):
    return f"{_fp_get2(fp, 'screen', 'width', 1920)}x{_fp_get2(fp, 'screen', 'height', 1080)}x24"

check("Screen normal", simulate_screen({"screen": {"width": 2560, "height": 1440}}) == "2560x1440x24")
check("Screen null", simulate_screen({"screen": None}) == "1920x1080x24")
check("Screen null width only", simulate_screen({"screen": {"width": None, "height": 1080}}) == "1920x1080x24")
check("Screen missing", simulate_screen({}) == "1920x1080x24")

# Test webgl case (used in f-string with json.dumps, not in f-string)
import json
def get_webgl_safe(fp):
    vendor = _fp_get2(fp, 'webgl', 'vendor', 'Intel Inc.')
    renderer = _fp_get2(fp, 'webgl', 'renderer', 'Intel(R) Iris(TM) Plus Graphics')
    return json.dumps({"vendor": vendor, "renderer": renderer})

check("WebGL normal", get_webgl_safe({"webgl": {"vendor": "Apple", "renderer": "M1"}}) == '{"vendor": "Apple", "renderer": "M1"}')
check("WebGL null", get_webgl_safe({"webgl": None}) == '{"vendor": "Intel Inc.", "renderer": "Intel(R) Iris(TM) Plus Graphics"}')
check("WebGL missing", get_webgl_safe({}) == '{"vendor": "Intel Inc.", "renderer": "Intel(R) Iris(TM) Plus Graphics"}')

# Summary
print("\n" + "=" * 60)
print(f"SUMMARY: {tests_run} tests, {tests_passed} passed, {tests_failed} failed")
print("=" * 60)
sys.exit(0 if tests_failed == 0 else 1)
