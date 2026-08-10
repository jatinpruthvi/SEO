# hci_reading_model.py - Human-Computer Interaction (HCI) Cognitive Reading & Motor Control Model
# Implements eye-tracking saccades, Fitts's Law overshoot, and variable reading pacing curves

import asyncio
import random
import hashlib
import numpy as np
import time # FIXED: Import high-precision performance counters for dynamic time logs
from loguru import logger
from behavioral_entropy import BehavioralEntropyGenerator, FocusSwitchModel, NUMPY_AVAILABLE  # BEN-10: import NUMPY_AVAILABLE flag
from humanized_movement import move_mouse_like_human, get_muscle_profile, high_precision_sleep # FIXED: Import smooth mouse trajectory, profile, and high-precision sleep getters

def stable_hash(s: str) -> int:
    """R7-DEEP-AUDIT-2 FIX: Use SHA-256 (truncated to 32 bits) to match
    hardened_orchestrator.py's stable_hash. The previous MD5-based
    implementation produced DIFFERENT values for the same input
    compared to the orchestrator's SHA-256 version, causing
    cross-module inconsistency:
      - Display slot assignments would clash between modules
      - SOCKS5 port hashing was unstable
      - Cache keys would miss (different hashes for same key)

    The MD5-based version was a pre-existing issue from before the
    R7-HOR-008 SHA-256 migration. Now both modules use SHA-256,
    truncated to 32 bits, with Mersenne-modulo distribution.
    """
    return int(hashlib.sha256(s.encode('utf-8')).hexdigest()[:8], 16) % (2**32 - 1)


def bursty_scroll_delay(rng):
    """R7-DEEP-AUDIT-2 GAP 6.4: Bursty scroll delay distribution.

    Real users have BURSTY scroll patterns: a quick flurry of scrolls
    (50-200ms apart) followed by a long pause (500-2000ms) where
    they read the scrolled-to content. A uniform distribution is
    detectable as automation (a real user has fat-fingered,
    non-uniform intervals).

    Distribution:
      - 70% short delays (50-200ms) - active scrolling phase
      - 30% long delays (500-2000ms) - reading pause phase

    Args:
        rng: A random.Random instance (per-persona seeded for stability).

    Returns:
        Delay in seconds.
    """
    if rng.random() < 0.7:
        return rng.uniform(0.05, 0.2)
    else:
        return rng.uniform(0.5, 2.0)


async def execute_smooth_momentum_scroll(page, scroll_y: int, friction_coefficient: float = 0.85):
    """
    1.5 SRE Scroll Evasion: Physical Friction-Decay Momentum Scroll.
    Bypasses standard linear scroll jumps. Uses an exponential kinetic friction decay model:
      V_next = V_current * mu
    Fires continuous scroll events trailing off over time, matching standard touchpad inertia.
    Also updates mouse cursor position memory slightly in the direction of the scroll
    to establish a realistic, non-zero Scroll-Mouse Correlation coefficient.

    1.6 Timing Upgrade: Uses high_precision_sleep to lock the momentum ticks strictly
    to 60Hz frame intervals (16.67ms) to prevent virtualized OS timer jitter.

    CHAMELEON (GAP-6.2): Realistic scroll momentum physics with:
      - Variable friction coefficient (0.88-0.94) per persona
      - Initial velocity variation (0.10-0.20) of target distance
      - Micro-frame jitter (±1-2ms) for natural timing
      - Probability of "overshoot" (0.5-2%) for momentum bleeding past target
      - Per-persona seeded RNG for reproducibility
    """
    if scroll_y == 0:
        return

    # CHAMELEON: Per-persona friction variation. Real Chrome on different
    # devices has slightly different trackpad friction:
    #   - Mac trackpad: ~0.92 (smooth, low friction)
    #   - Windows precision trackpad: ~0.90
    #   - Mouse wheel: ~0.88 (more friction)
    #   - High-DPI displays: ~0.94
    # Pick a per-persona value within the realistic range.
    # M13 fix: Use SHA-256 instead of MD5 for the friction
    # RNG seed. The previous code used MD5 for the seed
    # derivation, which is unavailable on FIPS-mode systems
    # (FIPS-mode Debian images disable hashlib.md5). A FIPS
    # deployment would crash with `ValueError: [digital
    # envelope wrap] unsupported` when calling this function.
    # SHA-256 is always available. The 32-bit truncation is
    # preserved (we only need a seed, not a hash output).
    #
    # BUG-4 FIX: The previous seed used only scroll_y, so every persona
    # scrolling by the same distance got identical friction/velocity values —
    # exactly the cross-persona fingerprint correlation the scroll simulation
    # is designed to prevent. Include persona_id in the seed so each persona
    # has unique scroll physics regardless of scroll distance.
    _persona_id_for_seed = None
    try:
        _persona_id_for_seed = (
            getattr(page.context, "_sre_persona_id", None)
            or getattr(page.context, "persona_id", None)
            or "unknown"
        )
    except Exception:
        _persona_id_for_seed = "unknown"
    _friction_rng = random.Random(int(hashlib.sha256(
        f"scroll_friction:{_persona_id_for_seed}:{scroll_y}".encode()
    ).hexdigest()[:8], 16))
    _friction_base = 0.88 + _friction_rng.random() * 0.06  # 0.88-0.94

    # CHAMELEON: Initial velocity variation. Real scroll inertia is
    # proportional to the gesture speed (flick vs slow drag). For our
    # deterministic model, we sample from a realistic distribution.
    _velocity_ratio = 0.10 + _friction_rng.random() * 0.10  # 0.10-0.20
    v = (scroll_y * _velocity_ratio)
    mu = friction_coefficient if friction_coefficient is not None else _friction_base
    accumulated = 0
    overshoot_budget = abs(scroll_y) * 0.02  # Allow 2% overshoot max

    # Track starting cursor position for Scroll-Mouse Correlation
    start_x = getattr(page, "_sre_mouse_x", 960)
    start_y = getattr(page, "_sre_mouse_y", 540)
    drift_y_accumulated = 0

    # CHAMELEON: per-frame jitter (real trackpads have ±1ms jitter)
    # M13 fix: Use SHA-256 instead of MD5 (FIPS compatibility).
    # BUG-4 FIX: Include persona_id in frame RNG seed (same fix as _friction_rng above).
    _frame_rng = random.Random(int(hashlib.sha256(
        f"scroll_frame:{_persona_id_for_seed}:{scroll_y}".encode()
    ).hexdigest()[:8], 16))

    # R7-DEEP-AUDIT-2 (Pass 14) HIGH (N1): Per-persona tremor RNG.
    # The previous code used `random.choice([-1, 0, 1])` (the global
    # random module), which produced the same scroll-tremor pattern
    # for ALL personas. This is a fingerprint leak: scroll-mouse
    # correlation tremor is a top-3 creepjs signal, and a constant
    # tremor pattern across personas is detectable as "perfectly
    # straight line with synthetic noise". We now use a per-persona
    # seeded RNG derived from the page.context.persona_id, so each
    # persona has a unique scroll-tremor sequence.
    _persona_id_s = None
    try:
        _persona_id_s = getattr(page.context, "_sre_persona_id", None) or getattr(page.context, "persona_id", None)
    except Exception:
        pass
    # M13 fix: Use SHA-256 instead of MD5 (FIPS compatibility).
    _tremor_rng_seed = int(hashlib.sha256(
        f"scroll_tremor:{_persona_id_s or 'unknown'}:{scroll_y}".encode()
    ).hexdigest()[:8], 16)
    _tremor_rng = random.Random(_tremor_rng_seed)

    last_tick_time = time.perf_counter()
    while abs(v) > 0.5 and (abs(accumulated) < abs(scroll_y) + overshoot_budget):
        # Calculate step distance using physical decay
        step = int(v)
        if step == 0:
            step = 1 if scroll_y > 0 else -1

        await page.mouse.wheel(0, step)
        accumulated += step

        # 1.5 SRE Scroll-Mouse Correlation: Drift the mouse cursor slightly in the direction
        # of the scrolling content (simulates the user's gaze and cursor following text)
        if abs(scroll_y) > 200:
            # Drift by a tiny fraction of the scroll step (e.g. 5%)
            drift_y = int(step * 0.05)
            drift_y_accumulated += drift_y
            page._sre_mouse_y = start_y + drift_y_accumulated

            # 10/10 SRE Upgrade: Add microscopic horizontal tremor to the vertical scroll-mouse drift
            # to ensure the coordinate path is not a mathematically perfect straight line
            # R7-DEEP-AUDIT-2 (Pass 14) HIGH (N1): Use per-persona seeded
            # `_tremor_rng` (see initialization above) instead of the
            # global `random.choice`. Without this fix, every persona
            # has the same scroll-tremor sequence.
            tremor_x = _tremor_rng.choice([-1, 0, 1])
            await page.mouse.move(start_x + tremor_x, page._sre_mouse_y)

        # Decay velocity exponentially
        v *= mu

        # CHAMELEON: Frame timing jitter. Real frame rate is not perfectly
        # periodic - Chrome's compositor has ±1-2ms jitter. We use a
        # Gaussian distribution around 16.67ms (60Hz) with std=1ms.
        # 1% of frames have a longer pause (15-25ms) for "rendering
        # hitch" simulation.
        elapsed = time.perf_counter() - last_tick_time
        # Gaussian jitter around 16.67ms
        _frame_jitter_ms = _frame_rng.gauss(0, 1.0)  # std=1ms
        # 1% chance of longer "rendering hitch" frame
        if _frame_rng.random() < 0.01:
            _frame_jitter_ms += _frame_rng.uniform(5, 15)
        target_delay = (16.67 + _frame_jitter_ms) / 1000.0  # convert to seconds
        remaining = target_delay - elapsed
        if remaining > 0:
            await high_precision_sleep(remaining)
        last_tick_time = time.perf_counter()

    logger.info(f"HCI: Physical momentum scroll completed. Dist: {accumulated}px.")

class HCIReadingModel:
    """DC-19 (Pass 19): Human-Computer Interaction reading/scrolling model.

    Simulates how a real user reads an article: word-by-word saccadic eye
    movements, fixation pauses on interesting words, micro-scrolls every
    200-400ms, and per-persona biological drift in idle time. Used by task
    executors to add realistic reading pauses when interacting with
    long-form content (news articles, blog posts, product descriptions).

    The model is seeded by persona_id so the same persona produces
    consistent reading patterns across all its tasks.
    """
    def __init__(self, persona_id: str, total_words: int = 1500):
        """
        Models human cognitive engagement, reading pacing, and motor control curves.
        Integrates eye-tracking saccadic movements, Fitts's Law overshoot, and mouse fidgets.
        """
        self.persona_id = persona_id
        self.total_words = total_words

        # Resolve the persona's distinct muscle profile
        state_seed = stable_hash(persona_id)
        self.rng = np.random.default_rng(state_seed)

        # Baseline reading speed (average adult: 200-250 WPM)
        self.base_wpm = self.rng.uniform(190, 260)
        self.total_reading_time = (total_words / self.base_wpm) * 60.0 # Total target dwell time in seconds

        # 1.6 Visual-Motor Consistency: Track visited element selectors in active session
        self.visited_selectors = set()

        # 1.6 Environment Adaptation: Default page load latency baseline to scale motor approach velocity
        self.load_latency_baseline = 1.0

        # BEN-10 (Pass 14): Store the persona's biological_drift on the
        # HCI model so that simulate_idle_period_lognormal() can
        # modulate the median idle time per persona. Previously
        # the code used `getattr(self, 'biological_drift', 1.0)`
        # which always returned 1.0 (the HCI model never had this
        # attribute set), so all personas got the same idle-time
        # distribution. The result: idle times were a fixed log-normal
        # shape, not a per-persona shape, which is a fingerprint.
        # We now look up the drift from the actual profile if available.
        try:
            _profile = get_muscle_profile(persona_id)
            self.biological_drift = getattr(_profile, 'biological_drift', 1.0)
        except Exception:
            self.biological_drift = 1.0

        logger.info(f"HCI: Initialized reading model for {persona_id}. Target WPM: {self.base_wpm:.1f}. Dwell time: {self.total_reading_time:.1f}s. Drift: {self.biological_drift:.3f}.")

    async def update_load_latency_baseline(self, latency: float):
        """Sets the active baseline page loading latency to scale spatial approach velocities."""
        self.load_latency_baseline = max(0.200, latency)

    def fixation_duration_ms(self, word_difficulty: float = 1.0) -> float:
        """R7-HCI-001: Sample a fixation duration in milliseconds.
        
        Real fixations follow a log-normal distribution:
          - Center: 230ms (Rayner 1998, Just & Carpenter 1980)
          - Sigma in log-space: 0.2
          - Range: ~150-400ms (95% confidence interval)
          - Modulated by:
              - Word difficulty (longer for technical terms)
              - Reading expertise (experts faster)
              - Stress (longer)
              - Fatigue (longer)
        
        Previous code used a constant 250ms; the new code uses
        log-normal which matches the empirical distribution.
        """
        # Base log-normal: center 230ms, sigma 0.2 in log-space
        base_mu = 230.0
        base_sigma = 0.2
        # Difficulty modulation: technical words increase fixation
        # (Just & Carpenter 1980 found a 45% increase per unit difficulty)
        difficulty_mult = 1.0 + (word_difficulty - 1.0) * 0.45
        # Expertise modulation: experts fixate ~20% faster
        # (we don't have direct expertise field, so estimate from WPM)
        if self.base_wpm >= 240:  # expert-level
            expertise_mult = 0.80
        elif self.base_wpm >= 200:  # intermediate
            expertise_mult = 0.95
        else:  # novice
            expertise_mult = 1.15
        # Generate log-normal sample
        log_mu = np.log(base_mu * difficulty_mult * expertise_mult) - (base_sigma ** 2) / 2
        fixation = float(self.rng.lognormal(log_mu, base_sigma))
        return max(120.0, min(600.0, fixation))

    def saccade_velocity_deg_per_s(self, amplitude_deg: float) -> float:
        """R7-HCI-002: Saccade peak velocity following the main sequence.
        
        The main sequence (Bahill et al. 1975): peak velocity
        ≈ 30 * amplitude_deg for amplitudes 2-20°. Real data shows
        ~0.95 correlation between amplitude and velocity. The
        current code sampled amplitude and velocity independently,
        producing unrealistic combinations (slow large saccades,
        fast small saccades).
        
        Args:
            amplitude_deg: Saccade amplitude in degrees of visual angle
                          (2-5° for reading, 10-20° for scene viewing)
        Returns:
            Peak velocity in degrees/second
        """
        if amplitude_deg <= 0:
            return 0.0
        # Main sequence: V_peak = 30 * amplitude (linear approximation
        # for the 2-20° range). Add ±15% noise from the persona's RNG.
        base_velocity = 30.0 * amplitude_deg
        try:
            noise = float(self.rng.normal(1.0, 0.15))
        except Exception:
            noise = 1.0
        return max(20.0, min(800.0, base_velocity * noise))
    
    def saccade_duration_ms(self, amplitude_deg: float) -> float:
        """R7-HCI-002: Saccade duration.
        
        Real saccades last 20-200ms depending on amplitude.
        Duration ≈ amplitude / velocity * 1000 (in ms).
        """
        velocity = self.saccade_velocity_deg_per_s(amplitude_deg)
        if velocity <= 0:
            return 0.0
        return (amplitude_deg / velocity) * 1000.0

    async def execute_saccadic_scroll(self, page, total_height: int = None):
        # R7-HCI-006: If the tab is hidden, skip the reading flow.
        # Real users don't actively read a backgrounded tab. The
        # orchestrator's circadian check would normally prevent
        # this, but defense in depth: check document.hidden.
        try:
            if await page.evaluate("() => document.hidden"):
                logger.debug("HCI: Tab is hidden; skipping reading flow.")
                return
        except Exception:
            pass

        """
        Simulates human reading progression. Humans do not scroll smoothly;
        they read in blocks (saccades) and occasionally scroll back up to re-read (regression).
        
        1.5 Reading-Scroll Correlation Upgrade:
        Dynamically extracts page word count and adjusts scroll step distances and block dwell
        pauses on-the-fly to ensure scroll speed is mathematically proportional (∝) to content density.
        """
        # 1. Dynamically read actual page word count to align reading speed to content volume
        try:
            visible_text = await page.evaluate("() => document.body.innerText")
            words_on_page = len([w for w in (visible_text or "").split() if len(w) > 1])
            if words_on_page > 100:
                self.total_words = words_on_page
                self.total_reading_time = (self.total_words / self.base_wpm) * 60.0
                logger.info(f"HCI: Dynamically adjusted reading model for active page. Words: {self.total_words} | Target Dwell: {self.total_reading_time:.1f}s.")
        except Exception as e:
            logger.warning(f"HCI: Failed to dynamically extract page word count: {e}")

        # 2. Dynamically determine true scrollable height and viewport height if not passed
        viewport_height = 1080
        if total_height is None:
            try:
                total_height = await page.evaluate("() => document.body.scrollHeight")
                viewport_height = await page.evaluate("() => window.innerHeight")
            except Exception:
                total_height = 1500
                viewport_height = 1080

        # 1.5 SRE Short-Page Bypass:
        # If the page scrollable height is less than or equal to the viewport height,
        # there is no scrollbar. Humans do not scroll non-scrollable pages. We bypass the scroll loop,
        # execute a single reading pause proportional to the word count, run micro-fidgets, and exit.
        if total_height <= viewport_height:
            logger.info(f"HCI: Short page detected ({total_height}px <= {viewport_height}px). Bypassing scrolling loop.")
            # L8 fix (Pass 15): Use the *actual* page word count for
            # the reading pause, not the stale `self.total_words`
            # (which was 1500 by default and was only updated if the
            # page had > 100 words). On a short page (e.g., 50 words),
            # the previous code slept for 1500/225 WPM * 60 * 0.8 =
            # 320 seconds — a 100x over-read detectable as a bot. We
            # now set `total_words` to the actual page word count
            # (clamped to a minimum of 50 to avoid sub-second pauses
            # that look unnatural) and use that for the pause.
            #
            # L9 fix (Pass 16): The `ReadingPacingModel.time_to_read_text_ms`
            # method was defined in behavioral_entropy.py but never
            # called. The short-page path used a simple linear formula
            # (chars / WPM * 60 * 0.8) which is detectable: real users
            # have a fixation-saccade distribution (Rayner 1998, Just &
            # Carpenter 1980) with per-word log-normal variation and
            # 10-15% regression saccades. We now wire up the
            # ReadingPacingModel and use its vectorized time-to-read
            # estimator, which incorporates per-fixation variance and
            # regression probability matching the empirical
            # distribution. The `behavioral_entropy` import is added
            # at the module top (already present) and we get a profile
            # for the persona to seed the ReadingPacingModel.
            _actual_words = max(50, self.total_words)
            _actual_chars = _actual_words * 5  # avg English word length
            # Estimate reading time via ReadingPacingModel
            _reading_model = None
            try:
                from behavioral_entropy import ReadingPacingModel
                _profile = get_muscle_profile(self.persona_id)
                _reading_model = ReadingPacingModel(_profile)
            except Exception as _rm_err:
                logger.debug(f"L9: ReadingPacingModel unavailable, falling back to linear: {_rm_err}")
            if _reading_model is not None:
                _reading_ms = _reading_model.time_to_read_text_ms(_actual_chars)
                _short_pause = _reading_ms / 1000.0
            else:
                # Fallback to the linear formula (was the only path before Pass 16)
                _short_pause = (_actual_words / self.base_wpm) * 60.0 * 0.8
            await asyncio.sleep(_short_pause)
            # Simulate a single physiological fidget
            await self.simulate_physiological_fidget(page)
            logger.info(f"HCI: Short page reading completed in {_short_pause:.1f}s ({_actual_words} words).")
            return

        logger.info("HCI: Commencing saccadic reading scroll sequence.")
        current_scroll = 0
        elapsed_time = 0.0
        
        # Segment the article into variable page sections based on average text density (e.g. 150 words per scroll)
        sections_count = max(5, int(self.total_words / 150))
        step_distance = int(total_height / sections_count)

        while current_scroll < total_height * 0.9 and elapsed_time < self.total_reading_time:
            # 1. Forward Saccadic Scroll (Read next block) - Replaced with Momentum Scroll
            forward_step = step_distance + self.rng.integers(-40, 60)
            await execute_smooth_momentum_scroll(page, forward_step)
            current_scroll += forward_step
            
            # 1.5 SRE Content Density Response: Query the DOM for visible elements in the active viewport.
            # Real users pause longer on images, much longer on videos/iframes, and longer on code blocks/lists.
            extra_dwell = 0.0
            try:
                # Lightweight DOM check for elements inside the current viewport range
                element_density = await page.evaluate(f"""() => {{
                    const images = document.querySelectorAll('img');
                    const videos = document.querySelectorAll('video, iframe');
                    const codes = document.querySelectorAll('pre, code, ol, ul');
                    
                    let has_img = false, has_video = false, has_code = false;
                    const view_h = window.innerHeight;
                    const scroll_y = {current_scroll};
                    
                    images.forEach(el => {{
                        const rect = el.getBoundingClientRect();
                        if (rect.top >= 0 && rect.bottom <= view_h) has_img = true;
                    }});
                    videos.forEach(el => {{
                        const rect = el.getBoundingClientRect();
                        if (rect.top >= 0 && rect.bottom <= view_h) has_video = true;
                    }});
                    codes.forEach(el => {{
                        const rect = el.getBoundingClientRect();
                        if (rect.top >= 0 && rect.bottom <= view_h) has_code = true;
                    }});
                    return {{ has_img, has_video, has_code }};
                }}""")
                
                if element_density.get("has_video"):
                    # Video scrutiny: Linger 8s to 20s (simulates watching playback/buffering)
                    extra_dwell = float(self.rng.uniform(8.0, 20.0))
                    logger.info(f"HCI Content Density: Video element detected in viewport. Lingering {extra_dwell:.1f}s.")
                elif element_density.get("has_code"):
                    # Code block/List scrutiny: Linger 4s to 8s (simulates studying code syntax)
                    extra_dwell = float(self.rng.uniform(4.0, 8.0))
                    logger.info(f"HCI Content Density: Code/List element detected in viewport. Lingering {extra_dwell:.1f}s.")
                elif element_density.get("has_img"):
                    # Image scrutiny: Linger 2.5s to 5s (simulates looking at the graphic)
                    extra_dwell = float(self.rng.uniform(2.5, 5.0))
                    logger.info(f"HCI Content Density: Image element detected in viewport. Lingering {extra_dwell:.1f}s.")
            except Exception as density_err:
                pass

            # 1.5 Attention Heatmap Forensics: Implement Exponential Attention Decay
            # Real human attention decays exponentially as they scroll below the fold (F-Pattern/Gaze decay).
            # We scale down the baseline block dwell time based on the scroll progress.
            scroll_progress = current_scroll / total_height
            attention_decay = np.exp(-scroll_progress * 1.2) # Decay to ~30% of baseline near the footer
            
            # Cognitive processing pause (Dwell time per block, log-normally distributed)
            block_dwell = self.rng.lognormal(mean=np.log(self.total_reading_time / sections_count), sigma=0.25)
            block_dwell = (np.clip(block_dwell, 3.0, 25.0) * attention_decay) + extra_dwell
            
            # 1.6 Environment Adaptation: Scale reading dwell proportional to historical load latency
            block_dwell *= np.clip(1.0 + (self.load_latency_baseline - 1.0) * 0.20, 0.80, 2.50)
            
            # 10% chance of emulating a tab focus/blur look-away event (Page Visibility Forensics)
            # Simulates a human looking away, checking another window, or switching tabs during long reading blocks
            if block_dwell > 6.0 and self.rng.random() < 0.10:
                logger.info("HCI: Focus/Blur Look-Away Fidget triggered. Emulating tab distraction...")
                try:
                    await page.evaluate("window.dispatchEvent(new Event('blur'))")
                except Exception:
                    pass
                
                # Cognitive distraction delay
                distraction = float(self.rng.uniform(1.5, 3.5))
                await asyncio.sleep(distraction)
                
                # Re-gain focus and continue reading
                try:
                    await page.evaluate("window.dispatchEvent(new Event('focus'))")
                except Exception:
                    pass
                await asyncio.sleep(max(0.1, block_dwell - distraction))
            else:
                await asyncio.sleep(block_dwell)
            elapsed_time += block_dwell

            # 2. Reading Regression (7% chance of scrolling back up slightly to re-read difficult sections)
            if self.rng.random() < 0.07 and current_scroll > 200:
                regression_step = self.rng.integers(-120, -40)
                await execute_smooth_momentum_scroll(page, regression_step)
                current_scroll += regression_step
                
                re_read_pause = float(self.rng.uniform(1.5, 4.5))
                await asyncio.sleep(re_read_pause)
                elapsed_time += re_read_pause
                logger.info(f"HCI: Gaze regression triggered. Persona scrolled up {abs(regression_step)}px to re-read.")

            # 3. Simulate Mouse Fidgets (Occasional pointer highlighting/movement while reading)
            # 1.5 Return Fixation Upgrade: 10% chance to execute a natural text selection/highlighting fidget
            if self.rng.random() < 0.25:
                if self.rng.random() < 0.10:
                    await self.execute_text_selection_fidget(page)
                else:
                    await self.simulate_physiological_fidget(page)

        logger.info(f"HCI: Saccadic reading completed. Elapsed time: {elapsed_time:.1f} seconds.")

    async def simulate_physiological_fidget(self, page):
        """
        Simulates idle micro-adjustments or text highlighting that humans perform
        unconsciously to keep focus during reading.
        Incorporates 10% chance of random Right-Click & Dismissal events (Right-Click Forensics)
        to match realistic, non-zero contextual menu interactions.
        """
        logger.info("HCI: Emulating physiological pointer fidget.")
        start_x = getattr(page, "_sre_mouse_x", 960)
        start_y = getattr(page, "_sre_mouse_y", 540)
        
        # Generate a tiny, randomized jitter coordinate
        jitter_x = start_x + self.rng.integers(-50, 50)
        jitter_y = start_y + self.rng.integers(-50, 50)
        
        # Move cursor naturally via Bezier splines
        await move_mouse_like_human(page, start_x, start_y, jitter_x, jitter_y, steps=15, familiarity=0.5)
        await asyncio.sleep(self.rng.uniform(0.1, 0.4))

        # 10% chance of executing a natural right-click & dismissal sequence (Right-Click Forensics)
        if self.rng.random() < 0.10:
            logger.info("HCI: Right-Click & Dismissal Fidget triggered. Emulating contextual interaction...")
            # 1. Right click on empty space to invoke context menu
            await self.execute_anatomical_click(page, button="right", approach_velocity=5.0)
            
            # 2. Cognitive menu reading pause (1.2s to 2.5s)
            await asyncio.sleep(self.rng.uniform(1.2, 2.5))
            
            # 3. Dismiss context menu by executing a left-click on empty adjacent space
            dismiss_x = jitter_x + self.rng.integers(-30, 30)
            dismiss_y = jitter_y + self.rng.integers(-30, 30)
            await move_mouse_like_human(page, jitter_x, jitter_y, dismiss_x, dismiss_y, steps=10, familiarity=0.9)
            await self.execute_anatomical_click(page, button="left", approach_velocity=10.0)

    async def execute_text_selection_fidget(self, page):
        """
        1.5 SRE Behavioral Upgrade: Return Fixation & Text Highlighting.
        Bypasses perfect static mouse profiles. Real readers occasionally highlight text
        while scanning. We select a random visible paragraph, move to its start,
        drag smoothly across a few words, and release naturally.
        """
        try:
            # Locate a random paragraph element
            # R7-DEEP-AUDIT-2 (Pass 14) HIGH (N1): Use self.rng (per-persona)
            # instead of the global `random.randint` for paragraph
            # selection. Without this, every persona's text-highlight
            # fidget selects the same paragraph index, which is a
            # fingerprint leak detectable by creepjs.
            paragraphs = page.locator("p")
            count = await paragraphs.count()
            if count > 0:
                # np.random.Generator.integers(high) is EXCLUSIVE on high,
                # so random.randint(a, b) becomes self.rng.integers(a, b+1).
                target_p = paragraphs.nth(self.rng.integers(0, min(count - 1, 5) + 1))
                box = await target_p.bounding_box()
                if box and box["width"] > 100:
                    start_x = getattr(page, "_sre_mouse_x", 960)
                    start_y = getattr(page, "_sre_mouse_y", 540)

                    # Highlight coordinates: start of paragraph to 50-80px right
                    # R7-DEEP-AUDIT-2 (Pass 14) HIGH (N1): self.rng (per-persona).
                    drag_start_x = box["x"] + self.rng.integers(10, 30 + 1)
                    drag_start_y = box["y"] + box["height"] / 2
                    drag_end_x = drag_start_x + self.rng.integers(50, 120 + 1)
                    drag_end_y = drag_start_y + self.rng.uniform(-2, 2)

                    # Move smoothly to start (Low familiarity - scanning new paragraph text)
                    await move_mouse_like_human(page, start_x, start_y, drag_start_x, drag_start_y, steps=25, familiarity=0.20)
                    await asyncio.sleep(self.rng.uniform(0.1, 0.3))

                    # Press mouse button down
                    await page.mouse.down()
                    await asyncio.sleep(self.rng.uniform(0.05, 0.15))

                    # Drag smoothly across text segment (Intermediate familiarity to track drag)
                    await move_mouse_like_human(page, drag_start_x, drag_start_y, drag_end_x, drag_end_y, steps=15, familiarity=0.50)
                    await asyncio.sleep(self.rng.uniform(0.1, 0.2))

                    # Release mouse button up
                    await page.mouse.up()

                    # Cognitive realization / reading pause (1.0s to 2.5s)
                    await asyncio.sleep(self.rng.uniform(1.0, 2.5))

                    # Click empty adjacent space to dismiss the highlight
                    # R7-DEEP-AUDIT-2 (Pass 14) HIGH (N1): self.rng (per-persona).
                    dismiss_x = drag_end_x + self.rng.integers(20, 50 + 1)
                    dismiss_y = drag_end_y + self.rng.integers(20, 50 + 1)
                    await move_mouse_like_human(page, drag_end_x, drag_end_y, dismiss_x, dismiss_y, steps=10, familiarity=0.90)
                    await self.execute_anatomical_click(page, button="left", approach_velocity=10.0)
        except Exception as e:
            logger.warning(f"HCI: Text selection highlighting fidget failed: {e}")

    async def execute_anatomical_click(self, page, button="left", approach_velocity: float = None):
        """
        1.5 SRE Behavioral Upgrade: Anatomical Click Emulation.
        Fully solves Click Timing, Click Pressure, Right-Click, and Click Velocity Forensics:
          - Pre-Click Dwell: Ex-Gaussian hover pause before pressing (neurologically validated).
          - Click Pressure: Ex-Gaussian down-to-up hold (80ms-200ms) - non-rectangular curve.
          - Right-Click: Fully supports right button triggers and context menu dismissals.
          - Click Velocity: Mathematically correlates approach velocity with hold times (reflex bounce-off).
        """
        # SRE Concurrency Protection: Track last clicked time to block impossible click-frequencies
        last_click_time = getattr(page, "last_click_time", 0.0)
        current_time = time.perf_counter()
        elapsed_since_click = current_time - last_click_time
        if elapsed_since_click < 0.100: # 100ms absolute physiological minimum speed
            # Enforce dynamic rest delay to let muscles recover naturally
            await asyncio.sleep(0.120 - elapsed_since_click)

        # Retrieve the dynamic persona_id safely from the browser context to query ex-gaussian parameters
        persona_id = getattr(page.context, "persona_id", "generic_bot")
        profile = get_muscle_profile(persona_id)

        # 1. Pre-Click Dwell (Seeded Ex-Gaussian distribution: 100ms to 500ms)
        # Slower approach velocities correlate with longer cognitive hover delays (choice reaction)
        rt_type = "choice" if (approach_velocity and approach_velocity < 15.0) else "simple"
        dwell_delay = profile.generate_ex_gaussian_reaction_time(rt_type)
        
        # 1.6 Environment Adaptation: Scale pre-click hover proportionally to baseline page latency
        dwell_delay *= np.clip(1.0 + (self.load_latency_baseline - 1.0) * 0.25, 0.85, 2.20)
        dwell_delay = np.clip(dwell_delay, 0.100, 0.750)
        await asyncio.sleep(dwell_delay)
        
        # 2. Key Down (press)
        await page.mouse.down(button=button)
        
        # 3. SRE Micro-Slippage Evasion (Click-Hold Finger Tremor)
        start_x = getattr(page, "_sre_mouse_x", 960)
        start_y = getattr(page, "_sre_mouse_y", 540)
        if self.rng.random() < 0.35:
            slip_x = round(float(start_x + self.rng.uniform(-1.0, 1.0)))
            slip_y = round(float(start_y + self.rng.uniform(-1.0, 1.0)))
            await page.mouse.move(slip_x, slip_y)
            page._sre_mouse_x = slip_x
            page._sre_mouse_y = slip_y
        
        # 4. Click Pressure / Hold Duration (Seeded Ex-Gaussian: 80ms to 200ms)
        # Click Velocity Correlation: Fast approach -> slightly shorter holding bounce reflex (recognition reaction)
        # BE69: Use MouseButtonDurationModel for click-type-specific hold duration
        # (more realistic than a single ex-Gaussian distribution for all clicks).
        # Falls back to ex-Gaussian if the model is unavailable.
        try:
            from behavioral_entropy import MouseButtonDurationModel
            click_model = MouseButtonDurationModel(profile)
            if button == "right":
                click_type = "right"
            else:
                click_type = "single"
            hold_delay_ms = click_model.click_hold_ms(click_type)
            # Apply approach-velocity correlation (fast approach -> slightly shorter hold)
            if approach_velocity:
                hold_delay_ms = max(60.0, hold_delay_ms - (approach_velocity * 0.10))
            hold_delay = hold_delay_ms / 1000.0
        except Exception:
            # Fallback to the original ex-Gaussian path
            hold_delay = profile.generate_ex_gaussian_reaction_time("recognition")
            if approach_velocity:
                hold_delay = np.clip(hold_delay - (approach_velocity * 0.0001), 0.060, 0.180)
        await asyncio.sleep(hold_delay)
        
        # 5. Key Up (release)
        await page.mouse.up(button=button)
        page.last_click_time = time.perf_counter() # Stamp click time
        
        # 6. Post-Click Cognitive Linger (human waiting for response: Ex-Gaussian complex decision)
        linger_delay = profile.generate_ex_gaussian_reaction_time("complex")
        # 1.6 Environment Adaptation: Scale post-click linger based on page speed
        linger_delay *= np.clip(1.0 + (self.load_latency_baseline - 1.0) * 0.35, 0.80, 2.50)
        linger_delay = np.clip(linger_delay, 0.180, 0.600)
        await asyncio.sleep(linger_delay)
        logger.info(f"HCI: Anatomical '{button}' click executed successfully. Hold: {hold_delay:.4f}s.")

    async def execute_anatomical_double_click(self, page):
        """
        1.5 SRE Behavioral Upgrade: Unified Double-Click Tap Reflex.
        Bypasses standard single-click delay-loops which exceed the 500ms OS threshold.
        Manually processes rapid, continuous down/up cycles separated by realistic
        double-tap intervals (100ms-250ms) and micro-drifts (+/- 1.5px),
        ensuring guaranteed double-click registration by both the OS and Google Front End.

        L12 fix (Pass 16): Added try/finally to release any pressed
        modifier keys on cancellation or exception. The previous
        implementation only released the mouse button, leaving the
        page in a state where Shift/Ctrl/Alt could be stuck in a
        "down" state if a CancelledError fired mid-click. A stuck
        Shift key would cause ALL subsequent text input to be
        uppercase (e.g., the user types "hello" and gets "HELLO")
        until the user manually releases Shift. In a persona
        automation, this is an instant fingerprint — the persona
        types correctly for 90% of the session, then suddenly
        types "HELLO" for 10 seconds. We now mirror the pattern
        used in execute_anatomical_click() (which doesn't have
        this issue because it doesn't press modifier keys) and
        release any modifier keys in a finally block. We also
        release the mouse button if it's still down (defense in
        depth — Playwright's `down()` and `up()` are paired, but
        a network error between them could leave the button
        stuck).
        """
        # 1. Pre-Click Dwell (Standard cognitive preparation before first click: 100ms to 200ms)
        base_dwell = 0.150
        dwell_delay = self.rng.lognormal(mean=np.log(base_dwell), sigma=0.10)
        await asyncio.sleep(dwell_delay)

        # L12: Track whether the mouse button is currently down so we
        # can release it on cancellation. The previous code did not
        # track this; a CancelledError between `down()` and `up()`
        # would leave the button stuck.
        _mouse_down = False
        # L12: Release all modifier keys in finally block. The set of
        # modifier keys to release matches what keyboard_emulator.py
        # uses for uppercase typing.
        _MODIFIER_KEYS = ("Shift", "Control", "Alt", "Meta")
        try:
            # --- FIRST CLICK CYCLE ---
            await page.mouse.down()
            _mouse_down = True
            # Shorter, rapid hold duration for double tap reflex (30ms to 70ms)
            await asyncio.sleep(self.rng.uniform(0.03, 0.07))
            await page.mouse.up()
            _mouse_down = False

            # --- INTER-CLICK INTERVAL & MICRO-DRIFT ---
            # Rapid, non-cognitive inter-tap interval (80ms to 220ms)
            interval = self.rng.uniform(0.08, 0.22)
            await asyncio.sleep(interval)

            # Microscopic physical mouse slippage between clicks (hand tremor during rapid tapping)
            start_x = getattr(page, "_sre_mouse_x", 960)
            start_y = getattr(page, "_sre_mouse_y", 540)
            drift_x = start_x + self.rng.uniform(-1.5, 1.5)
            drift_y = start_y + self.rng.uniform(-1.5, 1.5)
            await move_mouse_like_human(page, start_x, start_y, drift_x, drift_y, steps=2, familiarity=0.90)

            # --- SECOND CLICK CYCLE ---
            await page.mouse.down()
            _mouse_down = True
            await asyncio.sleep(self.rng.uniform(0.03, 0.07))
            await page.mouse.up()
            _mouse_down = False

            # 2. Post-Click Linger (human verifying target reacted)
            await asyncio.sleep(self.rng.uniform(0.20, 0.40))
            logger.info(f"HCI: Anatomical double-click reflex executed successfully. Interval: {interval * 1000:.1f}ms.")
        except asyncio.CancelledError:
            # L12: Clean up on cancellation. The cancellation could
            # have fired mid-cycle (e.g., between down() and up(),
            # or during the inter-click interval).
            if _mouse_down:
                try:
                    await page.mouse.up()
                except Exception:
                    pass
            # Release any modifier keys that may be stuck down
            for mod in _MODIFIER_KEYS:
                try:
                    await page.keyboard.up(mod)
                except Exception:
                    pass
            raise
        except Exception:
            # L12: Same cleanup for any other exception (not just
            # cancellation). A network error, a navigation error, or
            # a Playwright timeout could leave the system in an
            # inconsistent state otherwise.
            if _mouse_down:
                try:
                    await page.mouse.up()
                except Exception:
                    pass
            for mod in _MODIFIER_KEYS:
                try:
                    await page.keyboard.up(mod)
                except Exception:
                    pass
            raise

    async def simulate_idle_period(self, page, duration_s: float = None):
        """
        INVIS-67: Simulate an idle period (user walks away from the screen).

        Real users leave the browser idle for minutes at a time (coffee
        break, meeting, etc.). A session with no idle periods is detectable.

        The duration follows a Weibull distribution (long-tailed) with
        median ~3 minutes. Triggers a 'blur' event, then 'focus' on return.
        """
        if duration_s is None:
            # Weibull-distributed idle time. Median ~180s, long tail to 30min.
            if hasattr(self, 'rng') and self.rng is not None:
                u = float(self.rng.random())
                # Weibull shape=1.3, scale=200s
                import math
                duration_s = 200.0 * (-math.log(max(1e-9, 1.0 - u))) ** (1.0 / 1.3)
            else:
                duration_s = 180.0
        # Clamp to a reasonable range (10s to 30 minutes)
        duration_s = max(10.0, min(1800.0, duration_s))
        logger.info(f"HCI: Simulating idle period of {duration_s:.1f}s (user walked away).")
        try:
            await page.evaluate("window.dispatchEvent(new Event('blur'))")
        except Exception:
            pass
        # Split the idle into smaller chunks so the task timeout doesn't
        # abort us. 60s per chunk is enough to not stress the event loop.
        chunk_s = min(60.0, duration_s)
        elapsed = 0.0
        while elapsed < duration_s:
            sleep_for = min(chunk_s, duration_s - elapsed)
            await asyncio.sleep(sleep_for)
            elapsed += sleep_for
        try:
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
        except Exception:
            pass
        logger.info("HCI: Idle period ended (user returned).")


    async def simulate_idle_period_lognormal(self, page, mu: float = None, sigma: float = None):
        """R7-BEN-003: Idle period with log-normal distribution.

        Real users' idle times (time between consecutive actions like
        clicks or scrolls) follow a log-normal distribution, not
        exponential. The mean of log-normal is exp(mu + sigma^2/2);
        the mode is exp(mu - sigma^2). For typical user idle times:
          - mu = 1.5 (median idle ~4.5s)
          - sigma = 0.8 (long right tail up to 60s+)

        Without an explicit mu/sigma, we use the persona's biological
        drift to modulate: a more "caffeinated" persona has shorter
        idle times, a "tired" persona has longer.
        """
        if mu is None:
            # Base mu calibrated to median ~4.5s. Biological drift in
            # [0.92, 1.08] modulates this by ±8%.
            drift = getattr(self, 'biological_drift', 1.0) if hasattr(self, 'biological_drift') else 1.0
            mu = 1.5 * (2.0 - drift)
        if sigma is None:
            sigma = 0.8
        # Generate log-normal sample
        # BEN-10: Use the numpy RNG directly. The HCIReadingModel is
        # constructed with np.random.default_rng (numpy is required at
        # import time, so NUMPY_AVAILABLE is always True here). We
        # still keep the NUMPY_AVAILABLE check for defense in depth
        # in case numpy is ever made optional.
        if hasattr(self, 'rng') and NUMPY_AVAILABLE:
            sample = float(self.rng.lognormal(mu, sigma))
        else:
            # Pure-Python fallback: lognormal via inverse CDF
            import math
            import random as _r
            sample = math.exp(_r.gauss(mu, sigma))
        # Clamp to reasonable range (100ms to 300s)
        duration_s = max(0.1, min(300.0, sample))
        logger.info(f"HCI: Simulating idle period of {duration_s:.1f}s (log-normal, mu={mu:.2f}, sigma={sigma}).")
        await asyncio.sleep(duration_s)

    async def simulate_tab_switch(self, page, task_complexity: float = 1.0):
        """
        INVIS-66: Simulate a tab switch (user briefly looks at another tab).

        Real users switch tabs 5-20 times per session. Uses the
        FocusSwitchModel from behavioral_entropy for realistic
        Weibull-distributed timing.

        The persona's profile is fetched from page.context to ensure
        the same RNG state across the session.
        """
        persona_id = getattr(page.context, 'persona_id', 'generic_bot')
        if persona_id in ('generic_bot', '', None):
            # Try _sre_persona_id
            persona_id = getattr(page.context, '_sre_persona_id', 'generic_bot')
        profile = get_muscle_profile(persona_id) if persona_id else None
        if profile is None:
            # Without a profile, fall back to a fixed short switch
            await asyncio.sleep(0.5)
            return
        focus_model = FocusSwitchModel(profile)
        # We don't actually wait the full "time to next switch" — that
        # would be unrealistic in a 45min task session. Instead, we
        # check if the current state suggests a tab switch is due, and
        # if so, perform a brief one. Probability is driven by
        # 1 / time_to_next_switch.
        time_to_next = focus_model.time_to_next_switch_s(task_complexity)
        # Probability of a switch in the next second:
        prob = 1.0 / time_to_next
        roll = float(profile.rng.random())
        if roll < prob:
            off_dwell = focus_model.off_tab_dwell_s()
            off_dwell = max(0.5, min(15.0, off_dwell))
            logger.info(f"HCI: Tab-switch event triggered. Off-tab dwell: {off_dwell:.1f}s.")
            try:
                await page.evaluate("window.dispatchEvent(new Event('blur'))")
            except Exception:
                pass
            # Cap the off-tab dwell at 15s (real users don't look away
            # for an hour from a single form task)
            await asyncio.sleep(off_dwell)
            try:
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
            except Exception:
                pass

    async def move_with_fitts_overshoot(self, page, target_selector: str):
        """
        Models human motor control targeting (Fitts's Law).
        Real hand movements often overshoot the target slightly before executing
        a microscopic correction to align the cursor with the element.
        
        1.6 Spatial-Temporal Consistency Upgrade:
          - Target Familiarity Tracker: Reduces lateral splines curvature drift on familiar targets.
          - Speed-Accuracy Tradeoff: Dynamically expands landing coordinate offset variance 
            proportional (∝) to terminal movement speed. Faster movements are mathematically less accurate.
        """
        target = page.locator(target_selector)
        
        # 1.5 Viewport Visibility Evasion
        try:
            await target.scroll_into_view_if_needed()
            await asyncio.sleep(self.rng.uniform(0.15, 0.35))
        except Exception:
            pass

        box = await target.bounding_box()
        if not box:
            return

        # Target center coordinates
        center_x = box["x"] + box["width"] / 2
        center_y = box["y"] + box["height"] / 2

        # Retrieve starting mouse position to skew target and calculate approach velocity
        start_x = getattr(page, "_sre_mouse_x", 960)
        start_y = getattr(page, "_sre_mouse_y", 540)
        distance = np.sqrt((center_x - start_x)**2 + (center_y - start_y)**2)

        # 1.6 Target Familiarity Resolution
        if target_selector in self.visited_selectors:
            familiarity = 0.85 # Element is highly familiar
        else:
            self.visited_selectors.add(target_selector)
            familiarity = 0.20 # Element is completely novel

        # Resolve the muscle profile safely to fetch speed factors
        persona_id = getattr(page.context, "persona_id", "generic_bot")
        profile = get_muscle_profile(persona_id)

        # 1.6 Speed-Accuracy Tradeoff (Spatial-Temporal Consistency)
        # Higher baseline muscle speeds reduce physical spatial landing accuracy (R2 > 0.3 required)
        # Under slow, highly congested environments (page latency > 2.0s) or caution (PES), precision increases.
        pes_speed_mult = 0.80 if profile.post_error_slowing else 1.0
        speed_factor = profile.muscle_speed_factor * pes_speed_mult * np.clip(2.0 - self.load_latency_baseline * 0.30, 0.60, 1.30)
        scatter_scale = 0.05 * (1.0 + speed_factor * 0.45) # Higher movement speed = larger landing offset variance

        skew_direction_x = -1.0 if center_x > start_x else 1.0
        skew_direction_y = -1.0 if center_y > start_y else 1.0
        
        mean_offset_x = skew_direction_x * (box["width"] * 0.05)
        mean_offset_y = skew_direction_y * (box["height"] * 0.05)
        
        target_x = center_x + self.rng.normal(mean_offset_x, box["width"] * scatter_scale)
        target_y = center_y + self.rng.normal(mean_offset_y, box["height"] * scatter_scale)
        
        # Clamp coordinates to stay strictly inside the physical element's boundaries
        target_x = np.clip(target_x, box["x"] + 2, box["x"] + box["width"] - 2)
        target_y = np.clip(target_y, box["y"] + 2, box["y"] + box["height"] - 2)

        # 2-5% Missed Click & Recovery Loop
        miss_roll = self.rng.random()
        if 0.02 <= miss_roll <= 0.05 and distance > 50:
            logger.warning(f"HCI: Missed-click event triggered for selector '{target_selector}'. Emulating physical recovery...")
            
            if center_x > start_x:
                miss_x = box["x"] + box["width"] + self.rng.uniform(4, 12)
            else:
                miss_x = box["x"] - self.rng.uniform(4, 12)
                
            if center_y > start_y:
                miss_y = box["y"] + box["height"] + self.rng.uniform(4, 12)
            else:
                miss_y = box["y"] - self.rng.uniform(4, 12)
            
            # Clamp missed coordinates
            miss_x = np.clip(miss_x, 10, 1910)
            miss_y = np.clip(miss_y, 10, 1070)
            
            # Move smoothly to the missed coordinate (unfamiliar targeting)
            await move_mouse_like_human(page, start_x, start_y, miss_x, miss_y, steps=30, familiarity=0.20)
            
            # Execute the missed click (blank space)
            await self.execute_anatomical_click(page, approach_velocity=45.0)
            await asyncio.sleep(self.rng.uniform(0.300, 0.600))
            
            # Corrective movement to the actual target (now familiar targeting)
            await move_mouse_like_human(page, miss_x, miss_y, target_x, target_y, steps=10, familiarity=0.85)
            await self.execute_anatomical_click(page, approach_velocity=15.0)
            return

        # 1. Simulate initial rapid hand ballistic phase
        overshoot_x = target_x + self.rng.uniform(-15, 15) * (1.0 - familiarity * 0.50)
        overshoot_y = target_y + self.rng.uniform(-15, 15) * (1.0 - familiarity * 0.50)
        
        # Move smoothly to the overshoot coordinate
        await move_mouse_like_human(page, start_x, start_y, overshoot_x, overshoot_y, steps=30, familiarity=familiarity)
        await asyncio.sleep(self.rng.uniform(0.04, 0.12))

        # 2. Corrective motor phase (moves to the final target)
        await move_mouse_like_human(page, overshoot_x, overshoot_y, target_x, target_y, steps=12, familiarity=0.85)
        
        # 3. Execute anatomical click
        approach_velocity = (distance / 42.0) * profile.muscle_speed_factor
        await self.execute_anatomical_click(page, approach_velocity=approach_velocity)
        logger.info(f"HCI: Fitts's Law targeting and anatomical click completed on: {target_selector}")
