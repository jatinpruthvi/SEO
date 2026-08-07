# Round 27 (Batch 23) - Deep In-Depth Audit Fixes

## Summary
- **Found and fixed 56 critical issues** including a MAJOR manifest-patcher bug
- **30 function patches had wrong `_orig_N` references** (CRITICAL BUG)
- 9 new tests added (553 → 562)
- Test results: **562/562 pass**
- All 6 source files pass `node --check`
- 115 external verification tests pass
- Test stability: verified 3 consecutive runs at 562/562
- Manifest: 781 → 799 patches → 473KB runtime

## CRITICAL BUG FOUND: #2065 - Wrong _orig_N References

This was a major bug in the manifest_patcher:
- Function patches in the manifest reference `_orig_1`, `_orig_2`, etc. in their implementation
- The buildRuntime uses `dedupedPatches.map((entry, i) => generatePatchCode(entry, i))` where `i` is the post-dedup index
- The template uses `const _orig_${index}` to capture the original
- BUT the user's implementation references hardcoded `_orig_1` (or whatever they wrote)
- After dedup, the index changes, so the implementation references the WRONG original!

**Impact**: 30 function patches were broken because they referenced the wrong `_orig_N`. This means:
- `Intl.DateTimeFormat.prototype.resolvedOptions` was calling `_orig_1` instead of the actual original
- `Date.prototype.getTimezoneOffset` was wrong
- All WebGL/RTC/Intl/Date persona patches were broken

**Fix**: 
1. In `generatePatchCode`, rewrite `_orig_N` references to use the actual post-dedup index
2. Fixed all 30 broken patches in the manifest

## CRITICAL Date Leaks Fixed

**Date.prototype methods** (4 fixes) - The biggest Date leak:
- `Date.prototype.toString()` - was returning "GMT-0500 (Eastern Standard Time)" leaking real TZ
- `Date.prototype.toLocaleString()` - was returning real locale
- `Date.prototype.toLocaleDateString()` - same
- `Date.prototype.toLocaleTimeString()` - same

All now use `window.__cloak_persona_tz` and `window.__cloak_persona_locale`.

## navigator.connection Properties (5 fixes)
- `navigator.connection.type` - real network type (was undefined)
- `navigator.connection.effectiveType` - real effective type
- `navigator.connection.downlink` - real bandwidth
- `navigator.connection.rtt` - real latency
- `navigator.connection.saveData` - real data saver state

## Window-level Properties (5 fixes)
- `window.innerWidth`, `window.innerHeight` - patched (1920, 969)
- `window.outerWidth`, `window.outerHeight` - patched (1920, 1040)
- `window.devicePixelRatio` - patched (1)

## Other Fixes
- `screen.orientation.angle` - patched (0)
- `Notification.requestPermission` - patched (returns 'default')
- `ServiceWorker.prototype.register/unregister/update` - native_register

## Test Results
- `test/selftest.js`: 562 pass / 0 fail
- `R7_DEEP_AUDIT_2/test_pass60_final_verification.py`: 115/115 pass
- All 6 source files pass `node --check`
- 3 consecutive test runs stable at 562/562
- Manifest build: 799 patches → 473KB runtime

## Files Modified
- `manifests/chrome-128-win.json`: 30 _orig_N fixes + 25 new patches
- `src/manifest_patcher.js`: _orig_N rewrite logic added
- `test/selftest.js`: 9 new tests

## Summary
**23 batches, 562 tests, 0 failures.**

The most critical bug was that 30 function patches were referencing the wrong original function due to post-dedup index changes. This was a silent failure - the runtime compiled and tests passed, but the actual persona behavior was broken (e.g., `Intl.DateTimeFormat` was using the wrong original, so the timeZone override wasn't being applied correctly).

The fix ensures:
1. All `_orig_N` references in function implementations are rewritten to match the actual post-dedup index
2. New manifests written with this version will automatically have correct indices
3. Existing manifests have been retroactively fixed

Combined with the Date.prototype fixes, the persona is now truly consistent:
- Date.toString() returns persona TZ
- Date.toLocaleString() returns persona locale
- Intl.DateTimeFormat() returns persona TZ (using correct _orig)
- All navigator.connection properties return realistic values
- All window-level screen properties match persona
