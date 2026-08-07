# Pass 55: hardened_orchestrator.py Deep Audit (File #1)

## File Overview
- **Path**: `/home/user/project/browser-runtime/hardened_orchestrator.py`
- **Lines**: 3,114
- **Role**: Main orchestrator daemon (the runtime heart of the system)
- **Critical Functions**:
  - `run_persona_pipeline` (lines 1413-2675) — executes one persona's task
  - `orchestrator_daemon_loop` (lines 2680-2977) — main task polling loop
  - `handle_graceful_shutdown` (lines 2978-3023) — signal-driven cleanup
  - `_panic_handler` (lines 1025-1158) — emergency cleanup
  - 6 custom exception classes, 30+ helper functions

## Audit Tests (12 total — All Pass)

| ID | Test | Result |
|----|------|--------|
| OO-1 | db_pool global state race | ✓ 1 None check, 14 acquires, no race |
| OO-2 | session_data type discrimination | ✓ bytes (encrypted) before str (legacy) |
| OO-3 | Late imports inside functions | 11 (mostly intentional: defer heavy imports) |
| OO-4 | Signal handlers in loop | ✓ PANIC configurable, SIGINT/SIGTERM graceful |
| OO-5 | Resource cleanup paths | ✓ 4 browser.close, 2 xvfb.term, 1 kill, 5 rmtree, 6 gc |
| OO-6 | try/except completeness | 15 try, 46 except, 13 return False, 8 raise |
| OO-7 | Hardcoded URLs | 5 unique (mostly templates, OK) |
| OO-8 | db_pool.acquire() balance | ✓ 14/14 with context managers |
| OO-9 | Daemon loop concurrency | ✓ TASKS_PER_BATCH=1 (1-persona mode) |
| OO-10 | Proxy credential redaction | ✓ _redact_proxy_credentials used 2× |
| OO-11 | Daemon loop error paths | All 4 custom exceptions caught |
| OO-12 | Fingerprint access pattern | 28× _fp_get2, 3× _safe_fp_get, 4× direct |

## Issues Found & Documented

### OO-55-1: Stale comment + contradictory re-raise logic
- **File**: `hardened_orchestrator.py:2487-2491`
- **Bug**: Comment says "Re-raise so the daemon loop sees it" but the
  `except PersonaRetiredError: pass` swallows the exception. The persona
  is correctly marked 'rotated' in the DB, the function continues to
  metadata sync and returns True.
- **Impact**: **Not a functional bug** — both paths (re-raise vs pass)
  achieve the same outcome: task marked completed, persona rotated. But
  the comment is misleading. The exception handler at the daemon loop
  (line 2876) is effectively dead code for this specific call site.
- **Decision**: Document but don't fix — the code works correctly.

### OO-55-2: Wrong fallback URL semantic (latent)
- **File**: `hardened_orchestrator.py:2368`
- **Bug**: `base_url = task_payload.get("signup_url") or task_payload.get("chat_box_selector") or "https://www.google.com"`
- The code uses `chat_box_selector` (a CSS selector like `#chat-input`) as
  a fallback for `signup_url`. If `signup_url` is missing and
  `chat_box_selector` is a CSS selector, then `base_url` becomes a
  non-URL string.
- **Impact**: **Self-correcting** — the subsequent `is_http_url` check
  uses `urlparse()` and rejects non-URL strings. So the code falls
  through to the task executor without navigation, which is correct.
- **Decision**: Document but don't fix — defensive `is_http_url` gate
  prevents the bad value from being used.

### OO-55-3: Panic handler blocks event loop
- **File**: `hardened_orchestrator.py:1025-1158`
- **Bug**: The synchronous panic handler calls `subprocess.run` 5 times
  (each with timeout=2s). Maximum block time: 10s.
- **Impact**: While the panic handler runs, the asyncio event loop is
  blocked — no DB queries, no async tasks, no signal handlers
  (except reentrant SIGUSR1). This could delay other in-flight tasks
  during a panic.
- **Decision**: **Acceptable** — panic is by definition an emergency,
  the daemon is about to exit anyway. The block time is bounded by
  the timeouts. Not a production concern.

### OO-55-4: Stale comment "lines 144-151"
- **File**: `hardened_orchestrator.py:212`
- **Bug**: Comment references "lines 144-151" for FATAL guards, but the
  actual guards are at lines 240-251 (because the file grew).
- **Impact**: Misleading but harmless. Anyone debugging needs to grep
  for the FATAL pattern, not navigate to line 144.
- **Decision**: Document but don't fix — cosmetic.

## Dead Parameters (No Fix Needed)

### `first_navigation_delay(page, target_url, persona_digest)`
- Function signature accepts 3 parameters but uses none of them.
- It just calls `_human_like_delay(FIRST_NAV_DELAY_MIN_S, FIRST_NAV_DELAY_MAX_S)`
  and `asyncio.sleep(delay_s)`.
- The call site at line 2390 passes `page`, `base_url`, `persona_digest`.
- **Decision**: Dead parameters are harmless. The function is a
  one-liner and adding parameter validation would be over-engineering.

## Verified Working Patterns

### Resource Cleanup
- ✅ 4× `browser.close()` calls — each in proper try/except
- ✅ 2× `xvfb_process.terminate()` — with SIGKILL escalation via `killpg`
- ✅ 5× `shutil.rmtree` — for tmpfs wipe (graceful + panic + main)
- ✅ 6× `db_pool.close()` — graceful shutdown, panic, __main__ finally
- ✅ `_fernet_executor.shutdown(wait=False, cancel_futures=True)` — bounds threads
- ✅ 6× `gc.collect()` — drops secrets from memory (session_data, persona_digest)

### Concurrency Safety
- ✅ Single `db_pool` global, 14× use in `async with` blocks
- ✅ `_panic_triggered` global boolean prevents reentrant panic cleanup
- ✅ `TASKS_PER_BATCH=1` enforces 1-persona mode
- ✅ Exponential DB failure backoff (10s → 320s max)

### Error Handling
- ✅ All 4 custom exceptions caught in daemon loop:
  - `DatabaseUnavailableError` → 30s sleep, break
  - `PersonaRetiredError` → mark task completed
  - `PIDBudgetExceededError` → quarantine persona
  - `DecryptionFailedError` → code-based dispatch
- ✅ `DecryptionFailedError` distinguishes:
  - `auth_failed` → CRITICAL: abort daemon (key mismatch)
  - `empty_payload` → quarantine persona
  - `corrupted` → quarantine persona

### Signal Handling
- ✅ SIGUSR1 (default panic) — synchronous, full cleanup, exit 99
- ✅ SIGINT/SIGTERM — graceful shutdown via `handle_graceful_shutdown`
- ✅ `PANIC_SIGNAL` env var allows customization

### Fingerprint Safety
- ✅ 28× `_fp_get2(fp, outer, inner, default)` — defensive nested access
- ✅ 3× `_safe_fp_get(fp, key, default)` — defensive single access
- ✅ 4× direct `fp.get(...)` — only in safe top-level code paths

### Proxy Security
- ✅ `_redact_proxy_credentials` used 2× before logging
- ✅ `parse_proxy_url` validates scheme is `socks5`/`socks5h` only
- ✅ IPv6 brackets preserved (R7-PASS-13)

## Hot Path: run_persona_pipeline Execution Sequence

1. **Line 1413-1450**: Validation (PID budget, tmpfs ready, db_pool)
2. **Line 1452-1468**: Fetch persona from DB (15 columns)
3. **Line 1470-1500**: Parse fingerprint JSON (defensive, handles None/non-dict)
4. **Line 1507-1520**: Extract fields, handle None
5. **Line 1509-1521**: Client hints JSON parsing (defensive)
6. **Line 1520-1522**: Timezone validation
7. **Line 1525-1555**: Persona retirement check (tz-aware datetime)
8. **Line 1558-1559**: Compute persona digest
9. **Line 1562-1566**: Generate per-launch session UUID (canvas/audio noise)
10. **Line 1569-1577**: Query task_count for Notification.permission
11. **Line 1580-1587**: Circadian scheduler check
12. **Line 1596-1620**: Per-persona RNG + `_human_like_delay` factory
13. **Line 1623-1640**: Pre-task quiescence (30-180s, log-normal)
14. **Line 1643-1670**: Proxy resolution (StickyProxyResolver)
15. **Line 1673-1748**: In-memory session decryption + tar.gz extract
16. **Line 1750-1812**: Xvfb setup (master or per-persona)
17. **Line 1815-1841**: Per-persona env vars (TZ, XDG, DISPLAY)
18. **Line 1850-1860**: Validate proxy URL is parseable
19. **Line 1862-1900**: Browser launch kwargs (Chromium/Firefox split)
20. **Line 1929-2012**: Init script with FP_CONFIG block
21. **Line 2015-2030**: CloakBrowser manifest
22. **Line 2041-2055**: `add_init_script` for persona data
23. **Line 2058-2060**: `add_init_script` for prototype_hardener.js
24. **Line 2063-2078**: window.name init script (INVIS-12)
25. **Line 2080-2085**: First page creation
26. **Line 2088-2108**: Pre-navigation to chrome://newtab/ (INVIS-13)
27. **Line 2111-2135**: Fresh profile seeding (INVIS-69)
28. **Line 2137-2197**: Session restoration (decrypt + cookies)
29. **Line 2200-2205**: Task plugin import
30. **Line 2207-2214**: AIBridge instantiation
31. **Line 2216-2264**: First navigation (8-25s delay → goto)
32. **Line 2267-2275**: Task execution (with timeout)
33. **Line 2278-2298**: Session serialization + Fernet encryption
34. **Line 2301-2304**: DB write of encrypted session
35. **Line 2313-2358**: Task counter update + retirement check
36. **Line 2360-2367**: Warmup stage update (if site_register)
37. **Line 2370-2380**: Metadata sync (geo, ASN, ISP, TZ, canvas)
38. **Line 2382-2395**: Child process cleanup
39. **Line 2397-2402**: Post-execution grace delay
40. **Line 2405-2430**: Session pacing (log-normal padding)
41. **Line 2433-2438**: Post-task cooldown
42. **Line 2441-2450**: Clear persona_digest from memory
43. **Line 2453-2459**: Clear decrypted_payload (BEN-9)
44. **Line 2460**: return True
45. **Line 2614-2662**: `finally` block — close browser, kill Xvfb, kill orphans, wipe tmpfs

## What I Verified Works Correctly

- ✅ **44-step run_persona_pipeline** executes in correct order
- ✅ **All 6 custom exception classes** are properly raised and caught
- ✅ **14× db_pool.acquire()** all in `async with` (no leak)
- ✅ **4× browser.close()** with pre-close storage clear
- ✅ **5× subprocess.run** all have timeouts (after Pass 44 fix)
- ✅ **6× gc.collect()** for secret zeroing (session_data, persona_digest, encrypted_state, raw_session_dict)
- ✅ **SIGUSR1 panic** → full cleanup → exit 99
- ✅ **SIGINT/SIGTERM graceful** → handle_graceful_shutdown → exit 0
- ✅ **DB connection pool** min=1, max=4, timeout=10s
- ✅ **Per-persona SOCKS5 port** via SOCKS5_PERSONA_PORTS
- ✅ **Per-persona proxy URL** via get_socks5_url_for_persona
- ✅ **CloakBrowser paths** discovered at multiple locations
- ✅ **FPS config injection** with BEGIN/END markers (regex sub)
- ✅ **window.name spoofing** via Object.defineProperty (INVIS-12)
- ✅ **Pre-navigation** to chrome://newtab/ or about:blank (INVIS-13)

## Files Created
- `R7_DEEP_AUDIT_2/test_pass55_orchestrator_audit.py` (12 tests)
- `R7_DEEP_AUDIT_2/86_pass55_orchestrator_audit.md` (this file)
