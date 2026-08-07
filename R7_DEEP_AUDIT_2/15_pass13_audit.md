# R7-DEEP-AUDIT-2 Pass 13: Session Serializer & Proxy URL Edge Cases

**Date:** 2026-07-14
**Auditor:** Pass 13 (continuation of Pass 12)
**Scope:** Edge cases in session_serializer.py, hardened_orchestrator.py proxy URL handling, and additional defensive checks

## Methodology

Pass 12 verified multi-persona behavior. Pass 13 focuses on:
1. **Session serializer edge cases** — decrypt/encrypt with None/empty inputs
2. **Proxy URL handling** — IPv6 brackets, URL-encoding, edge cases
3. **Defensive parsing** — robust error codes for the orchestrator

## Issues Found

### Issue #1: `decrypt_payload(None)` raises confusing `AttributeError` (CRITICAL)

**Severity:** CRITICAL
**Location:** `session_serializer.py` line 51 (the `decrypt_payload` method)
**File:** `/home/user/project/browser-runtime/session_serializer.py`

The previous code called `self.cipher.decrypt(encrypted_payload.encode())`
which raises `AttributeError: 'NoneType' object has no attribute 'encode'`
when `encrypted_payload` is None. While the orchestrator's `except Exception`
catches this, the error code was misleading — it would say "corrupted"
when the data was actually just None.

**Fix:** Added a defensive check at the start of `decrypt_payload`:
```python
if not encrypted_payload or not isinstance(encrypted_payload, str):
    raise ValueError("decrypt_payload: payload is None or empty")
```

**Verification:** All None/empty/invalid inputs now raise `ValueError` with
a clear message. The orchestrator can distinguish "no data" from "corrupted".

### Issue #2: Orchestrator decryption error code logic improved

**Severity:** MEDIUM
**Location:** `hardened_orchestrator.py` line ~2066

The previous code had:
```python
code = "auth_failed" if "MAC" in str(dec_err) or "signature" in str(dec_err).lower() else "corrupted"
```

This only distinguished auth failures from generic errors. With the new
`ValueError` from Issue #1, we need a separate code for "no data".

**Fix:** Updated the error code logic:
```python
if "payload is None or empty" in err_msg:
    code = "empty_payload"
elif "MAC" in err_msg or "signature" in err_msg.lower() or "Fernet" in err_msg:
    code = "auth_failed"
else:
    code = "corrupted"
```

**Verification:** Test verifies all three error codes are produced correctly.

### Issue #3: `_redact_proxy_credentials` strips IPv6 brackets (HIGH)

**Severity:** HIGH
**Location:** `hardened_orchestrator.py` `_redact_proxy_credentials` function

The previous code used `parsed.hostname` which strips brackets for IPv6.
For `socks5://user:pass@[::1]:1080`:
- Result: `socks5://user:***@::1:1080` (no brackets — unparseable)
- Expected: `socks5://user:***@[::1]:1080` (with brackets — reparseable)

The redacted URL was unparseable as a proxy URL, breaking the redaction
log entries (and the redaction function was supposed to produce safe,
re-parseable URLs).

**Fix:** Reconstruct the host:port with IPv6 brackets from the netloc
when brackets are present.

**Verification:** Tests verify IPv6 brackets are preserved in both
`redact` and `parse` outputs.

### Issue #4: `_redact_proxy_credentials` unquotes URL-encoded chars (HIGH)

**Severity:** HIGH
**Location:** `hardened_orchestrator.py` `_redact_proxy_credentials` function

The previous code called `urllib.parse.unquote()` on the username, which
would decode `%40` to `@` and break the URL structure. For
`socks5://user%40:p%40ss@host:1080`:
- Result: `socks5://user@:***@host:1080` (the `@` in `user@` makes it look like the username is just `user`)
- Expected: `socks5://user%40:***@host:1080` (preserved encoded form)

**Fix:** Don't unquote in the redaction function. The redaction is for
LOGGING — the original URL is still valid and the password is the only
credential redacted.

Note: `parse_proxy_url` still DOES unquote (because Playwright expects
raw strings, not URL-encoded). The split is:
- `parse_proxy_url`: unquote credentials (Playwright compatibility)
- `_redact_proxy_credentials`: don't unquote (logging safety)

**Verification:** Tests verify both behaviors.

### Issue #5: `parse_proxy_url` strips IPv6 brackets (HIGH)

**Severity:** HIGH
**Location:** `hardened_orchestrator.py` `parse_proxy_url` function

The previous code used `parsed.hostname` which strips brackets for IPv6.
For `socks5://[::1]:1080`:
- Result: `{"server": "::1:1080"}` (no brackets — unparseable)
- Expected: `{"server": "[::1]:1080"}` (with brackets — Playwright-compatible)

**Fix:** Reconstruct the host from the netloc when IPv6 brackets are
present.

**Verification:** Tests verify IPv6 brackets are preserved in the server field.

## Results

**Before fix:** Multiple IPv6 and URL-encoding edge cases produced unparseable URLs
**After fix:** All 25 Pass 13 tests pass

**Total runtime tests: 28 + 57 + 24 + 85 + 51 + 25 = 270 tests, 0 failures.**

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

$ node /tmp/pass12_audit.js        # Pass 12
Total: 24 | Pass: 24 | Fail: 0

$ python3 /tmp/test_orchestrator_runtime.py  # Pass 11 base
SUMMARY: 85 tests, 85 passed, 0 failed

$ python3 /tmp/test_pass11_fixes.py   # Pass 11 fix verification
SUMMARY: 51 tests, 51 passed, 0 failed

$ python3 /tmp/test_pass13.py         # Pass 13 (NEW)
SUMMARY: 25 tests, 25 passed, 0 failed
```

## Total Audit Tally (13 passes)

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
| Deep audit Pass 12 (multi-persona determinism) | 4 | 4 |
| **Deep audit Pass 13 (session serializer + proxy URL)** | **5** | **5** |
| **TOTAL** | **227** | **227** |

**Total runtime tests: 270 tests, 0 failures.**

## Files Modified

- `/home/user/project/browser-runtime/session_serializer.py`
  - `decrypt_payload`: Defensive check for None/empty input, raise `ValueError`
  - Added comment explaining the design decision

- `/home/user/project/browser-runtime/hardened_orchestrator.py`
  - `_redact_proxy_credentials`: Preserve IPv6 brackets, don't unquote URL-encoded chars
  - `parse_proxy_url`: Preserve IPv6 brackets in server field
  - Decryption error code logic: Added `empty_payload` code for ValueError

## Test Files

- `/home/user/R7_DEEP_AUDIT_2/test_pass13.py` — 25 tests for session serializer and proxy URL edge cases
- (All other test files from previous passes remain in the audit directory)

## Production Impact

The IPv6 fixes are particularly important for production environments
where proxy servers may be on IPv6 addresses. Without the bracket
preservation, the orchestrator would produce unparseable proxy URLs
that would fail when passed to Playwright, causing task failures.
