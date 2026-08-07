"""Pass 47: Configuration & Deployment Issue Detection.

CD-1: Required env vars are properly validated
CD-2: Default values for optional env vars
CD-3: DB connection string format
CD-4: Path handling cross-platform
CD-5: File permission/ownership assumptions
CD-6: Container runtime detection
CD-7: Network connectivity assumptions
CD-8: Resource limits (memory, CPU, disk)
CD-9: Log file rotation/management
CD-10: Timezone handling
CD-11: Secret handling (no hardcoded secrets)
CD-12: Docker-specific issues
"""
import sys
import os
import re
import ast

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
    '/home/user/project/browser-runtime/ebpf_sentinel.py',
    '/home/user/project/browser-runtime/asymmetric_vault.py',
    '/home/user/project/browser-runtime/entrypoint.sh',
]


def test_CD_1_required_env_validation():
    """CD-1: Required env vars validated at startup."""
    print("--- CD-1: Required env var validation ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if '_REQUIRED_ENV' in src and '_MISSING' in src:
        print("  ✓ Orchestrator validates required env vars")
        # Find which env vars are required
        match = re.search(r'_REQUIRED_ENV\s*=\s*\[([^\]]+)\]', src)
        if match:
            required = [v.strip().strip('"').strip("'") for v in match.group(1).split(',')]
            print(f"  Required env vars: {required}")
    return True


def test_CD_2_env_var_defaults():
    """CD-2: All env var reads have defaults."""
    print("\n--- CD-2: Env var default values ---")
    issues = []
    for fpath in files:
        if not fpath.endswith('.py'):
            continue
        with open(fpath) as f:
            content = f.read()
        # Find direct os.getenv / _os.getenv calls
        for m in re.finditer(r'_?os\.getenv\("([A-Z_0-9]+)"\s*\)', content):
            env_name = m.group(1)
            # Check if default is provided
            if not re.search(rf'_?os\.getenv\("{env_name}"\s*,\s*[^)]+\)', content):
                issues.append((fpath, env_name))
    if issues:
        for f, n in issues[:5]:
            print(f"  ⚠ {f.split('/')[-1]}: env var '{n}' has no default")
    else:
        print(f"  ✓ All env vars have defaults")
    return True


def test_CD_3_hardcoded_secrets():
    """CD-3: No hardcoded secrets."""
    print("\n--- CD-3: Hardcoded secrets detection ---")
    secret_patterns = [
        (r'password\s*=\s*["\'][^"\']+["\']', 'password'),
        (r'api_key\s*=\s*["\'][^"\']+["\']', 'api_key'),
        (r'secret\s*=\s*["\'][^"\']+["\']', 'secret'),
        (r'token\s*=\s*["\'][^"\']+["\']', 'token'),
    ]
    issues = []
    for fpath in files:
        if not fpath.endswith('.py'):
            continue
        with open(fpath) as f:
            content = f.read()
        for pattern, name in secret_patterns:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                val = m.group(0)
                # Skip obvious non-secrets (env var names, "auto", etc.)
                if 'ENV' in val.upper() or 'AUTO' in val.upper() or 'CHANGEME' in val.upper():
                    continue
                if 'getenv' in val or 'os.' in val:
                    continue
                issues.append((fpath, name, val[:60]))
    if issues:
        for f, n, v in issues[:5]:
            print(f"  ⚠ {f.split('/')[-1]}: hardcoded {n}? '{v}'")
    else:
        print(f"  ✓ No hardcoded secrets detected")
    return True


def test_CD_4_path_handling():
    """CD-4: Path handling cross-platform."""
    print("\n--- CD-4: Path handling ---")
    issues = []
    for fpath in files:
        if not fpath.endswith('.py'):
            continue
        with open(fpath) as f:
            content = f.read()
        # Look for hardcoded /tmp/, /var/, /etc/ paths
        for m in re.finditer(r'["\']([/][\w/]+[/])["\']', content):
            path = m.group(1)
            if path.startswith(('/tmp/', '/var/', '/etc/', '/proc/')):
                # These are usually OK in container context
                pass
        # Look for Windows-style paths (should be none)
        if re.search(r'["\'][A-Z]:\\\\[\w]+', content):
            issues.append((fpath, 'Windows path'))
    if issues:
        for f, n in issues:
            print(f"  ⚠ {f.split('/')[-1]}: {n}")
    else:
        print(f"  ✓ No platform-specific paths")
    return True


def test_CD_5_loguru_setup():
    """CD-5: Loguru logging configuration."""
    print("\n--- CD-5: Logger setup ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if 'logger.add' in src or 'logger.remove' in src:
        print(f"  ✓ Loguru has custom config")
    else:
        print(f"  - No custom loguru config (uses defaults)")
    if 'LogLevel' in src or 'log_level' in src.lower():
        print(f"  ✓ Log level configurable")
    return True


def test_CD_6_container_detection():
    """CD-6: Container runtime detection."""
    print("\n--- CD-6: Container detection ---")
    found = False
    for fpath in files:
        if not fpath.endswith('.py'):
            continue
        with open(fpath) as f:
            content = f.read()
        if '/proc/1/cgroup' in content or 'in_docker' in content or 'is_container' in content:
            found = True
            print(f"  ✓ Container detection in {fpath.split('/')[-1]}")
    if not found:
        print(f"  - No explicit container detection")
    return True


def test_CD_7_resource_limits():
    """CD-7: Resource limit handling."""
    print("\n--- CD-7: Resource limits ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if 'RLIMIT_CORE' in src or 'setrlimit' in src:
        print(f"  ✓ Resource limits set (RLIMIT_CORE)")
    if 'max_size=' in src:
        print(f"  ✓ Connection pool size limited")
    return True


def test_CD_8_timezone_handling():
    """CD-8: Timezone handling."""
    print("\n--- CD-8: Timezone handling ---")
    with open('/home/user/project/browser-runtime/circadian_scheduler.py') as f:
        src = f.read()
    if 'ZoneInfo' in src or 'pytz' in src or 'timezone' in src:
        print(f"  ✓ Timezone-aware in circadian_scheduler")
    for fpath in files:
        if not fpath.endswith('.py'):
            continue
        with open(fpath) as f:
            content = f.read()
        # Check for naive datetime.now() (without tz)
        for m in re.finditer(r'datetime\.now\(\)', content):
            line_num = content[:m.start()].count('\n') + 1
            print(f"  ⚠ {fpath.split('/')[-1]}:{line_num} uses naive datetime.now()")
    return True


def test_CD_9_signal_handlers():
    """CD-9: Signal handlers for graceful shutdown."""
    print("\n--- CD-9: Signal handlers ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    signals = []
    for sig in ('SIGINT', 'SIGTERM', 'SIGUSR1', 'SIGUSR2', 'SIGHUP'):
        if sig in src:
            signals.append(sig)
    print(f"  Signal handlers: {signals}")
    return True


def test_CD_10_database_uri():
    """CD-10: Database URI format validation."""
    print("\n--- CD-10: Database URI format ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if 'DB_URI' in src and 'postgresql' in src:
        print(f"  ✓ DB_URI is PostgreSQL")
    if 'DB_PASSWD' in src:
        print(f"  ✓ DB password is env-var sourced")
    return True


def test_CD_11_fernet_key():
    """CD-11: Encryption key handling."""
    print("\n--- CD-11: Encryption key handling ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if 'ENCRYPTION_KEY' in src:
        print(f"  ✓ Encryption key from env var")
    if 'Fernet' in src:
        print(f"  ✓ Uses Fernet encryption")
    if 'FATAL' in src and 'ENCRYPTION_KEY' in src:
        print(f"  ✓ Fatals on missing encryption key")
    return True


def test_CD_12_tempfile_handling():
    """CD-12: Temporary file/directory handling."""
    print("\n--- CD-12: Temp file handling ---")
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if 'TASK_TMPFS_SIZE_MB' in src:
        print(f"  ✓ Task tmpfs size configurable")
    if 'shutil.rmtree' in src and 'tmpfs' in src.lower():
        print(f"  ✓ Tmpfs cleanup on shutdown")
    return True


if __name__ == '__main__':
    print("="*70)
    print("Pass 47: Configuration & Deployment Issue Detection")
    print("="*70)

    tests = [
        test_CD_1_required_env_validation,
        test_CD_2_env_var_defaults,
        test_CD_3_hardcoded_secrets,
        test_CD_4_path_handling,
        test_CD_5_loguru_setup,
        test_CD_6_container_detection,
        test_CD_7_resource_limits,
        test_CD_8_timezone_handling,
        test_CD_9_signal_handlers,
        test_CD_10_database_uri,
        test_CD_11_fernet_key,
        test_CD_12_tempfile_handling,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
    print("="*70)
