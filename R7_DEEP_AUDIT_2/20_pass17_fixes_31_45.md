# R7-DEEP-AUDIT-2 Pass 17: Fixes 31-45

## Summary

- **15 bugs fixed** across 8 files
- **All 44 Pass 14 tests pass**
- **All Python files** `py_compile` clean
- **`prototype_hardener.js`** `node --check` clean
- **All shell scripts** `bash -n` clean

## Fixes Applied

| # | Sev | File | Bug | Fix |
|---|-----|------|-----|-----|
| 31 | MED | `asymmetric_vault.py` | M1: No `__del__` warning for unclosed vault | Added `close()` method, `_closed` flag, `__del__` warning |
| 32 | MED | `asymmetric_vault.py` | M2: `_trusted_sender_keys` dict unbounded | FIFO eviction at 10000 entries |
| 33 | MED | `asymmetric_vault.py` | E27: `encrypt_session_envelope` no type check on session_data | Added `isinstance(session_data, dict)` check |
| 34 | HIGH | `identity_health.py` | E22: `_acquire_connection` no timeout on `pool.acquire()` | `asyncio.wait_for(..., timeout=5.0)` |
| 35 | MED | `identity_health.py` | E22: `report_telemetry_event` doesn't catch TimeoutError | Added try/except `asyncio.TimeoutError` |
| 36 | MED | `identity_health.py` | E21: `cooldown_seconds` could be negative | Clamp to minimum 1 second |
| 37 | MED | `session_serializer.py` | M8: `decrypt_payload` raises generic ValueError | Re-raise `InvalidToken` for cleaner auth-failure handling |
| 38 | MED | `session_serializer.py` | E23: Cookie list not validated for dict elements | Filter non-dict elements before iteration |
| 39 | LOW | `profile_archiver.py` | E24: `os.path.getsize(tar_path)` called after tar may be deleted | Capture size to local var before log line |
| 40 | HIGH | `profile_archiver.py` | E25: `rehydrate_profile_directory` no path traversal check | Validate path doesn't contain `..` and is absolute |
| 41 | MED | `circadian_scheduler.py` | E26: `__init__` doesn't validate timezone string | Try/except around `_get_timezone`; fall back to UTC |
| 42 | LOW | `circadian_scheduler.py` | E26: `_get_timezone` doesn't handle empty/None input | Validate non-empty string |
| 43 | MED | `dynamic_pacing.py` | E20: `execute_natural_reading_flow` no None-page check | Early return with warning |
| 44 | LOW | `dynamic_pacing.py` | E19: `simulate_idle_pattern` doesn't cap active_period to remaining | `active_period = min(active_period, remaining)` |
| 45 | MED | `ebpf_sentinel.py` | M7: `_connection_cache` unbounded | FIFO eviction at 1000 entries |

## Additional Defensive Fixes (15-15 in the same Pass)

| # | Sev | File | Bug | Fix |
|---|-----|------|-----|-----|
| 46 | LOW | `input_sanitizer.py` | E15: `max_length` not validated (could be 0/negative) | Clamp to [1, 1_000_000] |
| 47 | MED | `paste_emulator.py` | E16: `human_copy_paste_workflow` no input validation | Validate page, selector, text_content non-empty |
| 48 | MED | `session_serializer.py` | E17: `seed_fresh_profile` no input validation | Validate context and page are not None |
| 49 | LOW | `city_persona.py` | E15: env var parsing crashes on non-numeric | try/except with default fallback |

## Test Results

```
=== test_pass14_thread_removal.py ===  ✅ (10/10)
=== test_pass14_medium_priority.py ===  ✅ (12/12)
=== test_pass14_n1_n2_per_persona_rng.py ===  ✅ (9/9)
=== test_pass14_n6_n7_tasks_rng.py ===  ✅ (13/13)
```

**Total: 44/44 Pass 14 tests pass.**

## Key Implementation Notes

### Fix 31 (M1): Vault `__del__` warning

The `AsymmetricSessionVault` holds sensitive ECDH private key material. The previous code had no `__del__` method, so a vault that fell out of scope without `close()` would silently leak the private key (the key would be in memory until GC, but the GC isn't guaranteed to actually zero the bytes — only frees for reuse). The new `close()` method explicitly clears the TOFU cache and drops the private_key reference; `__del__` warns if the vault was not closed.

### Fix 34 (E22): Timeout on `pool.acquire()`

The previous `self._pool.acquire()` had no timeout. If the pool was exhausted (all 4 connections in use and waiting for slow queries), the acquire would hang indefinitely, blocking the identity health check. The new `asyncio.wait_for(..., timeout=5.0)` ensures the check returns within 5s with a clear default health score (0.5).

### Fix 40 (E25): Path traversal protection

The `rehydrate_profile_directory` method used `encrypted_archive_path` directly in `open(...)`. A caller passing `../../etc/passwd` would attempt to read an arbitrary file. The new validation rejects relative paths and paths containing `..` segments.

### Fix 45 (M7): Connection cache FIFO eviction

The `_connection_cache` dict could grow unbounded for a long-running daemon. Capping at 1000 entries with FIFO eviction matches the `_profile_cache` pattern in `humanized_movement.py`. Each entry is ~100 bytes, so the cap limits memory to ~100KB.

## Files Modified

- `browser-runtime/asymmetric_vault.py` (Fixes 31, 32, 33)
- `browser-runtime/identity_health.py` (Fixes 34, 35, 36)
- `browser-runtime/session_serializer.py` (Fixes 37, 38, 48)
- `browser-runtime/profile_archiver.py` (Fixes 39, 40)
- `browser-runtime/circadian_scheduler.py` (Fixes 41, 42)
- `browser-runtime/dynamic_pacing.py` (Fixes 43, 44)
- `browser-runtime/ebpf_sentinel.py` (Fix 45)
- `browser-runtime/input_sanitizer.py` (Fix 46)
- `browser-runtime/paste_emulator.py` (Fix 47)
- `browser-runtime/city_persona.py` (Fix 49)

## Next Steps

**Pass 18: Fix bugs 46-60** (15 remaining bugs):
- L3: `_COMPOSITOR_JITTER_STD` module-level (architectural — defer)
- L5: `move_mouse_like_human` doesn't handle `target_resolver` returning None
- L6: `Fetch_Dynamic_RSS_Headline` static list fallback
- L7: `dynamic_pacing.py` `execute_natural_reading_flow` ignores persona
- L11: (already fixed in Pass 16)
- Plus remaining edge cases
