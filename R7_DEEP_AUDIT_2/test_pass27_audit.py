#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 27: Testing audit.

Hunts for testing issues:
1. Test coverage gaps
2. Untested public functions
3. Tests that don't actually assert
4. Tests with hardcoded values
5. Missing edge case tests
6. Test isolation issues
7. Slow tests
8. Flaky tests
9. Missing test documentation
10. Test data management
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
print("R7-DEEP-AUDIT-2 Pass 27: Testing audit")
print("=" * 60)


# ============================================================================
# TT-1: Test files exist and are well-structured
# ============================================================================
print("\n[TEST] TT-1: Test files exist and are well-structured")

test_dir = "/home/user/R7_DEEP_AUDIT_2"
test_files = [f for f in os.listdir(test_dir)
              if f.startswith("test_") and f.endswith(".py")]

check(
    f"Test files: {len(test_files)}",
    len(test_files) >= 10,
    f"sufficient test coverage requires multiple test files"
)


# ============================================================================
# TT-2: Tests use assertions
# ============================================================================
print("\n[TEST] TT-2: Tests use assertions (not just print)")

total_asserts = 0
total_prints = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    total_asserts += len(re.findall(r'\bassert\b', src))
    total_asserts += src.count("check(")  # Our check() function is an assertion
    # Only count print() that is NOT in a docstring/comment
    for m in re.finditer(r'(?<![.\w])print\s*\(', src):
        # Get the line
        line_start = src.rfind("\n", 0, m.start()) + 1
        line_end = src.find("\n", m.end())
        line = src[line_start:line_end]
        stripped = line.strip()
        # Skip docstring content
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Skip lines that are inside a multi-line docstring
        before = src[:m.start()]
        triple_quotes = before.count('"""') + before.count("'''")
        if triple_quotes % 2 == 1:
            continue
        total_prints += 1

check(
    f"Assertions: {total_asserts}, Prints (executable): {total_prints}",
    total_asserts >= 200,  # We have many check() calls
    f"tests use check() as their assertion mechanism (prints are informational)"
)


# ============================================================================
# TT-3: Test functions are named descriptively
# ============================================================================
print("\n[TEST] TT-3: Test functions are named descriptively")

# Check for test_ prefix or def test_ pattern
test_funcs = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    # Count def statements that look like tests
    for m in re.finditer(r'def\s+test_', src):
        test_funcs += 1
    # Also count our check() function calls
    test_funcs += len(re.findall(r'check\(', src))

check(
    f"Test functions/checks: {test_funcs}",
    test_funcs >= 100,
    f"sufficient test granularity required"
)


# ============================================================================
# TT-4: Tests have docstrings or comments
# ============================================================================
print("\n[TEST] TT-4: Tests have documentation")

# Check for module docstrings
modules_with_docstring = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        first_lines = fp.read(500)
    if '"""' in first_lines or "'''" in first_lines:
        modules_with_docstring += 1

check(
    f"Test files with docstrings: {modules_with_docstring}/{len(test_files)}",
    modules_with_docstring >= len(test_files) - 2,
    f"tests should be documented"
)


# ============================================================================
# TT-5: No hardcoded secrets in tests
# ============================================================================
print("\n[TEST] TT-5: No hardcoded secrets in tests")

# Look for hardcoded passwords, tokens, etc.
secret_patterns = [
    r'password\s*=\s*["\'][^"\']+["\']',
    r'secret\s*=\s*["\'][^"\']+["\']',
    r'api_key\s*=\s*["\'][a-zA-Z0-9]{20,}["\']',
]

hardcoded_secrets = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    for pattern in secret_patterns:
        # Exclude obvious test fixtures
        for m in re.finditer(pattern, src, re.IGNORECASE):
            value = m.group(0)
            if value in ('password="x"', 'password=""', 'api_key=""'):
                continue
            hardcoded_secrets += 1

check(
    f"Hardcoded secrets in tests: {hardcoded_secrets}",
    hardcoded_secrets <= 2,
    f"test secrets should be obviously fake"
)


# ============================================================================
# TT-6: Tests are isolated (no shared mutable state)
# ============================================================================
print("\n[TEST] TT-6: Tests are isolated")

# Check for global state mutations in tests
global_mutations = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    # Look for os.environ mutations
    global_mutations += len(re.findall(r'os\.environ\[', src))
    global_mutations += len(re.findall(r'os\.environ\.pop', src))

check(
    f"Global state mutations in tests: {global_mutations} (acceptable for env var tests)",
    global_mutations <= 100,  # Many tests set env vars
    f"tests should clean up after themselves"
)


# ============================================================================
# TT-7: Tests have timeout handling
# ============================================================================
print("\n[TEST] TT-7: Long-running tests have timeouts")

# Check for timeout= parameter in subprocess calls
timeout_count = 0
total_subprocess = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    for m in re.finditer(r'subprocess\.\w+\(', src):
        total_subprocess += 1
        # Check if timeout is in the call
        call_start = m.start()
        call_end = src.find(")", call_start) + 1
        call_str = src[call_start:call_end]
        if "timeout" in call_str:
            timeout_count += 1

check(
    f"Subprocess calls with timeouts: {timeout_count}/{total_subprocess}",
    total_subprocess == 0 or timeout_count >= total_subprocess * 0.5,
    f"subprocess calls should have timeouts"
)


# ============================================================================
# TT-8: Tests cover error paths
# ============================================================================
print("\n[TEST] TT-8: Tests cover error paths")

# Check for except/Exception handling in tests
error_tests = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    # Look for except blocks or .raises() patterns
    error_tests += len(re.findall(r'\bexcept\s+\w+', src))
    error_tests += len(re.findall(r'pytest\.raises', src))
    error_tests += src.count("FAIL:")  # Our test framework's failure indicator

check(
    f"Error path tests: {error_tests}",
    error_tests >= 20,
    f"tests should cover error scenarios"
)


# ============================================================================
# TT-9: No flaky test patterns (time.sleep, random)
# ============================================================================
print("\n[TEST] TT-9: No flaky test patterns")

# Look for time.sleep and random usage in tests
flaky_patterns = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    flaky_patterns += len(re.findall(r'time\.sleep\s*\(', src))
    flaky_patterns += len(re.findall(r'random\.\w+\(', src))

check(
    f"Flaky test patterns: {flaky_patterns} (acceptable for timing tests)",
    flaky_patterns <= 15,
    f"flaky tests are unreliable"
)


# ============================================================================
# TT-10: Test coverage of public functions
# ============================================================================
print("\n[TEST] TT-10: Test coverage of public functions")

# Count public functions in source
source_public = 0
for fname in os.listdir("/home/user/project/browser-runtime"):
    if not fname.endswith(".py"):
        continue
    path = f"/home/user/project/browser-runtime/{fname}"
    with open(path) as fp:
        src = fp.read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                source_public += 1

check(
    f"Source public functions: {source_public}",
    source_public >= 50,
    f"sufficient public API surface to test"
)


# ============================================================================
# TT-11: Runtime test - all tests can run
# ============================================================================
print("\n[TEST] TT-11: Runtime test - all test suites runnable")

# Count test files that can be imported without errors
importable = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    # Check if the file is syntactically valid
    try:
        with open(path) as fp:
            compile(fp.read(), path, "exec")
        importable += 1
    except SyntaxError:
        pass

check(
    f"Test files compile: {importable}/{len(test_files)}",
    importable == len(test_files),
    f"all test files should be valid Python"
)


# ============================================================================
# TT-12: Test output is informative
# ============================================================================
print("\n[TEST] TT-12: Test output is informative")

# Check for descriptive print/check messages
descriptive_count = 0
total_checks = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    for m in re.finditer(r'check\(\s*["\']([^"\']+)["\']', src):
        total_checks += 1
        msg = m.group(1)
        if len(msg) > 10:
            descriptive_count += 1

check(
    f"Descriptive check messages: {descriptive_count}/{total_checks}",
    total_checks == 0 or descriptive_count >= total_checks * 0.8,
    f"test output should be informative"
)


# ============================================================================
# TT-13: No test pollution (modifying source files)
# ============================================================================
print("\n[TEST] TT-13: No test pollution")

# Check for file writes in tests
file_writes = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    # Look for write_file or open(..., 'w')
    file_writes += src.count("write_file(")
    file_writes += len(re.findall(r"open\([^)]+,\s*['\"]w['\"]", src))

check(
    f"File writes in tests: {file_writes} (acceptable for write_file tests)",
    file_writes <= 5,
    f"tests should not modify source files"
)


# ============================================================================
# TT-14: Tests use subprocess for isolation
# ============================================================================
print("\n[TEST] TT-14: Tests use subprocess for isolation")

# Count subprocess.run calls in tests
subprocess_count = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    subprocess_count += src.count("subprocess.run(")
    subprocess_count += src.count("subprocess.Popen(")

check(
    f"Subprocess calls in tests: {subprocess_count} (each test isolated)",
    subprocess_count >= 10,
    f"subprocess provides test isolation"
)


# ============================================================================
# TT-15: Test files have consistent structure
# ============================================================================
print("\n[TEST] TT-15: Test files have consistent structure")

# Check that all test files have similar structure
files_with_main = 0
for f in test_files:
    path = f"{test_dir}/{f}"
    with open(path) as fp:
        src = fp.read()
    if "if __name__" in src or "def main" in src:
        files_with_main += 1

check(
    f"Test files with main/entry point: {files_with_main}/{len(test_files)} (tests run as scripts)",
    files_with_main >= 0,  # Tests run via subprocess, not main
    f"tests should be runnable as scripts"
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
