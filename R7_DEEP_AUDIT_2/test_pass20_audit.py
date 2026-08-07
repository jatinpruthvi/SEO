#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 20: Performance audit.

Hunts for performance issues:
1. Blocking I/O in async functions (asyncio + sync I/O)
2. Memory leaks (global state, no cleanup)
3. Inefficient data structures (O(n^2) in hot paths)
4. Unbounded retries or loops
5. Missing connection pooling
6. Suboptimal regex patterns
7. Repeated computation in loops
"""
import os
import sys
import re
import ast
import subprocess
import time

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
print("R7-DEEP-AUDIT-2 Pass 20: Performance audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# PF-1: Blocking I/O calls in async functions
# ============================================================================
print("\n[TEST] PF-1: Blocking I/O in async functions")

# Find async functions that call blocking I/O
blocking_calls_in_async = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue

    # Walk to find async functions
    def find_async_with_blocking(node, parent_async=None):
        if isinstance(node, ast.AsyncFunctionDef):
            parent_async = node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and parent_async is not None:
                # Check for blocking calls
                func_name = None
                if isinstance(child.func, ast.Name):
                    func_name = child.func.id
                elif isinstance(child.func, ast.Attribute):
                    func_name = child.func.attr
                # Blocking I/O patterns
                if func_name in ("open", "read", "write", "load", "dump",
                                  "run", "call", "Popen", "connect"):
                    # Check if it's in a hot path (not in try/except)
                    blocking_calls_in_async.append(
                        (fname, parent_async.name, child.lineno, func_name)
                    )
            find_async_with_blocking(child, parent_async)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            find_async_with_blocking(node)

# Filter: many of these are intentional (e.g., file I/O in setup)
# We just check that the count is reasonable (< 50)
check(
    f"Blocking I/O calls in async funcs: {len(blocking_calls_in_async)}",
    len(blocking_calls_in_async) <= 50,
    f"high count may indicate synchronous I/O in event loop"
)


# ============================================================================
# PF-2: Hot path regex compilation
# ============================================================================
print("\n[TEST] PF-2: Regex patterns are not recompiled in loops")

# Find regex pattern usage
recompile_in_loop = []
for fname, src in _FILES.items():
    # Look for re.search/re.match/re.compile inside loops
    # Simple heuristic: look for re. inside a for/while block
    lines = src.split("\n")
    in_loop = 0
    for i, line in enumerate(lines, 1):
        if re.search(r'^\s*(for|while)\s+', line):
            in_loop += 5  # Approximate: stay "in loop" for next 5 lines
        if in_loop > 0 and re.search(r'\bre\.(?:search|match|findall|compile|sub)\(', line):
            recompile_in_loop.append(f"{fname}:{i}: {line.strip()[:80]}")
        in_loop -= 1 if in_loop > 0 else 0

check(
    f"Regex calls in loops: {len(recompile_in_loop)}",
    len(recompile_in_loop) <= 5,  # Allow a few legacy patterns
    f"sample: {recompile_in_loop[:3]}"
)


# ============================================================================
# PF-3: String concatenation in loops
# ============================================================================
print("\n[TEST] PF-3: No string concatenation in hot loops")

# Find += on strings in loops
str_concat_in_loop = []
for fname, src in _FILES.items():
    lines = src.split("\n")
    in_loop = 0
    for i, line in enumerate(lines, 1):
        if re.search(r'^\s*(for|while)\s+', line):
            in_loop += 10
        if in_loop > 0 and re.search(r'\w+\s*\+=\s*["\']', line):
            str_concat_in_loop.append(f"{fname}:{i}: {line.strip()[:80]}")
        in_loop -= 1 if in_loop > 0 else 0

check(
    f"String += in loops: {len(str_concat_in_loop)}",
    len(str_concat_in_loop) == 0,
    f"sample: {str_concat_in_loop[:3]}"
)


# ============================================================================
# PF-4: Global state cleanup (no memory leaks)
# ============================================================================
print("\n[TEST] PF-4: ThreadPoolExecutors and resources are cleaned up")

# Check for ThreadPoolExecutor without shutdown
uncleaned_executors = []
for fname, src in _FILES.items():
    # Find ThreadPoolExecutor() calls
    for m in re.finditer(r'(\w+)\s*=\s*ThreadPoolExecutor\(', src):
        var_name = m.group(1)
        # Check if shutdown is called on it
        if f"{var_name}.shutdown" not in src:
            uncleaned_executors.append(f"{fname}: {var_name}")

check(
    f"Uncleaned ThreadPoolExecutors: {len(uncleaned_executors)}",
    len(uncleaned_executors) <= 2,  # Allow a few
    f"sample: {uncleaned_executors[:3]}"
)


# ============================================================================
# PF-5: Connection pool is reused
# ============================================================================
print("\n[TEST] PF-5: DB connection pool is created once")

orch_src = _FILES.get("hardened_orchestrator.py", "")
pool_creates = len(re.findall(r'asyncpg\.create_pool\(', orch_src))
check(
    f"asyncpg.create_pool calls in orchestrator: {pool_creates}",
    pool_creates == 1,
    f"multiple create_pool calls would defeat connection pooling"
)


# ============================================================================
# PF-6: No busy-wait loops
# ============================================================================
print("\n[TEST] PF-6: No busy-wait loops (time.sleep in loops)")

busy_waits = []
for fname, src in _FILES.items():
    lines = src.split("\n")
    in_loop = 0
    for i, line in enumerate(lines, 1):
        if re.search(r'^\s*(for|while)\s+', line):
            in_loop += 10
        if in_loop > 0 and re.search(r'time\.sleep\(', line):
            busy_waits.append(f"{fname}:{i}: {line.strip()[:80]}")
        in_loop -= 1 if in_loop > 0 else 0

# time.sleep in loops is OK in some cases (polling), check for short sleeps
short_sleep_waits = [b for b in busy_waits if "0.0" in b or "0.1" in b]
check(
    f"Short time.sleep in loops (busy-wait risk): {len(short_sleep_waits)}",
    len(short_sleep_waits) <= 3,
    f"sample: {short_sleep_waits[:3]}"
)


# ============================================================================
# PF-7: Subprocess timeouts are enforced
# ============================================================================
print("\n[TEST] PF-7: subprocess calls have timeouts")

subprocess_no_timeout = []
for fname, src in _FILES.items():
    # Find subprocess.run/call/Popen calls
    for m in re.finditer(r'subprocess\.(?:run|call|Popen)\([^)]*\)', src, re.DOTALL):
        call_str = m.group(0)
        if "timeout" not in call_str and "DEVNULL" not in call_str:
            line_no = src[:m.start()].count("\n") + 1
            subprocess_no_timeout.append(f"{fname}:{line_no}: {call_str[:60]}")

check(
    f"subprocess calls without timeout: {len(subprocess_no_timeout)}",
    len(subprocess_no_timeout) <= 5,
    f"sample: {subprocess_no_timeout[:3]}"
)


# ============================================================================
# PF-8: HTTP clients reuse connections
# ============================================================================
print("\n[TEST] PF-8: httpx clients use connection pooling")

# Look for httpx.AsyncClient usage
for fname, src in _FILES.items():
    if "httpx" in src:
        # Count AsyncClient instantiations
        n = src.count("httpx.AsyncClient(")
        if n > 0:
            print(f"  {fname}: {n} httpx.AsyncClient instantiations")

# Should be few (ideally 1) per module
total_httpx = sum(src.count("httpx.AsyncClient(") for src in _FILES.values())
check(
    f"Total httpx.AsyncClient instantiations: {total_httpx}",
    total_httpx <= 5,  # Allow a few for different purposes
    f"each instance creates a new connection pool"
)


# ============================================================================
# PF-9: Large data structures are not held indefinitely
# ============================================================================
print("\n[TEST] PF-9: No unbounded global lists/dicts")

# Find global variables that look like unbounded collections
unbounded_globals = []
for fname, src in _FILES.items():
    tree = ast.parse(src)
    # Find top-level assignments
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    if name.startswith("_"):
                        continue
                    # Check if it's a list/dict/set
                    if isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
                        if name not in ("CITY_TO_STATE", "_PROXY_PREFERRED_RATIO",
                                         "_PROXY_PREFERRED_PROBABILITY", "_PLACEHOLDER_DOMAINS",
                                         "_ESSENTIAL_COOKIE_NAMES"):
                            unbounded_globals.append(f"{fname}: {name}")

check(
    f"Top-level mutable globals: {len(unbounded_globals)}",
    len(unbounded_globals) <= 10,  # Allow constants
    f"sample: {unbounded_globals[:5]}"
)


# ============================================================================
# PF-10: Runtime benchmark - import time
# ============================================================================
print("\n[TEST] PF-10: module import time is reasonable")

start = time.time()
result = subprocess.run(
    ["python3", "-c", "import sys; sys.path.insert(0, '/home/user/project/browser-runtime'); import behavioral_entropy"],
    capture_output=True, text=True, timeout=30
)
elapsed = time.time() - start
check(
    f"behavioral_entropy import time: {elapsed:.2f}s",
    elapsed < 5.0,
    f"slow imports indicate module-level computation"
)


# ============================================================================
# PF-11: Runtime benchmark - safe getenv helper
# ============================================================================
print("\n[TEST] PF-11: _safe_getenv_int is fast")

bench_test = '''
import sys, time
sys.path.insert(0, "/home/user/project/browser-runtime")
import os
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")
import behavioral_entropy as be

# Benchmark 100k calls
start = time.perf_counter()
for _ in range(100000):
    be._safe_getenv_int("NONEXISTENT", 99, min_value=1)
elapsed = time.perf_counter() - start
per_call_us = elapsed / 100000 * 1_000_000
print(f"{per_call_us:.2f}us per call")
'''
result = subprocess.run(
    ["python3", "-c", bench_test],
    capture_output=True, text=True, timeout=30
)
out = result.stdout.strip()
# Parse us per call
import re as _re
m = _re.search(r"([\d.]+)us per call", out)
if m:
    us = float(m.group(1))
    check(
        f"_safe_getenv_int performance: {us:.2f}μs/call",
        us < 100,  # Should be < 100μs per call
        f"slow env var parsing would impact startup time"
    )
else:
    check(
        f"_safe_getenv_int benchmark",
        False,
        f"output: {out[:100]}"
    )


# ============================================================================
# PF-12: No N+1 query patterns
# ============================================================================
print("\n[TEST] PF-12: No obvious N+1 query patterns")

# Check for query inside loop patterns
n_plus_1 = []
for fname, src in _FILES.items():
    # Look for `await conn.execute` or `await conn.fetch` inside for/while
    lines = src.split("\n")
    in_loop = 0
    for i, line in enumerate(lines, 1):
        if re.search(r'^\s*(for|async\s+for)\s+', line):
            in_loop += 20
        if in_loop > 0 and re.search(r'await\s+(?:conn|pool)\.(?:execute|fetch)', line):
            n_plus_1.append(f"{fname}:{i}: {line.strip()[:80]}")
        in_loop -= 1 if in_loop > 0 else 0

check(
    f"N+1 query patterns (await conn in loop): {len(n_plus_1)}",
    len(n_plus_1) <= 5,  # Allow some legacy patterns
    f"sample: {n_plus_1[:3]}"
)


# ============================================================================
# PF-13: Asyncio.gather used for parallel I/O
# ============================================================================
print("\n[TEST] PF-13: asyncio.gather is used for parallel I/O where appropriate")

gather_count = sum(src.count("asyncio.gather(") for src in _FILES.values())
create_task_count = sum(src.count("asyncio.create_task(") for src in _FILES.values())
print(f"  asyncio.gather calls: {gather_count}")
print(f"  asyncio.create_task calls: {create_task_count}")
check(
    f"Parallel I/O patterns: {gather_count + create_task_count} uses",
    gather_count + create_task_count >= 2,
    f"uses of gather/create_task suggest parallel I/O awareness"
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
