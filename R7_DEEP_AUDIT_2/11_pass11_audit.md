# R7-DEEP-AUDIT-2 Pass 11: Orchestrator JSON Null Safety Audit

**Date:** 2026-07-14
**Auditor:** Pass 11 (continuation of Pass 10)
**Scope:** hardened_orchestrator.py — defensive handling of JSON null values in fingerprint data

## Methodology

Pass 10 audited the prototype_hardener.js (JS). Pass 11 audits the orchestrator
itself (Python), specifically the fingerprint-handling code paths. The audit
focuses on the **FP_CONFIG f-string** that injects per-persona fingerprint data
into the JS hardener, since malformed fingerprint JSON (null values, missing
keys, wrong types) would crash the entire task with an obscure `AttributeError`.

## Issues Found

### Issue #1: `fp.get('navigator', {}).get('model', '')` crashes on `navigator: null` (CRITICAL)

**Severity:** CRITICAL
**Location:** hardened_orchestrator.py, multiple lines (20+ occurrences)
**File:** `/home/user/project/browser-runtime/hardened_orchestrator.py`

The standard Python idiom for nested-dict access uses `.get(key, default)`.
This pattern crashes with `AttributeError: 'NoneType' object has no attribute 'get'`
when the **KEY IS PRESENT but the VALUE IS `None`** (because the default is
only used when the key is missing).

The PostgreSQL JSONB schema for the `fingerprint` column allows null values
at any level. A record inserted with `{"navigator": null, "webgl": null}`
(e.g., a partial fingerprint, an import from another schema, or a manual
DB edit) would crash the entire task.

**Example crash:**
```python
fp = {"navigator": None}
val = fp.get('navigator', {}).get('model', '')  # AttributeError!
```

**Fix:** Added two helper functions:
- `_safe_fp_get(fp, key, default)` — 1-level safe access
- `_fp_get2(fp, outer_key, inner_key, default)` — 2-level safe access (most common pattern)

Both helpers return `default` (or None) if any level of the access is
invalid (None, missing, non-dict, or wrong type). The 26 unsafe
`fp.get('X', {}).get('Y', default)` patterns in the f-string were
replaced with `_fp_get2(fp, 'X', 'Y', default)`.

### Issue #2: `model: "None"` string in f-string when `model` is null

**Severity:** HIGH
**Location:** hardened_orchestrator.py line 1864 (and others)

When the outer level is a dict but the inner value is null, the original
code returned the null. The f-string `f"{None}"` produces the literal
string `"None"`. This would be inserted into the JS code as
`model: "None"` — a detectable string that doesn't match any real device
model.

**Fix:** The `_fp_get2` helper returns the default when the inner value
is None, so `f"{_fp_get2(fp, 'navigator', 'model', '')}"` produces
`model: ""` (empty string) instead of `model: "None"`.

### Issue #3: `int(None)` crash on `maxTouchPoints: null`

**Severity:** HIGH
**Location:** hardened_orchestrator.py line 1871

The f-string contains `int(_fp_get2(fp, 'navigator', 'maxTouchPoints', 0))`.
If `_fp_get2` returns None (e.g., when `navigator: null`), `int(None)` raises
`TypeError: int() argument must be a string, a bytes-like object or a real
number, not 'NoneType'`.

**Fix:** The `_fp_get2` helper now returns the default (0) when the value
is missing or null, so `int(0)` works correctly.

### Issue #4: `stable_hash(None)` crash

**Severity:** MEDIUM
**Location:** hardened_orchestrator.py line 500

The `stable_hash(s)` function calls `s.encode('utf-8')` which raises
`AttributeError` if `s` is None. The current call sites always pass
strings, but defensive programming prevents future crashes.

**Fix:** Added a None check at the start of `stable_hash`. If `s` is None
or not a string, return a stable hash of the empty string.

### Issue #5: `notification_session_count` could be `None`

**Severity:** LOW
**Location:** hardened_orchestrator.py line 1870

`persona_digest.get("notification_session_count", 0)` returns None if
the key exists with value None. The f-string would render `null` (JSON),
but the JS hardener expects a number. The `or 0` fallback ensures
a valid number.

**Fix:** Changed to `persona_digest.get("notification_session_count", 0) or 0`.

### Issue #6: `canvas_noise_session_id` could be `None`

**Severity:** LOW
**Location:** hardened_orchestrator.py line 1868

Same pattern as Issue #5. Changed to `or ''` fallback.

### Issue #7: `fp.get('window_name', '')` could be `None`

**Severity:** LOW
**Location:** hardened_orchestrator.py line 1982

`fp.get('window_name', '')` returns None if the key exists with None.
The f-string inserts `null` into JS, which is then used in `if (name)`.
This actually works correctly (falsy check), but for consistency we
changed to `or ''` fallback.

**Fix:** Changed to `fp.get('window_name', '') or ''`.

## Results

**Before fix:** 49 / 51 tests pass (2 critical crashes on null fingerprints)
**After fix:** 51 / 51 tests pass

**Total: 85 + 51 = 136 tests passing, 0 failures.**

## Final Validation

```bash
$ python3 -m py_compile browser-runtime/hardened_orchestrator.py  # OK
$ for f in browser-runtime/*.py; do python3 -m py_compile "$f"; done
Python: 18/18 OK
$ node --check browser-runtime/prototype_hardener.js
JS: OK
$ for f in browser-runtime/*.sh; do bash -n "$f"; done
Shell: 2/2 OK

$ node /tmp/test_after_fixes.js   # Pass 9 hardener tests
Results: 28 passed, 0 failed

$ node /tmp/pass10_audit.js        # Pass 10 hardener tests
Total: 57 | Pass: 57 | Fail: 0

$ python3 /tmp/test_orchestrator_runtime.py  # Pass 11 orchestrator tests
SUMMARY: 85 tests, 85 passed, 0 failed

$ python3 /tmp/test_pass11_fixes.py   # Pass 11 fix verification
SUMMARY: 51 tests, 51 passed, 0 failed
```

## Summary of All Pass 11 Fixes

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `fp.get('X', {}).get('Y')` crashes on `X: null` | CRITICAL | FIXED |
| 2 | `model: "None"` string in f-string | HIGH | FIXED |
| 3 | `int(None)` crash on null int field | HIGH | FIXED |
| 4 | `stable_hash(None)` crash | MEDIUM | FIXED |
| 5 | `notification_session_count` could be None | LOW | FIXED |
| 6 | `canvas_noise_session_id` could be None | LOW | FIXED |
| 7 | `window_name` could be None | LOW | FIXED |

## Files Modified

- `/home/user/project/browser-runtime/hardened_orchestrator.py`
  - Added `_safe_fp_get()` and `_fp_get2()` helper functions (lines 1058-1130)
  - Replaced 26 unsafe `fp.get('X', {}).get('Y', default)` patterns with `_fp_get2()`
  - Made `stable_hash()` defensive against None input
  - Added `or ''` / `or 0` fallbacks for f-string substitutions

## Test Files

- `/tmp/test_pass11_fixes.py` — 51 tests covering all edge cases
- `/tmp/test_orchestrator_runtime.py` — 85 tests of pure functions (from earlier)

## Total Audit Tally (11 passes)

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
| **Deep audit Pass 11 (orchestrator JSON null safety)** | **7** | **7** |
| **TOTAL** | **218** | **218** |

**Total runtime tests: 28 + 57 + 85 + 51 = 221 tests, 0 failures.**
