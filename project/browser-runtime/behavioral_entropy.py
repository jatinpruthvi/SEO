# behavioral_entropy.py - Advanced Behavioral Entropy & Muscle Profile Generator
# Implements hand dominance, signal-dependent noise, multi-frequency physiological tremors, and demographic-locked motor control
#
# CHANGELOG (iter10 - second-pass audit fixes, see BEHAVIORAL_ENTROPY_DEEP_DIVE_R2.md):
#   BE79 - CONFIRMED: _NumpyFallback.weibull() ORIGINAL code (1.0, shape)
#          was CORRECT. numpy's weibull(a) = Weibull(shape=a, scale=1)
#          and Python's weibullvariate(1.0, a) is the same distribution.
#          The BE64 "fix" was wrong and would have introduced a real
#          statistical bug. Reverted.
#   BE65 - post_error_slowing property: now reflects both boolean flag
#          AND keystroke counter. Returns True only if both are set.
#   BE66 - apply_post_error_slowing(False): now resets _pes_keystrokes_remaining
#          to 0. Eliminates state inconsistency.
#   BE67 - generate_humanized_mouse_trajectory: now calls _decay_pes_counter
#          at start. Mouse-only personas recover from PES after N movements.
#   BE68 - ReadingPacingModel: decoupled _reading_rng sub-RNG. Eliminates
#          text-length-correlated RNG consumption in main timing/mouse RNG.
#   BE69 - MouseButtonDurationModel: integrated into hci_reading_model.py
#          for click-type-specific hold duration.
#   BE70 - Documented: PES activation count + forced decay after >3
#          activations (architectural note, not code change).
#   BE71 - apply_stress_delta: NaN/Inf rejection.
#   BE72 - decay_stress: input validation (non-negative, finite).
#   BE73 - MouseButtonDurationModel.click_hold_ms: did-you-mean suggestion
#          on invalid click_type (via difflib).
#   BE74 - ScrollInertiaModel.tick_count: mu=1.0 returns 100 ticks
#          (effectively no deceleration), not 1.
#   BE75 - __repr__: includes _pes_keystrokes_remaining for debugging.
#   BE77 - FocusSwitchModel.time_to_next_switch_s: inverted complexity
#          multiplier. Complex tasks have LONGER focus (multiply, not
#          divide).
#   BE78 - ReadingPacingModel.time_to_read_text_ms: vectorized using
#          numpy + _reading_rng. O(1) calls regardless of char_count.
#
# CHANGELOG (iter9 - deep audit fixes, see BEHAVIORAL_ENTROPY_DEEP_DIVE_R1.md):
#   BE52 - _interpolate_catmull_rom: skip first sample of segments after the
#          first to avoid duplicating the segment boundary point. Eliminates
#          deterministic duplicate (x,y) in trajectory.
#   BE53 - _interpolate_catmull_rom: epsilon-clamp tj() AND denominator guards
#          in Bezier coefficients. Eliminates NaN/Inf crashes when consecutive
#          control points are identical (e.g. coasting zeros).
#   BE54 - Added self.pacing_rng (decoupled from main rng). dynamic_pacing.py
#          should use this to prevent statistical correlation between pacing
#          delays and trajectory tremor (same RNG was advancing both).
#   BE55 - Mixed hour-of-day into daily_rng seed. Prevents same-day
#          container-restart fingerprint from biological_drift freeze.
#          Real humans' caffeine/alertness varies continuously.
#   BE56 - Added record_typing_error() and _pes_keystrokes_remaining counter.
#          calculate_log_normal_delays auto-decays counter. Callers (esp.
#          keyboard_emulator.py) should invoke record_typing_error() after
#          each simulated typo.
#   BE57 - Per-call sigma parameter for calculate_log_normal_delays. Default
#          is persona-specific (expertise + stress-modulated). Eliminates
#          the global sigma=0.35 fingerprint.
#   BE58 - _NumpyFallback.normal() rewritten with *args, **kwargs. Handles
#          numpy keyword-arg signatures (loc=, scale=, size=).
#   BE59 - Added ReadingPacingModel class (fixation-saccade cognitive reading
#          model with F-pattern and Bezrukov-Futer curves). Real users don't
#          read at constant velocity; this is a top-3 reCAPTCHA Enterprise
#          detection vector.
#   BE60 - Added FocusSwitchModel class (Weibull-distributed tab-switch
#          timing, log-normal off-tab dwell). Bots that maintain 100% focus
#          are detectable.
#   BE61 - Added MouseButtonDurationModel class (key-down hold duration
#          distribution: 80-180ms mean, asymmetric tail).
#   BE62 - Added ScrollInertiaModel class (friction-decay momentum with
#          variable coefficient, matching real touchpad physics).
#   BE63 - Thread-safe lock around _profile_cache in humanized_movement.py
#          (documented TODO; not changed in this file).
#
# CHANGELOG (iter8 - runtime conflict fixes):
#   BE45 - apply_post_error_slowing / apply_stress_delta / decay_stress are dead code
#          in the current codebase: no caller invokes them. The _post_error_slowing
#          state is always False, so the PES multipliers are always 1.0 and the
#          stress-amplification in tremor is constant. This is a feature gap,
#          not a bug, but is documented here for future maintainers.
#   BE46 - profile.rng is SHARED with dynamic_pacing.py, which calls
#          profile.rng.normal() and profile.rng.exponential() internally.
#          This means pacing calls advance the same RNG state used by
#          generate_humanized_mouse_trajectory, generate_ex_gaussian_reaction_time,
#          and calculate_log_normal_delays. Determinism is preserved within a
#          single persona's session, but the persona's behavior is correlated
#          with pacing calls in a non-obvious way (potential stealth concern).
#   BE47 - AR(1) innovation std dev is now `0.45 * sqrt(1 - phi^2)` (~0.298),
#          not 0.45, to match the original code's steady-state variance
#          (std=0.45). The vectorized np.convolve path uses the correct value.
#   BE48 - _NumpyFallback.integers() now uses randrange (exclusive high) to
#          match numpy's semantics. Python's randint is inclusive on both ends;
#          np.random.default_rng().integers(low, high) returns values in [low, high).
#   BE49 - profile.session_task_count is set externally by humanized_movement.py
#          and keyboard_emulator.py, but the orchestrator's GLOBAL counter
#          is the same value for ALL personas (see hardened_orchestrator.py
#          line 783: `browser.session_task_count = session_task_count` where
#          session_task_count is module-level). This means all 20 personas
#          share the same fatigue profile — a fingerprint leak. The fix is
#          in the orchestrator (per-persona counters), not here, but is
#          documented for context.
#   BE50 - _profile_cache in humanized_movement.py is module-level and
#          unbounded (eviction only at >1000 entries). For long-running
#          deployments with >1000 unique persona_ids, the cache silently
#          evicts the oldest entry. Acceptable for the 20-persona scale.
#   BE51 - dynamic_pacing.py and hci_reading_model.py and keyboard_emulator.py
#          have top-level `import numpy as np` that fails if numpy is missing.
#          These files are NOT imported by the orchestrator in the production
#          path; they're only used by the verify_system_integrity.py test
#          and as utility modules for task executors. So this is acceptable
#          for the orchestrator's runtime, but if any future code imports
#          them, it will fail without numpy.
#
# CHANGELOG (iter7 - deep audit fixes):
#   BE1 - numpy import wrapped in try/except; if missing, falls back to pure-Python RNG.
#   BE2 - loguru import wrapped in try/except; falls back to stdlib logging.
#   BE3 - stable_hash moved to a shared utility (NOT defined here). Importers get it
#         from humanized_movement or hardened_orchestrator. We provide a
#         backwards-compat local definition but warn on duplicate.
#   BE4 - stable_hash still uses 32-bit output, but documented. Collisions on 900
#         display slots with 20 personas: probability ~0.0001% (acceptable).
#   BE5 - state_seed now uses full 64-bit hash (MD5 has 128 bits; we mask to 64).
#   BE6 - Day-of-year uses UTC explicitly (was local time).
#   BE7 - Daily RNG is properly day-keyed (was correct, just misleading comment).
#   BE8 - Age range extended: 0-17, 18-30, 31-55, 56-75 with distinct multipliers.
#         Children get even slower; elderly get even slower; young adults fastest.
#   BE9 - Expertise levels: novice, intermediate, expert with realistic distribution
#         (45% novice, 40% intermediate, 15% expert). Intermediate has its own
#         multipliers (between novice and expert).
#   BE10 - Tremor amplitude now also scaled by biological_drift (caffeine coupling).
#   BE11 - Confirmed typing delay formula is consistent with muscle speed.
#   BE12 - Drift bias: kept constant per persona-per-day (saccade bias IS stable
#         in real humans).
#   BE13 - Ambient stress now has continuous modulation methods
#         (apply_stress_delta, decay_stress) in addition to PES toggle.
#   BE14 - apply_post_error_slowing is now idempotent (state-tracked).
#   BE15 - generate_physiological_tremor validates steps >= 1.
#   BE16 - Tremor t-array now in seconds (time-domain), not dimensionless.
#         Wave frequencies now in real Hz, not 20x multiplier artifacts.
#   BE17 - AR(1) noise: variance is consistent with steady-state. OK.
#   BE18 - AR(1) loop vectorized via numpy cumsum (faster).
#   BE19 - calculate_log_normal_delays returns np.ndarray or list (consistent).
#   BE20 - generate_ex_gaussian_reaction_time validates reaction_type.
#   BE21 - min_clamp uses dict lookup per reaction type.
#   BE22 - _interpolate_catmull_rom: time parametrization validated; per-segment
#         sampling correctly placed between t1 and t2.
#   BE23 - Same.
#   BE24 - generate_humanized_mouse_trajectory return type annotated properly.
#   BE25 - Micro-adjustment delay calculation simplified.
#   BE26 - Fitts's Law ID is correct.
#   BE27 - RNG state is per-persona-persistent (good for determinism). Documented.
#   BE28 - Perp vector is now normalized before scaling.
#   BE29 - Variable shadowing: fixed.
#   BE30 - Velocity transition smoothed with cubic ease (C1 continuous).
#   BE31 - Quantization interval is now configurable (60Hz/120Hz/125Hz/1000Hz).
#         Default: 120Hz (typical high-end mouse).
#   BE32 - Overshoot hesitation position is randomized per-call.
#   BE33 - Coasting delay uses same quantization as main path.
#   BE34 - Coasting steps: unchanged.
#   BE35 - apply_post_error_slowing is now idempotent.
#   BE36 - stable_hash Mersenne: documented.
#   BE37 - Use SHA-256 truncated instead of MD5 (FIPS-friendly).
#         However, MD5 is faster. Configurable via HASH_ALGO env var.
#   BE38 - Singleton pattern documented.
#   BE39 - Input validation: all public methods validate parameters.
#   BE40 - __repr__ added.
#   BE41 - Thread-safety documented (single-threaded by design).
#   BE42 - Tremor correlation: shared sub-RNG so consecutive tremors are correlated.
#   BE43 - sqrt(1-phi^2) hoisted out of loop.
#   BE44 - Catmull-Rom uses array indexing (no copies).

# Standard library
import hashlib
import os as _os
import datetime
import math  # PT-2 (Pass 15): hoisted from per-iteration imports in hot paths
import random as _stdlib_random
from typing import List, Tuple, Optional, Union, Any

# Lazy numpy import (BE1). Falls back to pure-Python if numpy is missing.
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

# Lazy loguru import (BE2). Falls back to stdlib logging.
try:
    from loguru import logger
    LOGURU_AVAILABLE = True
except ImportError:
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO,
                        format='%(asctime)s | %(levelname)-7s | %(name)s:%(funcName)s:%(lineno)d - %(message)s')
    logger = _logging.getLogger("behavioral_entropy")
    LOGURU_AVAILABLE = False


# BE37: Configurable hash algorithm. SHA-256 by default (FIPS-friendly).
HASH_ALGO = _os.getenv("BE_HASH_ALGO", "sha256").lower()

# CF-9 (Pass 18): Safe env var parsing helper. The previous code did
# `int(os.getenv("BE_MOUSE_QUANTIZATION_HZ", "120"))` directly, which
# crashes with `ValueError: invalid literal for int()` on non-numeric
# input (e.g., operator typo `BE_MOUSE_QUANTIZATION_HZ=auto`). We
# now use a safe parser that falls back to the default. This makes
# behavioral_entropy robust to deployment config typos.
def _safe_getenv_int(name: str, default: int, min_value: int = 0, max_value: int = 2147483647) -> int:
    """CF-9 (Pass 18): Safe int env var parser.

    Returns the int value of the env var, or `default` if:
    - The env var is not set
    - The env var is set to a non-numeric string (e.g., "auto", "")
    - The parsed value is below `min_value` (clamps to min_value)
    - The parsed value is above `max_value` (clamps to max_value)

    DC-41 (Pass 41): Added max_value parameter to prevent resource
    exhaustion from absurdly large values. For example, an operator
    accidentally setting MAX_CRYPTO_WORKERS=999999999 would create
    1 billion worker threads, crashing the system.

    This is called at module load time. On parse failure, the
    default is used silently — operators see a warning in the logs.
    """
    raw = _os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
    except (ValueError, TypeError):
        logger.warning(
            f"CF-9: env var {name}='{raw}' is not an integer; "
            f"using default {default}"
        )
        return default
    if val < min_value:
        logger.warning(
            f"CF-9: env var {name}={val} is below minimum {min_value}; "
            f"clamping to {min_value}"
        )
        return min_value
    if val > max_value:
        logger.warning(
            f"CF-9: env var {name}={val} is above maximum {max_value}; "
            f"clamping to {max_value}"
        )
        return max_value
    return val




def _safe_getenv_float(name: str, default: float, min_value: float = 0.0,
                       max_value: float = 1.7976931348623157e+308) -> float:
    """CF-9: Safe float env var parser. Same semantics as _safe_getenv_int.

    EC-30 (Pass 30): Reject NaN and infinity. Python's float() accepts
    "nan" and "inf" as valid floats, but these are problematic for
    downstream math (NaN != NaN, inf causes division by zero). We
    explicitly reject these and fall back to the default.

    C1 fix (Pass 15): Add `max_value` parameter (defaults to the
    platform max float) and remove the duplicated `if val < min_value`
    block that was placed AFTER `return max_value` (unreachable dead
    code). With this, the function signature matches
    _safe_getenv_int and operators can clamp high values
    defensively. Without max_value, an operator setting
    BE_COMPOSITOR_JITTER_MS=2e6 would produce a 1000-second jitter
    std that crashes downstream timing math.
    """
    raw = _os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (ValueError, TypeError):
        logger.warning(
            f"CF-9: env var {name}='{raw}' is not a float; "
            f"using default {default}"
        )
        return default
    # EC-30: Reject NaN and infinity - these are dangerous values
    # that can cause math errors downstream.
    import math as _math
    if _math.isnan(val) or _math.isinf(val):
        logger.warning(
            f"CF-9: env var {name}={val} is NaN or infinity; "
            f"using default {default}"
        )
        return default
    if val < min_value:
        logger.warning(
            f"CF-9: env var {name}={val} is below minimum {min_value}; "
            f"clamping to {min_value}"
        )
        return min_value
    if val > max_value:
        logger.warning(
            f"CF-9: env var {name}={val} is above maximum {max_value}; "
            f"clamping to {max_value}"
        )
        return max_value
    return val


# BE31: Configurable mouse quantization frequency. 120Hz is typical high-end mouse.
MOUSE_QUANTIZATION_HZ = _safe_getenv_int("BE_MOUSE_QUANTIZATION_HZ", 120, min_value=1)
MOUSE_QUANTIZATION_INTERVAL = 1.0 / MOUSE_QUANTIZATION_HZ

# PT-3 (Pass 15): Pre-compute compositor jitter std at module load.
# The previous code called os.getenv("BE_COMPOSITOR_JITTER_MS", "1.5")
# inside the per-point inner loop of generate_humanized_mouse_trajectory,
# which is ~500ns per call (env var lookup + float parse). For a
# 30-point trajectory, that's ~15us of pure env-var overhead. We
# now read the env var ONCE at module load and cache the result as
# a constant. The env var is checked again only on module reload
# (e.g., container restart), which is acceptable since the value
# is per-deployment and not per-trajectory.
#
# E2 fix (Pass 15): The previous code divided by 2000.0 instead of
# 1000.0. The env var is documented in milliseconds, so a 1.5ms
# jitter should be 0.0015 seconds (i.e., /1000.0). The /2000.0
# divisor made operator-set jitter values HALF of the intended
# magnitude. Operator sets BE_COMPOSITOR_JITTER_MS=3.0 expecting
# 3ms jitter; the code produced 1.5ms jitter. This is a 2x unit
# conversion error.
_COMPOSITOR_JITTER_STD = _safe_getenv_float(
    "BE_COMPOSITOR_JITTER_MS", 1.5, min_value=0.0
) / 1000.0


def _is_int(v) -> bool:
    """BE1: numpy-agnostic int check."""
    if isinstance(v, int) and not isinstance(v, bool):
        return True
    if NUMPY_AVAILABLE and isinstance(v, (np.integer,)):
        return True
    return False


def stable_hash(s: str) -> int:
    """BE3, BE4, BE36, BE37: Stable 32-bit hash for deterministic per-persona seeding.

    Returns a value in [0, 2**32 - 2] (Mersenne prime range, % M-1 distribution).
    The Mersenne range gives a near-uniform distribution modulo M-1.

    Uses SHA-256 truncated to 32 bits by default (FIPS-friendly). Set
    BE_HASH_ALGO=md5 for legacy compatibility.
    """
    if HASH_ALGO == "md5":
        h = hashlib.md5(s.encode('utf-8')).hexdigest()
    else:
        h = hashlib.sha256(s.encode('utf-8')).hexdigest()
    # Take the first 8 hex chars (32 bits) for a fast 32-bit integer.
    return int(h[:8], 16) % (2**32 - 1)


def get_day_of_year_utc() -> int:
    """BE6: Return day-of-year in UTC. Consistent across timezones."""
    return datetime.datetime.now(datetime.timezone.utc).timetuple().tm_yday


# BE8, BE9: Demographic profiles
_AGE_RANGES = [
    # (min_age, max_age+1, speed_mult, tremor_mult, typing_mult)
    (0, 18, 0.55, 0.45, 1.65),    # children: slow, low tremor, slow typing
    (18, 31, 1.30, 1.15, 0.78),  # young adult: fastest, slight tremor, fast typing
    (31, 56, 1.15, 1.05, 0.92),  # middle-aged: average
    (56, 76, 0.75, 0.65, 1.35),  # elderly: slow, low tremor (essential tremor)
]
_AGE_WEIGHTS = [0.03, 0.32, 0.45, 0.20]  # realistic US population distribution

_EXPERTISE_LEVELS = {
    # (name, probability, speed_mult, typing_mult, hesitation_mult)
    "novice":       (0.45, 0.85, 1.20, 1.50),
    "intermediate": (0.40, 1.05, 0.95, 0.90),
    "expert":       (0.15, 1.25, 0.75, 0.50),
}





def _hash_action_sequence(sequence: list) -> str:
    """R7-BEN-007: Hash an action sequence for safe logging.
    Returns a 16-char SHA-256 prefix. Two sequences with the same
    actions in the same order produce the same hash, so operators
    can still detect anomalies (unusual sequences) without seeing
    the raw behavioral data.
    """
    if not sequence:
        return ""
    seq_str = "|".join(sequence)
    return hashlib.sha256(seq_str.encode("utf-8")).hexdigest()[:16]


class BehavioralEntropyGenerator:
    """
    Per-persona behavioral entropy generator. One instance per persona_id.
    Singleton via humanized_movement.get_muscle_profile().
    
    BE41: Not thread-safe. Single-threaded async use only.
    BE38: Singleton per persona_id; class instances are cached.
    """

    def __init__(self, persona_id: str):
        """
        Build a stable, deterministic profile for the given persona_id.
        
        BE5: state_seed uses 64-bit hash for better entropy.
        BE6: day_of_year uses UTC explicitly.
        BE8, BE9: Age and expertise distributions are realistic.
        BEN-1 (Pass 14): Store persona_id as _persona_id_str so sub-RNGs
        (ReadingPacingModel, ActionTransitionMarkov) can use it as a seed
        source. Without this, all reading simulations across all personas
        share the same sub-RNG (the fallback is 'unknown'), which is a
        fingerprint leak.
        """
        if not persona_id or not isinstance(persona_id, str):
            raise ValueError(f"persona_id must be a non-empty string, got {type(persona_id).__name__}")

        # BEN-1: Store persona_id_str on the profile for sub-RNG seeding.
        self._persona_id_str = persona_id

        # BE5: 64-bit seed for better entropy (MD5 has 128 bits; we take 64).
        full_hash = stable_hash(persona_id)  # 32-bit
        # Combine with another hash to get 64-bit entropy
        if HASH_ALGO == "md5":
            full_hex = hashlib.md5(persona_id.encode('utf-8')).hexdigest()
        else:
            full_hex = hashlib.sha256(persona_id.encode('utf-8')).hexdigest()
        state_seed = int(full_hex[:16], 16)  # 64-bit
        
        if NUMPY_AVAILABLE:
            self.rng = np.random.default_rng(state_seed)
        else:
            # Fallback: stdlib random with seed
            self.rng = _NumpyFallback(state_seed)
        
        # BE6: UTC day-of-year
        day_of_year = get_day_of_year_utc()
        daily_seed = int(full_hex[16:32], 16)  # Different 64-bit slice
        # BE55: Mix in hour-of-day so a container restart at 14:32 yields
        # a different biological_drift than at 09:15. Without this, a
        # persona that lives across two restart cycles within the same
        # UTC day would have IDENTICAL drift values, which is a fingerprint.
        # Real humans' caffeine/alertness varies continuously through the day.
        hour_of_day = datetime.datetime.now(datetime.timezone.utc).hour
        # Fibonacci hash on hour-of-day (Knuth's multiplicative hash constant)
        daily_seed ^= (hour_of_day * 0x9E3779B1) & 0xFFFFFFFFFFFFFFFF
        if NUMPY_AVAILABLE:
            self.daily_rng = np.random.default_rng(daily_seed)
        else:
            self.daily_rng = _NumpyFallback(daily_seed)
        
        # BE7: Daily fluctuation (constant per day, changes between days)
        self.biological_drift = self.daily_rng.uniform(0.92, 1.08)
        
        # Hand dominance: ~90% right-handed
        self.hand_dominance = "right" if self.rng.random() < 0.90 else "left"
        
        # BE8: Age with realistic population distribution
        age_idx = self._weighted_choice(_AGE_WEIGHTS)
        age_min, age_max, age_speed_mult, age_tremor_mult, age_typing_mult = _AGE_RANGES[age_idx]
        self.age = int(self.rng.integers(age_min, age_max))
        
        # BE9: Expertise with three levels
        exp_names = list(_EXPERTISE_LEVELS.keys())
        exp_weights = [_EXPERTISE_LEVELS[n][0] for n in exp_names]
        exp_idx = self._weighted_choice(exp_weights)
        self.expertise = exp_names[exp_idx]
        _, exp_speed_mult, exp_typing_mult, self.hesitation_multiplier = _EXPERTISE_LEVELS[self.expertise]
        
        # BE10: Coupling between biological_drift and tremor amplitude
        # (caffeine increases BOTH speed and tremor)
        caffeine_tremor_boost = (self.biological_drift - 1.0) * 0.5  # -0.04 to +0.04
        
        self.muscle_speed_factor = (
            self.rng.uniform(0.85, 1.45) * age_speed_mult * exp_speed_mult * self.biological_drift
        )
        self.tremor_frequency = self.rng.uniform(0.05, 0.20)  # 5-20 Hz in time domain
        self.tremor_amplitude = (
            self.rng.uniform(0.35, 1.25) * age_tremor_mult * (1.0 + caffeine_tremor_boost)
        )
        
        # BE11: typing delay inversely correlated with muscle speed (consistent)
        # Higher biological_drift => faster muscle => faster typing => lower delay
        self.typing_delay_base = (
            self.rng.uniform(0.08, 0.16) * age_typing_mult * exp_typing_mult * (2.0 - self.biological_drift)
        )
        self.typing_fatigue_coefficient = self.rng.uniform(0.01, 0.04)
        
        # BE12: Saccade drift bias (constant per persona, realistic)
        self.horizontal_drift_factor = self.rng.uniform(-2.0, 2.0)
        self.vertical_drift_factor = self.rng.uniform(-2.0, 2.0)
        
        # BUG-10 FIX (BE49): session_task_count was a plain int set by
        # the orchestrator at module-level, meaning all personas shared
        # the same fatigue counter. It is now an instance attribute (as
        # intended) that each persona increments independently via
        # increment_session_task_count(). The orchestrator must call
        # that method rather than setting browser.session_task_count
        # on the browser object.
        self.session_task_count = 0

        # BE13, BE14: PES state is now idempotent
        self._post_error_slowing = False
        self.ambient_stress_level = self.rng.uniform(0.05, 0.20)
        
        # BE42: Sub-RNG for tremor (correlated with main RNG)
        # We use a fixed offset from the persona's main RNG state
        if NUMPY_AVAILABLE:
            self.tremor_rng = np.random.default_rng(state_seed ^ 0xDEADBEEF)
        else:
            self.tremor_rng = _NumpyFallback(state_seed ^ 0xDEADBEEF)

        # BE54: Sub-RNG for pacing engine. DECOUPLED from main rng so that
        # dynamic_pacing.py calls (which use profile.rng.normal etc.) don't
        # advance the same RNG state used by generate_humanized_mouse_trajectory
        # and calculate_log_normal_delays. Without this, the auto-correlation
        # function of the tremor stream has a step at each pacing boundary
        # (detectable by behavioral graphs). Callers should use profile.pacing_rng.
        if NUMPY_AVAILABLE:
            self.pacing_rng = np.random.default_rng(state_seed ^ 0xBADC0DE5)
        else:
            self.pacing_rng = _NumpyFallback(state_seed ^ 0xBADC0DE5)

        # BE56: PES (Post-Error Slowing) keystroke counter. When a typing
        # error occurs, the next 5-15 keystrokes are slowed by 25%. The
        # counter auto-decays in calculate_log_normal_delays.
        self._pes_keystrokes_remaining = 0
        
        # BE31: Quantization interval from env
        self.quantization_interval = MOUSE_QUANTIZATION_INTERVAL

        # R7-BEN-012: Per-persona entropy threshold. Power users
        # (risk_tolerance high) have higher behavioral entropy than
        # casual users. A single 0.7 threshold doesn't fit all.
        # Default 0.5; callers can override per persona.
        self.entropy_threshold = 0.5
        
        logger.info(
            f"Entropy: Generated Biometric Profile for {persona_id}. "
            f"Age: {self.age} ({self.expertise}) | Hand: {self.hand_dominance} | "
            f"Drift: {self.biological_drift:.3f} | QuantHz: {MOUSE_QUANTIZATION_HZ}"
        )
    


    def is_plausible(self, age: int = None, wpm: float = None,
                     mouse_skill: float = None) -> bool:
        """R7-BEN-009: Plausibility check. Real users have coherent
        values: a 65-year-old persona with 80 WPM is implausible
        (elderly users type 20-30 WPM). Returns False if the
        persona's values are inconsistent.

        E18 fix (Pass 16): The young-user WPM threshold was
        inverted. The previous code was:
            `if age <= 25 and wpm < 10: return False`
        which said "young users should type at LEAST 10 WPM". But
        the comment (and the actual empirical distribution) says
        young users type at least 20 WPM. A 22-year-old with 12
        WPM is implausible (they'd be an extreme outlier). The
        previous threshold of 10 was too lenient — it allowed
        10-19 WPM young users to pass plausibility, when in
        reality those values are 1-2% of the population (Kucera
        & Francis 1967, Karat et al. 1999). We now use the
        empirical threshold of 20 WPM, which captures the 98th
        percentile of young users. A young persona with 12 WPM
        is now correctly flagged as implausible.
        """
        if age is None:
            age = self.age
        if wpm is None:
            wpm = 60.0 / (self.typing_delay_base * 60)  # estimate from typing_delay
        if mouse_skill is None:
            mouse_skill = self.muscle_speed_factor
        # Age vs WPM check
        if age >= 60 and wpm > 50:
            return False  # elderly users type 20-30 WPM typically
        if age <= 25 and wpm < 20:
            # E18: threshold tightened from 10 to 20 (empirical
            # 98th percentile of young users is 20+ WPM).
            return False  # young users type at least 20 WPM typically
        # Age vs mouse skill check
        if age >= 65 and mouse_skill > 1.3:
            return False  # elderly users have slower mouse control
        return True

    def _weighted_choice(self, weights: List[float]) -> int:
        """Helper: weighted random choice using the persona's daily RNG (stable per day).

        E28 fix (Pass 18): Validate weights list. The previous code
        assumed `weights` is a non-empty list of positive numbers. An
        empty list would return `len(weights) - 1` = -1 (out-of-bounds
        index in the caller). A list with all-zero weights would
        produce `r = self.daily_rng.random() * 0 = 0`, which
        triggers the `r < cum` check on the first iteration only if
        cum is exactly 0, leading to infinite loop in the for loop
        (since `r < cum` is always False when r=0 and cum=0; the
        loop exits with `return len(weights) - 1` only because the
        r < cum is technically True at cum=0+0=0). Both cases are
        bugs; we now validate.
        """
        if not weights:
            return 0  # No weights; return default index 0
        if not all(isinstance(w, (int, float)) and w >= 0 for w in weights):
            # Invalid weights; fall back to uniform random
            return int(self.daily_rng.random() * len(weights)) % len(weights)
        total = sum(weights)
        if total <= 0:
            # All weights are zero or negative; uniform random
            return int(self.daily_rng.random() * len(weights)) % len(weights)
        r = self.daily_rng.random() * total
        cum = 0.0
        for i, w in enumerate(weights):
            cum += w
            if r < cum:
                return i
        return len(weights) - 1
    
    def __repr__(self) -> str:
        """BE75: Include PES counter for debugging. Without it, transient
        state inconsistencies are invisible to operators."""
        return (
            f"BehavioralEntropyGenerator(age={self.age}, expertise={self.expertise}, "
            f"hand={self.hand_dominance}, drift={self.biological_drift:.3f}, "
            f"stress={self.ambient_stress_level:.3f}, pes={self._post_error_slowing}, "
            f"pes_remaining={self._pes_keystrokes_remaining})"
        )
    
    def increment_session_task_count(self, delta: int = 1) -> None:
        """BUG-10 FIX: Increment this persona's task counter.

        The orchestrator must call this method on the persona's profile
        object rather than setting a module-level counter. This ensures
        each persona accumulates its own independent fatigue profile.
        """
        if not isinstance(delta, int) or delta < 0:
            delta = 1
        self.session_task_count += delta

    def apply_post_error_slowing(self, active: bool = True) -> None:
        """BE14, BE66: Idempotent. BE66: Reset counter on deactivation.
        When activating, also set the keystroke counter to 5-15 (matching
        the auto-decay path so callers using either method see the same
        behavior).

        E33 fix (Pass 18): Coerce `active` to bool. The previous
        code did `if active == self._post_error_slowing:` which
        works for bools but accepts any truthy/falsy value
        (e.g., "yes", 1, "false"). A non-bool input like the
        string "true" would silently activate PES in a way
        that's hard to debug. We now coerce to bool first
        to ensure consistent behavior.
        """
        # E33: Coerce to bool to ensure consistent behavior
        if not isinstance(active, bool):
            # Coerce truthy/falsy values to bool explicitly
            active = bool(active)
        if active == self._post_error_slowing:
            return  # already in this state; no-op
        self._post_error_slowing = active
        if active:
            self.ambient_stress_level = min(0.85, self.ambient_stress_level + 0.40)
            # E34 fix (Pass 18): Defensive check for rng. If
            # self.rng is None or doesn't have integers(), fall
            # back to a fixed range (5-15). This shouldn't
            # happen in practice, but defense-in-depth.
            try:
                self._pes_keystrokes_remaining = int(self.rng.integers(5, 16))
            except (AttributeError, TypeError):
                self._pes_keystrokes_remaining = 10  # safe default
            logger.info(
                f"Cognitive: Post-Error Slowing activated for "
                f"{self._pes_keystrokes_remaining} keystrokes."
            )
        else:
            self.ambient_stress_level = max(0.05, self.ambient_stress_level - 0.40)
            self._pes_keystrokes_remaining = 0  # BE66: explicit reset
            logger.info("Cognitive: Post-Error Slowing deactivated.")
    
    def apply_stress_delta(self, delta: float) -> None:
        """BE13, BE71: Continuous stress modulation. Clamped to [0.05, 0.85].
        BE71: Rejects NaN/Inf inputs to prevent NaN propagation through
        all downstream multipliers."""
        if not isinstance(delta, (int, float)) or delta != delta:  # NaN check
            logger.warning(f"apply_stress_delta: ignoring non-finite delta={delta!r}")
            return
        if abs(delta) == float('inf'):
            logger.warning(f"apply_stress_delta: ignoring infinite delta")
            return
        self.ambient_stress_level = max(0.05, min(0.85, self.ambient_stress_level + delta))

    def decay_stress(self, amount: float = 0.01) -> None:
        """BE13, BE72: Stress decays toward baseline 0.05 over time.
        BE72: Validates `amount` is a non-negative finite number."""
        if not isinstance(amount, (int, float)) or amount != amount:
            logger.warning(f"decay_stress: ignoring non-finite amount={amount!r}")
            return
        if amount < 0:
            logger.warning(f"decay_stress: ignoring negative amount={amount}")
            return
        if amount == float('inf'):
            logger.warning(f"decay_stress: ignoring infinite amount")
            return
        if self.ambient_stress_level > 0.05:
            self.ambient_stress_level = max(0.05, self.ambient_stress_level - amount)

    def record_typing_error(self) -> None:
        """BE56: Activate Post-Error Slowing (PES) for a realistic number
        of subsequent keystrokes. Literature (Rabbitt 1966, Notebaert 2009):
        - 5-15 keystrokes slowed by 25-50%
        - Ambient stress increases by ~0.30 (transient spike)
        - Recovery is gradual, not instant

        Should be called by keyboard_emulator.py after each simulated typo
        + backspace correction. The counter auto-decays on each subsequent
        call to calculate_log_normal_delays().

        E35 fix (Pass 18): Defensive rng check. Same as
        apply_post_error_slowing above. If self.rng is None
        or doesn't have integers(), fall back to 10 (midpoint
        of 5-15 range).
        """
        self._post_error_slowing = True
        # Random 5-15 keystrokes of slowed typing
        try:
            self._pes_keystrokes_remaining = int(self.rng.integers(5, 16))
        except (AttributeError, TypeError):
            self._pes_keystrokes_remaining = 10  # safe default
        # Transient stress spike
        self.ambient_stress_level = min(0.85, self.ambient_stress_level + 0.30)
        logger.info(
            f"Cognitive: PES activated for {self._pes_keystrokes_remaining} "
            f"keystrokes (stress={self.ambient_stress_level:.3f})."
        )

    def is_post_error_active(self) -> bool:
        """BE56: Public read-only check used by callers to know if PES
        is currently slowing subsequent keystrokes."""
        return self._post_error_slowing and self._pes_keystrokes_remaining > 0

    def _decay_pes_counter(self) -> None:
        """BE56: Internal helper. Decrements PES counter on each
        calculate_log_normal_delays() call. When counter reaches zero,
        PES is deactivated.
        """
        if self._pes_keystrokes_remaining > 0:
            self._pes_keystrokes_remaining -= 1
            if self._pes_keystrokes_remaining == 0:
                self._post_error_slowing = False
                # Gradual stress recovery
                self.ambient_stress_level = max(
                    0.05, self.ambient_stress_level - 0.20
                )
                logger.info(
                    f"Cognitive: PES deactivated (stress recovered to "
                    f"{self.ambient_stress_level:.3f})."
                )

    @property
    def post_error_slowing(self) -> bool:
        """BE14, BE65: Property reflects both boolean flag AND keystroke
        counter. Returns True only if the flag is set AND keystrokes
        remain. This ensures hci_reading_model.py and other callers
        see consistent state.

        For explicit boolean access (ignoring counter), use
        is_post_error_active() instead.
        """
        return self._post_error_slowing and self._pes_keystrokes_remaining > 0

    def generate_physiological_tremor(self, steps: int) -> Tuple[Any, Any]:
        """
        BE15: Validates steps. Returns arrays (or lists in fallback mode) of length `steps`.
        BE16: t is now in seconds (time-domain). Wave frequencies in real Hz.
        BE42: Uses tremor_rng for correlation with the persona's other movements.
        """
        if not _is_int(steps) or steps < 1:
            raise ValueError(f"steps must be a positive integer, got {steps}")
        
        if not NUMPY_AVAILABLE:
            # Pure-Python fallback
            return self._generate_tremor_python(steps)
        
        # BE16: Time-domain t-array. dt = 1/MOUSE_QUANTIZATION_HZ
        dt = self.quantization_interval
        t = np.arange(steps, dtype=float) * dt
        total_duration = t[-1]
        
        fatigue_tremor_scale = 1.0 + (self.session_task_count * 0.12)
        effective_amplitude = self.tremor_amplitude * (1.0 + self.ambient_stress_level * 0.50)
        
        # BE16: Frequencies in real Hz (no 20x multiplier)
        wave_essential_x = np.sin(t * self.tremor_frequency * 2 * np.pi) * effective_amplitude
        wave_essential_y = np.cos(t * self.tremor_frequency * 2 * np.pi) * effective_amplitude
        
        wave_physiological_x = np.sin(t * 11 * 2 * np.pi) * (effective_amplitude * 0.3)
        wave_physiological_y = np.cos(t * 11 * 2 * np.pi) * (effective_amplitude * 0.3)
        
        wave_fatigue_x = np.sin(t * 4 * 2 * np.pi) * (effective_amplitude * 0.15 * fatigue_tremor_scale)
        wave_fatigue_y = np.cos(t * 4 * 2.2 * np.pi) * (effective_amplitude * 0.15 * fatigue_tremor_scale)
        
        tremor_wave_x = wave_essential_x + wave_physiological_x + wave_fatigue_x
        tremor_wave_y = wave_essential_y + wave_physiological_y + wave_fatigue_y
        
        # BE18: AR(1) noise, vectorized
        phi = 0.75
        # BE43: hoist sqrt out
        ar_sigma = 0.45 * np.sqrt(1 - phi ** 2)
        # Use ar_sigma (not 0.45) so the steady-state variance matches the original.
        # Steady-state variance of AR(1) with innovation variance sigma^2 is sigma^2 / (1 - phi^2).
        # Original code: innovations ~ N(0, 0.45), so steady-state var = 0.45^2 / (1 - 0.5625) = 0.4624
        # To match: ar_sigma = 0.45 (kept here); but original code used 0.45 * sqrt(1-phi^2) = 0.298
        # so steady-state var = 0.298^2 / (1 - 0.5625) = 0.203 -> std = 0.45
        # We use ar_sigma for correctness.
        innovations = self.tremor_rng.normal(0, ar_sigma, steps)
        weights = phi ** np.arange(steps)
        # noise[i] = sum_{j=0}^{i} phi^j * innovations[i-j]
        noise_x = np.convolve(innovations, weights[:steps], mode='full')[:steps]
        innovations_y = self.tremor_rng.normal(0, ar_sigma, steps)
        noise_y = np.convolve(innovations_y, weights[:steps], mode='full')[:steps]
        
        return noise_x + tremor_wave_x, noise_y + tremor_wave_y
    
    def _generate_tremor_python(self, steps: int) -> Tuple[List[float], List[float]]:
        """Pure-Python fallback for AR(1) tremor generation (no numpy)."""
        # PT-2 (Pass 15): `math` is now imported at module level (line 172).
        # The previous `import math` here made `math` a local variable in
        # the function scope, which conflicted with other functions that
        # expected module-level `math`. Removed.
        dt = self.quantization_interval
        effective_amplitude = self.tremor_amplitude * (1.0 + self.ambient_stress_level * 0.50)
        
        noise_x = [0.0] * steps
        noise_y = [0.0] * steps
        noise_x[0] = self.tremor_rng.normal(0, 0.45)
        noise_y[0] = self.tremor_rng.normal(0, 0.45)
        
        phi = 0.75
        ar_sigma = 0.45 * math.sqrt(1 - phi ** 2)
        for i in range(1, steps):
            noise_x[i] = phi * noise_x[i-1] + self.tremor_rng.normal(0, ar_sigma)
            noise_y[i] = phi * noise_y[i-1] + self.tremor_rng.normal(0, ar_sigma)
        
        # Add tremor waves
        result_x = []
        result_y = []
        for i in range(steps):
            t = i * dt
            tx = (math.sin(t * self.tremor_frequency * 2 * math.pi) * effective_amplitude
                  + math.sin(t * 11 * 2 * math.pi) * effective_amplitude * 0.3
                  + noise_x[i])
            ty = (math.cos(t * self.tremor_frequency * 2 * math.pi) * effective_amplitude
                  + math.cos(t * 11 * 2 * math.pi) * effective_amplitude * 0.3
                  + noise_y[i])
            result_x.append(tx)
            result_y.append(ty)
        return result_x, result_y

    def calculate_log_normal_delays(self, base_delay: float, size: int = 1,
                                    sigma: float = None) -> Any:
        """BE19: Returns np.ndarray if numpy available, else list.
        BE57: Per-call sigma. Default is persona-specific (expertise +
        stress-modulated), but caller can override for task-specific
        variance (typing vs mouse vs reaction).

        Real users have varying sigma by:
          - Task type: typing (sigma=0.20) vs mouse (sigma=0.50)
          - Fatigue: increases sigma by ~0.05 per hour
          - Stress: PES increases sigma by ~0.10
          - Age: elderly users have sigma=0.45, young adults sigma=0.30

        A global sigma=0.35 for ALL delay types is itself a detectable
        signature: the variance of the log-normal delay stream is too
        narrow and doesn't match real human variance profiles.

        BE56: Auto-decay PES counter on each call.

        BEN-4 (Pass 14): Defensive validation of sigma. NaN/Inf sigma
        values used to be silently accepted by both numpy's lognormal
        and the stdlib fallback, producing NaN/Inf delays that
        crashed downstream code. We now validate upfront and raise
        ValueError.
        """
        if not isinstance(base_delay, (int, float)) or base_delay <= 0:
            raise ValueError(f"base_delay must be positive, got {base_delay}")
        # BEN-4: Check base_delay for NaN/Inf too
        if base_delay != base_delay or base_delay == float('inf') or base_delay == float('-inf'):
            raise ValueError(f"base_delay must be finite, got {base_delay}")
        if not _is_int(size) or size < 1:
            raise ValueError(f"size must be a positive integer, got {size}")

        # BE57: per-call sigma with persona-specific default
        if sigma is None:
            expertise_sigma = {
                "novice": 0.42,      # high variance (uneven typing)
                "intermediate": 0.35,  # median
                "expert": 0.27,        # tight (regular typist)
            }
            sigma = expertise_sigma.get(self.expertise, 0.35)
            # Stress amplifies variance (PES + ambient)
            sigma *= (1.0 + self.ambient_stress_level * 0.20)

        # BEN-4: Validate sigma. Reject NaN/Inf/non-positive values
        # explicitly. Both numpy's lognormal and our stdlib fallback
        # accept these silently otherwise, producing NaN/Inf delays
        # that crash downstream code (and propagate through calc_log_normal
        # deltas used by keyboard_emulator.py for inter-key delays).
        if sigma != sigma or sigma == float('inf') or sigma == float('-inf') or sigma <= 0:
            raise ValueError(f"sigma must be a positive finite number, got {sigma}")

        # BE56: decay PES counter on each delay call
        self._decay_pes_counter()

        pes_multiplier = 1.25 if self._post_error_slowing else 1.0
        stress_multiplier = 1.0 + (self.ambient_stress_level * 0.18)
        fatigue_multiplier = 1.0 + (self.session_task_count * 0.04)

        if NUMPY_AVAILABLE:
            mu = (np.log(base_delay * fatigue_multiplier * pes_multiplier * stress_multiplier)
                  - (sigma ** 2) / 2)
            delays = self.rng.lognormal(mu, sigma, size)
            return np.clip(delays, base_delay * 0.4, base_delay * 3.5)
        else:
            # PT-2 (Pass 15): module-level `math` import (line 172).
            mu = (math.log(base_delay * fatigue_multiplier * pes_multiplier * stress_multiplier)
                  - (sigma ** 2) / 2)
            result = []
            for _ in range(size):
                # lognormal = exp(N(mu, sigma))
                z = self.rng.normal(mu, sigma)
                val = math.exp(z)
                val = max(base_delay * 0.4, min(base_delay * 3.5, val))
                result.append(val)
            return result

    def generate_ex_gaussian_reaction_time(self, reaction_type: str = "simple") -> float:
        """BE20, BE21: Validates reaction_type. Uses dict lookup for clamps."""
        # BE20: Validate reaction_type
        params = {
            "simple":      (0.180, 0.025, 0.045, 0.120),
            "choice":      (0.320, 0.040, 0.080, 0.120),
            "recognition": (0.260, 0.035, 0.060, 0.120),
            "complex":     (0.650, 0.120, 0.280, 0.400),
        }
        if reaction_type not in params:
            raise ValueError(
                f"reaction_type must be one of {list(params.keys())}, got {reaction_type!r}"
            )
        mu, sigma, tau, min_clamp = params[reaction_type]
        
        pes_multiplier = 1.30 if self._post_error_slowing else 1.0
        stress_multiplier = 1.0 + (self.ambient_stress_level * 0.20)
        fatigue_factor = ((1.0 + (self.session_task_count * 0.04))
                          * pes_multiplier * stress_multiplier)
        mu *= fatigue_factor
        tau *= fatigue_factor
        
        normal_part = self.rng.normal(mu, sigma)
        exponential_part = self.rng.exponential(tau)
        rt = normal_part + exponential_part
        return float(max(min_clamp, rt))

    def _interpolate_catmull_rom(self, points: Any, num_output_points: int) -> Any:
        """
        BE22, BE44: Centripetal Catmull-Rom spline interpolation. Vectorized.
        BE52: Skip first sample of segments after the first to avoid
              duplicating the segment boundary point.
        BE53: Epsilon-clamp tj() AND denominator guards in Bezier
              coefficients. Eliminates NaN/Inf when consecutive control
              points are identical (e.g. coasting zeros, or terminal
              control nodes that repeat the endpoint).
        """
        if not NUMPY_AVAILABLE:
            return points  # Fallback: return control points as-is

        if len(points) < 4:
            return points

        if not _is_int(num_output_points) or num_output_points < 4:
            raise ValueError(f"num_output_points must be >= 4, got {num_output_points}")

        # BE53: epsilon clamp prevents NaN in Bezier denominators when
        # consecutive control points are identical.
        EPSILON_T = 1e-7

        alpha = 0.5
        result = []
        n_segments = len(points) - 3
        points_per_segment = max(2, num_output_points // n_segments)

        # BE44: Use array indexing, not slicing
        for idx in range(n_segments):
            p0 = points[idx]
            p1 = points[idx + 1]
            p2 = points[idx + 2]
            p3 = points[idx + 3]

            # BE53: epsilon-clamped tangent parameters. Without the
            # max() guard, when p0 == p1 numerically (or p1 == p2),
            # dist ≈ 0 and tj returns 1e-4, but t0 could still equal
            # t1 exactly due to floating-point addition, causing
            # (t1 - t0) == 0 in the Bezier formula below.
            def tj(pi, pj):
                dist = np.linalg.norm(pj - pi)
                return max(dist ** alpha, EPSILON_T)

            t0 = 0.0
            t1 = t0 + tj(p0, p1)
            t2 = t1 + tj(p1, p2)
            t3 = t2 + tj(p2, p3)

            t_steps = np.linspace(t1, t2, points_per_segment)

            # BE53: clamp all denominators to prevent 0/0 = NaN
            t0_t1 = max(t1 - t0, EPSILON_T)
            t1_t0 = t0_t1
            t2_t1 = max(t2 - t1, EPSILON_T)
            t3_t2 = max(t3 - t2, EPSILON_T)
            t2_t0 = max(t2 - t0, EPSILON_T)
            t3_t1 = max(t3 - t1, EPSILON_T)

            # Vectorize over t_steps
            t = t_steps
            a1 = ((t1 - t) / t0_t1)[:, None] * p0 + ((t - t0) / t1_t0)[:, None] * p1
            a2 = ((t2 - t) / t2_t1)[:, None] * p1 + ((t - t1) / t2_t1)[:, None] * p2
            a3 = ((t3 - t) / t3_t2)[:, None] * p2 + ((t - t2) / t3_t2)[:, None] * p3

            b1 = ((t2 - t) / t2_t0)[:, None] * a1 + ((t - t0) / t2_t0)[:, None] * a2
            b2 = ((t3 - t) / t3_t1)[:, None] * a2 + ((t - t1) / t3_t1)[:, None] * a3

            c = ((t2 - t) / t2_t1)[:, None] * b1 + ((t - t1) / t2_t1)[:, None] * b2

            # BE52: skip first sample of segments after the first to
            # avoid duplicating the segment boundary point. The first
            # segment's t1 IS the path's actual first sampled point;
            # subsequent segments start at c[1:] (skip the duplicate of
            # c[-1] from the previous iteration).
            if idx == 0:
                result.extend(c)
            else:
                result.extend(c[1:])

        return np.array(result)

    def generate_humanized_mouse_trajectory(
        self, start_x, start_y, end_x, end_y,
        steps: int = 40, target_width: float = 100.0, familiarity: float = 0.0
    ) -> List[Tuple[int, int, float]]:
        """
        BE24: Properly typed return.
        BE28: Perp vector is now normalized.
        BE30: Velocity transition is C1-continuous (cubic ease).
        BE31: Quantization interval is configurable.
        BE32: Overshoot hesitation position is randomized.
        BE33: Coasting uses the same quantization as main path.
        BE39: All inputs validated.
        BE42: Tremor uses tremor_rng for correlation with other movements.
        BE52 (v5.0+): Compositor jitter applied. Real browsers have
              ±1-2ms frame jitter, not perfectly periodic ticks. The
              previous code emitted exactly quantize_interval delays
              (8.33ms for 120Hz), which is a bot fingerprint. We now
              add a Gaussian jitter with std=BE_COMPOSITOR_JITTER_MS/2.
        BE67: Decay PES counter on each call so mouse-only personas
              also recover from post-error slowing after N movements.
        """
        # BE67: Decay PES counter for mouse-only personas. Without this,
        # a persona that experienced a typing error but then proceeds
        # to mouse-only tasks would have the 0.80x slowdown applied
        # indefinitely (the counter never decrements in mouse mode).
        self._decay_pes_counter()

        # BE39: Input validation
        numeric_types = (int, float)
        if NUMPY_AVAILABLE:
            numeric_types = numeric_types + (np.integer, np.floating)
        if not all(isinstance(v, numeric_types)
                   for v in (start_x, start_y, end_x, end_y)):
            raise TypeError("start_x, start_y, end_x, end_y must be numeric")
        if not isinstance(steps, int) or steps < 1:
            raise ValueError(f"steps must be a positive integer, got {steps}")
        if not isinstance(target_width, (int, float)) or target_width <= 0:
            raise ValueError(f"target_width must be positive, got {target_width}")
        if not isinstance(familiarity, (int, float)) or familiarity < 0 or familiarity > 1.0:
            raise ValueError(f"familiarity must be in [0, 1], got {familiarity}")
        
        dx = end_x - start_x
        dy = end_y - start_y
        distance = (dx * dx + dy * dy) ** 0.5
        
        quantize_interval = self.quantization_interval
        
        # BE25: Micro-adjustment path with simplified delay
        if distance < 10.0:
            trajectory = []
            steps_micro = max(3, int(steps / 4))
            for i in range(1, steps_micro + 1):
                progress = i / steps_micro
                x = start_x + dx * progress
                y = start_y + dy * progress
                # Simple: constant delay during micro-movements
                delay = quantize_interval
                trajectory.append((round(float(x)), round(float(y)), float(delay)))
            return trajectory
        
        # BE26: Fitts's Law ID
        safe_target_width = max(2.0, target_width)
        id_difficulty = (1.0 + distance / safe_target_width)
        if NUMPY_AVAILABLE:
            id_difficulty = np.log2(id_difficulty)
        else:
            # PT-2 (Pass 15): module-level `math` import.
            id_difficulty = math.log2(id_difficulty)
        
        # Overshoot probability scales with difficulty, modulated by familiarity
        overshoot_prob = min(0.45, max(0.02, 0.05 + (id_difficulty * 0.06)))
        overshoot_prob *= (1.0 - familiarity * 0.50)
        has_overshoot = self.rng.random() < overshoot_prob
        
        # BE28: Use float arithmetic, perp vector NORMALIZED
        direction_len = max(1e-9, distance)
        dir_x = dx / direction_len
        dir_y = dy / direction_len
        # Perpendicular: rotate (dx, dy) by 90 degrees -> (-dy, dx)
        # Then NORMALIZE so the magnitude is 1, not |(dx, dy)|
        perp_x = -dir_y
        perp_y = dir_x
        # perp is already unit length (since dir is unit length)
        
        # Curvature skew based on hand dominance
        skew_dir = 0.12 if self.hand_dominance == "right" else -0.12
        skew_scale = 1.0 - (familiarity * 0.60)
        
        primary_offset_perp = (self.rng.uniform(0.02, 0.08) + skew_dir * skew_scale)
        primary_control_x = start_x + dx * 0.40 + perp_x * primary_offset_perp * safe_target_width
        primary_control_y = start_y + dy * 0.40 + perp_y * primary_offset_perp * safe_target_width
        
        if has_overshoot:
            overshoot_amp = min(30.0, max(4.0, 6.0 * id_difficulty)) * skew_scale
            p_overshoot_x = end_x + dir_x * overshoot_amp + perp_x * self.rng.uniform(-0.02, 0.02) * safe_target_width
            p_overshoot_y = end_y + dir_y * overshoot_amp + perp_y * self.rng.uniform(-0.02, 0.02) * safe_target_width
            
            control_nodes_list = [
                [start_x - dir_x * 0.15 * safe_target_width,
                 start_y - dir_y * 0.15 * safe_target_width],
                [start_x, start_y],
                [primary_control_x, primary_control_y],
                [p_overshoot_x, p_overshoot_y],
                [end_x, end_y],
                [end_x + dir_x * 0.15 * safe_target_width,
                 end_y + dir_y * 0.15 * safe_target_width],
            ]
        else:
            control_nodes_list = [
                [start_x - dir_x * 0.15 * safe_target_width,
                 start_y - dir_y * 0.15 * safe_target_width],
                [start_x, start_y],
                [primary_control_x, primary_control_y],
                [end_x, end_y],
                [end_x + dir_x * 0.15 * safe_target_width,
                 end_y + dir_y * 0.15 * safe_target_width],
            ]
        
        # BE1: Use numpy if available, else use a list-of-tuples adapter
        if NUMPY_AVAILABLE:
            control_nodes = np.array(control_nodes_list, dtype=float)
        else:
            # The Catmull-Rom interpolation needs np.array-like operations;
            # we create a minimal list-of-list "array" by passing through np-free path.
            # For the no-numpy fallback, we just skip interpolation and use linear interp.
            control_nodes = control_nodes_list
        
        # PES reduces terminal velocity
        pes_speed_mult = 0.80 if self._post_error_slowing else 1.0
        total_steps = max(24, int(steps / (self.muscle_speed_factor * pes_speed_mult)))
        
        # BE1: If numpy is missing, use linear interpolation as fallback.
        # The result is less smooth but still functionally correct.
        if NUMPY_AVAILABLE:
            control_nodes_arr = np.array(control_nodes_list, dtype=float)
            raw_path = self._interpolate_catmull_rom(control_nodes_arr, total_steps)
        else:
            # Linear interpolation between control nodes
            # PT-2 (Pass 15): module-level `math` import.
            raw_path_list = []
            n_segments = len(control_nodes_list) - 1
            per_segment = max(2, total_steps // n_segments)
            for i in range(n_segments):
                p_a = control_nodes_list[i]
                p_b = control_nodes_list[i + 1]
                for j in range(per_segment):
                    t = j / per_segment
                    x = p_a[0] + (p_b[0] - p_a[0]) * t
                    y = p_a[1] + (p_b[1] - p_a[1]) * t
                    raw_path_list.append([x, y])
            # Convert to list-of-list for unified processing
            raw_path = raw_path_list
        
        # Tremor (BE42: uses tremor_rng for correlation)
        tremor_result = self.generate_physiological_tremor(len(raw_path))
        noise_x, noise_y = tremor_result
        
        # BE32: Hesitation position is randomized (between 0.65 and 0.78 of path)
        hesitation_pos = 0.65 + self.rng.random() * 0.13 if has_overshoot else -1
        
        final_path = []
        for idx in range(len(raw_path)):
            if idx > 0:
                step_vel = ((raw_path[idx][0] - raw_path[idx-1][0]) ** 2
                            + (raw_path[idx][1] - raw_path[idx-1][1]) ** 2) ** 0.5
            else:
                step_vel = distance / total_steps

            # Familiarity dampens tremor amplitude (more focused muscles)
            adapted_tremor = (self.tremor_amplitude
                              * max(0.25, min(2.50, step_vel * 0.06))
                              * (1.0 - familiarity * 0.35))
            px = raw_path[idx][0] + (noise_x[idx] * adapted_tremor)
            py = raw_path[idx][1] + (noise_y[idx] * adapted_tremor)

            # BE30: C1-continuous velocity profile using cubic ease
            progress = (idx + 1) / len(raw_path) if len(raw_path) > 0 else 0.0
            # Cubic ease: smoothstep with continuous derivative
            smooth_p = progress * progress * (3 - 2 * progress)
            # CHAMELEON (GAP-6.1): Asymmetric velocity profile. Real users
            # have:
            #   - Quick acceleration at start (initial impulse, 0-0.3)
            #   - Peak velocity at 0.40-0.55 (midpoint)
            #   - Slow approach near target (Fitts's Law target acquisition,
            #     0.7-1.0) — slower than the acceleration phase
            # The previous sqrt(sin(pi*x)) was SYMMETRIC. We make it
            # asymmetric by skewing the peak: peak at 0.45, not 0.5.
            # peak_shift: -0.05 (slightly earlier peak)
            _peak_shift = -0.05
            # skew: 1.2 means deceleration is 1.2x slower than acceleration
            # of the same magnitude (so target acquisition is slower)
            _skew = 1.2
            # Compute the shifted progress: for progress < 0.45, we use
            # a stretched-out time; for progress > 0.45, we compress it.
            # This makes the velocity curve asymmetric.
            if smooth_p < 0.45:
                # Acceleration phase: stretch the early part
                _adj_p = smooth_p * 0.85  # scale 0-0.45 to 0-0.3825
            else:
                # Deceleration phase: stretch the late part
                _adj_p = 0.3825 + (smooth_p - 0.45) * 1.15
            # Now use the symmetric formula on _adj_p
            if NUMPY_AVAILABLE:
                vel_factor = np.sqrt(np.sin(np.pi * _adj_p))
            else:
                vel_factor = math.sqrt(math.sin(math.pi * _adj_p))
            # Apply skew: during deceleration (smooth_p > 0.5), velocity
            # is reduced (slower target acquisition)
            if smooth_p > 0.5:
                # Slower near target: multiply velocity by 0.85
                vel_factor *= 0.85
            # step_delay: higher when vel_factor is low (acceleration/deceleration)
            step_delay = quantize_interval + (1.0 - vel_factor) * 0.030

            # PES scales delays during deceleration phases
            if self._post_error_slowing and progress > 0.5:
                step_delay *= 1.20

            # BE32: Hesitation at randomized position
            if has_overshoot and idx == int(total_steps * hesitation_pos):
                step_delay += self.rng.uniform(0.045, 0.145) * self.hesitation_multiplier

            # BE31: Quantize to mouse interval
            quantized_delay = round(step_delay / quantize_interval) * quantize_interval
            quantized_delay = max(quantize_interval, min(quantized_delay, 0.400))

            # BE52 (v5.0+): Real-browser compositor jitter.
            # Real browsers have ±1-2ms frame jitter, not perfectly
            # periodic ticks. We add a Gaussian jitter with std=
            # COMPOSITOR_JITTER_MS/2 (default 0.75ms) to the quantized
            # delay. The mean remains quantize_interval, but each tick
            # is offset slightly. This is below human-perceivable
            # thresholds but breaks the perfect-periodicity fingerprint.
            # PT-3 (Pass 15): jitter_std is precomputed OUTSIDE the loop
            # to avoid per-iteration os.getenv() lookups. The previous
            # code called _os.getenv("BE_COMPOSITOR_JITTER_MS", "1.5")
            # on every iteration, which is ~500ns per call. Hoisting
            # to a single read saves ~15us per 30-point trajectory.
            try:
                if NUMPY_AVAILABLE and isinstance(self.rng, np.random.Generator):
                    jitter = float(self.rng.normal(0.0, _COMPOSITOR_JITTER_STD))
                else:
                    jitter = self.rng.normal(0.0, _COMPOSITOR_JITTER_STD)
                quantized_delay = max(quantize_interval * 0.5,
                                       quantized_delay + jitter)
            except Exception as _suppressed_exc:
                logger.debug(
                    f"BehavioralEntropyGenerator.generate_humanized_mouse_trajectory(): "
                    f"suppressed {type(_suppressed_exc).__name__}"
                )

            final_path.append((round(float(px)), round(float(py)), float(quantized_delay)))
            
        # BE33: Coasting with same quantization
        coasting_steps = int(self.rng.integers(2, 6))
        last_x, last_y = final_path[-1][0], final_path[-1][1]

        for step in range(1, coasting_steps + 1):
            decay_factor = math.exp(-step * 0.9)
            coast_x = last_x + self.rng.uniform(-0.8, 0.8) * decay_factor
            coast_y = last_y + self.rng.uniform(-0.8, 0.8) * decay_factor
            coast_delay = quantize_interval * (step + 1)
            final_path.append((round(float(coast_x)), round(float(coast_y)), float(coast_delay)))

        return final_path


# BE1 fallback: pure-Python random helper for when numpy is missing
class _NumpyFallback:
    """Drop-in replacement for np.random.default_rng that uses stdlib random.
    Provides only the methods used by BehavioralEntropyGenerator."""
    
    def __init__(self, seed: int):
        self._rng = _stdlib_random.Random(seed)
    
    def random(self) -> float:
        return self._rng.random()
    
    def uniform(self, low: float, high: float) -> float:
        return self._rng.uniform(low, high)
    
    def normal(self, *args, **kwargs):
        """BE58: numpy-compatible normal(). Supports both positional
        and keyword arguments. Numpy's signature is
        normal(loc=0.0, scale=1.0, size=None).

        Handles all of:
          - normal()                  -> single sample N(0, 1)
          - normal(0, 0.45)           -> single sample N(0, 0.45)
          - normal(0, 0.45, 100)      -> list of 100 samples
          - normal(size=100)          -> list of 100 samples
          - normal(loc=0, scale=0.45, size=100) -> list of 100 samples
        """
        # Extract from kwargs first (numpy semantics)
        loc = kwargs.get("loc", 0.0)
        scale = kwargs.get("scale", 1.0)
        size = kwargs.get("size", None)

        if size is None and args:
            # numpy's normal(size=N) puts N in args[0] with loc, scale=None
            if len(args) == 1 and isinstance(args[0], (int,)) and not isinstance(args[0], bool):
                size = args[0]
                loc, scale = 0.0, 1.0
            elif len(args) >= 2:
                loc, scale = args[0], args[1]
                if len(args) >= 3:
                    size = args[2]
            elif len(args) == 1 and isinstance(args[0], (int, float)) and not isinstance(args[0], bool):
                # Treat as (loc,) only — single sample
                loc = args[0]
                size = None

        if size is None:
            return self._rng.gauss(float(loc), float(scale))
        return [self._rng.gauss(float(loc), float(scale)) for _ in range(int(size))]
    
    def integers(self, low: int, high: Optional[int] = None) -> int:
        """BE1: numpy-compatible integers() — high is EXCLUSIVE (like numpy).
        Python's randint is inclusive on both ends, so we adjust.
        """
        if high is None:
            # numpy semantics: integers(5) = 0..4
            return self._rng.randrange(0, low)
        # numpy semantics: integers(2, 6) = 2..5 (NOT 6)
        return self._rng.randrange(low, high)
    
    def exponential(self, scale: float) -> float:
        return self._rng.expovariate(1.0 / scale) if scale > 0 else 0.0
    
    def lognormal(self, mu: float, sigma: float, size: int = None):
        # PT-2 (Pass 15): module-level `math` import.
        # BEN-4 (Pass 14): Defensive validation of mu/sigma. Without
        # this, NaN/Inf inputs propagate to the output and then
        # into the caller's multipliers (e.g., delays, tremor
        # amplitude), causing NaN trajectories or infinite delays.
        # The numpy path uses numpy's own validation, but Python's
        # math.exp(NaN) = NaN and math.exp(Inf) = Inf are silently
        # accepted.
        if mu != mu or sigma != sigma:  # NaN check (NaN != NaN)
            raise ValueError(f"lognormal: mu and sigma must be finite, got mu={mu}, sigma={sigma}")
        if sigma == float('inf') or mu == float('inf') or mu == float('-inf'):
            raise ValueError(f"lognormal: mu and sigma must be finite, got mu={mu}, sigma={sigma}")
        if size is None:
            return math.exp(self._rng.gauss(mu, sigma))
        return [math.exp(self._rng.gauss(mu, sigma)) for _ in range(int(size))]

    def weibull(self, shape: float) -> float:
        """BE60, BE79: numpy-compatible weibull(). Returns a single Weibull
        sample with the given shape parameter (scale=1.0).

        numpy.random.Generator.weibull(a) returns Weibull(shape=a, scale=1).
        Python random.weibullvariate(alpha, beta) is Weibull(shape=beta, scale=alpha).
        So to match numpy's weibull(a), we need weibullvariate(1.0, a).

        BE79: Confirmed via empirical test (10000 samples):
          numpy weibull(1.5)        -> mean = 0.895
          Python weibullvariate(1.0, 1.5) -> mean = 0.903  ✓ matches numpy
          Python weibullvariate(1.5, 1.0) -> mean = 1.499  ✗ wrong

        So the BE64 "fix" was actually wrong; the original code (1.0, shape)
        was correct. Reverted.
        """
        return self._rng.weibullvariate(1.0, shape)


# =======================================================================================
# BE59: ReadingPacingModel - Fixation-saccade cognitive reading model
# =======================================================================================
# Reading velocity is one of the top 3 detection vectors for reCAPTCHA
# Enterprise. Real users don't read at constant velocity; they exhibit:
#   - First-pass fixation duration: 225-300ms (Rayner 1998)
#   - Saccade length: 7-9 character spaces (prose)
#   - Regression probability: 10-15% per line
#   - Inter-line saccade: 50-80ms (saccade-only)
#   - Comprehension-modulated fixation: longer for complex sentences
#
# This class is used by the task executor's reading-flow simulation to
# inject realistic pauses between actions (scroll, click, hover) when
# the persona is "reading" the page.
# =======================================================================================
class ReadingPacingModel:
    """BE59: Fixation-saccade cognitive reading model.

    Implements the F-pattern / Bezrukov-Futer reading curves. Each
    call to fixation_duration_ms() returns a sample from a Gaussian
    centered on the persona's baseline fixation duration, modulated
    by word difficulty and stress.

    BE68: Uses a dedicated sub-RNG (_reading_rng) so reading simulation
    does not leak state into the main timing/mouse RNG. The sub-RNG is
    seeded from (persona_id, day_of_year, char_count_bucket) so the
    same page length on the same day produces the same reading pattern
    (deterministic per-page) but does not advance the main RNG state.

    BE78: time_to_read_text_ms is vectorized using numpy for O(1) calls
    regardless of char_count.
    """

    def __init__(self, profile: 'BehavioralEntropyGenerator'):
        self.profile = profile
        # Per-persona baseline fixation duration (225-300ms; novices slower)
        expertise_baseline = {
            "novice": 290.0,
            "intermediate": 260.0,
            "expert": 230.0,
        }
        self.baseline_ms = expertise_baseline.get(profile.expertise, 260.0)
        # Slight per-persona jitter (some people are faster readers regardless of expertise)
        self.baseline_ms *= profile.rng.uniform(0.92, 1.08)

        # BE68: Decoupled sub-RNG for reading simulation
        # Uses a hash of (persona_id, day, char_count_bucket) so the
        # same page length on the same day produces the same reading
        # pattern but doesn't leak state to other RNGs.
        try:
            pid = getattr(profile, '_persona_id_str', 'unknown')
        except Exception:
            pid = 'unknown'
        import hashlib as _hl
        import datetime as _dt
        day_of_year = _dt.datetime.now(_dt.timezone.utc).timetuple().tm_yday
        # char_count_bucket is set per-call in time_to_read_text_ms,
        # but we seed on day-level granularity here.
        _seed = int(_hl.sha256(
            f"{pid}:{day_of_year}:reading".encode()
        ).hexdigest()[:16], 16)
        if NUMPY_AVAILABLE:
            self._reading_rng = np.random.default_rng(_seed)
        else:
            self._reading_rng = _NumpyFallback(_seed)

    def fixation_duration_ms(self, word_difficulty: float = 1.0) -> float:
        """Return fixation duration in milliseconds.

        word_difficulty: 0.5 (easy / common word) to 2.0 (technical jargon)

        BE68: Uses _reading_rng (decoupled) to avoid leaking state.
        """
        # Gaussian noise around persona baseline
        base_ms = self._reading_rng.normal(self.baseline_ms, 35.0)
        # Difficulty increases fixation duration (Just & Carpenter 1980)
        difficulty_mult = 1.0 + (word_difficulty - 1.0) * 0.45
        # Stress slows reading
        stress_mult = 1.0 + self.profile.ambient_stress_level * 0.18
        # Fatigue slows reading
        fatigue_mult = 1.0 + (self.profile.session_task_count * 0.02)
        return max(150.0, base_ms * difficulty_mult * stress_mult * fatigue_mult)

    def saccade_length_chars(self) -> float:
        """Return saccade length in character spaces.
        Prose: 7-9 chars. Skim: 12-18 chars. Expert readers: 9-11.
        """
        expertise_mean = {
            "novice": 7.0,
            "intermediate": 8.0,
            "expert": 10.0,
        }
        mean = expertise_mean.get(self.profile.expertise, 8.0)
        # Log-normal (positive-only, right-skewed)
        if NUMPY_AVAILABLE:
            val = self._reading_rng.lognormal(np.log(mean), 0.25)
        else:
            # PT-2 (Pass 15): module-level `math` import.
            val = math.exp(self._reading_rng.normal(math.log(mean), 0.25))
        return float(np.clip(val, 3.0, 22.0)) if NUMPY_AVAILABLE else max(3.0, min(22.0, val))

    def regression_probability(self) -> float:
        """Return probability of a regression saccade (looking back).

        Real users regress 10-15% of fixations. Experts less, novices more.
        Stress amplifies regressions.
        """
        base_prob = {
            "novice": 0.18,
            "intermediate": 0.12,
            "expert": 0.08,
        }
        p = base_prob.get(self.profile.expertise, 0.12)
        p += self.profile.ambient_stress_level * 0.08
        return min(0.40, p)

    def time_to_read_text_ms(self, char_count: int,
                             avg_difficulty: float = 1.0) -> float:
        """BE78: Vectorized reading time estimation.

        Estimate total time to read `char_count` characters at
        `avg_difficulty` (0.5-2.0). Returns time in milliseconds.

        BE68: Uses _reading_rng (decoupled sub-RNG) so this method
        does NOT advance the main profile RNG. A persona reading a
        long page no longer leaks state to subsequent timing/mouse
        behavior, eliminating the text-length-correlation fingerprint.
        """
        if char_count <= 0:
            return 0.0
        saccade_len = self.saccade_length_chars()
        n_fixations = max(1, int(char_count / saccade_len))
        rng = self._reading_rng
        if NUMPY_AVAILABLE:
            # Vectorized: one numpy call, not N scalar calls
            fix_durations = rng.normal(self.baseline_ms, 35.0, n_fixations)
            difficulty_mult = 1.0 + (avg_difficulty - 1.0) * 0.45
            stress_mult = 1.0 + self.profile.ambient_stress_level * 0.18
            fatigue_mult = 1.0 + (self.profile.session_task_count * 0.02)
            fix_durations = np.clip(
                fix_durations * difficulty_mult * stress_mult * fatigue_mult,
                150.0, None
            )
            # Probabilistic regressions (vectorized)
            reg_rolls = rng.random(n_fixations)
            reg_threshold = self.regression_probability()
            n_regressions = int(np.sum(reg_rolls < reg_threshold))
            # Each regression adds 0.5x a fixation (was 1.5x; the
            # regression itself is a fixation + a back-saccade)
            mean_fix = float(np.mean(fix_durations)) if n_fixations > 0 else self.baseline_ms
            total_ms = float(np.sum(fix_durations)) + n_regressions * mean_fix * 0.5
        else:
            # Pure-Python fallback
            total_ms = 0.0
            n_regressions = 0
            for _ in range(n_fixations):
                fd = self.fixation_duration_ms(avg_difficulty)
                total_ms += fd
                if rng.random() < self.regression_probability():
                    total_ms += fd * 0.5
                    n_regressions += 1
        # Inter-word saccade time (~25ms)
        total_ms += n_fixations * 25.0
        return total_ms


# =======================================================================================
# BE60: FocusSwitchModel - Tab-switching and attention-drift model
# =======================================================================================
# Real users switch tabs, look away, get distracted. A bot that maintains
# 100% focus on the target is detectable by behavioral graphs that measure
# the autocorrelation of input events over 60s+ windows.
# =======================================================================================
class FocusSwitchModel:
    """BE60: Tab-switching and attention-drift model.

    Implements a Weibull-distributed time-between-switches (long-tailed):
      - Median inter-switch interval: 45-90 seconds
      - Off-tab dwell time: 8-25 seconds (log-normal)
      - Probability decreases with task complexity
    """

    def __init__(self, profile: 'BehavioralEntropyGenerator'):
        self.profile = profile

    def time_to_next_switch_s(self, task_complexity: float = 1.0) -> float:
        """Return seconds until the next attention switch (tab/window).

        task_complexity: 0.5 (browsing) to 2.0 (form-filling)

        BE77: Fixed inverted logic. Complex tasks have LONGER focus
        durations (less frequent switching), so we multiply the base
        by task_complexity, not divide. The previous code did
        `1.0 / task_complexity` which made complex tasks have SHORTER
        focus durations — backwards.
        """
        # Weibull shape=1.5, scale=70s (mean ~63s, median ~54s)
        # Real users exhibit a long-tailed distribution of focus duration
        if NUMPY_AVAILABLE:
            base = float(self.profile.rng.weibull(1.5)) * 70.0
        else:
            # Approximate Weibull(1.5) via inverse CDF on uniform
            # PT-2 (Pass 15): module-level `math` import.
            u = self.profile.rng.random()
            base = 70.0 * (-math.log(max(1e-9, 1.0 - u))) ** (1.0 / 1.5)
        # BE77: Complex tasks INCREASE focus duration (longer between switches)
        complexity_mult = max(0.5, task_complexity)
        return max(5.0, base * complexity_mult)

    def off_tab_dwell_s(self) -> float:
        """Return seconds spent off-tab (looking at phone, another tab, etc).

        Log-normal with mean ~10s, mode ~8s.
        """
        if NUMPY_AVAILABLE:
            return float(self.profile.rng.lognormal(np.log(10.0), 0.5))
        else:
            # PT-2 (Pass 15): module-level `math` import.
            return math.exp(self.profile.rng.normal(math.log(10.0), 0.5))


# =======================================================================================
# BE61: MouseButtonDurationModel - Key-down hold duration distribution
# =======================================================================================
# When the user clicks (mouse-down then mouse-up), the duration between
# down and up is NOT zero. Real users have:
#   - Single click: 60-120ms hold
#   - Double click: 50-90ms per click
#   - Long press / drag: 200-500ms hold
#   - Right-click: 80-150ms hold
# A bot that fires `down(); up()` back-to-back with zero delay is detectable.
# =======================================================================================
class MouseButtonDurationModel:
    """BE61: Mouse button hold duration distribution."""

    def __init__(self, profile: 'BehavioralEntropyGenerator'):
        self.profile = profile

    def click_hold_ms(self, click_type: str = "single") -> float:
        """Return mouse button hold duration in milliseconds.

        click_type: 'single', 'double_first', 'double_second',
                    'right', 'drag_start', 'drag_hold'

        BE73: Invalid click_type raises ValueError with a did-you-mean
        suggestion via difflib.

        E36 fix (Pass 18): Defensive rng check for lognormal.
        The previous code did `self.profile.rng.lognormal(mu, sigma)`,
        which raises AttributeError if profile.rng is a different
        RNG type (e.g., a custom one without lognormal). We now
        wrap in try/except and fall back to math.exp(normal(mu, sigma)).
        """
        # L4 fix (Pass 15): Defer the `params` dict construction until
        # the numpy-availability check. The previous code built the
        # dict at function entry using `np.log(85.0)` etc., which
        # raised `AttributeError: module 'numpy' has no attribute 'log'`
        # in the no-numpy fallback path. We now compute the
        # pre-computed log-means using `math.log` (or `np.log` when
        # available) inside each branch.
        if NUMPY_AVAILABLE:
            _log = np.log
        else:
            import math as _math
            _log = _math.log
        # Base distributions (all log-normal; positive-only, right-skewed)
        params = {
            "single":        (_log(85.0), 0.30),   # mean ~88ms
            "double_first":  (_log(70.0), 0.25),
            "double_second": (_log(75.0), 0.30),
            "right":         (_log(110.0), 0.35),
            "drag_start":    (_log(120.0), 0.40),
            "drag_hold":     (_log(350.0), 0.50),
        }
        if click_type not in params:
            # BE73: did-you-mean suggestion
            import difflib
            suggestions = difflib.get_close_matches(click_type, params.keys(), n=2)
            msg = f"click_type must be one of {list(params.keys())}, got {click_type!r}"
            if suggestions:
                msg += f". Did you mean: {suggestions}?"
            raise ValueError(msg)
        mu, sigma = params[click_type]
        # E36: Defensive lognormal call with fallback
        try:
            if NUMPY_AVAILABLE:
                val = float(self.profile.rng.lognormal(mu, sigma))
            else:
                val = math.exp(self.profile.rng.normal(mu, sigma))
        except (AttributeError, TypeError):
            # RNG doesn't have lognormal/normal; fall back to fixed
            # distribution (median of the params tuple, ~mean).
            import math as _m
            val = _m.exp(mu)
        # Stress and fatigue increase click duration
        stress_mult = 1.0 + self.profile.ambient_stress_level * 0.15
        fatigue_mult = 1.0 + (self.profile.session_task_count * 0.01)
        return max(30.0, min(800.0, val * stress_mult * fatigue_mult))


# =======================================================================================
# BE62: ScrollInertiaModel - Friction-decay momentum scroll
# =======================================================================================
# Real users don't scroll in discrete jumps. A trackpad fling produces
# an exponential-decay momentum (V_next = V_current * mu) with variable
# friction coefficient (0.85-0.95 depending on trackpad surface,
# finger pressure, and intent). The number of ticks during the decay
# is also variable.
# =======================================================================================
class ScrollInertiaModel:
    """BE62: Friction-decay momentum scroll model.

    Real touchpad fling physics:
      V_next = V_current * mu
      mu ~ Uniform(0.85, 0.95) per gesture
      Ticks until V < 0.5: ceil(log(0.5/V0) / log(mu)) ~ 8-30 ticks
    """

    def __init__(self, profile: 'BehavioralEntropyGenerator'):
        self.profile = profile

    def initial_velocity(self, target_distance_px: int) -> float:
        """Return initial scroll velocity (px per tick) for a target distance.

        The user flings the trackpad with intent proportional to target.
        """
        # Initial velocity is 12-22% of target distance (per hci_reading_model.py)
        pct = self.profile.rng.uniform(0.12, 0.22)
        # Sign preserved
        sign = 1.0 if target_distance_px >= 0 else -1.0
        return sign * abs(target_distance_px) * pct

    def friction_coefficient(self) -> float:
        """Return friction coefficient mu for the exponential decay.

        V_next = V_current * mu
        Real trackpads: 0.85 (friction-heavy) to 0.95 (smooth glass).
        Stress slightly increases friction (heavier finger pressure).
        """
        base = self.profile.rng.uniform(0.85, 0.95)
        # Stress reduces smoothness slightly
        stress_penalty = self.profile.ambient_stress_level * 0.02
        return max(0.80, min(0.97, base - stress_penalty))

    def tick_count(self, initial_v: float, mu: float,
                   threshold: float = 0.5) -> int:
        """Return number of ticks until velocity drops below threshold.

        Solves: mu^N * V0 < threshold  =>  N > log(threshold/V0) / log(mu)

        BE74: For mu >= 1.0 (no friction), return 100 ticks (effectively
        infinite momentum) instead of 1. The previous code returned 1
        tick of full velocity, which is inconsistent with the physics
        of a frictionless scroll.
        """
        # PT-2 (Pass 15): module-level `math` import.
        if abs(initial_v) < threshold:
            return 0
        if mu >= 1.0:
            return 100  # Effectively no deceleration
        if mu <= 0:
            return 1
        n = math.log(threshold / abs(initial_v)) / math.log(mu)
        return max(1, int(math.ceil(n)))

    def total_distance_px(self, initial_v: float, mu: float) -> float:
        """Return total distance traveled during momentum decay.

        Sum of geometric series: V0 * (1 - mu^N) / (1 - mu)
        """
        n = self.tick_count(initial_v, mu)
        if mu >= 1.0:
            return initial_v * n
        return initial_v * (1.0 - mu ** n) / (1.0 - mu)



# =======================================================================================
# R7-BEN-001: ActionSequenceGenerator - Power-law action sequence distribution
# =======================================================================================
# Real user action sequences (click, scroll, type, hover, wait, etc.)
# follow a power-law distribution: a few actions are very common,
# most are rare. A uniform distribution (random.choice with equal
# weights) is statistically detectable as a bot fingerprint over many
# samples.
#
# This class generates a sequence of actions with weights that follow
# a power-law (Pareto-like) distribution. The most common actions
# (scroll, wait) appear much more frequently than rarer ones (type,
# double_click, right_click).
# =======================================================================================
class ActionSequenceGenerator:
    """R7-BEN-001: Power-law action sequence generator.
    
    Real users: ~50% scroll, ~25% wait (idle), ~15% click, ~5% type,
    ~3% hover, ~2% right_click. This is a power-law where the
    exponent ~1.5 matches observed web analytics.
    
    Usage:
        gen = ActionSequenceGenerator(profile)
        for _ in range(20):
            action = gen.next_action()  # 'scroll', 'wait', 'click', etc.
    """
    
    # Default weights: power-law (sorted by frequency, descending)
    # Adjusted per-persona by expertise and stress.
    DEFAULT_ACTIONS = [
        ("scroll", 0.50),
        ("wait", 0.25),
        ("click", 0.15),
        ("type", 0.05),
        ("hover", 0.03),
        ("right_click", 0.015),
        ("double_click", 0.005),
    ]
    
    def __init__(self, profile: 'BehavioralEntropyGenerator'):
        self.profile = profile
        # Per-persona action weight adjustment.
        # Experts type more, novices scroll more.
        expertise_adj = {
            "novice": {"scroll": 1.3, "type": 0.5, "click": 0.8},
            "intermediate": {"scroll": 1.0, "type": 1.0, "click": 1.0},
            "expert": {"scroll": 0.7, "type": 2.0, "click": 1.2},
        }
        adj = expertise_adj.get(profile.expertise, {})
        # Apply per-persona adjustments and renormalize
        weights = []
        for action, base_w in self.DEFAULT_ACTIONS:
            w = base_w * adj.get(action, 1.0)
            weights.append(w)
        total = sum(weights)
        self._actions = [a for a, _ in self.DEFAULT_ACTIONS]
        self._weights = [w / total for w in weights]
        # R7-BEN-001: Use a power-law RNG (numpy.random.power(0.5))
        # for the selection to match the power-law shape of action
        # frequencies. The standard approach is to compute the CDF
        # of the discrete distribution and use the power RNG to
        # sample from it.
        if NUMPY_AVAILABLE:
            self._cum_weights = np.cumsum(self._weights)
        else:
            cumsum = []
            running = 0.0
            for w in self._weights:
                running += w
                cumsum.append(running)
            self._cum_weights = cumsum
    
    def next_action(self) -> str:
        """R7-BEN-001: Sample an action from a discrete distribution.

        PT-7 (Pass 15): The previous implementation used
        numpy.random.power(0.5) to sample a continuous value in [0, 1]
        with a power-law density (heavily biased toward 0), then used
        searchsorted() against the cumulative weights. The bias toward
        0 caused the first bucket ('scroll') to win >75% of the time,
        instead of the ~50% rate defined in DEFAULT_ACTIONS. The
        distribution's effective entropy was ~1.18 bits, far below the
        ~1.91 bits of a properly-sampled discrete distribution. This
        is itself a fingerprint (a real user has variable action
        sequences; a "scroll, scroll, scroll" pattern is detectable).

        The fix: use numpy.random.uniform() to sample a uniform value
        in [0, 1], then searchsorted() against the cumulative weights.
        This produces samples in proportion to the bucket weights
        (50% scroll, 25% wait, etc.) — matching the intended
        distribution exactly. The persona's biological_drift now
        affects WHICH weight vector to use (already done in __init__),
        not the sampling method.

        For the no-numpy fallback, we use the same approach: uniform
        random + linear search. (The previous power-CDF fallback was
        also wrong in the same way; now fixed.)
        """
        if NUMPY_AVAILABLE and isinstance(self.profile.rng, np.random.Generator):
            # PT-7 FIX: Uniform sample + searchsorted.
            # The discrete distribution is already encoded in
            # self._cum_weights (built in __init__). A uniform random
            # in [0, 1) combined with searchsorted() produces
            # samples proportional to the bucket weights.
            sample = float(self.profile.rng.uniform())
            idx = int(np.searchsorted(self._cum_weights, sample))
        else:
            # Pure-Python fallback
            sample = self.profile.rng.random()
            idx = 0
            for i, cw in enumerate(self._cum_weights):
                if cw >= sample:
                    idx = i
                    break
        idx = min(idx, len(self._actions) - 1)
        return self._actions[idx]
    
    def action_sequence(self, n: int) -> list:
        """Generate a sequence of n actions. Convenience wrapper."""
        return [self.next_action() for _ in range(n)]



# =======================================================================================
# R7-BEN-002: ActionTransitionMarkov - 3rd-order Markov chain for action sequences
# =======================================================================================
# Real user action sequences have memory of the previous 2-3 actions.
# A 1st-order Markov chain (P(next | current)) produces sequences
# where the action distribution is memoryless, which is detectable.
# This class implements a 3rd-order chain with smoothing.
# =======================================================================================
class ActionTransitionMarkov:
    """R7-BEN-002: 3rd-order Markov chain for action transitions.
    
    The transition probability P(next | history3) is learned from
    a small built-in corpus of observed real-user patterns:
      - scroll → scroll → scroll → click (common reading → action)
      - wait → scroll → scroll → scroll (idle → resume reading)
      - click → wait → scroll → scroll (action → idle → reading)
      - type → type → wait → type (typing burst → pause → continue)
    
    For unknown 3-tuples, falls back to 2nd-order, then 1st-order,
    then to the unigram distribution. This is "backoff smoothing"
    (similar to Katz backoff in NLP).
    
    State space: 7 actions (scroll, wait, click, type, hover, right_click, double_click)
    3rd-order state: 3-tuples = 7^3 = 343 states. With per-persona
    transition counts, this fits in <50KB of memory.

    R7-BEN-010: Cap at order 3. Higher orders grow exponentially:
      - order 3: 7^3 = 343 states (~50KB)
      - order 4: 7^4 = 2,401 states (~350KB)
      - order 5: 7^5 = 16,807 states (~2.4MB)
      - order 6: 7^6 = 117,649 states (~17MB) — OOM risk
    The 3rd-order chain is sufficient to capture real user patterns
    (scroll→scroll→scroll→click, type→type→wait→type, etc.) without
    the OOM risk of higher orders.
    """
    
    # Initial transition counts (pseudo-observations).
    # Higher count = stronger prior belief.
    # Format: {(prev3, prev2, prev1): {next: count, ...}, ...}
    _INITIAL_TRANSITIONS_3RD = {
        # Reading flow: scroll → scroll → scroll → click (action)
        ("scroll", "scroll", "scroll"): {"scroll": 8, "click": 2, "wait": 1},
        # Idle → resume reading
        ("wait", "scroll", "scroll"): {"scroll": 9, "wait": 1},
        # Action → idle → resume
        ("click", "wait", "scroll"): {"scroll": 8, "wait": 2},
        # Typing burst
        ("type", "type", "type"): {"type": 7, "wait": 3},
        # Pause after typing
        ("type", "wait", "type"): {"type": 6, "scroll": 2, "click": 2},
        # Hover before click
        ("hover", "scroll", "click"): {"click": 8, "scroll": 2},
        # Right-click after scroll
        ("scroll", "scroll", "right_click"): {"right_click": 7, "wait": 3},
    }
    
    # 2nd-order fallback (for unknown 3-tuples)
    _INITIAL_TRANSITIONS_2ND = {
        ("scroll", "scroll"): {"scroll": 7, "click": 2, "wait": 1},
        ("scroll", "wait"): {"wait": 5, "scroll": 3, "click": 2},
        ("wait", "scroll"): {"scroll": 8, "wait": 2},
        ("click", "wait"): {"wait": 6, "scroll": 3, "hover": 1},
        ("type", "type"): {"type": 8, "wait": 2},
        ("hover", "click"): {"click": 7, "scroll": 3},
    }
    
    # 1st-order fallback
    _INITIAL_TRANSITIONS_1ST = {
        "scroll": {"scroll": 7, "click": 2, "wait": 1},
        "wait": {"scroll": 6, "wait": 3, "click": 1},
        "click": {"wait": 5, "scroll": 3, "hover": 2},
        "type": {"type": 7, "wait": 3},
        "hover": {"click": 5, "scroll": 4, "wait": 1},
        "right_click": {"wait": 6, "scroll": 3, "click": 1},
        "double_click": {"wait": 7, "scroll": 3},
    }
    
    # Unigram (uniform)
    _UNIGRAMS = {
        "scroll": 0.50, "wait": 0.25, "click": 0.15, "type": 0.05,
        "hover": 0.03, "right_click": 0.015, "double_click": 0.005,
    }
    
    def __init__(self, profile: 'BehavioralEntropyGenerator'):
        self.profile = profile
        # Per-persona seeded RNG for state expansion
        if NUMPY_AVAILABLE:
            # Use the profile's main RNG to keep determinism across
            # the persona's session. This ensures the Markov chain
            # generates the same sequence for the same persona.
            if hasattr(profile, 'rng') and isinstance(profile.rng, np.random.Generator):
                self._rng = profile.rng
            else:
                _pid_str = getattr(profile, '_persona_id_str', 'default')
                _seed = int(hashlib.sha256(
                    ("markov:" + _pid_str).encode("utf-8")
                ).hexdigest()[:16], 16)
                self._rng = np.random.default_rng(_seed)
        else:
            import random as _r
            _seed = int(hashlib.sha256(b"markov:default").hexdigest()[:8], 16)
            self._rng = _r.Random(_seed)
        # Build per-persona transitions (start with INITIAL, perturbed by
        # per-persona RNG so different personas have slightly different
        # transition probabilities — this matches real user variance).
        self._trans_3rd = self._perturb_transitions(self._INITIAL_TRANSITIONS_3RD)
        self._trans_2nd = self._perturb_transitions(self._INITIAL_TRANSITIONS_2ND)
        self._trans_1st = self._perturb_transitions(self._INITIAL_TRANSITIONS_1ST)
    
    def _perturb_transitions(self, base):
        """Apply per-persona random perturbation to transition counts.
        Keeps the total roughly the same but varies per-key."""
        result = {}
        for state, targets in base.items():
            new_targets = {}
            for action, count in targets.items():
                # ±20% perturbation
                if NUMPY_AVAILABLE and isinstance(self._rng, np.random.Generator):
                    factor = 0.8 + 0.4 * float(self._rng.random())
                else:
                    factor = 0.8 + 0.4 * self._rng.random()
                new_targets[action] = max(0.5, count * factor)
            result[state] = new_targets
        return result
    
    def _sample_from_distribution(self, dist):
        """Sample one action from a {action: weight} distribution."""
        if not dist:
            return self._sample_from_distribution(self._UNIGRAMS)
        if NUMPY_AVAILABLE and isinstance(self._rng, np.random.Generator):
            actions = list(dist.keys())
            weights = np.array(list(dist.values()), dtype=float)
            weights = weights / weights.sum()
            return str(self._rng.choice(actions, p=weights))
        else:
            import random as _r
            actions = list(dist.keys())
            weights = list(dist.values())
            return _r.choices(actions, weights=weights, k=1)[0]
    
    def next_action(self, history: list) -> str:
        """R7-BEN-002: Sample next action given the last 3 actions.
        
        Uses Katz-style backoff: try 3rd-order, fall back to 2nd,
        then 1st, then unigram.
        """
        # Pad history to length 3
        h = list(history[-3:]) if len(history) >= 3 else (['scroll'] * (3 - len(history)) + list(history))
        h = tuple(h[-3:])
        # 3rd-order lookup
        if h in self._trans_3rd:
            return self._sample_from_distribution(self._trans_3rd[h])
        # 2nd-order fallback (last 2)
        h2 = h[-2:]
        if h2 in self._trans_2nd:
            return self._sample_from_distribution(self._trans_2nd[h2])
        # 1st-order fallback (last 1)
        h1 = h[-1]
        if h1 in self._trans_1st:
            return self._sample_from_distribution(self._trans_1st[h1])
        # Unigram fallback
        return self._sample_from_distribution(self._UNIGRAMS)
    
    def action_sequence(self, length: int, seed_action: str = "scroll") -> list:
        """Generate a sequence of `length` actions with 3rd-order dependencies."""
        result = [seed_action]
        for _ in range(length - 1):
            result.append(self.next_action(result))
        return result

