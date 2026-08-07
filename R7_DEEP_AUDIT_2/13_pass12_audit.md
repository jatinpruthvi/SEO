# R7-DEEP-AUDIT-2 Pass 12: Multi-Persona Determinism & Stress Audit

**Date:** 2026-07-14
**Auditor:** Pass 12 (continuation of Pass 11)
**Scope:** Multi-persona behavior — same persona across launches, different personas differ, no cross-contamination, stress testing

## Methodology

Pass 11 audited the orchestrator's null safety. Pass 12 focuses on the **integration layer**:
- Same persona launched multiple times → same fingerprint
- Different personas → different fingerprints
- Per-launch UUIDs don't affect stable values
- 20 personas in sequence → no leaks, no shared state
- Per-persona settings (notification, hardware, etc.) propagate correctly

## Issues Found

### Issue #1: `INVIS-15` language variants overwrote user's languages array (CRITICAL)

**Severity:** CRITICAL
**Location:** `prototype_hardener.js` lines 1028-1068 (INVIS-15 logic)
**File:** `/home/user/project/browser-runtime/prototype_hardener.js`

The previous INVIS-15 logic auto-generated language variants based on the FIRST
language of the persona, ignoring the rest of the array. For a mobile
Spanish speaker with `languages: ["en-US", "es-MX"]`, the output was
`["en-US", "en-CA", "en"]` — **losing the `es-MX` entirely** and adding
`en-CA` based on the master seed.

This is a critical fingerprint spoofing bug because:
1. The persona's claimed `language` (e.g., `es-MX` for a Spanish speaker) is lost
2. An auto-generated `en-CA` is added that doesn't match the persona
3. A page that checks `navigator.languages.includes("es-MX")` would return false

**Fix:** Modified INVIS-15 to PRESERVE the original languages and only APPEND
auto-generated variants. Deduplication prevents adding variants that already
exist. The base language code is only added if not already present.

**Verification:** Test now passes:
- Mac persona `["en-US", "en", "fr-CA"]` → `["en-US", "en", "fr-CA"]` (preserved)
- Mobile persona `["en-US", "es-MX"]` → `["en-US", "es-MX", ...]` (preserved)

### Issue #2: `screen.orientation` override blocked by instance property (TEST INFRA)

**Severity:** LOW (test infrastructure only, not a hardener bug)
**Location:** `pass12_audit.js` test polyfill

The test polyfill defined `screen.orientation` on the screen INSTANCE
(with `landscape-primary`). The hardener's override attempts to set
`Screen.prototype.orientation` (with `portrait-primary` for mobile), but
the instance property has higher precedence in the JS prototype chain.

**Fix:** Changed test polyfill to define on `Screen.prototype` instead of
the instance, allowing the hardener's override to take effect.

**Note:** This is a test infrastructure issue, not a hardener bug. In
production, the screen instance doesn't have a custom `orientation`
property, so the hardener's prototype override works correctly.

### Issue #3: `db_failure_count` not reset on success (BUG)

**Severity:** MEDIUM
**Location:** `hardened_orchestrator.py` line 2541

The previous code had `_db_failure_count = 0` inside the try block (after
a successful DB fetch), but this was a LOCAL variable, not the
function-level attribute used in the except block. So the backoff counter
was never reset on success — a single DB failure would permanently use
the elevated backoff.

**Fix:** Changed to `orchestrator_daemon_loop._db_failure_count = 0` to
reset the function-level attribute.

**Verification:** Manual inspection confirms the function-level attribute
is now properly reset on success.

### Issue #4: `hardware_concurrency` could be `null` in f-string

**Severity:** LOW
**Location:** `hardened_orchestrator.py` line 1886

`persona_digest.get("hardware_concurrency", cores_to_spoof)` returns None
if the key exists with value None. While `_compute_persona_digest()`
always sets this key with a valid value, defensive programming is
better.

**Fix:** Changed to `persona_digest.get("hardware_concurrency") or cores_to_spoof`
to ensure a valid fallback.

## Results

**Before fix:** 21 / 24 tests pass (3 critical failures: languages, screen.orientation, mobile touch)
**After fix:** 24 / 24 tests pass

**Total tests: 28 + 57 + 24 + 85 + 51 = 245 tests, 0 failures.**

## Multi-Persona Test Coverage

The Pass 12 test suite exercises 3 personas:
- **Windows_User**: Win32, 8 cores, en-US, America/New_York, 0 sessions
- **Mac_Designer**: MacIntel, 16 cores, en-US+fr-CA, America/Los_Angeles, 5 sessions
- **Mobile_Android**: Linux armv81, 4 cores, en-US+es-MX, America/Chicago, mobile=true, 5 touch points

Tests verify:
1. **Stability**: Same persona launched with different UUIDs → same values
2. **Uniqueness**: Different personas → different values
3. **Per-launch variation**: Session IDs don't affect stable values
4. **Cross-persona isolation**: No contamination between windows
5. **Stress**: 20 personas in sequence → no leaks
6. **Per-persona settings**: notification permissions, orientation, touch points

## Final Validation

```bash
$ for f in browser-runtime/*.py; do python3 -m py_compile "$f"; done
Python: 18/18 OK
$ node --check browser-runtime/prototype_hardener.js
JS: OK
$ for f in browser-runtime/*.sh; do bash -n "$f"; done
Shell: 2/2 OK

$ node /tmp/test_after_fixes.js   # Pass 9
Results: 28 passed, 0 failed

$ node /tmp/pass10_audit.js        # Pass 10
Total: 57 | Pass: 57 | Fail: 0

$ node /tmp/pass12_audit.js        # Pass 12 (NEW)
Total: 24 | Pass: 24 | Fail: 0

$ python3 /tmp/test_orchestrator_runtime.py  # Pass 11 base tests
SUMMARY: 85 tests, 85 passed, 0 failed

$ python3 /tmp/test_pass11_fixes.py   # Pass 11 fix verification
SUMMARY: 51 tests, 51 passed, 0 failed
```

## Total Audit Tally (12 passes)

| Pass | Issues Found | Fixed |
|------|-------------|-------|
| R7 Phase A (12 critical) | 12 | 12 |
| R7 Phase B (44 high) | 44 | 44 |
| R7 Phase C (111 medium/low) + 7 R7-NEW | 118 | 118 |
| Deep audit Pass 1-2 | 10 | 10 |
| Deep audit Pass 3 (orchestrator) | 7 | 7 |
| Deep audit Pass 8 (behavioral models) | 3 | 3 |
| Deep audit Pass 9 (Phase B re-audit) | 13 | 13 |
| Deep audit Pass 10 (runtime edge cases) | 4 | 4 |
| Deep audit Pass 11 (orchestrator JSON null safety) | 7 | 7 |
| **Deep audit Pass 12 (multi-persona determinism)** | **4** | **4** |
| **TOTAL** | **222** | **222** |

**Total runtime tests: 245 tests, 0 failures.**

## Files Modified

- `/home/user/project/browser-runtime/prototype_hardener.js`
  - INVIS-15: Preserve all user-provided languages, only append variants

- `/home/user/project/browser-runtime/hardened_orchestrator.py`
  - Line 2541: Reset function-level `_db_failure_count` on success
  - Line 1886: `or cores_to_spoof` fallback for hardware_concurrency

## Test Files

- `/home/user/R7_DEEP_AUDIT_2/pass12_audit.js` — 24 multi-persona tests
- `/home/user/R7_DEEP_AUDIT_2/test_orchestrator_runtime.py` — 85 orchestrator tests
- `/home/user/R7_DEEP_AUDIT_2/test_pass11_fixes.py` — 51 null-safety tests
- `/home/user/R7_DEEP_AUDIT_2/pass10_audit.js` — 57 hardener tests
