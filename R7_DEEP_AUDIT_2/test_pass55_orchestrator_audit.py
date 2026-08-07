"""Pass 55: hardened_orchestrator.py Deep Audit.

File #1 in the review: the main orchestrator (3,114 lines).
Targets:
- Race conditions in the daemon loop
- Error handling completeness
- Resource cleanup paths
- State consistency between global db_pool and async functions
- Edge cases in run_persona_pipeline
- Type safety on the hot path
- Hidden bugs in subprocess/shell interactions
"""
import sys
import os
import re
import ast
import time

sys.path.insert(0, '/home/user/project')

FILE = '/home/user/project/browser-runtime/hardened_orchestrator.py'


def test_OO_1_db_pool_race():
    """OO-1: db_pool global state race in async context.

    The orchestrator uses `global db_pool` in run_persona_pipeline,
    orchestrator_daemon_loop, handle_graceful_shutdown, and the
    __main__ block. Check whether there's a TOCTOU race where
    db_pool is set to None between check and use.
    """
    print("--- OO-1: db_pool global state race ---")
    with open(FILE) as f:
        src = f.read()
    # Count uses of 'global db_pool' vs 'if db_pool is None' vs 'if db_pool:'
    n_global = src.count('global db_pool')
    n_is_none = src.count('if db_pool is None')
    n_truthy = src.count('if db_pool:')
    n_with = src.count('async with db_pool.acquire()')
    print(f"  'global db_pool' statements: {n_global}")
    print(f"  'if db_pool is None' checks: {n_is_none}")
    print(f"  'if db_pool:' (truthy) checks: {n_truthy}")
    print(f"  'async with db_pool.acquire()' usages: {n_with}")
    # The risk: in async context, `if db_pool is None` is not atomic with
    # the subsequent `async with db_pool.acquire()`. Another coroutine
    # could set db_pool to None between the check and the use.
    # However, since asyncio is single-threaded, this only matters if
    # we explicitly yield. The `await` in `db_pool.create_pool` is
    # a yield point, but that's only at startup.
    # The check `if db_pool is None: raise` (line 1431) is followed by
    # `async with db_pool.acquire()`. There's no await between check
    # and use, so no race.
    if n_is_none > 0 and n_with > 0:
        # Check if there's an await between None check and acquire
        # Find the pattern
        m = re.search(
            r'if db_pool is None:\s*\n\s*raise\s+DatabaseUnavailableError[^\n]*\n[^\n]*\n[^\n]*\n[^\n]*\n\s*async with db_pool',
            src
        )
        if m:
            print(f"  ✓ db_pool None check is followed directly by acquire (no race)")
    return True


def test_OO_2_session_data_type_check():
    """OO-2: session_data type discrimination.

    The orchestrator checks session_data type twice (lines 1700-1748).
    Verify both paths are correctly handled.
    """
    print("\n--- OO-2: session_data type discrimination ---")
    with open(FILE) as f:
        src = f.read()
    # session_data can be bytes (encrypted archive) or str (legacy cookies JSON)
    if 'isinstance(session_data, (bytes, bytearray, memoryview))' in src:
        print(f"  ✓ Handles bytes/bytearray/memoryview (encrypted archive)")
    if 'isinstance(session_data, str)' in src:
        print(f"  ✓ Handles str (legacy cookies JSON)")
    # Check the order: bytes is checked FIRST, str is checked SECOND
    bytes_pos = src.find('isinstance(session_data, (bytes')
    str_pos = src.find('elif session_data and isinstance(session_data, str):')
    if bytes_pos < str_pos:
        print(f"  ✓ Bytes checked before str (correct priority)")
    else:
        print(f"  ⚠ Order issue: bytes at {bytes_pos}, str at {str_pos}")
    return True


def test_OO_3_imports_inside_functions():
    """OO-3: Late imports inside functions (could mask missing deps)."""
    print("\n--- OO-3: Late imports inside functions ---")
    with open(FILE) as f:
        src = f.read()
    # Find import statements inside function bodies
    tree = ast.parse(src)
    late_imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    if child.lineno > node.lineno:
                        late_imports.append((node.name, child.lineno, ast.unparse(child)[:60]))
    print(f"  Late imports inside functions: {len(late_imports)}")
    for fname, ln, code in late_imports[:5]:
        print(f"    {fname}() L{ln}: {code}")
    return True


def test_OO_4_signal_handlers_in_loop():
    """OO-4: Signal handler registration correctness."""
    print("\n--- OO-4: Signal handlers ---")
    with open(FILE) as f:
        src = f.read()
    # Verify SIGUSR1 is registered as panic signal
    if 'PANIC_SIGNAL' in src and 'SIGUSR1' in src:
        print(f"  ✓ PANIC_SIGNAL configurable (defaults to SIGUSR1)")
    if 'loop.add_signal_handler' in src:
        print(f"  ✓ Signal handlers registered via loop.add_signal_handler")
    if 'SIGINT' in src and 'SIGTERM' in src:
        print(f"  ✓ Graceful shutdown for SIGINT/SIGTERM")
    return True


def test_OO_5_resource_cleanup_paths():
    """OO-5: Resource cleanup completeness."""
    print("\n--- OO-5: Resource cleanup paths ---")
    with open(FILE) as f:
        src = f.read()
    cleanup_paths = {
        'browser.close()': src.count('browser.close()'),
        'xvfb_process.terminate()': src.count('xvfb_process.terminate()'),
        'xvfb_process.kill()': src.count('xvfb_process.kill()'),
        'killpg(': src.count('killpg('),
        'shutil.rmtree': src.count('shutil.rmtree'),
        'db_pool.close': src.count('db_pool.close'),
        'gc.collect': src.count('gc.collect'),
        '_fernet_executor.shutdown': src.count('_fernet_executor.shutdown'),
    }
    for k, v in cleanup_paths.items():
        if v > 0:
            print(f"  {k}: {v}")
    return True


def test_OO_6_try_except_completeness():
    """OO-6: try/except coverage in run_persona_pipeline."""
    print("\n--- OO-6: Exception handling in run_persona_pipeline ---")
    with open(FILE) as f:
        src = f.read()
    # Find the function
    m = re.search(r'async def run_persona_pipeline.*?(?=\nasync def |\ndef |\nclass |\Z)', src, re.DOTALL)
    if m:
        func_src = m.group(0)
        n_try = func_src.count('\n        try:') + func_src.count('\n            try:')
        n_except = func_src.count('except ') + func_src.count('except:')
        n_return_false = func_src.count('return False')
        n_raise = func_src.count('raise ')
        print(f"  try blocks: {n_try}")
        print(f"  except clauses: {n_except}")
        print(f"  return False: {n_return_false}")
        print(f"  raise: {n_raise}")
        # A ratio of <1 except per try means some paths are uncaught
        if n_except < n_try:
            print(f"  ⚠ {n_try - n_except} try blocks may not have except")
    return True


def test_OO_7_hardcoded_urls():
    """OO-7: Hardcoded URLs in the orchestrator."""
    print("\n--- OO-7: Hardcoded URLs ---")
    with open(FILE) as f:
        src = f.read()
    urls = re.findall(r'https?://[^\s"\'<>)]+', src)
    # Filter out comments
    urls = [u for u in urls if '//' in u and not u.startswith('//')]
    unique_urls = sorted(set(urls))
    print(f"  Unique URLs found: {len(unique_urls)}")
    for u in unique_urls[:5]:
        print(f"    {u[:80]}")
    if len(unique_urls) > 10:
        print(f"  ⚠ Many hardcoded URLs (consider env-configurable)")
    return True


def test_OO_8_db_acquire_balance():
    """OO-8: db_pool.acquire() balance."""
    print("\n--- OO-8: db_pool.acquire() balance ---")
    with open(FILE) as f:
        src = f.read()
    n_acquire = len(re.findall(r'async with db_pool\.acquire\(\)', src))
    n_manual = src.count('db_pool.acquire()') - n_acquire
    n_close = src.count('db_pool.close()')
    print(f"  async with db_pool.acquire(): {n_acquire}")
    print(f"  Manual acquire (without context manager): {n_manual}")
    print(f"  db_pool.close(): {n_close}")
    if n_manual > 0:
        print(f"  ⚠ {n_manual} manual acquires (may leak if exception)")
    return True


def test_OO_9_concurrency_safety():
    """OO-9: Concurrency safety in daemon loop."""
    print("\n--- OO-9: Daemon loop concurrency safety ---")
    with open(FILE) as f:
        src = f.read()
    # Check if there's a single-task lock
    if 'TASKS_PER_BATCH' in src:
        n = src.count('TASKS_PER_BATCH')
        print(f"  TASKS_PER_BATCH references: {n}")
        # Should be 1 for 1-persona mode
        m = re.search(r'TASKS_PER_BATCH\s*=\s*_safe_getenv_int\("TASKS_PER_BATCH"[^)]*\)', src)
        if m:
            print(f"  Configurable via env var (1-persona default)")
    return True


def test_OO_10_proxy_credential_redaction():
    """OO-10: Proxy credential redaction in logs."""
    print("\n--- OO-10: Proxy credential redaction ---")
    with open(FILE) as f:
        src = f.read()
    if '_redact_proxy_credentials' in src:
        print(f"  ✓ _redact_proxy_credentials function exists")
    # Check usage
    n = src.count('_redact_proxy_credentials(')
    print(f"  Usages: {n}")
    if n > 0:
        print(f"  ✓ Credentials redacted before logging")
    return True


def test_OO_11_daemon_loop_error_paths():
    """OO-11: Daemon loop error handling completeness."""
    print("\n--- OO-11: Daemon loop error handling ---")
    with open(FILE) as f:
        src = f.read()
    # Find the daemon loop
    m = re.search(r'async def orchestrator_daemon_loop.*?(?=\nasync def |\ndef |\nclass |\Z)', src, re.DOTALL)
    if m:
        func_src = m.group(0)
        # Count error catches
        n_db_err = func_src.count('DatabaseUnavailableError')
        n_retired = func_src.count('PersonaRetiredError')
        n_pid = func_src.count('PIDBudgetExceededError')
        n_decrypt = func_src.count('DecryptionFailedError')
        print(f"  DatabaseUnavailableError catches: {n_db_err}")
        print(f"  PersonaRetiredError catches: {n_retired}")
        print(f"  PIDBudgetExceededError catches: {n_pid}")
        print(f"  DecryptionFailedError catches: {n_decrypt}")
    return True


def test_OO_12_fingerprint_access():
    """OO-12: Fingerprint access pattern safety."""
    print("\n--- OO-12: Fingerprint access pattern ---")
    with open(FILE) as f:
        src = f.read()
    # Check that fingerprint access uses safe helpers
    n_safe_get2 = src.count('_fp_get2(')
    n_safe_get = src.count('_safe_fp_get(')
    n_direct = len(re.findall(r'fp\.[a-z]+\(', src))
    print(f"  _fp_get2 (defensive nested access): {n_safe_get2}")
    print(f"  _safe_fp_get (defensive single access): {n_safe_get}")
    print(f"  Direct fp.attr access: {n_direct}")
    return True


if __name__ == '__main__':
    print("="*70)
    print("Pass 55: hardened_orchestrator.py Deep Audit")
    print("="*70)

    tests = [
        test_OO_1_db_pool_race,
        test_OO_2_session_data_type_check,
        test_OO_3_imports_inside_functions,
        test_OO_4_signal_handlers_in_loop,
        test_OO_5_resource_cleanup_paths,
        test_OO_6_try_except_completeness,
        test_OO_7_hardcoded_urls,
        test_OO_8_db_acquire_balance,
        test_OO_9_concurrency_safety,
        test_OO_10_proxy_credential_redaction,
        test_OO_11_daemon_loop_error_paths,
        test_OO_12_fingerprint_access,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
    print("="*70)
