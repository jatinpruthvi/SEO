# R7 Pass 3 — Implementation Quality & Integration (Cross-Cutting Analysis)

**Question**: *What happens at the edges: crashes, races, memory growth, network failures, persona cold-start?*

**Scope**: All 21 files in `browser-runtime/`
**Approach**: For each lifecycle stage, examine all files for stress/edge-case issues.

---

## Lifecycle coverage map

| Stage | Files involved | Issues |
|---|---|---|
| **Cold start** | entrypoint.sh, Dockerfile, hardened_orchestrator.py | ENT-001, ENT-002, ENT-022, DOK-006, HOR-007 |
| **Persona fetch** | orchestrator (DB query), session_serializer | HOR-005, HOR-008, SSR-006 |
| **Browser launch** | orchestrator (Playwright), prototype_hardener.js | HOR-006, HOR-018, PHO-014 |
| **Warmup** | orchestrator (pacing, circadian), BEN, KBE, HCI, HUM | HOR-009, HOR-011, HOR-012, BEN-011, KBE-010 |
| **Task execution** | task plugins, BEN, KBE, HCI, HUM | BEN-001, KBE-001, HUM-001, HCI-001 |
| **Mid-task panic** | _panic_handler, _clear_browser_storage | HOR-002, HOR-013, HOR-027, HOR-029 |
| **Session serialize** | session_serializer | SSR-001, SSR-002, SSR-005 |
| **DB update** | orchestrator (asyncpg) | HOR-005, HOR-007 |
| **Cleanup** | entrypoint, orchestrator | ENT-024, HOR-019, HOR-020, HOR-024 |
| **Crash** | orchestrator (panic) | HOR-002, HOR-027 |

---

## Top Pass 3 issues by severity

### CRITICAL

| ID | Title | Why critical |
|---|---|---|
| HOR-002 | _panic_handler atexit cleanup never runs | tmpfs persists across panics, observable to next container |

### HIGH

| ID | Title | Impact |
|---|---|---|
| HOR-007 | db_pool min_size=2 may block during transient DB issues | Daemon hangs 5-10s on DB blip |
| HOR-010 | _fernet_decrypt_bytes uses SIGALRM (interrupts async) | Event loop corruption |
| HOR-014 | _verify_single_tmpfs_invariant uses sync subprocess in async | Blocks daemon loop |
| ENT-021 | set -e + apt-get install fragile in if-block | apt-get update failure aborts entrypoint |
| ENT-022 | Xvfb+DBus sequential ~3s startup penalty | Slow cold start |
| DOK-013 | chown -R slow on 1GB+ venv | Slow image build (5-10 min) |
| SPR-012 | ProxyPool cache unbounded | Memory leak in long-running scenarios |
| BEN-010 | Markov state space grows exponentially | OOM for order > 5 |
| KBE-010 | keyboard.type() slow for long texts | 2+ minutes for 1000 chars |
| SSR-006 | Fernet decrypt unbounded | DoS via malicious archive |

### MEDIUM (selected top 10)

| ID | Title | Impact |
|---|---|---|
| HOR-013 | _clear_browser_storage uses hard-coded 2s sleep | Wasteful + unreliable |
| HOR-017 | Schema init conn.execute doesn't handle multi-statement | Cold-start init incomplete |
| HOR-019 | daemon loop doesn't close db_pool on non-signal exit | DB connection leak |
| HOR-021 | Session state encryption uses default executor (unbounded) | Thread pool exhaustion |
| ENT-023 | chrony 3s wait optimistic | Time drift on slow sync |
| ENT-024 | cleanup kills PulseAudio while orchestrator may use it | Audio crash |
| ENT-026 | MASTER_XVFB_DISPLAY stale on pre-spawn failure | Display confusion |
| DOK-014 | ENTRYPOINT ["sh", "-c", ...] fragile | Container fails to start on minimal images |
| DOK-015 | No HEALTHCHECK directive | Docker doesn't know orchestrator is alive |
| SPR-013 | Race condition on concurrent get_proxy_for_persona | Stickiness violation |

---

## Stress-test scenarios (mental walkthrough)

### Scenario 1: Cold start with no NTP reachable

1. Container starts, entrypoint.sh runs
2. Phase 0.10: `chronyd` starts, but cannot reach NTP servers (firewall blocks them in addition to deb.debian.org)
3. `chronyc tracking` returns empty (no Reference ID) for 30 iterations × 0.1s = 3s
4. `CHRONY_OK=false`, warn logged, orchestrator continues
5. Orchestrator starts, `Date.now()` reports pre-sync time
6. Persona navigates to target. Target checks `Date.now()` against a third-party NTP source
7. **Detection**: Clock drift > 1s is flagged by maxmind/IPQS

**Coverage**: ENT-023 partially mitigates; full fix needs `chronyc waitsync 10 0 0 0` + 10s wait.

---

### Scenario 2: CloakBrowser missing at runtime

1. Dockerfile builds without `/opt/cloakbrowser` mount
2. Orchestrator's `cloak_paths` loop finds nothing
3. `cloakbrowser_required=true` (default) → `logger.critical` + `return False`
4. Task is marked failed; orchestrator tries again
5. Same result
6. After `MAX_TASK_RETRIES=3` (default), task is permanently failed; persona is `limited`
7. **Coverage**: HOR-019 — the persona is now `limited`, but the DB pool is still open
8. Daemon continues polling; new tasks for this persona are skipped
9. New personas still work

**Coverage**: Robust. No memory leak. The persona is correctly quarantined.

---

### Scenario 3: Two concurrent get_proxy_for_persona (impossible with MAX_CONCURRENT_PERSONAS=1, but stress test)

1. Task A calls `get_proxy_for_persona(persona_id)` for proxy P1
2. Task B (somehow) calls the same for the same persona_id
3. Both calls see the same cache state, both compute the same MD5 hash
4. Both return P1
5. **Coverage**: SPR-013 — without lock, both calls succeed but the proxy pool could return different proxies under high churn

---

### Scenario 4: Fernet key extraction from memory

1. `hardened_orchestrator.py` reads `ENCRYPTION_KEY` from env
2. Key lives in Python's memory for the orchestrator's lifetime (45 min session)
3. An attacker with `gdb` or `/proc/PID/mem` access extracts the key
4. Attacker can decrypt all session archives
5. **Coverage**: SSR-004 — the key is the master DB key. Per-session HKDF would limit blast radius.

---

### Scenario 5: Long-running session (45+ minutes)

1. Persona session starts; orchestrator has persona in active state
2. `compositor_jitter_sleep` adds 1.5ms jitter per delay
3. `_human_like_delay` produces 30-180s pre-task quiescence
4. Task runs for ~5-20 minutes (typical)
5. Session pacing pads to 60-115s / 120-450s / 600-MAX_SESSION_MINUTES*60 (long session)
6. DB queries: db_pool (size 2-4) handles it
7. `unidentified behavior`: the 1-persona daemon handles 1 task at a time, so 45 minutes is the entire task duration, not a steady state
8. **Coverage**: Robust for 1-persona design.

---

### Scenario 6: WebRTC SDP leak (despite `disable-webrtc` flag)

1. `chromium_args` includes `--disable-webrtc` and `--disable-rtc-audio-jitter-buffer`
2. `WebRTC_MODERN_FLAGS` includes `--force-webrtc-ip-handling-policy=default_public_interface_only`
3. Page calls `new RTCPeerConnection()` — should fail or return no candidates
4. Real Chrome 120 still creates mDNS candidates even with `--disable-webrtc` in some builds
5. **Coverage**: ENT-019 — the blanket UDP REJECT should drop any mDNS candidate. But `--disable-webrtc` may not fully suppress the mDNS query.

---

### Scenario 7: HTTP/2 SETTINGS frame order mismatch

1. Orchestrator's curl_cffi sends HTTP/2 with default Python SETTINGS order
2. Browser (CloakBrowser) sends HTTP/2 with Chrome 120's SETTINGS order
3. Target sees two different SETTINGS orders from the same IP
4. **Coverage**: CFH-3 in R6 added `is_chrome` flag. Need to verify curl_cffi uses Chrome 120's order. (Out of scope for this review — see CFH-*.md R6 fixes.)

---

### Scenario 8: Behavioral pattern across sessions

1. Persona does 5 tasks over 30 minutes
2. Each task has a different action sequence (BEN-001 produces uniform random)
3. Across 5 tasks, the distribution of action sequences is uniformly random
4. Real users have a stable action vocabulary (Markov chain state distribution is persona-specific)
5. **Coverage**: BEN-002 — Markov order 1 means the per-persona pattern is weak. Order 3 would help.

---

### Scenario 9: Container runs out of disk space

1. tmpfs is sized to `TASK_TMPFS_SIZE_MB=1400` (1.4GB)
2. Profile path is on tmpfs
3. If profile exceeds 1.4GB (e.g., 1000s of cookies, large IndexedDB), `OSError: No space left on device`
4. The orchestrator catches the exception, but the cleanup is partial
5. **Coverage**: No specific handler. Could add a size check at profile creation.

---

### Scenario 10: Clock jumps backward (NTP step)

1. chronyd syncs and steps the clock backward by 1 second
2. `time.time()` jumps
3. `perf_counter()` does NOT jump (monotonic)
4. Code using `time.time()` for elapsed-time calculations produces negative durations
5. **Coverage**: HOR-011 — `compositor_jitter_sleep` uses `asyncio.sleep` (which uses monotonic internally), but other places use `time.perf_counter` and `time.time` interchangeably. Document the choice.

---

## Race conditions inventory

| Location | Race | Severity | Status |
|---|---|---|---|
| db_pool acquire | Two tasks on same conn | L | Handled by pool |
| get_proxy_for_persona concurrent | Two tasks, same persona, different proxies | M | Not handled (SPR-013) |
| persona_digest build | Build twice in same task | L | Deterministic, no issue |
| _panic_handler + atexit | atexit doesn't run after os._exit | C | HOR-002 |
| Xvfb spawn (orchestrator fallback) | Two displays collide | M | Loop checks is_display_ready |
| tmpfs cleanup | Race with active writes | M | HOR-013 — 2s sleep is best-effort |
| shutdown signal + async tasks | Tasks cancelled but pool not closed | M | HOR-019, HOR-020 |
| chrony start + chronyc tracking | chronyc before chronyd is fully up | L | Wait loop handles it |
| WebRTC candidates | SDP mDNS leak despite disable | M | ENT-019 partial |

---

## Memory growth audit

| Cache / collection | Bound | Risk | Status |
|---|---|---|---|
| ProxyPool cache (SPR) | Unbounded | M | SPR-012 (LRU needed) |
| DB pool | min=2, max=4 | L | OK |
| Markov chain (BEN) | Order-dependent | M | BEN-010 (cap at 3) |
| worker Blob URLs (PHO) | FinalizationRegistry | L | OK |
| chrony drift samples (PHO) | None (liveJitterState) | L | OK |
| window.history (PHO) | Grows on pushState | L | OK |
| session_serializer decrypt buf | Unbounded (5MB+ archives) | M | SSR-006 (size check) |
| persona_digest dict | Per-task, GC'd | L | HOR-024 (explicit clear) |
| background PIDs (ENT) | Array | L | OK |
| Per-task tmpfs (ENT/HOR) | Bounded by tmpfs size | L | OK |
| cookieStore _jar (PHO) | Unbounded | L | OK for normal session |
| navigator.connection event listeners (PHO) | Unbounded | L | OK |

---

## Error swallowing audit

| Location | Error swallowed | Risk | Status |
|---|---|---|---|
| `try { import playwright } except` | ImportError | L | Logged, fatal |
| `_verify_dmesg_blocked` except | OSError | H | HOR-003 — should distinguish |
| `pacing_engine.record_load_latency` except | Any | L | Logged, OK |
| `try { importlib.import_module }` | ImportError | L | Logged |
| `subprocess.run(..., capture_output=True, timeout=2)` | TimeoutExpired | L | OK |
| `_panic_handler` finally | Any | L | Logs, exits |
| `dom storage clear` try/catch in JS | QuotaExceededError | M | Documented |
| `RTCPeerConnection.getStats` ip replacement | TypeError | M | Caught with Object.defineProperty fallback |
| `URL.revokeObjectURL` in finalizer | Any | L | Caught |

---

## Pass 3 severity distribution

- **Critical (Pass 3 specific)**: 1 (HOR-002)
- **High (Pass 3 specific)**: 10 (HOR-007, HOR-010, HOR-014, ENT-021, ENT-022, DOK-013, SPR-012, BEN-010, KBE-010, SSR-006)
- **Medium (Pass 3 specific)**: 28
- **Low (Pass 3 specific)**: 12
- **Total Pass 3 specific**: 51 issues

---

## Pass 3 implementation priority

**Immediate (next 1-2 days)**:
- HOR-002 (panic atexit)
- HOR-007 (db_pool sizing)
- HOR-010 (SIGALRM in async)
- HOR-014 (sync in async)
- SSR-006 (decrypt size check)
- SPR-012 (LRU cache)

**Near-term (next 1-2 weeks)**:
- DOK-013 (chown optimization)
- DOK-015 (HEALTHCHECK)
- HOR-013 (storage clear wait)
- HOR-019-022 (lifecycle)
- ENT-021-026 (startup/cleanup)
- KBE-010 (bulk typing)
- BEN-010 (Markov cap)

**Long-term (next month)**:
- All Medium/Low items
- Cross-system integration tests
- Stress-test harness

---

## Stress test recommendations

After Phase A fixes are applied, run the following stress tests:

1. **Cold start**: 100 container restarts, measure time to first task. Target: <30s.
2. **DB outage**: Disconnect DB for 60s, measure orchestrator behavior. Target: pauses, recovers.
3. **Proxy failure**: Kill proxy mid-task. Target: clean failure, persona retried, no tmpfs leak.
4. **NTP drift**: Block NTP, measure `Date.now()` drift over 1h. Target: <100ms.
5. **Long session**: Run 1 persona for 45min, monitor memory. Target: <500MB resident.
6. **Action sequence entropy**: Generate 1000 sequences for same persona, measure Shannon entropy. Target: 0.6-0.9.
7. **Mouse path spectral**: Generate 1000 paths, compute Fourier spectrum. Target: 1/f noise distribution.
8. **Cookie restore**: Round-trip 100 cookies with various flags. Target: 0 invalid cookies.
9. **Cold sync**: Container with `chronyc tracking` empty. Target: orchestrator waits 10s, then proceeds.
10. **Concurrent shutdown**: SIGTERM + SIGUSR1 simultaneously. Target: clean exit, no zombies.
