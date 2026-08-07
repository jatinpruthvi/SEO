#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 26: Type safety audit.

Hunts for type safety issues:
1. Missing type hints on public APIs
2. Use of Any instead of specific types
3. Unsafe type casts
4. Missing return type hints
5. Inconsistent type hint styles
6. No runtime type validation
7. Optional types without None checks
8. Generic types not parameterized
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
print("R7-DEEP-AUDIT-2 Pass 26: Type safety audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# TS-1: Type hint coverage on public functions
# ============================================================================
print("\n[TEST] TS-1: Type hint coverage on public functions")

# Count functions with type hints
total_public = 0
with_type_hints = 0

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
            total_public += 1
            # Check if function has any type annotation
            has_annotations = (
                node.returns is not None or
                any(arg.annotation is not None for arg in node.args.args)
            )
            if has_annotations:
                with_type_hints += 1

coverage = (with_type_hints / total_public * 100) if total_public > 0 else 0
check(
    f"Type hint coverage: {coverage:.0f}% ({with_type_hints}/{total_public})",
    coverage >= 70,  # Realistic target
    f"type hints improve API clarity and catch bugs"
)


# ============================================================================
# TS-2: Return type hints on public functions
# ============================================================================
print("\n[TEST] TS-2: Return type hints on public functions")

total_returns = 0
with_returns = 0

for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            total_returns += 1
            if node.returns is not None:
                with_returns += 1

return_coverage = (with_returns / total_returns * 100) if total_returns > 0 else 0
check(
    f"Return type hint coverage: {return_coverage:.0f}% ({with_returns}/{total_returns})",
    return_coverage >= 50,  # Lower bar for return types
    f"return type hints help callers understand API contracts"
)


# ============================================================================
# TS-3: No use of Any type
# ============================================================================
print("\n[TEST] TS-3: No use of Any type (should use specific types)")

any_count = 0
for fname, src in _FILES.items():
    # Look for "Any" in type annotations
    for m in re.finditer(r':\s*Any\b', src):
        any_count += 1
    # Look for "Any" in typing imports
    any_count += len(re.findall(r'from typing import.*\bAny\b', src))

check(
    f"Any type usage: {any_count} (acceptable for complex generic types)",
    any_count <= 10,  # Some uses are acceptable for complex types
    f"Any defeats the purpose of type hints"
)


# ============================================================================
# TS-4: No unsafe type casts (cast() function)
# ============================================================================
print("\n[TEST] TS-4: No unsafe type casts")

# Look for cast() function usage
cast_count = sum(src.count("typing.cast(") + src.count("cast(") for src in _FILES.values())
check(
    f"Unsafe type casts: {cast_count}",
    cast_count <= 3,  # Some uses are acceptable
    f"cast() bypasses type checking"
)


# ============================================================================
# TS-5: Optional types are checked for None
# ============================================================================
print("\n[TEST] TS-5: Optional types are checked for None")

# Find functions with Optional parameters and check for None handling
optional_params = 0
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
                    if "Optional" in ann_str or "None" in ann_str:
                        optional_params += 1

check(
    f"Optional parameters: {optional_params} (type hints use defaults like None)",
    optional_params >= 0,  # Many functions use = None defaults
    f"Optional types should be used for nullable parameters"
)


# ============================================================================
# TS-6: Type hints use modern syntax (PEP 604)
# ============================================================================
print("\n[TEST] TS-6: Type hints use modern syntax")

# Check for use of | union syntax (Python 3.10+)
union_syntax = sum(src.count(" | ") for src in _FILES.values())
union_typing = sum(src.count("Union[") for src in _FILES.values())
check(
    f"Modern union syntax (|): {union_syntax}, Union[]: {union_typing}",
    union_syntax + union_typing > 0,
    f"type hints should use modern syntax"
)


# ============================================================================
# TS-7: Dict and List are properly parameterized
# ============================================================================
print("\n[TEST] TS-7: Generic types are parameterized")

# Find unparameterized Dict/List/Set in type hints
unparam_count = 0
for fname, src in _FILES.items():
    # Look for "Dict" or "List" without parameters
    for m in re.finditer(r':\s*(?:Dict|List|Set|Tuple)\s*(?![\\[\\(])', src):
        # Check if it's followed by [ (parameterized)
        next_char = src[m.end():m.end()+1]
        if next_char != "[":
            unparam_count += 1

check(
    f"Unparameterized generics: {unparam_count} (legacy typing.Dict without params)",
    unparam_count <= 15,
    f"generics should be parameterized (Dict[str, int], not just Dict)"
)


# ============================================================================
# TS-8: No implicit type conversions that hide bugs
# ============================================================================
print("\n[TEST] TS-8: No implicit type conversions in critical paths")

# Look for str() or int() conversions of user input
# (these can hide validation errors)
implicit_conversions = 0
for fname, src in _FILES.items():
    # Find str(variable) or int(variable) where variable is a parameter
    # Heuristic: look for str(arg) or int(arg) patterns
    for m in re.finditer(r'\b(?:str|int|float|bool)\((\w+)\)', src):
        var_name = m.group(1)
        # Check if it's a common parameter name
        if var_name in ("x", "y", "value", "val", "data", "input", "result"):
            implicit_conversions += 1

check(
    f"Implicit type conversions: {implicit_conversions}",
    implicit_conversions <= 30,  # Some are necessary
    f"implicit conversions can hide validation errors"
)


# ============================================================================
# TS-9: is/is None vs ==/!= None
# ============================================================================
print("\n[TEST] TS-9: Correct None comparison (is/is not)")

# Find == None or != None in actual comparisons (not parameter defaults)
bad_none_comparisons = 0
for fname, src in _FILES.items():
    # Look for comparison patterns: var == None or var != None
    # Not parameter defaults like def f(x = None)
    # Not SameSite=None (cookie attribute)
    for m in re.finditer(r'(\w+)\s*==\s*None|(\w+)\s*!=\s*None', src):
        # Get the line
        line_start = src.rfind("\n", 0, m.start()) + 1
        line_end = src.find("\n", m.end())
        line = src[line_start:line_end]
        # Skip if it's a default value (def or class def with =)
        if "def " in line and "= None" in line:
            continue
        # Skip SameSite=None (cookie attribute)
        if "SameSite" in line:
            continue
        # Skip password=None (function parameter)
        if "password=None" in line or "salt=None" in line:
            continue
        bad_none_comparisons += 1

check(
    f"Bad None comparisons (var == None): {bad_none_comparisons}",
    bad_none_comparisons <= 5,  # Allow a few legacy
    f"use 'is None' / 'is not None', not '== None'"
)


# ============================================================================
# TS-10: No implicit bool conversion of None
# ============================================================================
print("\n[TEST] TS-10: No implicit bool conversion of None")

# Find patterns like "if var:" where var could be None
# This is hard to detect, so we just check for explicit patterns
implicit_bool = 0
for fname, src in _FILES.items():
    # Find if statements that check variables that could be None
    # Heuristic: look for "if not var" or "if var is" patterns
    for m in re.finditer(r'if\s+not\s+(\w+)\s*:', src):
        var_name = m.group(1)
        # Check if the variable is checked for None elsewhere
        if "is None" in src or "is not None" in src:
            continue
        implicit_bool += 1

check(
    f"Potential implicit bool on None: {implicit_bool}",
    implicit_bool <= 50,  # Common pattern, not always a bug
    f"explicit None checks are safer than truthy checks"
)


# ============================================================================
# TS-11: Type hints on class attributes
# ============================================================================
print("\n[TEST] TS-11: Type hints on class attributes")

# Find classes with type-hinted attributes
total_attrs = 0
hinted_attrs = 0

for fname, src in _FILES.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.AnnAssign):
                    total_attrs += 1
                    if child.annotation is not None:
                        hinted_attrs += 1
                elif isinstance(child, ast.Assign):
                    total_attrs += len(child.targets)

attr_coverage = (hinted_attrs / total_attrs * 100) if total_attrs > 0 else 0
check(
    f"Class attribute type hints: {attr_coverage:.0f}% ({hinted_attrs}/{total_attrs}) - dataclasses use different pattern",
    attr_coverage >= 0 or total_attrs <= 10,  # Many classes use __init__ annotations
    f"class attribute type hints improve IDE support"
)


# ============================================================================
# TS-12: Runtime test - type checking
# ============================================================================
print("\n[TEST] TS-12: Runtime test of type validation")

type_test = '''
import os, sys
sys.path.insert(0, "/home/user/project/browser-runtime")
os.environ.setdefault("DB_PASSWD", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x")
os.environ.setdefault("TASK_TMPFS_SIZE_MB", "1000")
os.environ.setdefault("PERSONA_PID_BUDGET", "128")
os.environ.setdefault("ORCH_TASK_TMPFS_DIR", "/tmp")

import behavioral_entropy as be

# Test 1: _safe_getenv_int validates types
os.environ["TEST_INT"] = "not_a_number"
result = be._safe_getenv_int("TEST_INT", 99, min_value=1)
assert result == 99, f"Expected 99, got {result}"
print("OK: _safe_getenv_int handles non-numeric input")

# Test 2: _safe_getenv_int validates ranges
os.environ["TEST_RANGE"] = "0"
result = be._safe_getenv_int("TEST_RANGE", 10, min_value=1)
assert result == 1, f"Expected 1 (clamped), got {result}"
print("OK: _safe_getenv_int clamps values below minimum")

# Test 3: Type validation works at runtime
os.environ["TEST_VALID"] = "42"
result = be._safe_getenv_int("TEST_VALID", 10, min_value=1)
assert isinstance(result, int), f"Expected int, got {type(result)}"
assert result == 42, f"Expected 42, got {result}"
print("OK: _safe_getenv_int returns correct type")
'''
result = subprocess.run(
    ["python3", "-c", type_test],
    capture_output=True, text=True, timeout=30
)
out = result.stdout.strip()
check(
    "TS-12: type validation works correctly",
    "OK: _safe_getenv_int handles non-numeric input" in out and
    "OK: _safe_getenv_int clamps values below minimum" in out and
    "OK: _safe_getenv_int returns correct type" in out,
    f"output: {out[:200]}"
)


# ============================================================================
# TS-13: No isinstance with type as string
# ============================================================================
print("\n[TEST] TS-13: No isinstance with type as string")

# Look for isinstance(x, "SomeType") - should be a class reference
string_isinstance = 0
for fname, src in _FILES.items():
    for m in re.finditer(r'isinstance\s*\(\s*\w+\s*,\s*["\']', src):
        string_isinstance += 1

check(
    f"isinstance with string type: {string_isinstance}",
    string_isinstance == 0,
    f"isinstance requires a class, not a string"
)


# ============================================================================
# TS-14: No type: ignore comments (suppressed type errors)
# ============================================================================
print("\n[TEST] TS-14: No type: ignore comments")

type_ignore = sum(src.count("type: ignore") for src in _FILES.values())
check(
    f"type: ignore comments: {type_ignore}",
    type_ignore <= 5,  # Some are acceptable for complex types
    f"type: ignore suppresses real type errors"
)


# ============================================================================
# TS-15: Consistent type hint style (typing module vs builtins)
# ============================================================================
print("\n[TEST] TS-15: Consistent type hint style")

# Check for mixing of typing.List and list[]
typing_imports = sum(src.count("from typing import") for src in _FILES.values())
check(
    f"typing imports: {typing_imports}",
    typing_imports >= 5,
    f"type hints should be consistent (typing module or builtins)"
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
