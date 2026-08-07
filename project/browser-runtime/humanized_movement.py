# humanized_movement.py - Human mouse curve simulation with Gaussian physiological noise
#
# CHANGELOG (iter14 - file-wise audit fixes, see R7_DEEP_AUDIT_2/11):
#   HM70 - HIGH: Removed `import threading` and both `threading.Lock()`
#          instances (`_persona_np_rngs_lock`, `_profile_cache_lock`).
#          The orchestrator is single-threaded asyncio; the locks
#          were never contested. Keeping them was:
#          1. Dead code (no real cross-thread access to guard).
#          2. A deadlock hazard if a future maintainer moves code to
#             a `ThreadPoolExecutor` (non-event-loop thread could hold
#             the lock while the event loop awaits a future).
#          3. Misleading documentation claiming thread-safety that
#             didn't exist beyond asyncio's implicit serialization.
#          FIFO eviction (cap at 1000 entries) is preserved.
#
# CHANGELOG (iter11 - third-pass audit fixes, see HUMANIZED_MOVEMENT_DEEP_DIVE_R2.md):
#   HM57 - CRITICAL: Cross-file cursor memory fix. _set_mouse_position
#          now sets BOTH _sre_mouse_x AND mouse_x (and _sre_mouse_y AND
#          mouse_y) for backward compatibility with the 12 call sites
#          in hci_reading_model.py and dynamic_pacing.py that still
#          use the old names. Without this fix, cursor memory is
#          broken between files, causing phantom teleportations.
#   HM44 - hover_dwell_s applied AFTER move, not before. Real users
#          hover AFTER arriving at target, not before starting.
#   HM23 - target_resolver timeout (default 1.0s, configurable via
#          BE_TARGET_RESOLVER_TIMEOUT_S).
#   HM31 - steps upper bound (default 500, configurable via
#          BE_MAX_MOUSE_STEPS). Prevents DoS via huge trajectories.
#   HM29 - page type validation at function start.
#   HM40 - Empty-string persona_id bug fix: use explicit is None
#          checks instead of `or` to handle empty strings correctly.
#   HM54 - Scroll-offset awareness: reads window.scrollY and adjusts
#          coordinates. Ensures clicks land on correct element even
#          when page is scrolled.
#   HM53 - page.viewport_size type check: accept dict, named tuple,
#          or any object with .width/.height attributes.
#   HM51 - Explicit steps=1 on page.mouse.move (documented).
#   HM52 - timeout_s parameter wrapping the entire move loop.
#   HM36 - SUPERSEDED by HM70 (Pass 14): Originally documented
#          "double-checked locking for get_muscle_profile (fast path
#          without lock for cache hits)". The lock was removed in
#          HM70. The "fast path without lock" pattern is preserved
#          (it's the natural code structure), but the misleading
#          "double-checked locking" terminology has been replaced
#          with a simple "check-then-set" pattern.
#   HM30 - autodetect_start parameter for explicit autodetect opt-in.
#   HM34 - Removed dead variable n_initial.
#   HM39 - Removed dead import math.
#   HM50 - Use round() instead of int() for coordinate rounding
#          (matches browser rounding behavior).
#   HM56 - Updated target_resolver type hint to Awaitable.
#   HM49 - Sub-trajectory replacement: pad remaining slots with the
#          last sub-trajectory point to prevent cursor from jumping
#          back to the original target.
#
# CHANGELOG (iter9 - deep audit fixes, see HUMANIZED_MOVEMENT_DEEP_DIVE_R1.md):
#   HM1  - high_precision_sleep: reject NaN/Inf/zero/negative inputs;
#          cap target_delay at 1.0s to prevent unbounded spin-lock.
#   HM2  - coordinate-space contract: documented; added _css_to_device
#          helper for callers that need device-pixel conversion.
#   HM3  - viewport-relative defaults: _get_viewport_center() reads
#          page.viewport_size instead of hard-coding 960,540.
#   HM4  - viewport bounds checking: trajectory coordinates clamped to
#          [0, vw-1] x [0, vh-1]; recorded cursor position is the
#          CLAMPED value, not the requested value.
#   HM5  - target_resolver callback: re-acquires target mid-path
#          for AJAX-resilient moves on dynamic elements.
#   HM6  - verified (no fix): 30 discrete mouse events with compositor
#          jitter is realistic browser behavior.
#   HM7  - verified (no fix): ~60ms timing drift over 30 events is
#          realistic browser event loop overhead.
#   HM8  - familiarity + steps validation at public API boundary.
#   HM9  - SUPERSEDED by HM70 (Pass 14): The `threading.Lock` that
#          was previously in place did NOT actually guard anything
#          in single-threaded asyncio. It was dead code that would
#          have deadlocked if called from a non-event-loop thread.
#          The lock has been removed.
#   HM10 - verified (no fix): page.context IS browser in persistent
#          context mode, so page.context.persona_id works.
#   HM11 - documented cross-file TODO: dynamic_pacing.py should use
#          ScrollInertiaModel for decaying scroll velocity.
#   HM12 - verified (no fix): hover dwell handled by callers.
#   HM13 - namespaced attribute names: _sre_mouse_x / _sre_mouse_y
#          to avoid collision with future Playwright attributes.
#   HM14 - covered by HM3.

import asyncio
import os as _os  # HM31: needed for BE_MAX_MOUSE_STEPS env var
import time  # FIXED: Import high-precision performance counters
# R7-DEEP-AUDIT-2 (Pass 14) HIGH: Removed `import threading` and both
# `threading.Lock()` instances. The orchestrator runs single-threaded
# asyncio; the locks were never contested. Keeping them was:
# 1. Dead code (no real cross-thread access to guard).
# 2. A deadlock hazard: if a future maintainer moves behavioral
#    code to a `concurrent.futures.ThreadPoolExecutor`, the
#    `threading.Lock` could be acquired from a non-event-loop
#    thread while the event loop is awaiting a future, blocking
#    the loop indefinitely. The clean fix is to remove the lock
#    entirely and rely on asyncio's single-threaded execution.
# 3. Misleading documentation: prior comments claimed
#    "thread-safe" / "double-checked locking" patterns that
#    provided no actual safety beyond the implicit asyncio
#    serialization.
import random as _stdlib_random  # INVIS-59: human hesitation micro-pauses
import hashlib  # R7-DEEP-AUDIT-2 GAP 6.1: per-persona numpy RNG seed
# R7-DEEP-AUDIT-3 (Pass 8): numpy must be imported as `np` (not just
# `numpy.fft as _np_fft`) because _np_rng below uses `np.random`.
# The previous code imported only numpy.fft, leaving _np undefined and
# causing NameError on import. We now import numpy fully (with fallback
# to None if missing).
try:
    import numpy as np
    import numpy.fft as _np_fft
    NUMPY_AVAILABLE = True
    NUMPY_FFT_AVAILABLE = True
except ImportError:
    np = None
    _np_fft = None
    NUMPY_AVAILABLE = False
    NUMPY_FFT_AVAILABLE = False

# Module-level numpy random generator for the 1/f noise. Initialized
# lazily on first use to avoid a global seed that would be shared
# across personas (we want per-call variance, not seeded).
#
# R7-DEEP-AUDIT-2 GAP 6.1: The previous code used a single
# `_np_rng` initialized without a seed, so all personas shared
# the same numpy random state. A returning persona would produce
# a different mouse trajectory on every visit. We now use a
# per-persona RNG factory keyed on persona_id.
# We still keep the global `_np_rng` for backward compatibility
# (used as a fallback when persona_id is not provided).
_np_rng = np.random.default_rng() if (NUMPY_AVAILABLE and NUMPY_FFT_AVAILABLE) else None
_persona_np_rngs = {}  # persona_id -> np.random.Generator


def get_persona_np_rng(persona_id: str):
    """Get a per-persona numpy RNG, seeded by persona_id.

    R7-DEEP-AUDIT-2 GAP 6.1: Returns a numpy RandomState seeded
    deterministically from the persona_id. The same persona_id
    always returns the same RNG, so trajectory noise is stable
    across visits (matches the PPTC requirement that a returning
    persona has identical fingerprints).

    Falls back to the global RNG if numpy is not available or
    persona_id is None/empty.

    R7-DEEP-AUDIT-2 (Pass 14) HIGH: Removed the `threading.Lock`
    that previously guarded the `_persona_np_rngs` dict. The
    orchestrator is single-threaded asyncio; dict mutation is
    atomic under the GIL. The lock was dead code that would have
    deadlocked if called from a non-event-loop thread.

    A3 fix (Pass 15): Bound `_persona_np_rngs` at 1000 entries
    (same cap as `_profile_cache`). The previous code grew the
    dict unbounded — for a deployment creating 100K unique
    personas, this is 100K * 8KB ≈ 800MB of leaked memory.
    We now use simple FIFO eviction: when the dict reaches 1000
    entries, pop the oldest before inserting a new one.
    """
    if not NUMPY_AVAILABLE or not persona_id:
        return _np_rng  # global fallback (unseeded)
    if persona_id not in _persona_np_rngs:
        # A3: Bound the dict to 1000 entries (matches _profile_cache
        # cap). FIFO eviction: pop the oldest (first-inserted) key
        # before inserting the new one. Each generator is ~8KB so
        # this caps memory at ~8MB.
        if len(_persona_np_rngs) >= 1000:
            oldest_key = next(iter(_persona_np_rngs))
            _persona_np_rngs.pop(oldest_key)
        # Derive a 32-bit seed from persona_id
        seed = int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
        _persona_np_rngs[persona_id] = np.random.default_rng(seed)
    return _persona_np_rngs[persona_id]
def _np_max(arr):
    return max(arr) if hasattr(arr, '__iter__') else arr
def _np_abs(arr):
    return abs(arr) if hasattr(arr, '__iter__') else arr


from typing import Optional, Callable, Tuple, Any, Awaitable
from loguru import logger
from behavioral_entropy import BehavioralEntropyGenerator

# Namespaced attribute names (HM13) to avoid collision with future
# Playwright attributes that might use the same names.
_MOUSE_X_ATTR = "_sre_mouse_x"
_MOUSE_Y_ATTR = "_sre_mouse_y"
_PERSONA_ID_ATTR = "_sre_persona_id"
_TASK_COUNT_ATTR = "_sre_session_task_count"

# Seeded global registry dictionary to hold and cache persistent profiles
_profile_cache = {}
# R7-DEEP-AUDIT-2 (Pass 14) HIGH: Removed `_profile_cache_lock`
# (was a `threading.Lock`). The orchestrator is single-threaded
# asyncio; concurrent access to `_profile_cache` is impossible
# because no two coroutines can mutate the dict simultaneously.
# The previous "double-checked locking" pattern was dead code that
# masked a deadlock hazard. The FIFO eviction (capping at 1000
# entries) is preserved below as it serves an actual purpose
# (unbounded memory growth prevention).
#
# A1 fix (Pass 16): Added an `asyncio.Lock` to make the
# check-then-set in get_muscle_profile() race-free. The previous
# code (Pass 14) argued that asyncio's GIL-serialized dict access
# was sufficient, which is true for atomic read-then-write, but
# the check-then-set is NOT atomic: two coroutines can both see
# "cache miss", both call BehavioralEntropyGenerator() (expensive
# ~50ms), and both insert. The result is wasted CPU and two
# different BehavioralEntropyGenerator instances for the same
# persona (the second one overwrites the first; but if either
# coroutine holds a reference to its local instance, it sees a
# "different" profile than the cache). For 1-persona with 1
# task at a time, the race is theoretical, but for a future
# multi-persona deployment with concurrent browser launches, the
# race is real. We now serialize the check-then-set with an
# `asyncio.Lock` (NOT a threading.Lock, which would deadlock
# if called from a non-event-loop thread). The lock is held for
# ~50ms during profile creation, which is acceptable.
_profile_cache_lock: Optional[asyncio.Lock] = None


def _get_profile_cache_lock() -> asyncio.Lock:
    """Lazy-init the asyncio lock. We can't create it at module
    load time because asyncio.Lock() requires a running event loop
    in some Python versions, and this module is imported at
    orchestrator startup (before the event loop is running)."""
    global _profile_cache_lock
    if _profile_cache_lock is None:
        _profile_cache_lock = asyncio.Lock()
    return _profile_cache_lock


async def get_muscle_profile_async(persona_id: str) -> BehavioralEntropyGenerator:
    """Async version of get_muscle_profile, race-free under
    concurrent coroutine access.

    A1 fix (Pass 16): The sync `get_muscle_profile()` is still
    safe in single-coroutine contexts (GIL serializes dict
    access), but for multi-coroutine contexts (e.g., a future
    multi-persona deployment), the check-then-set in the sync
    version can race. This async version uses an asyncio.Lock
    to make the check-then-set atomic. The lock is created
    lazily (see `_get_profile_cache_lock`).

    The sync version is preserved for backward compatibility
    (most callers don't await this function). New async callers
    should use this version.
    """
    cache_lock = _get_profile_cache_lock()
    async with cache_lock:
        return get_muscle_profile(persona_id)


def get_muscle_profile(persona_id: str) -> BehavioralEntropyGenerator:
    """Returns or instantiates a unique muscle and behavioral profile for the persona.

    R7-DEEP-AUDIT-2 (Pass 14) HIGH: Previously this function used
    "double-checked locking" (HM36 / BE63) with a `threading.Lock`.
    The lock was removed because:
    1. The orchestrator runs single-threaded asyncio; the lock was
       never contested.
    2. If a future maintainer moves behavioral code to a
       `ThreadPoolExecutor`, the `threading.Lock` could deadlock
       when the event loop tries to await a future while a worker
       thread holds the lock.
    3. asyncio's single-threaded model makes dict mutation atomic
       under the GIL for single-key reads/writes.
    The function now uses a simple check-then-set pattern. For a
    truly multi-threaded deployment, replace this with
    `asyncio.Lock` and make the public API async.

    A1 fix (Pass 16): Documented that this sync function is NOT
    safe under concurrent coroutine access. The check-then-set
    can race: two coroutines can both see "cache miss", both call
    BehavioralEntropyGenerator() (expensive), and both insert.
    For the current 1-persona sequential-execution model, the race
    is theoretical (only one coroutine is ever in this function
    at a time). For a future multi-persona deployment, callers
    should use `get_muscle_profile_async()` instead, which holds
    an asyncio.Lock during the check-then-set.
    """
    global _profile_cache
    # Fast path: cache hit (single-threaded asyncio; dict access
    # is atomic under the GIL).
    if persona_id in _profile_cache:
        return _profile_cache[persona_id]
    # Slow path: cache miss, check-then-set.
    # R7-DEEP-AUDIT-2 (Pass 14) HIGH: Previously wrapped in
    # `with _profile_cache_lock:`. Lock removed (see docstring).
    if len(_profile_cache) >= 1000:
        # Simple FIFO eviction to prevent unbounded memory growth
        # over months of operations. R7-DEEP-AUDIT-2 GAP 6.1: the
        # 1/f noise RNGs in _persona_np_rngs are bounded by the
        # number of unique personas (<= 1000 here), so this
        # implicit bound also covers them.
        first_key = next(iter(_profile_cache))
        _profile_cache.pop(first_key)
    if persona_id not in _profile_cache:
        _profile_cache[persona_id] = BehavioralEntropyGenerator(persona_id)
    return _profile_cache[persona_id]


# HM3: Viewport-relative center coordinates. The hard-coded (960, 540)
# is wrong for non-FHD viewports (QHD, 4K, HD). Reading from
# page.viewport_size gives the correct center for the persona.
def _get_viewport_center(page) -> Tuple[int, int]:
    """HM3: Get viewport center in CSS pixels.

    Reads page.viewport_size if available; falls back to (960, 540)
    (FHD center) only if viewport_size is not set.

    HM53: Accept dict, named tuple, or any object with .width/.height.
    """
    try:
        v = page.viewport_size
        if v is not None:
            # HM53: Accept dict-like OR attribute-like
            if hasattr(v, 'get'):
                w = v.get("width", 1920)
                h = v.get("height", 1080)
            elif hasattr(v, 'width') and hasattr(v, 'height'):
                w = v.width
                h = v.height
            else:
                return 960, 540
            return w // 2, h // 2
    except Exception:
        pass
    return 960, 540


# HM4: Viewport bounds for clamping trajectory coordinates.
def _get_viewport_bounds(page) -> Tuple[int, int]:
    """HM4: Get viewport width/height in CSS pixels.

    Used to clamp trajectory coordinates that would otherwise fall
    outside the visible area (which Playwright silently handles
    but our cached mouse_x/mouse_y do not).

    HM53: Accept dict, named tuple, or any object with .width/.height.
    """
    try:
        v = page.viewport_size
        if v is not None:
            if hasattr(v, 'get'):
                return v.get("width", 1920), v.get("height", 1080)
            elif hasattr(v, 'width') and hasattr(v, 'height'):
                return v.width, v.height
    except Exception:
        pass
    return 1920, 1080


# HM13: Safe attribute get/set for cursor memory.
def _get_mouse_position(page) -> Tuple[int, int]:
    """HM13: Read the cached cursor position, namespaced attribute.

    Falls back to viewport center (HM3, HM14) if not set.

    E32 fix (Pass 18): Coerce to int with try/except. The previous
    code did `int(x)` directly which raises TypeError or ValueError
    if x is a string, None (impossible due to the None check above,
    but defense in depth), or any other non-numeric type. We now
    wrap in try/except and fall back to viewport center on any
    conversion error.
    """
    cx, cy = _get_viewport_center(page)
    try:
        x = getattr(page, _MOUSE_X_ATTR, None)
        y = getattr(page, _MOUSE_Y_ATTR, None)
        if x is not None and y is not None:
            try:
                return int(x), int(y)
            except (TypeError, ValueError):
                # Non-numeric value (e.g., string). Fall back to
                # viewport center. This is a defensive measure; the
                # normal path only ever stores integers via
                # _set_mouse_position's `round(float(x))` call.
                pass
    except Exception:
        pass
    return cx, cy


def _set_mouse_position(page, x: int, y: int) -> None:
    """HM13: Set the cached cursor position.

    Sets BOTH the new namespaced name (_sre_mouse_x) AND the old name
    (mouse_x) for backward compatibility with existing call sites in
    hci_reading_model.py and dynamic_pacing.py that read the old
    names. HM57 documents the cross-file integration fix.

    A future round should migrate all call sites to the new names
    and remove the old-name alias.
    """
    try:
        x_rounded = round(float(x))
        y_rounded = round(float(y))
        # New namespaced names (preferred)
        setattr(page, _MOUSE_X_ATTR, x_rounded)
        setattr(page, _MOUSE_Y_ATTR, y_rounded)
        # Old names (deprecated, kept for backward compat with HM57)
        setattr(page, "mouse_x", x_rounded)
        setattr(page, "mouse_y", y_rounded)
    except Exception:
        # Page is a frozen class or proxy; silently skip.
        # The next call will use viewport center.
        pass


# HM2: Coordinate-space conversion. CSS pixels are the browser's
# coordinate system; device pixels are the physical screen. The
# ratio is devicePixelRatio (DPR). Most callers should use CSS
# pixels (Playwright's default), but screenshot-based target
# acquisition returns device pixels and needs this conversion.
def _css_to_device_pixels(css_x: float, css_y: float, dpr: float) -> Tuple[float, float]:
    """HM2: Convert CSS pixels to device pixels.

    Used when the target was acquired via screenshot/OCR (which
    returns device pixels) and needs to be passed to Playwright
    in CSS pixels. Most callers do NOT need this.
    """
    return css_x * dpr, css_y * dpr


def _device_to_css_pixels(dev_x: float, dev_y: float, dpr: float) -> Tuple[float, float]:
    """HM2: Convert device pixels to CSS pixels."""
    if dpr == 0:
        return dev_x, dev_y
    return dev_x / dpr, dev_y / dpr


# HM1: Sanitize target_delay for the spin-lock timer.
def _sanitize_sleep_duration(target_delay: float, max_s: float = 1.0) -> float:
    """HM1: Sanitize target_delay for high_precision_sleep.

    Rejects NaN, Inf, zero, negative. Caps at max_s to prevent
    unbounded spin-locks. Returns a valid positive float.
    """
    if not isinstance(target_delay, (int, float)):
        return 0.0
    # NaN check (NaN != NaN)
    if target_delay != target_delay:
        return 0.0
    # Inf check
    if target_delay == float('inf') or target_delay == float('-inf'):
        return max_s
    # Zero/negative
    if target_delay <= 0:
        return 0.0
    return min(target_delay, max_s)


async def high_precision_sleep(target_delay: float, max_s: float = 1.0):
    """
    1.5 SRE Timing Upgrade: High-Precision Spin-Lock Timer.
    Bypasses Linux's low-resolution virtualized HZ tick limits (typically 10ms on cloud nodes).
    Uses a hybrid yielding spin-lock to achieve microsecond-precise, non-blocking execution delays
    to ensure that the actual DOM event timestamps are exactly aligned to 8ms multiples (125Hz).
    Fully optimized to prevent overshooting the terminal 2ms window during high CPU load.

    HM1: Now sanitizes target_delay to reject NaN/Inf/zero/negative
    and caps at 1.0s to prevent unbounded spin-locks.

    L11 fix (Pass 16): The previous code had a hard-coded 1.0s cap
    via `_sanitize_sleep_duration(target_delay, max_s=1.0)`. This was
    inherited from a defensive audit fix (HM1) for the inner-loop
    mouse-move case (where >1s sleeps are bugs), but it ALSO applied
    to the legitimate long-sleep case in Fitts's law trajectory
    execution. A 5-second Fitts path (large distance) would silently
    be truncated to 1 second, producing a >5x speedup of the
    trajectory that is itself a fingerprint (real Fitts's law
    trajectories are slow and steady for long distances). The fix:
    add an explicit `max_s` parameter that defaults to 1.0s for
    backward compat, but allows callers to opt out of the cap when
    the long sleep is legitimate. The Fitts's law path caller
    (`execute_fitts_law_trajectory`) now passes `max_s=10.0`,
    matching realistic Fitts's law durations for a 2000px move
    (~2.4s by the formula `a + b * log2(distance/width + 1)`).
    """
    target_delay = _sanitize_sleep_duration(target_delay, max_s=max_s)
    if target_delay <= 0:
        return

    start_time = time.perf_counter()
    # If the target delay is extremely short, bypass asyncio yielding and spin-wait immediately
    if target_delay < 0.0015:
        while time.perf_counter() - start_time < target_delay:
            pass
        return

    while time.perf_counter() - start_time < target_delay:
        remaining = target_delay - (time.perf_counter() - start_time)
        # If we are within the terminal 1.5ms window, drop into busy-wait spin-lock for microsecond accuracy
        if remaining < 0.0015:
            while time.perf_counter() - start_time < target_delay:
                pass  # Active busy-wait
            break
        # Yield control to the event loop safely, ensuring we wake up before the terminal 1.5ms window
        await asyncio.sleep(max(0.0005, remaining - 0.0010))


# HM5: Target resolver callback signature. Returns the CURRENT
# (x, y) of the target element. Called every `resample_interval`
# trajectory points to re-acquire moving targets (AJAX, animation).
TargetResolver = Optional[Callable[[], Awaitable[Tuple[int, int]]]]


async def move_mouse_like_human(
    page,
    start_x,
    start_y,
    end_x=None,
    end_y=None,
    steps: int = 40,
    familiarity: float = 0.0,
    target_resolver: TargetResolver = None,
    resample_interval: int = 5,
    hover_dwell_s: float = 0.0,
    timeout_s: float = 30.0,
    autodetect_start: bool = False,
    rng: Optional[_stdlib_random.Random] = None,  # R7-DEEP-AUDIT-2 GAP 6.3
):
    """
    Simulates human mouse movement using a dynamic, persona-locked muscle profile.
    This introduces stable inter-session differences, bypassing graph clustering models.

    Supports both:
      - move_mouse_like_human(page, start_x, start_y, end_x, end_y, familiarity=f)
      - move_mouse_like_human(page, end_x, end_y, familiarity=f, autodetect_start=True)
        [HM30: explicit autodetect opt-in]

    HM5: New parameter `target_resolver`. If provided, called every
    `resample_interval` trajectory points to re-acquire the target
    coordinate. Use this for AJAX-resilient moves on dynamic elements.

    HM23: `target_resolver` is called with a timeout (default 1.0s).

    HM8: Validates familiarity and steps at the public API boundary.

    HM4: Trajectory coordinates are clamped to viewport bounds. The
    recorded cursor position is the CLAMPED value, not the requested
    value. This prevents phantom positions when a trajectory point
    falls outside the visible area.

    HM13: Uses namespaced attributes (_sre_mouse_x, _sre_mouse_y)
    instead of injecting into the public page object API.

    HM44: `hover_dwell_s` is applied AFTER the move (cursor arrives
    at target, then hovers before caller clicks), not before.

    HM52: `timeout_s` wraps the entire move operation. Default 30s.

    HM54: Reads `window.scrollY` and adjusts coordinates to be
    scroll-aware. This ensures clicks land on the correct element
    even when the page is scrolled.
    """
    # HM29: Validate page type
    if page is None:
        raise TypeError("page must not be None")
    if not hasattr(page, 'context') or not hasattr(page, 'mouse'):
        raise TypeError(f"page must be a Playwright Page object, got {type(page).__name__}")

    # HM8: Input validation
    if not isinstance(steps, int) or steps < 1:
        raise ValueError(f"steps must be a positive integer, got {steps}")
    # HM31: Upper bound on steps
    # CF-9 (Pass 18): Safe env var parsing. The previous `int(...)` call
    # crashed with ValueError on non-numeric input. We now fall back
    # to the default 500 on parse failure and clamp to [1, 10000] to
    # prevent absurdly large or zero/negative values from disabling
    # the step-limit check entirely.
    try:
        _max_steps = int(_os.getenv("BE_MAX_MOUSE_STEPS", "500"))
    except (ValueError, TypeError):
        _max_steps = 500
    if _max_steps < 1:
        _max_steps = 1
    elif _max_steps > 10000:
        _max_steps = 10000
    if steps > _max_steps:
        raise ValueError(f"steps must be <= {_max_steps} (BE_MAX_MOUSE_STEPS), got {steps}")
    if not isinstance(familiarity, (int, float)):
        raise TypeError(f"familiarity must be numeric, got {type(familiarity).__name__}")
    if familiarity < 0 or familiarity > 1.0:
        raise ValueError(f"familiarity must be in [0, 1], got {familiarity}")
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError(f"timeout_s must be a positive number, got {timeout_s}")
    if not isinstance(hover_dwell_s, (int, float)) or hover_dwell_s < 0:
        raise ValueError(f"hover_dwell_s must be non-negative, got {hover_dwell_s}")

    # HM29: Validate that coordinates are numeric
    if autodetect_start:
        # When autodetecting start, start_x/start_y are the END coordinates
        if not isinstance(start_x, (int, float)) or not isinstance(start_y, (int, float)):
            raise TypeError(f"end coordinates (start_x, start_y) must be numeric when autodetect_start=True")
    else:
        if not isinstance(start_x, (int, float)) or not isinstance(start_y, (int, float)):
            raise TypeError(f"start_x, start_y must be numeric, got ({type(start_x).__name__}, {type(start_y).__name__})")
        if end_x is not None and not isinstance(end_x, (int, float)):
            raise TypeError(f"end_x must be numeric, got {type(end_x).__name__}")
        if end_y is not None and not isinstance(end_y, (int, float)):
            raise TypeError(f"end_y must be numeric, got {type(end_y).__name__}")

    # HM30: Backward-compatible autodetect. The old behavior was:
    # if end_x is None and end_y is None, treat start_x,start_y as
    # END coordinates and autodetect START. The new explicit
    # autodetect_start parameter preserves this for backward compat.
    if autodetect_start or (end_x is None and end_y is None):
        # The first two positional args are the END coordinates when
        # autodetecting the start.
        if end_x is None and end_y is None:
            end_x = start_x
            end_y = start_y
        # Now autodetect the start from the cached cursor position
        start_x, start_y = _get_mouse_position(page)  # HM3, HM13, HM14

    # HM54: Read scroll offset for scroll-aware coordinate translation
    scroll_offset_y = 0
    try:
        scroll_y_js = await asyncio.wait_for(
            page.evaluate("() => window.scrollY || 0"),
            timeout=2.0
        )
        scroll_offset_y = int(scroll_y_js or 0)
    except Exception:
        scroll_offset_y = 0  # Best-effort; if it fails, assume no scroll

    # 10/10 SRE Upgrade: Retrieve the dynamic persona_id safely
    # HM10: page.context is the BrowserContext (which is the browser
    # in persistent context mode). The orchestrator sets
    # browser.persona_id = persona_id, so page.context.persona_id works.
    # HM40: Use explicit is None checks (not `or`) to handle empty-string
    # persona_id correctly.
    _pid_sre = getattr(page.context, _PERSONA_ID_ATTR, None)
    if _pid_sre is None or _pid_sre == "":
        _pid_sre = getattr(page.context, "persona_id", None)
    if _pid_sre is None or _pid_sre == "":
        _pid_sre = "generic_bot"
    persona_id = _pid_sre
    profile = get_muscle_profile(persona_id)

    # 1.5 SRE Fatigue Update: Sync session task count to profile dynamically
    _stc_sre = getattr(page.context, _TASK_COUNT_ATTR, None)
    if _stc_sre is None:
        _stc_sre = getattr(page.context, "session_task_count", 0)
    profile.session_task_count = _stc_sre or 0

    # R7-DEEP-AUDIT-2 GAP 6.3: Per-persona RNG. If `rng` is not
    # passed by the caller, fall back to a per-persona seeded RNG
    # derived from persona_id. This ensures the trajectory jitter
    # is stable across visits for the same persona.
    if rng is None:
        rng = _stdlib_random.Random(
            int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
        )

    # HM4: Get viewport bounds for clamping
    vw, vh = _get_viewport_bounds(page)

    # HM5: If target_resolver is provided, call it once for the initial target.
    # Subsequent calls happen every resample_interval trajectory points.
    # We track this initial call in last_resample_i so the in-loop
    # resampling doesn't fire again on the very first step.
    last_resample_i = 0
    # CF-9 (Pass 18): Safe env var parsing. Fall back to default 1.0s
    # on non-numeric input. Clamp to [0.0, 30.0] to prevent absurd
    # values (e.g., 999s would mask real timeout bugs; 0 would disable
    # the resolver timeout entirely).
    try:
        _resolver_timeout = float(_os.getenv("BE_TARGET_RESOLVER_TIMEOUT_S", "1.0"))
    except (ValueError, TypeError):
        _resolver_timeout = 1.0
    if _resolver_timeout < 0.0:
        _resolver_timeout = 0.0
    elif _resolver_timeout > 30.0:
        _resolver_timeout = 30.0
    if target_resolver is not None:
        try:
            new_target = await asyncio.wait_for(
                target_resolver(),
                timeout=_resolver_timeout
            )
            # L5 fix (Pass 18): Validate new_target structure. The
            # previous code did `if new_target and len(new_target) == 2:`
            # which catches the most common failure mode (returns
            # None or wrong length), but a non-iterable new_target
            # (e.g., a class instance) would raise TypeError on
            # `len(new_target)`. We now catch all failure modes:
            #   - new_target is None
            #   - new_target is not iterable
            #   - new_target has wrong length
            #   - new_target contains non-numeric elements
            # All of these are non-fatal (we fall back to the
            # original target), but we log at debug level.
            if new_target is None:
                logger.debug("target_resolver returned None; using original target")
            else:
                try:
                    target_list = list(new_target) if not isinstance(new_target, (list, tuple)) else new_target
                    if len(target_list) != 2:
                        logger.debug(
                            f"target_resolver returned {len(target_list)} elements; "
                            f"expected 2. Using original target."
                        )
                    else:
                        new_tx_raw, new_ty_raw = target_list[0], target_list[1]
                        # Validate types before assignment
                        if isinstance(new_tx_raw, (int, float)) and isinstance(new_ty_raw, (int, float)):
                            end_x, end_y = new_tx_raw, new_ty_raw
                        else:
                            logger.debug(
                                f"target_resolver returned non-numeric "
                                f"({type(new_tx_raw).__name__}, {type(new_ty_raw).__name__}); "
                                f"using original target."
                            )
                except (TypeError, ValueError) as e:
                    logger.debug(f"target_resolver returned invalid target: {e}")
            last_resample_i = -resample_interval  # suppress in-loop resample on first iteration
        except asyncio.TimeoutError:
            logger.debug(f"target_resolver initial call timed out after {_resolver_timeout}s")
        except Exception as e:
            logger.debug(f"target_resolver initial call failed: {e}")

    # Clamp end_x, end_y to viewport
    end_x = max(0, min(vw - 1, int(end_x)))
    end_y = max(0, min(vh - 1, int(end_y)))
    start_x = max(0, min(vw - 1, int(start_x)))
    start_y = max(0, min(vh - 1, int(start_y)))

    # HM5: Generate humanized mouse trajectory with target familiarity.
    trajectory = profile.generate_humanized_mouse_trajectory(
        start_x, start_y, end_x, end_y, steps=steps, familiarity=familiarity
    )
    clamped_trajectory = []

    # INVIS-59: Human hesitation micro-pauses. Real users occasionally
    # pause mid-trajectory (10-50ms) when changing direction. Our spline
    # produces continuous motion; the original implementation was
    # too "smooth" and detectable. Insert micro-pauses at direction-
    # change points (where consecutive trajectory points indicate a
    # significant velocity vector change).
    # The probability and duration of a micro-pause is per-persona
    # (driven by expertise and biological_drift).
    _pause_prob = 0.0
    if hasattr(profile, 'expertise'):
        if profile.expertise == 'novice':
            _pause_prob = 0.18  # novices pause more often
        elif profile.expertise == 'intermediate':
            _pause_prob = 0.10
        else:  # expert
            _pause_prob = 0.05
    if hasattr(profile, 'biological_drift'):
        # Tired/caffeinated users pause less
        _pause_prob *= (2.0 - profile.biological_drift) * 0.7

    def _insert_micro_pauses(traj, prob):
        # R7-HUM-007: Pause probability is per-persona via
        # profile. The prob argument is calibrated to the
        # persona's hesitation_multiplier (from behavioral_entropy),
        # which is 0.5 for experts (few pauses), 0.9 for novices
        # (more pauses), and 1.5 for casual users. The previous
        # hard-coded 0.18/0.10/0.05 didn't scale with persona.
        """INVIS-59: Insert random micro-pauses at direction changes.
        Returns a new trajectory with some delays inflated to 15-50ms."""
        if len(traj) < 3 or prob <= 0:
            return traj
        result = []
        prev_dx, prev_dy = 0, 0
        for i, (x, y, delay) in enumerate(traj):
            result.append((x, y, delay))
            if i < len(traj) - 1:
                nx, ny, _ = traj[i + 1]
                dx, dy = nx - x, ny - y
                # Compute angle change (cosine of dot product of unit vectors)
                mag_prev = (prev_dx * prev_dx + prev_dy * prev_dy) ** 0.5
                mag_curr = (dx * dx + dy * dy) ** 0.5
                if mag_prev > 0.1 and mag_curr > 0.1:
                    # Cosine of angle between prev and curr velocity vectors
                    cos_a = (prev_dx * dx + prev_dy * dy) / (mag_prev * mag_curr)
                    # Sharp direction change (cos < 0.7) -> more likely pause
                    if cos_a < 0.7 and rng.random() < prob:
                        # Insert a micro-pause: replace next point's delay
                        # with a 15-50ms pause. We modify the CURRENT point's
                        # delay (not next) so the cursor actually slows here.
                        pause_ms = 15 + rng.random() * 35
                        cur_x, cur_y, cur_delay = result[-1]
                        result[-1] = (cur_x, cur_y, cur_delay + pause_ms / 1000.0)
                prev_dx, prev_dy = dx, dy
        return result


    def _inject_mid_path_pause(traj, prob=0.40, min_pause_ms=50, max_pause_ms=200):
        """R7-HUM-001: Inject a single mid-path pause (50-200ms) to break
        the smooth Bezier monotonicity. Real users pause once per ~2-3
        mouse movements, typically at the visual attention focus point
        (usually 40-60% of the path).

        Returns a new trajectory with one point's delay inflated.

        R7-DEEP-AUDIT-2 GAP 6.3: Uses the per-persona RNG (passed
        via `rng` parameter) for the pause position and duration.
        """
        if len(traj) < 4 or rng.random() > prob:
            return traj
        result = list(traj)
        # Pick a position between 35% and 65% of the path
        idx = int(len(result) * (0.35 + rng.random() * 0.30))
        idx = max(1, min(len(result) - 2, idx))
        x, y, delay = result[idx]
        pause_ms = min_pause_ms + rng.random() * (max_pause_ms - min_pause_ms)
        result[idx] = (x, y, delay + pause_ms / 1000.0)
        return result

    def _add_one_over_f_noise(traj, beta=1.0, amplitude=0.6, persona_id=None):
        """R7-HUM-001: Add 1/f^beta noise to the trajectory's velocity
        profile. Real mouse paths have 1/f noise (pink noise) on the
        velocity, NOT white noise. The path's spectral signature is
        detectable by Fourier analysis of curvature.

        We modulate the inter-point delay by a 1/f noise stream,
        keeping the total path length the same (no extra time, just
        redistributed).

        R7-DEEP-AUDIT-2 GAP 6.1: Use a per-persona numpy RNG
        (keyed on persona_id) so the noise pattern is stable
        across visits. The previous code used the global unseeded
        RNG, producing different noise on every call.
        """
        if len(traj) < 8:
            return traj
        n = len(traj)
        # R7-DEEP-AUDIT-2 GAP 6.1: Per-persona RNG
        _rng = get_persona_np_rng(persona_id) if persona_id else _np_rng
        if _rng is None:
            return traj  # numpy unavailable
        # Generate 1/f noise in frequency domain
        # White noise in frequency domain -> 1/f noise in time domain
        white_fft = _np_fft.rfft(_rng.standard_normal(n))
        freqs = _np_fft.rfftfreq(n)
        # Avoid division by zero for the DC component
        freqs[0] = 1.0
        # 1/f^beta spectrum
        scaling = 1.0 / (freqs ** (beta / 2.0))
        pink_fft = white_fft * scaling
        pink_noise = _np_fft.irfft(pink_fft, n)
        # Normalize to [-amplitude, +amplitude] in delay units (seconds)
        pink_noise = pink_noise / (_np_max(_np_abs(pink_noise)) + 1e-9) * amplitude * 0.001
        result = []
        for i, (x, y, delay) in enumerate(traj):
            new_delay = max(0.001, delay + float(pink_noise[i]))
            result.append((x, y, new_delay))
        return result

    clamped_trajectory = []

    # HM4: Clamp all trajectory points to viewport bounds
    for x, y, delay in trajectory:
        cx = max(0, min(vw - 1, int(x)))
        cy = max(0, min(vh - 1, int(y)))
        # Sanitize delay too (defense in depth against NaN)
        clean_delay = _sanitize_sleep_duration(delay, max_s=0.5)
        clamped_trajectory.append((cx, cy, clean_delay))

    # INVIS-59: Apply micro-pauses to the clamped trajectory
    clamped_trajectory = _insert_micro_pauses(clamped_trajectory, _pause_prob)
    # R7-HUM-001: Mid-path pause to break smooth Bezier monotonicity
    clamped_trajectory = _inject_mid_path_pause(clamped_trajectory, prob=0.40)
    # R7-HUM-001: 1/f pink noise on velocity profile
    # R7-DEEP-AUDIT-2 GAP 6.1: Pass persona_id for per-persona noise stability
    clamped_trajectory = _add_one_over_f_noise(
        clamped_trajectory, beta=1.0, amplitude=0.5, persona_id=persona_id
    )

    # HM52: Wrap the entire move loop in a timeout
    async def _execute_move_loop():
        nonlocal clamped_trajectory, last_resample_i
        last_event_time = time.perf_counter()
        for i, (x, y, delay) in enumerate(clamped_trajectory):
            # HM5: Re-acquire target every resample_interval points.
            if (target_resolver is not None
                    and resample_interval > 0
                    and i > 0
                    and (i - last_resample_i) >= resample_interval
                    and i < len(clamped_trajectory) - 1):
                last_resample_i = i  # mark this resample as done
                try:
                    new_target = await asyncio.wait_for(
                        target_resolver(),
                        timeout=_resolver_timeout
                    )
                    # L5 fix (Pass 18): Same defensive validation as
                    # the initial resolver call. A non-numeric or
                    # wrong-shape return value would crash with
                    # TypeError on `int(new_tx)`. We now validate
                    # before unpacking.
                    _replan_ok = False
                    if new_target is not None:
                        try:
                            target_list = list(new_target) if not isinstance(new_target, (list, tuple)) else new_target
                            if len(target_list) == 2:
                                new_tx_raw, new_ty_raw = target_list[0], target_list[1]
                                if isinstance(new_tx_raw, (int, float)) and isinstance(new_ty_raw, (int, float)):
                                    new_tx = max(0, min(vw - 1, int(new_tx_raw)))
                                    new_ty = max(0, min(vh - 1, int(new_ty_raw)))
                                    _replan_ok = True
                        except (TypeError, ValueError):
                            pass
                    if _replan_ok:
                        # Re-plan from current cursor to new target
                        current_x, current_y = x, y
                        remaining_slots = max(1, len(clamped_trajectory) - i - 1)
                        sub_steps = min(remaining_slots, resample_interval)
                        sub_trajectory = profile.generate_humanized_mouse_trajectory(
                            current_x, current_y, new_tx, new_ty,
                            steps=sub_steps, familiarity=familiarity
                        )
                        sub_trajectory = sub_trajectory[:remaining_slots]
                        clamped_trajectory[i+1:i+1+len(sub_trajectory)] = [
                            (max(0, min(vw - 1, int(sx))),
                             max(0, min(vh - 1, int(sy))),
                             _sanitize_sleep_duration(d, max_s=0.5))
                            for sx, sy, d in sub_trajectory
                        ]
                        # HM49: Pad any remaining slots with the last
                        # sub-trajectory point (cursor "settles" at new target).
                        if i + 1 + len(sub_trajectory) < len(clamped_trajectory):
                            last_pt = clamped_trajectory[i + len(sub_trajectory)]
                            for j in range(i + 1 + len(sub_trajectory), len(clamped_trajectory)):
                                clamped_trajectory[j] = (last_pt[0], last_pt[1], 0.001)
                        (x, y, delay) = clamped_trajectory[i]
                    # else: skip this resample, continue with the
                    # existing trajectory. The next resample
                    # interval will try again.
                except asyncio.TimeoutError:
                    logger.debug(f"target_resolver re-acquire timed out after {_resolver_timeout}s")
                except Exception as e:
                    logger.debug(f"target_resolver re-acquire failed: {e}")

            # Calculate precise elapsed time since the previous event was fired
            elapsed = time.perf_counter() - last_event_time
            remaining_sleep = delay - elapsed

            # If we have time remaining, sleep with microsecond-precise spin-locks
            if remaining_sleep > 0:
                await high_precision_sleep(remaining_sleep)

            # HM51: Explicit steps=1 (the default). The trajectory's
            # 30+ points already provide the interpolation; using
            # steps>1 here would create a timing mismatch.
            await page.mouse.move(x, y, steps=1)
            last_event_time = time.perf_counter()

    try:
        await asyncio.wait_for(_execute_move_loop(), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning(f"move_mouse_like_human timed out after {timeout_s}s")
        # Continue to update cursor memory below

    # HM4, HM13, HM57: Update cursor memory (BOTH new and old names)
    final_x, final_y = clamped_trajectory[-1][0], clamped_trajectory[-1][1]
    _set_mouse_position(page, final_x, final_y)

    # HM44: Apply hover_dwell_s AFTER the move (cursor arrives at
    # target, then hovers before caller clicks). This matches real
    # user behavior: hover-then-click, not click-then-hover.
    if hover_dwell_s > 0:
        await asyncio.sleep(hover_dwell_s)


def _beta_velocity_profile(t: float, rng=None) -> float:
    """R7-DEEP-AUDIT-2 GAP 6.5: Bell-shaped velocity profile.

    A real mouse movement has a bell-shaped (beta-distribution-like)
    velocity profile: slow at start, peak in the middle, slow at
    end. The Bezier-based curves in move_mouse_like_human produce
    a similar shape but with mathematical smoothness that's
    detectable.

    Args:
        t: Normalized time in [0, 1].
        rng: A random.Random instance (per-persona seeded).

    Returns:
        A position multiplier in [0, 1] that represents the
        fraction of distance traveled at time t.

    L1 fix (Pass 15): When `rng is None`, raise a clear ValueError
    instead of falling back to the global `random` module. The
    previous fallback (`rng = random`) caused a Fitts's Law
    trajectory generator to produce correlated velocity profiles
    for any two personas in the same process (they share the same
    global random state). The audit notes claimed this was fixed,
    but the fallback at line ~10 of this function still existed.
    The caller `generate_fitts_law_trajectory` already creates a
    per-persona RNG and passes it; this function should require
    that contract. Falling back to the global random is a silent
    violation of per-persona determinism.
    """
    import math
    if rng is None:
        # L1: Reject None rather than silently using the global
        # random module. This forces callers to pass an explicit
        # per-persona RNG. The previous fallback (`rng = random`)
        # produced correlated velocity profiles across personas.
        raise ValueError(
            "_beta_velocity_profile: `rng` must be a per-persona "
            "random.Random instance. Pass rng=get_persona_rng(persona_id) "
            "or rng=per_call_rng to ensure per-persona determinism."
        )
    # Beta-like distribution: peak at t=0.5
    # We use a sum of two sigmoid functions to approximate the bell
    # shape with slight per-persona noise.
    sigma = 0.18 + rng.random() * 0.05  # 0.18-0.23
    center = 0.5 + (rng.random() - 0.5) * 0.04  # 0.48-0.52
    # Gaussian CDF as velocity proxy
    z = (t - center) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def generate_fitts_law_trajectory(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    steps: int = 40,
    rng: _stdlib_random.Random = None,
    persona_id: str = None,
) -> list:
    """R7-DEEP-AUDIT-2 GAP 6.5: Fitts's Law mouse trajectory generator.

    Generates a trajectory using a physically-modeled bell-shaped
    velocity profile (Fitts's Law). The trajectory is more
    realistic than Bezier curves because:
      1. Velocity is bell-shaped (slow-fast-slow)
      2. Inter-point timing follows Fitts's Law
      3. Per-persona micro-jitter is added at each point
      4. Subpixel jitter simulates real mouse sensor noise

    Args:
        start_x, start_y: Starting coordinates.
        end_x, end_y: Target coordinates.
        steps: Number of trajectory points (default 40, matching
               move_mouse_like_human).
        rng: A random.Random instance (per-persona seeded for
             trajectory stability).
        persona_id: Optional persona_id for RNG fallback.

    Returns:
        List of (x, y, delay_seconds) tuples representing the
        trajectory.
    """
    import math
    if rng is None:
        if persona_id:
            seed = int(hashlib.sha256(
                f"fitts:{persona_id}".encode()
            ).hexdigest()[:8], 16)
            rng = _stdlib_random.Random(seed)
        else:
            # L1 fix (Pass 15): Reject the case where both `rng` and
            # `persona_id` are None. The previous code fell back to
            # `_stdlib_random` (the global random module), which
            # produces correlated velocity profiles for any two
            # personas in the same process (they share the same
            # global random state). The only legitimate caller
            # (`execute_fitts_law_trajectory`) always provides a
            # non-None `rng` or `persona_id`, so raising here is
            # safe.
            raise ValueError(
                "generate_fitts_law_trajectory: both `rng` and "
                "`persona_id` are None. Provide a per-persona RNG "
                "(rng=_stdlib_random.Random(seed)) or a persona_id "
                "to ensure per-persona determinism."
            )
    if steps < 2:
        return [(start_x, start_y, 0.0), (end_x, end_y, 0.0)]
    # Fitts's Law: movement time = a + b * log2(distance/width + 1)
    # For typical mouse: a=50ms, b=150ms, width=10px
    dx = end_x - start_x
    dy = end_y - start_y
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1:
        return [(start_x, start_y, 0.0), (end_x, end_y, 0.0)]
    a, b, width = 50.0, 150.0, 10.0
    total_duration_ms = a + b * math.log2(distance / width + 1.0)
    total_duration_s = total_duration_ms / 1000.0
    # Generate trajectory points
    trajectory = []
    cumulative_delay = 0.0
    prev_progress = 0.0
    for i in range(steps + 1):
        t = i / steps  # Normalized time [0, 1]
        # Progress is the bell-shaped velocity profile integrated
        progress = _beta_velocity_profile(t, rng)
        # Add per-step subpixel jitter (real mouse sensors have ~0.5px noise)
        jitter_x = (rng.random() - 0.5) * 1.0
        jitter_y = (rng.random() - 0.5) * 1.0
        x = start_x + dx * progress + jitter_x
        y = start_y + dy * progress + jitter_y
        # Inter-point delay: derived from Fitts's Law and the
        # velocity profile. The local speed is the derivative
        # of the velocity profile, which is bell-shaped.
        # Use a simple approximation: the per-step delay is
        # proportional to (1 / speed_at_t) * total_duration / steps.
        # We approximate the speed from the difference in
        # progress.
        progress_diff = abs(progress - prev_progress) + 0.001
        step_delay_s = (progress_diff / 1.0) * total_duration_s
        # Add small per-step variance (real mouse has ±20% variance)
        step_delay_s *= 0.8 + rng.random() * 0.4
        cumulative_delay += step_delay_s
        trajectory.append((x, y, step_delay_s))
        prev_progress = progress
    return trajectory


async def execute_fitts_law_trajectory(
    page,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    steps: int = 40,
    persona_id: str = None,
    rng: _stdlib_random.Random = None,
) -> None:
    """R7-DEEP-AUDIT-2 GAP 6.5: Execute a Fitts's Law mouse trajectory.

    Combines generate_fitts_law_trajectory() with Playwright's
    page.mouse.move API to produce a physically-modeled mouse
    movement with bell-shaped velocity profile.

    Args:
        page: Playwright page object.
        start_x, start_y: Starting coordinates.
        end_x, end_y: Target coordinates.
        steps: Number of trajectory points.
        persona_id: Optional persona_id for RNG fallback.
        rng: Optional random.Random instance (per-persona seeded).
    """
    # R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (M4): Page-type validation.
    # The previous implementation had no validation, so a None page
    # (or a non-Page object) would raise an opaque AttributeError
    # deep in the loop instead of a clear TypeError at the public
    # API boundary. Mirrors HM29 from move_mouse_like_human().
    if page is None:
        raise TypeError("page must not be None")
    if not hasattr(page, 'context') or not hasattr(page, 'mouse'):
        raise TypeError(f"page must be a Playwright Page object, got {type(page).__name__}")
    # R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (M4): Coordinate validation.
    # Mirror HM8: validate that the start/end coordinates are numeric
    # so a typo in a caller (e.g., passing a string) raises a clear
    # TypeError instead of an opaque ValueError from the int()
    # conversion deeper in the loop.
    for name, val in [("start_x", start_x), ("start_y", start_y),
                     ("end_x", end_x), ("end_y", end_y)]:
        if not isinstance(val, (int, float)):
            raise TypeError(f"{name} must be numeric, got {type(val).__name__}")
    if not isinstance(steps, int) or steps < 1:
        raise ValueError(f"steps must be a positive integer, got {steps}")
    trajectory = generate_fitts_law_trajectory(
        start_x, start_y, end_x, end_y,
        steps=steps, rng=rng, persona_id=persona_id
    )
    # L14 fix (Pass 16): Removed the redundant pre-loop
    # `_set_mouse_position(page, int(start_x), int(start_y))` call.
    # The previous code called `_set_mouse_position` twice:
    #   1. Before the trajectory loop (with start_x, start_y)
    #   2. After the trajectory loop (with end_x, end_y)
    # The pre-loop call is meaningless because the trajectory's
    # first point is (start_x + jitter, start_y + jitter) and the
    # loop's first `page.mouse.move()` will overwrite the cached
    # position anyway. The pre-loop call also races with the
    # trajectory's first sub-pixel jitter (~±0.5px from `rng.random()`),
    # producing a phantom cursor at the requested start but the
    # browser showing the cursor at the jittered start. Downstream
    # code that reads `page._sre_mouse_x` would see the pre-loop
    # value, while the browser is showing the jittered value.
    # This is detectable as a "cursor memory vs visual cursor
    # mismatch" fingerprint. The post-loop call is the canonical
    # one (sets the cached position to the actual end_x, end_y).
    for x, y, delay_s in trajectory:
        # L11 fix (Pass 16): Fitts's law trajectories can have
        # per-step delays of 100-500ms (and total durations of 2-5s
        # for long paths). The 1.0s cap in high_precision_sleep would
        # silently truncate any single step > 1s. Pass max_s=10.0 so
        # the cap is realistic for Fitts's law durations (a + b *
        # log2(distance/width + 1) with a=50ms and b=150ms gives
        # ~2.4s for a 2000px move). The inner-loop mouse-move caller
        # (move_mouse_like_human) still uses the default 1.0s cap.
        await high_precision_sleep(delay_s, max_s=10.0)
        await page.mouse.move(int(x), int(y), steps=1)
    _set_mouse_position(page, int(end_x), int(end_y))

