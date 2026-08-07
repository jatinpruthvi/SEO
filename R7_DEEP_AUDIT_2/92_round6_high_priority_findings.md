# R7-DEEP-AUDIT-2 — Round 6 High-Priority Findings (30 issues)

Scope: `/home/user/project/browser-runtime/prototype_hardener.js` and `hardened_orchestrator.py`
Mode: **No code changes. Findings only.** Round 6 builds on the Round 5 fix batch.
Severity legend: **CRIT** = instant detection, **HIGH** = strong signal, **MED** = corroborative.

---

## Category A: Event System & Timing (10 issues)

### A1. [CRIT] **`PerformanceObserver` mock supports "navigation" and "resource" entry types but never fires them**
- **Location/Trigger**: `prototype_hardener.js:1910-1927`
  ```js
  POFake.supportedEntryTypes = ['paint', 'navigation', 'resource', 'mark', 'measure', ...];
  this.observe = function(opts) { /* Silently accept but never fire */ };
  ```
- **Blind spot**: A page that does `new PerformanceObserver(cb).observe({type: 'navigation'})` and waits for callbacks never sees them. Real Chrome fires `navigtion` entries on page load. Detecting: any page that registers a callback that never fires is a clear automation signal. Also, `supportedEntryTypes` lists types we never fire — pages can check `POFake.supportedEntryTypes.includes('navigation')` (returns true) but then never see entries.
- **Detection**: `const obs = new PerformanceObserver(() => window.__sre_perfFired = true); obs.observe({type: 'navigation'}); setTimeout(() => { if (!window.__sre_perfFired) flag; }, 1000);`

### A2. [HIGH] **`PerformanceObserver` constructor signature mismatch — `new` requirement**
- **Location/Trigger**: `prototype_hardener.js:1910-1917` — `POFake = function(callback) {...}` is a regular function, not a class. `new POFake(cb)` works but `POFake(cb)` (without `new`) also works (returning `this`). Real Chrome's `PerformanceObserver` throws TypeError if called without `new`. Pages that do `Reflect.construct(PerformanceObserver, [cb])` or `PerformanceObserver.prototype.constructor.call(...)` would see different behavior.
- **Detection**: `Reflect.construct(PerformanceObserver, [() => {}])` vs `new PerformanceObserver(() => {})` — both should work in real Chrome.

### A3. [CRIT] **`chrome.csi().tran` increments on `popstate` (line ~1316) but `chrome.csi()` itself caches the startE on the FIRST call only — so a page that calls `csi()` AFTER the first navigation event will get the OLD startE**
- **Location/Trigger**: `prototype_hardener.js:1280-1307`
  ```js
  let _csiStartECached = null;
  // First call computes startE; subsequent calls return cached
  ```
  The `pageshow`/`popstate` listener calls `_resetCsi()` which sets the cache to null. But the reset only happens on `pageshow`/`popstate` events. If a page navigates WITHOUT firing `pageshow` (e.g., via `location.replace()` or `window.location.href = ...`), the cache is not reset, and a subsequent `csi()` call returns the OLD page's startE.
- **Detection**: `chrome.csi().startE` should change after navigation; ours doesn't always.

### A4. [HIGH] **`EventTarget` prototype pollution: `addEventListener` doesn't preserve `this` binding for `EventTarget`-style events fired by our mocks**
- **Location/Trigger**: Multiple places where we use `eventTarget.addEventListener.bind(eventTarget)`. The `bind` creates a new function, so `this` is fixed. Real Chrome's `EventTarget.addEventListener` is bound to the actual target. Pages that check `target === this` in callback (or use `WeakRef` to compare) flag.
- **Detection**: `el.addEventListener('click', function(e) { this === el ? null : flag; });`

### A5. [MED] **`devicePixelRatio` is set on `window` but `screen.devicePixelRatio` is not — pages reading `screen.devicePixelRatio` get a different value than `window.devicePixelRatio`**
- **Location/Trigger**: `prototype_hardener.js:2997-3030` (window.devicePixelRatio) vs no `screen.devicePixelRatio` override
- **Detection**: `if (window.devicePixelRatio !== screen.devicePixelRatio) flag;` — real Chrome has them equal.

### A6. [HIGH] **`MouseEvent.movementX/movementY` and `TouchEvent` constructors are not patched** — the `MouseEvent` constructor inherits from `UIEvent` which inherits from `Event`. Pages that create `new MouseEvent('mousemove', { movementX: 100, movementY: 50 })` get REAL values that can leak (the OS mouse position).
- **Location/Trigger**: No override found in `prototype_hardener.js` for `MouseEvent` or `TouchEvent`.
- **Detection**: `const m = new MouseEvent('mousemove', {movementX: 99999, movementY: 99999}); if (m.movementX !== 99999) flag;`

### A7. [HIGH] **`PointerEvent.prototype.pressure` override at line 4003-4020 uses `this[pressureSym] = ...` for the first call but only for `this.buttons > 0`** — if `buttons` is 0 (mouse hover, no click), pressure is 0.0, but a real pen with `buttons=0` (hovering) reports pressure > 0 (real pressure values from pen hardware).
- **Location/Trigger**: `prototype_hardener.js:4003-4020`
- **Detection**: Pen API tests: `const e = new PointerEvent('pointermove', {pointerType: 'pen', pressure: 0.5, buttons: 0}); e.pressure === 0` is wrong for pen.

### A8. [CRIT] **`chrome.csi()` and `chrome.loadTimes()` are cached on first call, but `requestStart` and `responseStart` should change if the page makes XHR/fetch requests after the first call**
- **Location/Trigger**: `prototype_hardener.js:1280-1325` and `1325-1360`
  The `csi()` mock returns `_csiStartECached` set on first call. Real Chrome's csi() returns the cached `startE` (correct) but `tran` changes per navigation. Our `tran` does increment, but `startE` is a NAVIGATION-START timestamp, not a session-start. After a SPA navigation, our cached `startE` may be from BEFORE the navigation, while real Chrome resets it. Pages calling `csi()` at different points in the page lifecycle detect the discrepancy.
- **Detection**: Call `csi().startE` 5s after page load; if startE > 5s (cached), flag.

### A9. [MED] **`AbortSignal.timeout()` returns the native signal but attaches `timeStamp` to `reason` — if `reason` is already a `DOMException` (real Chrome case), our `Object.defineProperty(sig.reason, 'timeStamp', ...)` may FAIL because DOMException properties are non-configurable**
- **Location/Trigger**: `prototype_hardener.js:4471-4495` (AbortSignal.timeout override)
  ```js
  try {
      Object.defineProperty(sig.reason, 'timeStamp', {
          get: () => _capturedNow, configurable: true
      });
  } catch (e) {}
  ```
  The catch swallows the error, so the override is silently broken for real DOMException reasons. Pages reading `sig.reason.timeStamp` get `undefined`.
- **Detection**: `const sig = AbortSignal.timeout(50); setTimeout(() => log(sig.reason.timeStamp), 100);` — expected 0-50ms, ours is undefined.

### A10. [HIGH] **`new Date().getTimezoneOffset()` cache uses `tzOffsetCache.set(this, offset)` but `Date` instances are not retained by WeakMap across `.getTime()` calls on the same value — a page doing `d.getTime() === d.getTime()` (same Date) works, but `new Date(d.getTime())` (new instance) misses the cache and re-runs the expensive Intl call**
- **Location/Trigger**: `prototype_hardener.js:4810-4860` (Date.getTimezoneOffset)
  The cache is keyed on the `Date` instance (`this`). Creating a new Date with the same ms value misses. Real Chrome has no such limitation.
- **Detection**: Performance: 1000 calls to `new Date(d.getTime()).getTimezoneOffset()` — ours is 1000× Intl calls (~5-10ms each = 5-10s total), real is 1000× native (<1ms total).

---

## Category B: Canvas, Image & Visual (8 issues)

### B1. [CRIT] **`OffscreenCanvas.getContext('webgl')` and `getContext('webgl2')` are NOT wrapped** — the override at line 2597-2605 only handles generic context types, but the `getContext` override for OffscreenCanvas is missing entirely. WebGL fingerprinting via OffscreenCanvas returns RAW values.
- **Location/Trigger**: `prototype_hardener.js:2597-2605`
  ```js
  if (typeof OffscreenCanvas !== 'undefined') {
      const rawOffscreenGetContext = OffscreenCanvas.prototype.getContext;
      if (rawOffscreenGetContext) {
          safePrototypeOverride(OffscreenCanvas.prototype, 'getContext', function(type, attributes) {
              return rawOffscreenGetContext.call(this, type, attributes);
          });
      }
  }
  ```
  The override is there but doesn't pass attributes through correctly AND doesn't apply the WebGL/ANGLE renderer override. So a page creating `new OffscreenCanvas(1,1)` and calling `getContext('webgl').getParameter(37446)` returns the REAL renderer string (not our ANGLE-formatted one), bypassing the fix in `getParameter`.
- **Detection**: `const oc = new OffscreenCanvas(1,1); const gl = oc.getContext('webgl'); const ext = gl.getExtension('WEBGL_debug_renderer_info'); gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)` returns real GPU string.

### B2. [CRIT] **`CanvasRenderingContext2D.getImageData` noise uses byte-level LSB jitter, but `createImageData()` returns a new ImageData that does NOT have noise applied**
- **Location/Trigger**: `prototype_hardener.js:2110-2117` (getImageData is wrapped, but `createImageData` is not)
  A page that does `ctx.createImageData(w, h)` then `ctx.putImageData(img, 0, 0)` then `ctx.getImageData(...)` may not get noise consistently. Or: `createImageData()` returns un-noised data, leaking the underlying real GPU.
- **Detection**: `const id1 = ctx.createImageData(1,1); const id2 = ctx.getImageData(0,0,1,1); JSON.stringify([id1.data[0], id2.data[0]])` — if id1 is "raw" and id2 is "noised", flag.

### B3. [HIGH] **`ImageBitmap.close()` and `ImageBitmapRenderingContext.transferFromImageBitmap` are not properly chained** — `transferFromImageBitmap` is wrapped (line 2346-2353) but the wrapped function does nothing, while the real `transferFromImageBitmap` should add the bitmap to the context. This is a no-op mock that doesn't preserve real behavior.
- **Location/Trigger**: `prototype_hardener.js:2346-2353`
- **Detection**: `const ibm = await createImageBitmap(canvas); ctx.transferFromImageBitmap(ibm); if (!ctx.canvas.toDataURL().length) flag;`

### B4. [HIGH] **`OffscreenCanvas.convertToBlob` override is a no-op pass-through** at line 2242-2245
  ```js
  if (OffscreenCanvas.prototype.convertToBlob) {
      const ocRawConvertToBlob = OffscreenCanvas.prototype.convertToBlob;
      safePrototypeOverride(OffscreenCanvas.prototype, 'convertToBlob', function(options) {
          return ocRawConvertToBlob.call(this, options);
      });
  }
  ```
  This passes the call through to the real `convertToBlob`, which will use the OffscreenCanvas's REAL image data (no canvas noise applied for webgl contexts). For a 2D OffscreenCanvas, the noise is applied via `getImageData` (line 2122-2129), so `convertToBlob` triggers it. But for `imagebitmaprendering` context, the bitmap is not noised.
- **Detection**: Compare main canvas toDataURL hash vs OffscreenCanvas convertToBlob hash — they should match for a "noised" persona, but ours may differ for non-2d contexts.

### B5. [MED] **`Path2D.prototype.arc` jitter is text-length-dependent (`text.length % 5`)** — for a fingerprinting script that calls `arc(0, 0, 10, 0, Math.PI*2)` with a fixed text, the jitter is always the same. Real Chrome's arc has sub-pixel positioning based on anti-aliasing heuristics, not a per-text hash.
- **Location/Trigger**: `prototype_hardener.js:2284-2289`
  ```js
  safePrototypeOverride(Path2D.prototype, 'arc', function(x, y, radius, ...) {
      const jitter = seededRandom() * 0.001;  // <-- only 1 RNG call, no per-call variance
      return rawPath2DArc.call(this, x + jitter, y + jitter, radius, ...);
  });
  ```
  The jitter is a function of `seededRandom()` (deterministic, not per-call), so ALL arc calls return the SAME jitter. Pages can hash the arc output and detect the constant offset.
- **Detection**: `const p = new Path2D(); p.arc(0,0,10,0,Math.PI*2); p.arc(0,0,20,0,Math.PI*2);` — should have different centers if jitter is per-call; ours has same jitter.

### B6. [HIGH] **`Element.prototype.getBoundingClientRect` is overridden with per-element jitter, but `Element.prototype.getClientRects()` (plural) is not** — pages that use `getClientRects()` for multi-line text bounding boxes bypass the jitter.
- **Location/Trigger**: `prototype_hardener.js:2336-2375` (only getBoundingClientRect is wrapped)
- **Detection**: `el.getClientRects().length > 0` for inline elements; the rects are un-jittered.

### B7. [CRIT] **`HTMLCanvasElement.prototype.toDataURL` translate-jitter at line 2156-2190 modifies the canvas state — but the `_callJitterX/Y` is seeded by `seededRandom()` (DETERMINISTIC), not per-call random**
- **Location/Trigger**: `prototype_hardener.js:2173-2174`
  ```js
  const _callJitterX = (seededRandom() - 0.5) * 0.02;
  const _callJitterY = (seededRandom() - 0.5) * 0.02;
  ```
  `seededRandom()` is a closure-captured deterministic function. All toDataURL calls use the SAME jitter values. The first call's RNG state advances the deterministic seed; subsequent calls also use the same seeded function. Wait, actually `seededRandom` advances state per call, so different calls give different values. But the `setTransform` restore uses `_savedTransform` which only applies if a pre-existing transform exists. If the canvas has no transform, `_savedTransform` is null, and we fall through to `translate(-_callJitterX, -_callJitterY)` which is fine.
  
  But the issue is: each `toDataURL` call uses DIFFERENT jitter (good for variance), but the jitter is based on `seededRandom()` which is a SINGLETON for the whole page. So a sequence of toDataURL calls produces a SEQUENCE of jitters that is deterministic and per-page. Real Chrome's toDataURL jitter is also deterministic but per-page-load. So this is actually OK.
  
  Wait — but the `getContext('2d')` call at line 2148 is called on `this`, which may have a different `getContext` override elsewhere. Let me check: the override at line 2148-2149 does `this.getContext('2d')`, which goes through `HTMLCanvasElement.prototype.getContext` (NOT overridden). The real `getContext('2d')` returns a CanvasRenderingContext2D. The `if (ctx instanceof CanvasRenderingContext2D)` check uses `instanceof`, which is correct.
  
  However, the issue is that `getContext('2d')` may return a CACHED context, and that context's getImageData is already wrapped. So calling `ctx.getImageData(0, 0, 1, 1)` triggers the canvas noise. But the noise function uses `_sessionRandom` which is the per-session seed; the same noise is applied to every toDataURL call. So the **noise pattern is consistent across toDataURL calls** — pages that do `canvas.toDataURL(); canvas.toDataURL();` and compare see identical output. Real Chrome has sub-pixel variation between calls.
- **Detection**: hash `canvas.toDataURL() === canvas.toDataURL()` — should be `false` in real Chrome (sub-pixel variation), `true` in ours (deterministic).

### B8. [MED] **`OffscreenCanvas.getContext('2d').measureText` is wrapped (line 2218-2240), but `OffscreenCanvas.getContext('bitmaprenderer')` measureText is not — `bitmaprenderer` contexts are used for fast image blitting, and `measureText` is undefined for them but the parent's measureText is still callable on the context**
- **Location/Trigger**: `prototype_hardener.js:2597-2605` (OffscreenCanvas.getContext wrapper)
- **Detection**: `const oc = new OffscreenCanvas(100,100); const ctx = oc.getContext('bitmaprenderer'); if (ctx.measureText) flag;` — real Chrome's bitmaprenderer context doesn't have measureText.

---

## Category C: Storage & APIs (6 issues)

### C1. [CRIT] **`localStorage`, `sessionStorage`, `indexedDB` are NOT touched — a returning persona may have data from a previous session in the SAME profile (tmpfs is per-task, not per-persona)**
- **Location/Trigger**: `hardened_orchestrator.py:2807-2820` (pre-launch filesystem wipe is selective)
  The wipe removes `.git/config`, etc. but NOT `Local Storage/`, `Session Storage/`, `IndexedDB/`, `Shared Dictionary/`, etc. These are in `Default/` subdirectory and persist if a previous persona on the same task left data.
- **Detection**: `localStorage.length` for a "fresh" persona should be 0-5, but ours may be 50+ from a prior task on the same tmpfs UUID.

### C2. [HIGH] **`navigator.storage.estimate()` returns a hard-coded 500GB quota (line 4880-4887) — real Chrome on Linux returns `quota = 274877906944` (256GB) and on Windows 100GB. Pages cross-checking OS vs quota flag.**
- **Location/Trigger**: `prototype_hardener.js:4880-4887`
- **Detection**: `navigator.storage.estimate().then(r => { if (r.quota > 300e12) flag_windows_or_generic; });`

### C3. [HIGH] **`navigator.storage.persist()` returns `false` synchronously (line 4894) — but the call is async and real Chrome returns a `Promise<boolean>` that resolves after permission check (~50-200ms). Pages measuring the resolution time flag.**
- **Location/Trigger**: `prototype_hardener.js:4894-4897`
  ```js
  navigator.storage.persist = async function() { return false; };
  ```
  The `async` keyword adds a microtask delay (~1µs), but real Chrome has IPC roundtrip to the storage subsystem (~50-200ms). Our resolution is too fast.
- **Detection**: `const t = performance.now(); await navigator.storage.persist(); log(performance.now() - t);` — real is 50-200ms, ours is <1ms.

### C4. [CRIT] **`crypto.getRandomValues()` is NOT wrapped** — pages using `crypto.getRandomValues(new Uint8Array(32))` to fingerprint (by hashing the output and checking against known values) get REAL random values that may have a Chrome-specific bias or be from a non-persona seed.
- **Location/Trigger**: No override found in `prototype_hardener.js`.
- **Detection**: Hash `crypto.getRandomValues(new Uint8Array(64))` output; real Chrome has hardware-RNG distribution, ours has whatever V8's PRNG produces (no override = real).

### C5. [HIGH] **`crypto.randomUUID()` is NOT wrapped** — real Chrome returns a v4 UUID, ours returns real. Pages can use this for stable fingerprinting if the UUID is logged server-side. Also, `crypto.subtle` is not wrapped (already noted in Round 5 #5.19).
- **Location/Trigger**: No override.
- **Detection**: `crypto.randomUUID().match(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)` should match; ours matches but the underlying RNG is the real one.

### C6. [CRIT] **`CookieStore` (Async Cookies API) `delete()` and `set()` with options are not properly mocked** — the API is removed entirely (line 6233-6245) but pages that check `'cookieStore' in window` see `false`. Real Chrome 120 has it disabled by default, but pages that detect missing API via feature detection can flag.
- **Location/Trigger**: `prototype_hardener.js:6233-6245`
  Real Chrome 120: `cookieStore` is `undefined` (default off). Our delete: `safeDelete(window, 'cookieStore')`. This matches real Chrome. **However**, real Chrome exposes `'cookieStore' in window === false` only if the feature flag is off. Pages that probe `if ('cookieStore' in window) {...}` work fine. Pages that probe `window.cookieStore === undefined` (which is the same) work. But pages that probe `Object.getOwnPropertyDescriptor(window, 'cookieStore')` see our deleted descriptor vs real Chrome's "never defined" descriptor. Subtle but detectable.
- **Detection**: `Object.getOwnPropertyDescriptor(window, 'cookieStore')` — in real Chrome returns `undefined`; in ours (after safeDelete) may return a descriptor with `configurable: true`.

---

## Category D: Plugin & Detection (6 issues)

### D1. [HIGH] **`navigator.plugins` mock has 5 entries (line 2825-2830) including 'Microsoft Edge PDF Viewer' — Chrome personas should NOT have this entry; only Edge does**
- **Location/Trigger**: `prototype_hardener.js:2825-2830`
  ```js
  { name: "Microsoft Edge PDF Viewer", description: "Portable Document Format", filename: "internal-pdf-viewer" }
  ```
  Real Chrome 120 has: PDF Viewer, Chrome PDF Viewer, Chromium PDF Viewer, Native Client (4 entries). Edge has 5 (adds Microsoft Edge PDF Viewer). Our mock has 5 (correct for Edge, wrong for Chrome).
- **Detection**: `navigator.plugins.namedItem('Microsoft Edge PDF Viewer') !== null` — flags on Chrome claim.

### D2. [CRIT] **`PluginArrayMock` is constructed as a class (line 2811-2820) but the iteration via `[Symbol.iterator]()` returns `this._items[Symbol.iterator]()` — which yields the array items, but the class has no `length` field initialized at construction**
- **Location/Trigger**: `prototype_hardener.js:2811-2820`
  ```js
  class PluginArrayMock {
      constructor(items) { this._items = items; }
      item(index) { return this._items[index] || null; }
      namedItem(name) { return this._items.find(p => p.name === name) || null; }
      refresh() {}
      get length() { return this._items.length; }
      ...
  }
  ```
  `navigator.plugins.length` works (it's a getter). But `navigator.plugins instanceof PluginArray` is `false` (real Chrome's plugins is a `PluginArray`, not our `PluginArrayMock`). The class is not exported; `window.PluginArray` is undefined. Pages that check `navigator.plugins.constructor === PluginArray` flag.
- **Detection**: `navigator.plugins.constructor.name === 'PluginArrayMock'` — leaks our class name.

### D3. [HIGH] **`navigator.mimeTypes.length = 2` (line 1943) but `navigator.mimeTypes` should be empty (`length: 0`) on real Chrome 88+** — Chrome deprecated the entire mimeTypes API**
- **Location/Trigger**: `prototype_hardener.js:1943`
- **Detection**: `navigator.mimeTypes.length === 0` expected; ours returns 2.

### D4. [HIGH] **`getScreenDetails()` is wrapped (line 1452-1480) but the returned screens array has `isInternal: true` and `label: "Primary Display"` — Windows multi-monitor real Chrome sets `isInternal: false` for external monitors; even single-monitor laptops should NOT have `label: "Primary Display"` (Windows uses a vendor-specific string like "\\\\.\\DISPLAY1")**
- **Location/Trigger**: `prototype_hardener.js:1461-1462`
- **Detection**: `await window.getScreenDetails().then(sd => { if (sd.screens[0].label === 'Primary Display') flag; })`.

### D5. [MED] **`navigator.userAgentData.brands` is a frozen array (real Chrome); our mock brands array is mutable**
- **Location/Trigger**: `prototype_hardener.js:990-997`
  Pages that do `navigator.userAgentData.brands.push({brand: 'fake', version: '0'})` succeed on our mock but throw in real Chrome.
- **Detection**: `try { navigator.userAgentData.brands.push({}); flag = 'mutated'; } catch(e) { flag = 'frozen'; }` — expect 'frozen' in real.

### D6. [CRIT] **`window.chrome.cookies` mock (line 1325-1333) returns `Promise.resolve([])` for `getAll`, but the API requires `chrome.cookies` permission which is only granted in extension context. Real Chrome in a regular page has `chrome.cookies === undefined`. Our mock has it defined.**
- **Location/Trigger**: `prototype_hardener.js:1325-1333`
- **Detection**: `if (typeof chrome.cookies !== 'undefined') flag;` — real Chrome pages have `chrome.cookies === undefined`.

---

## Category E: Hardener Internals & Memory (0 issues)

### E1-E0. — None (covered in Round 5: `_ENCRYPTION_KEY` zero, panic handler, tmpfs cleanup, etc.)

---

## Category F: Detection Vectors (5 issues)

### F1. [CRIT] **`Object.freeze` is NOT applied to mocks** — `mockUAData`, `mockMimeTypes`, `_PatchedPort` (the chrome.runtime Port), and others are mutable. Real Chrome's `NavigatorUAData`, `PluginArray`, `MimeTypeArray` are frozen.
- **Location/Trigger**: Multiple — `mockUAData` at line 981, `mockMimeTypes` at line 1936, `_PatchedPort` at line 1124.
- **Detection**: `navigator.userAgentData.brands[0] = {brand: 'fake'}` — succeeds in our mock, throws in real.

### F2. [HIGH] **`Error.prototype.stack` filter list at line 144-155 contains `__sre_` and `prototype_hardener` — but the filter is a regex/substring match. Pages that construct an error message containing `'prototype_hardener'` in user code (e.g., `'prototype_hardener.js:123'`) would have their legitimate frames removed**
- **Location/Trigger**: `prototype_hardener.js:144-155`
- **Detection**: `try { throw new Error('prototype_hardener.js: line 100 in user code'); } catch(e) { if (!e.stack.includes('line 100')) flag; }` — our filter strips user-code frames matching the substring.

### F3. [HIGH] **`safePropertyOverride` uses `Object.getPrototypeOf(obj)` for instances — but for `document.cookie`, the override creates a NEW object (`cookieStore`) and bypasses the safePropertyOverride. The cookie property is on `Document.prototype`, but our override puts the descriptor directly on `document` (the instance)**
- **Location/Trigger**: `prototype_hardener.js:1622-1635`
  ```js
  Object.defineProperty(document, 'cookie', {
      get: () => cookieStore._buildString(),
      set: (val) => {...}
  });
  ```
  The descriptor is on `document` (instance), not `Document.prototype`. Real Chrome has it on `Document.prototype`. Pages that do `Document.prototype.cookie` and check the descriptor see real Chrome's getter; ours is on the instance.
- **Detection**: `const d = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie'); if (d === undefined) flag;` — real Chrome has it on prototype; ours has it on document instance.

### F4. [CRIT] **`console.warn` calls leak detection strings** — the prototype_hardener.js has 15+ `console.warn('[CB-Manifest] ...')` calls. Real Chrome doesn't have a custom console.warn pattern. Pages that override `console.warn` and capture the arguments see these strings, including the persona's manifest, the cloak_manifest values, etc.
- **Location/Trigger**: `prototype_hardener.js` — search for `console.warn`
  Examples: `console.warn('[CB-Manifest] Skipped non-configurable ${prop}')`, `console.warn('[CB-Manifest] Coordination mismatch for ${prop}: JS=${jsValue}, C++=${cloakValue}')`
- **Detection**: `(function(){ const orig = console.warn; const captured = []; console.warn = (...args) => captured.push(args); triggerSpoofMismatch(); if (captured.some(c => c[0].includes('[CB-Manifest]'))) flag; console.warn = orig; })();`

### F5. [HIGH] **`__sre_signature`, `__sre_persona_digest`, `__sre_fp_config`, `__sre_cloak_manifest`, `__sre_init_done` are all set on `window` at line ~6610** — pages that probe `window.__sre_*` see the entire configuration. Real Chrome has none of these.
- **Location/Trigger**: `prototype_hardener.js:6600-6620`
  ```js
  window.__sre_persona_digest = personaDigest;
  window.__sre_fp_config = FP_CONFIG;
  window.__sre_cloak_manifest = CB.manifest;
  window.__sre_init_done = true;
  window.__sre_signature = PERSONA_SIGNATURE;
  ```
- **Detection**: `for (const k in window) if (k.startsWith('__sre')) flag;` — real Chrome has no `__sre_*` properties.

---

## Detection-By-Vector Summary (Round 6)

- **Fingerprinting via fake-class prototype** → D2, F1 (0.5s)
- **Cross-API consistency checks** → A1, A8, A9, B7, C4, C5 (0.5-2s)
- **Timing/performance** → A10, C3, B1 (1-30s)
- **Quirks/edge cases** → A5, A7, B1, B2, B4, C2, C6, D3, D4, D6 (0.5-1s)
- **Cross-context leaks** → B1 (OffscreenCanvas), A3, A6, B6, F2 (0.5-5s)
- **Detection of automation internals** → F4, F5 (instant)

## Top 10 Critical Findings (Round 6)

1. **F5** — `window.__sre_*` properties leak entire persona config — instant
2. **F4** — `console.warn('[CB-Manifest] ...')` patterns — instant via console hooking
3. **B1** — `OffscreenCanvas.getContext('webgl')` returns real GPU renderer — bypasses ANGLE fix
4. **F1** — Mocks (userAgentData, plugins, mimeTypes) are mutable, real Chrome's are frozen
5. **D1** — `Microsoft Edge PDF Viewer` in Chrome persona's plugin list
6. **D2** — `PluginArrayMock` class name leaks via `navigator.plugins.constructor.name`
7. **D3** — `navigator.mimeTypes.length === 2` is Chrome 60-era, real Chrome 120 has 0
8. **A1** — `PerformanceObserver` lists entry types it never fires
9. **A8** — `csi().startE` cached too aggressively — doesn't update on certain navigations
10. **C1** — localStorage/sessionStorage/IndexedDB may persist from prior persona on same tmpfs

## Files Affected

- `prototype_hardener.js` (60KB+ changes) — all 30 fixes target this file
- `hardened_orchestrator.py` — 1-2 fixes (C1 storage wipe)

## Next Steps

Awaiting user confirmation to apply fixes. As before, fixes will be:
- Targeted at specific code locations
- Tested after each major group against the 115-test suite
- Applied without refactoring surrounding code
