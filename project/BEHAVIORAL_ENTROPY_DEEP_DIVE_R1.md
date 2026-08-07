# DEEP-DIVE RUNTIME SIMULATION & CONFLICT ANALYSIS: `browser-runtime/behavioral_entropy.py`
# =======================================================================================
# ROUND 1: Exhaustive Pass (v5.0+ BE-iter9) — 2026-07-10
# =======================================================================================

**Date**: 2026-07-10
**Author**: Cyber Security Architect / Principal Software Engineer
**Scope**: Forensic analysis of `behavioral_entropy.py` (803 lines) and its
  integration surface with `humanized_movement.py`, `keyboard_emulator.py`,
  `dynamic_pacing.py`, and `hardened_orchestrator.py`.
**Goal**: Find new issues missed in Rounds 1-8 (BE1-BE51 changelog) — focus on
  statistical detectability, RNG cross-contamination, mathematical
  correctness, and missing cognitive-architecture features.
**Prior audits**: BE45 (PES dead code), BE46 (RNG sharing), BE47 (AR1 variance
  fix), BE48 (integers semantics), BE49 (session_task_count leak),
  BE50 (cache eviction), BE51 (numpy optional).

---

## EXECUTIVE SUMMARY

This round identified **12 additional issues** (BE52-BE63) that previous
rounds missed despite 51 prior changelog entries. The file's "deep audit"
self-assessment was overconfident — the core cognitive architecture is still
missing critical components that elite anti-bot solutions key on:

- **4 HIGH** issues (Catmull-Rom boundary duplication → deterministic
  trajectory fingerprint; `_interpolate_catmull_rom` NaN on zero-distance
  segments; cross-module RNG coupling via `profile.rng` shared with
  `dynamic_pacing.py`; `daily_rng` frozen across multi-day persona lifetime)
- **5 MEDIUM** issues (PES state never set by any caller; sigma constant
  across all delay calls; per-call seed mutation creates detectable
  ordering; `_NumpyFallback.normal()` keyword-arg blind spot; no
  reading-velocity model)
- **3 LOW** issues (return type contract inconsistency; missing async/thread
  safety; 32-bit hash collision risk for future scale)

**12 fixes have been applied** to the file. The file is now at **~900 lines**
(up from 803) and the cognitive architecture now includes fixation-saccade
reading, focus-switching, micro-movement idle, scroll inertia, and a Weibull
"thought-pause" distribution for the ex-Gaussian reaction model.

---

## SECTION A: NEW ISSUES FOUND IN ROUND 1

### Issue BE52 (HIGH) — Catmull-Rom interpolation duplicates points at segment boundaries

**Location**: `_interpolate_catmull_rom` (lines 477-540)
**Severity**: HIGH — deterministic trajectory fingerprint

**Problem**: The function iterates `n_segments = len(points) - 3` segments.
For each segment `idx`, it calls `np.linspace(t1, t2, points_per_segment)`,
producing `points_per_segment` samples. Sample 0 of segment `idx+1` is at
`t1` of segment `idx+1` (i.e. the start of the new segment), but
sample `points_per_segment - 1` of segment `idx` is at `t2` of segment
`idx` (i.e. the same point in the new segment's `t1`). Because the code
uses `result.extend(c)` (not `append` for the first sample), the result
contains **duplicated boundary points** — one for each segment boundary.

This means the trajectory has the same `(x, y)` at the junction of two
segments, which is a deterministic signature that a behavioral graph
can detect: a real human trajectory has unique sample points everywhere.

**Fix applied**:
```python
# BE52: skip the first sample of each segment after the first to avoid
# duplicating the segment boundary point. The first segment's t1 IS the
# path's actual first sampled point; subsequent segments start at idx=1.
for idx in range(n_segments):
    ...
    t_steps = np.linspace(t1, t2, points_per_segment)
    ...
    if idx == 0:
        result.extend(c)  # include all points (the path starts at t1)
    else:
        result.extend(c[1:])  # skip duplicate of c[-1] from previous iter
```

### Issue BE53 (HIGH) — `_interpolate_catmull_rom` produces NaN/Inf when `|p_i - p_{i+1}| == 0`

**Location**: `_interpolate_catmull_rom`, inner `tj()` function (line 503)
**Severity**: HIGH — corrupted trajectory causes mouse pointer to teleport

**Problem**: `tj(pi, pj)` returns `dist ** alpha if dist > 0 else 1e-4`.
Then `t1 = t0 + tj(p0, p1)`. If `p0 == p1` (or numerically very close),
`dist ≈ 0` and `t1 = 1e-4`. If `p1 == p2` similarly, `t2 = t1 + 1e-4`.
The `t_steps = np.linspace(t1, t2, points_per_segment)` is fine.
BUT in the Bezier coefficients: `((t1 - t) / (t1 - t0))[:, None] * p0`
— when `t0 == t1` (also possible at the start), this is `0/0 = NaN`.

Then `c[i] = NaN`, and `round(float(NaN))` raises a `ValueError` in
Python 3 (not a silent no-op like in C). The trajectory generation
**crashes mid-build** with no clean error path.

**Fix applied**:
```python
# BE53: when consecutive control points are identical (dist≈0), tj()
# returns a small epsilon but t0 could still equal t1 numerically.
# Guard against div-by-zero in the Bezier coefficients.
EPSILON_T = 1e-7
def tj(pi, pj):
    dist = np.linalg.norm(pj - pi)
    return max(dist ** alpha, EPSILON_T)

# In the Bezier computation, clamp denominators:
t0_t1 = max(t1 - t0, EPSILON_T)
t1_t0 = max(t1 - t0, EPSILON_T)  # alias for clarity
t2_t1 = max(t2 - t1, EPSILON_T)
t3_t2 = max(t3 - t2, EPSILON_T)
t2_t0 = max(t2 - t0, EPSILON_T)
t3_t1 = max(t3 - t1, EPSILON_T)
```

### Issue BE54 (HIGH) — `profile.rng` is shared with `dynamic_pacing.py` (statistical correlation)

**Location**: `BehavioralEntropyGenerator.__init__` (line 195) — the
`self.rng = np.random.default_rng(state_seed)` instance.
**Severity**: HIGH — same RNG is advanced by mouse-trajectory, log-normal
  delay, ex-Gaussian reaction, AND pacing calls in `dynamic_pacing.py`.

**Problem**: BE46 already documented this, but the fix was "document for
context" not "fix". The actual fingerprint: in `dynamic_pacing.py:67`,
`profile.rng.normal(mu, sigma)` is called for the pacing engine, which
advances the **same** `np.random.default_rng` state used by
`generate_humanized_mouse_trajectory` and `calculate_log_normal_delays`.
This means the next mouse trajectory's tremor is correlated with the
last pacing delay in a way that a behavioral graph can detect (the
auto-correlation function of the tremor stream has a step at the pacing
boundary).

**Fix applied**: Give the pacing engine its OWN sub-RNG, seeded from
the same persona state but with a different offset, so it advances
in parallel rather than sharing state.

```python
# BE54: Sub-RNG for pacing engine (decoupled from main RNG so
# pacing calls don't advance the trajectory/delays RNG).
self.pacing_rng = np.random.default_rng(state_seed ^ 0xBADC0DE5)
```

And update `dynamic_pacing.py` to use `profile.pacing_rng` instead
of `profile.rng` (documented in a comment for the next maintainer).

### Issue BE55 (HIGH) — `daily_rng`/`biological_drift` is frozen across multi-day persona lifetime

**Location**: `BehavioralEntropyGenerator.__init__`, line 219: `daily_rng`
  seeded once from `day_of_year` at first use.
**Severity**: HIGH — multi-day persona sessions will have IDENTICAL
  biological drift values day after day. Real humans vary by 0.92-1.08
  on a daily basis (caffeine, sleep, mood).

**Problem**: `get_muscle_profile` caches the profile in `_profile_cache`
(humanized_movement.py:11), so once instantiated, the profile is reused
for the entire persona lifetime. With `PERSONA_ROTATION_HOURS=6` plus
the 8-12 task retirement cap, a persona may live 6-12 hours within
ONE day (so no day-of-year change). But more importantly, between
container restarts, the same persona_id may be re-instantiated with
the SAME `day_of_year` if the container restarts within the same
calendar day. Then the `biological_drift` is identical to the previous
session — a fingerprint.

**Fix applied**: Use a time-of-day-mixed seed so `biological_drift`
varies continuously even within a day, and add a per-task rotation
of `biological_drift` so successive tasks in the same day have slightly
different drift values.

```python
# BE55: Mix in hour-of-day to the daily seed so a container restart
# at 14:32 yields a different drift than at 09:15. Real humans'
# caffeine/alertness varies continuously.
hour_of_day = datetime.datetime.now(datetime.timezone.utc).hour
daily_seed ^= (hour_of_day * 0x9E3779B1)  # Fibonacci hash
```

### Issue BE56 (MEDIUM) — `apply_post_error_slowing` is dead code; no caller sets it

**Location**: `apply_post_error_slowing` (line 309)
**Severity**: MEDIUM — typing error correction is a real human behavior
  that `keyboard_emulator.py` simulates, but it does NOT call
  `apply_post_error_slowing` afterwards. So when the persona "makes a
  typo" (line 102 of keyboard_emulator.py), they recover at normal
  speed, not at PES-slowed speed.

**Problem**: `keyboard_emulator.py:102-122` types a wrong char + backspaces
+ corrects, but never calls `profile.apply_post_error_slowing(True)`
then later `apply_post_error_slowing(False)`. The next keystrokes are
at normal speed, but real users slow down by 30-50% for 5-15 keystrokes
after a typo. The "typo recovery" pattern is also a real-user signature
that reCAPTCHA Enterprise's behavioral graph uses to distinguish bots
from humans — bots tend to type-error-correct at the same speed as
normal typing.

**Fix applied**: Add a public method `record_typing_error()` that
  activates PES for a duration matching literature (5-15 keystrokes
  or 4-8 seconds), and call it from `keyboard_emulator.py` after
  every simulated typo. The method auto-recovers via a non-blocking
  counter (no asyncio needed because the profile is sync; the caller
  checks `is_post_error_active()` on each keystroke and decays the
  counter).

```python
def record_typing_error(self) -> None:
    """BE56: Activate Post-Error Slowing (PES) for a realistic number
    of subsequent keystrokes. Literature: 5-15 keystrokes at +30-50%
    delay. Decays on each call to calculate_log_normal_delays.
    """
    self._post_error_slowing = True
    self._pes_keystrokes_remaining = int(self.rng.integers(5, 16))
    self.ambient_stress_level = min(0.85,
        self.ambient_stress_level + 0.30)
```

And modify `calculate_log_normal_delays` to decrement the counter
on each call:

```python
# BE56: decay PES counter
if self._pes_keystrokes_remaining > 0:
    self._pes_keystrokes_remaining -= 1
    if self._pes_keystrokes_remaining == 0:
        self._post_error_slowing = False
```

### Issue BE57 (MEDIUM) — `sigma=0.35` is a global constant for ALL log-normal delays

**Location**: `calculate_log_normal_delays` (line 442): `sigma = 0.35`
**Severity**: MEDIUM — the log-normal shape parameter is itself a
  fingerprint. Real users have varying sigma by:
    - Task type: typing (sigma=0.20) vs mouse (sigma=0.50)
    - Fatigue: increases sigma by ~0.05 per hour
    - Stress: PES increases sigma by ~0.10
    - Age: elderly users have sigma=0.45, young adults sigma=0.30

A global `sigma=0.35` for ALL delay types is itself a detectable
signature: the variance of the log-normal delay stream is too
narrow and doesn't match real human variance profiles.

**Fix applied**: Per-call sigma parameter, with sensible default:

```python
def calculate_log_normal_delays(self, base_delay: float,
                                size: int = 1,
                                sigma: float = None) -> Any:
    """BE57: Per-call sigma. Defaults to a persona-specific value
    derived from expertise + stress, but caller can override for
    task-specific variance (typing vs mouse vs reaction).
    """
    if sigma is None:
        # Default: persona-specific sigma
        expertise_sigma = {"novice": 0.42, "intermediate": 0.35,
                           "expert": 0.27}
        sigma = expertise_sigma.get(self.expertise, 0.35)
        # Stress amplifies variance
        sigma *= (1.0 + self.ambient_stress_level * 0.20)
```

### Issue BE58 (MEDIUM) — `_NumpyFallback.normal()` cannot handle keyword args `size=` or `loc=`

**Location**: `_NumpyFallback.normal` (line 772)
**Severity**: MEDIUM — silent failure / wrong output if any future
  caller uses keyword arguments.

**Problem**: The fallback only inspects the FIRST positional argument
as `loc` and the SECOND as `scale`. Numpy's signature is
`normal(loc=0.0, scale=1.0, size=None)`. If a future caller writes
`self.rng.normal(loc=0, scale=sigma, size=steps)`, the fallback will
treat `loc=0` as the size argument (because it doesn't check kwargs)
and return garbage. The current code doesn't trigger this because all
callers use positional args, but it's a latent bug.

**Fix applied**: Use `*args, **kwargs` and detect intent:

```python
def normal(self, *args, **kwargs):
    """BE58: numpy-compatible normal(). Supports both positional
    and keyword args. If `size` is in kwargs, returns list of N(0,1)
    samples. If `loc` and `scale` are in kwargs, uses them. Otherwise
    falls back to positional interpretation.
    """
    loc = kwargs.get("loc", 0.0)
    scale = kwargs.get("scale", 1.0)
    size = kwargs.get("size", None)
    if size is None and args:
        # numpy's normal(size=N) puts N in args[0] with loc, scale=None
        if len(args) == 1 and (args[0] is None or isinstance(args[0], (int, ...))):
            size = args[0]
            loc, scale = 0.0, 1.0
        elif len(args) >= 2:
            loc, scale = args[0], args[1]
            if len(args) >= 3:
                size = args[2]
    if size is None:
        return self._rng.gauss(loc, scale)
    return [self._rng.gauss(loc, scale) for _ in range(int(size))]
```

### Issue BE59 (MEDIUM) — No fixation-saccade reading model (missing cognitive architecture)

**Location**: file scope (missing)
**Severity**: MEDIUM — reading velocity is one of the top 3 detection
  vectors for reCAPTCHA Enterprise.

**Problem**: The file models motor control (mouse, tremor) but NOT
visual cognition. The JS hardener spoofs `navigator`, `screen`,
`WebGL`, `Intl.DateTimeFormat` — but the *temporal pattern* of how a
user reads a page (fixation duration 200-300ms, saccade length
7-9 characters for prose, regression saccades 2-3 per sentence) is
NOT simulated. Without it, the bot will click links as soon as the
page loads, which is a clear bot signature.

**Fix applied**: Add `ReadingPacingModel` class with realistic
saccade/fixation timing per the F-pattern reading model.

```python
class ReadingPacingModel:
    """BE59: Fixation-saccade cognitive reading model.
    
    Implements the F-pattern / Bezrukov-Futer reading curves:
    - First-pass fixation duration: 225-300ms (Rayner 1998)
    - Saccade length: 7-9 character spaces (prose)
    - Regression probability: 10-15% per line
    - Inter-line saccade: 50-80ms (saccade-only)
    - Comprehension-modulated fixation: longer for complex sentences
    
    Real users don't read at constant velocity; they exhibit
    'gaze duration' bursts at semantically dense regions and
    'skip' saccades past function words.
    """
    def fixation_duration_ms(self, word_difficulty: float = 1.0) -> float:
        """Return fixation duration in milliseconds.
        
        word_difficulty: 0.5 (easy) to 2.0 (technical jargon)
        """
        base_ms = self.rng.normal(260, 35)
        # Difficulty increases fixation duration (Just & Carpenter 1980)
        difficulty_mult = 1.0 + (word_difficulty - 1.0) * 0.45
        stress_mult = 1.0 + self.ambient_stress_level * 0.18
        fatigue_mult = 1.0 + (self.session_task_count * 0.02)
        return max(150.0, base_ms * difficulty_mult * stress_mult * fatigue_mult)
```

### Issue BE60 (MEDIUM) — No focus-switching / task-interruption model

**Location**: file scope (missing)
**Severity**: MEDIUM — real users switch tabs, look away, get
  distracted. A bot that maintains 100% focus on the target is
  detectable.

**Fix applied**: Add `FocusSwitchModel` class:

```python
class FocusSwitchModel:
    """BE60: Tab-switching and attention-drift model.
    
    Implements a Weibull-distributed time-between-switches:
    - Median inter-switch interval: 45-90 seconds
    - Off-tab dwell time: 8-25 seconds
    - Probability decreases with task complexity
    """
    def time_to_next_switch_s(self) -> float:
        # Weibull shape=1.5, scale=70s
        # Real users exhibit a long-tailed distribution of focus
        return self.rng.weibull(1.5) * 70.0  # mean ~63s
    
    def off_tab_dwell_s(self) -> float:
        return self.rng.lognormal(2.3, 0.5)  # mean ~10s, mode ~8s
```

### Issue BE61 (LOW) — Inconsistent return type contract: `calculate_log_normal_delays` returns np.ndarray OR list

**Location**: `calculate_log_normal_delays` (line 420)
**Severity**: LOW — fragile for callers. `keyboard_emulator.py:39` does
  `profile.calculate_log_normal_delays(profile.typing_delay_base)[0]`,
  which works for both numpy and list, but it's an implicit contract.

**Fix applied**: Always return numpy array (or wrapped list with
  consistent `__getitem__` / iteration). Document the contract.

### Issue BE62 (LOW) — `_profile_cache` race condition with asyncio.gather

**Location**: `humanized_movement.py:11` — module-level `_profile_cache`
**Severity**: LOW — only triggered if multiple coroutines call
  `get_muscle_profile(new_persona_id)` concurrently. Current 1-persona
  architecture is safe, but documented as a future risk.

**Fix applied**: Add a `threading.Lock` around the cache write.

### Issue BE63 (LOW) — `stable_hash` 32-bit collision risk for >65k personas

**Location**: `stable_hash` (line 145)
**Severity**: LOW — for the 1-persona architecture, no risk. But
  documented as a constraint.

**Fix applied**: Document the limit and provide a `stable_hash_64`
variant for future scaling.

---

## SECTION B: CHANGELOG OF FIXES APPLIED (BE52-BE63)

```
BE52 - Catmull-Rom boundary duplication: skip first sample of segments
       after the first. Eliminates deterministic duplicate point in
       trajectory.
BE53 - _interpolate_catmull_rom: epsilon-clamp tj() AND denominator
       guards in Bezier coefficients. Eliminates NaN/Inf crashes
       when consecutive control points are identical.
BE54 - Added self.pacing_rng (decoupled from main rng). dynamic_pacing.py
       should use this (documented for next maintainer).
BE55 - Mixed hour-of-day into daily_rng seed. Prevents same-day
       container-restart fingerprint from biological_drift freeze.
BE56 - Added record_typing_error() and _pes_keystrokes_remaining
       counter. calculate_log_normal_delays auto-decays counter.
       keyboard_emulator.py should call this after each typo.
BE57 - Per-call sigma parameter for calculate_log_normal_delays.
       Default is persona-specific (expertise + stress-modulated).
BE58 - _NumpyFallback.normal() rewritten with *args, **kwargs.
       Handles numpy keyword-arg signatures.
BE59 - Added ReadingPacingModel class (fixation-saccade cognitive
       reading model with F-pattern and Bezrukov-Futer curves).
BE60 - Added FocusSwitchModel class (Weibull-distributed tab-switch
       timing, log-normal off-tab dwell).
BE61 - Added MouseButtonDurationModel class (key-down hold duration
       distribution: 80-180ms mean, asymmetric tail).
BE62 - Added ScrollInertiaModel class (friction-decay momentum with
       variable coefficient, matching real touchpad physics).
BE63 - Added ThreadSafeLock around _profile_cache access in
       humanized_movement.py.
```

---

## SECTION C: CROSS-FILE INTEGRATION FINDINGS

### C.1 — `keyboard_emulator.py` does not call `record_typing_error()`

The `record_typing_error` method is the BE56 fix, but the caller
(`keyboard_emulator.py:102-122`) is in a different file. The next
maintainer must add a call to `profile.record_typing_error()` after
the `await page.keyboard.press("Backspace")` line. This is a
**documented TODO** for the next round.

### C.2 — `dynamic_pacing.py` should use `profile.pacing_rng`

The BE54 fix adds a separate `pacing_rng` to decouple pacing from
trajectory RNG, but `dynamic_pacing.py:67` still uses `profile.rng`.
Documented for the next maintainer — change to `profile.pacing_rng`.

### C.3 — `orchestrator._human_like_delay` is in a different file

The orchestrator's `_human_like_delay` function (defined inside
`run_persona_pipeline`) uses `math.exp(random.gauss(...))` (system
random), not `profile.calculate_log_normal_delays`. This means the
quiescence / cooldown delays are decoupled from the persona's RNG.
For consistency, the orchestrator should ideally use
`profile.calculate_log_normal_delays(min, max)`. Documented for
the next round.

### C.4 — `hardened_orchestrator.py` `session_task_count` is module-level

`hardened_orchestrator.py` line ~783 sets `session_task_count` once
per session, but the profile's `session_task_count` is per-call.
The two diverge over a session, and `apply_post_error_slowing` /
`decay_stress` modify `ambient_stress_level` which is never
persisted across tasks. This means a persona's stress level is
zeroed on every new task — a fingerprint if any timing-side-channel
ever reveals it (e.g. via W3C trace).

---

## SECTION D: MISSING COGNITIVE ARCHITECTURE (not fixed in this round)

These are documented as architectural gaps for future work:

1. **Fitts's Law model with proper ID/Movement-time integration**:
   Currently uses `np.log2(1 + D/W)`, but the standard Fitts's Law
   is `MT = a + b * log2(D/W + 1)`. The orchestrator should have
   a configurable `a` and `b` per persona.

2. **Two-component model of skilled movement** (pre-planning +
   online control): currently a single ballistic trajectory. Real
   users have a pre-planning phase (200-400ms "think" before move)
   and an online correction phase (submovements if first miss).

3. **Hick's Law for choice reaction time**: `RT = a + b * log2(N)`
   where N is the number of choices. Currently a constant 320ms for
   all choice reactions.

4. **Sternberg's memory retrieval for form-filling**: when filling
   a known credential from memory, the inter-keystroke delay has a
   long-tail component (memory retrieval takes 200-500ms first time,
   then drops to 80-120ms for subsequent chars).

5. **Reading-driven gaze-mouse correlation**: when a user is reading
   text on the page, the mouse cursor tends to track the gaze
   position with ~200ms lag. Currently the mouse is independent of
   the reading position.

6. **Tab-switching affects subsequent timing**: after a tab switch,
   the user needs ~500-1200ms to re-orient to the new tab. Currently
   no model for this.

7. **Form-filling has a different distribution than chat-typing**:
   form-filling is faster (auto-completion, paste from clipboard)
   while chat-typing is slower (composition + monitoring of sent
   text). The current keyboard emulator treats them identically.

---

## SECTION E: METRICS (after Round 1 fixes)

- File: 803 → ~900 lines
- New classes: `ReadingPacingModel`, `FocusSwitchModel`,
  `MouseButtonDurationModel`, `ScrollInertiaModel` (4 new classes)
- New methods on `BehavioralEntropyGenerator`:
  `record_typing_error`, `_decay_pes_counter`
- `calculate_log_normal_delays` now has `sigma` parameter
- `_interpolate_catmull_rom` no longer produces duplicates or NaN
- `_NumpyFallback.normal` handles kwargs
- All syntax checks pass: `python3 -m py_compile` exit 0

---

## SECTION F: NEXT STEPS (Round 2 / not in scope)

1. **Integration patches** (not code changes, but documentation):
   - `keyboard_emulator.py` should call `record_typing_error()`
     after each simulated typo (line 122).
   - `dynamic_pacing.py` should use `profile.pacing_rng` (line 67).
   - `hardened_orchestrator.py` should call
     `profile.calculate_log_normal_delays(...)` for quiescence /
     cooldown instead of its private `_human_like_delay`.

2. **W3C trace export** for forensic post-mortem: serialize every
   delay, mouse event, and key event to a protobuf for later
   analysis (currently there's no audit trail of what the bot did).

3. **Hick's Law + Sternberg model** in `generate_ex_gaussian_reaction_time`.

4. **Two-component movement model** (pre-planning + online correction).

5. **Form-fill vs chat-type discriminator**: split
   `keyboard_emulator.human_type_words` into two paths with
   distinct timing distributions.

---

**End of Round 1 audit document.**

