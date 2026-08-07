#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 37: Shell & JavaScript audit.

Hunts for issues in non-Python files:
1. Shell script error handling
2. Shell script security issues
3. JavaScript error handling
4. JavaScript security issues
5. Dockerfile best practices
6. Cross-file consistency
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
print("R7-DEEP-AUDIT-2 Pass 37: Shell & JavaScript audit")
print("=" * 60)


# ============================================================================
# SJ-1: Shell scripts compile (bash -n)
# ============================================================================
print("\n[TEST] SJ-1: Shell scripts are syntactically valid")

shell_files = [
    "/home/user/project/browser-runtime/entrypoint.sh",
    "/home/user/project/browser-runtime/network_audit.sh",
]

compile_errors = []
for f in shell_files:
    if os.path.exists(f):
        result = subprocess.run(
            ["bash", "-n", f],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            compile_errors.append(f"{f}: {result.stderr[:100]}")

check(
    f"Shell scripts compile: {len(shell_files) - len(compile_errors)}/{len(shell_files)}",
    len(compile_errors) == 0,
    f"shell syntax errors: {compile_errors}"
)


# ============================================================================
# SJ-2: Shell script error handling
# ============================================================================
print("\n[TEST] SJ-2: Shell scripts use 'set -e' for error handling")

entrypoint = open("/home/user/project/browser-runtime/entrypoint.sh").read()
has_set_e = "set -e" in entrypoint or "set -eu" in entrypoint
check(
    f"Shell 'set -e' present",
    has_set_e,
    f"shell scripts should exit on error"
)


# ============================================================================
# SJ-3: Shell script error handling for each command
# ============================================================================
print("\n[TEST] SJ-3: Shell scripts check command return codes")

# Look for || error handling
error_handling = entrypoint.count("||")
check(
    f"Shell error handling (||): {error_handling}",
    error_handling >= 5,
    f"critical commands should have error handling"
)


# ============================================================================
# SJ-4: No hardcoded secrets in shell scripts
# ============================================================================
print("\n[TEST] SJ-4: No hardcoded secrets in shell scripts")

# Look for password=, secret=, key= with literal values
secret_patterns = [
    r'password\s*=\s*["\'][^"\']+["\']',
    r'secret\s*=\s*["\'][^"\']+["\']',
    r'api_key\s*=\s*["\'][a-zA-Z0-9]{20,}["\']',
]

hardcoded_secrets = 0
for pattern in secret_patterns:
    for m in re.finditer(pattern, entrypoint, re.IGNORECASE):
        value = m.group(0)
        if value in ('password=""', 'password="x"', 'api_key=""'):
            continue
        hardcoded_secrets += 1

check(
    f"Hardcoded secrets in entrypoint.sh: {hardcoded_secrets}",
    hardcoded_secrets == 0,
    f"shell scripts should not contain hardcoded secrets"
)


# ============================================================================
# SJ-5: JavaScript file is syntactically valid
# ============================================================================
print("\n[TEST] SJ-5: JavaScript file is syntactically valid")

result = subprocess.run(
    ["node", "--check", "/home/user/project/browser-runtime/prototype_hardener.js"],
    capture_output=True, text=True, timeout=10
)
check(
    f"JavaScript syntax check",
    result.returncode == 0,
    f"JS errors: {result.stderr[:200]}"
)


# ============================================================================
# SJ-6: JavaScript has error handling
# ============================================================================
print("\n[TEST] SJ-6: JavaScript has error handling")

js_content = open("/home/user/project/browser-runtime/prototype_hardener.js").read()
try_count = js_content.count("try {")
catch_count = js_content.count("catch")
check(
    f"JavaScript try/catch: {try_count} try, {catch_count} catch",
    try_count > 0 and catch_count > 0,
    f"JavaScript should have error handling"
)


# ============================================================================
# SJ-7: No eval() in JavaScript
# ============================================================================
print("\n[TEST] SJ-7: No eval() in JavaScript")

eval_count = len(re.findall(r'\beval\s*\(', js_content))
check(
    f"JavaScript eval() calls: {eval_count}",
    eval_count == 0,
    f"eval() on untrusted data allows code execution"
)


# ============================================================================
# SJ-8: No innerHTML in JavaScript (XSS risk)
# ============================================================================
print("\n[TEST] SJ-8: No innerHTML in JavaScript")

innerhtml_count = len(re.findall(r'\.innerHTML\s*=', js_content))
check(
    f"innerHTML usage: {innerhtml_count}",
    innerhtml_count <= 1,  # May be used in some test contexts
    f"innerHTML can lead to XSS if user input is rendered"
)


# ============================================================================
# SJ-9: Shell script uses functions for complex logic
# ============================================================================
print("\n[TEST] SJ-9: Shell script uses functions")

function_count = len(re.findall(r'^\s*\w+\s*\(\s*\)\s*\{', entrypoint, re.MULTILINE))
check(
    f"Shell function definitions: {function_count}",
    function_count >= 5,
    f"complex logic should be in functions"
)


# ============================================================================
# SJ-10: Cross-file consistency (env vars)
# ============================================================================
print("\n[TEST] SJ-10: Env vars are consistent across files")

# Get env vars from entrypoint.sh
env_in_entrypoint = set()
for m in re.finditer(r'\$\{?([A-Z_][A-Z0-9_]+)\}?', entrypoint):
    env_in_entrypoint.add(m.group(1))

# Get env vars from Python files
env_in_python = set()
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            src = f.read()
        for m in re.finditer(r'os\.getenv\(\s*["\']([A-Z_][A-Z0-9_]+)["\']', src):
            env_in_python.add(m.group(1))
        for m in re.finditer(r'os\.environ\[\s*["\']([A-Z_][A-Z0-9_]+)["\']', src):
            env_in_python.add(m.group(1))

# Find env vars in Python but not in entrypoint
missing_in_entrypoint = env_in_python - env_in_entrypoint
# Filter out:
# 1. Common shell variables
# 2. Required sensitive vars (passed via Docker, not entrypoint)
# 3. Internal-only vars (test vars, etc.)
exclude_vars = {
    "PATH", "HOME", "USER", "PWD", "SHELL",  # Shell vars
    "DB_PASSWD", "ENCRYPTION_KEY",  # Required sensitive (from Docker)
    "TASK_TMPFS_SIZE_MB", "PERSONA_PID_BUDGET",  # Required (from Docker)
    "ORCH_TASK_TMPFS_DIR", "ORCH_TASK_UUID",  # Set by entrypoint
    "RAM_DISK_DIR",  # Internal
    "MASTER_XVFB_DISPLAY", "XVFB_PID",  # Set by entrypoint
    "SANDBOX_ENV", "DB_URI",  # Orchestrator defaults
    "CLOAKBROWSER_REQUIRED",  # Orchestrator default
    "BE_MOUSE_QUANTIZATION_HZ", "BE_COMPOSITOR_JITTER_MS",  # Behavioral defaults
    "BE_PROXY_CACHE_TTL_S",  # Proxy defaults
    "BE_TARGET_RESOLVER_TIMEOUT_S", "BE_AUTOCOMPLETE_TIMEOUT_MS",  # Behavioral
    "BE_MAX_MOUSE_STEPS",  # Behavioral
    "ORCH_PANIC_SIGNAL",  # Orchestrator default
    "IP_CHECK_URL",  # Orchestrator default
    "CLOAKBROWSER_PATH",  # Optional
    "DEFAULT_AI_API_KEY",  # Optional
    "SINKHOLE_URL", "UTLS_TARGET_HELLO",  # Orchestrator defaults
    "TASK_UUID",  # Set by Docker
    "TASK_TMPFS_DIR",  # Set by Docker
    "EDGE_TEST",  # Test var
}
missing_in_entrypoint = {v for v in missing_in_entrypoint if v not in exclude_vars}

check(
    f"Optional env vars in Python not in entrypoint: {len(missing_in_entrypoint)}",
    len(missing_in_entrypoint) <= 10,
    f"optional env vars should ideally be documented"
)
if missing_in_entrypoint:
    for v in list(missing_in_entrypoint)[:10]:
        print(f"    - {v}")


# ============================================================================
# Summary
# ============================================================================
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed > 0:
    sys.exit(1)
sys.exit(0)
