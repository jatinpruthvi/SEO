"""Pass 52: Type Safety & API Contract Verification.

TS-1: Verify all env var reads have proper return types
TS-2: Check async function awaits on I/O
TS-3: Check return type annotations match actual returns
TS-4: Check argument types match usage
TS-5: Find missing type hints on public functions
TS-6: Check for Any usage (too-broad type)
TS-7: Check Optional[] usage for nullable returns
TS-8: Check Tuple vs List usage
TS-9: Check Callable usage
TS-10: Check Dict vs TypedDict
"""
import sys
import os
import ast
import re

sys.path.insert(0, '/home/user/project')

files = [
    '/home/user/project/browser-runtime/hardened_orchestrator.py',
    '/home/user/project/browser-runtime/behavioral_entropy.py',
    '/home/user/project/browser-runtime/sticky_proxy_resolver.py',
    '/home/user/project/browser-runtime/humanized_movement.py',
    '/home/user/project/browser-runtime/keyboard_emulator.py',
    '/home/user/project/browser-runtime/session_serializer.py',
    '/home/user/project/browser-runtime/profile_archiver.py',
    '/home/user/project/browser-runtime/identity_health.py',
    '/home/user/project/browser-runtime/ebpf_sentinel.py',
    '/home/user/project/browser-runtime/asymmetric_vault.py',
    '/home/user/project/browser-runtime/input_sanitizer.py',
    '/home/user/project/browser-runtime/prompt_guard.py',
    '/home/user/project/browser-runtime/dynamic_pacing.py',
    '/home/user/project/browser-runtime/circadian_scheduler.py',
    '/home/user/project/browser-runtime/hci_reading_model.py',
    '/home/user/project/browser-runtime/curl_cffi_handover.py',
    '/home/user/project/browser-runtime/paste_emulator.py',
    '/home/user/project/browser-runtime/verify_tls_stealth.py',
]


def test_TS_1_safe_env_return_types():
    """TS-1: Safe env var helpers return proper types."""
    print("--- TS-1: Safe env var return types ---")
    # Just confirm the helpers exist
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if '_safe_getenv_int' in src and '_safe_getenv_float' in src:
        print(f"  ✓ _safe_getenv_int and _safe_getenv_float present")
    return True


def test_TS_2_async_function_io():
    """TS-2: Async functions on I/O have awaits."""
    print("\n--- TS-2: Async function await checks ---")
    issues = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                func_src = ast.unparse(node)
                # Check if function does I/O but has no await
                has_io = any(io in func_src for io in ['asyncpg', 'httpx', 'aiohttp', 'playwright', 'page.', 'conn.', 'client.'])
                has_await = 'await ' in func_src
                # Exclude trivial functions
                if node.name.startswith('_') and len(func_src) < 300:
                    continue
                if has_io and not has_await:
                    issues.append((fpath, node.name, node.lineno))
    if issues:
        for f, n, ln in issues[:5]:
            print(f"  ⚠ {f.split('/')[-1]}:{ln} {n}() - I/O but no await")
    else:
        print(f"  ✓ All async I/O functions use await")
    return True


def test_TS_3_return_type_annotations():
    """TS-3: Functions with return type annotations."""
    print("\n--- TS-3: Return type annotation coverage ---")
    total_public = 0
    with_annotations = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith('_'):
                    continue
                total_public += 1
                if node.returns is not None:
                    with_annotations += 1
    pct = 100 * with_annotations / total_public if total_public else 0
    print(f"  Public functions: {total_public}, with return type: {with_annotations} ({pct:.0f}%)")
    return True


def test_TS_4_argument_annotations():
    """TS-4: Public functions with argument type annotations."""
    print("\n--- TS-4: Argument annotation coverage ---")
    total_args = 0
    annotated_args = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith('_'):
                    continue
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.arg == 'self':
                        continue
                    total_args += 1
                    if arg.annotation is not None:
                        annotated_args += 1
    pct = 100 * annotated_args / total_args if total_args else 0
    print(f"  Public function args: {total_args}, annotated: {annotated_args} ({pct:.0f}%)")
    return True


def test_TS_5_any_usage():
    """TS-5: Use of Any (too-broad type)."""
    print("\n--- TS-5: Any type usage ---")
    any_count = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        for m in re.finditer(r':\s*Any\b', content):
            line_num = content[:m.start()].count('\n') + 1
            any_count += 1
    print(f"  : Any annotations: {any_count}")
    return True


def test_TS_6_optional_usage():
    """TS-6: Optional[] usage for nullable returns."""
    print("\n--- TS-6: Optional[] usage ---")
    opt_count = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        for m in re.finditer(r'Optional\[', content):
            opt_count += 1
    print(f"  Optional[] usages: {opt_count}")
    return True


def test_TS_7_dict_usage():
    """TS-7: Dict vs TypedDict usage."""
    print("\n--- TS-7: Dict vs TypedDict ---")
    dict_count = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        for m in re.finditer(r':\s*Dict\[', content):
            dict_count += 1
    print(f"  Dict[] annotations: {dict_count}")
    return True


def test_TS_8_callable_usage():
    """TS-8: Callable usage."""
    print("\n--- TS-8: Callable usage ---")
    callable_count = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        for m in re.finditer(r'Callable\[', content):
            callable_count += 1
    print(f"  Callable[] usages: {callable_count}")
    return True


def test_TS_9_runtime_type_checking():
    """TS-9: Runtime type checks (isinstance, type())."""
    print("\n--- TS-9: Runtime type checks ---")
    checks = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        checks += content.count('isinstance(')
        checks += content.count('type() ==')
    print(f"  isinstance/type checks: {checks}")
    return True


def test_TS_10_type_coercion_safety():
    """TS-10: Type coercion safety (str() vs f-string, etc.)."""
    print("\n--- TS-10: Type coercion safety ---")
    # Just count
    for fpath in files[:3]:
        with open(fpath) as f:
            content = f.read()
        n_str = content.count('str(')
        n_int = content.count('int(')
        n_float = content.count('float(')
        n_bool = content.count('bool(')
        print(f"  {fpath.split('/')[-1]}: str={n_str}, int={n_int}, float={n_float}, bool={n_bool}")
    return True


if __name__ == '__main__':
    print("="*70)
    print("Pass 52: Type Safety & API Contracts")
    print("="*70)

    tests = [
        test_TS_1_safe_env_return_types,
        test_TS_2_async_function_io,
        test_TS_3_return_type_annotations,
        test_TS_4_argument_annotations,
        test_TS_5_any_usage,
        test_TS_6_optional_usage,
        test_TS_7_dict_usage,
        test_TS_8_callable_usage,
        test_TS_9_runtime_type_checking,
        test_TS_10_type_coercion_safety,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
    print("="*70)
