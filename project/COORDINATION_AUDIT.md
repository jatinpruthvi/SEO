# Deep-Dive Audit: CloakBrowser × prototype_hardener.js

## CRITICAL ISSUES FOUND IN v5.0+ COORDINATION

### Issue #1: `safePropertyOverride` Has a Logic Bug for Property Placement

Looking at lines 184-209 of prototype_hardener.js:
```js
const protoDesc = (obj.__proto__ || Object.getPrototypeOf(obj)) ? 
    Object.getOwnPropertyDescriptor(obj.__proto__ || Object.getPrototypeOf(obj), prop) : null;
const target = (desc && desc.get) ? obj : (protoDesc ? (obj.__proto__ || Object.getPrototypeOf(obj)) : obj);
```

**BUG**: This logic is wrong. When `desc` is from the instance (own property) and has a getter, we override on the instance. But when `desc` is null and `protoDesc` is null too, we still set on `obj` (the instance), which creates a new own-property that SHADOWS the prototype — this changes the property enumeration order that anti-bot scripts check for.

**FIX**: Always prefer the prototype unless the property is an own accessor on the instance. Detect via `Object.prototype.hasOwnProperty.call(obj, prop)`.

### Issue #2: Missing WebGL2 `getShaderPrecisionFormat` Override

The script overrides `getShaderPrecisionFormat` for `WebGLRenderingContext` and `WebGL2RenderingContext` in the OLD code but it was REMOVED in the rewrite. WebGL2's `getShaderPrecisionFormat` is a separate vector that anti-bot scripts (especially Google's) use.

### Issue #3: `navigator.connection` rtt/downlink Use Wrong Base Values

Lines 919-952:
```js
return (rawRtt ? rawRtt.get.call(this) : 100) + jitter;
```

**BUG**: This reads `rawRtt.get.call(this)` which is the actual C++ value. CloakBrowser may have set the C++ value to 100ms (Linux/Android default). The persona should be claiming 4G, which has rtt=50ms. The script should OVERRIDE the base value, not add jitter to the real one.

### Issue #4: Service Worker Override Doesn't Honor Manifest

The Service Worker registration is wrapped unconditionally (line 1426). CloakBrowser may own this. Need `!shouldSkipJS('serviceWorker')` check.

### Issue #5: `getScreenDetails` (Window Management API) Missing

`window.getScreenDetails()` is a new API in Chrome 100+ for multi-monitor fingerprinting. CloakBrowser may or may not own this. Not handled at all in v5.0+.

### Issue #6: `navigator.bluetooth`, `navigator.usb`, `navigator.serial`, `navigator.hid` Missing

These are new APIs in Chromium that are restricted but the restriction can be detected. The script should:
- Make them `undefined` (consistent with restricted state)
- OR mock them with persona-deterministic device lists

### Issue #7: `OffscreenCanvas.getContext` Override Missing

The script overrides `HTMLCanvasElement.prototype.getContext` but NOT `OffscreenCanvas.prototype.getContext`. A `transferControlToOffscreen` call bypasses the override.

### Issue #8: `StorageManager.persist` Not Handled

`navigator.storage.persist()` is a new API. A script can call this to detect if the storage is persistent. The script doesn't override it.

### Issue #9: `navigator.permissions.query` Always Returns 'prompt' — Even for Granted

Lines 858-866:
```js
Permissions.prototype.query = async function(parameters) {
    return { state: 'prompt', onchange: null };
};
```

**BUG**: This returns `'prompt'` for ALL permission queries. But a real Chrome with a logged-in user might have `'granted'` for `clipboard-read` after the user has used copy-paste. A real user with notifications enabled has `'granted'`. Returning `'prompt'` for everything is a fingerprint vector — but actually, it's also realistic for a fresh profile. However, the orchestrator's persona should drive this.

### Issue #10: `navigator.userAgentData.brands` Schema Mismatch

The mockUAData creates `brands` as array of `{brand, version}`. But real Chrome's brand has a specific ordering and version format. The script should ensure `brands[0].version` matches `grease_version` if CloakBrowser applies GREASE.

### Issue #11: `navigator.userAgentData.mobile` and `getHighEntropyValues` Schema Mismatch

For a "desktop" persona, `mobile: false`. The script sets this correctly. But `getHighEntropyValues` should return `wow64: false` (Windows on Windows 64-bit), which is missing.

### Issue #12: `window.crossOriginIsolated` Not Handled

`window.crossOriginIsolated` is a boolean that depends on COOP/COEP headers. In a typical anti-bot context, it's `false`. The script doesn't set it.

### Issue #13: `navigator.ink` and `navigator.pointerEvent` Missing

New APIs in Chromium 94+. `navigator.ink` returns a PointerEvent API for stylus. `navigator.maxTouchPoints` should match the persona (0 for desktop, 1+ for touch).

### Issue #14: `window.visualViewport` Not Handled

`window.visualViewport` exposes viewport info that changes on mobile/pinch-zoom. For a desktop persona, it should be consistent with `innerWidth/innerHeight`.

### Issue #15: `Document.fonts.check()` and `FontFaceSet.load()` Not Handled

The script mocks `FontFaceSet.prototype.check` but not `load()`. A real script can call `await document.fonts.load('12px Calibri')` to test font availability, bypassing the `check` mock.

### Issue #16: `navigator.clipboard.readText/writeText` Not Handled

A real script can call `navigator.clipboard.writeText('test')` to detect if clipboard is allowed. Returning a Permission denied error is correct, but a fresh profile should have `clipboard-write: granted` by default in some Chromium versions.

### Issue #17: `navigator.credentials` Not Handled

`navigator.credentials.get({password: true})` triggers a password manager prompt. The script doesn't override this.

### Issue #18: `navigator.mediaSession` Not Handled

`navigator.mediaSession.metadata` is a new API for media controls. A real desktop browser exposes this; headless doesn't.

### Issue #19: `navigator.serial`, `navigator.hid` Not Handled

These are WebUSB/HID/Serial APIs. The script doesn't even check for them.

### Issue #20: `chrome.cookies` (Chrome extension API) Not Handled

Some fingerprint scripts check for `window.chrome.cookies` to detect Chrome extensions. The script mocks `chrome.runtime` and `chrome.app` but not `chrome.cookies` or `chrome.extension`.

### Issue #21: `screen.isExtended` Not Handled

New API for multi-monitor detection. The script doesn't set it.

### Issue #22: `screen.orientation` Not Handled

`screen.orientation.type` returns a string like `"landscape-primary"`. A real desktop with no rotation has `"landscape-primary"`. The script doesn't set this.

### Issue #23: `screen.colorDepth` Set to 24 But Not Verified

The persona digest has `screenColorDepth: 24`, but the script doesn't actually set `Screen.prototype.colorDepth` anywhere. CloakBrowser owns this at C++ level. Need to verify the manifest.

### Issue #24: `navigator.pdfViewerEnabled` Not Handled

Chrome 94+ exposes `navigator.pdfViewerEnabled`. Real Chrome has `true`; headless has `false`. Script doesn't set this.

### Issue #25: `window.scheduler` Not Handled

`window.scheduler` is a new API for task scheduling. Some headless detection checks for its presence.

### Issue #26: `navigator.deviceMemory` Is Writable Property (Wrong!)

The script uses `safePropertyOverride` to set `Navigator.prototype.deviceMemory`. But real Chrome's `deviceMemory` is a **getter** on `Navigator.prototype`. Setting it via `defineProperty` with a `value` instead of `get` may produce an inconsistency. The script should preserve the getter pattern.

### Issue #27: Manifest Doesn't Declare ALL Owned Properties

The orchestrator's `cloak_manifest` is minimal:
```python
"owned_by_cloak": [
    "Function.prototype.toString",
],
```

But CloakBrowser typically owns many more. Need a comprehensive list.

### Issue #28: Performance.now Rounding Creates "Time Travel" Artifacts

Lines 627-633:
```js
window.performance.now = function() {
    const time = rawPerformanceNow.call(window.performance);
    const rounded = Math.round(time / 0.1) * 0.1;
    if (rounded > lastPerformanceTime) lastPerformanceTime = rounded;
    return lastPerformanceTime;
};
```

**BUG**: This rounds to 0.1ms (100µs) granularity. Real Chrome has 5µs granularity (from `performance.timeOrigin` precision). 100µs is too coarse — a fingerprint script that measures sub-millisecond intervals (e.g., between `keydown` and `keyup`) will see 100µs steps, which is a bot tell. Real browsers have variable intervals.

**FIX**: Add ±5-20µs jitter to each call (persona-deterministic) instead of rounding.

### Issue #29: OffscreenCanvas toBlob/convertToBlob Noise Trigger Inefficient

Lines 477-494. Each call to `convertToBlob` triggers a 1x1 `getImageData` call to apply noise. This is wasteful. Better: apply noise lazily, only when the buffer is actually read.

### Issue #30: `requestAnimationFrame` Timing Not Handled

The script doesn't override `requestAnimationFrame` to add jitter. Real browsers have ±1-2ms frame jitter; headless often has 0ms jitter if `prefers-reduced-motion: reduce` is set (or vice versa).

### Issue #31: `Document.fonts.ready` Not Handled

`await document.fonts.ready` resolves after fonts are loaded. The mock should not break this promise.

### Issue #32: `navigator.userAgentData.platformVersion` Default Mismatch

`platformVersion: "10.0.0"` is set as default, but for a "Windows 11" persona it should be `"15.0.0"`. The mockUAData doesn't read this from the persona digest.

### Issue #33: `getContext('webgl2')` Returns Same Object as `getContext('webgl')`

The script doesn't override `getContext` to ensure WebGL1 and WebGL2 contexts are distinct. Some Chromium versions return the same context for both calls, which is detectable.

### Issue #34: `Worker` Override Misses `importScripts` Errors

The Worker wrapper uses `importScripts` to load the original script. If the script uses ES modules (not classic), this fails. The check `options.type === 'module'` is there but the error path is silent.

### Issue #35: `Worker.prototype.postMessage` Override Missing

Some fingerprint scripts run fingerprinting INSIDE a worker. The script wraps the `Worker` constructor but not the `postMessage` and `onmessage` handlers.

### Issue #36: `navigator.webdriver` Should Be in Manifest

`navigator.webdriver` is ALWAYS overridden (line 175, unconditional). But CloakBrowser owns this at C++ level. Need to add `!shouldSkipJS('webdriver')` check.

### Issue #37: `navigator.languages` Should Be in Manifest

Line 458 overrides `languages` unconditionally. Should check `!shouldSkipJS('languages')`.

### Issue #38: `Screen.prototype.availHeight` Default to 0 If Real Value Is 0

Lines 793-802:
```js
const rawAvailHeight = Screen.prototype.availHeight;
...
const h = rawAvailHeight || screen.height;
```

**BUG**: If `rawAvailHeight` is 0 (real value from CloakBrowser), it falls back to `screen.height`. But 0 might be the legitimate C++ value (e.g., when CloakBrowser explicitly sets `availHeight=screen.height` to hide the taskbar). Should use `rawAvailHeight` directly when present.

### Issue #39: `navigator.connection` Add Type Field

`navigator.connection.type` (e.g., 'wifi', 'cellular', '4g', 'none') is not overridden. Real browsers expose this. Should return persona-consistent value.

### Issue #40: `navigator.connection.effectiveType` Not Handled

`effectiveType: '4g'` is a high-signal fingerprint vector. The script doesn't set it.

### Issue #41: `navigator.connection.saveData` Not Handled

Boolean. Real Chrome has `false` by default.

### Issue #42: `navigator.buildID` Firefox-Specific Path Incomplete

Firefox's `navigator.buildID` is set in the Firefox path (line 217). But `navigator.buildID` is also in the Chromium path as a delete. Inconsistency: if isFirefox is wrong, the wrong path runs.

### Issue #43: `Object.prototype.toString` Not Handled

`Object.prototype.toString.call(window.chrome)` returns `[object Object]` instead of `[object Chrome]`. Anti-bot scripts check this.

### Issue #44: `navigator.platform` Override Doesn't Handle Linux Variants

Line 196 / 224 sets `platform` from `FP_CONFIG.platform`. If the persona is "Win32", JS sets it. But CloakBrowser at C++ level may have set it to "Linux x86_64" (the actual host). The orchestrator's `cloak_manifest.values.platform` should be added to the values map.

### Issue #45: `navigator.oscpu` Firefox Path Inconsistent

Line 213 sets `oscpu` based on platform. But the platform map (`platformOscpu`) doesn't include ALL platforms. If `FP_CONFIG.platform` is unexpected, the fallback is "Linux x86_64" — but this is set even when the persona claims Windows.

### Issue #46: `chrome.runtime.id` Missing

Real Chrome extensions have `chrome.runtime.id` (a UUID). The mock should generate a stable fake ID per persona, not `undefined`.

### Issue #47: `chrome.runtime.getManifest()` Missing

Real Chrome has `chrome.runtime.getManifest()`. The mock should return a persona-stable manifest object.

### Issue #48: `navigator.userAgentData.architecture` Not Set

The mockUAData doesn't set `architecture` field. A real Chromium reports `"x86"` for desktop. The script should set it from `FP_CONFIG.webglVendor` or a separate field.

### Issue #49: `navigator.userAgentData.bitness` Not Set

Similar — should be `"64"` for x86_64 persona.

### Issue #50: `navigator.userAgentData.model` Always Empty

`model: ""` is set as default. For a desktop, this is correct. For a mobile persona, it should be the model name.

### Issue #51: `navigator.userAgentData.uaFullVersion` Default Inconsistency

Default is `"120.0.6099.109"`. If the persona's UA is Chrome 119, the fullVersion should be 119.x. The script doesn't read UA version from FP_CONFIG.

### Issue #52: `navigator.userAgentData.fullVersionList` Always Has Same Default

Same issue as #51.

### Issue #53: WorkerNavigator Overrides Don't Cover `oscpu`, `buildID` for Firefox Worker

Line 728-742. Only covers `hardwareConcurrency, deviceMemory, platform, languages`. Firefox workers also have `oscpu, buildID`.

### Issue #54: `navigator.clipboard` Permission Behavior

The script mocks `Permissions.query` to always return `prompt`. But for `clipboard-write`, Chrome actually has `granted` by default in some contexts. Should be configurable via persona.

### Issue #55: `window.isSecureContext` Not Set

A real browser on `https://` is `true`; on `http://` is `false`. The script doesn't override. CloakBrowser may set this, but if it doesn't, anti-bot scripts can read it.

### Issue #56: `document.hidden` and `document.visibilityState`

For a hidden tab (backgrounded), `document.hidden = true, visibilityState = 'hidden'`. For an active tab, `false, 'visible'`. Anti-bot scripts can check this. The script doesn't override.

### Issue #57: `document.hasFocus()` Not Handled

For an active tab, `true`. For backgrounded, `false`. Important for click-event validation (real user events have focus; bot-injected events don't).

### Issue #58: `document.activeElement` Not Handled

Returns the currently focused element. For a click sequence, this should be consistent with where the click happened.

### Issue #59: `XMLHttpRequest` Progress Events Timing

The script doesn't override `XMLHttpRequest` to add timing jitter. Real browsers have variable progress event timing based on network. Headless can have constant intervals.

### Issue #60: `fetch()` Response Timing

Similar — `fetch()` should have realistic timing for each chunk.

### Issue #61: `navigator.gpu` (WebGPU) Not Handled at the Right Level

The script overrides `GPUAdapterInfo.prototype` but not `navigator.gpu.requestAdapter()`. A real WebGPU request returns an adapter; the script should ensure it returns a stable persona-consistent one.

### Issue #62: `navigator.userAgentData.toJSON()` Not Implemented

Real `NavigatorUAData` has a `toJSON()` method that returns a serializable object. The mock doesn't implement it.

### Issue #63: `AudioContext.createMediaStreamDestination()` Behavior

The script doesn't override. A real MediaStreamDestination has `channelCount=1` (mono). The script overrides `AudioNode.prototype.channelCount = 2`, which would WRONG for this specific node.

### Issue #64: `AnalyserNode.fftSize`, `frequencyBinCount` Should Be Matched

These are constructor-time values. Anti-bot checks if `fftSize / 2 === frequencyBinCount`. The script doesn't verify or override.

### Issue #65: `OfflineAudioContext.startRendering()` Result Hash

The script overrides `AnalyserNode.getFloatFrequencyData` but not `OfflineAudioContext` rendering output. A real script that renders an audio buffer offline and hashes it gets the C++ engine's output. The script should add persona-deterministic noise here too.

### Issue #66: `HTMLImageElement.src` Setter Behavior

Setting `img.src = 'data:...'` triggers async load. The script doesn't override. Some detection scripts test this with a known data URL and time the load.

### Issue #67: `navigator.mediaDevices.getUserMedia()` Not Handled

The script overrides `enumerateDevices` but not `getUserMedia`. A real script that calls `getUserMedia({video: true})` will fail with `NotAllowedError` — a real browser shows a permission prompt. The script should override to return a persona-deterministic stream or consistent error.

### Issue #68: `document.cookie` Setter Not Handled

Setting a cookie via `document.cookie = 'name=value'` should be allowed in real browsers (after CORS check). The script doesn't override, but headless can have restrictions.

### Issue #69: `window.opener` Not Handled

A new tab opened via `window.open` has `window.opener` set. The script doesn't override.

### Issue #70: `document.referrer` Not Handled

A real page navigated from a search result has `document.referrer` set. The script doesn't override. A fresh navigation has empty referrer; this should be consistent.

### Issue #71: `location.ancestorOrigins` Not Handled

For iframes, `location.ancestorOrigins` returns the chain of parent origins. The script doesn't override.

### Issue #72: `document.domain` Setter Not Handled

A real browser allows `document.domain = 'example.com'` (downgrade). The script doesn't override.

### Issue #73: `crypto.getRandomValues()` Performance Not Handled

`crypto.getRandomValues()` is supposed to be fast (~1µs). If a CloakBrowser-level hook adds delay, the script should compensate or trust C++.

### Issue #74: `crypto.subtle` Behavior Not Handled

`crypto.subtle.digest()` returns a Promise. Some scripts time this. The script doesn't add jitter.

### Issue #75: `navigator.canShare()` Not Handled

New API. Returns boolean. Real Chrome has `false` by default. The script doesn't set.

### Issue #76: `navigator.share()` Not Handled

Similar.

### Issue #77: `navigator.mediaCapabilities` Not Handled

`navigator.mediaCapabilities.decodingInfo()` returns info about media support. The script doesn't override.

### Issue #78: `scheduler` API Not Handled

`scheduler.postTask()` is a new API. The script doesn't override.

### Issue #79: `navigator.locks` Not Handled

`navigator.locks.request()` is a new API. The script doesn't override.

### Issue #80: `BarcodeDetector` / `FaceDetector` / `TextDetector` Missing

These are Shape Detection API. Real Chrome has them; the script doesn't mock.

### Issue #81: `navigator.scheduling` Not Handled

New API. The script doesn't override.

### Issue #82: `navigator.managed` Not Handled

`navigator.managed` is `true` for managed devices (corporate). Default `false` for consumer. Not set.

### Issue #83: `document.pictureInPictureEnabled` Not Set

Real Chrome has `true` for desktop. Not set.

### Issue #84: `document.pictureInPictureElement` Default

`null` for non-PiP. Not set explicitly.

### Issue #85: `document.fullscreenEnabled` Not Set

Real Chrome has `true`. Not set.

### Issue #86: `document.webdriver` Not Handled

`document.webdriver` is a separate property from `navigator.webdriver`. Both should be `false` (or undefined). The script only handles `navigator`.

### Issue #87: `window.trustedTypes` Not Handled

`window.trustedTypes` is a security API. Real Chrome has it; headless may not.

### Issue #88: `navigator.deprecatedRunAdAuctionEnqueue` Not Set

Recent API. Not handled.

### Issue #89: `navigator.viewport` Not Handled

In some browsers, this is a SegmentedControl. Not handled.

### Issue #90: `navigator.webkitPersistentStorage` Not Handled

WebKit-specific. Chrome may have it as a legacy alias. Not set.

### Issue #91: `navigator.webkitTemporaryStorage` Not Handled

Same.

### Issue #92: `navigator.credentials.preventSilentAccess()` Not Set

Credential Management API. Not handled.

### Issue #93: `window.ApplePaySession` Not Set

On non-Apple devices, this should be `undefined`. Real Chromium has it. Not handled.

### Issue #94: `navigator.connection.downlinkMax` Not Set

Some browsers expose this. The script doesn't set it.

### Issue #95: `navigator.connection.ontypechange` Not Set

Event handler. Not set.

### Issue #96: `navigator.onLine` Default Value

Real browser always `true` (unless offline). Not set.

### Issue #97: `window.queueMicrotask` Not Handled

Real browsers have this. Not overridden (but it should be there by default).

### Issue #98: `window.structuredClone` Not Handled

New API. Not overridden.

### Issue #99: `window.reportError` Not Handled

For error reporting. Not overridden.

### Issue #100: `navigator.userAgentData.brand` field missing in mock

Real `NavigatorUAData.brand` is a string (the first brand's name). The mock has `brands` array but no `brand` field.

### Issue #101: WorkerNavigator `userAgent` Not Overridden

The Worker wrapper only overrides a few properties. `WorkerNavigator.userAgent` is left unhardened. Real worker userAgent may be `Mozilla/5.0 ... Chrome/120 ...`. Should be set to the persona's UA.

### Issue #102: WorkerNavigator `appVersion` Not Overridden

Similar — `WorkerNavigator.appVersion` should be the persona's app version.

### Issue #103: WorkerNavigator `vendor` / `vendorSub` Not Overridden

The wrapper doesn't cover these.

### Issue #104: WorkerNavigator `product` / `productSub` Not Overridden

`"Gecko"` and `"20030107"` for Firefox; `"Google Chrome"` and `"20030107"` for Chrome. Not set in worker.

### Issue #105: WorkerNavigator `cookieEnabled` Not Overridden

Real browser has `true`. Not set.

### Issue #106: `window.caches` Behavior

`window.caches.open('name')` returns a Cache object. The script doesn't mock.

### Issue #107: `window.indexedDB` Behavior

Same. Not mocked.

### Issue #108: Service Worker `MessageChannel` Not Mocked

When a service worker communicates via postMessage, MessageChannel is used. The script doesn't override.

### Issue #109: `BroadcastChannel` Behavior

`new BroadcastChannel('name')` is used for cross-tab communication. Not handled.

### Issue #110: `SharedWorker` Not Handled

The script wraps `Worker` but not `SharedWorker`. A real script that uses `new SharedWorker('...')` gets a shared worker.

### Issue #111: `Object.keys(document.fonts)` Order

The script mocks `FontFaceSet.prototype.check` but not the iteration order. Anti-bot scripts check that font order matches a real Chrome.

### Issue #112: `window.event` Global Not Handled

IE legacy `window.event`. Real Chrome doesn't have it, but old scripts may check.

### Issue #113: `document.all` Behavior

`document.all` is an HTMLAllCollection in old IE; in modern Chrome, it's `undefined`. The script doesn't set.

### Issue #114: `navigator.maxTouchPoints` Not Set

Real desktop has `0`. Mobile has `5+`. Not set.

### Issue #115: `window.orientation` Mobile-Only

`window.orientation` is `0` or `90` etc. on mobile. Desktop has `undefined`. Not set.

### Issue #116: `document.body.contentEditable` Not Handled

A real document has `contentEditable = 'inherit'` by default. Not set.

### Issue #117: `getComputedStyle()` Timing

`window.getComputedStyle(element)` is synchronous but takes ~0.1-0.5ms. Anti-bot scripts measure this. The script doesn't add jitter.

### Issue #118: `Element.prototype.scrollIntoView()` Not Handled

Real browsers have a smooth scroll animation. Headless can be instant. The script doesn't override.

### Issue #119: `getBoundingClientRect` Override Returns Wrong toStringTag

Line 543 returns an object with `toJSON()` but not `Symbol.toStringTag`. A real `DOMRect` has `[Symbol.toStringTag] = 'DOMRect'`. The override should match.

### Issue #120: `document.elementFromPoint()` Not Handled

A real browser returns the element at (x, y). For headless, may return null. Not set.

### Issue #121: `caretRangeFromPoint()` and `caretPositionFromPoint()` Not Handled

Selection API. Not set.

### Issue #122: `document.elementsFromPoint()` Not Handled

Similar to #120.

### Issue #123: `HTMLCanvasElement.captureStream()` Not Handled

Real Chrome has this. Headless can fail.

### Issue #124: `HTMLVideoElement.requestVideoFrameCallback` Not Handled

Real Chrome has this for video. Not set.

### Issue #125: `OffscreenCanvasRenderingContext2D.commit()` Not Handled

If `commit()` is called, the rendering context is committed. Not set.

### Issue #126: `Worker` Override May Break Object URL Lifecycle

The script creates a `Blob` and `URL.createObjectURL(blob)`, but never revokes the URL. Memory leak: each worker spawn leaks the blob.

### Issue #127: `URL.createObjectURL` Not Wrapped to Track Allocations

The script creates object URLs but doesn't track them. No revocation on worker termination.

### Issue #128: `Worker` Override Breaks `onmessage` Property

The override sets `onicecandidate` for ICE, but for general Workers, setting `onmessage` is not affected. The override should also be careful with `onerror` and `onmessageerror`.

### Issue #129: ICE Gathering Delay Doesn't Handle `pc.close()` Before Timeout

If `pc.close()` is called within the 300-800ms delay, the `setTimeout` fires anyway and calls `originalHandler` with a stale `event` reference. The `gatheringCompleteFired` flag prevents double-firing, but the listener still runs with potentially undefined state.

### Issue #130: `RTCPeerConnection.prototype.addIceCandidate` Override Missing

The script overrides `createOffer/createAnswer/getStats` but not `addIceCandidate`. A real script can call `pc.addIceCandidate({candidate: "candidate:1 1 udp 2122252543 ..."})` and inspect the timing.

### Issue #131: `RTCDataChannel` Behavior Not Handled

`new RTCPeerConnection().createDataChannel('chat')` returns an RTCDataChannel. The script doesn't override.

### Issue #132: `navigator.userAgentData.getHighEntropyValues` Mock Doesn't Have `wow64` Field

Line 281. The mock returns `architecture, bitness, brands, fullVersionList, mobile, model, platform, platformVersion, uaFullVersion`. Missing: `wow64` (Windows on Windows 64-bit flag).

### Issue #133: `getBattery` Override May Not Have `onchargingchange` etc.

The script sets `charging, chargingTime, dischargingTime, level` on the mockBattery but doesn't add the `onchargingchange` event handler field. Real BatteryManager has these.

### Issue #134: `navigator.permissions.query` Should Mock ALL Permission Names

The mock returns `prompt` for all. But a real browser returns different states for different permissions:
- `clipboard-read`: prompt
- `clipboard-write`: granted (in some configs)
- `notifications`: prompt / granted / denied
- `geolocation`: prompt
- `camera`: prompt
- `microphone`: prompt
- `midi`: granted (in some configs)
- `push`: granted
- `screen-wake-lock`: prompt

A blanket `prompt` for all is detectable.

### Issue #135: `navigator.permissions.query` Permission-Specific Mocks Missing

The mock should return persona-specific states for each permission name.

### Issue #136: `permissions.query({name: 'clipboard-sanitized-write'})` Not Handled

New permission name. Default to `prompt` or `granted`.

### Issue #137: `document.featurePolicy` Not Handled

`document.featurePolicy.allowedFeatures()` returns a list. Not set.

### Issue #138: `document.policy` Not Handled

New API (Permissions Policy). Not set.

### Issue #139: `navigator.connection.addEventListener` Not Handled

The script overrides `rtt` and `downlink` getters but the underlying `NetworkInformation` events (`change`, etc.) may not fire. Not set.

### Issue #140: `navigator.connection.removeEventListener` Not Handled

Similar.

### Issue #141: `navigator.mediaCapabilities.encodingInfo` Not Handled

Similar to `decodingInfo`. Not set.

### Issue #142: `navigator.ink` (PointerEvent for Stylus) Not Handled

`navigator.ink.requestPresenter()` etc. Not set.

### Issue #143: `window.getDigitalGoodsService` Not Handled

Play Billing API. Not set.

### Issue #144: `navigator.managed` Not Set

Enterprise feature. Default `false`. Not set.

### Issue #145: `navigator.plugins` Iteration Order

The mock PluginArray iteration via `for...of` is OK, but the order of plugins matters. Real Chrome has `["PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer"]` in that exact order. Verify.

### Issue #146: `navigator.mimeTypes` Not Set

Real Chrome has `mimeTypes` array. The script doesn't override.

### Issue #147: `navigator.pdfViewerEnabled` Not Set

Real Chrome has `true`. Not set.

### Issue #148: `window.chrome.csi()` Returns Wrong Schema

Real `chrome.csi()` returns `{startE, onloadT, pageT, tran}`. The mock matches. But real Chrome adds more fields like `redirectCount`, `navigationId`. Not in the mock.

### Issue #149: `window.chrome.loadTimes()` Schema Mismatch

Real `chrome.loadTimes()` includes `connectionInfo`, `npnNegotiatedProtocol`, etc. The mock matches. But `pipelineFirstByteAt` may also be present in newer Chrome. Not in the mock.

### Issue #150: `navigator.webdriver` Should Also Reset `document.webdriver`

The script sets `Navigator.prototype.webdriver = undefined` but `document.webdriver` is a separate property. Both should be `undefined` for a real user.
