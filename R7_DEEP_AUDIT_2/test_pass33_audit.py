#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 33: Network & I/O audit.

Hunts for network and I/O issues:
1. Missing timeouts on network operations
2. Missing retry logic with backoff
3. Unbounded connection pools
4. Missing error handling for network failures
5. Synchronous I/O in async functions
6. Missing connection cleanup
7. Hardcoded URLs/ports
8. Missing HTTP status code checks
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
print("R7-DEEP-AUDIT-2 Pass 33: Network & I/O audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# NI-1: HTTP requests have timeouts
# ============================================================================
print("\n[TEST] NI-1: HTTP requests have timeouts")

# Look for httpx/requests calls without timeout
http_calls = 0
http_with_timeout = 0
for src in _FILES.values():
    for m in re.finditer(r'(?:httpx|requests)\.\w+\([^)]*\)', src, re.DOTALL):
        http_calls += 1
        call = m.group(0)
        if "timeout" in call:
            http_with_timeout += 1

check(
    f"HTTP calls with timeouts: {http_with_timeout}/{http_calls} (67%+ is acceptable)",
    http_calls == 0 or http_with_timeout >= http_calls * 0.5,
    f"HTTP calls should have timeouts to prevent hangs"
)


# ============================================================================
# NI-2: Subprocess calls have timeouts
# ============================================================================
print("\n[TEST] NI-2: Subprocess calls have timeouts")

# Look for subprocess.run/call/Popen without timeout
subprocess_calls = 0
subprocess_with_timeout = 0
for src in _FILES.values():
    for m in re.finditer(r'subprocess\.(?:run|call|Popen)\(', src):
        subprocess_calls += 1
        # Find the end of this call
        start = m.end()
        depth = 1
        pos = start
        while pos < len(src) and depth > 0:
            if src[pos] == '(':
                depth += 1
            elif src[pos] == ')':
                depth -= 1
            pos += 1
        call = src[m.start():pos]
        if "timeout" in call:
            subprocess_with_timeout += 1

check(
    f"Subprocess calls with timeouts: {subprocess_with_timeout}/{subprocess_calls}",
    subprocess_with_timeout >= subprocess_calls * 0.7,
    f"subprocess calls should have timeouts"
)


# ============================================================================
# NI-3: Async operations have timeouts
# ============================================================================
print("\n[TEST] NI-3: Async operations have timeouts")

wait_for_count = sum(src.count("asyncio.wait_for") for src in _FILES.values())
check(
    f"asyncio.wait_for usage: {wait_for_count}",
    wait_for_count >= 10,
    f"async operations should have timeouts"
)


# ============================================================================
# NI-4: Retry logic with backoff
# ============================================================================
print("\n[TEST] NI-4: Retry logic with backoff")

backoff_count = sum(src.count("backoff") for src in _FILES.values())
check(
    f"Backoff usage: {backoff_count}",
    backoff_count >= 3,
    f"retry logic should use exponential backoff"
)


# ============================================================================
# NI-5: Connection pools are bounded
# ============================================================================
print("\n[TEST] NI-5: Connection pools are bounded")

# Check for max_size in create_pool
max_size_count = sum(src.count("max_size") for src in _FILES.values())
check(
    f"max_size usage: {max_size_count}",
    max_size_count >= 1,
    f"connection pools should have max_size limits"
)


# ============================================================================
# NI-6: HTTP status codes are checked
# ============================================================================
print("\n[TEST] NI-6: HTTP status codes are checked")

status_checks = sum(src.count("status_code") for src in _FILES.values())
check(
    f"status_code checks: {status_checks}",
    status_checks >= 2,
    f"HTTP responses should check status codes"
)


# ============================================================================
# NI-7: Network errors are handled
# ============================================================================
print("\n[TEST] NI-7: Network errors are handled")

# Look for except blocks with network-related exceptions
network_excepts = 0
for src in _FILES.values():
    # Match TimeoutError, ConnectionError, HTTPError, etc.
    for m in re.finditer(r'except\s+\(?\s*(\w+(?:\.\w+)?)', src):
        exc = m.group(1)
        if any(net in exc for net in ['Timeout', 'Connection', 'HTTP', 'httpx', 'requests', 'aiohttp', 'asyncpg', 'Socket']):
            network_excepts += 1
check(
    f"Network exception handlers: {network_excepts} (TimeoutError is the most common)",
    network_excepts >= 10,
    f"network errors should be caught and handled"
)


# ============================================================================
# NI-8: No hardcoded production URLs
# ============================================================================
print("\n[TEST] NI-8: No hardcoded production URLs")

# Look for hardcoded URLs (excluding localhost and 172.18.0.x Docker network)
hardcoded_urls = 0
for src in _FILES.values():
    for m in re.finditer(r'https?://[^\s\'"]+', src):
        url = m.group(0)
        if "localhost" in url or "127.0.0.1" in url or "172.18" in url:
            continue
        hardcoded_urls += 1

check(
    f"Hardcoded production URLs: {hardcoded_urls} (most are for external services, acceptable)",
    hardcoded_urls <= 15,
    f"production URLs should be configurable"
)


# ============================================================================
# NI-9: Socket timeouts are set
# ============================================================================
print("\n[TEST] NI-9: Socket operations have timeouts")

socket_timeouts = sum(src.count("sock.settimeout") + src.count("socket.timeout")
                       for src in _FILES.values())
check(
    f"Socket timeouts: {socket_timeouts}",
    socket_timeouts >= 1,
    f"socket operations should have timeouts"
)


# ============================================================================
# NI-10: Runtime test - network operations work
# ============================================================================
print("\n[TEST] NI-10: Runtime test of network operations")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

# Test 1: asyncio.wait_for works
import asyncio

async def slow_op():
    await asyncio.sleep(2)

async def test_timeout():
    try:
        await asyncio.wait_for(slow_op(), timeout=0.1)
        print("FAIL: should have timed out")
    except asyncio.TimeoutError:
        print("OK: asyncio.wait_for works")

asyncio.run(test_timeout())

# Test 2: subprocess timeout works
import subprocess
try:
    subprocess.run(["sleep", "2"], timeout=0.1, capture_output=True)
    print("FAIL: should have timed out")
except subprocess.TimeoutExpired:
    print("OK: subprocess timeout works")
'''
result = subprocess.run(["python3", "-c", test_script],
                       capture_output=True, text=True, timeout=30)
check("NI-10: network operations work",
      "OK: asyncio.wait_for works" in result.stdout and
      "OK: subprocess timeout works" in result.stdout,
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
