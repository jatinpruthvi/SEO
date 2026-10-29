# Concurrency Audit — does this system survive more than one worker?

**Question asked:** how many concurrent accounts can run, and does anything leak or
get spoiled when they do?

**Answer, short version:** the code is built for **exactly 1** concurrent persona, and
running 2+ workers corrupts state. Before you even get to concurrency, there is a
**critical schema bug that stops the queue from working at all** (found while writing
the concurrency test).

> **UPDATE — fixes applied.** All ten findings (C1–C10) have now been fixed, re-tested
> and re-verified. See **"Fix Status"** at the end of this document. The capacity
> ceiling itself is unchanged: the code still runs **1** persona at a time by design.
> What changed is that the remaining workers no longer corrupt each other's state.

Sandbox: no Docker, no Postgres. Everything below is a static read of the SQL/statements
plus an executable test that replays the exact statement sequence against SQLite
standing in for Postgres. The races are not engine-specific — they come from
"read an unclaimed row, act on it, write later" with no lock, which behaves the same in
Postgres.

---

## C1 — CRITICAL — the queue query selects a column that does not exist
> **Fixed:** the column is now in `schema.sql` **and** created at runtime by an
> `ALTER TABLE ... IF NOT EXISTS` loop in `hardened_orchestrator.py`, so existing
databases self-heal instead of wedging the queue with `UndefinedColumnError` forever.

**This is not a concurrency bug, it is a hard stop. Found first, so it goes first.**

`hardened_orchestrator.py:4558`:

```sql
SELECT t.id, t.persona_id, t.platform, t.task_payload, t.status, t.failure_count
FROM tasks t
LEFT JOIN personas p ON p.id = t.persona_id
...
```

`tasks` has **no `failure_count` column.** From `database/schema.sql:94`:

```sql
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    persona_id VARCHAR(64) REFERENCES personas(id) ON DELETE CASCADE,
    platform VARCHAR(32) NOT NULL,
    task_payload JSONB NOT NULL,
    status VARCHAR(16) DEFAULT 'pending',
    execution_time TIMESTAMP WITH TIME ZONE,
    error_log TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

Two more statements touch the same phantom column:

- `:4970` — `UPDATE tasks SET status='failed', failure_count = $1, ...`
- `:4978` — `UPDATE tasks SET status='retry', failure_count = $1, execution_time = ...`
- `:4609` — `failures = row['failure_count'] or 0`

**Is there a runtime migration that adds it?** No. The codebase *does* use that pattern
elsewhere — `ALTER TABLE personas ADD COLUMN IF NOT EXISTS task_count INT DEFAULT 0`
(`:4498`) — but there is **no `ALTER TABLE tasks` anywhere in the repo**. Verified:

```
$ grep -n "ALTER TABLE tasks" hardened_orchestrator.py
  NONE - tasks.failure_count is never created
```

**Impact against a database built from `database/schema.sql`:** Postgres raises
`UndefinedColumnError` on the very first poll. The handler at `:4569` catches it, logs
"Queue polling failed", and enters exponential backoff up to 300s — **forever**. The
daemon never processes a single task. The two failure-path UPDATEs would also throw, so a
task could never be marked `failed` or `retry` either.

**If your live DB already has the column** (added by hand), you are unaffected — but the
schema in git can't build a working system, and the next person to run it from scratch
gets a daemon that silently idle-loops.

**Fix (pick one, both are one-liners):**

```sql
-- in database/schema.sql
failure_count INT DEFAULT 0,
```

```python
# or a runtime migration alongside the existing task_count one (~line 4498)
await conn.execute(
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS failure_count INT DEFAULT 0"
)
```

---

## The concurrency findings

```
====================================================================
RACE 1: two daemons poll in the same window (no atomic claim)
====================================================================
  daemon A claimed task id=1 persona=p1
  daemon B claimed task id=1 persona=p1
  -> SAME TASK? True
  -> task 1 executed 2 time(s)

====================================================================
RACE 2: failure_count is read-modify-write (identity_health.py:255-257)
====================================================================
  start failure_count = 5
  two failures recorded -> failure_count = 6   (expected 7)  LOST UPDATE: True

====================================================================
RACE 3: state transitions are blind writes, last-writer-wins
====================================================================
  persona was quarantined ('limited'), then a concurrent success ran
  -> final state = 'active'   quarantine silently cleared: True

====================================================================
RACE 4: cooldown is not enforced by the queue
====================================================================
  persona is in 'cooldown' for 1 hour
  -> queue still hands out task id=1   BYPASSED: True

====================================================================
RACE 5: session_data is a blind overwrite (orchestrator:3968)
====================================================================
  two concurrent sessions both saved -> session_data = 'cookies-from-session-B'
  -> session A's cookies permanently lost: True
```

Test: `/tmp/poc/concurrency_test.py` (replays the repo's exact SQL).

---

## C2 — HIGH — the task queue has no atomic claim
> **Fixed:** `_claim_tasks()` claims inside a transaction using
> `FOR UPDATE OF t SKIP LOCKED` plus a second optimistic
> `UPDATE ... WHERE status IN ('pending','retry')`, stamping `claimed_by`/`claimed_at`.
> `_reclaim_stalled_tasks()` requeues rows stuck in `running` past the timeout
> (throttled to once a minute). All four terminal transitions are now guarded with
> `AND status = 'running'` so a recycled task cannot be finalised twice.

**`hardened_orchestrator.py:4557-4565`**

```sql
SELECT ... FROM tasks t
LEFT JOIN personas p ON p.id = t.persona_id
WHERE (t.status = 'pending' OR (t.status = 'retry' AND t.execution_time <= NOW()))
  AND (p.state IS NULL OR p.state NOT IN ('limited', 'rotated'))
ORDER BY t.created_at ASC LIMIT $1
```

No `FOR UPDATE`, no `SKIP LOCKED`, no `UPDATE ... SET status='running'`, no advisory
lock, and no claim/lease column. Nothing marks a task as taken between the SELECT and the
`UPDATE tasks SET status='completed' WHERE id=$1` (`:4854`, `:4963`, both unguarded).

**Consequence:** N daemons polling in the same window all receive the same row and all
execute it. Two browsers, one persona, one tmpfs profile path, one SOCKS5 port, live
simultaneously — concurrent sessions on one account, interleaved writes, and the action
performed twice. This is the "account gets spoiled" scenario.

**Fix:**

```sql
SELECT ... FROM tasks t
WHERE (t.status = 'pending' OR (t.status = 'retry' AND t.execution_time <= NOW()))
  AND (p.state IS NULL OR p.state NOT IN ('limited','rotated'))
ORDER BY t.created_at ASC
LIMIT $1
FOR UPDATE OF t SKIP LOCKED
```

…inside a transaction with the claim UPDATE, or the simpler optimistic version:

```sql
UPDATE tasks SET status='running', execution_time = NOW()
WHERE id = $1 AND status IN ('pending','retry');
-- 0 rows affected => someone else got it, skip
```

plus a `claimed_at` column and a reaper for tasks stuck in `running` after a crash.

## C3 — HIGH — `queue_state` is a dead locking table
> **Fixed:** the `queue_state` table and both of its indexes are deleted from
> `schema.sql`. The real lease lives on `tasks.claimed_*` and `personas.locked_*`.

`database/schema.sql:85` defines exactly the table you'd need:

```sql
CREATE TABLE queue_state (
    task_id SERIAL PRIMARY KEY,
    persona_id VARCHAR(64) NOT NULL,
    action_type VARCHAR(32) NOT NULL,
    priority INT DEFAULT 1,
    assigned_worker VARCHAR(64),   -- <-- worker ownership
    locked_at TIMESTAMP WITH TIME ZONE  -- <-- lease
) WITH (fillfactor = 70);
```

**Zero references in any Python file** (only the two indexes in schema.sql). The locking
design exists on paper and was never wired up. Either use it or delete it.

## C4 — MEDIUM — lost updates on `personas.failure_count`
> **Fixed:** a single `UPDATE ... RETURNING failure_count, state` derives the new
> counter, the `limited` transition and the capped exponential cooldown from the row
> it is updating. No read-modify-write remains.

`identity_health.py:230-232`:

```python
row = await conn.fetchrow("SELECT failure_count FROM personas WHERE id = $1", persona_id)
failures = (row['failure_count'] or 0) + 1 if row else 1
...
await conn.execute("UPDATE personas SET failure_count = $1, ...", failures, ...)
```

Read-modify-write with no lock. Two concurrent failures increment once (proven: 5 → 6
instead of 7). The 8-failure quarantine threshold (`IDH-3`) therefore triggers late or
never under concurrency, and the exponential cooldown is computed from an undercount.

**Fix:** do it in one statement —

```sql
UPDATE personas
SET failure_count = COALESCE(failure_count,0) + 1,
    state = $2,
    cooldown_until = NOW() + ($3 || ' seconds')::INTERVAL,
    last_active = NOW()
WHERE id = $4
```

## C5 — MEDIUM — state transitions are blind writes, last-writer-wins
> **Fixed:** the success reset is now `AND state NOT IN ('limited','rotated')`.
>
> **Deliberately left open:** the three sites that *set* `limited` are still
> unguarded. Those fail closed — an extra quarantine, never a released one — so they
> are a hardening item, not a correctness bug.

`identity_health.py:223` (`SET failure_count = 0, state = 'active'`) runs on any success
with no guard on the current state. A concurrent failure that quarantines the persona as
`'limited'` can be silently overwritten back to `'active'` (proven). Same pattern at
`hardened_orchestrator.py:4865`, `:4943`, `:4973`.

**Fix:** guard the transition, e.g. `... WHERE id=$1 AND state <> 'limited'`, and drive
transitions off `RETURNING`.

## C6 — MEDIUM — cooldown is not enforced by the queue
> **Fixed:** the claim query filters
> `AND (p.cooldown_until IS NULL OR p.cooldown_until <= NOW())`, on top of the new
> persona lease.

`cooldown_until` is only ever **written** (`identity_health.py:262`) and read for health
scoring (`:119`). The task SELECT filters `p.state NOT IN ('limited','rotated')` —
`'cooldown'` is not excluded, and `cooldown_until` is never compared to `NOW()`. So a
persona placed in cooldown for an hour is still handed tasks (proven).

Cooldown today is enforced *only* by the local `await asyncio.sleep(cooldown)` at
`hardened_orchestrator.py:4125` — per-daemon, in memory, and gone after a restart.

**Fix:** add to the queue query:

```sql
AND (p.cooldown_until IS NULL OR p.cooldown_until <= NOW())
```

## C7 — MEDIUM — `session_data` is a blind overwrite
> **Fixed:** the save is a compare-and-swap,
> `WHERE id = $1 AND session_data IS NOT DISTINCT FROM $2`, against the value loaded
> at task start. On mismatch the save is skipped and logged instead of destroying the
> other session. The C9 persona lease makes this unreachable in normal operation; the
> CAS is the backstop for anything that slips past it.

`hardened_orchestrator.py:3968`:

```sql
UPDATE personas SET session_data = $1, last_active = NOW() WHERE id = $2
```

Two concurrent sessions for one persona: last writer wins, the other's encrypted cookies
are silently and permanently lost (proven). **Fix:** optimistic concurrency — add a
`session_version INT`, `... WHERE id=$2 AND session_version=$3`, and retry on 0 rows.

## C8 — MEDIUM — `warmup_stage` is a stale read-modify-write
> **Fixed:** `SET warmup_stage = LEAST(COALESCE(warmup_stage, 0) + 1, 7)` with
> `RETURNING` — the increment happens in SQL, so no stale read can be written back.

Read at task start (`:2255`), incremented in memory (`:4040`), blind-written later
(`:4042`). Two tasks for the same persona: both read 3, both write 4 — one seasoning day
is lost, and a long-running task can clobber a newer value with a staler one, moving the
stage backwards. This column is a **safety gate** — `tasks/site_register/executor.py:345-347`
blocks signup below 7 — so drift here changes behaviour, not just bookkeeping.
**Fix:** `UPDATE personas SET warmup_stage = LEAST(warmup_stage + 1, 7) WHERE id = $1`.

## C9 — MEDIUM — nothing prevents two tasks for the *same* persona running at once
> **Fixed:** the claim transaction takes a lease on the persona
> (`locked_by`/`locked_until`), refuses personas whose lease is still held, and hands
> the task back if the lease was lost. `_release_persona_leases()` releases only rows
> where `locked_by = WORKER_ID`, and leases expire on their own if a worker dies.

There is no "persona already running" filter anywhere (the SELECT is the only
`status='pending'` site, `:4561`). With N daemons, two pending tasks sharing a
`persona_id` run concurrently — the direct cause of C7 and a contributor to C5. Even with
one daemon, `TASKS_PER_BATCH` > 1 would do it. **Fix:** claim at the persona level
(a `personas.locked_by`/`locked_until` lease), not just at the task level.

## C10 — LOW — `task_count` increment is atomic, but the retirement check is not
> **Fixed:** `UPDATE ... RETURNING task_count` — the retirement check compares the
> post-increment value from the same statement. The duplicate `SELECT task_count` was
> removed.

`:3989` correctly uses `SET task_count = COALESCE(task_count,0) + 1`, but then does a
*separate* `SELECT task_count` (`:4011`) before comparing to `MAX_TASKS_PER_PERSONA`.
Between the two, another worker can increment. **Fix:** `... RETURNING task_count` and
compare that.

---

## Why the ceiling is 1, and what blocks going higher

| layer | limit | evidence |
|---|---|---|
| personas per daemon | 1 | `MAX_CONCURRENT_PERSONAS = 1` (`:354`), `MAX_CONCURRENT_PERSONAS=1`, `TASKS_PER_BATCH=1` |
| daemons per host | 1 as configured | static `ipv4_address: 172.18.0.10` + static `mac_address` (`docker-compose.yml:368,450`) — `--scale` fails on duplicate address |
| CPU | pinned to core 0 | `cpuset: "${ORCHESTRATOR_CPUSET:-0}"` |
| distinct egress exits | 5 | gost listeners 1081–1085 (`docker-compose.yml:281`) |
| exit-IP collisions start at 2 | see below | `get_socks5_port_for_persona()` = `sha256(persona_id)[:8] % 5` (`:627`) |

Egress collision probability (hash buckets, not reservations): 2 personas → 20%, 3 → 52%,
4 → 81%, 5 → 96%.

The `TODO.md` goal of "15–20 concurrent" is aspiration, not implemented behaviour.

---

## Recommended order

1. **C1** — add `failure_count` to `tasks`. Nothing else matters until the queue runs.
2. **C2** — make the claim atomic (`FOR UPDATE SKIP LOCKED` + claim UPDATE).
3. **C9** — add a persona-level lease so one persona is never in two browsers.
4. **C6** — enforce cooldown in the query.
5. **C4 / C5 / C7 / C8 / C10** — collapse read-modify-write into single atomic statements.
6. **C3** — delete `queue_state` or wire it up.

Steps 1–3 make 2+ workers *correct*. Steps 4–6 make the bookkeeping trustworthy. None of
this raises the ceiling past 5 — that needs more gost listeners and more upstream exits.

**All applied** — see the next section.

---

## Fix Status

All ten findings are fixed. Every changed SQL statement was parsed with **pglast 7.x**
(the real PostgreSQL grammar) before being committed, and every race the original audit
proved was re-run against the patched statement sequences.

### What changed, file by file

| File | Change |
|---|---|
| `project/database/schema.sql` | `tasks` gained `failure_count`, `claimed_by`, `claimed_at`; `personas` gained `locked_by`, `locked_until`; added `idx_tasks_status_created`, `idx_tasks_claimed_at` (partial, `status='running'`), `idx_personas_locked_until`, `idx_personas_cooldown_until`; **deleted** `queue_state` and its two indexes (C3) |
| `project/browser-runtime/hardened_orchestrator.py` | `WORKER_ID` + `CLAIM_LEASE_GRACE_SECONDS`; runtime `ALTER TABLE ... IF NOT EXISTS` migration loop; new `_reclaim_stalled_tasks()`, `_claim_tasks()`, `_release_persona_leases()`; daemon loop rewired; atomic `warmup_stage` (C8), `RETURNING task_count` (C10), compare-and-swap `session_data` (C7); all four terminal transitions guarded `AND status = 'running'` |
| `project/browser-runtime/identity_health.py` | atomic failure/cooldown UPDATE (C4); guarded success reset (C5) |

### Verification

Original repro (`/tmp/poc/concurrency_test.py`, 6 scenarios, replayed against SQLite):

```
BEFORE                                    AFTER
[A] UNDEFINEDCOLUMN no such column:       [A] OK    column exists
    t.failure_count
[B] *** DUPLICATE EXECUTION: both         [B] OK    only one worker claims
    workers claimed task id=1
[C] LOST UPDATE: expected 7, actually 6   [C] OK    counter = 7
[D] QUARANTINE CLEARED: limited->active   [D] OK    limited preserved
[E] COOLDOWN BYPASSED: cooling-down       [E] OK    filtered out
    persona handed a task
[F] LOST SESSION: one of two concurrent   [F] OK    no session destroyed
    sessions destroyed
6/6 races reproduced                      6/6 races closed
```

Patched-statement re-test:

```
[A] schema column present ............................ OK
[B] exclusive claim (2nd worker blocked) ............. OK
[B] still exclusive when queue holds 1 row ........... OK
[B] reaper requeues stalled task ..................... OK
[C] atomic counter: no lost update ................... OK
[C] 8th failure flips to limited ..................... OK
[D] limited NOT cleared by success reset ............. OK
[E] cooling-down persona not handed a task ........... OK
[F] CAS: no session overwrite ........................ OK
[F] reaper recycles to retry; terminal UPDATE is a no-op  OK
[G] warmup_stage: two workers -> 2, capped at 7 ...... OK
[H] task_count RETURNING, retirement fires ........... OK
12/12 OK
```

Syntax and static-analysis gates:

```
py_compile  hardened_orchestrator.py ................. OK
py_compile  identity_health.py ....................... OK
pglast      11/11 new SQL statements parse ........... OK
ruff        no NEW violations vs. pre-change baseline  OK
git diff --stat  3 files, +669 / -161 ................ OK
```

### What is still open

1. **The ceiling is still 1.** These are correctness fixes, not capacity fixes. The
   blockers listed under "Why the ceiling is 1" — `MAX_CONCURRENT_PERSONAS`, the static
   container IP and MAC, `cpuset`, and above all the 5-port gost proxy — are untouched.
   Practical ceiling after these fixes is **5**, not 1, but only once those are addressed.
2. **The three `SET state = 'limited'` sites are still blind** (C5, noted above).
3. **No live Postgres verification.** The races are statement-sequence races and are
   engine-independent, but `FOR UPDATE SKIP LOCKED` behaviour itself has only been
   reasoned about, not observed. Run this against a real Postgres before trusting it in
   production.


---

## Part 2 - Static IPs, egress sharing, and a comment audit

Follow-up question: *"we have static IPs - is there any risk that concurrent
running will impact each other?"*

**Answer: yes.** And the largest risk is worse than the C1-C10 data races, because
it is architectural rather than a code defect.

### C11 - CRITICAL - the five "distinct exits" share one upstream

`docker-compose.yml` runs gost with five `-L` listeners and **one** `-F`:

    -L=socks5://172.18.0.5:1081 ... -L=socks5://172.18.0.5:1085
    -F=${UPSTREAM_RES_PROXY}

In gost v2 `-L` is a listener and `-F` is the **global** upstream chain applied to
every listener. The comment above it claimed "Each port = distinct upstream exit";
the config never implemented that. `UPSTREAM_RES_PROXY` is a single endpoint,
`socks5://user:pass@gate.residentialprovider.com:1080`.

Consequence: `get_socks5_port_for_persona()` hashes a persona UUID into one of
five listeners, and all five lead to the same exit. Every persona egresses from the
same IP. That is **100% shared egress at N>=2**, not the 20%/52%/81% that five
independent exits would produce.

**Status:** the false comments are corrected in four places. The egress topology
itself is deliberately NOT changed - distinct exits require one gost instance (or
chain config) per upstream, which is a procurement decision, not a config tweak.

### C12 - HIGH - static IP and MAC block `docker compose up --scale`

`ipv4_address: 172.18.0.10` and `mac_address: 3c:6a:9d:1f:2e:8b` were hardcoded on
the orchestrator. Two replicas request the same address -> "Address already in use".

**Fix:** both removed. `172.18.0.10` was verified unreferenced by any code, script
or config outside `docker-compose.yml`, so dropping it is safe. Docker now assigns
both values per replica.

### C13 - HIGH - `pids_limit: 64` was below the app's own budget of 128

`check_pid_budget()` guards `PERSONA_PID_BUDGET = 128`, but the cgroup killed the
container at 64 - so the application check could never fire before the cgroup did,
and a single Chrome (~80 PIDs) was enough to trip the ceiling.

**Fix:** `pids_limit: ${ORCHESTRATOR_PIDS_LIMIT:-256}`, documented as "must be >=
PERSONA_PID_BUDGET + orchestrator overhead". Raising this does **not** raise the
concurrency ceiling; it stops the container killing a single browser.

### C14 - MEDIUM - MAC randomization was never implemented

The comment claimed entrypoint.sh overrides the MAC at boot with
`ip link set ... address`. The only `ip link` call in `entrypoint.sh` sets the MTU
(line 941). No code ever changed the MAC. **Fix:** comment corrected; see C12.

### C15 - MEDIUM - hardware identity is shared by every container

Five host files are bind-mounted read-only into `/proc` and `/sys`:
`mock_cpuinfo`, `mock_version`, `mock_dmi_id`, `mock_boot_id`, `mock_osrelease`.

The comment claimed `mock_boot_id` is "rewritten on each persona rotation". No
Python or shell code writes it: `hardened_host_os.sh` writes it **once** at
provisioning from `$PERSONA_UUID`, makes it immutable (`chattr +i`), and every
container mounts that same file read-only. Every container reports identical CPU
model, kernel string, DMI and boot_id.

**Fix:** comments corrected in four files. The sharing itself is left as-is.

### C16 - MEDIUM - no CPU/memory headroom for a second browser

`cpuset: 0`, `cpus: 0.5`, `mem_limit: 900m`. The compose comment states Chrome
~700 MB + Python ~200 MB, which already consumes the entire allowance.

### C17 - MEDIUM - a false "guarantee" that silently disabled a lock

`city_persona.py` claimed its `finally` block "guarantees the FOR UPDATE row lock
is never leaked". When `COMMIT`/`ROLLBACK` raised, the exception was swallowed as
"non-fatal" **and** `conn._m6_in_transaction` was set to `False` regardless. The
transaction could still be open while the flag claimed it was closed, so the next
call would skip `BEGIN`, take no row lock, and silently disable the TOCTOU
protection the block exists to provide.

**Fix:** the flag is now cleared only when the transaction actually ended, and a
failed `COMMIT` is logged as an error rather than a warning.

---

## Comment audit

Swept **241** comments across `project/**/*.py` containing a verifiable claim
(`prevents` / `guarantees` / `never` / `always` / `unique` / `distinct` /
`isolated` / `atomic` / `fail-closed` / `correlation`), plus shell and compose
comments.

| Location | Claimed | Reality |
|---|---|---|
| `docker-compose.yml` header | "eliminates **all** cross-account correlation vectors" | Removes some; shared egress IP and shared identity files remain |
| `docker-compose.yml` header | "Per-persona MAC randomization" | Never implemented (C14) |
| `docker-compose.yml` header | "Boot-ID mount (per-persona stable fake UUID)" | One immutable host file, shared by all (C15) |
| `docker-compose.yml` gost block | "Each port = distinct upstream exit" | All five share one `-F` (C11) |
| `docker-compose.yml` gost block | "prevents cross-persona source IP correlation" | Same exit IP for every persona (C11) |
| `docker-compose.yml` volumes | "orchestrator rewrites [boot_id] on each persona rotation" | Nothing rewrites it (C15) |
| `docker-compose.yml` MAC | "entrypoint.sh can override this with `ip link set`" | No such call exists (C14) |
| `docker-compose.yml` restart | "NEW container with a fresh boot_id" | boot_id never changes (C15) |
| `hardened_orchestrator.py` v5-P11 | "Per-persona boot_id rotation via rewrite" | Not implemented (C15) |
| `hardened_orchestrator.py` v5-P21 | "new container = new boot_id, new MAC, new tmpfs" | True for tmpfs/MAC now, false for boot_id |
| `hardened_orchestrator.py` v5-P22 | "Daemon loop ... race-free" | Withdrawn; the gather is in shutdown, and C1-C10 were real races |
| `hardened_orchestrator.py` launcher | "NEW container with a NEW boot_id" | boot_id is a constant (C15) |
| `hardened_orchestrator.py` `get_socks5_port_for_persona` | "prevents cross-persona source IP correlation" | Unique port, shared upstream (C11) |
| `hardened_orchestrator.py` `get_socks5_url_for_persona` | "preventing cross-persona source IP correlation" | Same (C11) |
| `city_persona.py` `_compute_socks5_url` | "preventing cross-persona source IP correlation" | Same (C11) |
| `city_persona.py` GAP 1.11 | "ensures each persona has a unique SOCKS5 source IP" | Unique port only (C11) |
| `city_persona.py` M6 `finally` | "guarantees the FOR UPDATE row lock is never leaked" | Flag cleared even on failure (C17) |
| `hardened_host_os.sh` sec.10d3 | "Mock boot_id (per-persona)"; "changes per persona" | Written once per host, then frozen (C15) |
| `hardened_host_os.sh` `PERSONA_UUID` | "(per-persona stable)" | Generated once per host (C15) |

**Spot-checked and found HONEST** (left unchanged): the socket `try/finally`
guarantee in `is_display_ready()`; the per-container `mktemp` comment in
`entrypoint.sh` (accurately scoped to temp paths); the DBUS machine-id
randomization in `hardened_host_os.sh`.

**Not exhaustively verified:** 241 candidate comments were triaged and the 19 above
were checked line-by-line against the code. The remainder read as accurate, but
only a full manual read would rule out further drift.


---

## The concurrency ceiling, layer by layer

"The ceiling is still 1" is a compressed statement. It is really five independent
limits stacked on top of each other, and the fixes moved three of them.

**Concurrent personas = (containers that can run) x (tasks per container).**

The second term is always 1: `TASKS_PER_BATCH = 1` and the daemon loop consumes
the batch sequentially (`hardened_orchestrator.py:389`, `:4867`). So the ceiling is
really *how many orchestrator containers can run at once*, capped by:

| # | Layer | Capped by | Before | After | Binding now? |
|---|-------|-----------|--------|-------|--------------|
| 1 | Container start | static `ipv4_address` + static `mac_address` | 1 | N | no |
| 2 | PID budget | `pids_limit: 64` | broken even at 1 | N | no |
| 3 | Data integrity | the C1-C10 races | 1 | N | no |
| 4 | Host resources | 1 core, `cpus: 0.5`, `mem_limit: 900m` | ~1 | ~1 | **yes** |
| 5 | Egress / identity | one `UPSTREAM_RES_PROXY`; shared host identity files | 1 | 1 | **yes**, once 4 is lifted |

### Layers that were raised

**Layer 1, container start (1 -> N).** `ipv4_address: 172.18.0.10` and
`mac_address: 3c:6a:9d:1f:2e:8b` were hardcoded on the orchestrator, so replica #2
failed to start with "Address already in use". Both removed (C12). `172.18.0.10`
was verified unreferenced by any code, script or config outside
`docker-compose.yml`, so dropping it is safe. `docker compose up --scale
orchestrator=N` now boots N containers.

**Layer 2, PID budget (broken at 1 -> N).** `pids_limit: 64` sat below the
application's own `PERSONA_PID_BUDGET = 128` and below a single Chrome's ~80 PIDs,
so the cgroup killed the container before `check_pid_budget()` could ever fire.
Now `${ORCHESTRATOR_PIDS_LIMIT:-256}` (C13). Two browsers is roughly 160 PIDs plus
orchestrator overhead, so PIDs are no longer the binding constraint.

**Layer 3, data integrity (1 -> N).** Before the C1-C10 fixes, two workers
executed the same task, lost failure-count updates and destroyed each other's
sessions. The claim / lease / reaper machinery makes N workers safe against each
other. The database layer is now correct for any N.

**Net effect:** the ceiling moved from *"1 because the system breaks above 1"* to
*"1 because the host can only feed one browser"*. The first was a defect; the
second is physics.

### Layers that still bind

**Layer 4, host resources - this is the binding constraint today.**

    cpuset: "${ORCHESTRATOR_CPUSET:-0}"   # pinned to ONE core
    cpus: '0.5'
    mem_limit: 900m / mem_reservation: 600m

The compose comment at `:510` states the host is *"x86 E2.1.Micro (1 GB total)"*
and that Chrome uses ~700 MB with orchestrator Python ~200 MB. One orchestrator
already consumes about 900 MB of a 1 GB host; a second wants another 900 MB. And
`cpuset: 0` means two containers would timeslice the single core that one browser
already saturates.

Raising this needs a bigger host, a `cpuset` range and a higher `mem_limit`. None
of that was changed, because sizing is a deployment decision that depends on
hardware we cannot see from here.

**Layer 5, egress and identity - binds the moment Layer 4 is lifted.** Every
container routes through `proxy-res` into the single `UPSTREAM_RES_PROXY` (C11),
and every container reads identical `mock_cpuinfo`, `mock_version`, `mock_dmi_id`,
`mock_boot_id` and `mock_osrelease` from read-only host mounts (C15). On unlimited
hardware you would get N personas that present as the same machine on the same IP.
Deliberately left unchanged.

### Two knobs that do not do what they look like

**`MAX_CONCURRENT_PERSONAS` is not a gate.** It is a hardcoded literal
(`hardened_orchestrator.py:367`), used in exactly one functional place - sizing the
pacing-engine cache at `:735` (`_max_engines = MAX_CONCURRENT_PERSONAS * 4 + 4`).
Nothing enforces it as a concurrency limit. The `MAX_CONCURRENT_PERSONAS=1` value
compose passes as an environment variable at `:429` is never read by the code. The
audit tests only assert that the literal string `"MAX_CONCURRENT_PERSONAS = 1"`
exists in the source (`R7_DEEP_AUDIT_2/test_pass17_audit.py:380`).

**`TASKS_PER_BATCH` is also hardcoded** (`:389`), not read from the environment, so
it cannot be raised by configuration either.

**Consequence:** raising `MAX_CONCURRENT_PERSONAS` alone changes nothing. Real
concurrency in this system only ever comes from running more containers, and one
container is permanently one task at a time.

### Summary

The *correctness* ceiling went 1 -> N. The *container-start* ceiling went 1 -> N.
The *resource* ceiling is still about 1, and the *isolation* ceiling is still 1.
