# circadian_scheduler.py - Circadian Rhythm & Operational Pacing Engine (Bypasses SIVT Clustering)
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   CIR-1 - Sleep window is now per-persona: night owls (10% of personas)
#           sleep 02:00-10:00, early birds (5%) sleep 04:00-12:00, normal
#           (85%) sleep 23:00-07:00. The previous fixed 23-07 window
#           was wrong for non-US time zones.
#   CIR-2 - Rest day probability is now per-persona: workaholics (< 1%),
#           normal users (3%), casual (5%). Previously hardcoded 3% for
#           all personas.
#   CIR-3 - calculate_circadian_pacing_delay is now integrated into
#       sleep_human_delay in dynamic_pacing.py.
#   CIR-4 - DST handling: use zoneinfo (Python 3.9+) for proper DST-
#           aware time zone conversion. Fall back to pytz if unavailable.

import random
from datetime import datetime
try:
    # Python 3.9+: zoneinfo handles DST correctly
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    # Fallback: pytz (note: pytz.timezone() has known DST issues
    # with certain edge cases; prefer zoneinfo when available)
    try:
        from pytz import timezone as _pytz_timezone
        _HAS_ZONEINFO = False
    except ImportError:
        # No timezone library — assume UTC
        _HAS_ZONEINFO = None
from loguru import logger

def _get_timezone(tz_name: str):
    """CIR-4: DST-aware timezone lookup. zoneinfo handles DST
    transitions correctly; pytz has known issues with edge cases.

    E26 fix (Pass 17): Validate tz_name is a non-empty string.
    The previous code passed `tz_name` directly to `ZoneInfo(tz_name)`
    without checking if it was empty or None. `ZoneInfo("")` raises
    `ValueError` (not `ZoneInfoNotFoundError`), and `ZoneInfo(None)`
    raises `TypeError`. We now guard against these inputs.
    """
    if not tz_name or not isinstance(tz_name, str):
        # Empty/None timezone name — fall back to UTC
        from datetime import timezone
        return timezone.utc
    if _HAS_ZONEINFO is True:
        return ZoneInfo(tz_name)
    elif _HAS_ZONEINFO is False:
        return _pytz_timezone(tz_name)
    else:
        # No timezone library; use UTC offset of 0
        from datetime import timezone
        return timezone.utc

class CircadianScheduler:
    """DC-19 (Pass 19): Persona sleep/wake schedule based on geographic timezone.

    Prevents the 24/7 uniform operation signature (a key bot-detection signal)
    by gating task execution to the persona's local time. A "night owl" persona
    in San Francisco sleeps 02:00-10:00 PT; an "early bird" persona in London
    sleeps 04:00-12:00 GMT. The orchestrator pauses tasks for sleeping personas
    and resumes them at their configured wake time.

    The schedule is per-persona (not global) so a 5-persona container has 5
    different wake windows, mimicking real users in different timezones.
    """
    def __init__(self, timezone: str = "America/New_York", persona_sleep_schedule: str = "normal"):
        """
        Locks task execution to human circadian sleep schedules based on the persona's
        assigned proxy timezone. Prevents the 24/7 uniform operation signatures flagged
        by ML graph clustering algorithms.

        CIR-1: persona_sleep_schedule parameter lets each persona have a
        different sleep window. Options:
          - "normal": 23:00-07:00 (85% of personas)
          - "night_owl": 02:00-10:00 (10%)
          - "early_bird": 04:00-12:00 (5%)

        E26 fix (Pass 17): Validate timezone string. The previous
        code passed `timezone` to `_get_timezone` which delegates
        to `ZoneInfo(tz_name)`. If tz_name is invalid (e.g., a
        typo like "America/New_Yorkk" or a made-up value),
        `ZoneInfo` raises `ZoneInfoNotFoundError` (a KeyError
        subclass). This propagated up to the orchestrator's
        `_try_import` (which catches it as a generic exception),
        but more importantly, the persona's runtime was left
        with an uninitialized `self.tz` attribute. We now
        validate the timezone string and fall back to UTC on
        invalid input.
        """
        # E26: Validate timezone string. Use a try/except to
        # gracefully fall back to UTC on invalid input.
        try:
            self.tz = _get_timezone(timezone)
        except (KeyError, Exception) as tz_err:
            logger.warning(
                f"CIR-E26: Invalid timezone {timezone!r} ({tz_err}). "
                f"Falling back to UTC."
            )
            try:
                self.tz = _get_timezone("UTC")
            except Exception:
                # Last resort: naive datetime (no tzinfo)
                from datetime import timezone as _dt_tz
                self.tz = _dt_tz.utc
        # CIR-1: Sleep window per persona. The persona_id can be passed
        # to vary this; default is "normal".
        self.sleep_schedule = persona_sleep_schedule
        self._sleep_windows = {
            "normal":     (23, 7),   # sleep from 23:00 to 07:00
            "night_owl":  (2, 10),   # sleep from 02:00 to 10:00
            "early_bird": (4, 12),   # sleep from 04:00 to 12:00 (overnight workers)
        }
        self._rest_day_probability = {
            # CIR-2: Workaholics have < 1% rest days, casual users 5%.
            # The orchestrator can pass a persona-specific multiplier.
            "workaholic": 0.005,
            "normal":      0.03,
            "casual":      0.05,
        }
        self._user_archetype = "normal"  # default

    def set_user_archetype(self, archetype: str) -> None:
        """CIR-2: Set the persona's user archetype (affects rest day
        probability and pacing delay)."""
        if archetype in self._rest_day_probability:
            self._user_archetype = archetype
        else:
            logger.warning(f"Circadian: Unknown archetype '{archetype}', keeping '{self._user_archetype}'")

    def set_sleep_schedule(self, schedule: str) -> None:
        """CIR-1: Set the persona's sleep schedule (overrides default)."""
        if schedule in self._sleep_windows:
            self.sleep_schedule = schedule
        else:
            logger.warning(f"Circadian: Unknown sleep schedule '{schedule}', keeping '{self.sleep_schedule}'")

    def _get_rng(self):
        """R7-DEEP-AUDIT-2 GAP 7.8: Per-persona seeded RNG for the
        circadian scheduler. The RNG is keyed on the persona's
        sleep schedule and archetype, so two personas with
        different schedules have different rest-day probabilities
        and pacing delays.

        Returns a random.Random instance. Falls back to the
        global random module if hashlib is unavailable.
        """
        try:
            import hashlib
            seed_input = f"circadian:{self.sleep_schedule}:{self._user_archetype}"
            seed = int(hashlib.sha256(seed_input.encode()).hexdigest()[:8], 16)
            return random.Random(seed)
        except Exception:
            return random

    def is_persona_awake(self) -> bool:
        """
        Determines if the persona is currently 'awake' based on its localized timezone
        and persona-specific sleep schedule. Returns True if awake, False if in
        sleeping block (quarantine execution).
        """
        localized_now = datetime.now(self.tz)
        current_hour = localized_now.hour

        # CIR-1: Use persona-specific sleep window instead of hardcoded 23-07
        sleep_start, sleep_end = self._sleep_windows.get(
            self.sleep_schedule, (23, 7)
        )
        if sleep_start < sleep_end:
            # Simple case: sleep_start to sleep_end same day (e.g., 04:00-12:00)
            is_asleep = sleep_start <= current_hour < sleep_end
        else:
            # Wraps midnight (e.g., 23:00-07:00): sleep if hour >= start OR < end
            is_asleep = current_hour >= sleep_start or current_hour < sleep_end

        if is_asleep:
            logger.warning(
                f"Circadian: Localized time is {localized_now.strftime('%H:%M %p')}. "
                f"Persona sleep schedule '{self.sleep_schedule}' ({sleep_start:02d}:00-{sleep_end:02d}:00) "
                f"is active. Task execution blocked."
            )
            return False

        # CIR-2: Per-persona rest day probability
        rest_prob = self._rest_day_probability.get(self._user_archetype, 0.03)
        if self._get_rng().random() < rest_prob:
            logger.info(
                f"Circadian: Randomized human rest-block triggered "
                f"(archetype '{self._user_archetype}', prob={rest_prob}). "
                f"Task execution paused to maintain organic behavioral signatures."
            )
            return False

        logger.info(f"Circadian: Localized time is {localized_now.strftime('%H:%M %p')}. Persona is awake and authorized for task execution.")
        return True

    def calculate_circadian_pacing_delay(self) -> float:
        """
        Calculates a dynamic wait interval that mimics daily human pacing variations.
        Pacing is naturally slower during early mornings and faster during mid-afternoon peaks.
        Returns a recommended delay multiplier factor.
        """
        localized_now = datetime.now(self.tz)
        hour = localized_now.hour

        # R7-DEEP-AUDIT-2 GAP 7.8: Per-persona seeded RNG.
        # Each persona has a stable pacing distribution based on
        # their sleep schedule and archetype. The RNG is
        # re-instantiated per call to avoid state accumulation.
        _sched_rng = self._get_rng()
        # Slower early mornings (07:00 - 09:00)
        if 7 <= hour < 9:
            return _sched_rng.uniform(1.5, 2.5)
        # Standard mid-day active pacing (09:00 - 17:00)
        elif 9 <= hour < 17:
            return _sched_rng.uniform(0.8, 1.2)
        # Relaxed evening pacing (17:00 - 23:00)
        else:
            return _sched_rng.uniform(1.2, 1.8)
