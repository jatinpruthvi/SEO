# schema.sql - PostgreSQL Identity and Task queue tables with unlogged runtime optimization

# PPPC (Pass 61): Added 'rotated' to the account_state enum. The
# orchestrator marks a persona as 'rotated' when it reaches its task
# limit (MAX_TASKS_PER_PERSONA). Without this enum value, the SQL
# `UPDATE personas SET state = 'rotated'` would fail with
# `invalid input value for enum account_state: "rotated"`.
# We use CREATE TYPE here for fresh DBs, and a separate idempotent
# ALTER TYPE for existing DBs (PG 9.6+).
CREATE TYPE account_state AS ENUM ('new', 'warming', 'active', 'cooldown', 'limited', 'banned', 'rotated');

# Migration for existing DBs that don't have 'rotated' in the enum.
# This is wrapped in a DO block with exception handling so it's
# idempotent: it does nothing on a fresh DB (where 'rotated' was
# just created above) and adds the value on an existing DB.
DO $$
BEGIN
    BEGIN
        ALTER TYPE account_state ADD VALUE IF NOT EXISTS 'rotated';
    EXCEPTION
        WHEN OTHERS THEN
            -- Either the value already exists, or the DB version
            -- is too old to support ADD VALUE. Both cases are OK.
            NULL;
    END;
END$$;

CREATE TABLE personas (
    id VARCHAR(64) PRIMARY KEY,
    fingerprint JSONB NOT NULL,
    session_data TEXT, -- Encrypted AES-256 session package (cookies, localStorage, sessionStorage)
    state account_state DEFAULT 'new',
    cooldown_until TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    failure_count INT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_active TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Geo-Consistency & Sticky Proxy Vault Fields
    username VARCHAR(64) NOT NULL,
    password VARCHAR(128) NOT NULL, -- Decoupled plain auth credential storage
    assigned_proxy_url TEXT NOT NULL, -- Strict sticky proxy reservation
    signup_geo_region VARCHAR(64) NOT NULL, -- Locked target georegion (e.g. 'Dallas')
    session_expiration TIMESTAMP WITH TIME ZONE, -- Dynamic session cookie expiry tracking
    verification_flagged BOOLEAN DEFAULT FALSE, -- SIVT quarantine flag
    
    -- 1.2 & 1.3 & 1.5 Reverse Geo-Consistency Persona Routing Columns
    canvas_noise_seed INT NOT NULL DEFAULT 12345, -- Cryptographic static seed locking Canvas/WebGL noise per persona
    client_hints JSONB, -- Explicit structural alignment for Sec-Ch-Ua client headers
    assigned_asn VARCHAR(128), -- Binds the persona to the network carrier ASN to prevent network hopping flags
    assigned_isp VARCHAR(128), -- Binds the persona to the residential ISP network provider
    timezone VARCHAR(64) DEFAULT 'America/New_York', -- Precise local clock and system zone locking
    
    -- 30-Day Platform Authority Warming Columns
    warmup_stage INT DEFAULT 0, -- Track current seasoning day (0 to 7) to enforce gradual warmup before Gmail signup
    
    -- 2026 Evasion Upgrade: Design Pattern 2 - Database-Based Pluggable AI Keys
    ai_provider VARCHAR(32) NOT NULL DEFAULT 'openai',
    ai_model VARCHAR(64) NOT NULL DEFAULT 'gpt-4o-mini',
    ai_api_key TEXT NOT NULL DEFAULT '', -- Plaintext API Key storage as requested

    -- CONCURRENCY-FIX C9: persona-level lease. The task queue only ever
    -- claimed a *task*; nothing stopped two workers from running two
    -- different tasks for the SAME persona at the same time, which put one
    -- account in two browsers at once (concurrent sessions, interleaved
    -- cookie writes, session_data lost to a blind overwrite).
    -- A worker must take this lease before it may execute a task for the
    -- persona. The lease self-heals: locked_until expires on its own, so a
    -- crashed worker cannot deadlock a persona forever.
    locked_by VARCHAR(128),
    locked_until TIMESTAMP WITH TIME ZONE
);

-- CONCURRENCY-FIX C9: the queue query filters on lock expiry on every poll.
CREATE INDEX IF NOT EXISTS idx_personas_locked_until
    ON personas(locked_until);

-- CONCURRENCY-FIX C6: the queue query now also filters on cooldown expiry.
CREATE INDEX IF NOT EXISTS idx_personas_cooldown_until
    ON personas(cooldown_until);

-- PPPC (Persistent Persona Per City): migration adds task_count column
-- to the existing personas table. ADD COLUMN IF NOT EXISTS is idempotent.
ALTER TABLE personas ADD COLUMN IF NOT EXISTS task_count INT DEFAULT 0;

-- PPPC: Persistent Persona Per City (CHAMELEON feature)
-- Maps each city to a stable persona_id so the same person always
-- uses the same browser fingerprint / SOCKS5 port / cookie jar
-- when browsing from that city. Real users in Dallas keep the same
-- browser profile; they don't get a new identity each visit.
CREATE TABLE persona_by_city (
    city VARCHAR(96) PRIMARY KEY, -- Normalized city name (e.g., "dallas", "new york city")
    persona_id VARCHAR(64) NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    task_count INT DEFAULT 0,
    -- Audit: track when/why the persona was rotated
    rotated_at TIMESTAMP WITH TIME ZONE,
    rotation_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_persona_by_city_last_used ON persona_by_city(last_used DESC);
CREATE INDEX IF NOT EXISTS idx_persona_by_city_persona_id ON persona_by_city(persona_id);

-- CONCURRENCY-FIX C3: `queue_state` was DELETED.
-- It carried exactly the locking fields this system needed (assigned_worker,
-- locked_at) and -- verified across the whole repo -- no Python file ever
-- referenced it; only its two indexes did. A table that looks like the queue's
-- lock but is never read or written is a trap: it invites the assumption that
-- task claiming is handled somewhere. The real lease now lives on
-- tasks.claimed_by/claimed_at (C2) and personas.locked_by/locked_until (C9),
-- which the daemon actually reads and writes.

CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    persona_id VARCHAR(64) REFERENCES personas(id) ON DELETE CASCADE,
    platform VARCHAR(32) NOT NULL,
    task_payload JSONB NOT NULL,
    status VARCHAR(16) DEFAULT 'pending',
    execution_time TIMESTAMP WITH TIME ZONE,
    error_log TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- CONCURRENCY-FIX C1: the daemon's queue query selects t.failure_count
    -- (hardened_orchestrator.py) and the retry paths UPDATE it, but this column
    -- was never declared. Against a DB built from this file, Postgres raised
    -- UndefinedColumnError on the very first poll and the daemon never
    -- processed a single task (it logs "Queue polling failed" and backs off
    -- to 300s forever).
    failure_count INT DEFAULT 0,
    -- CONCURRENCY-FIX C2: lease columns backing the atomic task claim.
    -- claimed_by  = worker that owns the row while status='running'
    -- claimed_at  = when the lease was taken (used by the reaper to recycle
    --               tasks whose worker crashed mid-execution)
    claimed_by VARCHAR(128),
    claimed_at TIMESTAMP WITH TIME ZONE
);

-- CONCURRENCY-FIX C2: the queue polls "oldest pending/retry first"; without an
-- index this is a seq scan that grows linearly with task history.
CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks(status, created_at);

-- CONCURRENCY-FIX C2: the reaper recycles leases older than the task timeout.
CREATE INDEX IF NOT EXISTS idx_tasks_claimed_at
    ON tasks(claimed_at) WHERE status = 'running';

CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    persona_id VARCHAR(64) REFERENCES personas(id) ON DELETE CASCADE,
    checked_post_url TEXT NOT NULL,
    is_publicly_visible BOOLEAN DEFAULT TRUE,
    checked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
-- (CONCURRENCY-FIX C3: the two queue_state indexes were dropped with the table.)
CREATE INDEX idx_persona_cooldown_lookup ON personas(cooldown_until) WHERE state = 'cooldown';

-- PPPC: also index persona_by_city
CREATE INDEX IF NOT EXISTS idx_persona_by_city_last_used ON persona_by_city(last_used DESC);
CREATE INDEX IF NOT EXISTS idx_persona_by_city_persona_id ON persona_by_city(persona_id);

-- Pass 61: Partial index on personas.state for fast exclusion of
-- quarantined personas. The orchestrator's daemon loop has:
--   AND (p.state IS NULL OR p.state NOT IN ('limited', 'rotated'))
-- Without an index, this is a full scan. With this partial index,
-- queries that filter out rotated/limited personas are O(log n).
CREATE INDEX IF NOT EXISTS idx_personas_active_state
    ON personas(state)
    WHERE state IN ('new', 'warming', 'active', 'cooldown');

-- Pass 61: Index for persona_by_city.task_count lookups (used in
-- the orchestrator's quota check).
CREATE INDEX IF NOT EXISTS idx_persona_by_city_task_count
    ON persona_by_city(task_count DESC);
