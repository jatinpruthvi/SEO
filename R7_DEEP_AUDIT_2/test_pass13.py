"""
Pass 13: Test for proxy URL IPv6 and edge cases
"""
import sys
import os
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

_redact = orch._redact_proxy_credentials
parse = orch.parse_proxy_url

print("=" * 60)
print("PASS 13: Proxy URL edge cases (IPv6, URL-encoding)")
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

# Section 1: IPv6 in redaction
print("\n=== IPv6 in redaction ===")
check("IPv6 basic",
      _redact("socks5://user:pass@[::1]:1080") == "socks5://user:***@[::1]:1080",
      _redact("socks5://user:pass@[::1]:1080"))
check("IPv6 no creds",
      _redact("socks5://[::1]:1080") == "socks5://[::1]:1080",
      _redact("socks5://[::1]:1080"))
check("IPv6h scheme",
      _redact("socks5h://u:p@[2001:db8::1]:1080") == "socks5h://u:***@[2001:db8::1]:1080",
      _redact("socks5h://u:p@[2001:db8::1]:1080"))
check("IPv4-mapped IPv6",
      _redact("socks5://user:pass@[::ffff:192.0.2.1]:1080") == "socks5://user:***@[::ffff:192.0.2.1]:1080",
      _redact("socks5://user:pass@[::ffff:192.0.2.1]:1080"))

# Section 2: URL-encoding in redaction
print("\n=== URL-encoding in redaction ===")
check("URL-encoded username preserved (NOT unquoted)",
      _redact("socks5://user%40:p%40ss@host:1080") == "socks5://user%40:***@host:1080",
      _redact("socks5://user%40:p%40ss@host:1080"))
check("URL-encoded password preserved",
      _redact("socks5://user:p%21%40%23@host:1080") == "socks5://user:***@host:1080",
      _redact("socks5://user:p%21%40%23@host:1080"))
check("Standard URL works",
      _redact("socks5://user:pass@host:1080") == "socks5://user:***@host:1080")

# Section 3: IPv6 in parse
print("\n=== IPv6 in parse_proxy_url ===")
check("IPv6 basic server",
      parse("socks5://[::1]:1080") == {"server": "[::1]:1080"})
check("IPv6 with creds",
      parse("socks5://user:pass@[::1]:1080") == {"server": "[::1]:1080", "username": "user", "password": "pass"})
check("IPv6 no port rejected",
      parse("socks5://[::1]") is None)
check("IPv4 still works",
      parse("socks5://host:1080") == {"server": "host:1080"})

# Section 4: URL-encoding in parse (unquoted form for Playwright)
print("\n=== URL-encoding in parse (unquoted for Playwright) ===")
check("URL-encoded username UNQUOTED in parse",
      parse("socks5://user%40:p%40ss@host:1080") == {"server": "host:1080", "username": "user@", "password": "p@ss"})
check("URL-encoded special chars UNQUOTED",
      parse("socks5://user:p%21%40%23@host:1080") == {"server": "host:1080", "username": "user", "password": "p!@#"})

# Section 5: Edge cases
print("\n=== Edge cases ===")
check("None returns empty", _redact(None) == "")
check("Empty string returns empty", _redact("") == "")
check("None to parse returns None", parse(None) is None)
check("Empty to parse returns None", parse("") is None)
check("Garbage to parse returns None", parse("garbage") is None)
check("Garbage to redact returns something safe (no leak)",
      "password" not in _redact("garbage") and "secret" not in _redact("garbage"),
      _redact("garbage"))
check("HTTP scheme rejected by parse", parse("http://host:8080") is None)
check("HTTPS scheme rejected by parse", parse("https://host:8080") is None)

# Section 6: Both redaction and parse should handle same URL consistently
print("\n=== Consistency: redaction vs parse ===")
for url in [
    "socks5://user:pass@host:1080",
    "socks5h://user:pass@host:1080",
    "socks5://user:pass@[::1]:1080",
    "socks5://[::1]:1080",
]:
    redacted = _redact(url)
    parsed = parse(url)
    # The server in parsed should be a substring of the redacted form
    if parsed:
        server = parsed.get("server", "")
        check(f"Redaction preserves server '{url}'",
              server in redacted or f"{server}" in redacted,
              f"redacted: {redacted}, server: {server}")

print("\n" + "=" * 60)
print(f"SUMMARY: {tests_run} tests, {tests_passed} passed, {tests_failed} failed")
print("=" * 60)
sys.exit(0 if tests_failed == 0 else 1)
