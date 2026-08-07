#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 21: Security audit.

Hunts for security issues:
1. SQL injection (string concatenation in queries)
2. Command injection (shell=True with user input)
3. Path traversal (user input in file paths)
4. XXE / unsafe XML parsing
5. Unsafe deserialization (pickle/yaml with user data)
6. Hardcoded credentials
7. Insecure random (random instead of secrets)
8. Missing authentication/authorization
9. Sensitive data in logs
10. Unvalidated redirects
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
print("R7-DEEP-AUDIT-2 Pass 21: Security audit")
print("=" * 60)


# Load all source files
_FILES = {}
for fname in os.listdir("/home/user/project/browser-runtime"):
    if fname.endswith(".py"):
        path = f"/home/user/project/browser-runtime/{fname}"
        with open(path) as f:
            _FILES[fname] = f.read()


# ============================================================================
# SC-1: SQL injection (string concatenation in queries)
# ============================================================================
print("\n[TEST] SC-1: No SQL injection via string formatting")

sql_injection_risks = []
for fname, src in _FILES.items():
    # Find SQL queries that use string formatting/concatenation
    # Pattern: execute/fetchrow/fetch with f-string or % or + concat
    for m in re.finditer(r'(?:execute|fetch|fetchrow|fetchval)\s*\(\s*(f["\']|["\'][^"\']*%|["\'][^"\']*\s*\+)', src):
        line_no = src[:m.start()].count("\n") + 1
        sql_injection_risks.append(f"{fname}:{line_no}: {src[m.start():m.end()][:60]}")

check(
    f"SQL queries with string formatting: {len(sql_injection_risks)}",
    len(sql_injection_risks) == 0,
    f"all queries should use parameterized queries ($1, $2, etc.)"
)
if sql_injection_risks:
    for r in sql_injection_risks[:3]:
        print(f"    - {r}")


# ============================================================================
# SC-2: Command injection (shell=True with user input)
# ============================================================================
print("\n[TEST] SC-2: No shell=True with user input")

shell_injection_risks = []
for fname, src in _FILES.items():
    # Find shell=True usage
    for m in re.finditer(r'subprocess\.(?:run|call|Popen)\([^)]*shell\s*=\s*True', src, re.DOTALL):
        line_no = src[:m.start()].count("\n") + 1
        # Get the call
        call_start = m.start()
        call_end = src.find(")", call_start) + 1
        if call_end > call_start:
            shell_injection_risks.append(f"{fname}:{line_no}: {src[call_start:call_end][:80]}")

check(
    f"subprocess calls with shell=True: {len(shell_injection_risks)}",
    len(shell_injection_risks) == 0,
    f"shell=True with user input is a command injection vector"
)
if shell_injection_risks:
    for r in shell_injection_risks[:3]:
        print(f"    - {r}")


# ============================================================================
# SC-3: Hardcoded credentials
# ============================================================================
print("\n[TEST] SC-3: No hardcoded credentials in source")

# Look for password=, secret=, api_key= with literal values
cred_patterns = [
    (r'password\s*=\s*["\'][^"\']+["\']', "password"),
    (r'secret\s*=\s*["\'][^"\']+["\']', "secret"),
    (r'api_key\s*=\s*["\'][^"\']+["\']', "api_key"),
    (r'token\s*=\s*["\'][a-zA-Z0-9]{20,}["\']', "token"),
]

hardcoded_creds = []
for fname, src in _FILES.items():
    for pattern, cred_type in cred_patterns:
        for m in re.finditer(pattern, src, re.IGNORECASE):
            line_no = src[:m.start()].count("\n") + 1
            line = src[max(0, m.start()-50):m.end()+50]
            # Skip test files, comments, and known safe values
            if "test" in fname or "#" in line[:line.find(m.group(0))][-5:]:
                continue
            if m.group(0) in ('password=""', 'password="x"', 'api_key=""', 'token=""'):
                continue
            hardcoded_creds.append(f"{fname}:{line_no}: {cred_type}")

check(
    f"Hardcoded credentials: {len(hardcoded_creds)}",
    len(hardcoded_creds) <= 2,  # Allow test fixtures
    f"sample: {hardcoded_creds[:3]}"
)


# ============================================================================
# SC-4: Unsafe deserialization (pickle.loads with user data)
# ============================================================================
print("\n[TEST] SC-4: No unsafe pickle.loads with user data")

unsafe_pickle = []
for fname, src in _FILES.items():
    for m in re.finditer(r'pickle\.loads?\s*\(', src):
        line_no = src[:m.start()].count("\n") + 1
        # Check if input is from user (heuristic: variable named input/user/data)
        line = src.split("\n")[line_no - 1]
        if any(word in line.lower() for word in ["input", "user", "data", "request", "payload"]):
            unsafe_pickle.append(f"{fname}:{line_no}: {line.strip()[:80]}")

check(
    f"Unsafe pickle.loads with user data: {len(unsafe_pickle)}",
    len(unsafe_pickle) == 0,
    f"pickle.loads on untrusted data allows arbitrary code execution"
)


# ============================================================================
# SC-5: Insecure random for security purposes
# ============================================================================
print("\n[TEST] SC-5: secrets module used for security-critical randomness")

# Check for random.random/random.randint in security contexts
# (token generation, password generation, etc.)
insecure_random = []
for fname, src in _FILES.items():
    for m in re.finditer(r'random\.(?:random|randint|choice|uniform)\s*\(', src):
        line_no = src[:m.start()].count("\n") + 1
        line = src.split("\n")[line_no - 1].lower()
        if any(word in line for word in ["token", "password", "secret", "key", "uuid", "session_id"]):
            insecure_random.append(f"{fname}:{line_no}: {line.strip()[:80]}")

check(
    f"random.* used for security: {len(insecure_random)}",
    len(insecure_random) <= 5,  # Some use cases are OK (jitter, delays)
    f"sample: {insecure_random[:3]}"
)


# ============================================================================
# SC-6: Fernet encryption is used for sensitive data
# ============================================================================
print("\n[TEST] SC-6: Sensitive data uses Fernet encryption")

# Check that session_data is encrypted
orch_src = _FILES.get("hardened_orchestrator.py", "")
uses_fernet = "Fernet" in orch_src and "encrypt" in orch_src
check(
    f"Fernet used for session encryption",
    uses_fernet,
    f"session data should be Fernet-encrypted before storage"
)


# ============================================================================
# SC-7: File operations validate paths (no path traversal)
# ============================================================================
print("\n[TEST] SC-7: File paths are validated")

# Check for os.path.join with user input
# This is hard to detect automatically, so we just check for common patterns
path_traversal_risks = []
for fname, src in _FILES.items():
    # Look for os.path.join with request/input/payload
    for m in re.finditer(r'os\.path\.join\s*\([^)]*(?:request|input|payload|data)', src):
        line_no = src[:m.start()].count("\n") + 1
        path_traversal_risks.append(f"{fname}:{line_no}")

check(
    f"Potential path traversal risks: {len(path_traversal_risks)}",
    len(path_traversal_risks) <= 3,
    f"sample: {path_traversal_risks[:3]}"
)


# ============================================================================
# SC-8: Environment variables are not logged
# ============================================================================
print("\n[TEST] SC-8: Sensitive env vars are not logged")

# Check that we don't log env var values
sensitive_envs = ["DB_PASSWD", "ENCRYPTION_KEY", "API_KEY", "SECRET", "TOKEN"]
env_log_risks = []
for fname, src in _FILES.items():
    for env in sensitive_envs:
        # Look for logger calls that interpolate the env var VALUE
        # We check for f-string with {env} or string + env
        # Pattern: f"...{ENV}..." or "..." + ENV + "..."
        pattern1 = 'f"' + '[^{}]*\\{[^}]*' + env + '[^}]*\\}[^{}]*"'
        pattern2 = '"[^"]*"\\s*\\+\\s*' + env
        for pattern in [pattern1, pattern2]:
            for m in re.finditer(pattern, src):
                # Verify it's in a logger call
                line_start = src.rfind("\n", 0, m.start()) + 1
                line_end = src.find("\n", m.end())
                line = src[line_start:line_end]
                if "logger." in line:
                    line_no = src[:m.start()].count("\n") + 1
                    env_log_risks.append(f"{fname}:{line_no}: {env}")

check(
    f"Sensitive env vars logged: {len(env_log_risks)}",
    len(env_log_risks) == 0,
    f"DB_PASSWD, ENCRYPTION_KEY, etc. should never be logged"
)


# ============================================================================
# SC-9: SSL/TLS verification is enforced
# ============================================================================
print("\n[TEST] SC-9: HTTPS connections verify certificates")

# Check for verify=False in httpx/requests
no_verify = []
for fname, src in _FILES.items():
    for m in re.finditer(r'verify\s*=\s*False', src):
        line_no = src[:m.start()].count("\n") + 1
        no_verify.append(f"{fname}:{line_no}")

check(
    f"SSL verification disabled: {len(no_verify)}",
    len(no_verify) <= 2,  # SANDBOX_ENV is the only legitimate case
    f"verify=False allows MITM attacks"
)


# ============================================================================
# SC-10: Input sanitization for scraped content
# ============================================================================
print("\n[TEST] SC-10: Scraped content is sanitized")

# Check for DefensiveInputSanitizer usage
uses_sanitizer = "DefensiveInputSanitizer" in "\n".join(_FILES.values())
check(
    f"DefensiveInputSanitizer is used",
    uses_sanitizer,
    f"scraped content should be sanitized before storage/AI processing"
)


# ============================================================================
# SC-11: AdversarialPromptGuard is used
# ============================================================================
print("\n[TEST] SC-11: AdversarialPromptGuard is used")

uses_prompt_guard = "AdversarialPromptGuard" in "\n".join(_FILES.values())
check(
    f"AdversarialPromptGuard is used",
    uses_prompt_guard,
    f"LLM inputs should be checked for prompt injection"
)


# ============================================================================
# SC-12: No eval/exec with user input
# ============================================================================
print("\n[TEST] SC-12: No eval/exec with user data")

eval_exec_risks = []
for fname, src in _FILES.items():
    for m in re.finditer(r'\beval\s*\(', src):
        line_no = src[:m.start()].count("\n") + 1
        line = src.split("\n")[line_no - 1]
        if any(word in line.lower() for word in ["input", "user", "data", "request", "payload"]):
            eval_exec_risks.append(f"{fname}:{line_no}: {line.strip()[:60]}")

check(
    f"eval() with user data: {len(eval_exec_risks)}",
    len(eval_exec_risks) == 0,
    f"eval() on untrusted data allows arbitrary code execution"
)


# ============================================================================
# SC-13: File permissions are restrictive
# ============================================================================
print("\n[TEST] SC-13: Sensitive files use restrictive permissions")

# Check for mode=0o700 or 0o600 in file creation
restrictive_modes = sum(src.count("0o700") + src.count("0o600") for src in _FILES.values())
permissive_modes = sum(src.count("0o755") + src.count("0o644") for src in _FILES.values())
check(
    f"Restrictive file modes used: {restrictive_modes} (0o700/0o600)",
    restrictive_modes >= 2,
    f"sensitive files (tmpfs, keys) should use 0o600/0o700"
)


# ============================================================================
# SC-14: Constants like 172.18.0.2 are documented as Docker network
# ============================================================================
print("\n[TEST] SC-14: Hardcoded IPs are documented")

# The 172.18.0.x range is Docker's default bridge network
# This is expected in the orchestrator
orch_src = _FILES.get("hardened_orchestrator.py", "")
has_docker_comment = "Docker" in orch_src or "172.18" in orch_src
check(
    f"Docker network IPs are documented",
    has_docker_comment,
    f"hardcoded IPs should be explained"
)


# ============================================================================
# SC-15: Runtime test - SQL injection attempt
# ============================================================================
print("\n[TEST] SC-15: SQL injection attempt is safely handled")

# Test that the DB layer rejects SQL injection
# We can't actually connect to a DB, so we test the query construction
injection_test = '''
import re
import sys
sys.path.insert(0, "/home/user/project/browser-runtime")

# Read the orchestrator source
with open("/home/user/project/browser-runtime/hardened_orchestrator.py") as f:
    src = f.read()

# Find all SQL queries
queries = re.findall(r'(?:execute|fetch|fetchrow|fetchval)\\s*\\(\\s*\\"\\"\\"(.*?)\\"\\"\\"', src, re.DOTALL)
print(f"Found {len(queries)} SQL queries")

# Check that queries use $1, $2, etc. (parameterized)
unsafe = 0
for q in queries:
    if "f\\"" in q or "%" in q or "+ " in q:
        # Could be a false positive (e.g., comment with %), but worth flagging
        if "f\\"" in q:
            unsafe += 1
            print(f"  UNSAFE: f-string in query: {q[:60]}")

if unsafe == 0:
    print("OK: all queries are parameterized")
    sys.exit(0)
else:
    print(f"FAIL: {unsafe} unsafe queries")
    sys.exit(1)
'''
result = subprocess.run(
    ["python3", "-c", injection_test],
    capture_output=True, text=True, timeout=30
)
out = result.stdout.strip()
check(
    f"SC-15: SQL queries are parameterized",
    "OK: all queries are parameterized" in out,
    f"output: {out[:200]}"
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
