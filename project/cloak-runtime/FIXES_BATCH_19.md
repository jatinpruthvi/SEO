# Round 23 (Batch 19) - Critical Persona/Bot Detection Fixes

## Summary
- **15 critical bugs** found that broke the core goals:
  - **Goal #1** (Website sees ONLY persona): 4 bugs
  - **Goal #2** (Website trusts real user): 11 bugs
- 15 fixes applied to manifest + 1 fix to buildRuntime
- 15 new tests added (427 → 442)
- Test results: **442/442 pass**
- All 6 source files pass `node --check`
- 115 external verification tests pass
- Test stability: verified 3 consecutive runs at 442/442
- Manifest: 478 → 493 patches → 207KB runtime

## CRITICAL Bugs Fixed (Break Goal #1: "Website sees ONLY persona")

### FIX #2041: Intl.DateTimeFormat/Date.getTimezoneOffset hardcoded to America/New_York
- **Before**: Patches hardcoded `timeZone: 'America/New_York'` and `return 240`
- **After**: Patches read from `window.__cloak_persona_tz` (set from manifest)
- **Impact**: Custom personas with different timezones now work correctly. Previously, a `Europe/Paris` persona would still report `America/New_York` timezone — a CRITICAL inconsistency.

### FIX #2042: Intl.Collator/Intl.NumberFormat hardcoded to en-US
- **Before**: Patches hardcoded `locale: 'en-US'`
- **After**: Patches read from `window.__cloak_persona_locale`
- **Impact**: Custom personas with different languages now report correct locale.

### FIX #2041c: buildRuntime injects persona globals
- `__cloak_persona_tz`, `__cloak_persona_locale`, `__cloak_persona_screen` are set from manifest.persona
- All persona-aware patches can now access these via `window.__cloak_persona_*`

## CRITICAL Bugs Fixed (Break Goal #2: "Website trusts real user")

### FIX #2043: window.chrome missing critical properties
- **Before**: Only had `runtime: {}, csi: function(){}, loadTimes: function(){}`
- **After**: Full Chrome 128 chrome object with:
  - `runtime.OnInstalledReason` (enum)
  - `runtime.OnRestartRequiredReason` (enum)
  - `runtime.PlatformArch` (enum)
  - `runtime.PlatformNaclArch` (enum)
  - `runtime.PlatformOs` (enum)
  - `runtime.RequestUpdateCheckStatus` (enum)
  - `app.isInstalled` (boolean) - **CRITICAL bot detection vector**
  - `app.InstallState` (enum)
  - `app.RunningState` (enum)
  - `csi.pageT, csi.startE, csi.tran` (real values)
  - `loadTimes.requestTime, startLoadTime, commitLoadTime, finishDocumentLoadTime, finishLoadTime, firstPaintTime, navigationType, wasFetchedViaSpdy, wasNpnNegotiated, npnNegotiatedProtocol, connectionInfo`

### FIX #2048: document.hasFocus returns true
- **Before**: Headless returns `false` (CRITICAL bot signal)
- **After**: Patched to always return `true`

### FIX #2049: document.hidden returns false
- **Before**: Headless often returns `true` (bot signal)
- **After**: Patched to return `false`

### FIX #2050: document.visibilityState returns 'visible'
- **Before**: Headless returns 'hidden' (bot signal)
- **After**: Patched to return 'visible'

### FIX #2051: document.webkitHidden returns false
- WebKit-prefixed version of hidden, also used by some detectors

### FIX #2044-2046: clientWidth/Height leak real screen
- **Before**: `document.documentElement.clientWidth` returns real viewport size
- **After**: Patched to 1920 (persona screen width)
- **Impact**: Even when `screen.width` is patched, these leak real viewport

### FIX #2052: performance.memory leaks heap
- **Before**: Returns real `usedJSHeapSize`, `totalJSHeapSize`, `jsHeapSizeLimit`
- **After**: Patched to realistic values (Chrome 128 typical: 5MB used, 10MB total, 4GB limit)

### FIX #2057: navigator.globalPrivacyControl
- Privacy signal that some sites check

### FIX #2047: window.matchMedia
- Can detect actual screen capabilities
- Patched to handle common media queries

### FIX #2058: window.webkitURL, URL.createObjectURL
- WebKit-prefixed URL APIs

### FIX #2059: navigator.storage
- Storage estimate API

### FIX #2063: window.speechSynthesis
- Speech synthesis API

## Test Results
- `test/selftest.js`: 442 pass / 0 fail
- `R7_DEEP_AUDIT_2/test_pass60_final_verification.py`: 115/115 pass
- All 6 source files pass `node --check`
- 3 consecutive test runs stable at 442/442
- Manifest build: 493 patches → 207KB runtime

## Files Modified
- `manifests/chrome-128-win.json`: +15 patches (478 → 493)
- `src/manifest_patcher.js`: buildRuntime now injects persona globals
- `test/selftest.js`: +15 tests (427 → 442)

## What's Now Consistent With Persona

| Vector | Before | After |
|--------|--------|-------|
| `Intl.DateTimeFormat` timezone | Hardcoded NY | From persona |
| `Date.getTimezoneOffset()` | Hardcoded 240 | From persona |
| `Intl.Collator` locale | Hardcoded en-US | From persona |
| `Intl.NumberFormat` locale | Hardcoded en-US | From persona |
| `document.hasFocus()` | Real (headless: false) | Always true |
| `document.hidden` | Real (headless: true) | Always false |
| `document.visibilityState` | Real (headless: hidden) | Always visible |
| `document.documentElement.clientWidth` | Real viewport | 1920 (persona) |
| `document.body.clientHeight` | Real viewport | 969 (persona) |
| `performance.memory.usedJSHeapSize` | Real heap | 5MB (realistic) |
| `window.chrome.app.isInstalled` | undefined | false (realistic) |
| `window.chrome.csi.pageT` | undefined | Real value |
| `window.chrome.loadTimes.*` | undefined | Real values |
| `navigator.globalPrivacyControl` | undefined | false |
| `window.matchMedia` | Real | Patched |
