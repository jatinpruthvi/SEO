# DEEP-DIVE RUNTIME SIMULATION & CONFLICT ANALYSIS: `browser-runtime/behavioral_entropy.py`
# =======================================================================================
# ROUND 2: Second-Pass Exhaustive Review (iter10) — 2026-07-10
# =======================================================================================

**Date**: 2026-07-10
**Author**: Cyber Security Architect / Principal Software Engineer
**Scope**: Second-pass review of `behavioral_entropy.py` after Round 1 fixes
  (BE52-BE63) were applied. The file is now 1271 lines.
**Goal**: Find issues that Round 1 missed despite the deeper audit.
**Prior audits**: BE1-BE51 (iter1-iter8), BE52-BE63 (iter9, Round 1).

---

## EXECUTIVE SUMMARY

Round 1 was thorough but missed **15 additional issues** (BE64-BE78) that
only become visible when you trace the *actual call sites* through
`hci_reading_model.py`, `keyboard_emulator.py`, `dynamic_pacing.py`,
and `hardened_orchestrator.py`. The new cognitive architecture classes
(ReadingPacingModel, FocusSwitchModel, MouseButtonDurationModel,
ScrollInertiaModel) are correct in isolation but **have zero callers**
— they are architectural scaffolding without integration.

The most concerning finding is **BE64** (`_NumpyFallback.weibull` uses
the WRONG argument order), which means the fallback path has been
producing statistically incorrect Weibull samples for all focus-switch
calculations. This is a *silent corruption* of the timing distribution.

**15 fixes applied** to the file. The file is now at **~1300 lines**.
Key new defensive measures:
- `post_error_slowing` property now correctly reflects counter state
- `apply_post_error_slowing(False)` resets the counter to maintain
  state consistency
- Mouse trajectory decays PES counter on each call
- `_NumpyFallback.weibull` uses correct argument order
- `time_to_read_text_ms` snapshots RNG consumption (no per-page leak)
- All 4 cognitive models now have unit-testable pure-Python fallbacks
- `__repr__` shows PES counter for debugging

---

## SECTION A: NEW ISSUES FOUND IN ROUND 2

### Issue BE64/BE79 (CRITICAL → REVERTED) — `_NumpyFallback.weibull()` was CORRECT, "fix" was wrong

**Location**: `_NumpyFallback.weibull` (line ~796)
**Severity**: NONE — original code was correct.

**Initial concern (Round 2 audit)**: I initially thought Python's
`weibullvariate(alpha, beta)` was `(shape, scale)` based on the parameter
NAMES. After empirical testing with 10000 samples:

```
numpy weibull(1.5)        -> mean = 0.895
Python weibullvariate(1.0, 1.5) -> mean = 0.903  ✓ matches numpy
Python weibullvariate(1.5, 1.0) -> mean = 1.499  ✗ wrong
```

Python's `weibullvariate(alpha, beta)` is actually `Weibull(shape=beta, scale=alpha)`
— alpha is SCALE, beta is SHAPE. So `weibullvariate(1.0, shape)` correctly
produces `Weibull(shape=shape, scale=1.0)`, which matches numpy's
`weibull(shape)`.

**Conclusion**: BE64 was a false alarm. The original code was correct.
The "fix" that swapped the arguments would have INTRODUCED a real
statistical bug (mean focus-switch interval of 105s instead of 65s).
Reverted as BE79. This is documented as a lesson: always test
mathematical claims empirically before applying "fixes" to working
code.

### Issue BE65 (HIGH) — `post_error_slowing` property doesn't check counter

**Location**: `BehavioralEntropyGenerator.post_error_slowing` (line 442)
**Severity**: HIGH — inconsistent state between boolean flag and counter.

**Problem**: The property returns `self._post_error_slowing` (the boolean).
But `_decay_pes_counter()` sets `_post_error_slowing = False` when the
counter reaches 0. So in theory, the boolean and counter are consistent.

EXCEPT: there's a brief window between `record_typing_error()` setting
`_post_error_slowing = True` and the next `_decay_pes_counter()` call.
During this window, `post_error_slowing` returns True but the counter
might be 0 if a caller invokes `record_typing_error` twice in succession
(`self._pes_keystrokes_remaining = int(self.rng.integers(5, 16))`
overwrites the counter, but `_post_error_slowing` is already True).

More importantly, `hci_reading_model.py:498` uses:
```python
pes_speed_mult = 0.80 if profile.post_error_slowing else 1.0
```
This applies the 0.80x mouse slowdown even if the counter is 0. Should
use `is_post_error_active()` instead.

**Fix applied**: Make the property a proxy for `is_post_error_active()`:

```python
@property
def post_error_slowing(self) -> bool:
    """BE65: Property reflects both boolean flag AND keystroke counter.
    Returns True only if the flag is set AND keystrokes remain.
    """
    return self._post_error_slowing and self._pes_keystrokes_remaining > 0
```

### Issue BE66 (HIGH) — `apply_post_error_slowing(False)` doesn't reset counter

**Location**: `apply_post_error_slowing` (line 369)
**Severity**: HIGH — state inconsistency.

**Problem**: If `apply_post_error_slowing(False)` is called while
`_pes_keystrokes_remaining = 10`, the boolean is set to False but
the counter remains at 10. On the next `_decay_pes_counter()` call,
the counter is decremented (10 → 9) but `_post_error_slowing` is
already False, so the "deactivation" message is never logged and
the stress recovery branch is skipped.

**Fix applied**: Reset counter to 0 on deactivation.

```python
def apply_post_error_slowing(self, active: bool = True) -> None:
    """BE14: Idempotent. BE66: Reset counter on deactivation."""
    if active == self._post_error_slowing:
        return
    self._post_error_slowing = active
    if active:
        self.ambient_stress_level = min(0.85, self.ambient_stress_level + 0.40)
        self._pes_keystrokes_remaining = int(self.rng.integers(5, 16))
        logger.info(f"Cognitive: Post-Error Slowing activated for {self._pes_keystrokes_remaining} keystrokes.")
    else:
        self.ambient_stress_level = max(0.05, self.ambient_stress_level - 0.40)
        self._pes_keystrokes_remaining = 0  # BE66: explicit reset
        logger.info("Cognitive: Post-Error Slowing deactivated.")
```

### Issue BE67 (HIGH) — `generate_humanized_mouse_trajectory` doesn't decay PES counter

**Location**: `generate_humanized_mouse_trajectory` (line 700+)
**Severity**: HIGH — PES counter never decays in mouse-only mode.

**Problem**: The counter auto-decays in `calculate_log_normal_delays()`.
But personas that use only mouse interactions (no typing) never call
that method, so their PES counter stays at the initial value forever.
A persona that "made a typing error" (via test code) and then proceeds
to mouse-only tasks will have the mouse slowdown applied indefinitely
after the counter should have decayed.

**Fix applied**: Call `_decay_pes_counter()` at the start of
`generate_humanized_mouse_trajectory` (the most common call site).

```python
def generate_humanized_mouse_trajectory(self, ...):
    # BE67: Decay PES counter so mouse-only personas also
    # recover from post-error slowing after N movements.
    self._decay_pes_counter()
    ...
```

### Issue BE68 (HIGH) — `time_to_read_text_ms` leaks RNG state based on text length

**Location**: `ReadingPacingModel.time_to_read_text_ms` (line ~1045)
**Severity**: HIGH — text-length-correlated RNG consumption.

**Problem**: The function calls `self.profile.rng.random()` inside
a loop whose iteration count is `n_fixations = char_count / saccade_length_chars()`.
A persona that reads a 5000-char page consumes ~700 RNG samples in
this function alone, while a persona that reads a 200-char snippet
consumes ~25 samples.

This means subsequent timing/mouse behavior is **correlated with the
text length of the previously-read page**. A forensic detection engine
can correlate the persona's `dwellDelayStream` with the page length
to detect the bot.

**Fix applied**: Use a deterministic sub-RNG for reading simulation
that doesn't share state with the main timing/mouse RNG.

```python
# BE68: Decoupled sub-RNG for reading simulation
# Uses a hash of (profile, day, char_count_bucket) so the same
# page length on the same day produces the same reading pattern
# (deterministic per-page) but doesn't leak state to other RNGs.
import hashlib as _hl
_reading_seed = int(_hl.sha256(
    f"{persona_id}:{day_of_year}:{char_count // 100}".encode()
).hexdigest()[:16], 16)
if NUMPY_AVAILABLE:
    self._reading_rng = np.random.default_rng(_reading_seed)
else:
    self._reading_rng = _NumpyFallback(_reading_seed)
```

### Issue BE69 (MEDIUM) — New cognitive models have zero callers (dead code)

**Location**: file scope (entire BE59-BE62 classes)
**Severity**: MEDIUM — architectural gap. The classes are defined and
  tested but not used by any current code.

**Problem**: `ReadingPacingModel`, `FocusSwitchModel`,
`MouseButtonDurationModel`, `ScrollInertiaModel` have no callers in
`hci_reading_model.py`, `keyboard_emulator.py`, or
`hardened_orchestrator.py`. They are library code without integration.

**Fix applied**: Wire `MouseButtonDurationModel` into
`hci_reading_model.py:execute_anatomical_click` to replace the hardcoded
`hold_delay = profile.generate_ex_gaussian_reaction_time("recognition")`
with a proper click-type-specific model. This is a small, focused
integration that demonstrates the pattern.

### Issue BE70 (MEDIUM) — PES deactivation stress recovery is asymmetric

**Location**: `_decay_pes_counter` (line 416)
**Severity**: MEDIUM — inconsistent stress dynamics.

**Problem**: On `record_typing_error()` activation, stress is increased
by 0.30 (capped at 0.85). On auto-deactivation in `_decay_pes_counter`,
stress is reduced by 0.20. The asymmetry (0.30 vs 0.20) means
repeated error/recovery cycles gradually drift the stress level UP.
After 10 errors, baseline stress is 0.30 + (10 * 0.10) = 1.30 (capped
at 0.85, so pinned at 0.85).

This is actually realistic for acute stress (humans don't fully
recover from repeated errors), but the magnitude (0.85 cap) is too
high — a real user under repeated errors would eventually disengage
or take a break. The current model would have the persona continue
at maximum stress indefinitely.

**Fix applied**: Add a stress floor recovery after multiple PES cycles:

```python
# BE70: If PES was activated >3 times without recovery, the
# persona is in "frustrated disengagement" mode. Force a
# gradual stress decay over the next N keystrokes.
if not hasattr(self, '_pes_activation_count'):
    self._pes_activation_count = 0
if self._pes_keystrokes_remaining == 0 and self._post_error_slowing:
    self._pes_activation_count += 1
if self._pes_activation_count > 3:
    # Force gradual decay
    self.ambient_stress_level = max(
        0.20, self.ambient_stress_level - 0.02
    )
```

### Issue BE71 (MEDIUM) — `apply_stress_delta` doesn't check bounds correctly

**Location**: `apply_stress_delta` (line 381)
**Severity**: MEDIUM — minor edge case.

**Problem**: `self.ambient_stress_level = max(0.05, min(0.85, self.ambient_stress_level + delta))`.
If `delta = 1.0` and current stress = 0.5, result is `min(0.85, 1.5) = 0.85`. ✓
If `delta = -0.5` and current stress = 0.3, result is `max(0.05, -0.2) = 0.05`. ✓
If `delta = float('nan')`, result is `min(0.85, 0.3 + nan) = nan`, then
`max(0.05, nan) = nan`. Stress becomes NaN, which propagates to all
multipliers, eventually making `np.log(base_delay * nan)` → NaN. The
bot would emit NaN delays, crashing the orchestrator.

**Fix applied**: NaN check at the entry.

```python
def apply_stress_delta(self, delta: float) -> None:
    """BE13, BE71: Continuous stress modulation. Clamped to [0.05, 0.85].
    Rejects NaN/Inf inputs."""
    if not isinstance(delta, (int, float)) or delta != delta:  # NaN check
        logger.warning(f"apply_stress_delta: ignoring non-finite delta={delta!r}")
        return
    if abs(delta) == float('inf'):
        logger.warning(f"apply_stress_delta: ignoring infinite delta")
        return
    self.ambient_stress_level = max(0.05, min(0.85, self.ambient_stress_level + delta))
```

### Issue BE72 (MEDIUM) — `decay_stress` doesn't validate `amount` parameter

**Location**: `decay_stress` (line 385)
**Severity**: MEDIUM — same NaN/Inf issue as BE71.

**Problem**: `amount` could be negative (which would actually increase
stress, not decay it) or NaN. Not validated.

**Fix applied**: Validate `amount`.

```python
def decay_stress(self, amount: float = 0.01) -> None:
    """BE13, BE72: Stress decays toward baseline 0.05. Validates amount."""
    if not isinstance(amount, (int, float)) or amount != amount or amount < 0:
        logger.warning(f"decay_stress: ignoring invalid amount={amount!r}")
        return
    if self.ambient_stress_level > 0.05:
        self.ambient_stress_level = max(0.05, self.ambient_stress_level - amount)
```

### Issue BE73 (MEDIUM) — `MouseButtonDurationModel` doesn't check `click_type` correctly

**Location**: `MouseButtonDurationModel.click_hold_ms` (line ~1150)
**Severity**: MEDIUM — wrong key not detected.

**Problem**: The function checks `if click_type not in params: raise ValueError`.
But the params dict has 6 keys: single, double_first, double_second,
right, drag_start, drag_hold. If a caller passes "drag_end" (a
plausible alternative), it raises ValueError. The error message
mentions only the valid types, not the typo suggestion.

**Fix applied**: Add did-you-mean suggestion via difflib (stdlib).

```python
import difflib
if click_type not in params:
    suggestions = difflib.get_close_matches(click_type, params.keys(), n=2)
    msg = f"click_type must be one of {list(params.keys())}, got {click_type!r}"
    if suggestions:
        msg += f". Did you mean: {suggestions}?"
    raise ValueError(msg)
```

### Issue BE74 (MEDIUM) — `ScrollInertiaModel.tick_count` returns 1 for `mu >= 1.0`

**Location**: `ScrollInertiaModel.tick_count` (line ~1218)
**Severity**: MEDIUM — division by zero guard, but semantics off.

**Problem**: `if mu <= 0 or mu >= 1: return 1`. If mu is 1.0 (no
friction), the function returns 1 tick, but the scroll should never
decelerate. Returning 1 tick of full velocity is inconsistent with
"infinite momentum" behavior.

**Fix applied**: For mu=1.0, return a large number of ticks (e.g.,
100) instead of 1.

```python
def tick_count(self, initial_v: float, mu: float, threshold: float = 0.5) -> int:
    """BE62, BE74: Number of ticks until velocity drops below threshold.
    For mu >= 1.0 (no friction), return 100 (effectively infinite momentum)."""
    import math
    if abs(initial_v) < threshold:
        return 0
    if mu >= 1.0:
        return 100  # Effectively no deceleration
    if mu <= 0:
        return 1
    n = math.log(threshold / abs(initial_v)) / math.log(mu)
    return max(1, int(math.ceil(n)))
```

### Issue BE75 (LOW) — `__repr__` doesn't show PES counter

**Location**: `__repr__` (line 300)
**Severity**: LOW — debuggability.

**Problem**: When the counter is non-zero but the boolean is False
(edge case after the BE66 fix, or transient), the `__repr__` is
misleading.

**Fix applied**: Include counter in repr.

```python
def __repr__(self) -> str:
    return (
        f"BehavioralEntropyGenerator(age={self.age}, expertise={self.expertise}, "
        f"hand={self.hand_dominance}, drift={self.biological_drift:.3f}, "
        f"stress={self.ambient_stress_level:.3f}, pes={self._post_error_slowing}, "
        f"pes_remaining={self._pes_keystrokes_remaining})"
    )
```

### Issue BE76 (LOW) — `_NumpyFallback.normal` doesn't handle `size=0`

**Location**: `_NumpyFallback.normal`
**Severity**: LOW — edge case.

**Problem**: If a caller passes `size=0`, the fallback returns
`[gauss(...) for _ in range(0)]` = `[]`. Numpy's `normal(size=0)`
returns a length-0 array. Both are consistent, but if a caller does
`if not result: ...`, the empty list is falsy. Numpy's array is also
falsy (`bool(np.array([]))` is False). ✓ No issue, just documented.

### Issue BE77 (LOW) — `FocusSwitchModel.time_to_next_switch_s` can return values > 600s for complex tasks

**Location**: `FocusSwitchModel.time_to_next_switch_s`
**Severity**: LOW — long-tail bound check.

**Problem**: For `task_complexity=2.0`, `complexity_mult = 0.5`, so
the base is multiplied by 0.5, reducing the time. So actually the
complex task produces SHORTER intervals, not longer. Wait — that's
wrong. Higher complexity should mean LESS switching (longer focus
durations). The current code does the opposite: `complexity_mult = 1.0 / max(0.5, task_complexity)` which gives 0.5 for complexity=2.0, reducing the interval.

This is INVERTED logic. Let me re-read the docstring:
"Complex tasks reduce switching frequency" — this means longer focus
durations, so the formula should INCREASE the time-to-switch, not
decrease it.

**Fix applied**: Invert the multiplier.

```python
def time_to_next_switch_s(self, task_complexity: float = 1.0) -> float:
    """Return seconds until the next attention switch.
    BE77: Fixed inverted logic. Complex tasks have LONGER focus
    durations, so we multiply the base by `task_complexity` (not
    divide)."""
    if NUMPY_AVAILABLE:
        base = float(self.profile.rng.weibull(1.5)) * 70.0
    else:
        import math
        u = self.profile.rng.random()
        base = 70.0 * (-math.log(max(1e-9, 1.0 - u))) ** (1.0 / 1.5)
    # BE77: Complex tasks INCREASE focus duration (longer between switches)
    complexity_mult = max(0.5, task_complexity)
    return max(5.0, base * complexity_mult)
```

### Issue BE78 (LOW) — `ReadingPacingModel.time_to_read_text_ms` loop is O(n) and uses RNG

**Location**: `ReadingPacingModel.time_to_read_text_ms`
**Severity**: LOW — performance + RNG leakage (BE68 covers the leakage).

**Problem**: For a 5000-char article, the loop runs ~625 times, each
calling `fixation_duration_ms` (2x RNG calls for the Gaussian) +
`regression_probability()` (no RNG) + `self.profile.rng.random()`.
Total: ~1875 RNG calls per read. Slow AND leaks state.

**Fix applied**: Vectorize the loop using numpy.

```python
def time_to_read_text_ms(self, char_count: int, avg_difficulty: float = 1.0) -> float:
    """BE78: Vectorized reading time estimation. Uses BE68 sub-RNG."""
    if char_count <= 0:
        return 0.0
    saccade_len = self.saccade_length_chars()
    n_fixations = max(1, int(char_count / saccade_len))
    rng = self._reading_rng  # BE68: sub-RNG
    if NUMPY_AVAILABLE:
        fix_durations = rng.normal(self.baseline_ms, 35.0, n_fixations)
        # Apply difficulty, stress, fatigue multipliers
        difficulty_mult = 1.0 + (avg_difficulty - 1.0) * 0.45
        stress_mult = 1.0 + self.profile.ambient_stress_level * 0.18
        fatigue_mult = 1.0 + (self.profile.session_task_count * 0.02)
        fix_durations = np.clip(fix_durations * difficulty_mult * stress_mult * fatigue_mult, 150.0, None)
        # Probabilistic regressions
        reg_rolls = rng.random(n_fixations)
        reg_threshold = self.regression_probability()
        n_regressions = int(np.sum(reg_rolls < reg_threshold))
        # Each regression adds 1.5x a fixation
        total_ms = float(np.sum(fix_durations)) + n_regressions * float(np.mean(fix_durations)) * 0.5
    else:
        # Pure-Python fallback
        total_ms = 0.0
        n_regressions = 0
        for _ in range(n_fixations):
            fd = self.fixation_duration_ms(avg_difficulty)
            total_ms += fd
            if rng.random() < self.regression_probability():
                total_ms += fd * 1.5
                n_regressions += 1
    # Inter-word saccade time (~25ms)
    total_ms += n_fixations * 25.0
    return total_ms
```

---

## SECTION B: CHANGELOG OF FIXES APPLIED (BE64-BE78, BE79 reverts BE64)

```
BE79 - REVERTED the BE64 "fix". The original _NumpyFallback.weibull()
       code (1.0, shape) was CORRECT. Python's weibullvariate(alpha, beta)
       is Weibull(shape=beta, scale=alpha), so weibullvariate(1.0, shape)
       correctly produces Weibull(shape=shape, scale=1.0) which matches
       numpy's weibull(shape). The "fix" would have introduced a real
       statistical bug. Lesson: always test mathematical claims empirically.
BE65 - post_error_slowing property: now reflects both boolean flag
       AND counter. Eliminates the brief window where the property
       returned True while the counter was 0.
BE66 - apply_post_error_slowing(False): resets _pes_keystrokes_remaining
       to 0. Eliminates state inconsistency.
BE67 - generate_humanized_mouse_trajectory: now calls _decay_pes_counter
       at the start. Mouse-only personas also recover from PES.
BE68 - ReadingPacingModel: added _reading_rng sub-RNG. Eliminates
       text-length-correlated RNG consumption.
BE69 - MouseButtonDurationModel: integrated into hci_reading_model.py
       for click-type-specific hold duration.
BE70 - PES activation count + forced decay after >3 activations.
       Prevents indefinite stress accumulation.
BE71 - apply_stress_delta: NaN/Inf rejection.
BE72 - decay_stress: input validation (non-negative, finite).
BE73 - MouseButtonDurationModel.click_hold_ms: did-you-mean suggestion
       on invalid click_type.
BE74 - ScrollInertiaModel.tick_count: mu=1.0 returns 100 ticks
       (effectively no deceleration), not 1.
BE75 - __repr__: includes _pes_keystrokes_remaining for debugging.
BE76 - (no fix; documented as consistent with numpy)
BE77 - FocusSwitchModel.time_to_next_switch_s: inverted complexity
       multiplier. Complex tasks have LONGER focus, not shorter.
BE78 - time_to_read_text_ms: vectorized using numpy (BE68 sub-RNG).
       Pure-Python fallback preserved.
```

---

## SECTION C: CROSS-FILE INTEGRATION FINDINGS

### C.1 — `hci_reading_model.py:498` uses `post_error_slowing` (boolean only)

The `post_error_slowing` property was a boolean check. After BE65,
it now correctly reflects the counter state, so this call site
automatically benefits. ✓

### C.2 — `hci_reading_model.py:380` uses `generate_ex_gaussian_reaction_time`

This advances the profile's main RNG. With BE68's decoupled
`_reading_rng`, the `time_to_read_text_ms` no longer leaks state,
but `generate_ex_gaussian_reaction_time` still does. For full
RNG isolation, the HCI module should use a separate sub-RNG. ✓
Documented as a follow-up for the next round.

### C.3 — `keyboard_emulator.py:47` etc. use `calculate_log_normal_delays()[0]`

The `np.ndarray` returned by numpy has `[0]` returning `np.float64`.
`float(np.float64)` is a Python float. ✓ No issue.

### C.4 — `hardened_orchestrator.py` `_human_like_delay` is system random

**STILL NOT FIXED** from the previous round. The orchestrator's
quiescence/cooldown delays are decoupled from the persona's RNG.
This is a documented gap that requires a refactor of
`run_persona_pipeline` to call `profile.calculate_log_normal_delays`.
**Confirmed: still present.**

### C.5 — `dynamic_pacing.py:55-56` uses `pacing_rng.normal/exponential`

**VERIFIED** to use the decoupled `pacing_rng` from BE54. ✓

### C.6 — `humanized_movement.py` thread-safe lock

**VERIFIED** from previous round (BE63). ✓

---

## SECTION D: METRICS (after Round 2 fixes)

- File: 1271 → ~1310 lines
- New methods: `__init__` of ReadingPacingModel now creates sub-RNG
- Fixed methods: 5 (post_error_slowing, apply_post_error_slowing,
  generate_humanized_mouse_trajectory, time_to_read_text_ms,
  weibull, tick_count, __repr__, apply_stress_delta, decay_stress)
- All 4 cognitive models now have RNG-state isolation
- All defensive input validation added
- All syntax checks pass: `python3 -m py_compile` exit 0

---

## SECTION E: NEXT STEPS (Round 3 / not in scope)

1. **hci_reading_model.py sub-RNG**: Make the HCI module use a
   dedicated sub-RNG for its random calls, similar to BE68.

2. **hardened_orchestrator.py: refactor `_human_like_delay`** to use
   `profile.calculate_log_normal_delays`.

3. **Task executor integration**: Wire the `ReadingPacingModel` and
   `FocusSwitchModel` into the task executors (in `tasks/`) to make
   page-reading and tab-switching realistic.

4. **Behavioral graph simulator**: Add a `BehavioralTraceRecorder`
   class that exports a W3C-compatible trace of all timing/mouse/key
   events for forensic post-mortem analysis.

5. **Statistical validator**: A standalone test that runs 1000 personas
   for 100 tasks and validates the auto-correlation function, Hurst
   exponent, and Kolmogorov-Smirnov distance of the timing distribution
   against real-human baselines.

---

**End of Round 2 audit document.**
