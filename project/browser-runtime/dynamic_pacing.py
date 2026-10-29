# dynamic_pacing.py - Dynamic Cognitive Delay and Navigation Pacing Engine
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   DYN-1 - record_load_latency is now called by the orchestrator after
#           every page.goto(). Previously the latency_scale factor was
#           always 1.0 (default), making the "Environment Adaptation"
#           feature dead code.
#   DYN-2 - execute_natural_reading_flow now uses hci_reading_model's
#           execute_smooth_momentum_scroll (persona-aware momentum
#           model) instead of page.mouse.wheel (which triggers OS
#           accessibility events and ignores the persona).
#   DYN-3 - execute_natural_reading_flow scroll_count is now sampled
#           from an exponential distribution (median 3, max 8) instead
#           of a hardcoded 3. Real users vary widely in reading depth.
#   DYN-4 - Mouse jitter during long pauses now uses Gaussian
#           continuous noise (sigma=0.7px) instead of discrete ±1px
#           steps. Sub-pixel drift is more realistic.
#   DYN-5 - target_wpm is now used as an inverse multiplier on
#           base_delay (slower WPM = longer delays). Previously dead.

import asyncio
import random
import time
import numpy as np
from loguru import logger

class DynamicPacingEngine:
    """DC-19 (Pass 19): Adaptive pacing engine that scales delays based on context.

    Real users don't have uniform timing — they type slower when thinking,
    faster when transcribing, pause longer after slow page loads, etc. This
    engine tracks page load latency and adjusts all subsequent delays to
    match the persona's cognitive state. A user on a slow connection (2s
    page load) has longer inter-action delays than one on fiber (200ms).

    The engine is also used to scale delays per-persona by their WPM
    (words-per-minute) profile, so slow typers get longer typing delays
    and quick typers get shorter ones.
    """
    def __init__(self, target_wpm: int = 70):
        self.target_wpm = target_wpm
        # DYN-5: Compute the WPM-to-delay scaling factor. 70 WPM is
        # baseline (1.0x), slower WPM = longer delays.
        # 200 WPM → 0.35x delay; 50 WPM → 1.4x delay.
        self._wpm_scale = max(0.3, min(1.5, 70.0 / max(1, self.target_wpm)))
        # 1.6 Environment Adaptation: Track historical page navigation and load latencies
        self.last_load_latency = 1.0 # Default baseline latency in seconds

    async def record_load_latency(self, start_time: float):
        """Measures and updates active page loading latency to scale cognitive pacing.
        DYN-1: Now called by the orchestrator after every page.goto() to
        actually drive the latency_scale factor in sleep_human_delay().
        """
        try:
            load_latency = time.perf_counter() - float(start_time)
        except (TypeError, ValueError):
            # E18 fix (Pass 18): Coerce to valid float on bad input.
            logger.warning(
                f"DYN-E18: record_load_latency called with invalid "
                f"start_time={start_time!r}. Using baseline 1.0s."
            )
            load_latency = 1.0
        self.last_load_latency = max(0.200, load_latency)
        logger.info(f"Pacing Adaptation: Page load latency resolved to {self.last_load_latency:.3f}s. Scaling pacing factors.")

    async def sleep_human_delay(self, base_delay: float, variance_factor: float = 0.35, page=None):
        """
        Generates delay intervals simulating authentic human operational pacing.

        1.6 Environmental Adaptation Upgrade:
          - Latency-Scaled Cognitive Pauses: Dynamically scales pacing delays proportionally
            to historical page load latency (higher latencies = slower, more cautious pacing).
          - Ocular Microsaccades: Runs 1-2px cursor drifts during long cognitive pauses.

        DYN-4: Mouse jitter is now continuous Gaussian (sigma=0.7px), not
        discrete ±1px steps. Real cursor jitter is sub-pixel.
        DYN-5: target_wpm now scales the base_delay.
        """
        persona_id = "generic_bot"
        if page is not None:
            persona_id = getattr(page.context, "persona_id", "generic_bot")

        from humanized_movement import get_muscle_profile
        profile = get_muscle_profile(persona_id)

        # Scale base delay according to active load latency (capped at 4x delay for absolute security)
        latency_scale = np.clip(1.0 + (self.last_load_latency - 1.0) * 0.35, 0.70, 4.00)
        # DYN-5: Apply WPM scaling (slower readers wait longer)
        adapted_base_delay = base_delay * latency_scale * self._wpm_scale

        mu = adapted_base_delay * 0.70
        sigma = adapted_base_delay * 0.10
        tau = adapted_base_delay * 0.30

        fatigue_factor = 1.0 + (getattr(profile, "session_task_count", 0) * 0.04)
        mu *= fatigue_factor
        tau *= fatigue_factor

        # BE54: Use the decoupled pacing_rng instead of profile.rng.
        # This prevents pacing calls from advancing the same RNG state
        # used by generate_humanized_mouse_trajectory and
        # calculate_log_normal_delays. Without this, the auto-correlation
        # function of the tremor stream has a step at each pacing boundary
        # (detectable by behavioral graphs).
        pacing_rng = getattr(profile, "pacing_rng", None) or profile.rng
        # R7-DYN-006: Store the persona's pacing_rng on the instance so
        # subsequent calls (e.g., the mouse-jitter loop below) can use
        # the same seeded RNG for deterministic per-persona behavior.
        self._last_pacing_rng = pacing_rng
        normal_part = pacing_rng.normal(mu, sigma)
        exponential_part = pacing_rng.exponential(tau)
        final_delay = max(adapted_base_delay * 0.3, normal_part + exponential_part)

        logger.info(f"Pacing: Emulating Ex-Gaussian pause of {final_delay:.3f}s (latency scaled from {base_delay}s by {latency_scale:.2f}x, WPM scale {self._wpm_scale:.2f}x).")

        if page is not None and final_delay > 1.0:
            from humanized_movement import move_mouse_like_human
            elapsed = 0.0
            while elapsed < final_delay:
                chunk = min(0.500, final_delay - elapsed)
                await asyncio.sleep(chunk)
                elapsed += chunk

                try:
                    # DYN-4: Use continuous Gaussian jitter instead of
                    # discrete ±1px steps. Real cursor drift is sub-pixel.
                    # R7-DYN-006: Use persona's pacing_rng (already fetched
                    # below) for jitter. Falls back to global if not available.
                    start_x = getattr(page, "_sre_mouse_x", 960)
                    start_y = getattr(page, "_sre_mouse_y", 540)
                    _pacing_rng = self._last_pacing_rng if hasattr(self, "_last_pacing_rng") else random
                    jitter_x = round(start_x + _pacing_rng.gauss(0, 0.7))
                    jitter_y = round(start_y + _pacing_rng.gauss(0, 0.7))
                    await move_mouse_like_human(page, start_x, start_y, jitter_x, jitter_y, steps=2)
                except Exception as _suppressed_exc:
                    logger.debug(
                        f"DynamicPacingEngine.sleep_human_delay(): suppressed {type(_suppressed_exc).__name__}"
                    )
        else:
            await asyncio.sleep(final_delay)

    async def execute_natural_reading_flow(self, page, scroll_selector: str = "body", scroll_count: int = 3):
        """
        Simulates an active user reading content: scrolling, pausing, hovering, and looking around.

        DYN-2: Uses execute_smooth_momentum_scroll from hci_reading_model
        (persona-aware exponential-decay momentum) instead of page.mouse.wheel
        (which fires raw wheel events with no persona).

        DYN-3: scroll_count is now sampled from exponential distribution
        (median 3, range 1-8) instead of hardcoded 3. Real users vary
        in reading depth.

        R7-DYN-006: All random samples (scroll_count, step_distance, read_delay,
        mouse jitter) now use the persona's pacing_rng instead of the global
        random module. Without this, two different personas running the same
        task would see correlated scroll/reading values (since the global
        random state is shared), which is a behavioral fingerprinting leak.

        E20 fix (Pass 17): Validate page is not None. The previous
        code assumed `page` is always a valid Playwright Page object.
        A None page (e.g., if the browser failed to launch but the
        executor still called this function) would crash with a
        confusing AttributeError at `page.context.persona_id`. We
        now return early with a warning if page is None.
        """
        if page is None:
            logger.warning(
                "DYN-E20: execute_natural_reading_flow called with page=None. "
                "Skipping reading flow."
            )
            return
        from humanized_movement import get_muscle_profile, move_mouse_like_human
        # R7-DYN-006: Get the persona's profile for seeded RNG
        persona_id = getattr(page.context, "persona_id", "generic_bot") if page is not None else "generic_bot"
        profile = get_muscle_profile(persona_id)
        pacing_rng = getattr(profile, "pacing_rng", None) or profile.rng
        # DYN-3: Variable scroll count. Skimmers do 1-2 scrolls, deep
        # readers do 5-8. Exponential distribution with median 3.
        actual_scroll_count = max(1, min(8, int(pacing_rng.exponential(3))))
        if actual_scroll_count != scroll_count:
            logger.debug(
                f"DYN-3: Adjusted scroll_count from {scroll_count} to {actual_scroll_count} "
                f"(exponential distribution, persona-dependent)"
            )
        logger.info(f"Pacing: Commencing natural page reading and navigation flow simulation ({actual_scroll_count} scrolls).")
        for i in range(actual_scroll_count):
            # R7-DYN-006: Use persona's pacing_rng for scroll distance
            step_distance = int(pacing_rng.uniform(180, 450))
            # DYN-2: Use persona-aware momentum scroll from hci_reading_model
            # instead of page.mouse.wheel. The hci_reading_model version
            # uses the ScrollInertiaModel (friction-decay physics) which
            # varies by persona's biological_drift and ambient_stress.
            try:
                from hci_reading_model import execute_smooth_momentum_scroll
                await execute_smooth_momentum_scroll(page, step_distance)
            except ImportError:
                # Fallback if hci_reading_model not available
                await page.mouse.wheel(0, step_distance)

            # R7-DYN-006: Use persona's pacing_rng for read delay
            read_delay = float(pacing_rng.uniform(2.5, 6.0))
            await self.sleep_human_delay(read_delay, page=page)

            try:
                start_x = getattr(page, "_sre_mouse_x", 960)
                start_y = getattr(page, "_sre_mouse_y", 540)
                # R7-DYN-006: Use persona's pacing_rng for mouse jitter
                adjust_x = start_x + int(pacing_rng.uniform(-15, 15))
                adjust_y = start_y + int(pacing_rng.uniform(-15, 15))
                await move_mouse_like_human(page, start_x, start_y, adjust_x, adjust_y, steps=8)
                await asyncio.sleep(float(pacing_rng.uniform(0.15, 0.45)))
            except Exception as _suppressed_exc:
                logger.debug(
                    f"DynamicPacingEngine.execute_natural_reading_flow(): suppressed {type(_suppressed_exc).__name__}"
                )

        logger.info("Pacing: Dynamic page reading completed successfully.")
        await self.sleep_human_delay(1.5, page=page)



async def simulate_idle_pattern(page, persona_id: str, total_duration_s: float = 300.0):
    """CHAMELEON (GAP-6.4): Simulate realistic user idle patterns.

    Real users have bursts of activity interspersed with idle periods:
      - Active period: 30-180s (typing, scrolling, clicking)
      - Idle period: 0-90s (exponential distribution, mean 30s)
      - Tab background: 1-5 minutes (page in background, no input)
      - Micro-pauses: 0.5-3s between actions (between typing and clicking)

    This function:
      1. Picks an active/idle pattern per persona
      2. During idle, just waits (no input)
      3. During active, does mouse jiggle to simulate focus
      4. Occasionally simulates tab backgrounding (longer pauses)

    Args:
        page: Playwright page object
        persona_id: Persona UUID for seeded randomness
        total_duration_s: How long to run the idle pattern
    """
    import hashlib
    # Per-persona seeded RNG for reproducibility.
    # R7-DEEP-AUDIT-2 GAP 7.6: Use SHA-256 for consistency with the
    # rest of the codebase (other modules use SHA-256 truncated to
    # 32 bits). MD5 is deprecated on FIPS-mode systems and may be
    # unavailable.
    _idle_seed = int(hashlib.sha256(f"idle:{persona_id}".encode()).hexdigest()[:8], 16)
    _idle_rng = random.Random(_idle_seed)

    elapsed = 0.0
    # Track cursor position for jiggle
    _last_x = 960
    _last_y = 540
    try:
        _last_x = getattr(page, "_sre_mouse_x", 960)
        _last_y = getattr(page, "_sre_mouse_y", 540)
    except Exception as _suppressed_exc:
        logger.debug(f"simulate_idle_pattern(): suppressed {type(_suppressed_exc).__name__}")

    while elapsed < total_duration_s:
        # Active period: 30-180s
        active_period = _idle_rng.uniform(30, 180)
        # Idle period: exponential distribution (mean 30s, max 90s)
        idle_period = min(90.0, _idle_rng.expovariate(1.0/30.0))
        # E19 fix (Pass 17): Cap active_period to remaining time.
        # The previous code used the full active_period (30-180s)
        # regardless of how much time was left. For a persona with
        # `total_duration_s=300` and elapsed=250, the loop would
        # enter an "active period" of up to 180s, then exit. But
        # inside the active loop, each iteration sleeps 5-30s, so
        # the loop may run 1+ times (5-30s each) before exiting.
        # The bug was: if the active_iterations loop ran for 180s
        # when only 50s remained, the function would block for 180s
        # (capped by total_duration_s - elapsed only inside the
        # inner loop), but then the subsequent idle_period sleep
        # would also attempt to run. We now cap the active_period
        # to remaining time, mirroring the idle_period behavior.
        remaining = total_duration_s - elapsed
        if remaining <= 0:
            break
        active_period = min(active_period, remaining)

        # During active period, occasionally do a mouse jiggle
        # to simulate the user moving the cursor without clicking
        _active_iterations = max(1, int(active_period / 30))
        for _ in range(_active_iterations):
            if elapsed >= total_duration_s:
                break
            # Wait 5-30s
            _wait_s = _idle_rng.uniform(5, 30)
            _wait_s = min(_wait_s, total_duration_s - elapsed)
            if _wait_s <= 0:
                break
            await asyncio.sleep(_wait_s)
            elapsed += _wait_s
            # Mouse jiggle (no click)
            try:
                from humanized_movement import move_mouse_like_human
                _jitter_x = _last_x + _idle_rng.randint(-50, 50)
                _jitter_y = _last_y + _idle_rng.randint(-30, 30)
                _jitter_x = max(100, min(1800, _jitter_x))
                _jitter_y = max(100, min(1000, _jitter_y))
                await move_mouse_like_human(
                    page, _last_x, _last_y, _jitter_x, _jitter_y, steps=8
                )
                _last_x = _jitter_x
                _last_y = _jitter_y
            except Exception as _suppressed_exc:
                logger.debug(f"simulate_idle_pattern(): suppressed {type(_suppressed_exc).__name__}")

        # Idle period: no input (simulates user reading, watching video, etc.)
        _idle_actual = min(idle_period, total_duration_s - elapsed)
        if _idle_actual > 0:
            await asyncio.sleep(_idle_actual)
            elapsed += _idle_actual

        # 5% chance of "tab background" (1-5 minute pause)
        if _idle_rng.random() < 0.05:
            _bg_pause = _idle_rng.uniform(60, 300)  # 1-5 minutes
            _bg_actual = min(_bg_pause, total_duration_s - elapsed)
            if _bg_actual > 0:
                await asyncio.sleep(_bg_actual)
                elapsed += _bg_actual
