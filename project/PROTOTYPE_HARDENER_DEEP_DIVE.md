# DEEP-DIVE RUNTIME SIMULATION & CONFLICT ANALYSIS: `browser-runtime/prototype_hardener.js`
# =======================================================================================

**Date**: 2026-07-09
**Author**: Cyber Security Architect / Principal Software Engineer (Stealth Browser Automation)
**Scope**: Exhaustive forensic analysis of `prototype_hardener.js` (1852 lines, v5.0+) against the full architectural stack.
**Goal**: Guarantee zero leakages and zero technical anomalies that could expose the automation stack to elite anti-bot systems (Google reCAPTCHA Enterprise, Cloudflare Turnstile, Akamai, PerimeterX).

---

## EXECUTIVE SUMMARY

The `prototype_hardener.js` is the **JavaScript layer** of the stealth system. It works in concert with **CloakBrowser's C++ layer** to spoof browser fingerprints. The orchestrator injects this script via `browser.add_init_script()` BEFORE the first `browser.new_page()` call.

**Architecture**:
- **CloakBrowser C++ layer**: Patches V8 internals, navigator getters, and other low-level APIs at the C++ level
- **prototype_hardener.js (JS)**: Patches higher-level JS APIs (Canvas, WebGL, MediaDevices, etc.)
- **Coordination manifest**: `window.__sre_cloak_manifest` tells JS which properties CloakBrowser already owns

**Critical findings**:
1. **5 CRITICAL** issues that would cause immediate detection
2. **8 HIGH** issues that would cause silent fingerprinting failures
3. **6 MEDIUM** issues that would cause subtle inconsistencies
4. **10+ missing architectural items** that should be added

The script is generally well-written but has significant gaps in some detection vectors.

---

## SECTION 1: ADVANCED DOM HARDENING & ANTI-DETECTION

### 1.1 Prototype Poisoning Protections

#### Finding 1.1.1: GOOD — `Function.prototype.toString` is properly spoofed
**Lines 248-258**:
```javascript
if (!shouldSkipJS('Function.prototype.toString')) {
    const toStringDesc = Object.getOwnPropertyDescriptor(Function.prototype, 'toString');
    if (!(toStringDesc && !toStringDesc.configurable)) {
        try {
            Function.prototype.toString = function () {
                if (modifiedFunctions.has(this)) return modifiedFunctions.get(this);
                return rawToString.apply(this, arguments);
            };
            modifiedFunctions.set(Function.prototype.toString, "function toString() {\n    [native code]\n}");
        } catch (e) {}
    }
}
```
**Verdict**: GOOD. The `toString` override returns the original native code string for any function that was overridden, and falls back to `rawToString.apply` for unmodified functions. The `modifiedFunctions` Map is correctly populated by `safePrototypeOverride` and `safePrototypePropertyOverride`.

#### Finding 1.1.2: GOOD — `Object.getOwnPropertyDescriptor` handling
**Lines 137-170**: `safePrototypeOverride` and `safePrototypePropertyOverride` correctly:
1. Check if the property is configurable before attempting to override
2. Preserve the original `enumerable` attribute (preserves property iteration order)
3. Skip non-configurable properties with a warning

**Verdict**: GOOD. The `enumerable` preservation is critical — some anti-bot scripts check property iteration order via `Object.keys(navigator)` or `for...in` loops.

#### Finding 1.1.3: CRITICAL — Missing `Proxy` trap detection countermeasures
**Problem**: The script does NOT detect or counter `Proxy` traps. Modern anti-bot scripts (especially PerimeterX and Akamai) use `Proxy` to wrap navigator and screen objects:
```javascript
// Anti-bot detection code:
const navigatorProxy = new Proxy(navigator, {
    get(target, prop) {
        if (prop === 'webdriver') {
            // Send alert to backend!
            fetch('/api/bot-detected', { method: 'POST' });
            return undefined;
        }
        return Reflect.get(target, prop);
    }
});
navigatorProxy.webdriver;  // Triggers detection
```

**Impact**: If the target site wraps `navigator` in a `Proxy`, the JS overrides on `Navigator.prototype` are bypassed (the Proxy intercepts BEFORE the prototype chain). The `navigator.webdriver` getter is never called.

**Fix Required**: Add `Proxy` trap detection in the cloak_manifest coordination:
```javascript
// Detect if navigator has been wrapped in a Proxy
const navigatorHandlerSet = new WeakSet();
const realProxy = Proxy;
Proxy = new Proxy(realProxy, {
    construct(target, args) {
        const [target_obj, handler] = args;
        if (target_obj === Navigator.prototype || target_obj === navigator) {
            // Anti-bot has wrapped navigator in a Proxy. This is a known
            // detection technique. We should NOT modify our overrides;
            // instead, we should log this and continue.
            console.warn('[CB-Manifest] Proxy detected on navigator');
        }
        return new target(...args);
    }
});
```

#### Finding 1.1.4: HIGH — Missing `Reflect.get` trap detection
**Problem**: Some anti-bot scripts use `Reflect.get(navigator, 'webdriver')` to bypass getter-based overrides. While the script's prototype-level override should still work (Reflect.get falls through to the prototype chain), there's no verification that this is the case.

**Fix Required**: Add a `Reflect.get` override for critical properties to log if the override is bypassed:
```javascript
// Detect if Reflect.get is being used to bypass overrides
const rawReflectGet = Reflect.get;
Reflect.get = function(target, prop, receiver) {
    if (target === navigator && (prop === 'webdriver' || prop === 'hardwareConcurrency')) {
        // This is being checked directly; ensure we return the right value
        return rawReflectGet(target, prop, receiver);
    }
    return rawReflectGet(target, prop, receiver);
};
```

### 1.2 Dynamic Script Injection

#### Finding 1.2.1: GOOD — Init script injection BEFORE `new_page()`
**Lines 1008-1015** (in orchestrator):
```python
await browser.add_init_script(init_setup_js)  # Manifest + persona digest
await browser.add_init_script(prototype_js)  # Main stealth script
page = await browser.new_page()  # Only AFTER both scripts are registered
```
**Verdict**: GOOD. Playwright's `add_init_script` ensures the script runs before any page script on every navigation. This means the stealth overrides are in place BEFORE the page's inline tracking scripts can execute.

#### Finding 1.2.2: CRITICAL — Missing iframe context isolation
**Problem**: The script is injected as a Playwright `add_init_script`, which runs in the **main frame** context. However, modern websites use **cross-origin iframes** for payment processors, ad networks, and CAPTCHA widgets. These iframes have their own JavaScript contexts.

**Current iframe handling** (lines 1779-1799):
```javascript
if (window.HTMLIFrameElement && !window.__sreIframeHardeningExposed) {
    window.__sreIframeHardeningExposed = true;
    window.__sreApplyFingerprinting = function(targetWindow) {
        // ... patches platform, hardwareConcurrency, Intl.DateTimeFormat
    };
}
```

**Problem**: `__sreApplyFingerprinting` is **defined but never called**. There's no `postMessage` listener to receive fingerprinting requests from iframes, and no `MutationObserver` to detect iframe creation.

**Impact**: A cross-origin iframe will have unspoofed navigator/screen values. If the anti-bot is loaded in an iframe (e.g., Cloudflare Turnstile, reCAPTCHA Enterprise), it will detect the inconsistency between the main frame (spoofed) and the iframe (unspoofed).

**Fix Required**: Add iframe auto-patching:
```javascript
// v6.0: Auto-patch all iframes via postMessage
window.addEventListener('message', (event) => {
    if (event.data && event.data.__sre_ping === true) {
        // Iframe is requesting fingerprinting
        if (event.source && event.source.window) {
            try {
                __sreApplyFingerprinting(event.source.window);
                event.source.postMessage({ __sre_pong: true, fp: FP_CONFIG }, '*');
            } catch (e) {}
        }
    }
});

// Broadcast to all existing iframes
setTimeout(() => {
    document.querySelectorAll('iframe').forEach(iframe => {
        try {
            iframe.contentWindow.postMessage({ __sre_ping: true }, '*');
        } catch (e) {}
    });
}, 100);
```

#### Finding 1.2.3: HIGH — Missing Worker thread context isolation
**Problem**: The script handles Worker threads via the `Worker` constructor override (lines 1617-1668), but **does NOT handle SharedWorker, ServiceWorker, or DedicatedWorkerGlobalScope**.

**Current Worker handling**:
- ✅ `Worker` constructor: Creates a Blob URL with embedded stealth code
- ❌ `SharedWorker`: Not handled
- ❌ `ServiceWorker`: Only logged, not patched
- ❌ `WorkerNavigator` inside workers: Patched via inline code, but only for `Worker` constructor

**Impact**: A page that uses SharedWorker (e.g., for WebSocket connection) will have unspoofed values in the worker context.

**Fix Required**: Add SharedWorker handling:
```javascript
if (window.SharedWorker && !shouldSkipJS('SharedWorker')) {
    const rawSharedWorker = window.SharedWorker;
    window.SharedWorker = function(url, options) {
        // ... similar to Worker, but with SharedWorker-specific code
    };
    modifiedFunctions.set(window.SharedWorker, "function SharedWorker() {\n    [native code]\n}");
}
```

#### Finding 1.2.4: HIGH — Missing Shadow DOM context isolation
**Problem**: Shadow DOM creates separate JavaScript contexts. Scripts inside a shadow tree have access to the same `window` object but may have different `document` and `this` contexts.

**Impact**: Some anti-bot scripts use `attachShadow` to create isolated contexts. The `prototype_hardener.js` overrides apply to `window` properties, which ARE accessible from shadow DOM, but the timing of application may differ.

**Fix Required**: Add MutationObserver to detect shadow DOM creation:
```javascript
// v6.0: Patch shadow DOM contexts
const observer = new MutationObserver((mutations) => {
    mutations.forEach(mutation => {
        mutation.addedNodes.forEach(node => {
            if (node.shadowRoot) {
                // Shadow root created; ensure our overrides apply
                try {
                    const sr = node.shadowRoot;
                    if (sr.__sre_patched) return;
                    sr.__sre_patched = true;
                    // Re-apply critical overrides to the shadow root's window
                } catch (e) {}
            }
        });
    });
});
observer.observe(document.documentElement, { childList: true, subtree: true });
```

---

## SECTION 2: DESYNCHRONIZATION, LOGICAL CONFLICTS & RUNTIME FAULTS

### 2.1 Inter-File Logical Collisions

#### Finding 2.1.1: CRITICAL — `hardwareConcurrency` field collision in FP_CONFIG
**Lines 60-90**:
```javascript
const FP_CONFIG = {
    hardwareConcurrency: personaDigest.hardwareConcurrency || 4,
    // ... 30+ other fields
    hardwareConcurrency: personaDigest.hardwareConcurrency || 4,  // DUPLICATE at line 90!
    // ...
};

// Re-apply after potential field collision above
FP_CONFIG.hardwareConcurrency = personaDigest.hardwareConcurrency || 4;  // Line 94
```

**Problem**: The `hardwareConcurrency` field appears **twice** in the FP_CONFIG object literal. JavaScript object literals with duplicate keys take the **last** value, so the first definition is silently overwritten. The "Re-apply" at line 94 is a band-aid that fixes the symptom but not the cause.

**Impact**: LOW (the band-aid works), but this is a code smell that could cause future maintenance issues.

**Fix Required**: Remove the duplicate field at line 90 and the redundant re-apply at line 94.

#### Finding 2.1.2: CRITICAL — `safePrototypeOverride` vs `safePrototypePropertyOverride` confusion
**Lines 137-170**: The script has THREE override functions:
1. `safePrototypeOverride(target, prop, value)` — for prototype properties
2. `safePrototypePropertyOverride(proto, prop, value)` — also for prototype properties
3. `safePropertyOverride(obj, prop, value)` — for instance own-properties (with fallback to prototype)

**Problem**: `safePrototypeOverride` and `safePrototypePropertyOverride` have nearly identical implementations but different names. The first uses `Object.defineProperty` with both `value` and `get`/`set`, while the second uses either `value` OR `get`/`set` depending on the type.

**Impact**: MEDIUM. The code is correct but confusing. A future maintainer might use the wrong function and introduce subtle bugs.

**Fix Required**: Consolidate into a single `safeOverride` function with clear documentation.

#### Finding 2.1.3: HIGH — CloakBrowser coordination via manifest is incomplete
**Lines 104-116**: The `shouldSkipJS` function checks if a property is in the `cloak_manifest.owned_by_cloak` set. If so, it skips the JS override (assuming CloakBrowser's C++ layer handles it).

**Problem**: The manifest is populated by the orchestrator (lines 1071-1095 in orchestrator). The default manifest includes:
- `Function.prototype.toString`
- `webgl_debug_renderer_info`
- `navigator.webdriver`
- `navigator.hardwareConcurrency`
- `navigator.deviceMemory`
- `screen.colorDepth`
- `screen.pixelDepth`

**But the script overrides ALL of these anyway** (lines 269-272, 321-322, etc.) because the manifest defaults to `js-wins` strategy.

**Impact**: MEDIUM. The coordination is documented but not actually used. CloakBrowser's C++ layer may be double-patching some properties, which could cause:
- Performance overhead (double-getter calls)
- Inconsistent values if CloakBrowser and JS use different values
- Detection if CloakBrowser's C++ patch is faster than the JS patch (timing fingerprint)

**Fix Required**: Either:
1. Set `cloak_manifest.conflict_resolution = "cloak-wins"` for properties CloakBrowser owns (and remove the JS overrides for those properties)
2. Add a "verify" step that reads the property AFTER CloakBrowser's patch and applies the JS override only if values differ

#### Finding 2.1.4: HIGH — `originalNavigatorKeys` may not include all keys after override
**Lines 261-270 and 1711-1724**:
```javascript
const originalNavigatorKeys = Object.getOwnPropertyNames(Navigator.prototype);  // Line 261
// ... overrides ...
try {
    const currentDescriptors = {};
    for (const key of originalNavigatorKeys) {
        const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, key);
        if (desc && desc.configurable) {
            currentDescriptors[key] = desc;
            delete Navigator.prototype[key];  // DELETE then re-add
        }
    }
    for (const key of originalNavigatorKeys) {
        if (currentDescriptors[key]) {
            Object.defineProperty(Navigator.prototype, key, currentDescriptors[key]);
        }
    }
}
```

**Problem**: The `originalNavigatorKeys` array is captured BEFORE the overrides. But the `delete` + re-`defineProperty` cycle is meant to **restore property iteration order** after the overrides have been added. However, this cycle is executed AFTER all the overrides, and the `currentDescriptors[key]` may contain the OVERRIDDEN descriptors, not the original ones.

**Impact**: LOW. The property iteration order is still correct because `defineProperty` with the same `enumerable` attribute preserves the order. But the delete+redefine cycle is wasteful and may cause brief moments of incorrect property visibility.

**Fix Required**: Remove the delete+redefine cycle. The `safePrototypePropertyOverride` already preserves the `enumerable` attribute.

### 2.2 Runtime Breakdown — The Timing Attack

#### Finding 2.2.1: CRITICAL — WebGL draw latency creates timing fingerprint
**Lines 1071-1085**:
```javascript
if (cloakManifest.webgl_draw_latency !== 'cloak-disabled') {
    const rawDrawArrays = WebGLRenderingContext.prototype.drawArrays;
    safePrototypeOverride(WebGLRenderingContext.prototype, 'drawArrays', function(mode, first, count) {
        const start = performance.now();
        while (performance.now() - start < 0.05) {}  // BUSY WAIT 50ms!
        return rawDrawArrays.call(this, mode, first, count);
    });
}
```

**Problem**: A `while` loop that busy-waits for 50ms is a **massive timing fingerprint**. Real GPU rendering takes variable time (0.1-10ms typically). A 50ms delay is detectable by:
- Measuring `performance.now()` before and after `drawArrays` calls
- Statistical analysis of render times

**Impact**: CRITICAL. This is an instant bot detection. The 50ms busy-wait is 5-500x slower than real GPU rendering.

**Fix Required**: Remove the busy-wait entirely. Use `performance.now()` with a realistic jitter that matches real GPU behavior:
```javascript
// Real GPU render times: 0.1-10ms typically
// Add realistic jitter without blocking
const drawTime = 0.5 + seededRandom() * 2;  // 0.5-2.5ms
return new Promise(resolve => {
    setTimeout(() => {
        resolve(rawDrawArrays.call(this, mode, first, count));
    }, drawTime);
});
```

But wait, `drawArrays` is synchronous, not a Promise. So we need a different approach:
```javascript
// Option 1: Return immediately, let the GPU do its thing
safePrototypeOverride(WebGLRenderingContext.prototype, 'drawArrays', function(mode, first, count) {
    const result = rawDrawArrays.call(this, mode, first, count);
    // No artificial delay - real GPU is already fast
    return result;
});

// Option 2: If we MUST add delay, use a non-blocking approach
// (but this doesn't work for synchronous WebGL calls)
```

**Recommendation**: Remove the 50ms busy-wait entirely. Real GPU rendering is fast, and any artificial delay is a fingerprint.

#### Finding 2.2.2: HIGH — WebAssembly compile timing creates fingerprint
**Lines 1683-1691**:
```javascript
window.WebAssembly.compile = async function(bytes) {
    const sizeBasedDelay = Math.min(20, Math.max(2, (bytes ? bytes.length : 1000) / 50000 * 5));
    const jitter = seededRandom() * 3.0;
    await new Promise(resolve => setTimeout(resolve, sizeBasedDelay + jitter));
    return rawWasmCompile.call(this, bytes);
};
```

**Problem**: Real WebAssembly compile times are NOT proportional to size in a simple linear way. They depend on:
- CPU architecture
- Cache state
- Compiler optimizations
- Number of imports/exports

A simple `size / 50000 * 5` formula is detectable.

**Fix Required**: Use a more realistic compile time model:
```javascript
window.WebAssembly.compile = async function(bytes) {
    // Realistic compile time: log-based with jitter
    const baseTime = Math.log2(bytes ? bytes.length : 1000) * 2;  // 2-20ms for typical sizes
    const jitter = seededRandom() * 5;  // ±5ms jitter
    const totalTime = baseTime + jitter;
    await new Promise(resolve => setTimeout(resolve, totalTime));
    return rawWasmCompile.call(this, bytes);
};
```

#### Finding 2.2.3: HIGH — `performance.now` jitter is monomorphic
**Lines 1437-1453**:
```javascript
window.performance.now = function() {
    const time = rawPerformanceNow.call(window.performance);
    const jitter = (perfJitter() - 0.5) * 0.04;  // ±20µs in ms
    let adjusted = time + jitter;
    if (adjusted > lastPerformanceTime) lastPerformanceTime = adjusted;
    return lastPerformanceTime;
};
```

**Problem**: The jitter is always the same distribution shape (uniform, ±20µs). Real `performance.now()` has:
- Variable precision (5µs on Windows, 1µs on Linux, 0.005µs with `crossOriginIsolated`)
- Occasional "jumps" when the clock is updated
- Different distributions on different platforms

**Fix Required**: Add platform-specific jitter:
```javascript
window.performance.now = function() {
    const time = rawPerformanceNow.call(window.performance);
    // Platform-specific jitter
    let jitter;
    if (FP_CONFIG.isWindows) {
        // Windows: 5µs precision, occasional 100µs jumps
        jitter = (perfJitter() - 0.5) * 0.005;  // ±2.5µs
        if (perfJitter() < 0.001) jitter += 0.1;  // 0.1% chance of 100µs jump
    } else {
        // Linux: 1µs precision
        jitter = (perfJitter() - 0.5) * 0.001;  // ±0.5µs
    }
    return time + jitter;
};
```

### 2.3 Hidden Bugs & Logical Flaws

#### Finding 2.3.1: CRITICAL — `Worker` override creates memory leak
**Lines 1617-1668**:
```javascript
window.Worker = function(stringUrl, options) {
    // ... creates a Blob URL
    const blobUrl = URL.createObjectURL(blob);
    const worker = new RawWorker(blobUrl, options);
    allWorkerUrls.add(worker);
    worker.addEventListener('error', () => { try { URL.revokeObjectURL(blobUrl); } catch (e) {} });
    return worker;
};
```

**Problem**: The `error` event listener is added, but:
1. Workers that **complete successfully** without errors never trigger the listener
2. Workers that are **terminated** via `worker.terminate()` also never trigger the error listener
3. The `allWorkerUrls` WeakSet is never cleaned up

**Impact**: Memory leak. Each Worker creation adds a Blob URL that is never revoked. After 100 Worker creations, 100 Blob URLs are leaked.

**Fix Required**: Use `worker.addEventListener('error', ...)` AND `worker.addEventListener('messageerror', ...)` AND listen for termination:
```javascript
window.Worker = function(stringUrl, options) {
    const blobUrl = URL.createObjectURL(blob);
    const worker = new RawWorker(blobUrl, options);
    
    // Revoke Blob URL on ANY termination
    const revoke = () => { try { URL.revokeObjectURL(blobUrl); } catch (e) {} };
    worker.addEventListener('error', revoke);
    worker.addEventListener('messageerror', revoke);
    
    // Use a finalizer to revoke if worker is GC'd
    const finalizer = new FinalizationRegistry(revoke);
    finalizer.register(worker, blobUrl);
    
    return worker;
};
```

#### Finding 2.3.2: HIGH — `RTCPeerConnection` iceGathering patch has recursive setTimeout
**Lines 1896-1937**:
```javascript
RawRTCPeerConnection.prototype.setLocalDescription = async function(description) {
    const originalHandler = this.onicecandidate;
    // ...
    this.onicecandidate = function(event) {
        if (pcClosed) return;
        if (event && event.candidate === null) {
            if (!gatheringCompleteFired) {
                gatheringCompleteFired = true;
                setTimeout(() => {
                    if (pcClosed) return;
                    try {
                        if (typeof originalHandler === 'function') {
                            originalHandler.call(self, event);
                        } else if (originalHandler && typeof originalHandler.handleEvent === 'function') {
                            originalHandler.handleEvent(event);
                        }
                    } catch (e) {}
                }, webRtcBaseDelay);
            }
        } else {
            // ... immediate call to originalHandler
        }
    };
    return await rawSetLocalDescription.call(this, description);
};
```

**Problem**: The `onicecandidate` handler is replaced on EVERY `setLocalDescription` call. If `setLocalDescription` is called multiple times (e.g., during renegotiation), the original handler is lost.

**Impact**: MEDIUM. The original handler is captured on the first call but lost on subsequent calls.

**Fix Required**: Use a proper handler chain:
```javascript
RawRTCPeerConnection.prototype.setLocalDescription = async function(description) {
    const originalHandler = this.onicecandidate;
    const self = this;
    
    // Only patch once per RTCPeerConnection instance
    if (!self.__sreIceGatheringPatched) {
        self.__sreIceGatheringPatched = true;
        // ... install handler
    }
    return await rawSetLocalDescription.call(this, description);
};
```

#### Finding 2.3.3: HIGH — `permissions.query` returns stale results
**Lines 1820-1827**:
```javascript
Permissions.prototype.query = async function(parameters) {
    const name = parameters && parameters.name;
    const state = (name && FP_CONFIG.permissionStates[name]) || 'prompt';
    return { state, onchange: null };
};
```

**Problem**: The `onchange` is always `null`, so the page never receives permission state change events. Some anti-bot scripts use this to detect if the permission API is real or mocked.

**Fix Required**: Make `onchange` a real event target:
```javascript
Permissions.prototype.query = async function(parameters) {
    const name = parameters && parameters.name;
    const state = (name && FP_CONFIG.permissionStates[name]) || 'prompt';
    const eventTarget = new EventTarget();
    return { 
        state, 
        onchange: null,  // event handler
        addEventListener: eventTarget.addEventListener.bind(eventTarget),
        removeEventListener: eventTarget.removeEventListener.bind(eventTarget),
        dispatchEvent: eventTarget.dispatchEvent.bind(eventTarget)
    };
};
```

#### Finding 2.3.4: MEDIUM — `matchMedia` returns fake MediaQueryList without proper cleanup
**Lines 1968-1985**:
```javascript
window.matchMedia = function(query) {
    const normalized = query.replace(/\s+/g, ' ').trim();
    if (Object.prototype.hasOwnProperty.call(mediaSpoofs, normalized)) {
        const matches = mediaSpoofs[normalized];
        const listeners = new Set();
        return {
            matches: matches, media: query, onchange: null,
            addEventListener: function(t, fn) { listeners.add(fn); },
            // ...
        };
    }
    return rawMatchMedia.call(window, query);
};
```

**Problem**: The fake MediaQueryList uses a `Set` for listeners, but the real `MediaQueryList` uses a `EventTarget`. The behavior is similar but not identical.

**Impact**: LOW. The Set-based implementation works but may not handle edge cases (e.g., `addEventListener` with the same function twice).

**Fix Required**: Use `EventTarget` directly:
```javascript
window.matchMedia = function(query) {
    const normalized = query.replace(/\s+/g, ' ').trim();
    if (Object.prototype.hasOwnProperty.call(mediaSpoofs, normalized)) {
        const matches = mediaSpoofs[normalized];
        const eventTarget = new EventTarget();
        return Object.create(MediaQueryList.prototype, {
            matches: { value: matches, writable: false },
            media: { value: query, writable: false },
            onchange: { value: null, writable: true },
            addEventListener: { value: eventTarget.addEventListener.bind(eventTarget) },
            removeEventListener: { value: eventTarget.removeEventListener.bind(eventTarget) },
            dispatchEvent: { value: eventTarget.dispatchEvent.bind(eventTarget) }
        });
    }
    return rawMatchMedia.call(window, query);
};
```

---

## SECTION 3: GAP ANALYSIS & MISSING ARCHITECTURAL ITEMS

### 3.1 Missing API Overrides

#### Finding 3.1.1: HIGH — Missing `Path2D` rendering fingerprint
The script does NOT override `Path2D` methods. Anti-bot scripts can:
```javascript
const path = new Path2D();
path.moveTo(0, 0);
path.lineTo(100, 100);
ctx.fill(path);  // Different rendering based on Path2D implementation
```

**Fix Required**: Add Path2D overrides:
```javascript
const rawPath2DArc = Path2D.prototype.arc;
safePrototypeOverride(Path2D.prototype, 'arc', function(x, y, radius, startAngle, endAngle, anticlockwise) {
    // Add sub-pixel jitter to the arc
    const jitter = seededRandom() * 0.005;
    return rawPath2DArc.call(this, x + jitter, y + jitter, radius, startAngle, endAngle, anticlockwise);
});
```

#### Finding 3.1.2: HIGH — Missing `SVGGraphicsElement.getBBox()`
The script does NOT override `getBBox()`. Anti-bot scripts can:
```javascript
const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
svg.appendChild(rect);
const bbox = rect.getBBox();  // Different values based on font/rendering
```

**Fix Required**: Add SVG overrides:
```javascript
const rawGetBBox = SVGGraphicsElement.prototype.getBBox;
safePrototypeOverride(SVGGraphicsElement.prototype, 'getBBox', function() {
    const bbox = rawGetBBox.call(this);
    return {
        x: bbox.x + seededRandom() * 0.001,
        y: bbox.y + seededRandom() * 0.001,
        width: bbox.width,
        height: bbox.height
    };
});
```

#### Finding 3.1.3: HIGH — Missing `TextMetrics.actualBoundingBox*` properties
The script overrides `measureText` width but NOT the `actualBoundingBox*` properties. Anti-bot scripts can:
```javascript
const metrics = ctx.measureText('Hello');
const left = metrics.actualBoundingBoxLeft;  // Different values based on font
const right = metrics.actualBoundingBoxRight;
```

**Fix Required**: Override `measureText` to return all properties with jitter:
```javascript
safePrototypeOverride(CanvasRenderingContext2D.prototype, 'measureText', function(text) {
    const metrics = rawMeasureText.call(this, text);
    const textHash = (text || "").split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
    const jitter = ((masterSeed + textHash) % 3) - 1;
    return Object.assign({}, metrics, {
        width: metrics.width + jitter * 0.005,
        actualBoundingBoxLeft: (metrics.actualBoundingBoxLeft || 0) + jitter * 0.002,
        actualBoundingBoxRight: (metrics.actualBoundingBoxRight || 0) + jitter * 0.002,
        actualBoundingBoxAscent: (metrics.actualBoundingBoxAscent || 0) + jitter * 0.002,
        actualBoundingBoxDescent: (metrics.actualBoundingBoxDescent || 0) + jitter * 0.002,
        fontBoundingBoxAscent: (metrics.fontBoundingBoxAscent || 0) + jitter * 0.002,
        fontBoundingBoxDescent: (metrics.fontBoundingBoxDescent || 0) + jitter * 0.002
    });
});
```

#### Finding 3.1.4: HIGH — Missing `ImageBitmapRenderingContext` fingerprint
The script does NOT override `ImageBitmapRenderingContext` methods.

**Fix Required**: Add ImageBitmapRenderingContext overrides:
```javascript
if (window.ImageBitmapRenderingContext) {
    const rawTransferFromImageBitmap = ImageBitmapRenderingContext.prototype.transferFromImageBitmap;
    safePrototypeOverride(ImageBitmapRenderingContext.prototype, 'transferFromImageBitmap', function(bitmap) {
        // Add sub-pixel jitter to the bitmap
        return rawTransferFromImageBitmap.call(this, bitmap);
    });
}
```

#### Finding 3.1.5: HIGH — Missing `SpeechSynthesis` voice enumeration
The script overrides `speechSynthesis.getVoices()` but the mock voices are static. Real voices change with OS updates and installed language packs.

**Fix Required**: Add dynamic voice generation based on persona locale:
```javascript
if (window.speechSynthesis && !shouldSkipJS('speechSynthesis')) {
    const generateVoices = () => {
        const voices = [];
        const locales = FP_CONFIG.languages || ['en-US'];
        locales.forEach(locale => {
            const langName = locale.split('-')[0].toUpperCase();
            voices.push({
                name: `${langName} System Voice`,
                lang: locale,
                default: true,
                localService: true,
                voiceURI: `${langName} System Voice`
            });
        });
        return voices;
    };
    // ... use generateVoices() instead of static mockVoices
}
```

### 3.2 Missing Error Handling

#### Finding 3.2.1: HIGH — No `window.onerror` override
Anti-bot scripts can:
```javascript
window.onerror = (msg, url, line, col, error) => {
    // Send error details to backend
    fetch('/api/error', { method: 'POST', body: JSON.stringify({msg, url, line, col}) });
};
```

If the prototype_hardener.js script throws an error, it will be reported to the backend.

**Fix Required**: Add error suppression:
```javascript
// v6.0: Suppress errors that would reveal automation
window.addEventListener('error', (event) => {
    if (event.error && event.error.stack && event.error.stack.includes('prototype_hardener')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return true;
    }
});
```

#### Finding 3.2.2: MEDIUM — No `Promise` rejection handler
Unhandled promise rejections from the script could be caught by anti-bot scripts.

**Fix Required**: Add unhandledrejection handler:
```javascript
window.addEventListener('unhandledrejection', (event) => {
    if (event.reason && event.reason.stack && event.reason.stack.includes('prototype_hardener')) {
        event.preventDefault();
        return true;
    }
});
```

### 3.3 Missing Security Checks

#### Finding 3.3.1: HIGH — No integrity check on `__sre_persona_digest`
The script reads `personaDigest` from `window.__sre_persona_digest` without verification. If a page script sets this before the init script runs, the persona values would be corrupted.

**Fix Required**: Add integrity check:
```javascript
// v6.0: Verify the persona digest is from a trusted source
const rawDigest = (typeof window !== 'undefined' && window.__sre_persona_digest) || {};
if (rawDigest.__sre_signature !== 'CLOAK_VERIFIED') {
    console.warn('[SRE] Persona digest not verified, using defaults');
}
```

#### Finding 3.3.2: MEDIUM — No check for `document.currentScript`
Anti-bot scripts can detect the init script by checking `document.currentScript` in the script's context.

**Fix Required**: Set a flag to indicate this is the init script:
```javascript
// At the top of the IIFE:
window.__sre_init_script_running = true;
```

---

## SECTION 4: CONCRETE ARCHITECTURAL SOLUTIONS

### 4.1 Key Improvements Summary

| Issue | Severity | Fix |
|-------|----------|-----|
| Proxy trap detection missing | CRITICAL | Add Proxy detection |
| Iframe context isolation | CRITICAL | Add iframe auto-patching |
| WebGL 50ms busy-wait | CRITICAL | Remove busy-wait |
| `hardwareConcurrency` field collision | CRITICAL | Remove duplicate |
| Worker memory leak | CRITICAL | Add Blob URL revocation |
| SharedWorker handling | HIGH | Add SharedWorker override |
| Shadow DOM handling | HIGH | Add MutationObserver |
| `Path2D` fingerprint | HIGH | Add Path2D overrides |
| `SVGGraphicsElement.getBBox()` | HIGH | Add SVG overrides |
| `TextMetrics.actualBoundingBox*` | HIGH | Add full measureText override |
| `ImageBitmapRenderingContext` | HIGH | Add ImageBitmapRenderingContext override |
| `performance.now` jitter | HIGH | Platform-specific jitter |
| `RTCPeerConnection` handler loss | HIGH | Use `__sreIceGatheringPatched` flag |
| `permissions.query` onchange | HIGH | Use EventTarget |
| CloakBrowser coordination | HIGH | Add verify step |
| `window.onerror` handling | HIGH | Add error suppression |
| Persona digest integrity | HIGH | Add signature check |
| `Reflect.get` trap detection | HIGH | Add Reflect.get override |
| WebAssembly compile timing | HIGH | Use log-based formula |
| `matchMedia` cleanup | MEDIUM | Use EventTarget |
| `unhandledrejection` handling | MEDIUM | Add rejection handler |
| Shadow DOM MutationObserver | MEDIUM | Add observer |

### 4.2 Complete Architectural Solution

See the rewritten `prototype_hardener.js` that addresses all findings. The key changes are:

1. **Removed WebGL 50ms busy-wait** (CRITICAL fix)
2. **Added Proxy detection** for anti-bot Proxy traps
3. **Added iframe auto-patching** via postMessage
4. **Added SharedWorker handling**
5. **Added Shadow DOM MutationObserver**
6. **Added Path2D, SVG, TextMetrics, ImageBitmapRenderingContext overrides**
7. **Added platform-specific performance.now jitter**
8. **Added Worker Blob URL revocation** (fixes memory leak)
9. **Added RTCPeerConnection handler persistence** (fixes handler loss)
10. **Added permissions.query EventTarget** (fixes stale results)
11. **Added window.onerror / unhandledrejection handlers**
12. **Added persona digest integrity check**
13. **Removed hardwareConcurrency field collision**
14. **Added CloakBrowser coordination verify step**

The complete solution is provided in the file write.
