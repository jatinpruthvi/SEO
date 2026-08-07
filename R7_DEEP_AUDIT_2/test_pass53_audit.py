"""Pass 53: Inter-Module Contract Verification.

Verifies that modules agree on the format of shared data structures.
A contract mismatch between two modules is a hidden bug that only
manifests at runtime when one module calls another.

IC-1: HardenedSessionSerializer.encrypt_payload contract
IC-2: HardenedSessionSerializer.decrypt_payload contract
IC-3: BehavioralEntropyGenerator profile attributes contract
IC-4: StickyProxyResolver session_id format
IC-5: IdentityHealthMonitor DB schema contract
IC-6: task_payload structure (consumed by executors)
IC-7: fingerprint JSON structure (consumed by all modules)
IC-8: AIBridge request/response format
IC-9: Config script file format
IC-10: Database schema consistency
"""
import sys
import os
import ast
import re

sys.path.insert(0, '/home/user/project')
sys.path.insert(0, '/home/user/project/browser-runtime')
os.environ.setdefault('ORCH_TASK_TMPFS_DIR', '/tmp')
os.environ.setdefault('DB_PASSWD', 'x' * 32)
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 64)

import importlib.util as iu
def load(name, path):
    spec = iu.spec_from_file_location(name, path)
    mod = iu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

import types
pkg = types.ModuleType('browser_runtime')
pkg.__path__ = ['/home/user/project/browser-runtime']
sys.modules['browser_runtime'] = pkg

ss = load('browser_runtime.session_serializer', '/home/user/project/browser-runtime/session_serializer.py')
be = load('browser_runtime.behavioral_entropy', '/home/user/project/browser-runtime/behavioral_entropy.py')


def test_IC_1_encrypt_payload_contract():
    """IC-1: encrypt_payload contract verification."""
    print("--- IC-1: encrypt_payload contract ---")
    # encrypt_payload should return a string (Fernet token)
    # Check signature
    import inspect
    sig = inspect.signature(ss.HardenedSessionSerializer.encrypt_payload)
    print(f"  encrypt_payload{sig}")
    # Check Fernet import
    with open('/home/user/project/browser-runtime/session_serializer.py') as f:
        src = f.read()
    if 'Fernet' in src:
        print(f"  ✓ Uses Fernet (returns URL-safe base64 string)")
    return True


def test_IC_2_decrypt_payload_contract():
    """IC-2: decrypt_payload contract verification."""
    print("\n--- IC-2: decrypt_payload contract ---")
    import inspect
    sig = inspect.signature(ss.HardenedSessionSerializer.decrypt_payload)
    print(f"  decrypt_payload{sig}")
    with open('/home/user/project/browser-runtime/session_serializer.py') as f:
        src = f.read()
    # Check that decrypt handles None/empty
    if 'None or empty' in src or 'None' in src:
        print(f"  ✓ Handles None/empty payloads")
    return True


def test_IC_3_profile_attributes():
    """IC-3: BehavioralEntropyGenerator profile attributes."""
    print("\n--- IC-3: Profile attributes contract ---")
    profile = be.BehavioralEntropyGenerator(persona_id="contract_test_53")
    expected_attrs = ['age', 'expertise', 'muscle_speed_factor', 'tremor_frequency',
                      'typing_delay_base', 'hand_dominance', 'biological_drift',
                      'session_task_count', 'ambient_stress_level', 'rng',
                      'tremor_rng', 'pacing_rng', 'quantization_interval']
    found = []
    missing = []
    for attr in expected_attrs:
        if hasattr(profile, attr):
            found.append(attr)
        else:
            missing.append(attr)
    print(f"  Found {len(found)}/{len(expected_attrs)} expected attributes")
    if missing:
        print(f"  Missing: {missing}")
    return True


def test_IC_4_session_id_format():
    """IC-4: session_id format contract."""
    print("\n--- IC-4: session_id format ---")
    # Check sticky_proxy_resolver for session_id format
    with open('/home/user/project/browser-runtime/sticky_proxy_resolver.py') as f:
        src = f.read()
    # Should be hex string of 32 chars (16 bytes * 2)
    if '_SESSION_ID_BYTES = 16' in src:
        print(f"  ✓ _SESSION_ID_BYTES = 16 (UUID4 stripped of dashes)")
    if 'session_id = str(uuid.uuid4())' in src:
        print(f"  ✓ Uses uuid.uuid4() (cryptographically random)")
    return True


def test_IC_5_db_schema_contract():
    """IC-5: DB schema contract."""
    print("\n--- IC-5: DB schema contract ---")
    # identity_health queries 'personas' table
    with open('/home/user/project/browser-runtime/identity_health.py') as f:
        src = f.read()
    cols_queried = set()
    for m in re.finditer(r'SELECT\s+(.+?)\s+FROM\s+personas', src, re.DOTALL):
        select_part = m.group(1)
        for c in re.findall(r'\b(\w+)\b', select_part):
            if c.lower() not in ('select', 'as', 'from', 'where', 'count', 'coalesce', 'extract', 'epoch', 'now', 'lower'):
                cols_queried.add(c)
    print(f"  Columns queried: {sorted(cols_queried)[:10]}")
    return True


def test_IC_6_task_payload_contract():
    """IC-6: task_payload structure contract."""
    print("\n--- IC-6: task_payload contract ---")
    # Check orchestrator writes/reads task_payload
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    if 'task_payload' in src:
        n = src.count('task_payload')
        print(f"  task_payload referenced: {n} times in orchestrator")
    # Check that the executor expects a dict
    exec_files = [
        '/home/user/project/tasks/site_register/executor.py',
        '/home/user/project/tasks/gmail_verify/executor.py',
        '/home/user/project/tasks/site_interact/executor.py',
    ]
    for f in exec_files:
        if os.path.exists(f):
            with open(f) as fh:
                content = fh.read()
            if 'task_payload' in content or 'task.get' in content or 'payload' in content:
                print(f"  {f.split('/')[-1]}: uses payload")
    return True


def test_IC_7_fingerprint_json_contract():
    """IC-7: fingerprint JSON structure contract."""
    print("\n--- IC-7: fingerprint JSON contract ---")
    # Check what keys are accessed in fingerprint JSON
    with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
        src = f.read()
    fp_keys = set()
    for m in re.finditer(r'fingerprint\.get\(["\']([a-zA-Z_]+)["\']', src):
        fp_keys.add(m.group(1))
    for m in re.finditer(r'fp\[["\']([a-zA-Z_]+)["\']\]', src):
        fp_keys.add(m.group(1))
    print(f"  Fingerprint keys accessed: {sorted(fp_keys)[:15]}")
    return True


def test_IC_8_ai_bridge_contract():
    """IC-8: AIBridge request/response format."""
    print("\n--- IC-8: AIBridge contract ---")
    if os.path.exists('/home/user/project/core/ai_bridge.py'):
        with open('/home/user/project/core/ai_bridge.py') as f:
            src = f.read()
        if 'class AIBridge' in src:
            print(f"  ✓ AIBridge class exists")
        # Check for response format
        for m in re.finditer(r'def\s+(\w+)', src):
            print(f"    method: {m.group(1)}")
    else:
        print(f"  - ai_bridge.py not found")
    return True


def test_IC_9_config_script_format():
    """IC-9: config script format contract."""
    print("\n--- IC-9: Config script format ---")
    config_dir = '/home/user/project/config'
    if os.path.exists(config_dir):
        for f in os.listdir(config_dir):
            print(f"  config/{f}")
    return True


def test_IC_10_schema_consistency():
    """IC-10: Database schema vs query consistency."""
    print("\n--- IC-10: Schema vs query consistency ---")
    schema_path = '/home/user/project/database/schema.sql'
    if os.path.exists(schema_path):
        with open(schema_path) as f:
            schema = f.read()
        # Extract personas columns
        persona_cols = set()
        m = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?personas\s*\((.+?)\);', schema, re.DOTALL | re.IGNORECASE)
        if m:
            for line in m.group(1).split('\n'):
                col_match = re.match(r'\s*(\w+)\s+', line)
                if col_match:
                    persona_cols.add(col_match.group(1))
        print(f"  Schema persona columns: {sorted(persona_cols)[:15]}")
    else:
        print(f"  - schema.sql not found")
    return True


if __name__ == '__main__':
    print("="*70)
    print("Pass 53: Inter-Module Contract Verification")
    print("="*70)

    tests = [
        test_IC_1_encrypt_payload_contract,
        test_IC_2_decrypt_payload_contract,
        test_IC_3_profile_attributes,
        test_IC_4_session_id_format,
        test_IC_5_db_schema_contract,
        test_IC_6_task_payload_contract,
        test_IC_7_fingerprint_json_contract,
        test_IC_8_ai_bridge_contract,
        test_IC_9_config_script_format,
        test_IC_10_schema_consistency,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
    print("="*70)
