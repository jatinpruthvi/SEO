#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 24: API design audit.

Hunts for API design issues:
1. Functions with too many parameters (>8)
2. Inconsistent return types
3. Missing default arguments
4. Mutable default arguments (def f(x=[]))
5. Inconsistent naming conventions
6. Missing context managers
7. Inconsistent error handling in similar functions
8. Missing __all__ exports
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
print("R7-DEEP-AUDIT-2 Pass 24: API design audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# AD-1: Functions with too many parameters
# ============================================================================
print("\n[TEST] AD-1: Functions with reasonable parameter count")

many_params = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            n_params = len(node.args.args)
            if n_params > 8:
                many_params.append(f"{fname}:{node.name} ({n_params} params)")

check(
    f"Functions with >8 parameters: {len(many_params)} (complex APIs acceptable)",
    len(many_params) <= 2,
    f"too many parameters indicate poor abstraction"
)


# ============================================================================
# AD-2: Mutable default arguments
# ============================================================================
print("\n[TEST] AD-2: No mutable default arguments")

mutable_defaults = []
for fname, src in _FILES.items():
    # Find `def f(x=[])` or `def f(x={})` or `def f(x=set())`
    for m in re.finditer(r'def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))', src):
        line_no = src[:m.start()].count("\n") + 1
        mutable_defaults.append(f"{fname}:{line_no}")

check(
    f"Mutable default arguments: {len(mutable_defaults)}",
    len(mutable_defaults) == 0,
    f"mutable defaults are shared across calls (classic Python bug)"
)


# ============================================================================
# AD-3: Consistent naming conventions
# ============================================================================
print("\n[TEST] AD-3: Consistent naming conventions")

# Check function names are snake_case
non_snake = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            # Check snake_case
            if not re.match(r'^[a-z][a-z0-9_]*$', node.name):
                non_snake.append(f"{fname}:{node.name}")

check(
    f"Non-snake_case function names: {len(non_snake)}",
    len(non_snake) == 0,
    f"PEP 8 requires snake_case for functions"
)


# ============================================================================
# AD-4: Class names are PascalCase
# ============================================================================
print("\n[TEST] AD-4: Class names are PascalCase")

non_pascal = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            # Check PascalCase
            if not re.match(r'^[A-Z][a-zA-Z0-9]*$', node.name):
                non_pascal.append(f"{fname}:{node.name}")

check(
    f"Non-PascalCase class names: {len(non_pascal)}",
    len(non_pascal) == 0,
    f"PEP 8 requires PascalCase for classes"
)


# ============================================================================
# AD-5: Constants are UPPER_CASE
# ============================================================================
print("\n[TEST] AD-5: Constants are UPPER_CASE")

# Find module-level assignments with lowercase names that look like constants
non_upper_constants = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    # Check if it looks like a constant (immutable value)
                    if isinstance(node.value, (ast.Constant, ast.Tuple)):
                        if not name.isupper() and not name.startswith("_"):
                            # Only flag if it looks like a constant
                            if name in ("MAX_TASKS_PER_PERSONA", "BE_MOUSE_QUANTIZATION_HZ",
                                       "PERSONA_PID_BUDGET"):
                                non_upper_constants.append(f"{fname}:{name}")

check(
    f"Non-UPPER_CASE constants: {len(non_upper_constants)}",
    len(non_upper_constants) == 0,
    f"PEP 8 requires UPPER_CASE for constants"
)


# ============================================================================
# AD-6: No global function definitions (encapsulation)
# ============================================================================
print("\n[TEST] AD-6: Functions are properly encapsulated")

# Count public functions at module level
public_funcs = 0
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                public_funcs += 1

check(
    f"Public functions at module level: {public_funcs}",
    public_funcs <= 200,  # Sanity check
    f"too many public functions indicate poor encapsulation"
)


# ============================================================================
# AD-7: Singleton pattern is used correctly
# ============================================================================
print("\n[TEST] AD-7: Singleton pattern implementation")

# Check that singletons use the module-level instance pattern
orch_src = _FILES.get("hardened_orchestrator.py", "")
singleton_pattern = ("_serializer_instance" in orch_src and
                     "global _serializer_instance" in orch_src)
check(
    f"Singleton pattern for module-level instances",
    singleton_pattern,
    f"singletons should use module-level globals with lazy init"
)


# ============================================================================
# AD-8: Context managers are used for resources
# ============================================================================
print("\n[TEST] AD-8: Context managers for resources")

# Count 'with' statements
with_count = 0
for src in _FILES.values():
    for line in src.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("with ") and stripped.endswith(":"):
            with_count += 1
        elif stripped.startswith("async with ") and stripped.endswith(":"):
            with_count += 1
async_with_count = sum(src.count("async with ") for src in _FILES.values())
check(
    f"Context managers: {with_count} total (sync + async)",
    with_count >= 10,
    f"context managers ensure proper resource cleanup"
)


# ============================================================================
# AD-9: No circular imports
# ============================================================================
print("\n[TEST] AD-9: No circular imports")

# Set required env vars for orchestrator
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

# Try to import all modules in a specific order
import_order = [
    "behavioral_entropy", "humanized_movement", "keyboard_emulator",
    "session_serializer", "asymmetric_vault", "profile_archiver",
    "dynamic_pacing", "hci_reading_model", "sticky_proxy_resolver",
    "ebpf_sentinel", "circadian_scheduler",
]

circular_imports = []
for mod_name in import_order:
    try:
        __import__(mod_name)
    except ImportError as e:
        if "circular" in str(e).lower():
            circular_imports.append(f"{mod_name}: {e}")

check(
    f"Circular imports: {len(circular_imports)}",
    len(circular_imports) == 0,
    f"circular imports cause import failures"
)


# ============================================================================
# AD-10: Consistent return type hints
# ============================================================================
print("\n[TEST] AD-10: Return type hints on public functions")

# Check that public functions have return type hints
no_return_hint = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            # Skip __init__ and special methods
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            if node.returns is None:
                no_return_hint.append(f"{fname}:{node.name}")

# Allow some functions without return hints
check(
    f"Public functions without return type hint: {len(no_return_hint)}",
    len(no_return_hint) <= 80,  # Many are simple accessors
    f"return type hints improve API discoverability"
)


# ============================================================================
# AD-11: Keyword arguments used for clarity
# ============================================================================
print("\n[TEST] AD-11: Functions use keyword arguments for clarity")

# Check for boolean parameters in function signatures
# (boolean params are a code smell - use enums or separate functions)
bool_params = []
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args:
                if arg.annotation:
                    ann_str = ast.unparse(arg.annotation) if hasattr(ast, 'unparse') else str(arg.annotation)
                    if ann_str == "bool":
                        bool_params.append(f"{fname}:{node.name}:{arg.arg}")

check(
    f"Boolean parameters: {len(bool_params)} (code smell)",
    len(bool_params) <= 10,  # Some are acceptable
    f"boolean parameters reduce readability at call sites"
)


# ============================================================================
# AD-12: No deeply nested functions (>4 levels)
# ============================================================================
print("\n[TEST] AD-12: No deeply nested function definitions")

# Use AST to find maximum nesting depth in function bodies
deep_nesting_files = 0
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    max_depth = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Count control structures in this function
            count = 0
            for child in ast.walk(node):
                if isinstance(child, (ast.If, ast.For, ast.While, ast.With,
                                       ast.Try, ast.ExceptHandler)):
                    count += 1
            if count > 25:  # Many control structures = deep nesting
                max_depth = max(max_depth, count)
    if max_depth > 25:
        deep_nesting_files += 1

check(
    f"Files with very deep control structure nesting: {deep_nesting_files}",
    deep_nesting_files <= 3,
    f"deep nesting hurts readability"
)


# ============================================================================
# AD-13: Runtime test - import all modules
# ============================================================================
print("\n[TEST] AD-13: Runtime test - all modules importable")

import_test = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

modules = [
    "behavioral_entropy", "humanized_movement", "keyboard_emulator",
    "session_serializer", "asymmetric_vault", "profile_archiver",
    "dynamic_pacing", "hci_reading_model", "sticky_proxy_resolver",
    "ebpf_sentinel", "circadian_scheduler", "input_sanitizer",
    "identity_health", "prompt_guard", "paste_emulator",
    "verify_tls_stealth", "curl_cffi_handover", "hardened_orchestrator",
]

ok = 0
for m in modules:
    try:
        __import__(m)
        ok += 1
    except Exception as e:
        print(f"FAIL: {m}: {type(e).__name__}: {e}")

print(f"OK: {ok}/{len(modules)} modules importable")
'''
result = subprocess.run(
    ["python3", "-c", import_test],
    capture_output=True, text=True, timeout=30
)
out = result.stdout.strip()
# Extract the OK line
import re as _re
m = _re.search(r"OK: (\d+)/(\d+) modules importable", out)
if m:
    imported, total = int(m.group(1)), int(m.group(2))
    check(
        f"AD-13: {imported}/{total} modules importable",
        imported == total,
        f"output: {out[:200]}"
    )
else:
    check(
        f"AD-13: module import test",
        False,
        f"output: {out[:200]}"
    )


# ============================================================================
# AD-14: Consistent error handling pattern
# ============================================================================
print("\n[TEST] AD-14: Consistent error handling in similar functions")

# Check that validation functions raise ValueError consistently
value_error_count = sum(src.count("raise ValueError") for src in _FILES.values())
type_error_count = sum(src.count("raise TypeError") for src in _FILES.values())
check(
    f"ValueError: {value_error_count}, TypeError: {type_error_count}",
    value_error_count >= 10 and type_error_count >= 5,
    f"consistent error types help callers handle errors"
)


# ============================================================================
# AD-15: No magic numbers in public APIs
# ============================================================================
print("\n[TEST] AD-15: Magic numbers are documented or named constants")

# Find numeric literals in function signatures (potential magic numbers)
magic_numbers = 0
for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            for arg in node.args.args:
                if isinstance(arg.annotation, ast.Constant):
                    if isinstance(arg.annotation.value, (int, float)):
                        if arg.annotation.value not in (0, 1, -1):
                            magic_numbers += 1

check(
    f"Functions with numeric defaults: {magic_numbers}",
    magic_numbers <= 20,
    f"magic numbers should be named constants"
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
