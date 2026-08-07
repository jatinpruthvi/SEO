"""Verify R7-DYN-006 fix: pacing values should be deterministic per persona."""
import sys
import os
sys.path.insert(0, '/home/user/project')
sys.path.insert(0, '/home/user/project/browser-runtime')
os.environ.setdefault('ORCH_TASK_TMPFS_DIR', '/tmp')
os.environ.setdefault('DB_PASSWD', 'x' * 32)
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 64)
os.environ.setdefault('TASK_TMPFS_SIZE_MB', '512')
os.environ.setdefault('PERSONA_PID_BUDGET', '100')

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

# Load modules
be = load('browser_runtime.behavioral_entropy', '/home/user/project/browser-runtime/behavioral_entropy.py')
hm = load('browser_runtime.humanized_movement', '/home/user/project/browser-runtime/humanized_movement.py')
dp = load('browser_runtime.dynamic_pacing', '/home/user/project/browser-runtime/dynamic_pacing.py')

# Get two different personas
profile_a = hm.get_muscle_profile("dyn_test_persona_a")
profile_b = hm.get_muscle_profile("dyn_test_persona_b")

# Get pacing_rngs
rng_a = profile_a.pacing_rng
rng_b = profile_b.pacing_rng

# Sample some values from each
samples_a = [rng_a.exponential(3) for _ in range(5)]
samples_b = [rng_b.exponential(3) for _ in range(5)]

print(f"Persona A pacing samples: {[f'{s:.3f}' for s in samples_a]}")
print(f"Persona B pacing samples: {[f'{s:.3f}' for s in samples_b]}")

# Verify they're different
if samples_a != samples_b:
    print(f"✓ Different personas produce different samples (per-persona determinism)")
else:
    print(f"✗ Personas share samples (bug!)")

# Verify same persona is deterministic
rng_a_again = hm.get_muscle_profile("dyn_test_persona_a").pacing_rng
samples_a2 = [rng_a_again.exponential(3) for _ in range(5)]
# This won't match because the singleton returned the SAME instance with advanced state
print(f"ℹ Second call (same singleton, advanced state): {[f'{s:.3f}' for s in samples_a2]}")

# Test: new instance should give same starting values
profile_c = be.BehavioralEntropyGenerator(persona_id="dyn_test_persona_fresh_c")
profile_d = be.BehavioralEntropyGenerator(persona_id="dyn_test_persona_fresh_c")
samples_c = [profile_c.pacing_rng.exponential(3) for _ in range(3)]
samples_d = [profile_d.pacing_rng.exponential(3) for _ in range(3)]
if samples_c == samples_d:
    print(f"✓ Same persona_id produces same starting samples (reproducibility)")
else:
    print(f"✗ Different starting samples for same persona_id")
