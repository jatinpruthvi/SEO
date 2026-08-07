"""
Pass 11: Runtime stress test for hardened_orchestrator.py
Tests pure functions and edge cases that static analysis might miss.
"""
import sys
import os
import json
import time
import hashlib
import signal
import asyncio
import importlib.util
from datetime import datetime, timezone, timedelta

# Set required env vars BEFORE importing orchestrator
os.environ['DB_PASSWD'] = 'test_password'
os.environ['ENCRYPTION_KEY'] = 'test_key_must_be_44_chars_long_aaaaaa'
os.environ['TASK_TMPFS_SIZE_MB'] = '100'
os.environ['PERSONA_PID_BUDGET'] = '128'
# Set tmpfs dir to a real path to pass the startup check
os.environ['ORCH_TASK_TMPFS_DIR'] = '/tmp/orch_test_tmpfs'
os.makedirs('/tmp/orch_test_tmpfs', exist_ok=True)

# Mock third-party modules before import
import types
asyncpg_mock = types.ModuleType('asyncpg')
asyncpg_mock.create_pool = MagicMock() if False else None  # placeholder
sys.modules['asyncpg'] = asyncpg_mock

import unittest.mock
from unittest.mock import MagicMock, AsyncMock, patch

asyncpg_mock.create_pool = MagicMock()
asyncpg_mock.Pool = MagicMock()
asyncpg_mock.exceptions = MagicMock()

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

# Load the orchestrator module
spec = importlib.util.spec_from_file_location(
    "orchestrator",
    "/home/user/project/browser-runtime/hardened_orchestrator.py"
)
orch = importlib.util.module_from_spec(spec)
sys.modules['orchestrator'] = orch

try:
    spec.loader.exec_module(orch)
    print("✓ Orchestrator module loaded")
except Exception as e:
    print(f"✗ Load error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Extract symbols
_redact_proxy_credentials = orch._redact_proxy_credentials
parse_proxy_url = orch.parse_proxy_url
validate_timezone = orch.validate_timezone
stable_hash = orch.stable_hash
get_socks5_port_for_persona = orch.get_socks5_port_for_persona
get_socks5_url_for_persona = orch.get_socks5_url_for_persona
derive_state_from_city = orch.derive_state_from_city
_safe_parse_user_agent = orch._safe_parse_user_agent
is_chrome_ua = orch.is_chrome_ua
DecryptionFailedError = orch.DecryptionFailedError
PersonaRetiredError = orch.PersonaRetiredError
DatabaseUnavailableError = orch.DatabaseUnavailableError
GeoConflictError = orch.GeoConflictError
PIDBudgetExceededError = orch.PIDBudgetExceededError
OrchestratorError = orch.OrchestratorError

print("=" * 60)
print("PASS 11: Orchestrator Runtime Stress Tests")
print("=" * 60)

tests_run = 0
tests_passed = 0
tests_failed = 0

def check(test_name, condition, detail=""):
    global tests_run, tests_passed, tests_failed
    tests_run += 1
    if condition:
        tests_passed += 1
        print(f"  ✓ {test_name}" + (f" — {detail}" if detail else ""))
    else:
        tests_failed += 1
        print(f"  ✗ FAIL: {test_name}" + (f" — {detail}" if detail else ""))

# === Test 1: _redact_proxy_credentials ===
print("\n=== Test 1: _redact_proxy_credentials ===")
check("Empty string", _redact_proxy_credentials("") == "")
check("None", _redact_proxy_credentials(None) == "")
check("No creds", _redact_proxy_credentials("socks5://host:1080") == "socks5://host:1080")
check("With creds", "***" in _redact_proxy_credentials("socks5://user:pass@host:1080"))
check("With creds no pw in result", "pass" not in _redact_proxy_credentials("socks5://user:pass@host:1080"))
check("URL-encoded password", "***" in _redact_proxy_credentials("socks5://user:p%40ss%21@host:1080"))
check("socks5h scheme", "socks5h" in _redact_proxy_credentials("socks5h://u:p@host:1080"))
check("User only no password", "***" in _redact_proxy_credentials("socks5://user@host:1080"))

# === Test 2: parse_proxy_url ===
print("\n=== Test 2: parse_proxy_url ===")
r = parse_proxy_url("socks5://user:pass@host:1080")
check("socks5 server parsed", r and r.get("server") == "host:1080")
check("socks5 username parsed", r and r.get("username") == "user")
check("socks5 password parsed", r and r.get("password") == "pass")

r = parse_proxy_url("socks5h://host:1080")
check("socks5h no creds", r == {"server": "host:1080"})

check("Empty -> None", parse_proxy_url("") is None)
check("None -> None", parse_proxy_url(None) is None)
check("http rejected", parse_proxy_url("http://host:8080") is None)  # R7-HOR-018
check("https rejected", parse_proxy_url("https://host:8080") is None)
check("No port -> None", parse_proxy_url("socks5://host") is None)
check("No host -> None", parse_proxy_url("socks5://:1080") is None)
check("URL-encoded creds", 
      parse_proxy_url("socks5://user%40:p%40ss@host:1080") and 
      parse_proxy_url("socks5://user%40:p%40ss@host:1080").get("username") == "user@")

# === Test 3: validate_timezone ===
print("\n=== Test 3: validate_timezone ===")
check("NY valid", validate_timezone("America/New_York") == "America/New_York")
check("London valid", validate_timezone("Europe/London") == "Europe/London")
check("Empty -> default", validate_timezone("") == "America/New_York")
check("None -> default", validate_timezone(None) == "America/New_York")
check("Invalid -> default", validate_timezone("Invalid/Zone_Not_Real") == "America/New_York")
check("Number -> default", validate_timezone(12345) == "America/New_York")
check("NoneType -> default", validate_timezone(None) == "America/New_York")
# Etc timezones should be rejected
check("Etc/GMT rejected", validate_timezone("Etc/GMT+5") == "America/New_York")
check("posixrules rejected", validate_timezone("posixrules") == "America/New_York")

# === Test 4: stable_hash ===
print("\n=== Test 4: stable_hash ===")
h1 = stable_hash("test")
h2 = stable_hash("test")
check("Same input -> same hash", h1 == h2)
h3 = stable_hash("different")
check("Different input -> different hash", h1 != h3)
h_empty = stable_hash("")
check("Empty string", isinstance(h_empty, int))
h_unicode = stable_hash("test_测试_🎉")
check("Unicode", isinstance(h_unicode, int))
h_long = stable_hash("x" * 10000)
check("Long string", isinstance(h_long, int))
# Result should be in 32-bit range
check("32-bit range", 0 <= h1 < 2**32)

# === Test 5: get_socks5_port_for_persona ===
print("\n=== Test 5: get_socks5_port_for_persona ===")
# Distribution check
ports_seen = set()
for i in range(500):
    p = get_socks5_port_for_persona(f"persona_{i}")
    if p:
        ports_seen.add(p)
check("Multiple unique ports (500 personas)", len(ports_seen) >= 2)
# Determinism
p1 = get_socks5_port_for_persona("fixed_uuid_abc")
p2 = get_socks5_port_for_persona("fixed_uuid_abc")
check("Deterministic", p1 == p2)
# Edge cases
p_empty = get_socks5_port_for_persona("")
check("Empty persona handled", p_empty is not None)
# All ports should be in SOCKS5_PERSONA_PORTS range
SOCKS5_PORTS = os.environ.get("SOCKS5_PERSONA_PORTS", "1081,1082,1083,1084,1085").split(",")
all_in_range = all(int(get_socks5_port_for_persona(f"p_{i}")) in [int(p) for p in SOCKS5_PORTS] for i in range(50))
check("All in configured range", all_in_range)

# === Test 6: get_socks5_url_for_persona ===
print("\n=== Test 6: get_socks5_url_for_persona ===")
url = get_socks5_url_for_persona("test")
check("URL has scheme", url.startswith("socks5h://"))
check("URL has port", ":" in url.split("//")[1])
check("URL has host", "172.18.0.5" in url)
url_empty = get_socks5_url_for_persona("")
check("Empty persona URL", url_empty.startswith("socks5h://"))

# === Test 7: derive_state_from_city ===
print("\n=== Test 7: derive_state_from_city ===")
check("Dallas -> tx", derive_state_from_city("dallas") == "tx")
check("DALLAS (upper) -> tx", derive_state_from_city("DALLAS") == "tx")
check("Dallas (capital) -> tx", derive_state_from_city("Dallas") == "tx")
check("Houston -> tx", derive_state_from_city("houston") == "tx")
check("Los Angeles -> ca", derive_state_from_city("Los Angeles") == "ca")
check("New York -> ny", derive_state_from_city("New York") == "ny")
check("London -> ln", derive_state_from_city("london") == "ln")
check("Tokyo -> tk", derive_state_from_city("Tokyo") == "tk")
check("Empty -> default tx", derive_state_from_city("") == "tx")
check("None -> default tx", derive_state_from_city(None) == "tx")
check("Unknown -> default tx", derive_state_from_city("Atlantis") == "tx")
check("With whitespace", derive_state_from_city("  Dallas  ") == "tx")
check("Hyphenated", derive_state_from_city("san-antonio") == "tx")  # default (not in dict)

# === Test 8: _safe_parse_user_agent ===
print("\n=== Test 8: _safe_parse_user_agent ===")
# Standard Chrome 120
fp1 = {"navigator": {"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.109 Safari/537.36"}}
v, m = _safe_parse_user_agent(fp1)
check("Chrome 120 full version", v == "120.0.6099.109")
check("Chrome 120 major", m == 120)

# Chrome 121
fp2 = {"navigator": {"userAgent": "Chrome/121.0.6167.85 Safari/537.36"}}
v, m = _safe_parse_user_agent(fp2)
check("Chrome 121 full version", v == "121.0.6167.85")
check("Chrome 121 major", m == 121)

# Firefox
fp3 = {"navigator": {"userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"}}
v, m = _safe_parse_user_agent(fp3)
check("Firefox falls back to default", v == "120.0.6099.109")
check("Firefox falls back to major 120", m == 120)

# Edge cases
v, m = _safe_parse_user_agent(None)
check("None fp -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent({})
check("Empty dict -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent({"navigator": {}})
check("Empty navigator -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent({"navigator": {"userAgent": None}})
check("None UA -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent({"navigator": {"userAgent": 12345}})
check("Non-string UA -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent({"navigator": {"userAgent": "Chrome/abc.def Safari/537.36"}})
check("Non-numeric major -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent("not a dict")
check("String fp -> default", v == "120.0.6099.109" and m == 120)
v, m = _safe_parse_user_agent(42)
check("Int fp -> default", v == "120.0.6099.109" and m == 120)

# === Test 9: is_chrome_ua ===
print("\n=== Test 9: is_chrome_ua ===")
check("Chrome 120", is_chrome_ua("Chrome/120.0.6099.109") == True)
check("Chromium 120", is_chrome_ua("Chromium/120.0.6099.0") == True)
check("Firefox", is_chrome_ua("Firefox/121.0") == False)
check("Safari", is_chrome_ua("Safari/537.36") == False)
check("Empty", is_chrome_ua("") == False)
check("None", is_chrome_ua(None) == False)
check("Random string", is_chrome_ua("garbage") == False)

# === Test 10: Exception classes ===
print("\n=== Test 10: Exception classes ===")
e1 = DecryptionFailedError("test", code="auth_failed")
check("DecryptionFailedError code", e1.code == "auth_failed")
check("DecryptionFailedError str", str(e1) == "test")
check("DecryptionFailedError is OrchestratorError", isinstance(e1, OrchestratorError))

e2 = PersonaRetiredError("rotated", persona_id="p123")
check("PersonaRetiredError persona_id", e2.persona_id == "p123")
check("PersonaRetiredError is OrchestratorError", isinstance(e2, OrchestratorError))

e3 = GeoConflictError("mismatch", persona_id="p456")
check("GeoConflictError persona_id", e3.persona_id == "p456")
check("GeoConflictError is OrchestratorError", isinstance(e3, OrchestratorError))

e4 = PIDBudgetExceededError("too many", persona_id="p789")
check("PIDBudgetExceededError persona_id", e4.persona_id == "p789")
check("PIDBudgetExceededError is OrchestratorError", isinstance(e4, OrchestratorError))

# === Summary ===
print("\n" + "=" * 60)
print(f"SUMMARY: {tests_run} tests, {tests_passed} passed, {tests_failed} failed")
print("=" * 60)
sys.exit(0 if tests_failed == 0 else 1)
