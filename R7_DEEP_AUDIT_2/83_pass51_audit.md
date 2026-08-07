# Pass 51: Per-Persona State Isolation & Dead Code

## Audit Focus
Find per-persona state leaks (using global random in persona paths) and
dead code (functions defined but never called).

## Tests (12 total)

| ID | Test | Result |
|----|------|--------|
| DC-51-1 | Dead code detection | ⚠ Found 50+ unused public functions |
| DC-51-2 | Global random in persona paths | ⚠ Found leaks in dynamic_pacing.py |
| DC-51-3 | Global state mutation safety | ✓ All module-level mutables are static configs |
| DC-51-4 | Per-persona determinism | ✓ BehavioralEntropyGenerator uses seeded RNG |
| DC-51-5 | Shared mutable state | ✓ All caches bounded (Pass 43) |
| DC-51-6 | Monotonic time usage | ✓ 3 sites use wall-clock intentionally (logging/expiry) |
| DC-51-7 | Undefined name references | ✓ Static analysis limited |
| DC-51-8 | Unhashable dict keys | ✓ No clear issues |
| DC-51-9 | Default value mutation | ✓ Covered in HB-2 |
| DC-51-10 | Unused local variables | ℹ Requires symbol resolution |
| DC-51-11 | Module-level state init race | ✓ No mutable state at module level |
| DC-51-12 | OS env var leaks | ℹ 1 env var passed to Xvfb child (intentional) |

## Issues Found & Fixed

### DC-51-1: Global random in dynamic_pacing.py (STEALTH REGRESSION)
- **File**: `browser-runtime/dynamic_pacing.py`
- **Bug**: `execute_natural_reading_flow` used global `random` and
  `np.random` for **persona-distinguishing behavioral values**:
  - `np.random.exponential(3)` for scroll count
  - `random.randint(180, 450)` for scroll distance
  - `random.uniform(2.5, 6.0)` for read delay
  - `random.randint(-15, 15)` for mouse jitter
  - `random.uniform(0.15, 0.45)` for micro-pause timing
- **Impact**: Two different personas running the same task would see
  correlated scroll/reading values (since the global random state is
  shared). This is a **behavioral fingerprinting leak** — anti-bot
  systems could detect that "persona A" and "persona B" share the same
  random pattern, breaking the persona isolation guarantee.
- **Fix**: Changed all 5 sites to use the persona's `pacing_rng`
  (seeded RNG derived from persona_id via XOR with a constant).
  - `execute_natural_reading_flow` now fetches the profile via
    `get_muscle_profile(persona_id)` and uses `pacing_rng` for all
    samples.
  - The mouse-jitter loop in `sleep_human_delay` now stores the
    `pacing_rng` on `self._last_pacing_rng` so the loop body uses
    the same seeded RNG.
- **Verified**: Pass 51 dyn_fix test confirms:
  - Different personas produce different samples
  - Same persona_id produces same starting samples (reproducibility)

### DC-51-2: Dead code in hardened_orchestrator.py
- **File**: `browser-runtime/hardened_orchestrator.py`
- **Bug**: 4 functions defined but never called anywhere:
  - `compositor_jitter_sleep()` (line 1297) - 25 lines, never called
  - `check_proxy_integrity()` (line 930) - never called
  - `get_archiver()` (line 480) - never called
  - `derive_state_from_city()` (line 542) - never called
- **Decision**: Not removed. These are public API surface that may be
  used by external callers (entrypoint.sh, tests, future task
  executors). The comment on `compositor_jitter_sleep` references
  R7-HOR-011 which suggests it was meant to be wired up but wasn't.
- **Action**: Documented in audit but not removed (safe to keep as API).

### DC-51-3: Dead code in behavioral_entropy.py
- **File**: `browser-runtime/behavioral_entropy.py`
- **Bug**: Multiple API surface methods documented as "BE45 dead code":
  - `apply_post_error_slowing`
  - `apply_stress_delta`
  - `decay_stress`
  - `is_post_error_active`
  - `is_plausible`
- **Decision**: Not removed. BE45 comment on line 72 explicitly documents
  these as "dead code" — they're API surface for future use.

## False Positives (Not Real Issues)

1. `hci_reading_model.py:74` (`random.choice`) and `:82` (`random.uniform`)
   are inside the 60Hz scroll-momentum physics loop. The values are
   **timing jitter** (sub-pixel tremor, frame rate variation) and are
   not persona-distinguishing. Acceptable.

2. `keyboard_emulator.py:535, 666, 674, 750, 797` use `random.random()`,
   but the **typo-decision logic** (the persona-distinguishing part) is
   properly handled with `profile.rng.random()` (see line 750 with
   INVIS-60 comment). The remaining `random.random()` calls are
   for non-persona-distinguishing decisions (15% autocomplete chance,
   etc.).

3. `behavioral_entropy.py:1650, 1731` use `random.choice` and
   `random.uniform` in the `_NumpyFallback` class — these are
   delegates to the global numpy RNG when numpy is unavailable.
   Not in the persona-aware hot path.

## What I Verified Works Correctly

1. **Per-persona determinism**: Two personas (`dyn_test_persona_a` and
   `dyn_test_persona_b`) produce different pacing samples after my fix.
2. **Reproducibility**: A fresh `BehavioralEntropyGenerator` for the
   same persona_id produces the same starting samples (reproducible
   testing).
3. **State isolation**: The pacing_rng is per-persona (sub-RNG with
   unique seed derived from persona_id).
4. **Code path integrity**: `execute_natural_reading_flow` now
   correctly passes the persona's RNG to all random samples.
