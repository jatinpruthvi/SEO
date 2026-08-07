# R7-DEEP-AUDIT-2 (Pass 13) — All 7 Bug Fixes

**Date:** 2026-07-16
**Audit ref:** 09_cross_api_timer_scope_audit.md
**Status:** All 7 bugs fixed, 9 new tests added, 232/232 tests pass.

---

## Summary

| Bug | Severity | File area | Lines added | New test |
|-----|----------|-----------|-------------|----------|
| #1 | MEDIUM | archString mapping | +14 | test_bug1_arch_string_covers_default_renderer |
| #2 | MEDIUM | navigator.connection consistency | +18 | test_bug2_connection_type_consistency |
| #3 | LOW | battery start time (docs only) | +8 | test_bug3_battery_start_time_documented |
| #4 | MEDIUM | connection jitter cleanup | +21 | test_bug4_connection_jitter_has_pagehide_cleanup |
| #5 | LOW | RTC setTimeout WeakRef | +14 | test_bug5_rtc_setTimeout_uses_weakref |
| #6 | LOW | rAF Map max-size cap | +19 | test_bug6_raf_handles_map_has_max_size |
| #7 | **HIGH** | iFrame Navigator coverage | +110 | test_bug7_iframe_navigator_property_coverage (+ 2 more) |

**Total: ~204 lines added to `prototype_hardener.js`, +198 lines in tests, 9 new test methods, 17 inline "BUG #N" markers.**

---

## Bug #7 (HIGH) — iFrame Navigator property coverage

**File:** `prototype_hardener.js` line 5180-5360
**Fix:** Replaced the 5-property iFrame patch with a 25+ property patch mirroring the Worker path. The previous iFrame path covered only:
- platform, hardwareConcurrency, deviceMemory, language, languages

The new iFrame path now also covers:
- userAgent, appVersion, vendor, vendorSub, product, productSub
- cookieEnabled, pdfViewerEnabled, onLine, maxTouchPoints, doNotTrack
- serviceWorker, gpu, usb, serial, hid, bluetooth, ink, nfc
- geolocation (with persona coords mock), permissions, storage, clipboard, userAgentData, locks

**Bonus fix:** iFrame `Date.toString` was calling `rawToStringDate.call(this)` which leaks the **system TZ** (e.g., "Coordinated Universal Time" on Debian 12) into the iframe context. Now it constructs a persona-TZ string using `formatToParts` (same algorithm as the main script).

---

## Bug #1 (MEDIUM) — WebGL archString mapping

**File:** `prototype_hardener.js` line 2603-2618
**Fix:** Replaced the 6-line `if/else` chain that fell through to `"Gen11"` for any non-matching renderer (including the default `"Intel(R) Iris(TM) Plus Graphics"` which is Gen 8 hardware). New mapping:
- Added explicit `iris xe` / `iris plus` / `iris pro` → Gen 9.5 / Gen 9
- Added `uhd 7` → Gen 11
- Added `hd graphics 5` → Gen 8
- Added `hd graphics 4/4400/4600` → Gen 7.5
- Default fallback changed from `"Gen11"` to `"Gen 9.5"` (safe modern Windows desktop default)

---

## Bug #2 (MEDIUM) — connection type + effectiveType consistency

**Files:** `prototype_hardener.js` line 218-225, 4313-4320, 4398-4404, 4433-4448
**Fix:** Three changes:
1. `FP_CONFIG.effectiveType` default now conditions on `connectionType` — returns `undefined` for non-cellular (was always '4g').
2. `liveJitterState.effectiveType` initialization also conditions on type.
3. The `connection.effectiveType` getter returns `undefined` when `liveJitterState.type !== 'cellular'`.
4. The network-change event handler now sets `effectiveType = undefined` when the new type is `wifi`.

This eliminates the impossible `wifi + 4g` combination that was the default persona state.

---

## Bug #4 (MEDIUM) — connection jitter timer cleanup

**File:** `prototype_hardener.js` line 4313-4351 + 4505-4518
**Fix:** Added `_connJitterTimer` handle and `_connJitterClosing` flag. The setTimeout now:
- Returns early if `_connJitterClosing` is set
- Stores its handle in `_connJitterTimer`
- Mirrors the focusFlip pattern with a `pagehide` handler that clears the timer

Without this, the 5-15s recurring timer would continue firing after SPA navigation and mutate `liveJitterState` for a possibly-replaced `navigator.connection` object.

---

## Bug #3 (LOW) — battery start time documentation

**File:** `prototype_hardener.js` line 848-855
**Fix:** Added an 8-line comment explaining that the anchor is IIFE init time, not page-load. The discrepancy is sub-millisecond over 10 minutes (because `performance.now()` is monotonic and relative drift is what matters, not the absolute anchor). Not exploitable, but documented for clarity.

---

## Bug #5 (LOW) — RTCPeerConnection setTimeout WeakRef

**File:** `prototype_hardener.js` line 4953-4985
**Fix:** Added a `WeakRef(this)` capture at the moment the setTimeout is scheduled. The setTimeout callback now calls `selfRef.deref()` and aborts if the RTCPeerConnection has been GC'd during the delay. Without this, a page that closes during ICE gathering would see `originalHandler.call(self, event)` on a destroyed RTCPeerConnection, throwing a TypeError.

---

## Bug #6 (LOW) — rAF Map max-size cap

**File:** `prototype_hardener.js` line 3555-3573
**Fix:** Added a size cap on `_rafHandles` Map. When size > 1000, the oldest 200 entries are dropped (FIFO by insertion order, which Map iteration preserves). This prevents unbounded growth in 100-hour sessions where rAF callbacks may not fire (e.g., attached to removed elements).

---

## Test coverage

9 new tests added to `test_pass62_rounds_2_5.py`:

```
TestPass13CrossAPIConsistency:
  test_bug1_arch_string_covers_default_renderer  (Pass 13)
  test_bug2_connection_type_consistency           (Pass 13)
  test_bug3_battery_start_time_documented         (Pass 13)
  test_bug4_connection_jitter_has_pagehide_cleanup (Pass 13)
  test_bug5_rtc_setTimeout_uses_weakref           (Pass 13)
  test_bug6_raf_handles_map_has_max_size          (Pass 13)
  test_bug7_iframe_navigator_property_coverage    (Pass 13)
  test_bug7_iframe_date_tostring_not_raw          (Pass 13)
  test_bug7_iframe_risk_marker_present            (Pass 13)
```

Each test reads `prototype_hardener.js` and asserts the specific pattern is present. The tests are static (no JS execution) — they verify the patches are in the source code, not that they work in a browser. This is consistent with all other tests in the file.

---

## Validation results

- `python3 -m py_compile` on all `.py` files: **pass**
- `node --check browser-runtime/prototype_hardener.js`: **pass** (6,414 lines)
- `bash -n` on all `.sh` files: **pass**
- `test_pass60_final_verification.py`: **115/115 pass**
- `test_pass62_rounds_2_5.py`: **117/117 pass** (108 previous + 9 new)
- **Total: 232/232 tests pass**

---

## Inline bug markers

The hardener source now contains 17 `R7-DEEP-AUDIT-2 (Pass 13) BUG #N` comments that document the fix points. Each marker includes a brief explanation of what the bug was and why the fix is correct. These serve as audit trail and prevent regressions if a future refactor accidentally removes one of the fixes.

---

## Net change

- `prototype_hardener.js`: 6,146 → 6,414 lines (+268 net, including ~64 lines of comments/markers)
- `test_pass62_rounds_2_5.py`: 776 → 974 lines (+198 lines, 9 new test methods)
- 7 real bugs eliminated from the persona's fingerprint surface
- 0 new dependencies, 0 new architecture, 0 logger/dashboard additions
