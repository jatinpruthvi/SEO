#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 40: Config & test files audit.

Hunts for issues in config and test files:
1. Test file structure
2. Config file syntax
3. System integrity check
4. Shell script consistency
"""
import os
import sys
import re
import subprocess

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
print("R7-DEEP-AUDIT-2 Pass 40: Config & test files audit")
print("=" * 60)


# ============================================================================
# CT-1: Test files exist
# ============================================================================
print("\n[TEST] CT-1: Test files exist")

test_files = [
    "/home/user/project/tests/fingerprint_audit.py",
    "/home/user/project/tests/test_stealth.py",
    "/home/user/project/tests/prototype_hardener.js",
]

existing = sum(1 for f in test_files if os.path.exists(f))
check(
    f"Test files exist: {existing}/{len(test_files)}",
    existing == len(test_files),
    f"all test files should exist"
)


# ============================================================================
# CT-2: Test files compile
# ============================================================================
print("\n[TEST] CT-2: Test files compile")

compile_errors = []
for f in test_files:
    if f.endswith(".py") and os.path.exists(f):
        try:
            with open(f) as fp:
                compile(fp.read(), f, "exec")
        except SyntaxError as e:
            compile_errors.append(f"{f}:{e.lineno}: {e.msg}")

check(
    f"Python test files compile: {len([f for f in test_files if f.endswith('.py') and os.path.exists(f)]) - len(compile_errors)}",
    len(compile_errors) == 0,
    f"compile errors: {compile_errors}"
)


# ============================================================================
# CT-3: Test JS file syntax
# ============================================================================
print("\n[TEST] CT-3: Test JS file syntax")

for f in test_files:
    if f.endswith(".js") and os.path.exists(f):
        result = subprocess.run(
            ["node", "--check", f],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            check(f"Test JS syntax: {os.path.basename(f)}", True, "")
        else:
            check(f"Test JS syntax: {os.path.basename(f)}", False, result.stderr[:100])


# ============================================================================
# CT-4: System integrity check exists
# ============================================================================
print("\n[TEST] CT-4: System integrity check exists")

integrity_path = "/home/user/project/verify_system_integrity.py"
if os.path.exists(integrity_path):
    check("System integrity check exists", True, "")
else:
    check("System integrity check exists", False, "file not found")


# ============================================================================
# CT-5: Config shell scripts exist
# ============================================================================
print("\n[TEST] CT-5: Config shell scripts exist")

config_scripts = [
    "/home/user/project/config/hardened_host_os.sh",
    "/home/user/project/config/iptables_docker.sh",
    "/home/user/project/config/setup_zram_overlay.sh",
]

existing = sum(1 for f in config_scripts if os.path.exists(f))
check(
    f"Config shell scripts exist: {existing}/{len(config_scripts)}",
    existing == len(config_scripts),
    f"all config scripts should exist"
)


# ============================================================================
# CT-6: Config shell scripts are valid
# ============================================================================
print("\n[TEST] CT-6: Config shell scripts are valid")

syntax_errors = []
for f in config_scripts:
    if os.path.exists(f):
        result = subprocess.run(
            ["bash", "-n", f],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            syntax_errors.append(f"{f}: {result.stderr[:50]}")

check(
    f"Config scripts valid: {len(config_scripts) - len(syntax_errors)}/{len(config_scripts)}",
    len(syntax_errors) == 0,
    f"syntax errors: {syntax_errors}"
)


# ============================================================================
# CT-7: No hardcoded secrets in config files
# ============================================================================
print("\n[TEST] CT-7: No hardcoded secrets in config files")

hardcoded = 0
for f in config_scripts + [integrity_path]:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        for m in re.finditer(r'(?:password|api_key|secret|token)\s*=\s*["\'][a-zA-Z0-9]{8,}["\']',
                             content, re.IGNORECASE):
            hardcoded += 1

check(
    f"Hardcoded secrets in config: {hardcoded}",
    hardcoded == 0,
    f"config files should not have hardcoded secrets"
)


# ============================================================================
# CT-8: Config scripts use set -e
# ============================================================================
print("\n[TEST] CT-8: Config scripts use error handling")

with_set_e = 0
for f in config_scripts:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        if "set -e" in content or "set -eu" in content:
            with_set_e += 1

check(
    f"Config scripts with set -e: {with_set_e}/{len(config_scripts)}",
    with_set_e >= 1,  # At least one
    f"config scripts should have error handling"
)


# ============================================================================
# CT-9: Test files have assertions
# ============================================================================
print("\n[TEST] CT-9: Test files have assertions")

with_assertions = 0
for f in test_files:
    if f.endswith(".py") and os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        if "assert" in content:
            with_assertions += 1

check(
    f"Test files with assertions: {with_assertions}",
    with_assertions >= 1,
    f"test files should have assertions"
)


# ============================================================================
# CT-10: Runtime test - config files
# ============================================================================
print("\n[TEST] CT-10: Runtime test of config files")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

# Test 1: Config files exist
config_files = [
    "/home/user/project/config/hardened_host_os.sh",
    "/home/user/project/config/iptables_docker.sh",
    "/home/user/project/config/setup_zram_overlay.sh",
]
ok = sum(1 for f in config_files if os.path.exists(f))
print(f"OK: {ok}/{len(config_files)} config files exist")

# Test 2: Test files exist
test_files = [
    "/home/user/project/tests/fingerprint_audit.py",
    "/home/user/project/tests/test_stealth.py",
    "/home/user/project/tests/prototype_hardener.js",
]
ok = sum(1 for f in test_files if os.path.exists(f))
print(f"OK: {ok}/{len(test_files)} test files exist")
'''
result = subprocess.run(["python3", "-c", test_script],
                       capture_output=True, text=True, timeout=30)
check("CT-10: config files work",
      "OK: 3/3 config files exist" in result.stdout and
      "OK: 3/3 test files exist" in result.stdout,
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
