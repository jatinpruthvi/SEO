# city_persona.py - Persistent Persona Per City (PPPC) Manager
#
# CHAMELEON: Persistent persona per city gives the system a "real returning
# user" feel. When a task needs a residential IP from, say, Dallas, TX,
# we don't generate a fresh persona every time - we reuse the SAME Dallas
# persona across all tasks. This means:
#   - Same browser fingerprint (canvas noise, fonts, GPU, etc.)
#   - Same cookie jar (re-using logged-in sessions)
#   - Same localStorage / IndexedDB (preferences, theme, etc.)
#   - Same SOCKS5 port (consistent source IP)
#   - Accumulating browser history (real users accrue this over time)
#
# Without PPPC, the system would create a brand-new persona for each task,
# which itself is a fingerprint: real users don't appear as fresh
# installations every time they browse.
#
# Schema:
#   persona_by_city (city VARCHAR(64) PK, persona_id, created_at, last_used, task_count)
#
# Behavior:
#   1. Daemon loop polls tasks, extracts city from task_payload.signup_city
#   2. For each city, look up the existing persona (or create one)
#   3. Run the persona for the task (preserves session_data, fingerprint, etc.)
#   4. Update last_used timestamp
#
# CHAMELEON-2: Browser history coherence. The persona's history.length
#   accumulates naturally because session_data persists across tasks.
#   We expose this to the JS side via persona_digest.history_depth, which
#   the prototype_hardener.js uses to vary the pre-navigation count.
#
# CHAMELEON-3: When a persona is "rotated" (task limit reached), the
#   persona_by_city entry is removed so the next task for that city gets
#   a fresh persona. This mimics a real user who gets a new device.
#
# Configuration (env vars):
#   PERSISTENT_PERSONA_BY_CITY=true|false (default: true)
#   PERSISTENT_PERSONA_TTL_DAYS=30 (default; personas older than this are recycled)
#   PERSISTENT_PERSONA_MAX_PER_CITY=1 (default; 1 = true "real returning user")
#

import os
import time
import asyncio
import hashlib
import secrets
import json
from typing import Optional, Dict, Any, Tuple

# Lazy import - avoid circular dependency with hardened_orchestrator.py
# asyncpg is imported at function level to allow mock testing

# Logger (loguru preferred, fall back to standard logging)
try:
    from loguru import logger as _loguru_logger
    logger = _loguru_logger
except ImportError:
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s | %(levelname)-7s | %(name)s:%(funcName)s:%(lineno)d - %(message)s'
    )
    logger = _logging.getLogger("city_persona")

# Configuration
PERSISTENT_PERSONA_ENABLED = os.getenv("PERSISTENT_PERSONA_BY_CITY", "true").lower() == "true"
# E15 fix (Pass 17): Safe env var parsing. The previous code
# used `int(os.getenv(...))` directly, which crashes with
# ValueError on non-numeric input (e.g., an operator typo
# `PERSISTENT_PERSONA_TTL_DAYS=30d`). We now use try/except
# to fall back to defaults on parse failure. Without this,
# a single operator typo crashes the orchestrator at module
# import time.
try:
    PERSISTENT_PERSONA_TTL_DAYS = int(os.getenv("PERSISTENT_PERSONA_TTL_DAYS", "30"))
except (ValueError, TypeError):
    PERSISTENT_PERSONA_TTL_DAYS = 30
try:
    PERSISTENT_PERSONA_MAX_PER_CITY = int(os.getenv("PERSISTENT_PERSONA_MAX_PER_CITY", "1"))
except (ValueError, TypeError):
    PERSISTENT_PERSONA_MAX_PER_CITY = 1

# SOCKS5 ports available for per-persona assignment.
# Mirrors SOCKS5_PERSONA_PORTS in hardened_orchestrator.py.
_SOCKS5_PORTS = ["1081", "1082", "1083", "1084", "1085"]
_SOCKS5_PROXY_BASE = "socks5h://172.18.0.5"


# Embedded CITY_TO_STATE map (mirrors hardened_orchestrator.py:CITY_TO_STATE).
# We embed rather than import to avoid the env-var boot requirement of
# hardened_orchestrator.py (DB_PASSWD, ENCRYPTION_KEY, etc.).
_CITY_TO_STATE = {
    "dallas": "tx", "houston": "tx", "austin": "tx", "san antonio": "tx",
    "los angeles": "ca", "san francisco": "ca", "san diego": "ca", "sacramento": "ca",
    "new york": "ny", "buffalo": "ny", "brooklyn": "ny",
    "chicago": "il", "springfield": "il",
    "phoenix": "az", "tucson": "az",
    "philadelphia": "pa", "pittsburgh": "pa",
    "miami": "fl", "orlando": "fl", "tampa": "fl", "jacksonville": "fl",
    "atlanta": "ga", "savannah": "ga",
    "seattle": "wa", "spokane": "wa",
    "denver": "co", "boulder": "co",
    "boston": "ma", "cambridge": "ma",
    "washington": "dc",
    "london": "ln", "manchester": "ln", "birmingham": "ln",
    "paris": "fr", "berlin": "be", "munich": "be",
    "amsterdam": "nl", "rotterdam": "nl",
    "toronto": "on", "vancouver": "bc", "montreal": "qc",
    "sydney": "nsw", "melbourne": "vic",
    "tokyo": "tk", "osaka": "tk",
    "singapore": "sg", "hong kong": "hk",
    "mumbai": "mh", "delhi": "dl", "bangalore": "ka",
}


def _signup_geo_for_city(city_norm: str) -> str:
    """Map a normalized city name to its 2-letter state/country code.

    Falls back to the city name itself (capitalized) for unknown cities.
    This is used as the persona's `signup_geo_region` field, which the
    proxy resolver compares against the actual proxy exit geo.
    """
    return _CITY_TO_STATE.get(city_norm, city_norm.title())


def _compute_socks5_url(persona_id: str) -> str:
    """Compute a deterministic SOCKS5 URL based on the persona_id.

    Uses the same hash->port mapping as the orchestrator's
    `get_socks5_port_for_persona()`.

    C11 ACCURACY: this gives each persona a unique *listener port*, not a
    unique source IP. Every port is a listener on the same gost instance
    sharing one -F upstream, so all personas still egress from the same
    address. It does NOT prevent cross-persona source-IP correlation,
    and the rationale below does not hold: a returning Dallas persona and
    a new Houston persona differ only in which local door they knock on,
    not in the IP the far side sees. See CONCURRENCY_AUDIT.md C11.
    """
    h = int(hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:8], 16)
    port = _SOCKS5_PORTS[h % len(_SOCKS5_PORTS)]
    return f"{_SOCKS5_PROXY_BASE}:{port}"


def _normalize_city(city: str) -> str:
    """Normalize city name for consistent matching.

    Examples:
        "St. Petersburg" -> "saint petersburg"
        "New York City" -> "new york city"
        "  DALLAS  " -> "dallas"
        "Los Angeles" -> "los angeles" (no expansion of "la" inside "dallas")

    IMPORTANT: City aliases (la -> los angeles, sf -> san francisco) are
    applied as WHOLE-WORD replacements only. A naive string.replace() would
    corrupt city names like "dallas" -> "dallos angeles..." because the
    substring "la" appears inside "dallas". We tokenize, expand aliases
    only on full tokens, then rejoin.
    """
    if not city:
        return ""
    city = city.lower().strip()
    # Punctuation abbreviations: "st." -> "saint", "mt." -> "mount", "ft." -> "fort"
    # These are substring-safe because the period disambiguates ("dallas" has no ".")
    # We append a space if the next char is a letter, so "St.Petersburg" becomes
    # "saint petersburg" (not "saintpetersburg"). The trailing space is
    # collapsed by the .split() below.
    # "St. Petersburg" -> "saint petersburg" (period always becomes space)
    # "St.Petersburg" -> "saint petersburg" (period always becomes space)
    # Double spaces are collapsed by the .split()/.join() below.
    for abbr, full in [("st.", "saint "), ("mt.", "mount "), ("ft.", "fort ")]:
        city = city.replace(abbr, full)
    # Tokenize on whitespace, then expand whole-word aliases.
    # This prevents "dallas" -> "dallos angeles" corruption.
    _ALIASES = {
        "nyc": "new york city",
        "la": "los angeles",
        "sf": "san francisco",
    }
    tokens = city.split()
    expanded = []
    for tok in tokens:
        # Strip leading/trailing punctuation for alias lookup only
        stripped = tok.strip(",.'\"")
        if stripped in _ALIASES:
            # Replace the stripped form inside the token, preserving
            # any surrounding punctuation. E.g., "la," -> "los angeles,"
            inner_stripped = tok.strip(",.'\"")
            tok = tok.replace(inner_stripped, _ALIASES[stripped], 1)
        expanded.append(tok)
    city = " ".join(expanded)
    # Remove remaining punctuation (but keep spaces)
    for ch in [",", ".", "'", '"']:
        city = city.replace(ch, "")
    # Collapse multiple spaces
    city = " ".join(city.split())
    return city.strip()


def _extract_city_from_payload(task_payload: dict) -> Optional[str]:
    """Extract city from task_payload.

    Priority of fields:
        1. signup_city (preferred)
        2. target_city
        3. city
        4. geo.city

    Returns normalized lowercase city name or None.
    """
    if not isinstance(task_payload, dict):
        return None
    for key in ["signup_city", "target_city", "city", "geo_city"]:
        val = task_payload.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_city(val)
    # Nested geo dict
    geo = task_payload.get("geo")
    if isinstance(geo, dict):
        val = geo.get("city")
        if isinstance(val, str) and val.strip():
            return _normalize_city(val)
    return None


def _generate_persona_id(city: str, salt: str = "") -> str:
    """Generate a stable persona_id for a city.

    The persona_id is derived from the city name + a per-city salt so:
      - Same city -> same persona_id (always)
      - Different cities -> different persona_ids
      - Per-city salt allows operator to manually trigger persona
        regeneration by changing the salt (e.g., "v2" -> "v3").

    The persona_id format: "ppc-<8-char-city-hash>-<8-char-content-hash>"
    This is human-readable and short (21 chars + 5 prefix = 26 chars).
    """
    city_hash = hashlib.sha256(city.encode("utf-8")).hexdigest()[:8]
    # Content hash: include salt + current time bucket (per TTL) so
    # personas older than TTL get recycled, but the same city within
    # the TTL window gets the same persona.
    ttl_bucket = int(time.time()) // (PERSISTENT_PERSONA_TTL_DAYS * 86400)
    content_input = f"{city}|{salt}|{ttl_bucket}"
    content_hash = hashlib.sha256(content_input.encode("utf-8")).hexdigest()[:8]
    return f"ppc-{city_hash}-{content_hash}"


async def get_or_create_persona_for_city(
    conn,
    city: str,
    task_payload: dict = None,
    salt: str = ""
) -> Tuple[str, bool]:
    """Get or create a persistent persona for a city.

    Args:
        conn: asyncpg connection (must be in a transaction or have autocommit).
        city: City name (will be normalized).
        task_payload: Task payload dict (used to extract proxy URL, fingerprint hints).
        salt: Optional salt to force persona regeneration.

    Returns:
        (persona_id, was_created) tuple.
        was_created is True if a new persona was created, False if reused.
    """
    if not PERSISTENT_PERSONA_ENABLED:
        # PPPC disabled: fall back to caller-provided persona logic.
        return None, False

    city_norm = _normalize_city(city)
    if not city_norm:
        return None, False

    # M6 fix: Use a transaction with SELECT ... FOR UPDATE to
    # prevent TOCTOU races between concurrent get_or_create
    # calls for the same city. Without the row lock, two
    # concurrent tasks could both:
    #   1. SELECT (no existing persona)
    #   2. INSERT a new persona (different IDs)
    #   3. Both INSERT into persona_by_city (only one wins
    #      due to ON CONFLICT, but the losing task's local
    #      persona_id is now stale)
    # The FOR UPDATE acquires a row-level lock on
    # persona_by_city. If no row exists, the lock has no
    # effect (it'll be created by us); if a row exists, we
    # wait for any concurrent transaction to release it.
    # We use an explicit transaction (not the implicit
    # autocommit of conn.execute) to ensure the lock is held
    # for the duration of the read-modify-write sequence.
    _m6_lock_held = False
    _m6_should_commit = False
    try:
        # Begin an explicit transaction so FOR UPDATE is held
        # until commit/rollback.
        if not getattr(conn, '_m6_in_transaction', False):
            await conn.execute("BEGIN")
            conn._m6_in_transaction = True
            _m6_lock_held = True
        # Step 1: Look up existing persona for this city
        # FOR UPDATE acquires a row-level lock. If the row
        # doesn't exist, the lock is effectively a no-op
        # (FOR UPDATE of zero rows is fine).
        existing = await conn.fetchrow(
            """
            SELECT pbc.persona_id, pbc.last_used, pbc.task_count,
                   p.state, p.signup_geo_region
            FROM persona_by_city pbc
            JOIN personas p ON p.id = pbc.persona_id
            WHERE pbc.city = $1
            FOR UPDATE OF pbc
            """,
            city_norm
        )

        if existing:
            persona_id = existing["persona_id"]
            # Check if persona is still usable
            if existing["state"] in ("limited", "banned", "rotated"):
                # Persona is quarantined/rotated - create a new one
                logger.info(
                    f"PPPC: City '{city_norm}' had persona {persona_id} in state "
                    f"'{existing['state']}', creating fresh persona"
                )
                # Delete the old mapping (so the new persona becomes the "official" one)
                await conn.execute(
                    "DELETE FROM persona_by_city WHERE city = $1",
                    city_norm
                )
                # Fall through to Step 2 (create new persona)
            else:
                # Reuse existing persona
                await conn.execute(
                    """
                    UPDATE persona_by_city
                    SET last_used = NOW(), task_count = task_count + 1
                    WHERE city = $1
                    """,
                    city_norm
                )
                logger.info(
                    f"PPPC: Reusing persona {persona_id} for city '{city_norm}' "
                    f"(task #{existing['task_count'] + 1})"
                )
                # M6 fix: Mark for commit and return.
                _m6_should_commit = True
                return persona_id, False

        # Step 2: Create new persona
        new_persona_id = _generate_persona_id(city_norm, salt)

        # Extract persona details from task_payload
        # Falls back to safe defaults
        if task_payload is None:
            task_payload = {}

        # R7-DEEP-AUDIT-2 GAP 1.11: Use a deterministic SOCKS5 URL based on
        # the persona_id (not the default 1081 port).
        #
        # C11 ACCURACY: this gives each persona a unique *listener port*,
        # not a unique source IP. Every port is a listener on the same
        # gost instance sharing one -F upstream, so all personas egress
        # from the same address. It does NOT prevent cross-persona
        # source-IP correlation. See CONCURRENCY_AUDIT.md C11.
        assigned_proxy_url = task_payload.get("proxy_url") or _compute_socks5_url(new_persona_id)
        # R7-DEEP-AUDIT-2 GAP 1.10: Use the 2-letter state code (e.g., "tx")
        # instead of the city name (e.g., "Dallas"). The proxy resolver
        # expects a state code for geo-clamping.
        signup_geo_region = task_payload.get("geo_region") or _signup_geo_for_city(city_norm)
        username = task_payload.get("username", f"user_{new_persona_id[:8]}")
        password = task_payload.get("password", secrets.token_urlsafe(16))
        timezone = task_payload.get("timezone", "America/New_York")
        ai_provider = task_payload.get("ai_provider", "openai")
        ai_model = task_payload.get("ai_model", "gpt-4o-mini")
        ai_api_key = task_payload.get("ai_api_key", "")
        assigned_asn = task_payload.get("assigned_asn", "")
        assigned_isp = task_payload.get("assigned_isp", "")

        # Generate a basic fingerprint (will be enriched by the task executor)
        # We don't include the full per-persona noise here - that's done
        # at runtime by BehavioralEntropyGenerator and prototype_hardener.js
        # PPPC (Pass 61): Use SHA-256 instead of MD5 for the canvas noise
        # seed. The previous MD5 usage was a FIPS-incompatibility — FIPS-
        # enabled deployments disable MD5, which would cause the persona
        # creation to fail with "ValueError: [digital envelope wrap]
        # unsupported". SHA-256 is always available. The 32-bit
        # truncation is preserved (we only need an index, so 32 bits
        # of entropy is more than enough).
        canvas_noise_seed = int(hashlib.sha256(new_persona_id.encode()).hexdigest()[:8], 16)
        fingerprint = {
            "navigator": {
                "platform": "Win32",
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.109 Safari/537.36",
                "hardwareConcurrency": 8,
                "deviceMemory": 8,
                "maxTouchPoints": 0,
            },
            "screen": {"width": 1920, "height": 1080, "colorDepth": 24, "pixelDepth": 24},
            "webgl": {"vendor": "Intel Inc.", "renderer": "Intel(R) Iris(TM) Plus Graphics 640"},
        }

        # Step 3: INSERT into personas + persona_by_city atomically
        await conn.execute(
            """
            INSERT INTO personas (
                id, fingerprint, session_data, state,
                username, password, assigned_proxy_url, signup_geo_region,
                canvas_noise_seed, client_hints, timezone,
                assigned_asn, assigned_isp, warmup_stage,
                ai_provider, ai_model, ai_api_key,
                task_count
            ) VALUES (
                $1, $2::jsonb, NULL, 'new',
                $3, $4, $5, $6,
                $7, NULL, $8,
                $9, $10, 0,
                $11, $12, $13,
                0
            )
            ON CONFLICT (id) DO NOTHING
            """,
            new_persona_id,
            json.dumps(fingerprint),
            username,
            password,
            assigned_proxy_url,
            signup_geo_region,
            canvas_noise_seed,
            timezone,
            assigned_asn,
            assigned_isp,
            ai_provider,
            ai_model,
            ai_api_key,
        )

        await conn.execute(
            """
            INSERT INTO persona_by_city (city, persona_id, last_used, task_count)
            VALUES ($1, $2, NOW(), 0)
            ON CONFLICT (city) DO UPDATE SET
                persona_id = EXCLUDED.persona_id,
                last_used = NOW()
            """,
            city_norm,
            new_persona_id,
        )

        logger.info(
            f"PPPC: Created new persona {new_persona_id} for city '{city_norm}'"
        )
        _m6_should_commit = True
        return new_persona_id, True

    except Exception as e:
        logger.error(f"PPPC: Failed to create persona for city '{city_norm}': {e}")
        raise
    finally:
        # M6 fix: Commit on success, rollback on error, then release
        # the lock.
        #
        # C17 fix: the previous comment claimed this "guarantees the
        # FOR UPDATE row lock is never leaked". It did not, and the
        # failure mode was worse than a leak. When COMMIT/ROLLBACK
        # raised, the exception was swallowed as "non-fatal" and
        # `conn._m6_in_transaction` was set to False regardless - so the
        # transaction could still be open while the flag claimed it was
        # closed. The next call would then skip BEGIN (see the guard
        # above), take no row lock, and silently disable the TOCTOU
        # protection this block exists to provide.
        #
        # We now only clear the flag when the transaction actually
        # ended, and we report a failed COMMIT as an error rather than
        # a warning. `conn` is owned by the caller, so we do not close
        # it here; asyncpg rolls back any open transaction when the
        # connection is released to the pool.
        if _m6_lock_held:
            _m6_tx_ended = False
            try:
                if _m6_should_commit:
                    await conn.execute("COMMIT")
                else:
                    await conn.execute("ROLLBACK")
                _m6_tx_ended = True
            except Exception as tx_err:
                logger.error(
                    f"PPPC: transaction end FAILED ({tx_err}). The "
                    f"connection may still hold a row lock; leaving "
                    f"_m6_in_transaction set so the next call reopens "
                    f"it rather than running unlocked."
                )
            finally:
                if _m6_tx_ended:
                    conn._m6_in_transaction = False


async def get_persistent_persona_id(conn, city: str) -> Optional[str]:
    """Get the persistent persona_id for a city (read-only).

    Returns None if PPPC is disabled or no persona exists for the city.
    """
    if not PERSISTENT_PERSONA_ENABLED:
        return None
    city_norm = _normalize_city(city)
    if not city_norm:
        return None
    row = await conn.fetchrow(
        "SELECT persona_id FROM persona_by_city WHERE city = $1",
        city_norm
    )
    if row:
        return row["persona_id"]
    return None


async def list_persistent_personas(conn) -> list:
    """List all city->persona mappings (for debugging/audit)."""
    rows = await conn.fetch(
        """
        SELECT pbc.city, pbc.persona_id, pbc.created_at, pbc.last_used,
               pbc.task_count, p.state
        FROM persona_by_city pbc
        LEFT JOIN personas p ON p.id = pbc.persona_id
        ORDER BY pbc.last_used DESC
        """
    )
    return [dict(row) for row in rows]


async def retire_persistent_persona(conn, city: str) -> bool:
    """Remove a city->persona mapping (e.g., when persona is rotated).

    Returns True if a mapping was removed.
    """
    city_norm = _normalize_city(city)
    if not city_norm:
        return False
    result = await conn.execute(
        "DELETE FROM persona_by_city WHERE city = $1",
        city_norm
    )
    # asyncpg returns "DELETE <count>"
    return "DELETE 1" in result


async def sync_persona_task_count(conn, persona_id: str) -> None:
    """Sync personas.task_count with the MAX(task_count) across all cities
    that map to this persona.

    R7-DEEP-AUDIT-2 GAP 1.8: PPTC personas were created without a
    task_count, so MAX_TASKS_PER_PERSONA was never enforced for them.
    This function keeps the persona's task_count in sync with the
    city-level task_count, so the retirement logic at the bottom of
    run_persona_pipeline triggers correctly.

    Call this at the end of each task (after the task completes) so
    the persona's task_count is updated to the highest city count.
    """
    if not PERSISTENT_PERSONA_ENABLED or not persona_id:
        return
    try:
        await conn.execute(
            """
            UPDATE personas
            SET task_count = COALESCE((
                SELECT MAX(task_count)
                FROM persona_by_city
                WHERE persona_id = $1
            ), 0)
            WHERE id = $1
            """,
            persona_id
        )
    except Exception as e:
        logger.warning(f"PPPC: Failed to sync persona task_count for {persona_id}: {e}")
