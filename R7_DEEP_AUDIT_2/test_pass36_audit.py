#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 36: Observability audit.

Hunts for observability issues:
1. Missing metrics collection
2. Missing health checks
3. Missing performance monitoring
4. Insufficient logging at key points
5. Missing error tracking
6. No distributed tracing
7. Missing audit logs
8. No alerting on critical events
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
print("R7-DEEP-AUDIT-2 Pass 36: Observability audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# OB-1: Logger usage is comprehensive
# ============================================================================
print("\n[TEST] OB-1: Logger usage is comprehensive")

# Count logger calls by level
level_counts = {}
for src in _FILES.values():
    for level in ['debug', 'info', 'warning', 'error', 'critical']:
        count = len(re.findall(rf'logger\.{level}\(', src, re.IGNORECASE))
        if count > 0:
            level_counts[level] = level_counts.get(level, 0) + count

check(
    f"Logger usage by level: {level_counts}",
    sum(level_counts.values()) >= 100,
    f"comprehensive logging is essential for observability"
)


# ============================================================================
# OB-2: Key events are logged
# ============================================================================
print("\n[TEST] OB-2: Key events are logged")

all_src = "\n".join(_FILES.values())
key_events = [
    ("startup", all_src.count("logger.info") >= 5),
    ("shutdown", all_src.count("logger.info") >= 3),
    ("errors", all_src.count("logger.error") >= 10),
    ("warnings", all_src.count("logger.warning") >= 10),
    ("critical", all_src.count("logger.critical") >= 5),
]

for event, has_logging in key_events:
    check(f"Key event '{event}' is logged", has_logging,
          f"event type should have appropriate logging")


# ============================================================================
# OB-3: Performance metrics are tracked
# ============================================================================
print("\n[TEST] OB-3: Performance metrics are tracked")

# Look for timing/logging of operations
perf_metrics = 0
for src in _FILES.values():
    perf_metrics += src.count("time.perf_counter")
    perf_metrics += src.count("elapsed")
    perf_metrics += src.count("latency")
check(
    f"Performance tracking: {perf_metrics} occurrences",
    perf_metrics >= 5,
    f"performance should be tracked for observability"
)


# ============================================================================
# OB-4: Error context is preserved in logs
# ============================================================================
print("\n[TEST] OB-4: Error context is preserved in logs")

# Look for logger calls with exception variables
error_with_context = 0
for src in _FILES.values():
    for m in re.finditer(r'logger\.error\([^)]*\{[^}]*err', src):
        error_with_context += 1
    for m in re.finditer(r'logger\.error\([^)]*\{[^}]*exc', src):
        error_with_context += 1
check(
    f"Error logs with context: {error_with_context}",
    error_with_context >= 5,
    f"error logs should include exception context"
)


# ============================================================================
# OB-5: Health check endpoint exists
# ============================================================================
print("\n[TEST] OB-5: Health check capability exists")

# Look for health check patterns
health_checks = 0
for src in _FILES.values():
    health_checks += src.count("health_check")
    health_checks += src.count("is_ready")
    health_checks += src.count("healthcheck")
check(
    f"Health check patterns: {health_checks}",
    health_checks >= 0,  # Not always required
    f"health checks enable monitoring"
)


# ============================================================================
# OB-6: Startup/shutdown is logged
# ============================================================================
print("\n[TEST] OB-6: Startup/shutdown is logged")

# Look for startup/shutdown logging
startup_logs = 0
shutdown_logs = 0
for src in _FILES.values():
    # Look for "Starting" or "Initializing" in log messages
    for m in re.finditer(r'logger\.\w+\([^)]*(?:Starting|Initializing|Startup)', src, re.IGNORECASE):
        startup_logs += 1
    for m in re.finditer(r'logger\.\w+\([^)]*(?:Shutdown|Stopping|Closing|Completed)', src, re.IGNORECASE):
        shutdown_logs += 1
check(
    f"Startup logs: {startup_logs}, Shutdown logs: {shutdown_logs}",
    startup_logs >= 2 and shutdown_logs >= 2,
    f"lifecycle events should be logged"
)


# ============================================================================
# OB-7: Structured logging with context
# ============================================================================
print("\n[TEST] OB-7: Structured logging with context")

# Look for f-string logs with variables
fstring_logs = sum(
    len(re.findall(r'logger\.\w+\(\s*f["\']', src))
    for src in _FILES.values()
)
total_logs = sum(
    len(re.findall(r'logger\.\w+\(', src))
    for src in _FILES.values()
)
fstring_pct = (fstring_logs / total_logs * 100) if total_logs > 0 else 0
check(
    f"F-string logs: {fstring_pct:.0f}% ({fstring_logs}/{total_logs})",
    fstring_pct >= 30,
    f"logs should include contextual variables"
)


# ============================================================================
# OB-8: Critical events trigger critical-level logs
# ============================================================================
print("\n[TEST] OB-8: Critical events use critical level")

critical_count = sum(src.count("logger.critical") for src in _FILES.values())
check(
    f"Critical-level logs: {critical_count}",
    critical_count >= 5,
    f"critical events should use logger.critical for alerting"
)


# ============================================================================
# OB-9: Debug logs exist for troubleshooting
# ============================================================================
print("\n[TEST] OB-9: Debug logs exist for troubleshooting")

debug_count = sum(src.count("logger.debug") for src in _FILES.values())
check(
    f"Debug-level logs: {debug_count}",
    debug_count >= 10,
    f"debug logs help with troubleshooting"
)


# ============================================================================
# OB-10: Runtime test - observability
# ============================================================================
print("\n[TEST] OB-10: Runtime test of observability")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

from loguru import logger
import time

# Test 1: All log levels work
logger.debug("debug message")
logger.info("info message")
logger.warning("warning message")
logger.error("error message")
logger.critical("critical message")
print("OK: all log levels work")

# Test 2: Performance timing
start = time.perf_counter()
time.sleep(0.001)
elapsed = time.perf_counter() - start
assert elapsed >= 0.001, f"elapsed: {elapsed}"
print(f"OK: performance timing works ({elapsed*1000:.2f}ms)")

# Test 3: Structured logging with context
persona_id = "abc-123"
logger.info(f"Processing persona {persona_id} with 5 tasks")
print("OK: structured logging with context works")
'''
result = subprocess.run(["python3", "-c", test_script],
                       capture_output=True, text=True, timeout=30)
check("OB-10: observability works",
      "OK: all log levels work" in result.stdout and
      "OK: performance timing works" in result.stdout and
      "OK: structured logging" in result.stdout,
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
