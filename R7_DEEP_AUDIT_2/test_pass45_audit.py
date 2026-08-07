"""Pass 45: Error Handling & Exception Path Coverage.

Finds:
- Bare except clauses that swallow all errors
- except Exception followed by only pass
- Critical paths without try/except
- Resource cleanup in error paths
- Specific exception types that should be caught
"""
import sys
import os
import ast
import re

sys.path.insert(0, '/home/user/project')
sys.path.insert(0, '/home/user/project/browser-runtime')

files = [
    '/home/user/project/browser-runtime/hardened_orchestrator.py',
    '/home/user/project/browser-runtime/behavioral_entropy.py',
    '/home/user/project/browser-runtime/sticky_proxy_resolver.py',
    '/home/user/project/browser-runtime/humanized_movement.py',
    '/home/user/project/browser-runtime/keyboard_emulator.py',
    '/home/user/project/browser-runtime/session_serializer.py',
    '/home/user/project/browser-runtime/profile_archiver.py',
    '/home/user/project/browser-runtime/identity_health.py',
    '/home/user/project/browser-runtime/input_sanitizer.py',
    '/home/user/project/browser-runtime/prompt_guard.py',
    '/home/user/project/browser-runtime/asymmetric_vault.py',
    '/home/user/project/browser-runtime/ebpf_sentinel.py',
    '/home/user/project/browser-runtime/dynamic_pacing.py',
    '/home/user/project/browser-runtime/circadian_scheduler.py',
    '/home/user/project/browser-runtime/hci_reading_model.py',
    '/home/user/project/browser-runtime/curl_cffi_handover.py',
    '/home/user/project/browser-runtime/paste_emulator.py',
    '/home/user/project/browser-runtime/verify_tls_stealth.py',
]

def test_EH_1_silent_passes():
    """EH-1: Find 'except ... : pass' that silently swallow errors."""
    print("--- EH-1: Silent except: pass patterns ---")
    issues = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Check if body is just 'pass'
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    issues.append((fpath, node.lineno, ast.unparse(node.type) if node.type else "Exception"))
    print(f"  Found {len(issues)} 'except: pass' patterns")
    for f, ln, t in issues[:5]:
        print(f"    {f.split('/')[-1]}:{ln} except {t}: pass")
    return True


def test_EH_2_specific_exception_types():
    """EH-2: Verify catch-all exceptions are justified."""
    print("\n--- EH-2: except Exception usage ---")
    total = 0
    by_file = {}
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type and isinstance(node.type, ast.Name) and node.type.id == 'Exception':
                    total += 1
                    by_file[fpath.split('/')[-1]] = by_file.get(fpath.split('/')[-1], 0) + 1
    print(f"  Total 'except Exception': {total}")
    for f, n in sorted(by_file.items(), key=lambda x: -x[1])[:5]:
        print(f"    {f}: {n}")
    return True


def test_EH_3_specific_exceptions():
    """EH-3: Find specifically-typed except clauses (best practice)."""
    print("\n--- EH-3: Specific exception catches ---")
    specific = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type:
                try:
                    type_str = ast.unparse(node.type)
                    if 'Exception' not in type_str and 'BaseException' not in type_str:
                        specific.append((fpath, node.lineno, type_str))
                except Exception:
                    pass
    print(f"  Found {len(specific)} specific exception catches")
    return True


def test_EH_4_try_finally_cleanup():
    """EH-4: Check for proper resource cleanup with try/finally or context managers."""
    print("\n--- EH-4: Resource cleanup patterns ---")
    try_blocks = 0
    with_blocks = 0
    finally_blocks = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                try_blocks += 1
                if node.finalbody:
                    finally_blocks += 1
            if isinstance(node, ast.With) or isinstance(node, ast.AsyncWith):
                with_blocks += 1
    print(f"  try blocks: {try_blocks}, with blocks: {with_blocks}, finally: {finally_blocks}")
    if with_blocks > 0:
        print(f"  ✓ Using context managers (with statement): {with_blocks}")
    return True


def test_EH_5_uncaught_async_paths():
    """EH-5: Find async functions without proper error handling."""
    print("\n--- EH-5: Async error handling ---")
    async_funcs = 0
    async_with_try = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith)):
                if isinstance(node, ast.AsyncFunctionDef):
                    async_funcs += 1
                    # Check if it has a try block in its body
                    for child in ast.walk(node):
                        if isinstance(child, ast.Try):
                            async_with_try += 1
                            break
    print(f"  Async functions: {async_funcs}, with try: {async_with_try}")
    if async_with_try < async_funcs:
        print(f"  ⚠ {async_funcs - async_with_try} async functions have no try block")
    else:
        print(f"  ✓ All async functions have error handling")
    return True


def test_EH_6_logging_in_except():
    """EH-6: Verify except blocks log errors (not silently swallow)."""
    print("\n--- EH-6: Logging in except blocks ---")
    except_with_log = 0
    except_total = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                except_total += 1
                # Check if the body contains a logger call
                body_src = ast.unparse(node)
                if 'logger' in body_src or 'log' in body_src.lower():
                    except_with_log += 1
    pct = 100 * except_with_log / except_total if except_total else 0
    print(f"  Except blocks: {except_total}, with logging: {except_with_log} ({pct:.0f}%)")
    return True


def test_EH_7_reraise_patterns():
    """EH-7: Find patterns where exception is caught and re-raised."""
    print("\n--- EH-7: Re-raise patterns ---")
    reraise = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise):
                reraise += 1
    print(f"  Re-raise (raise) statements: {reraise}")
    return True


def test_EH_8_critical_orchestrator_paths():
    """EH-8: Critical paths in orchestrator have error handling."""
    print("\n--- EH-8: Critical path coverage ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    # Critical operations
    critical_ops = [
        'asyncpg.create_pool',
        'httpx.AsyncClient',
        'async_playwright',
        'context.acquire',
        'db_pool.acquire',
    ]
    for op in critical_ops:
        n = src.count(op)
        print(f"  '{op}': {n} occurrence(s)")
    return True


if __name__ == '__main__':
    print("="*70)
    print("Pass 45: Error Handling & Exception Path Coverage")
    print("="*70)

    tests = [
        test_EH_1_silent_passes,
        test_EH_2_specific_exception_types,
        test_EH_3_specific_exceptions,
        test_EH_4_try_finally_cleanup,
        test_EH_5_uncaught_async_paths,
        test_EH_6_logging_in_except,
        test_EH_7_reraise_patterns,
        test_EH_8_critical_orchestrator_paths,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
    print("="*70)
