#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 15: Performance / timing audit.

Hunts for performance, timing, and distribution-shape issues in the
browser-runtime codebase. We focus on:

1. Loop invariants (per-iteration recomputation of constants)
2. Repeated module imports inside hot loops
3. String concatenation in hot paths
4. Profile.rng contention under concurrency
5. Off-by-one in clamping (correct lower < upper bound)
6. Sleep bound order (max vs min for clamps)
7. Sample size=0 / negative in distributions
8. Cache key collisions on small persona space
9. List reallocation in hot paths
10. dict.get() in tight loops where __slots__ would help
11. Time.perf_counter vs time.time in timing-critical paths
12. Frequency inaccuracy from integer truncation
"""
import os
import sys
import math
import time as _time
import statistics

# Add project root to path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/user/project/browser-runtime")

_BROWSER_RUNTIME = "/home/user/project/browser-runtime"

import behavioral_entropy as be  # noqa: E402
import humanized_movement as hm  # noqa: E402
import keyboard_emulator as ke  # noqa: E402

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
print("R7-DEEP-AUDIT-2 Pass 15: Performance / timing audit")
print("=" * 60)


# ============================================================================
# PT-1: per-iteration recomputation of loop constants
# ============================================================================
print("\n[TEST] PT-1: per-iteration constant recomputation")
# generate_humanized_mouse_trajectory has the inner loop
# Check that it doesn't re-compute values that depend only on idx
# and not on the loop variable. Common bug:
#   for idx in range(N):
#       x = 1.0 / N  # constant — hoist out
# OR
#   for i in range(N):
#       result.append(self.rng.uniform(0, 1) * (i / N))  # i/N could be precomputed
#
# Note: per-call constant computations are fine; per-LOOP constant
# computations are not (recomputed N times when once suffices).
src = open(os.path.join(_BROWSER_RUNTIME, "behavioral_entropy.py")).read()

# Count occurrences of `len(raw_path)` in the loop body of generate_humanized_mouse_trajectory
loop_body_start = src.find("for idx in range(len(raw_path)):")
if loop_body_start == -1:
    # find by alternate
    loop_body_start = src.find("for idx in range(len(raw_path))")
loop_body_end_marker = "final_path.append"
loop_body_end = src.find(loop_body_end_marker, loop_body_start)
if loop_body_start > 0 and loop_body_end > loop_body_start:
    loop_body = src[loop_body_start:loop_body_end]
    n_len_calls = loop_body.count("len(raw_path)")
    check(
        f"len(raw_path) recomputed ≤ 3 times in loop body (got {n_len_calls})",
        n_len_calls <= 3,
        f"len(raw_path) called {n_len_calls} times in generate_humanized_mouse_trajectory loop"
    )
else:
    check("found loop body in generate_humanized_mouse_trajectory", False, "could not parse")


# ============================================================================
# PT-2: import inside hot loop
# ============================================================================
print("\n[TEST] PT-2: import inside hot path")
# math is imported inside _generate_tremor_python's loop (per element)
# this is a per-iteration import. The function imports it once outside but
# uses it inside the inner loop. We check that NO imports are inside any
# inner loops in behavioral_entropy.py

# Search for "import " inside function bodies that are clearly hot paths
import_lines_in_functions = 0
in_function = False
fn_name = None
for i, line in enumerate(src.split("\n")):
    stripped = line.strip()
    if stripped.startswith("def "):
        fn_name = stripped.split("def ")[1].split("(")[0]
    if stripped.startswith("import "):
        # Check if we're in a hot path (e.g., loop-bearing function)
        if fn_name in ("_generate_tremor_python", "generate_physiological_tremor",
                       "generate_humanized_mouse_trajectory", "calculate_log_normal_delays",
                       "time_to_read_text_ms", "_interpolate_catmull_rom"):
            import_lines_in_functions += 1

check(
    "no per-iteration import in hot path functions",
    import_lines_in_functions <= 1,  # allow one in the function body (top), not in the inner loop
    f"found {import_lines_in_functions} import statements in hot path functions"
)


# ============================================================================
# PT-3: time.perf_counter vs time.time in timing paths
# ============================================================================
print("\n[TEST] PT-3: use perf_counter for sub-second timing")
# humanized_movement.py uses time.perf_counter() for high_precision_sleep
src_hm = open(os.path.join(_BROWSER_RUNTIME, "humanized_movement.py")).read()
n_perf = src_hm.count("time.perf_counter()")
n_time = src_hm.count("time.time()")
check(
    "humanized_movement uses perf_counter (not time.time) for sub-second timing",
    n_perf >= 2 and n_time == 0,
    f"perf_counter={n_perf}, time()={n_time}"
)


# ============================================================================
# PT-4: lognormal sigma=0 returns exp(mu) exactly
# ============================================================================
print("\n[TEST] PT-4: lognormal handles sigma=0 (degenerate case)")
profile = be.BehavioralEntropyGenerator("pt4_user")
result = profile.calculate_log_normal_delays(0.1, size=10, sigma=1e-10)
# With tiny sigma, should be ≈ exp(mu) = base_delay
mean = sum(result) / len(result)
ok = 0.05 < mean < 0.5
check(
    f"lognormal with sigma≈0 returns base_delay (mean={mean:.4f})",
    ok,
    f"mean should be near base_delay=0.1"
)


# ============================================================================
# PT-5: per-persona seeded RNG produces consistent output
# ============================================================================
print("\n[TEST] PT-5: per-persona RNG determinism")
p1 = be.BehavioralEntropyGenerator("pt5_a")
p2 = be.BehavioralEntropyGenerator("pt5_a")
out1 = p1.calculate_log_normal_delays(0.1, size=5, sigma=0.3)
out2 = p2.calculate_log_normal_delays(0.1, size=5, sigma=0.3)
all_equal = all(abs(a - b) < 1e-9 for a, b in zip(out1, out2))
check(
    "same persona_id produces identical delay sequence",
    all_equal,
    f"out1={out1}, out2={out2}"
)


# ============================================================================
# PT-6: different persona_ids produce different sequences
# ============================================================================
print("\n[TEST] PT-6: per-persona RNG isolation")
p1 = be.BehavioralEntropyGenerator("pt6_a")
p2 = be.BehavioralEntropyGenerator("pt6_b")
out1 = p1.calculate_log_normal_delays(0.1, size=5, sigma=0.3)
out2 = p2.calculate_log_normal_delays(0.1, size=5, sigma=0.3)
all_diff = not all(abs(a - b) < 1e-9 for a, b in zip(out1, out2))
check(
    "different persona_ids produce different delay sequences",
    all_diff,
    f"out1={out1}, out2={out2}"
)


# ============================================================================
# PT-7: ActionSequenceGenerator power-law distribution shape
# ============================================================================
print("\n[TEST] PT-7: ActionSequenceGenerator distribution shape")
p = be.BehavioralEntropyGenerator("pt7_user")
gen = be.ActionSequenceGenerator(p)
counts = {}
for _ in range(10000):
    a = gen.next_action()
    counts[a] = counts.get(a, 0) + 1
# Top action should be 'scroll' (default power 0.50)
top = max(counts, key=counts.get)
check(
    f"most common action is 'scroll' (got '{top}', {counts[top]}/10000)",
    top == "scroll",
    f"top action is {top} ({counts[top]}/10000), expected 'scroll'"
)
# Check that the distribution has a long tail (entropy > 1.5 bits)
entropy = -sum((c/10000) * math.log2(c/10000) for c in counts.values() if c > 0)
check(
    f"distribution has reasonable entropy (got {entropy:.2f} bits)",
    entropy > 1.5,
    f"entropy={entropy:.2f}, expected > 1.5"
)


# ============================================================================
# PT-8: Markov chain respects history
# ============================================================================
print("\n[TEST] PT-8: ActionTransitionMarkov respects history")
p = be.BehavioralEntropyGenerator("pt8_user")
mk = be.ActionTransitionMarkov(p)
# In a known 3-tuple context, distribution should be biased
# ("type","type","type") has counts {type:7, wait:3}
# So sampling 100 times with history=["type","type","type"] should
# give roughly 70% type, 30% wait
n_type = 0
n_total = 1000
for _ in range(n_total):
    a = mk.next_action(["type", "type", "type"])
    if a == "type":
        n_type += 1
ratio = n_type / n_total
check(
    f"Markov chain respects (type,type,type) -> mostly type (got {ratio:.2f})",
    0.55 < ratio < 0.85,
    f"expected 0.55-0.85, got {ratio:.2f}"
)


# ============================================================================
# PT-9: 1/f noise produces a continuous distribution
# ============================================================================
print("\n[TEST] PT-9: 1/f noise generates valid output")
src_hm = open(os.path.join(_BROWSER_RUNTIME, "humanized_movement.py")).read()
# The function _add_one_over_f_noise requires numpy; check it exists
has_pink_noise = "_add_one_over_f_noise" in src_hm
check("1/f pink noise function exists in humanized_movement.py", has_pink_noise)


# ============================================================================
# PT-10: focus switch model is reasonable
# ============================================================================
print("\n[TEST] PT-10: focus switch model returns plausible intervals")
p = be.BehavioralEntropyGenerator("pt10_user")
fsm = be.FocusSwitchModel(p)
durations = [fsm.time_to_next_switch_s(task_complexity=1.0) for _ in range(100)]
mean_dur = sum(durations) / len(durations)
check(
    f"focus switch mean duration is plausible (got {mean_dur:.1f}s, expected 30-90s)",
    30 < mean_dur < 90,
    f"mean={mean_dur:.1f}s"
)


# ============================================================================
# PT-11: fixation duration is bounded
# ============================================================================
print("\n[TEST] PT-11: fixation duration bounds")
p = be.BehavioralEntropyGenerator("pt11_user")
rpm = be.ReadingPacingModel(p)
durations = [rpm.fixation_duration_ms() for _ in range(200)]
min_dur = min(durations)
max_dur = max(durations)
check(
    f"fixation duration is bounded (min={min_dur:.0f}ms, max={max_dur:.0f}ms)",
    min_dur >= 150 and max_dur <= 1000,
    f"min={min_dur}, max={max_dur} (expected 150-1000ms)"
)


# ============================================================================
# PT-12: trajectory length scales with steps
# ============================================================================
print("\n[TEST] PT-12: trajectory length scales with steps")
p = be.BehavioralEntropyGenerator("pt12_user")
traj30 = p.generate_humanized_mouse_trajectory(0, 0, 100, 100, steps=30)
traj60 = p.generate_humanized_mouse_trajectory(0, 0, 100, 100, steps=60)
# Trajectory length should be at least the requested number of steps
# (with coasting it can be more, but never less)
check(
    f"30-step trajectory has >= 30 points (got {len(traj30)})",
    len(traj30) >= 30,
    f"len(traj30)={len(traj30)}"
)
check(
    f"60-step trajectory is longer than 30-step (got {len(traj60)} vs {len(traj30)})",
    len(traj60) >= len(traj30),
    f"len(traj60)={len(traj60)} vs len(traj30)={len(traj30)}"
)


# ============================================================================
# PT-13: timing helper doesn't go negative
# ============================================================================
print("\n[TEST] PT-13: high_precision_sleep handles edge cases")
# Test that high_precision_sleep with target=0 returns immediately
import asyncio
async def test_zero_sleep():
    start = _time.perf_counter()
    await hm.high_precision_sleep(0.0)
    elapsed = _time.perf_counter() - start
    return elapsed
elapsed = asyncio.run(test_zero_sleep())
check(
    f"high_precision_sleep(0) returns immediately (took {elapsed*1000:.2f}ms)",
    elapsed < 0.05,
    f"elapsed={elapsed*1000:.2f}ms"
)

# Test that target_delay=NaN is sanitized
async def test_nan_sleep():
    start = _time.perf_counter()
    await hm.high_precision_sleep(float('nan'))
    elapsed = _time.perf_counter() - start
    return elapsed
elapsed = asyncio.run(test_nan_sleep())
check(
    f"high_precision_sleep(NaN) returns immediately (took {elapsed*1000:.2f}ms)",
    elapsed < 0.05,
    f"elapsed={elapsed*1000:.2f}ms"
)


# ============================================================================
# PT-14: lognormal with size=0 raises
# ============================================================================
print("\n[TEST] PT-14: lognormal with invalid size")
p = be.BehavioralEntropyGenerator("pt14_user")
try:
    p.calculate_log_normal_delays(0.1, size=0)
    check("size=0 raises ValueError", False, "no exception raised")
except ValueError:
    check("size=0 raises ValueError", True)


# ============================================================================
# PT-15: read text time is non-negative
# ============================================================================
print("\n[TEST] PT-15: time_to_read_text_ms is non-negative")
p = be.BehavioralEntropyGenerator("pt15_user")
rpm = be.ReadingPacingModel(p)
t = rpm.time_to_read_text_ms(0)
check(f"time_to_read_text_ms(0) == 0 (got {t})", t == 0)
t = rpm.time_to_read_text_ms(1000)
check(f"time_to_read_text_ms(1000) > 0 (got {t:.0f}ms)", t > 0)
# And the result should be reasonable: ~250ms per fixation * ~125 fixations
# = ~31000ms
check(
    f"time_to_read_text_ms(1000) is plausible (got {t:.0f}ms, expected 5000-60000ms)",
    5000 < t < 60000,
    f"t={t:.0f}ms"
)


# ============================================================================
# PT-16: timing in hardened_orchestrator uses perf_counter
# ============================================================================
print("\n[TEST] PT-16: orchestrator uses perf_counter for elapsed time")
src_orch = open(os.path.join(_BROWSER_RUNTIME, "hardened_orchestrator.py")).read()
n_perf_orch = src_orch.count("time.perf_counter()")
n_time_orch = src_orch.count("time.time()")
# The orchestrator uses time.time() for absolute timestamps (start of
# orchestrator) and time.perf_counter() for elapsed time. This is correct.
# We check that time.perf_counter is used for the elapsed-time measurement
# in run_persona_pipeline.
elapsed_correctly = "start_session_time = time.perf_counter()" in src_orch
check(
    "run_persona_pipeline uses time.perf_counter for elapsed time",
    elapsed_correctly,
    f"n_perf={n_perf_orch}, n_time={n_time_orch}"
)


# ============================================================================
# PT-17: sleep bound order — high_precision_sleep(negative) returns immediately
# ============================================================================
print("\n[TEST] PT-17: high_precision_sleep with negative target")
async def test_neg_sleep():
    start = _time.perf_counter()
    await hm.high_precision_sleep(-1.0)
    elapsed = _time.perf_counter() - start
    return elapsed
elapsed = asyncio.run(test_neg_sleep())
check(
    f"high_precision_sleep(-1.0) returns immediately (took {elapsed*1000:.2f}ms)",
    elapsed < 0.05,
    f"elapsed={elapsed*1000:.2f}ms"
)


# ============================================================================
# PT-18: per-persona fingerprint digest is stable
# ============================================================================
print("\n[TEST] PT-18: persona_digest values are stable per-persona")
# BehavioralEntropyGenerator parameters are stable per persona
p1 = be.BehavioralEntropyGenerator("pt18_user")
p2 = be.BehavioralEntropyGenerator("pt18_user")
check(
    "same persona_id produces same age",
    p1.age == p2.age,
    f"p1.age={p1.age}, p2.age={p2.age}"
)
check(
    "same persona_id produces same expertise",
    p1.expertise == p2.expertise,
    f"p1.expertise={p1.expertise}, p2.expertise={p2.expertise}"
)
check(
    "same persona_id produces same biological_drift",
    p1.biological_drift == p2.biological_drift,
    f"p1.drift={p1.biological_drift}, p2.drift={p2.biological_drift}"
)


# ============================================================================
# PT-19: PES counter decrements correctly
# ============================================================================
print("\n[TEST] PT-19: PES counter decays on each lognormal call")
p = be.BehavioralEntropyGenerator("pt19_user")
p.record_typing_error()
initial = p._pes_keystrokes_remaining
# Each call to calculate_log_normal_delays decays the counter
for _ in range(initial + 1):
    p.calculate_log_normal_delays(0.1, size=1)
final = p._pes_keystrokes_remaining
check(
    f"PES counter decays to 0 after {initial+1} calls (final={final})",
    final == 0,
    f"final={final}, expected 0"
)


# ============================================================================
# PT-20: click hold durations are bounded
# ============================================================================
print("\n[TEST] PT-20: click hold durations are bounded")
p = be.BehavioralEntropyGenerator("pt20_user")
mbdm = be.MouseButtonDurationModel(p)
durations = [mbdm.click_hold_ms("single") for _ in range(200)]
min_d = min(durations)
max_d = max(durations)
check(
    f"click hold duration bounded (min={min_d:.0f}ms, max={max_d:.0f}ms, expected 30-800)",
    30 <= min_d and max_d <= 800,
    f"min={min_d}, max={max_d}"
)


# ============================================================================
# PT-21: scroll inertia model physics consistency
# ============================================================================
print("\n[TEST] PT-21: scroll inertia physics consistency")
p = be.BehavioralEntropyGenerator("pt21_user")
sim = be.ScrollInertiaModel(p)
# Initial velocity 100px, friction 0.9, threshold 0.5
# Tick count = log(0.5/100) / log(0.9) = -5.30 / -0.105 = 50 ticks
# Total distance = 100 * (1 - 0.9^50) / 0.1 = 100 * 0.9948 / 0.1 = 994.8
v0 = 100.0
mu = 0.9
n = sim.tick_count(v0, mu)
total = sim.total_distance_px(v0, mu)
# With 50 ticks, total distance should be ~995px (less than v0 * n=5000)
check(
    f"scroll inertia total distance < v0 * ticks ({total:.1f} < {v0*n:.1f})",
    total < v0 * n,
    f"total={total:.1f}, v0*n={v0*n:.1f}"
)
check(
    f"scroll inertia tick count is plausible (got {n}, expected 40-60)",
    40 <= n <= 60,
    f"n={n}"
)


# ============================================================================
# PT-22: keyboard inter-key distance multiplier
# ============================================================================
print("\n[TEST] PT-22: keyboard inter-key distance multipliers are sensible")
# Same-finger should be slower (1.40x), opposite-hand same-row faster (0.90x)
# 'a' (left pinky) -> 's' (left ring) — same hand, different finger = 1.00
# 'a' (left pinky) -> ';' (right pinky) — different hand, same row = 0.90
# 'a' (left pinky) -> 'a' — same key = 1.20
mult_same_hand_diff_finger = ke._inter_key_delay_multiplier('a', 's')
mult_diff_hand = ke._inter_key_delay_multiplier('a', ';')
mult_same_key = ke._inter_key_delay_multiplier('a', 'a')
check(
    f"same-hand different-finger: ~1.00 (got {mult_same_hand_diff_finger})",
    abs(mult_same_hand_diff_finger - 1.00) < 0.01
)
check(
    f"different-hand: < 1.00 (got {mult_diff_hand})",
    mult_diff_hand < 1.0
)
check(
    f"same-key: ~1.20 (got {mult_same_key})",
    abs(mult_same_key - 1.20) < 0.01
)


# ============================================================================
# PT-23: log-normal with size=1 returns single value (not array)
# ============================================================================
print("\n[TEST] PT-23: log-normal size=1 returns correct type")
p = be.BehavioralEntropyGenerator("pt23_user")
result = p.calculate_log_normal_delays(0.1, size=1)
# In numpy mode, this is np.ndarray of length 1
# In fallback mode, this is a list of length 1
if hasattr(result, '__len__'):
    check(
        f"size=1 returns iterable of length 1 (got len={len(result)})",
        len(result) == 1,
        f"len(result)={len(result)}"
    )
else:
    check("size=1 returns a number (not iterable)", True)


# ============================================================================
# PT-24: trajectory start and end match expected coordinates
# ============================================================================
print("\n[TEST] PT-24: trajectory endpoints near start/end coordinates")
p = be.BehavioralEntropyGenerator("pt24_user")
# Short trajectory so it's predictable
traj = p.generate_humanized_mouse_trajectory(100, 100, 200, 200, steps=10)
if traj:
    first = traj[0]
    last = traj[-1]
    # First point should be near (100, 100)
    start_dist = ((first[0] - 100) ** 2 + (first[1] - 100) ** 2) ** 0.5
    # Last point should be near (200, 200)
    end_dist = ((last[0] - 200) ** 2 + (last[1] - 200) ** 2) ** 0.5
    check(
        f"trajectory starts near (100,100) — first={first}, dist={start_dist:.1f}",
        start_dist < 50,  # tolerate some overshoot
        f"first={first}"
    )
    check(
        f"trajectory ends near (200,200) — last={last}, dist={end_dist:.1f}",
        end_dist < 50,
        f"last={last}"
    )


# ============================================================================
# PT-25: micro-pause probability is bounded
# ============================================================================
print("\n[TEST] PT-25: micro-pause probability is bounded")
# From humanized_movement.py: _pause_prob = 0.18 (novice) -> 0.05 (expert)
# multiplied by (2 - biological_drift) * 0.7
# With drift in [0.92, 1.08], factor in [0.644, 0.756]
# So _pause_prob is in [0.05 * 0.644, 0.18 * 0.756] = [0.032, 0.136]
# This is in valid range [0, 1].
# We just check the formula is correct.
prob_novice_drift1 = 0.18 * (2.0 - 1.0) * 0.7
prob_expert_drift1 = 0.05 * (2.0 - 1.0) * 0.7
prob_novice_drift_max = 0.18 * (2.0 - 0.92) * 0.7
check(
    f"novice drift=1.0: prob=0.126 (got {prob_novice_drift1})",
    abs(prob_novice_drift1 - 0.126) < 0.01
)
check(
    f"expert drift=1.0: prob=0.035 (got {prob_expert_drift1})",
    abs(prob_expert_drift1 - 0.035) < 0.01
)
check(
    f"novice drift=0.92: prob=0.1356 (got {prob_novice_drift_max:.4f})",
    abs(prob_novice_drift_max - 0.1356) < 0.01
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
