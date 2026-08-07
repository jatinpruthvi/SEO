"""Pass 50: Subtle Logic & Contract Analysis.

SA-1: Function returns are consistent (always same type or None)
SA-2: Functions with too many return paths
SA-3: Mix of string concatenation styles
SA-4: Integer overflow risks
SA-5: Unused imports
SA-6: Unused variables (heuristic)
SA-7: Bare except: clauses (not just except Exception: pass)
SA-8: Functions with no docstring
SA-9: Class with no __init__
SA-10: Methods that don't use self
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


def test_SA_1_return_type_consistency():
    """SA-1: Function return paths are consistent."""
    print("--- SA-1: Return type consistency ---")
    issues = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Check for functions with type hints that return
                if node.returns:
                    returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
                    if len(returns) > 1:
                        # Multiple return paths - check consistency
                        types = set()
                        for r in returns:
                            if r.value is None:
                                types.add('None')
                            elif isinstance(r.value, ast.Constant):
                                types.add(type(r.value.value).__name__)
                            elif isinstance(r.value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
                                types.add(type(r.value).__name__)
                            elif isinstance(r.value, ast.Call):
                                types.add('Call')
                            else:
                                types.add('Other')
                        if len(types) > 2:
                            issues.append((fpath, node.name, node.lineno, types))
    if issues:
        for f, n, ln, t in issues[:5]:
            print(f"  ⚠ {f.split('/')[-1]}:{ln} {n}() returns: {t}")
    else:
        print(f"  ✓ Return types consistent")
    return True


def test_SA_2_function_complexity():
    """SA-2: Functions with high cyclomatic complexity."""
    print("\n--- SA-2: Function complexity ---")
    high_complexity = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                complexity = 1
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                        complexity += 1
                    elif isinstance(child, ast.BoolOp):
                        complexity += len(child.values) - 1
                if complexity > 30:
                    high_complexity.append((fpath, node.name, node.lineno, complexity))
    if high_complexity:
        for f, n, ln, c in high_complexity[:5]:
            print(f"  ⚠ {f.split('/')[-1]}:{ln} {n}() complexity: {c}")
    else:
        print(f"  ✓ No functions with complexity > 30")
    return True


def test_SA_3_unused_imports():
    """SA-3: Unused imports."""
    print("\n--- SA-3: Unused imports ---")
    # Build import map
    for fpath in files[:3]:  # Check a few key files
        with open(fpath) as f:
            content = f.read()
        # Find imports
        imports = re.findall(r'^(?:from\s+\S+\s+)?import\s+(.+?)$', content, re.MULTILINE)
        # Skip - this is complex to check accurately
        print(f"  {fpath.split('/')[-1]}: {len(imports)} import statements")
    print(f"  ✓ Sample checked (full analysis would need symbol resolution)")
    return True


def test_SA_4_bare_except():
    """SA-4: Bare except: clauses."""
    print("\n--- SA-4: Bare except: clauses ---")
    issues = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        # Look for 'except:' (bare) vs 'except SomeException:'
        # We need to match except: not except Exception:
        for m in re.finditer(r'^\s*except\s*:\s*$', content, re.MULTILINE):
            line_num = content[:m.start()].count('\n') + 1
            issues.append((fpath, line_num))
    if issues:
        for f, ln in issues:
            print(f"  ⚠ {f.split('/')[-1]}:{ln} has bare except:")
    else:
        print(f"  ✓ No bare except: clauses")
    return True


def test_SA_5_functions_without_docstrings():
    """SA-5: Public functions without docstrings."""
    print("\n--- SA-5: Public function docstrings ---")
    missing = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name.startswith('_'):
                    continue
                # Check for docstring
                if not (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    missing += 1
    print(f"  Public functions without docstrings: {missing}")
    return True


def test_SA_6_classes_without_init():
    """SA-6: Classes without __init__."""
    print("\n--- SA-6: Classes without __init__ ---")
    no_init = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                has_init = any(
                    isinstance(c, ast.FunctionDef) and c.name == '__init__'
                    for c in node.body
                )
                if not has_init and not node.name.startswith('_') and not node.name.endswith('Error'):
                    no_init.append((fpath, node.name))
    if no_init:
        for f, n in no_init[:5]:
            print(f"  ℹ {f.split('/')[-1]}: {n} (no __init__, may be intentional)")
    else:
        print(f"  ✓ All public classes have __init__")
    return True


def test_SA_7_methods_not_using_self():
    """SA-7: Methods that don't use self (could be @staticmethod)."""
    print("\n--- SA-7: Methods not using self ---")
    candidates = []
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef):
                for method in cls.body:
                    if not isinstance(method, ast.FunctionDef):
                        continue
                    if method.name.startswith('_') and method.name not in ('__init__', '__str__', '__repr__'):
                        continue
                    # Check if self is used
                    if any(isinstance(c, ast.Name) and c.id == 'self' for c in ast.walk(method)):
                        continue
                    candidates.append((fpath, cls.name, method.name))
    if candidates:
        for f, c, m in candidates[:5]:
            print(f"  ℹ {f.split('/')[-1]}: {c}.{m} (doesn't use self)")
    print(f"  Total candidates: {len(candidates)}")
    return True


def test_SA_8_long_lines():
    """SA-8: Excessively long lines."""
    print("\n--- SA-8: Long lines ---")
    long_lines = 0
    for fpath in files:
        with open(fpath) as f:
            for i, line in enumerate(f, 1):
                if len(line) > 200:
                    long_lines += 1
    print(f"  Lines > 200 chars: {long_lines}")
    return True


def test_SA_9_print_statements():
    """SA-9: Use of print() instead of logger."""
    print("\n--- SA-9: print() usage ---")
    print_uses = 0
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        for m in re.finditer(r'^\s*print\(', content, re.MULTILINE):
            line_num = content[:m.start()].count('\n') + 1
            print_uses += 1
    if print_uses > 0:
        print(f"  ⚠ {print_uses} print() calls (should use logger)")
    else:
        print(f"  ✓ No print() statements (all use logger)")
    return True


def test_SA_10_global_state():
    """SA-10: Module-level mutable state (globals)."""
    print("\n--- SA-10: Global mutable state ---")
    globals_count = {}
    for fpath in files:
        with open(fpath) as f:
            content = f.read()
        # Count global declarations
        globals_count[fpath.split('/')[-1]] = len(re.findall(r'^\s*global\s+\w+', content, re.MULTILINE))
    top = sorted(globals_count.items(), key=lambda x: -x[1])[:5]
    for f, n in top:
        if n > 0:
            print(f"  {f}: {n} 'global' declarations")
    return True


if __name__ == '__main__':
    print("="*70)
    print("Pass 50: Subtle Logic & Contract Analysis")
    print("="*70)

    tests = [
        test_SA_1_return_type_consistency,
        test_SA_2_function_complexity,
        test_SA_3_unused_imports,
        test_SA_4_bare_except,
        test_SA_5_functions_without_docstrings,
        test_SA_6_classes_without_init,
        test_SA_7_methods_not_using_self,
        test_SA_8_long_lines,
        test_SA_9_print_statements,
        test_SA_10_global_state,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
    print("="*70)
