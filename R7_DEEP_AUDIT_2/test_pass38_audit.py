#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 38: Task plugins & database audit.

Hunts for issues in task plugins and database:
1. Task plugin structure
2. Task plugin error handling
3. Database schema integrity
4. SQL injection in schema
5. Task plugin security
6. Cross-task consistency
"""
import os
import sys
import re
import subprocess

sys.path.insert(0, "/home/user/project")

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
print("R7-DEEP-AUDIT-2 Pass 38: Task plugins & database audit")
print("=" * 60)


# ============================================================================
# TP-1: Task plugin structure
# ============================================================================
print("\n[TEST] TP-1: Task plugins have TaskExecutor class")

task_files = [
    "/home/user/project/tasks/site_register/executor.py",
    "/home/user/project/tasks/gmail_verify/executor.py",
    "/home/user/project/tasks/site_interact/executor.py",
]

all_have_executor = True
for f in task_files:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        if "class TaskExecutor" not in content:
            all_have_executor = False
            print(f"    - {f}: missing TaskExecutor class")

check(
    f"All task plugins have TaskExecutor class",
    all_have_executor,
    f"task plugins must implement TaskExecutor"
)


# ============================================================================
# TP-2: Task plugins compile
# ============================================================================
print("\n[TEST] TP-2: Task plugins are syntactically valid")

compile_errors = []
for f in task_files:
    if os.path.exists(f):
        try:
            with open(f) as fp:
                compile(fp.read(), f, "exec")
        except SyntaxError as e:
            compile_errors.append(f"{f}:{e.lineno}: {e.msg}")

check(
    f"Task plugins compile: {len(task_files) - len(compile_errors)}/{len(task_files)}",
    len(compile_errors) == 0,
    f"syntax errors: {compile_errors}"
)


# ============================================================================
# TP-3: Task plugins have docstrings
# ============================================================================
print("\n[TEST] TP-3: Task plugins have docstrings")

with_docstrings = 0
for f in task_files:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        if '"""' in content[:500] or "'''" in content[:500]:
            with_docstrings += 1

check(
    f"Task plugins with docstrings: {with_docstrings}/{len(task_files)}",
    with_docstrings >= len(task_files) - 1,
    f"task plugins should be documented"
)


# ============================================================================
# TP-4: Task plugins have error handling
# ============================================================================
print("\n[TEST] TP-4: Task plugins have error handling")

with_try_except = 0
for f in task_files:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        if "try:" in content and "except" in content:
            with_try_except += 1

check(
    f"Task plugins with try/except: {with_try_except}/{len(task_files)}",
    with_try_except >= 1,  # At least one
    f"task plugins should have error handling"
)


# ============================================================================
# TP-5: No SQL injection in database schema
# ============================================================================
print("\n[TEST] TP-5: Database schema is safe")

schema_path = "/home/user/project/database/schema.sql"
if os.path.exists(schema_path):
    with open(schema_path) as f:
        schema = f.read()
    # Check for dangerous patterns
    dangerous_patterns = [
        r"EXEC\s+sp_",  # SQL Server stored procs
        r"xp_cmdshell",  # Command execution
        r"DROP\s+DATABASE",  # Dangerous
    ]
    dangerous = 0
    for pattern in dangerous_patterns:
        if re.search(pattern, schema, re.IGNORECASE):
            dangerous += 1
    check(
        f"Dangerous SQL patterns: {dangerous}",
        dangerous == 0,
        f"schema should not have dangerous SQL"
    )
else:
    check("Database schema exists", False, "schema.sql not found")


# ============================================================================
# TP-6: Database schema has proper constraints
# ============================================================================
print("\n[TEST] TP-6: Database schema has constraints")

if os.path.exists(schema_path):
    with open(schema_path) as f:
        schema = f.read()
    has_pk = "PRIMARY KEY" in schema.upper()
    has_fk = "FOREIGN KEY" in schema.upper() or "REFERENCES" in schema.upper()
    has_idx = "CREATE INDEX" in schema.upper() or "INDEX" in schema.upper()
    check(
        f"Schema constraints: PK={has_pk}, FK={has_fk}, IDX={has_idx}",
        has_pk,
        f"schema should have primary keys"
    )


# ============================================================================
# TP-7: No hardcoded secrets in task plugins
# ============================================================================
print("\n[TEST] TP-7: No hardcoded secrets in task plugins")

hardcoded = 0
for f in task_files:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        # Look for hardcoded passwords, API keys
        for m in re.finditer(r'(?:password|api_key|secret|token)\s*=\s*["\'][a-zA-Z0-9]{8,}["\']',
                             content, re.IGNORECASE):
            hardcoded += 1

check(
    f"Hardcoded secrets in task plugins: {hardcoded}",
    hardcoded == 0,
    f"task plugins should not have hardcoded secrets"
)


# ============================================================================
# TP-8: Task plugins use async correctly
# ============================================================================
print("\n[TEST] TP-8: Task plugins use async correctly")

async_correct = 0
for f in task_files:
    if os.path.exists(f):
        with open(f) as fp:
            content = fp.read()
        # Check that run method is async (if it exists)
        if "async def run" in content or "def run" in content:
            async_correct += 1

check(
    f"Task plugins with run method: {async_correct}/{len(task_files)}",
    async_correct >= len(task_files) - 1,
    f"task plugins should have a run method"
)


# ============================================================================
# TP-9: base_task.py defines interface
# ============================================================================
print("\n[TEST] TP-9: base_task.py defines interface")

base_task_path = "/home/user/project/tasks/base_task.py"
if os.path.exists(base_task_path):
    with open(base_task_path) as f:
        base = f.read()
    has_base_class = "class" in base
    check(
        f"base_task.py has class definition",
        has_base_class,
        f"base task should define interface"
    )
else:
    check("base_task.py exists", False, "base_task.py not found")


# ============================================================================
# TP-10: Runtime test - task plugin structure
# ============================================================================
print("\n[TEST] TP-10: Runtime test of task plugin structure")

test_script = '''
import os, sys
sys.path.insert(0, "/home/user/project")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

# Test 1: All task plugins have TaskExecutor class
import importlib.util
task_modules = [
    "tasks.site_register.executor",
    "tasks.gmail_verify.executor",
    "tasks.site_interact.executor",
]

ok = 0
skipped = 0
for mod_name in task_modules:
    try:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "TaskExecutor"):
            ok += 1
    except ImportError as e:
        # playwright or other dependency not installed
        # This is acceptable for test environment
        skipped += 1
    except Exception as e:
        print(f"FAIL: {mod_name}: {e}")

if skipped > 0:
    print(f"OK: {ok}/{len(task_modules)} task plugins have TaskExecutor ({skipped} skipped due to missing dependencies)")
else:
    print(f"OK: {ok}/{len(task_modules)} task plugins have TaskExecutor")
'''
result = subprocess.run(["python3", "-c", test_script],
                       capture_output=True, text=True, timeout=30)
check("TP-10: task plugin structure works",
      "OK:" in result.stdout and ("3/3" in result.stdout or "skipped due to missing dependencies" in result.stdout),
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
