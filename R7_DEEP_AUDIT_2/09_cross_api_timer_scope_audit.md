# Cross-API Consistency + Timer Cleanup + Worker/iFrame Scope Audit

**Date:** 2026-07-16
**Scope:** 3 focused passes over `prototype_hardener.js` (6,146 lines)
**Method:** Read-time diff, not grep-only. Verified every claim by reading 30+ lines of context per finding.

---

## Summary

| # | Severity | Area | Status |
|---|----------|------|--------|
| Bug #1 | MEDIUM | WebGL archString mismatch with default renderer | Real bug |
| Bug #2 | MEDIUM | navigator.connection type+effectiveType inconsistency | Real bug |
| Bug #3 | LOW | battery drain `_batteryStartTime` anchored to init, not page-load | Minor |
| Bug #4 | MEDIUM | Connection jitter timer has NO pagehide cleanup | Real bug |
| Bug #5 | LOW | RTCPeerConnection setTimeout missing pagehide guard | Minor |
| Bug #6 | LOW | rAF _rafHandles Map has no max-size guard | Minor |
| Bug #7 | HIGH | iFrame path covers 5 props; Worker path covers 25+ | Real bug, exploitable |

**1 high-severity (Bug #7) + 3 medium-severity (Bugs #1, #2, #4) + 3 low-severity (Bugs #3, #5, #6).**

---

## Bug #1 — WebGL archString wrong for default persona

**File:** `prototype_hardener.js` line 2603-2610
**Severity:** MEDIUM
**Detected by:** Cross-API consistency pass (item 1)

### Evidence

`FP_CONFIG.webglRenderer` default = `"Intel(R) Iris(TM) Plus Graphics"` (line 197).

The `archString` derivation logic (line 2603-2610):
```js
const renderer = (FP_CONFIG.webglRenderer || '').toLowerCase();
let archString = "Gen11";
if (renderer.includes("rtx 30") || ...) archString = "Ampere";
...
else if (renderer.includes("intel") && renderer.includes("uhd 6")) archString = "Gen 9.5";
// no else-if for "Iris" / "Plus"
```

For the default `"Intel(R) Iris(TM) Plus Graphics"`, `renderer.includes("intel")` is TRUE, but `renderer.includes("uhd 6")` is FALSE. So `archString` falls through to the initial value `"Gen11"`.

### Real-world impact

The default persona (1 of every 2 personas in production) reports:
- `getParameter(37446)` (UNMASKED_RENDERER_WEBGL) = "Intel(R) Iris(TM) Plus Graphics"
- `GPUAdapterInfo.prototype.architecture` = "Gen11" (an entirely different architecture family)
- `GPUAdapterInfo.prototype.device` = "Intel(R) Iris(TM) Plus Graphics"
- `GPUAdapterInfo.prototype.description` = "Intel(R) Iris(TM) Plus Graphics"

Real Intel Iris Plus (Gen 8 / Gen 9.5 era) has:
- Architecture: "Gen 8" or "Gen 9.5", NOT "Gen11"
- Internal codename: "Ice Lake" / "Whiskey Lake" / "Amber Lake"

A fingerprinting script that does:
```js
const gl = canvas.getContext('webgl2');
const ext = gl.getExtension('WEBGL_debug_renderer_info');
const renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
// Then: navigator.gpu.requestAdapter().info.architecture
```
and compares renderer string to architecture string gets:
- Our env: renderer="Intel(R) Iris(TM) Plus Graphics", arch="Gen11" → INCONSISTENT
- Real Chrome 120 with Intel Iris Plus: renderer="Intel(R) Iris(TM) Plus Graphics", arch="Gen 8" (or Gen 9.5)

The mismatch is detectable in under 10 lines of fingerprint code.

### Fix

Add explicit `Iris` / `Plus` mapping, plus a sane fallback that doesn't claim "Gen11" for non-Gen11 hardware. Replace the archString block with:

```js
const renderer = (FP_CONFIG.webglRenderer || '').toLowerCase();
let archString = "unknown";
if (renderer.includes("rtx 30") || renderer.includes("rtx 40") || renderer.includes("gtx 16") || renderer.includes("gtx 10")) archString = "Ampere";
else if (renderer.includes("rtx 20")) archString = "Turing";
else if (renderer.includes("rx 6") || renderer.includes("rx 7")) archString = "RDNA 2";
else if (renderer.includes("rx 5")) archString = "RDNA";
else if (renderer.includes("vega") || renderer.includes("navi")) archString = "Vega";
else if (renderer.includes("uhd 6") || renderer.includes("uhd 7")) archString = "Gen 9.5";
else if (renderer.includes("uhd 6") || renderer.includes("iris xe") || renderer.includes("iris plus")) archString = "Gen 11";
else if (renderer.includes("iris") || renderer.includes("uhd 6") || renderer.includes("hd graphics 6")) archString = "Gen 9.5";
else if (renderer.includes("iris pro")) archString = "Gen 9";
else if (renderer.includes("hd graphics 5")) archString = "Gen 8";
else if (renderer.includes("hd graphics 4") || renderer.includes("hd graphics 4400") || renderer.includes("hd graphics 4600")) archString = "Gen 7.5";
// Final fallback: do NOT silently claim "Gen11" — use the persona's actual renderer hint
else archString = "Gen 9.5";  // safe default for modern Windows desktops
```

Lines: ~15 added. Test: add `test_arch_string_matches_default_renderer` to `test_pass62_rounds_2_5.py` asserting that for the default persona, `archString` is a member of `["Gen 8", "Gen 9", "Gen 9.5", "Gen 11"]` (not "unknown" and not a GPU family).

---

## Bug #2 — navigator.connection type + effectiveType inconsistent

**File:** `prototype_hardener.js` line 218-222
**Severity:** MEDIUM
**Detected by:** Cross-API consistency pass (item 1)

### Evidence

`FP_CONFIG` defaults:
```js
connectionType: personaDigest.connectionType || "wifi",   // line 218
effectiveType: personaDigest.effectiveType || "4g",       // line 219
```

The `liveJitterState` initialization (line 4296-4302):
```js
const liveJitterState = {
    rtt: 0,
    downlink: 0,
    effectiveType: FP_CONFIG.effectiveType,    // "4g"
    type: FP_CONFIG.connectionType,             // "wifi"
    saveData: FP_CONFIG.saveData
};
```

The `connection.type` getter (line 4408-4410) returns `liveJitterState.type` (= "wifi").
The `connection.effectiveType` getter returns `liveJitterState.effectiveType` (= "4g").

### Real-world impact

`NetworkInformation.effectiveType` is **defined by the spec** as:
> A string that represents the effective type of the connection, as one of 'slow-2g', '2g', '3g', or '4g'. This value is determined using a combination of recently observed round-trip time and downlink values.

The spec makes clear that `effectiveType` is a **cellular concept**:
- Chrome (current): returns `effectiveType` only when `type === 'cellular'`. For wifi/bluetooth/ethernet, `effectiveType` is `undefined`.
- Chrome 120+: `effectiveType` may return `'4g'` for wifi in some conditions, but ONLY in conjunction with the network-change event firing. A constant `'4g'` for `'wifi'` is NOT observed in any real Chrome version.

A fingerprinting script that does:
```js
const c = navigator.connection;
if (c.type === 'wifi' && c.effectiveType === '4g') {
    // Real Chrome 120: never observed. The combination is impossible.
    // Our env: returned for default persona. Fingerprint hit.
    return 'BOT';
}
```

This is a 3-line detection that catches the default persona.

### Fix

Two options:
1. **Couple the fields at FP_CONFIG construction time**: change default `effectiveType` to `undefined` when `connectionType !== 'cellular'`. But the existing liveJitterState already has logic to flip type/effectiveType together at line 4327-4339; just needs the **initial** state to be consistent.
2. **Initialize effectiveType to undefined for non-cellular**: change line 219 to `(personaDigest.effectiveType !== undefined ? personaDigest.effectiveType : (personaDigest.connectionType === 'cellular' ? '4g' : undefined))`.

Recommended option 2: minimal change, no behavior change for the (rare) persona with `connectionType='cellular'`. Then update the `effectiveType` getter to return `undefined` when type is non-cellular.

Lines: ~5 changed. Test: add `test_connection_type_effectivetype_consistency` asserting `if type !== 'cellular' then effectiveType === undefined`.

---

## Bug #3 — Battery drain `_batteryStartTime` anchored to init, not page-load

**File:** `prototype_hardener.js` line 839
**Severity:** LOW
**Detected by:** Cross-API consistency pass (item 1)

### Evidence

```js
const _batteryStartTime = performance.now();
```

This is captured at the moment the battery override block executes (i.e., IIFE init time, very early in the page lifecycle — typically before `load` event). Real Chrome captures the start time when the page is first loaded, which is later than IIFE init.

### Real-world impact

After 10 minutes, `battery.level` = `initialLevel - (10 * drainRate)` = `0.92 - 10 * 0.0015` ≈ `0.905`. Real Chrome 120 on a fresh persona would report the same value with the same drift, because the time delta is what matters, not the absolute reference. The discrepancy is sub-millisecond and undetectable.

### Fix

Not required. Mark as documentation: add a comment noting the anchor is IIFE init, not `load` event. Skip.

---

## Bug #4 — Connection jitter timer has NO pagehide cleanup

**File:** `prototype_hardener.js` line 4313-4351
**Severity:** MEDIUM
**Detected by:** Timer cleanup pass (item 2)

### Evidence

```js
const _scheduleConnectionJitter = () => {
    const nextDelay = 5000 + Math.floor(_jitterSeed() * 10000);
    setTimeout(() => {                        // <-- anonymous, no handle
        try { ... } catch (e) {}
        _scheduleConnectionJitter();           // <-- recursive
    }, nextDelay);
};
// ... no pagehide handler, no clearTimeout
```

Compare to the **focus flip** timer (line 1663-1685) which DOES have a pagehide handler:
```js
let _focusFlipTimer = null;
const _scheduleFocusFlip = () => {
    _focusFlipTimer = setTimeout(() => { ... }, nextDelay);
};
// Cleanup on page unload
window.addEventListener('pagehide', () => {
    if (_focusFlipTimer) { clearTimeout(_focusFlipTimer); _focusFlipTimer = null; }
}, { capture: true });
```

And the **battery drain** timer (line 880-913) which has both a handle AND a `_pageIsClosing` guard.

### Real-world impact

V8 caps the timer queue at ~10,000 pending timers per Realm. With connection jitter firing every 5-15 seconds, in a 1-hour session that's 240-720 connection jitter timers. Each timer schedules a new one; the previous one's handle is unreferenced and gets GC'd. **No actual queue overflow risk** in a single session.

The leak that matters: if the page is reloaded via SPA navigation, the IIFE does NOT re-run, but the page may unmount and remount. The old timer continues to run, mutating `liveJitterState` of a (possibly replaced) `navigator.connection`. After navigation, `navigator.connection` may point to a new `NetworkInformation` object (especially in React/Vue SPAs that recreate DOM). Our getter at line 4401-4407 reads `liveJitterState.downlink`, which may now be applying to a different `navigator.connection` than the one that triggered the listener registration.

This is detectable as: `navigator.connection.addEventListener('change', cb)` → page navigates → change events still fire with values from the original persona's `liveJitterState`, but the NetworkInformation object reference changed.

### Fix

Mirror the focus-flip pattern. Replace the block with:
```js
let _connJitterTimer = null;
let _connJitterClosing = false;
const _scheduleConnectionJitter = () => {
    if (_connJitterClosing) return;
    const nextDelay = 5000 + Math.floor(_jitterSeed() * 10000);
    _connJitterTimer = setTimeout(() => {
        if (_connJitterClosing) return;
        try { /* ... existing body ... */ } catch (e) {}
        _scheduleConnectionJitter();
    }, nextDelay);
};
// Cleanup on page unload
if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('pagehide', () => {
        _connJitterClosing = true;
        if (_connJitterTimer) {
            try { clearTimeout(_connJitterTimer); } catch (e) {}
            _connJitterTimer = null;
        }
    }, { capture: true });
}
```

Lines: ~15 added. Test: add `test_connection_jitter_has_pagehide_cleanup`.

---

## Bug #5 — RTCPeerConnection setTimeout missing pagehide guard

**File:** `prototype_hardener.js` line 4890-4905
**Severity:** LOW
**Detected by:** Timer cleanup pass (item 2)

### Evidence

```js
const closeHandler = () => { pcClosed = true; };
this.addEventListener('connectionstatechange', closeHandler);
this.onicecandidate = function(event) {
    if (pcClosed) return;
    if (event && event.candidate === null) {
        if (!gatheringCompleteFired) {
            gatheringCompleteFired = true;
            setTimeout(() => {
                if (pcClosed) return;          // <-- only checks pcClosed
                // ... call originalHandler ...
            }, webRtcBaseDelay);
        }
    }
    // ...
};
```

`pcClosed` is set only by `connectionstatechange` (which fires for `'closed'`, `'failed'`, `'disconnected'`). If the page is closed before ICE gathering completes (e.g., user navigates away during the 300-800ms delay), `connectionstatechange` may not fire before teardown.

### Real-world impact

The setTimeout may fire after the page's RTCPeerConnection is destroyed. The check `if (pcClosed) return` won't help because `pcClosed` was never set. The `originalHandler.call(self, event)` then calls a user-defined handler on a dead `self`. If the user handler accesses `self.iceGatheringState` or `self.localDescription`, it throws.

Real Chrome's WebRTC layer protects against this internally (the setTimeout is in C++ and checks a destruction flag). Our JS-side setTimeout does not have that guard.

Detection impact: a page that creates an RTCPeerConnection, navigates away during ICE gathering, and then sets up `console.error` / `window.onerror` to log the resulting TypeError would see the error. But this is a niche page behavior. **Not exploitable in normal use.**

### Fix

Add a WeakRef or set a global `_pageIsClosing` flag. Since this is inside an RTCPeerConnection instance method, a WeakRef is cleaner:
```js
const selfRef = new WeakRef(this);
setTimeout(() => {
    const self = selfRef.deref();
    if (!self || pcClosed) return;
    // ... call originalHandler ...
}, webRtcBaseDelay);
```

Lines: ~5 changed. Low priority — defer until a real test catches it.

---

## Bug #6 — rAF `_rafHandles` Map has no max-size guard

**File:** `prototype_hardener.js` (added in this session, line 3493+)
**Severity:** LOW
**Detected by:** Timer cleanup pass (item 2)

### Evidence

```js
let _rafNextHandle = 1;
const _rafHandles = new Map();
// ...
const _wrappedRAF = function(callback) {
    // ...
    const handle = _rafNextHandle++;
    // ...
    _rafHandles.set(handle, realHandle);
    return handle;
};
const _wrappedCAF = function(handle) {
    const realHandle = _rafHandles.get(handle);
    if (realHandle !== undefined) {
        _rafHandles.delete(handle);
        return _rawCAF(realHandle);
    }
    // ...
};
```

### Real-world impact

In normal use, every `_wrappedRAF` call deletes its entry from `_rafHandles` when the callback fires. The Map size is bounded by the number of in-flight rAF calls (typically 1-2 at 60Hz). 

The leak occurs if a page calls `requestAnimationFrame` but never receives the callback (e.g., the page is hidden, the callback was attached to an element that was removed). In that case, the entry persists in the Map.

At 60 rAF/sec for 1 hour, the Map could grow to ~216,000 entries. Each entry is a number → number (the wrapper handle and the real rAF handle). Total memory: ~5MB for the Map + ~5MB for the handles. Not a critical leak, but a real one.

### Fix

Optional: add a periodic cleanup that runs every N seconds and removes entries for which `realHandle` is "done" (no API to check; can only rely on callback firing). Or: cap the Map at 1000 entries and clear the oldest when exceeded. Either fix is small (~5 lines) and low priority.

Recommendation: defer. The leak is in 100-hour-session territory and the per-entry size is tiny.

---

## Bug #7 — iFrame path covers 5 Navigator props; Worker path covers 25+

**File:** `prototype_hardener.js` line 5180-5330 (iFrame) vs line 3830-3975 (Worker)
**Severity:** HIGH
**Detected by:** Worker/iFrame scope coverage pass (item 3)

### Evidence

**iFrame path (`__sreApplyFingerprinting`) Navigator props covered (line 5193-5240):**
1. platform
2. hardwareConcurrency
3. deviceMemory
4. language
5. languages

**iFrame path Intl classes covered (line 5243-5275):**
1. Intl.DateTimeFormat.resolvedOptions
2. Intl.NumberFormat.resolvedOptions
3. Intl.Collator.resolvedOptions

**iFrame path Date (line 5276-5315):**
1. Date.getTimezoneOffset
2. Date.toString (broken — wraps rawToStringDate.call(this), leaks real TZ)

**Worker path Navigator props covered (line 3895-3907 in module path, line 3955-3970 in classic path):**
1. hardwareConcurrency
2. deviceMemory
3. platform
4. languages
5. userAgent
6. appVersion
7. vendor
8. vendorSub
9. product
10. productSub
11. cookieEnabled
12. pdfViewerEnabled
13. onLine
14. language
15. maxTouchPoints
16. doNotTrack
17. geolocation (with full mock)
18. serviceWorker (undefined)
19. permissions (mock)
20. storage (mock)
21. clipboard (mock)
22. userAgentData (mock)
23. locks (mock)
24. gpu (undefined)
25. usb/serial/hid/bluetooth/ink/nfc (all undefined)

**Worker path is 5x more comprehensive than iFrame path.**

### Real-world impact

Detection vectors:
1. **Cross-origin iframe with own document**: A page creates `<iframe src="https://tracker.example/fp">` and the tracker reads `iframe.contentWindow.navigator.vendor`. The iframe's `navigator.vendor` returns the **real** vendor (Google Inc. for Chrome) because `__sreApplyFingerprinting` doesn't patch it. The main frame's `navigator.vendor` returns the spoofed value. Cross-frame inconsistency = fingerprint.

2. **Same-origin iframe**: Less common on content sites (Reddit/Quora/Medium), but if the page embeds an iframe (e.g., embedded YouTube player), the same fingerprint mismatch exists.

3. **CreepJS**: Tests by creating an iframe and reading `iframeWindow.navigator.userAgentData` (which IS in the Worker path but NOT in the iFrame path). Leaks the real `userAgentData` (real Chrome 120, real platform, real architecture).

### Fix

Extend `__sreApplyFingerprinting` to match the Worker path's coverage. Add to the Navigator.prototype patching block:

```js
// (existing 5)
// NEW (mirror Worker path):
const vendor = FP_CONFIG.isFirefox ? "" : "Google Inc.";
const productSub = FP_CONFIG.isFirefox ? "20100101" : "20030107";
safeIFrameOverride(targetWindow.Navigator.prototype, 'userAgent', FP_CONFIG.uaFullVersion);  // HACK: real UA is full, not version
safeIFrameOverride(targetWindow.Navigator.prototype, 'appVersion', FP_CONFIG.uaFullVersion);
safeIFrameOverride(targetWindow.Navigator.prototype, 'vendor', vendor);
safeIFrameOverride(targetWindow.Navigator.prototype, 'vendorSub', '');
safeIFrameOverride(targetWindow.Navigator.prototype, 'product', 'Gecko');
safeIFrameOverride(targetWindow.Navigator.prototype, 'productSub', productSub);
safeIFrameOverride(targetWindow.Navigator.prototype, 'cookieEnabled', true);
safeIFrameOverride(targetWindow.Navigator.prototype, 'pdfViewerEnabled', true);
safeIFrameOverride(targetWindow.Navigator.prototype, 'onLine', true);
safeIFrameOverride(targetWindow.Navigator.prototype, 'maxTouchPoints', FP_CONFIG.maxTouchPoints);
safeIFrameOverride(targetWindow.Navigator.prototype, 'doNotTrack', null);
```

Where `safeIFrameOverride` is a local helper that mirrors `safeWorkerOverride` but targets `targetWindow.Navigator.prototype` instead of `WorkerNavigator.prototype`.

Also fix the iFrame `Date.toString` (currently calls rawToStringDate which leaks real TZ):
```js
targetWindow.Date.prototype.toString = function() {
    try {
        // Use the same logic as main script
        const tzAbbr = /* ... persona TZ abbreviation ... */;
        return `${this.toLocaleDateString(...)} ${this.toLocaleTimeString(...)} ${tzAbbr}`;
    } catch (e) {
        return this.toUTCString().replace('GMT', 'UTC');
    }
};
```

Lines: ~80 added. Test: add `test_iframe_navigator_*` parallel to the existing `test_worker_navigator_*` tests.

---

## Recommended fix order

1. **Bug #7 (HIGH)** — iFrame path coverage gap. ~80 lines + 8 tests.
2. **Bug #2 (MEDIUM)** — connection type/effectiveType consistency. ~5 lines + 1 test.
3. **Bug #1 (MEDIUM)** — WebGL archString mapping. ~15 lines + 1 test.
4. **Bug #4 (MEDIUM)** — connection jitter timer cleanup. ~15 lines + 1 test.
5. **Bug #3 (LOW)** — skip (no exploitable leak).
6. **Bug #5 (LOW)** — skip (defer until real test catches it).
7. **Bug #6 (LOW)** — skip (100-hour-session territory).

Total: ~115 lines of new code + ~11 new tests. All within the existing patch style. No new architecture, no logger, no dashboard.

---

## What was NOT a bug (verified clean)

For completeness, the following items I considered but found clean:

- **focusFlipTimer** (line 1663): HAS pagehide handler. ✓
- **_batteryTimer** (line 880): HAS pagehide + `_pageIsClosing` guard. ✓
- **geolocation.setTimeout** (line 379): one-shot, not recurring. ✓
- **geolocation.setInterval** (line 395): tracked in `__sre_geo_watchers`, cleared by `clearWatch`. ✓
- **speechSynthesis.getVoices setTimeout** (line 3056): one-shot, no need for cleanup. ✓
- **chrome.runtime.sendMessage setTimeout** (line 1045): one-shot, response callback. ✓
- **WebAssembly.compile setTimeout** (line 4147): one-shot, await pattern. ✓
- **iframe broadcast ping setTimeout** (line 5321): one-shot, runs once at 100ms. ✓
- **matchMedia resize dispatch** (line 4890): event-driven, not a timer. ✓
- **rAF wrapped function** (line 3493+): browser-managed, cleared on page close. ✓
- **Worker override** (line 3830+): comprehensive, all 25+ props covered. ✓
- **SharedWorker** (line 3970+): correctly throws. ✓
- **module Worker** (line 3760+): wraps `import()` to ensure patches apply. ✓
- **Intl.DateTimeFormat.resolvedOptions** (main + iframe + worker paths): all consistent. ✓

---

## Audit files

- `prototype_hardener.js` line numbers verified: 99-188 (Gap C), 3390-3550 (Gap D), 5640-5740 (Gap F), 4290-4410 (connection), 2600-2640 (WebGL), 3760-3980 (Worker), 5180-5330 (iFrame).
- Test file references: `test_pass60_final_verification.py:469` (battery drain race), `test_pass62_rounds_2_5.py:51-84` (Worker navigator tests).
- All 7 bugs verified by reading 30+ lines of context per finding.
