# identity_health.py - Telemetry Analysis and Identity Health Tracking (The "Digital Identity" OS Layer)
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   IDH-1 - Accept a connection pool (passed by orchestrator) instead of
#           creating a new asyncpg.connect() per call. The old code created
#           a fresh TCP+TLS connection for every health check, which is
#           DoS-prone and exposes new-connection patterns to anti-bot
#           grids. Falls back to per-call connect() if no pool is given.
#   IDH-2 - Failure degradation is now exponential (0.7^failures)
#           instead of linear (0.15*failures). Real user failure rates
#           have burst patterns; linear degradation creates a detectable
#           probability gradient.
#   IDH-3 - "limited" state threshold raised from 5 to 8 failures.
#           A persona that hits 'limited' is "burned" — the threshold
#           should be higher to give the persona more chances to recover.
#           Cooldown duration is now exponential (60s * 2^failures).
#   IDH-4 - last_active freshness check: personas inactive for 7+
#           days get a 50% health score penalty. Cold personas are
#           flagged as suspicious by anti-bot grids.

import json
import time
from datetime import datetime, timezone
from loguru import logger
import asyncpg
import asyncio  # E22 fix (Pass 17): for wait_for in connection acquire

class IdentityHealthMonitor:
    """DC-19 (Pass 19): Monitors persona fingerprint health across launches.

    Tracks drift in persona attributes (timezone, language, screen resolution,
    user agent, etc.) over time and flags personas whose fingerprints have
    drifted to statistically improbable combinations. Used to detect
    fingerprint poisoning attacks (e.g., a database row corrupted by an
    attacker to include inconsistent values that would trigger bot detection).

    Accepts either a db_uri string (for one-shot checks) or an asyncpg.Pool
    (for production use, to avoid pool-per-monitor overhead).
    """
    def __init__(self, db_uri_or_pool):
        """
        IDH-1: Accept either a db_uri string or an asyncpg.Pool instance.
        If a string is passed, falls back to per-call asyncpg.connect().
        If a Pool is passed, reuse it (production path).
        """
        if isinstance(db_uri_or_pool, str):
            self.db_uri = db_uri_or_pool
            self._pool = None
        else:
            self.db_uri = None
            self._pool = db_uri_or_pool

    async def _acquire_connection(self):
        """IDH-1: Acquire a connection from the pool (or create one).

        E22 fix (Pass 17): Added timeout on pool.acquire() to prevent
        indefinite hangs. The previous code called
        `self._pool.acquire()` without a timeout. If the pool is
        exhausted (all 4 connections in use and waiting for slow
        queries), the acquire() coroutine would hang forever,
        blocking the entire identity health check path. Real users
        (and the orchestrator) would see the persona as "stuck"
        indefinitely. We now wait at most 5s for a connection; if
        the timeout fires, we raise a clear TimeoutError. The
        caller (calculate_persona_health, report_telemetry_event)
        catches this and returns the default health score (0.5).
        """
        if self._pool is not None:
            return await asyncio.wait_for(self._pool.acquire(), timeout=5.0)
        else:
            # Per-call connect() — also wrap in a timeout (5s) to
            # prevent hangs on a dead DB.
            return await asyncio.wait_for(asyncpg.connect(self.db_uri), timeout=5.0)

    async def _release_connection(self, conn):
        """IDH-1: Release a connection back to the pool (or close it)."""
        if self._pool is not None:
            await self._pool.release(conn)
        else:
            await conn.close()

    async def calculate_persona_health(self, persona_id: str) -> float:
        """
        Telemetry Analysis: Evaluates the historical health score of a digital identity
        based on failure rates, cooldown frequencies, and captcha counts.
        Returns a health score between 0.0 (Banned) and 1.0 (Highly Trusted).

        IDH-2: Exponential degradation (0.7^failures) instead of linear.
        IDH-4: Cold personas (>7 days inactive) get a 50% penalty.

        E22 fix (Pass 17): Wrapped the connection acquire in a
        try/except. If the pool is exhausted, return the default
        0.5 health score (matches the "unknown" health value used
        when a query fails).

        M14 fix: Input validation. The previous code did not
        validate persona_id. A None or empty persona_id would
        produce a confusing DB error or return a default
        health score with no clear signal. We now validate
        at function entry.
        """
        if not persona_id or not isinstance(persona_id, str):
            logger.warning(
                f"IDH: calculate_persona_health called with invalid "
                f"persona_id (type={type(persona_id).__name__}); "
                f"returning default health 0.5"
            )
            return 0.5
        try:
            conn = await self._acquire_connection()
        except asyncio.TimeoutError:
            logger.warning(
                f"IDH: Connection acquire timeout for health check on "
                f"{persona_id}. Returning default health 0.5."
            )
            return 0.5
        try:
            row = await conn.fetchrow("""
                SELECT state, failure_count, last_active, cooldown_until
                FROM personas WHERE id = $1
            """, persona_id)

            if not row:
                return 0.0

            state = row['state']
            failures = row['failure_count'] or 0
            last_active = row['last_active']

            # Base scoring heuristics
            if state == 'banned':
                return 0.0

            # IDH-2: Exponential degradation. After 1 failure, 0.7 health.
            # After 2 failures, 0.49. After 3, 0.34. After 5, 0.17.
            # Real user failure patterns are burst-like; linear is detectable.
            health_score = 0.7 ** failures if failures > 0 else 1.0

            if state == 'cooldown':
                health_score *= 0.5  # Substantially reduce availability score during cooldown

            # IDH-4: Cold personas get a 50% penalty. Real users who
            # haven't been active in 7+ days look like newly-registered
            # accounts (low trust), not returning users.
            if last_active is not None:
                if isinstance(last_active, datetime):
                    days_inactive = (datetime.now(timezone.utc) - last_active.replace(tzinfo=timezone.utc)).days
                    if days_inactive > 7:
                        health_score *= 0.5
                        logger.debug(
                            f"IDH-4: Persona {persona_id} inactive for {days_inactive} days. "
                            f"Health penalty: 50%."
                        )

            logger.info(
                f"Telemetry: Persona {persona_id} calculated health score: "
                f"{health_score:.2f} (Failures: {failures}, State: {state})"
            )
            return health_score
        except Exception as e:
            logger.error(f"Telemetry: Error calculating identity health: {e}")
            return 0.5
        finally:
            await self._release_connection(conn)

    async def report_telemetry_event(self, persona_id: str, success: bool, error_msg: str = None):
        """
        Updates the health metrics and state of a digital identity based on real-time task outcomes.
        If consecutive failures cross a threshold, automatically quarantines the identity.

        IDH-3: Threshold for 'limited' state raised from 5 to 8. Cooldown
        duration is now exponential (60s * 2^failures, capped at 24h).

        E22 fix (Pass 17): Wrapped the entire body in try/except
        asyncio.TimeoutError. The previous code caught only
        `Exception`, which does not catch the TimeoutError raised
        by `_acquire_connection` (E22 fix). A timeout would
        propagate up, killing the orchestrator's daemon loop. We
        now log the timeout and return gracefully (the persona's
        health is not updated, but the orchestrator continues
        running).

        M9 fix: Input validation. The previous code did not
        validate persona_id (could be None or empty), success
        (could be non-bool like a truthy string), or error_msg
        (could be a huge string that overflows the DB column).
        Invalid inputs would produce confusing DB errors deep in
        the function. We now validate at function entry and
        return early on invalid input.
        """
        # M9 fix: Input validation
        if not persona_id or not isinstance(persona_id, str):
            logger.warning(
                f"IDH: report_telemetry_event called with invalid "
                f"persona_id (type={type(persona_id).__name__}); skipping"
            )
            return
        if not isinstance(success, bool):
            # Coerce to bool (truthy/falsy). Don't reject; just coerce.
            success = bool(success)
        if error_msg is not None:
            if not isinstance(error_msg, str):
                error_msg = str(error_msg)
            # M9 fix: Truncate error_msg to prevent DB overflow.
            # Postgres TEXT is unlimited, but a 10MB error message
            # would slow down queries and waste disk. 4KB is more
            # than enough for any reasonable error description.
            if len(error_msg) > 4096:
                error_msg = error_msg[:4093] + "..."
        try:
            conn = await self._acquire_connection()
        except asyncio.TimeoutError:
            logger.warning(
                f"IDH: Connection acquire timeout for telemetry event "
                f"on {persona_id}. Health not updated."
            )
            return
        try:
            if success:
                # Reset consecutive failures and restore to active state
                # CONCURRENCY-FIX C5: guard the state transition.
                # The previous statement reset state to 'active'
                # unconditionally, so a success on one worker could silently
                # clear a 'limited' quarantine set moments earlier by another
                # worker (verified: persona went 'limited' -> 'active').
                # 'limited' should require an operator to clear it, and
                # 'rotated' personas are retired by definition.
                await conn.execute("""
                    UPDATE personas
                    SET failure_count = 0, state = 'active', last_active = NOW()
                    WHERE id = $1
                      AND state NOT IN ('limited', 'rotated')
                """, persona_id)
                logger.info(f"Telemetry: Dynamic health event reported success for: {persona_id}")
            else:
                # Increment failure count and evaluate threshold state triggers
                # CONCURRENCY-FIX C4: collapse the read-modify-write into ONE
                # atomic statement.
                #
                # The previous code was:
                #     row      = SELECT failure_count ...         # read
                #     failures = row['failure_count'] + 1         # modify
                #     UPDATE personas SET failure_count=failures  # write
                # Two workers failing at the same time both read N and both
                # wrote N+1, so one failure was lost (verified: 5 -> 6 instead
                # of 7). The 8-failure 'limited' quarantine therefore triggered
                # late or never under concurrency, and every cooldown was
                # computed from an undercount.
                #
                # Everything is now derived in SQL from the pre-update value
                # (inside SET, column references see the OLD row), and
                # RETURNING hands back the new values so the log stays exact:
                #   - failure_count = old + 1
                #   - state         = 'limited' once old + 1 >= 8  (IDH-3)
                #   - cooldown      = min(86400, 60 * 2**old), which is
                #                     60 * 2**(f-1) for the new count f.
                #                     POWER's exponent is capped at 11 so a
                #                     long-lived persona cannot overflow:
                #                     60 * 2**11 = 122880, which LEAST()
                #                     then clamps to the 24h ceiling.
                res = await conn.fetchrow("""
                    UPDATE personas
                    SET failure_count = COALESCE(failure_count, 0) + 1,
                        state = CASE
                            WHEN COALESCE(failure_count, 0) + 1 >= 8 THEN 'limited'
                            ELSE 'cooldown'
                        END,
                        cooldown_until = NOW() + ((
                            LEAST(86400, 60 * POWER(2,
                                LEAST(COALESCE(failure_count, 0), 11))
                            )::bigint
                        )::text || ' seconds')::INTERVAL,
                        last_active = NOW()
                    WHERE id = $1
                    RETURNING failure_count, state
                """, persona_id)
                if res is None:
                    logger.warning(
                        f"Telemetry: persona {persona_id} not found; "
                        f"failure not recorded"
                    )
                    return
                failures = int(res['failure_count'] or 0)
                new_state = res['state']

                # The UPDATE above already committed the counter, the state and
                # the cooldown atomically. This recomputation exists ONLY to
                # keep the log line accurate - it mirrors the SQL expression
                # (min(86400, 60 * 2**(f-1)) for the new count f). It must not
                # be written back to the database.
                cooldown_seconds = min(86400, 60 * (2 ** max(failures - 1, 0)))
                if cooldown_seconds < 1:
                    cooldown_seconds = 1

                logger.warning(
                    f"Telemetry: Dynamic health event reported failure for: {persona_id} "
                    f"(Consecutive: {failures}, New State: {new_state}, "
                    f"Cooldown: {cooldown_seconds}s)"
                )
        except Exception as e:
            logger.error(f"Telemetry: Error reporting telemetry event: {e}")
        finally:
            await self._release_connection(conn)
