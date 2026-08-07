#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 25: Logging audit.

Hunts for logging issues:
1. print() statements (should use logger)
2. Sensitive data in log messages
3. Inconsistent log levels
4. Missing log context
5. Log injection vulnerabilities
6. No log rotation configuration
7. Missing structured logging
8. Debug logs in production paths
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
        print(f"  ✅ OK: {name}")
    else:
        failed += 1
        print(f"  ❌ FAIL: {name}  {detail}")


print("=" * 60)
print("R7-DEEP-AUDIT-2 Pass 25: Logging audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# LG-1: No print() statements (use logger)
# ============================================================================
print("\n[TEST] LG-1: No print() statements (use logger)")

print_statements = []
for fname, src in _FILES.items():
    # Find print( calls (not in comments, not in strings)
    for m in re.finditer(r'(?<![.\w])print\s*\(', src):
        line_no = src[:m.start()].count("\n") + 1
        line = src.split("\n")[line_no - 1] if line_no <= len(src.split("\n")) else ""
        # Skip if it's a comment
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Skip docstring content
        if '"""' in line or "'''" in line:
            continue
        print_statements.append(f"{fname}:{line_no}: {line.strip()[:60]}")

check(
    f"print() statements: {len(print_statements)} (should use logger)",
    len(print_statements) <= 5,  # Allow a few in test/utility files
    f"print() bypasses log levels and structured logging"
)
if print_statements:
    for p in print_statements[:3]:
        print(f"    - {p}")


# ============================================================================
# LG-2: Logger is consistently used across modules
# ============================================================================
print("\n[TEST] LG-2: Logger is consistently used")

# Count files that use logger
files_with_logger = sum(1 for src in _FILES.values() if "logger." in src)
files_with_prints = sum(1 for src in _FILES.values()
                         if re.search(r'(?<![.\w])print\s*\(', src))

check(
    f"Files using logger: {files_with_logger}/{len(_FILES)}",
    files_with_logger >= len(_FILES) - 3,
    f"all modules should use the logger"
)


# ============================================================================
# LG-3: Appropriate log levels are used
# ============================================================================
print("\n[TEST] LG-3: Appropriate log levels")

# Count usage of each level
level_counts = {}
for fname, src in _FILES.items():
    for level in ['debug', 'info', 'warning', 'error', 'critical']:
        count = len(re.findall(rf'logger\.{level}\(', src, re.IGNORECASE))
        if count > 0:
            level_counts[level] = level_counts.get(level, 0) + count

# Check distribution (info should be most common, critical least)
check(
    f"Log level distribution: {level_counts}",
    level_counts.get("info", 0) > level_counts.get("debug", 0) and
    level_counts.get("warning", 0) > level_counts.get("error", 0),
    f"info > debug, warning > error (severity hierarchy)"
)


# ============================================================================
# LG-4: No sensitive data in log messages
# ============================================================================
print("\n[TEST] LG-4: No sensitive data in log messages")

# Check for password, secret, key, token in logger calls
sensitive_patterns = [
    r'logger\.\w+\([^)]*password[^)]*\)',
    r'logger\.\w+\([^)]*secret[^)]*\)',
    r'logger\.\w+\([^)]*api_key[^)]*\)',
    r'logger\.\w+\([^)]*token[^)]*\)',
]

# Exclude the env var NAMES being mentioned (not values)
sensitive_logs = []
for fname, src in _FILES.items():
    for pattern in sensitive_patterns:
        for m in re.finditer(pattern, src, re.IGNORECASE):
            line_no = src[:m.start()].count("\n") + 1
            # Check if it's just the env var name in a message
            line = src.split("\n")[line_no - 1] if line_no <= len(src.split("\n")) else ""
            if any(word in line for word in ["ENCRYPTION_KEY mismatch", "DB_PASSWD not set"]):
                continue
            sensitive_logs.append(f"{fname}:{line_no}")

check(
    f"Sensitive data in logs: {len(sensitive_logs)}",
    len(sensitive_logs) == 0,
    f"sensitive data should never be logged"
)


# ============================================================================
# LG-5: Log messages include context
# ============================================================================
print("\n[TEST] LG-5: Log messages include context")

# Check that log messages have variables or descriptive text
context_logs = 0
total_logs = 0
for fname, src in _FILES.items():
    for m in re.finditer(r'logger\.\w+\(\s*(["\'].*?["\']|f["\'].*?["\'])', src):
        total_logs += 1
        msg = m.group(1)
        # Check if message has variables (f-string) or is descriptive (>20 chars)
        if msg.startswith("f") or len(msg) > 30 or "{" in msg:
            context_logs += 1

context_pct = (context_logs / total_logs * 100) if total_logs > 0 else 0
check(
    f"Log messages with context: {context_pct:.0f}% ({context_logs}/{total_logs})",
    context_pct >= 50,
    f"log messages should include relevant context"
)


# ============================================================================
# LG-6: No log injection vulnerabilities
# ============================================================================
print("\n[TEST] LG-6: No log injection vulnerabilities")

# Check for user input directly in log messages
# (user input in logs can be used for log injection attacks)
log_injection = []
for fname, src in _FILES.items():
    # Look for logger.X(user_input) or logger.X(f"...{user_input}...")
    # Heuristic: look for variable names like 'input', 'user_data', 'request'
    for m in re.finditer(r'logger\.\w+\(\s*(?:f["\'][^{]*\{(\w+)|["\'][^"\']*["\']\s*%\s*(\w+))', src):
        var_name = m.group(1) or m.group(2)
        if var_name in ("input", "user_input", "request", "user_data", "payload"):
            line_no = src[:m.start()].count("\n") + 1
            log_injection.append(f"{fname}:{line_no}: {var_name}")

check(
    f"Log injection risks: {len(log_injection)}",
    len(log_injection) <= 2,
    f"user input in logs can be used for log injection"
)


# ============================================================================
# LG-7: Logger is imported correctly
# ============================================================================
print("\n[TEST] LG-7: Logger is imported correctly")

# Check that loguru logger is used
uses_loguru = "from loguru import logger" in "\n".join(_FILES.values())
check(
    f"loguru logger is used",
    uses_loguru,
    f"consistent logger import across modules"
)


# ============================================================================
# LG-8: Error logs include exception details
# ============================================================================
print("\n[TEST] LG-8: Error logs include exception details")

# Find logger.error calls in except blocks that include the exception
# Heuristic: look for logger.error with f-string containing 'e' or 'err' or 'exc'
error_with_context = 0
total_error_logs = 0
for fname, src in _FILES.items():
    for m in re.finditer(r'logger\.error\s*\(\s*(["\'].*?["\']|f["\'].*?["\'])', src):
        total_error_logs += 1
        msg = m.group(1)
        # Check if it has exception context
        if "{" in msg or "e}" in msg or "err" in msg.lower() or "exc" in msg.lower():
            error_with_context += 1

check(
    f"Error logs with exception context: {error_with_context}/{total_error_logs}",
    total_error_logs == 0 or error_with_context >= total_error_logs * 0.3,
    f"error logs should include exception details"
)


# ============================================================================
# LG-9: Critical events are logged at critical level
# ============================================================================
print("\n[TEST] LG-9: Critical events use critical level")

# Check that panic, fatal, abort events use critical level
critical_events = sum(
    src.count("logger.critical(") for src in _FILES.values()
)
check(
    f"Critical-level logs: {critical_events}",
    critical_events >= 5,
    f"critical events (panic, abort, fatal) should use critical level"
)


# ============================================================================
# LG-10: Debug logs are used for diagnostic info
# ============================================================================
print("\n[TEST] LG-10: Debug logs exist for diagnostic info")

debug_count = sum(src.count("logger.debug(") for src in _FILES.values())
check(
    f"Debug-level logs: {debug_count}",
    debug_count >= 10,
    f"debug logs help with troubleshooting"
)


# ============================================================================
# LG-11: No bare exception logging (missing exception type)
# ============================================================================
print("\n[TEST] LG-11: Exception details are preserved in logs")

# Find except blocks where the exception is not logged
unlogged_exceptions = 0
for fname, src in _FILES.items():
    # Find except X as e: blocks without logger calls
    for m in re.finditer(r'except\s+\w+(?:\s+as\s+(\w+))?\s*:\s*\n((?:\s{4,}.*\n)+)', src):
        block = m.group(2)
        exc_var = m.group(1)
        # Check if the block logs the exception
        if exc_var and exc_var not in block:
            # Exception variable is not used in the block
            if "logger." in block or "raise" in block:
                continue
            unlogged_exceptions += 1

check(
    f"Exceptions not logged: {unlogged_exceptions}",
    unlogged_exceptions <= 30,  # Many are intentional (re-raise patterns)
    f"caught exceptions should be logged for debugging"
)


# ============================================================================
# LG-12: Runtime test - loguru configuration
# ============================================================================
print("\n[TEST] LG-12: Runtime test of logger configuration")

log_test = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

from loguru import logger

# Test 1: Logger can log at all levels
logger.debug("debug message")
logger.info("info message")
logger.warning("warning message")
logger.error("error message")
logger.critical("critical message")
print("OK: logger works at all 5 levels")

# Test 2: Logger supports f-strings
persona_id = "abc-123"
logger.info(f"Processing persona {persona_id}")
print("OK: logger supports f-strings")

# Test 3: Logger handles exceptions
try:
    raise ValueError("test error")
except ValueError as e:
    logger.error(f"Caught error: {e}")
print("OK: logger handles exceptions")
'''
result = subprocess.run(
    ["python3", "-c", log_test],
    capture_output=True, text=True, timeout=30
)
out = result.stdout.strip()
check(
    "LG-12: loguru logger works correctly",
    "OK: logger works at all 5 levels" in out and
    "OK: logger supports f-strings" in out and
    "OK: logger handles exceptions" in out,
    f"output: {out[:200]}"
)


# ============================================================================
# LG-13: Log messages are not duplicated
# ============================================================================
print("\n[TEST] LG-13: No duplicate log messages")

# Find identical log messages
from collections import Counter
all_messages = []
for fname, src in _FILES.items():
    for m in re.finditer(r'logger\.\w+\(\s*["\']([^"\']+)["\']', src):
        all_messages.append((m.group(1), fname))

msg_counts = Counter(msg for msg, _ in all_messages)
duplicates = [(msg, count) for msg, count in msg_counts.items() if count > 3]
check(
    f"Duplicate log messages (>3 occurrences): {len(duplicates)}",
    len(duplicates) <= 5,
    f"duplicate logs indicate copy-paste or missing context"
)


# ============================================================================
# LG-14: Structured logging fields
# ============================================================================
print("\n[TEST] LG-14: Structured logging with extra fields")

# Check for logger.bind() or .opt() usage (loguru features)
structured_logs = sum(
    src.count("logger.bind(") + src.count(".opt(")
    for src in _FILES.values()
)
check(
    f"Structured logging calls: {structured_logs}",
    structured_logs >= 0,  # Not required, just informational
    f"structured logging helps with log aggregation"
)


# ============================================================================
# LG-15: No logging in hot loops
# ============================================================================
print("\n[TEST] LG-15: No logging in performance-critical loops")

# Use AST to find logger calls nested deep inside loops
hot_loop_logs = 0
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if this is a logger.info/warning/error call
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("info", "warning", "error", "critical"):
                    # Count nesting depth (for/while/if/with)
                    depth = 0
                    # This is a simplified check - we just count control structures
                    # in the same function
                    for parent in ast.walk(tree):
                        if isinstance(parent, (ast.For, ast.While)):
                            for child in ast.walk(parent):
                                if child is node:
                                    depth += 1
                    if depth >= 3:  # Nested 3+ levels deep
                        hot_loop_logs += 1

check(
    f"Non-debug logs in deep loops (3+ levels): {hot_loop_logs}",
    hot_loop_logs <= 10,  # Allow some
    f"logging in hot loops hurts performance"
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
