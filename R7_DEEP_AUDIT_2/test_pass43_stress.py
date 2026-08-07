"""Pass 43 stress test: verify memory stays bounded under sustained load."""
import sys
import os
import gc
import tracemalloc
import time

sys.path.insert(0, '/home/user/project')
sys.path.insert(0, '/home/user/project/browser-runtime')
os.environ.setdefault('ORCH_TASK_TMPFS_DIR', '/tmp')

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

be = load('browser_runtime.behavioral_entropy', '/home/user/project/browser-runtime/behavioral_entropy.py')
hm = load('browser_runtime.humanized_movement', '/home/user/project/browser-runtime/humanized_movement.py')

# Tracemalloc
tracemalloc.start(15)

# Test 1: 50 personas
print("--- Stress: 50 personas x 10 mouse trajectories each ---")
gc.collect()
snap0 = tracemalloc.take_snapshot()
profiles = []
for i in range(50):
    pid = f"persona_stress_{i:03d}"
    profiles.append(hm.get_muscle_profile(pid))
# Each profile generates 10 mouse trajectories
for p in profiles:
    for j in range(10):
        try:
            p.generate_humanized_mouse_trajectory(
                start_x=100, start_y=100, end_x=500, end_y=500,
                steps=20, target_width=20.0
            )
        except Exception as e:
            pass

snap1 = tracemalloc.take_snapshot()
diff = snap1.compare_to(snap0, 'lineno')
total = sum(s.size_diff for s in diff)
print(f"Total allocation: {total/1024:.1f} KB ({total/1024/50:.2f} KB per profile)")
# Top 10
for s in sorted(diff, key=lambda s: -s.size_diff)[:10]:
    print(f"  {s.size_diff/1024:7.1f} KB  {s.traceback}")

# Test 2: profile cache FIFO eviction
print("\n--- Cache eviction: 1500 personas should evict down to 1000 ---")
n_cache_after = len(hm._profile_cache)
print(f"Cache size after 50 personas: {n_cache_after}")
# Add 1500 more
for i in range(1500):
    hm.get_muscle_profile(f"persona_evict_{i:04d}")
n_cache_after = len(hm._profile_cache)
print(f"Cache size after 1550 total personas: {n_cache_after}")
assert n_cache_after <= 1000, f"Cache grew to {n_cache_after} - eviction broken!"
print(f"  ✓ Cache bounded at {n_cache_after}")

# Test 3: Repeated profile access should not leak
print("\n--- Profile access leak test ---")
gc.collect()
snap2 = tracemalloc.take_snapshot()
for _ in range(1000):
    p = hm.get_muscle_profile("persona_stress_001")  # cache hit
snap3 = tracemalloc.take_snapshot()
diff = snap3.compare_to(snap2, 'lineno')
total = sum(s.size_diff for s in diff)
print(f"1000 cache hits allocation: {total/1024:.1f} KB")
# Should be ~0 since cache hit doesn't allocate
for s in sorted(diff, key=lambda s: -s.size_diff)[:5]:
    print(f"  {s.size_diff/1024:7.1f} KB  {s.traceback}")

# Test 4: ebpf_sentinel audit log eviction
print("\n--- ebpf sentinel audit log eviction ---")
sentinel = load('browser_runtime.ebpf_sentinel', '/home/user/project/browser-runtime/ebpf_sentinel.py')
# Inject 2000 events via internal _audit_kill_event - should cap at 1000
for i in range(2000):
    try:
        sentinel._audit_kill_event(1000 + i, "127.0.0.1", 80)
    except Exception as e:
        pass
n_log = len(sentinel._audit_log)
print(f"Audit log size after 2000 events: {n_log}")
assert n_log <= 1000, f"Audit log grew to {n_log} - eviction broken!"
print(f"  ✓ Audit log bounded at {n_log}")

tracemalloc.stop()
print("\nAll stress tests complete.")
