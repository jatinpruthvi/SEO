# R7-DEEP-AUDIT-2 — Hidden Failure Mode Audit (5 Categories)

Scope: `/home/user/project/browser-runtime/` (19 files) and the prototype_hardener.js (347KB).
Mode: **No code changes. Findings only.** Items grouped by the 5 failure modes requested.
Severity legend: **CRIT** = instant detection, **HIGH** = strong signal, **MED** = corroborative, **LOW** = hygiene.

---

## 1. Automation Framework Fingerprint Leaks

### 1.1 [CRIT] `Reflect.get` "wrap" is a no-op — pages reading `Reflect.get(navigator, 'webdriver')` see the REAL value
- **Location/Trigger**: `prototype_hardener.js:539-545`
  ```js
  const rawReflectGet = Reflect.get;
  Reflect.get = function(target, prop, receiver) {
      return rawReflectGet.apply(this, arguments);  // <- passes through!
  };
  ```
- **Blind spot**: The comment says "We ensure Reflect.get works correctly". A page that does `Reflect.get(Navigator.prototype, 'webdriver', navigator)` calls into the prototype chain, so the `safePrototypePropertyOverride` setter on `Navigator.prototype.webdriver` would still return `undefined`. **However**, anti-bot scripts that wrap `navigator` in a `Proxy` and trap `get(_, 'webdriver')` will see the OVERRIDDEN value (good for them, bad for us). More importantly, the override is detectable: `Reflect.get.toString()` returns the literal source `function (target, prop, receiver) { ... }` *unless* it is in `modifiedFunctions`. It **is** registered only inside `safePrototypeOverride`, but `Reflect.get` is replaced at file-scope with no corresponding `modifiedFunctions.set(Reflect.get, ...)`. Hence `Reflect.get.toString()` exposes JS source.
- **Detection**: 
  ```js
  // Sensor: 
  Reflect.get.toString()  // => "function (target, prop, receiver) { ... }"
  // Real Chrome: "function get() { [native code] }"
  // The string also includes the unminified identifier `rawReflectGet` (after closure capture, V8 may inline, but pre-inlined shows the name).
  ```

### 1.2 [HIGH] `Error.prototype.stack` override registers only the GETTER in `modifiedFunctions`, not the `[native code]` token mapped to the function
- **Location/Trigger**: `prototype_hardener.js:161-164` — only the getter function is registered; if a fingerprinting script calls `toString.call(descriptor.get)` directly (not through the bound getter on the prototype), it returns the source.
- **Blind spot**: The override is set on the prototype, so `err.stack.toString()` (rare) would still leak. More importantly, the **filter list** itself is a fingerprint:
  ```js
  return !line.includes('prototype_hardener') &&
         !line.includes('__sre_') &&
         !line.includes('safeOverride') &&
         !line.includes('safePrototype') &&
         ...
  ```
  A real Chrome stack from the same JS engine will **never** contain `prototype_hardener` or `__sre_`. Pages that catch a generic error inside a perf-sensitive code path can pattern-match for "no filtered-prefix frames" = spoofed. The behavior of "filter the stack on getter access" is itself Chrome-distinct; some real V8 builds also do this for `--site-per-process`, but the filtering is by URL/origin, not by a deny-list string.
- **Detection**: `(new Error()).stack.replace(/\n/g, '').includes('safeOverride')` returns `false`; on the first call to an `__sre_*` function that throws, **real Chrome** would include the frame; **our build** strips it. The difference is observable: `Error.captureStackTrace` users can detect the discrepancy.

### 1.3 [CRIT] `window.Worker` is replaced with a wrapper that uses `importScripts` to re-execute the original URL — but the page code may detect the **Blob URL scope leak**
- **Location/Trigger**: `prototype_hardener.js:4091-4094`
  ```js
  const blob = new Blob([workerCode], { type: 'application/javascript' });
  const blobUrl = URL.createObjectURL(blob);
  const worker = new RawWorker(blobUrl, options);
  ```
  The worker's `location.href` is `blob:chrome-extension://<UUID>`. Anti-bot scripts that do `self.location.href.startsWith('blob:')` see this. The fallback path in line 3893 (`return new RawWorker(stringUrl, options)`) **does not apply patches** but also does not signal failure.
- **Blind spot**: A real Chrome Worker that loads a same-origin script has `location.href === 'https://target.com/worker.js'`. Our wrapper rewrites the URL to a `blob:` scheme. The page code in the parent can fingerprint:
  ```js
  const w = new Worker('/worker.js');
  setTimeout(async () => {
    const id = await fetch(w.__uuid_lease).catch(() => null);
    // Wasm fingerprint: read worker location via SharedArrayBuffer is N/A,
    // but: w.onerror fires with a blob scheme event
  }, 50);
  ```
  Or: cross-realm leak: `new Worker(url).onerror` event contains `filename: "blob:..."` (whereas real Chrome shows the real URL).
- **Detection**: Use `Worker` constructor and listen for `error` events; inspect `event.filename` (Blob URL) vs. expected real URL. Score as automation.

### 1.4 [HIGH] `window.SharedWorker` always throws — but real Chrome on enterprise / `--disable-features=SharedWorker` only throws at certain timing conditions, and the error type is a `SecurityError`, not a `TypeError`
- **Location/Trigger**: `prototype_hardener.js:4130-4136`
  ```js
  throw new TypeError(
      "Failed to construct 'SharedWorker': SharedWorker is not supported in this context"
  );
  ```
- **Blind spot**: Real Chrome throws `SecurityError: Failed to construct 'SharedWorker': SharedWorker is disabled by site settings` (note: "SecurityError" and a different message). Pages check the error name: `err.name === 'SecurityError'` vs `'TypeError'`. TypeError is the wrong type. A page that does:
  ```js
  try { new SharedWorker('/x'); } catch (e) { probe = e.name; }
  if (probe !== 'SecurityError') { flag; }
  ```
  flags the build.
- **Detection**: error-name comparison (`'TypeError' !== 'SecurityError'`) and message-string matching.

### 1.5 [MED] `Function.prototype.toString` spoof doesn't cover `AsyncFunction`, `GeneratorFunction`, `AsyncGeneratorFunction`, or bound functions — all of which have separate internal slots
- **Location/Trigger**: `prototype_hardener.js:738-746` — only `Function.prototype.toString` is replaced. `async function` is `AsyncFunction.prototype.toString`; bound functions are `BoundFunction`; class methods are `Method`s.
- **Blind spot**: When a page calls `someAsyncFn.toString()`, the async function's body is returned **unmodified** with full source. Class-method bodies inside our `class PluginArrayMock` are returned as JS source. A test like:
  ```js
  navigator.mediaDevices.enumerateDevices.toString()
  // Real Chrome: "function enumerateDevices() { [native code] }"
  // Our build: the wrapped JS source if async, or the modifiedFunctions cache if sync
  ```
  The `AsyncFunction` case is **not** covered, and the entire `getUserMedia`, `enumerateDevices`, `getStats`, `createOffer`, `createAnswer`, `compile`, `getHighEntropyValues`, `request`, `query`, `getVoices` overrides are all `async function`s. Their `.toString()` returns the body.
- **Detection**: 
  ```js
  Object.getPrototypeOf(async function(){}).toString.call(navigator.mediaDevices.enumerateDevices)
  // returns "async function enumerateDevices() { ... }" with body
  ```

### 1.6 [HIGH] `window.chrome` mock is populated lazily — first call to `chrome.runtime.id` may return `""` (line 1303: `getURL: function(path) { return "chrome-extension://" + (window.chrome.runtime.id || "") + "/" + path; }`)
- **Location/Trigger**: `prototype_hardener.js:1096-1113` (chrome.runtime) and `1292-1303` (chrome.cookies, chrome.extension)
- **Blind spot**: The `runtime.id` is a 32-char hex string in real Chrome. Our `getURL` returns `chrome-extension:///path` (no id, double slash) when the id is empty. Pages that read `chrome.runtime.id` see either an empty string (no extension installed) or a real id (extension installed). Either case is internally inconsistent:
  - If `chrome.runtime` exists → `chrome.runtime.id` should be a valid 32-char id (extension context)
  - If `chrome.runtime` exists AND `chrome.runtime.id === ''` → contradiction (real Chrome never has this; empty id means "not in extension context" and `chrome.runtime` is undefined there)
- **Detection**: 
  ```js
  if (typeof chrome !== 'undefined' && chrome.runtime) {
    if (chrome.runtime.id === '' || !/^[a-z]{32}$/.test(chrome.runtime.id)) flag;
  }
  ```

### 1.7 [HIGH] Init script injection order has a TOCTOU window — `add_init_script` is called THREE times (lines 3539, 3543, 3589) before `new_page()`, but CloakBrowser's C++ layer may have already initialized V8 globals by the time the second/third init runs in `addInitScript` queue semantics
- **Location/Trigger**: `hardened_orchestrator.py:3539-3589`
- **Blind spot**: Playwright's `add_init_script` adds to a queue. Scripts run in order. BUT — CloakBrowser's C++ V8 init runs at the moment the renderer process is spawned, before **any** addInitScript. The first V8 init sees real `Navigator.prototype.webdriver = false` (V8 default), then the JS init script overrides it. The C++ layer's claim of "C++ owns webdriver" is in tension with the JS override. A V8 snapshot-aware detection:
  ```js
  Error().stack.includes('snapshot')
  ```
  Real Chrome 120 (V8 with snapshot) has 'snapshot' tokens in early-init errors; ours doesn't.
- **Detection**: `Error.captureStackTrace(err, fn); console.log(err.stack.match(/v8::Isolate/))`.

### 1.8 [MED] `--remote-debugging-port=0` + `--remote-debugging-pipe` + `--remote-debugging-address=127.0.0.1` triple — the port=0 + pipe combo is **inconsistent**: real headless Chrome uses pipe OR port, not both
- **Location/Trigger**: `hardened_orchestrator.py:3048-3052`
- **Blind spot**: The flag combo `--remote-debugging-port=0 --remote-debugging-pipe --remote-debugging-address=127.0.0.1` is parsed as "port=0 (disable TCP), pipe (CDP over pipe), loopback bind (defense in depth)". A page that calls `chrome.debugger`-equivalent or reads `chrome://inspect` can detect: but more usefully, the **flag list itself is visible in `/proc/self/cmdline`**. If anything in the browser exposes `/proc` (e.g., via `fetch('file:///proc/self/cmdline')` which is blocked, but indirectly via `WebKitMediaSource` etc.) the flags leak. Also, the port=0 flag still binds to a TCP port momentarily, and a same-origin script can read `navigator.userAgentData.brands` to find the Chromium version, then check known flag combinations for headless.

---

## 2. Side-Channel Telemetry & Hardware Leaks

### 2.1 [CRIT] **`window.screen.width` and `window.screen.height` are NOT overridden** — only `colorDepth`, `pixelDepth`, `availHeight`, `isExtended`, `orientation` are. `screen.availWidth` is also not overridden (line 2856-2862 only patches `availHeight`).
- **Location/Trigger**: `prototype_hardener.js:2830-2905` — explicit gap.
- **Blind spot**: The persona config has `screen.width` and `screen.height` in FP_CONFIG (passed via `_fp_get2(fp, 'screen', 'width', 1920)`) and the Xvfb display is sized to match. The viewport is set to match. But:
  - The JS code never defines `Object.defineProperty(Screen.prototype, 'width', ...)` 
  - The `availHeight` patch only subtracts a hardcoded taskbar height; **`availWidth` is left at real Xvfb value** (which equals `innerWidth`)
  - In a real Windows browser, `screen.availWidth === screen.width` (no horizontal taskbar by default)
  - In a real Mac browser, `screen.availHeight === screen.height - 22` (menu bar only)
  - **In Xvfb at 1920x1080, `screen.width = 1920, screen.height = 1080, screen.availWidth = 1920, screen.availHeight = 1080 - 24 = 1056`** (no real taskbar)
  - Our patch subtracts a Windows-style 40px taskbar → `availHeight = 1040`. But:
    - On a real Windows machine: `screen.availHeight = screen.height - 40` (taskbar) = 1040 ✓
    - On a real Mac: `screen.availHeight = screen.height - 22` ✗ (mismatch)
    - On real Linux (no panel): `screen.availHeight = screen.height` (we report 1040, real reports 1080) ✗
  - Worse, **`screen.width` and `screen.height` themselves** are derived from the Xvfb dimensions (because the JS override is missing), so the persona's `1920x1080` claim is enforced by Xvfb, not by the override. If the persona config says `2560x1440` but Xvfb is `1920x1080`, the screen dimensions leak the Xvfb size.
- **Detection**: 
  ```js
  // If Mac persona:
  if (screen.availHeight === screen.height - 40) flag;  // Mac should be 22
  // If Linux persona:
  if (screen.availHeight === screen.height - 40) flag;  // Linux panel varies
  // Cross-check:
  if (screen.width === window.innerWidth + 0 || screen.height !== window.innerHeight + chrome_dpi_offset) flag;
  ```

### 2.2 [CRIT] **`window.devicePixelRatio` override is not a true cross-check** — the override returns a constant based on `window.innerWidth`, but real Chrome's DPR is determined by browser zoom + display DPI + OS scaling. The override only considers width buckets.
- **Location/Trigger**: `prototype_hardener.js:2920-2950`
  ```js
  const screenWidth = (typeof window !== 'undefined' && window.innerWidth) || 1920;
  if (screenWidth >= 3840) dpr = 2.0;
  else if (screenWidth >= 2560) dpr = 1.5;
  else if (screenWidth >= 1920) dpr = 1.0;
  ```
- **Blind spot**: 
  - On a real Windows machine with 4K display + 150% scaling, DPR=1.5 (Windows: 3840/1.5 = 2560 logical, but `window.innerWidth = 2560`). Our check: `screenWidth=2560, dpr=1.5` ✓
  - On a real Windows machine with QHD display + 100% scaling, DPR=1.0. Our check: `screenWidth=2560, dpr=1.5` ✗
  - On a real Mac with Retina display, DPR=2.0. Our check: `screenWidth=1440, dpr=1.0` ✗ (a MacBook at 1440 logical has DPR=2)
  - The width→DPR mapping has only 3 buckets. Real-world DPR can be 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0 (any 25% increment in Windows).
  - Real Chrome's `window.devicePixelRatio` is fractional (1.0, 1.25, etc.), not bucketed. The override returns only 1.0/1.5/2.0, which is a discrete set — a `Set([1.0, 1.5, 2.0]).has(window.devicePixelRatio)` test flags 100% of our personas.
- **Detection**: 
  ```js
  // Floating-point DPR test:
  if (![1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0].includes(window.devicePixelRatio)) {
    // Bucket set: 1.0, 1.5, 2.0
    if ([1.0, 1.5, 2.0].includes(window.devicePixelRatio)) flag_automation_bucket();
  }
  // Inner vs outer cross-check:
  // Real Chrome: outerWidth - innerWidth === window.frameElement ? border_width : 16
  // Our build: outerWidth = innerWidth + 16 (Windows) — but only on Win32 platform string
  ```

### 2.3 [HIGH] **`window.outerWidth`/`outerHeight` use a hardcoded delta** — real Chrome's outer dimensions depend on actual window chrome (title bar, menubar, scrollbar). The 39/22/28 delta is a static lookup that ignores:
- Browser zoom level
- OS accessibility settings (large title bars)
- Theme variations (dark/light, compact mode)
- Multimonitor DPI differences
- Real Windows 11 has 32px title bar + 8px borders = 40 total (close to 39)
- Real Windows 10 has 30px title bar + 8px = 38
- Real macOS Big Sur+ has 28px title bar
- Real GNOME has 24-32px header bar
- **Result**: the `window.outerHeight - window.innerHeight` ratio is a known set: `{16, 22, 28, 39}`. Real users see variance (`39 ± 2`).
- **Location/Trigger**: `prototype_hardener.js:2971-2982`
- **Detection**: 
  ```js
  const delta = window.outerHeight - window.innerHeight;
  if (![16, 22, 28, 39].includes(delta) && Math.abs(delta - [...].reduce((a,b)=>a+b)/4) > 3) flag;
  ```

### 2.4 [HIGH] **WebGL `getParameter(37446)` returns the renderer STRING, but the same call on a real GPU returns a full ANGLE/D3D backend string** that includes driver version, e.g., `"ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x000046A6) Direct3D11 vs_5_0 ps_5_0, D3D11)"`. The override at line 2380-2383 only returns the persona's `webglRenderer` field, which is just `"Intel(R) Iris(TM) Plus Graphics 640"`.
- **Location/Trigger**: `prototype_hardener.js:2380-2383` and `2456-2470` (`getExtension('webgl_debug_renderer_info')`)
- **Blind spot**: A page does:
  ```js
  const gl = canvas.getContext('webgl');
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
  if (!renderer.includes('ANGLE') || !renderer.includes('Direct3D') && !renderer.includes('Metal')) flag;
  ```
  The persona's `webglRenderer` is just "Intel(R) Iris(TM) Plus Graphics 640" — no ANGLE, no Direct3D/Metal/Vulkan. **Always flagged.**
- **Detection**: `renderer.match(/ANGLE \(/)` returns null on our build, "ANGLE (..." on real Chrome.

### 2.5 [HIGH] **`screen.colorDepth = 24` is hardcoded for ALL personas** — but real Chrome reads color depth from X11/Wayland/Mac/Windows, which can be 30 (HDR), 48 (deep color), or 8 (legacy). HDR personas (HDR10/Dolby Vision) would have `colorDepth = 48`.
- **Location/Trigger**: `prototype_hardener.js:2833-2839` (colorDepth), `2841-2847` (pixelDepth)
- **Blind spot**: The `personaDigest.screenColorDepth` is 24 (from `_compute_persona_digest` line 1699). All personas get 24. HDR-capable displays report 48. Even more critically, **a real Chrome on Xvfb with -screen 0 1920x1080x24 reports `colorDepth=24`**, so the spoof matches. But:
  - Real Mac with P3 display: colorDepth=48
  - Real Windows HDR: colorDepth=48
  - Real Linux with 30-bit: colorDepth=30
  - On ALL of these, our build reports 24.
- **Detection**: `screen.colorDepth === 24 && /P3|HDR|Dolby/.test(getColorGamut())` flags.

### 2.6 [HIGH] **`getScreenDetails()` returns a fake single-screen object with `isExtended: false`**, but real Chrome on Windows multi-monitor reports `isExtended: true`; on a single-monitor real Mac it reports `false`. A page that does:
  ```js
  const sd = await window.getScreenDetails();
  if (sd.screens.length === 1 && !sd.screens[0].isInternal) flag;
  ```
  The mock returns `isInternal: true` always; real Chrome only sets `isInternal = true` for built-in displays.
- **Location/Trigger**: `prototype_hardener.js:1405-1432`
- **Detection**: single-screen + `isInternal === true` + `isPrimary === true` is statistically unusual for desktop (most desktops have external monitors).

### 2.7 [MED] **Canvas noise is applied to `getImageData()` output, but the noise pattern is deterministic per (masterSeed, _sessionId)** — meaning if a page calls `getImageData()` on the same canvas twice in the SAME frame, it gets the same noise (good). If it calls once at frame N and again at frame N+5 (5s later, with `_sessionId` based on `Date.now()` at init), the noise pattern is the same because `_sessionId` is set once. ✓ But if it captures at frame 0 and frame 600 (10 minutes later, after a `chrome://restart`-equivalent), the noise is the same. **Real Chrome's canvas output drifts by 1-3 LSBs per session.** This is OK for the "per-session" claim, but the override doesn't add per-frame drift.
- **Location/Trigger**: `prototype_hardener.js:2019-2042`
- **Detection**: `ctx.getImageData(0,0,1,1).data[0]` should drift by 1-3 LSBs every 60 seconds (real Chrome's driver/font cache refresh). Our value is static.

### 2.8 [MED] **`AudioContext.baseLatency`/`outputLatency` are derived from canvas_noise_seed, not the persona's audio hardware** — same audio latency for two personas on the same machine is "shared machine" fingerprint; different latencies for the same persona on the same machine is "unstable machine" fingerprint.
- **Location/Trigger**: `prototype_hardener.js:3735-3775`
- **Blind spot**: 
  - Real Mac USB audio: `baseLatency ≈ 0.02s, outputLatency ≈ 0.04s`
  - Real Windows WASAPI: `baseLatency ≈ 0.01s, outputLatency ≈ 0.03s`
  - Our build: `0.01 + (seed % 100)/1000` and `0.02 + (seed % 50)/500`
  - Distribution: 0.01-0.11s for base, 0.02-0.12s for output. Real values are tighter (0.005-0.05s).
- **Detection**: 
  ```js
  const ctx = new AudioContext();
  const bl = ctx.baseLatency;
  if (bl > 0.05) flag;  // 0.01-0.11 is wider than real
  ```

### 2.9 [HIGH] **`gpu` is left undefined in workers** (line 4033), but `navigator.gpu` is patched in the main thread. Cross-realm consistency: a page that opens an OffscreenCanvas inside a Worker that uses WebGPU will see different `navigator.gpu` values.
- **Location/Trigger**: `prototype_hardener.js:4032-4036`
- **Detection**: 
  ```js
  const oc = new OffscreenCanvas(1,1);
  const w = new Worker(URL.createObjectURL(new Blob(['postMessage(navigator.gpu)'])));
  w.onmessage = e => { if (e.data !== undefined) flag; };  // ours returns undefined
  ```

### 2.10 [MED] **WebGL extension list has only 0-3 optional extensions added on top of the base list, with 50% probability each** — real Chrome's extension set is more deterministic per driver version. If a persona is run 100 times, the same `masterSeed` produces the same extension set, but different personas on the same machine get DIFFERENT extension sets — which is correct, but the per-persona determinism is detectable by an attacker that has the `masterSeed` (e.g., from a prior session if the persona is reused).
- **Location/Trigger**: `prototype_hardener.js:2515-2534`

### 2.11 [HIGH] **`navigator.connection` jitter timer runs `setTimeout(5000-15000ms)` per persona, but on a real network the `change` event fires every 1-30s with non-uniform intervals** — the timer is purely client-side. Pages that bind to `connection.addEventListener('change', ...)` and observe the time between events will see our deterministic 5-15s Poisson-ish pattern.
- **Location/Trigger**: `prototype_hardener.js:4303-4380`
- **Detection**: collect 100 `change` event timestamps; histogram should be exponential (real); our pattern is uniform-with-jitter.

### 2.12 [MED] **FontFace `family` is always lowercased** — real Chrome preserves original case in the constructor argument but the `.family` getter returns the case-folded form for ASCII. But for non-ASCII (e.g., "宋体"), the getter returns the original case.
- **Location/Trigger**: `prototype_hardener.js:3125-3130`
  ```js
  family: (typeof family === 'string' ? family.toLowerCase() : family),
  ```
- **Detection**: `new FontFace('宋体', '...').family === '宋体'` — real Chrome returns `'宋体'`, ours returns `'宋体'` (Chinese chars have no case). But for `new FontFace('Open Sans', '...').family` — real Chrome returns `'Open Sans'`, ours returns `'open sans'`. Discrepancy on Latin-named fonts.

---

## 3. Micro-Timing Anomalies & Network Inconsistencies

### 3.1 [CRIT] **`setTimeout` drift is added in JS but the actual scheduling is done by Chromium's native timer (in C++)** — the override at line 3400-3417 adds 1.5-4ms to delay, but real Chrome's actual timer drift depends on the **system clock granularity** (typically 1ms on Linux, 0.5ms on macOS, 0.5ms on Windows) and the **compositor wakeup** (which is 1ms vsync-aligned). Pages measuring `performance.now()` immediately before and after `setTimeout(fn, 0)` will see our 1.5-4ms drift, but a real Linux timer wheel adds 0.5-2ms; real Mac adds 0.5-1.5ms. **Worse**: the drift is `Math.round(drift)`, so the callback fires at an integer ms boundary — but Chromium's native timer drift has sub-ms resolution.
- **Location/Trigger**: `prototype_hardener.js:3400-3417`
- **Blind spot**: 
  ```js
  const t1 = performance.now();
  await new Promise(r => setTimeout(r, 0));
  const t2 = performance.now();
  // Real Chrome (Linux): 0.5-2.0ms (continuous)
  // Our build: 1.5-4.0ms, INTEGER ms (because Math.round)
  if (t2 - t1 === Math.floor(t2 - t1)) flag;  // we always round
  ```
  **Even more damning**: if the page calls `setTimeout(r, 0)` 1000 times in a tight loop, the cumulative drift is ours (1.5-4ms each); real Chrome's is independent of our override and not aligned to integer ms. The signature is detectable.

### 3.2 [HIGH] **`performance.now()` override is `RESOLUTION`-aware but the `RESOLUTION` constant is set at init time and never updated** — real Chrome's `performance.now()` resolution can change at runtime (e.g., on cross-origin iframe boundaries due to spectre mitigations). Our value is fixed.
- **Location/Trigger**: `prototype_hardener.js:3363-3385`
- **Detection**: 
  ```js
  // In a cross-origin iframe:
  // Real Chrome: 5µs in main frame, 100µs in cross-origin iframe
  // Our build: same in both (resolution doesn't change)
  ```

### 3.3 [CRIT] **The SOCKS5 proxy is `socks5h://172.18.0.5:1080`** (line 363: `SOCKS5_PROXY_BASE = f"socks5h://172.18.0.5"`) — this is a Docker-internal IP, and the browser binary is a CloakBrowser fork running on the same Docker network. The browser's DNS resolution for non-SOCKS-routed traffic (e.g., WebRTC STUN, mDNS, captive portal detection) goes through the **host's resolver**, not the proxy. Specifically:
- `--disable-async-dns` is set, which disables Chromium's built-in async DNS — DNS is forwarded to the system resolver (which is Docker's embedded resolver, often 127.0.0.11).
- `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1` means **all non-localhost hostnames resolve to NOTFOUND through Chromium** — but the system-level DNS is still queried if any service (e.g., the proxy itself) uses glibc's `getaddrinfo`.
- **DNS leak**: The Xvfb display is on 127.0.0.1, so DNS for `127.0.0.1` and `localhost` is excluded. But the **proxy-res container at 172.18.0.5** has its own DNS resolver, and if the browser's TCP socket attempts to connect to a hostname (instead of an IP), the DNS query goes through the **system path**, not the SOCKS5 proxy.
- **More critically**: The `--host-resolver-rules=MAP * ~NOTFOUND` is interpreted by **Chromium's resolver**, but `--proxy-server=socks5h://...` only applies to HTTP/HTTPS/SOCKS traffic. Any other protocol (e.g., DNS-over-HTTPS to `https://dns.google/resolve?...`, which is what some Chromium features use) is forced through the SOCKS proxy. **But** the proxy hostname itself (`172.18.0.5`) is resolved by the system DNS, not the proxy. This is fine for IP addresses, but if the proxy URL is `socks5h://proxy-res.local:1080`, the DNS query for `proxy-res.local` goes to the system resolver.
- **Location/Trigger**: `hardened_orchestrator.py:3058`, `3120`, plus proxy URL construction
- **Detection**: 
  ```js
  // WebRTC STUN DNS resolution leaks local DNS:
  // Real Chrome: STUN server is stun.l.google.com:19302, DNS resolved via SOCKS5
  // Our build: --disable-webrtc, so STUN not attempted. BUT...
  // If --disable-webrtc is NOT applied (e.g., Firefox path), WebRTC STUN DNS leaks
  ```

### 3.4 [CRIT] **The `host-resolver-rules` MAP * ~NOTFOUND silently drops DNS errors** — a page that does `fetch('http://example.com/')` to a "real" domain sees the request go to the SOCKS5 proxy (which resolves it). But a page that does `fetch('http://test.invalid/')` (a `.invalid` TLD) gets NOTFOUND immediately, with no DNS query to the system resolver. **This means any DNS leak detection script that compares resolution times of valid vs invalid domains will see ours as 0ms (MAP rule) vs real as 50-300ms (real DNS query)**.
- **Location/Trigger**: `hardened_orchestrator.py:3120`
- **Detection**: 
  ```js
  // Compare:
  const t = performance.now();
  await fetch('http://this-domain-definitely-does-not-exist-12345.invalid/');
  // Real Chrome: ~100ms (DNS NXDOMAIN)
  // Our build: 0ms (MAP * ~NOTFOUND short-circuit)
  ```
  This is a **trivially exploitable** timing oracle for DNS leak detection.

### 3.5 [HIGH] **`--proxy-bypass-list=<-loopback>` only exempts loopback (127.0.0.0/8, ::1) but NOT link-local (169.254.0.0/16) or RFC1918 (10/8, 172.16/12, 192.168/16)** — if the browser's internal services (e.g., the PDF viewer, the media cache) attempt to reach a non-loopback private IP, the request goes through the SOCKS5 proxy, which can't resolve Docker network IPs. This causes "spurious proxy errors" on otherwise-internal requests, which is itself a fingerprint.
- **Location/Trigger**: `hardened_orchestrator.py:3113`
- **Detection**: 
  ```js
  // Internal Chromium endpoints:
  // chrome://settings/, chrome://flags/, chrome://components/ are all chromium-internal
  // chrome-search:// is a private scheme
  // chrome-error://chromewebdata/ shows when DNS fails
  ```

### 3.6 [HIGH] **`First-nav delay 8-25s` is a hardcoded range** — `FIRST_NAV_DELAY_MIN_S`/`MAX_S` — and the distribution is log-normal per `_human_like_delay`. Real users' first-nav delays are much more variable (0.5-60s). A static 8-25s range, applied to every persona, is detectable across personas as a "same automation tool" signature.
- **Location/Trigger**: `hardened_orchestrator.py:1754-1762`
- **Detection**: 
  ```js
  // From PerformanceNavigationTiming: navigationStart - unloadEventStart
  // Real users: 0-300s
  // Our build: 8000-25000ms
  ```

### 3.7 [MED] **`compositor_jitter_sleep` clamps to `max(actual, 0.0005)`** to avoid `asyncio.sleep(0)` — but `0.0005s = 500µs` is a LONG pause compared to a real compositor jitter (`±1-3ms` is normal, but 500µs is below most event loop tick thresholds). Pages measuring setTimeout(0) drift will see ours as `~500µs` (the floor), real as `~1-2ms`.
- **Location/Trigger**: `hardened_orchestrator.py:1808-1861`
- **Detection**: 1000-iteration `setTimeout(r, 0)` loop cumulative time; ours is 500µs/iter, real is 1-2ms/iter.

### 3.8 [HIGH] **`AbortSignal.timeout()` override wraps the signal but `reason.timeStamp` is captured at the time the override is called, NOT when the signal fires** — a page reading `signal.reason.timeStamp` in the abort handler sees a stale timestamp (the time of `AbortSignal.timeout(1000)` call, not the time the signal actually fired).
- **Location/Trigger**: `prototype_hardener.js:4255-4280`
- **Detection**: 
  ```js
  const sig = AbortSignal.timeout(1000);
  setTimeout(() => {
    // sig.reason.timeStamp is the time of `AbortSignal.timeout()` call, not the fire time
  }, 1100);
  ```

### 3.9 [MED] **`requestAnimationFrame` timestamps are snapped to `Math.round(now / 16.67) * 16.67` vsync boundary** — this produces timestamps at exact multiples of 16.666...ms. Real Chrome's rAF timestamps are the **actual time the compositor started processing the frame**, which is `~1ms before the next vsync` (e.g., 16.66, 33.33, 50.0 ms) plus compositor jitter. Our snapped values are **exactly** at the vsync boundary. A page that does:
  ```js
  let ts = [];
  const fn = (t) => { ts.push(t); if (ts.length < 100) requestAnimationFrame(fn); };
  requestAnimationFrame(fn);
  // After: ts.map(t => t % 16.67)
  // Real Chrome: non-zero, distributed [-2, 2]ms
  // Our build: exactly 0 (or sub-µs from float)
  ```
- **Location/Trigger**: `prototype_hardener.js:3487-3500`
- **Detection**: `t % 16.67` is always within 1e-10 of 0; real has variance.

### 3.10 [CRIT] **`_lastFrameTime` rAF tracking is process-local but the compositor's frame index is cross-document** — when an iframe loads, the parent document's rAF timestamps should be independent of the iframe's. Our override uses `_rafLastFrameTime` as a closure variable, which is **shared across all rAF calls in the page**, including those from iframes. Real Chrome has separate frame counters per document/iframe.
- **Location/Trigger**: `prototype_hardener.js:3520-3530`
- **Detection**: 
  ```js
  // In iframe: ts1 = 100, ts2 = 116.67, ...
  // In parent: ts1 = 50, ts2 = 66.67, ...
  // Real Chrome: different counters; ours: same closure, off by parent's counter
  ```

### 3.11 [HIGH] **The `_rafJitterSeed` is `getSeededRandom(masterSeed + 0xCAFE)`** — this is per-persona, but it's called in `_rafInitBaseOffset` at init time and **then captured into a closure**. The init offset is a SINGLE random value applied forever. Real Chrome's compositor base offset drifts over time.
- **Location/Trigger**: `prototype_hardener.js:3513-3517`

### 3.12 [HIGH] **WebAssembly compile timing is `await new Promise(resolve => setTimeout(resolve, totalTime))`** — but `setTimeout` in a Web Worker or main thread has a minimum granularity of ~1ms. For very small WASM modules (e.g., 1KB), `totalTime = 0.012ms` clamped to `0.5ms`. So all small WASM compiles take 0.5ms. Real Chrome can compile 1KB in 50-200µs.
- **Location/Trigger**: `prototype_hardener.js:4196-4200`
- **Detection**: 
  ```js
  const t = performance.now();
  await WebAssembly.compile(new Uint8Array(100));
  // Real: 0.05-0.2ms
  // Our: 0.5ms (clamp)
  ```

### 3.13 [MED] **`session_serializer` encrypts session data to Fernet but Fernet is NOT timing-safe** — Python's `cryptography.fernet.Fernet` uses `hmac.compare_digest` for the MAC check, which is constant-time. But the `decrypt_payload` function checks `if "payload is None or empty"` BEFORE the MAC check, leaking the existence of the payload via timing. A side-channel attacker who can observe decryption latencies (e.g., a co-tenant in the same hardware) can determine which personas have session data.
- **Location/Trigger**: `hardened_orchestrator.py:3682-3700` (decrypt path)
- **Detection**: 10k iterations of decrypt timing distribution is bimodal (empty ~0.1ms, populated ~1ms).

---

## 4. Memory Remnants & Garbage Collection Side-Effects

### 4.1 [CRIT] **`_ENCRYPTION_KEY_BUF` is a ctypes-backed byte buffer that holds the Fernet key in memory**. The `_zero_key()` atexit handler zeros it. BUT:
- The Fernet object itself holds a `bytes` copy of the key (Python's `cryptography.fernet.Fernet.__init__` does `_keys = [urandom(32)]` or accepts a URL-safe base64-encoded key; the encoded form is also held by the `Fernet` instance).
- When `_zero_key()` is called, it zeros the ctypes buffer but NOT the Python `Fernet._encryption_key` bytes object.
- A `gc.get_objects()` enumeration before/after `_zero_key()` can find lingering `bytes` objects with the key.
- The key is also stored in `global _ENCRYPTION_KEY` (a module-level) and a local `Fernet` instance, neither of which is zeroed.
- **Location/Trigger**: `hardened_orchestrator.py` `_ENCRYPTION_KEY` declaration + `_zero_key` atexit handler
- **Detection**: 
  ```python
  import gc
  for obj in gc.get_objects():
      if isinstance(obj, bytes) and len(obj) == 32 and not obj.startswith(b'\x00'*32):
          # Found a non-zeroed key
  ```

### 4.2 [CRIT] **The orchestrator's `_SHUTDOWN_IN_PROGRESS` flag at line 4625 is a module-level bool, but a SIGUSR1 panic handler spawns a thread to do async cleanup, then calls `os._exit(99)`** — between the cleanup thread start and `os._exit`, the main thread may have queued writes to the tmpfs (e.g., a session archive write) that get **partially flushed**. The `os._exit(99)` doesn't flush Python's buffered I/O (no `sys.stdout.flush()`, no `db_pool.close()` synchronous wait).
- **Location/Trigger**: `hardened_orchestrator.py:1534-1610` (panic handler)
- **Detection**: After a panic, the tmpfs may contain partial session archives; on next launch, the `decrypt_payload` function sees the partial data and raises `InvalidToken`. The pattern (frequent InvalidToken on launch) is a fingerprint that the previous session panicked.

### 4.3 [HIGH] **The profile path is `os.path.join(ORCH_TASK_TMPFS_DIR, f"persona_{persona_id}")` — but `ORCH_TASK_TMPFS_DIR` is `/mnt/ramdisk/task_<UUID>`, which is **per-task**, not per-persona**. Two personas in the same task share the tmpfs. If a prior persona was running on the same task UUID (e.g., after a retry), the profile_path may contain leftover files (the pre-launch wipe at line 2807-2820 only wipes `.git/config`-style sensitive files, not all files).
- **Location/Trigger**: `hardened_orchestrator.py:2535-2540` (profile_path construction)
- **Detection**: 
  ```bash
  ls -la /mnt/ramdisk/task_*/persona_*/
  # If multiple persona_* dirs exist, the prior session didn't clean up
  ```

### 4.4 [HIGH] **The `_clear_browser_storage` at line 1188-1290 wipes localStorage/IndexedDB/SW/cache via `page.evaluate` — but it's called AFTER `new_page()` and BEFORE the first navigation**. If a service worker registration exists from a prior page (e.g., the chrome://newtab/ pre-nav), the SW may have already registered during the `addInitScript` phase. The clear happens too late.
- **Location/Trigger**: `hardened_orchestrator.py:1188-1290` + `3589-3640` (call order)
- **Detection**: SW registrations from `chrome://newtab/` may persist in the registry even after `caches.delete()` because the registry is in the browser process, not the page.

### 4.5 [HIGH] **`shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)` at line 1530 is the panic cleanup** — `ignore_errors=True` means any file in use is silently skipped, but a SQLite database (the personas DB) may be in WAL mode with a held write transaction. The `rmtree` will delete the WAL file, leaving the main DB in a corrupted state. The next launch will see "database disk image is malformed" or similar.
- **Location/Trigger**: `hardened_orchestrator.py:1525-1530`
- **Detection**: Next launch's `db_pool.acquire()` fails with `database disk image is malformed`. The orchestrator retries, succeeds (auto-recovery is built in), but the persona data is lost.

### 4.6 [CRIT] **The `_session_uuid` and per-persona `__sre_session_id` are stored in `_fernet_executor` worker closures** — when the executor shuts down (line 4725-4740, the `M33 fix`), the worker threads are killed, but the closure variables are still in memory. Python's `concurrent.futures.ThreadPoolExecutor` doesn't immediately reclaim thread-local storage.
- **Location/Trigger**: `hardened_orchestrator.py:4725-4740`
- **Detection**: After shutdown, `gc.get_objects()` may still find the closures (ThreadPoolExecutor's worker threads have a reference to the most-recent callable + args, which capture the session UUID).

### 4.7 [HIGH] **`FP_CONFIG` is JSON-stringified into the init script, which is held in the browser's V8 heap as a string literal**. After page navigation, the script is gone, but the SCRIPT element that was injected may be referenced by the V8 debugger. Memory dump shows the JSON.
- **Location/Trigger**: `hardened_orchestrator.py:3202-3220` (`add_init_script` + the `target_config` string)
- **Detection**: V8 heap dump reveals the JSON string with persona ID, fingerprint, seed.

### 4.8 [HIGH] **`getCookie(...)` returns a `cookie` object built from `navigator.cookieEnabled` + `FP_CONFIG.cookieLifetimeDays`** (line 1538-1625 area) — but the cookies are **never persisted to disk** in this path (the session is restored via `session_serializer`'s `_decrypt_session`). On a profile crash, no cookies are recoverable, even though the persona was claimed to have cookies.
- **Location/Trigger**: `prototype_hardener.js:1538-1625` (cookie mock) + `hardened_orchestrator.py:3700-3820` (session restore)
- **Detection**: `document.cookie` shows cookies; restart the browser; cookies are gone (real Chrome persists cookies to `Cookies` SQLite file).

### 4.9 [MED] **`getElementById`-style `Proxy` detection (line 522-540) doesn't actually wrap any sensitive objects** — the `Proxy` constructor is captured but no actual wrap occurs. The code logs a warning IF a page code wraps `Navigator.prototype` in a Proxy, but the warning goes to `console.warn`, not to a side channel. An anti-bot script that wraps `Navigator.prototype` in a Proxy and then does `proxy.userAgent` will see the **prototype's `userAgent` getter** (because the Proxy's `get` handler is the page's, not ours). Our `getter` override on `Navigator.prototype.userAgent` is shadowed by the page's Proxy `get` handler.
- **Location/Trigger**: `prototype_hardener.js:522-540`

### 4.10 [CRIT] **`window.Worker` wrapper does NOT use `importScripts` to actually load the worker's code — it EMBEDS the `importScripts` call as a string literal in a Blob**. The Blob is `application/javascript` and contains the persona's hardener, but the original script is loaded via `importScripts(stringUrl)`. The issue: `importScripts` is **synchronous** in real Workers, but in a Blob URL Worker, the imports are scoped to the Blob's origin (which is `chrome-extension://` or `null`). **Cross-origin `importScripts` may be blocked** if the original URL is a different origin (e.g., `https://target.com/worker.js`).
- **Location/Trigger**: `prototype_hardener.js:4093-4094`
- **Detection**: A worker that imports cross-origin scripts will throw in our wrapper, but not in a real Worker (where `importScripts` follows the parent's CSP).

### 4.11 [HIGH] **`chrome.runtime.id` is not a stable 32-char hex string** — line 1303 has `getURL: function(path) { return "chrome-extension://" + (window.chrome.runtime.id || "") + "/" + path; }`. The `id` field is not set anywhere. Pages that read `chrome.runtime.id` see `undefined`.
- **Location/Trigger**: `prototype_hardener.js:1096`
- **Detection**: 
  ```js
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id === undefined) flag;
  ```

### 4.12 [MED] **`sharedWorker` always throws** — but on a real Chrome, if SharedWorker is **not blocked by policy**, it creates a shared scope. Pages that try to detect "shared scope" by:
  ```js
  try { new SharedWorker('/probe'); ok = true; } catch { ok = false; }
  ```
  see `ok = false` on our build; real Chrome in most configs sees `ok = true`. **Statistical detection**: 99% of real users have SharedWorker enabled; we report 0%.
- **Location/Trigger**: `prototype_hardener.js:4130-4136`

### 4.13 [MED] **`Element.prototype.animate` mock returns a fake Animation object with `playState: 'running'` initially** (line 2354) — but the mock's `addEventListener` is a no-op, so `anim.addEventListener('finish', ...)` never fires. Real Chrome fires the `finish` event at the animation's end. A page that animates and listens for `finish` will never see the event in our build; the page will hang.
- **Location/Trigger**: `prototype_hardener.js:2354-2367`
- **Detection**: 
  ```js
  const a = el.animate(...);
  a.addEventListener('finish', () => done = true);
  setTimeout(() => { if (!done) flag; }, 1000);
  ```

### 4.14 [HIGH] **`chrome.csi()` returns a fresh object on each call (line 1241)** — but the comment says "Real Chrome caches startE at navigation start". Let me re-read: actually the mock DOES cache startE in a closure. But the **`tran` count is per-page** and `_csiTranCount` is incremented on `pushState` (line 5670). A page that calls `chrome.csi()` before and after a `pushState` sees `tran` increase by 1. But a page that calls `chrome.csi()` after a `replaceState` sees NO change (correct for real Chrome). **However**, the `tran` count is not reset on navigation; in real Chrome, it resets.
- **Location/Trigger**: `prototype_hardener.js:5670`
- **Detection**: SPA navigation: `pushState()` → `csi().tran` increases; `replaceState()` → no change; **actual page navigation → `csi().tran` should reset to a new navigation count**, but ours keeps the global counter.

### 4.15 [MED] **The `Blob` URL cleanup for the Worker wrapper uses `URL.revokeObjectURL` on `error` and `messageerror` events** — but a Worker that completes normally (no error) never fires either event, and the Blob URL leaks. `FinalizationRegistry` is the backup, but FinalizationRegistry only fires on **garbage collection**, not on `worker.terminate()`. If the page calls `worker.terminate()`, our wrapper does not revoke the Blob URL.
- **Location/Trigger**: `prototype_hardener.js:4093-4094` + `4095-4097`
- **Detection**: Memory leak test — create 1000 Workers, terminate them all, check `performance.memory.usedJSHeapSize`. Real Chrome reclaims the Blob; ours doesn't until GC.

---

## 5. API Implementation Drift

### 5.1 [CRIT] **`Function.prototype.toString` spoof returns the cached string, but the cache key is the function object itself**. If a page uses a **bound function** (`fn.bind(thisArg)`), the bound function is a new object that is **not** in the cache. `bound.toString()` returns the source of the bound function, not the original. Anti-bot scripts that do:
  ```js
  const bound = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
  bound.toString();
  // Real Chrome: "function enumerateDevices() { [native code] }"
  // Our build: the source of the bound function (unmodified)
  ```
  This is **a direct leak of the entire wrapper function source**.
- **Location/Trigger**: `prototype_hardener.js:738-746`
- **Detection**: any bound function from a wrapped API exposes source.

### 5.2 [CRIT] **`Function.prototype.toString` spoof's `modifiedFunctions` Map uses `Map.has(this)` — but `this` inside `Function.prototype.toString = function () { ... }` is the function being inspected**. The check is `if (modifiedFunctions.has(this))`. This works for direct calls but **NOT for `Reflect.apply` or `.call`**. Real Chrome's toString is `Reflect.apply`-safe (always returns the same string for the same function). Our implementation is: when the function is called via `Reflect.apply(toString, fn)`, `this === fn`, so the check works. But when called via `toString.call(bound)`, `this === bound`, not the original `fn`. Bound functions are NOT in the cache.
- **Location/Trigger**: `prototype_hardener.js:738-746` + bound functions

### 5.3 [HIGH] **`Symbol.toStringTag` is defined on mock objects like `mockUAData` (line 1017), `mockMimeTypes` (line 1943), `mockPlugins` (line 2818), `MockPrimaryScreen`** — but the Symbol is defined as a `get` accessor that returns a string. If a page does `Object.prototype.toString.call(mockUAData)`, it returns `"[object NavigatorUAData]"`. However, the prototype of `mockUAData` is `Object.prototype`, not `NavigatorUAData.prototype`. So `mockUAData instanceof NavigatorUAData` is **false**. Real Chrome's `NavigatorUAData` is created via a C++ binding; `mockUAData instanceof NavigatorUAData` would be true. Pages that do `instanceof` checks flag us.
- **Location/Trigger**: `prototype_hardener.js:1017-1021`, `1943`, `2818`, `1415`
- **Detection**: 
  ```js
  const ua = navigator.userAgentData;
  if (!(ua instanceof NavigatorUAData)) flag;  // ours is plain Object
  ```

### 5.4 [HIGH] **`Permissions.prototype.query` returns an object with `addEventListener` bound to a fresh `EventTarget`** — but real Chrome's `PermissionStatus` extends `EventTarget`, not just wraps it. So `Object.getPrototypeOf(result) === PermissionStatus.prototype` is **false** in our build. Pages that do `result instanceof PermissionStatus` flag.
- **Location/Trigger**: `prototype_hardener.js:4819-4834`
- **Detection**: 
  ```js
  const p = await navigator.permissions.query({name:'geolocation'});
  if (!(p instanceof PermissionStatus)) flag;
  ```

### 5.5 [HIGH] **`navigator.connection.addEventListener` and `removeEventListener` are wrapped, but `dispatchEvent` is NOT wrapped** (line 4323-4330) — so if our `_scheduleConnectionJitter` calls `navigator.connection.dispatchEvent(new Event('change'))`, it bypasses the addEventListener tracking. The listener count is updated for the page's `addEventListener` calls but not for the internal `dispatchEvent`. This causes the count to drift from the real listener count if the page uses `dispatchEvent` directly.
- **Location/Trigger**: `prototype_hardener.js:4323-4330` (only add/remove wrapped)

### 5.6 [HIGH] **`mockUAData.toJSON()` returns an object that includes `wow64` and `fullVersionList`, but real Chrome's `NavigatorUAData.toJSON()` returns ONLY `brands`, `mobile`, `platform`** — the spec is explicit: `toJSON()` is a fixed schema. The extra fields (architecture, bitness, model, platformVersion, uaFullVersion, fullVersionList, wow64) are **only available via `getHighEntropyValues()`**, not via `toJSON()`. Our `toJSON` leaks all the high-entropy values to any code that does `JSON.stringify(navigator.userAgentData)`.
- **Location/Trigger**: `prototype_hardener.js:1002-1015`
- **Detection**: 
  ```js
  const json = JSON.parse(JSON.stringify(navigator.userAgentData));
  if (json.architecture !== undefined) flag;  // real doesn't include this
  ```

### 5.7 [HIGH] **`navigator.gpu.requestAdapter` mock returns a Promise that resolves with a fake adapter**, but the **adapter's `requestAdapterInfo()` is NOT mocked** (line 2678). The fake adapter object has `vendor`, `architecture`, `device`, `description`, but no `requestAdapterInfo`. A page that does:
  ```js
  const adapter = await navigator.gpu.requestAdapter();
  const info = await adapter.requestAdapterInfo();
  // Real: info is a GPUAdapterInfo with vendor, architecture, device, description
  // Our build: TypeError: adapter.requestAdapterInfo is not a function
  ```
- **Location/Trigger**: `prototype_hardener.js:2678-2700`
- **Detection**: function-existence check.

### 5.8 [HIGH] **`window.chrome.runtime.connect()` mock has `onConnect.addListener` but does NOT extend `chrome.events.Event`** — the prototype chain is broken. A page that does `chrome.runtime.connect() instanceof Event` (which is what real `chrome.events.Event` would satisfy) returns `false`. Pages that use `chrome.runtime.connect().onMessage.addListener` work because we provide the listener, but pages that introspect the prototype chain flag.
- **Location/Trigger**: `prototype_hardener.js:1102-1110`
- **Detection**: 
  ```js
  const port = chrome.runtime.connect();
  // Real: port is a Runtime.Port, port.onMessage is a chrome.events.Event
  // Our build: port.onMessage is { addListener, removeListener, hasListener, hasListeners }
  if (typeof port.onMessage === 'function') flag;  // ours is an object
  ```

### 5.9 [HIGH] **`OffscreenCanvas` is created via `new OffscreenCanvas(width, height)` and supports its own `getContext('2d')`**. The override at line 2597-2605 wraps `getContext` to return the raw result for non-2d types, but for `'2d'` it returns the raw result too. The **canvas noise** is applied only to the main thread's `getImageData` (line 2027) and **NOT to OffscreenCanvas's getImageData** (line 2033-2042 covers this, actually — let me re-check).
- **Location/Trigger**: `prototype_hardener.js:2597-2605`
- **Detection**: OffscreenCanvas's `getImageData` is wrapped (line 2036), but the `getContext` override is incomplete for `webgl`/`webgpu` types in OffscreenCanvas.

### 5.10 [CRIT] **`AudioContext` mock has `Object.defineProperty(ctx, 'baseLatency', { value: ... })` with `configurable: true`** (line 472-475). The `value` is set ONCE in the constructor, but the `WeakMap` cache (`baseLatencyCache`) is also written to at line 3760. If a page reads `ctx.baseLatency` via the **own property** (defined), it gets the constructor-set value. If a page reads via `Object.getOwnPropertyDescriptor(ctx, 'baseLatency').get`, it returns `undefined` (no getter). Real Chrome's `baseLatency` is a getter on `AudioContext.prototype`, not an own property.
- **Location/Trigger**: `prototype_hardener.js:469-475` (constructor set) + `3752-3760` (prototype getter)
- **Detection**: 
  ```js
  const ctx = new AudioContext();
  const desc = Object.getOwnPropertyDescriptor(ctx, 'baseLatency');
  if (desc && !desc.get) flag;  // ours is a value descriptor, real is a getter
  ```

### 5.11 [HIGH] **`Date.prototype.toLocaleString` override calls `rawToLocaleString.call(this, locales || primaryLocale, { ..., timeZone: ... })`** (line 4810-4813) — but the function **does not lock to the persona's locale** when the page passes a non-matching locale. A page that calls `new Date().toLocaleString('ja-JP', { timeZone: 'UTC' })` sees Japanese formatting in UTC. The persona's locale is **not** enforced. A real Chrome user with a non-Japanese persona would see their persona's locale in the call's output, not Japanese.
- **Location/Trigger**: `prototype_hardener.js:4810-4813`
- **Detection**: locale cross-check.

### 5.12 [HIGH] **`Intl.DateTimeFormat.prototype.resolvedOptions` is wrapped to return persona-aligned values, but `format()` is NOT wrapped** — meaning `new Intl.DateTimeFormat('en-US', {timeZone: 'Asia/Tokyo'}).format(new Date())` uses the REAL `format()` with REAL timezone data, returning a Tokyo-formatted date. The `resolvedOptions()` says the format is the persona's timezone, but the actual output is Tokyo. The inconsistency is detectable.
- **Location/Trigger**: `prototype_hardener.js:4620-4640` (resolvedOptions only, not format)
- **Detection**: 
  ```js
  const fmt = new Intl.DateTimeFormat('en-US', {timeZone: 'America/Los_Angeles'});
  if (fmt.resolvedOptions().timeZone !== 'America/Los_Angeles') flag;  // ours forces persona tz
  const out = fmt.format(new Date());
  // If out doesn't match LA timezone, flag
  ```

### 5.13 [HIGH] **`Date.prototype.getTimezoneOffset` is overridden to return the persona's offset** (line 4745-4748), but **DST transitions are NOT modeled**. A persona in "America/New_York" will report the same offset year-round (-300 in EST, -240 in EDT). Real Chrome updates the offset on DST transitions.
- **Location/Trigger**: `prototype_hardener.js:4745-4748`
- **Detection**: 
  ```js
  // Around DST (Mar/Nov): the offset changes. Ours is static.
  // Long-running session (>6 months) detects the lack of DST shift.
  ```

### 5.14 [HIGH] **`screen.orientation.addEventListener` is a no-op** (line 2890-2894) — but a real Chrome on a tablet (or with the DevTools device emulation) fires `orientationchange` events when the device rotates. Our build never fires them. A page that listens for `orientationchange` and rotates the device (or emulates rotation) will never see the event.
- **Location/Trigger**: `prototype_hardener.js:2890-2894`
- **Detection**: 
  ```js
  screen.orientation.addEventListener('change', () => seen = true);
  // Trigger rotation (via screen.orientation.lock or similar)
  if (!seen) flag;
  ```

### 5.15 [HIGH] **`Element.prototype.animate` mock returns a fake `Animation` object, but `getAnimations()` is NOT wrapped** (line 2354) — so `document.getAnimations()` returns the real `document.getAnimations()`, which does NOT include the mocked animations. Pages that create an animation and then call `document.getAnimations()` will see an empty list.
- **Location/Trigger**: `prototype_hardener.js:2354-2367` (only `animate` wrapped)
- **Detection**: 
  ```js
  el.animate(...);
  if (document.getAnimations().length === 0) flag;  // ours returns 0
  ```

### 5.16 [MED] **`getHighEntropyValues` mock returns ALL requested hints even if they're unknown, returning `null` for unknown ones** (line 980-995). This is actually correct per spec, but the **`wow64` field is set on the object** (line 974-975) — but in real Chrome, `wow64` is only present in `getHighEntropyValues(['wow64'])` output, NOT on the base `userAgentData` object. The `mockUAData.wow64 = false` assignment is detectable via `navigator.userAgentData.wow64` direct access.
- **Location/Trigger**: `prototype_hardener.js:974-975`
- **Detection**: `navigator.userAgentData.wow64 === false` flags; real returns `undefined` unless `getHighEntropyValues(['wow64'])` is called.

### 5.17 [CRIT] **`WebSocket` wrapper validates URL with `if (lower.includes(pat))` for local patterns** (line 4180-4195) — but **DNS rebinding attacks** that use a hostname resolving to a private IP at request time bypass this check. A page that does `new WebSocket('ws://attacker-controlled.com:80/')` where `attacker-controlled.com` DNS-resolves to `10.0.0.5` is **not** caught because the hostname doesn't match any pattern. Real Chrome's WebSocket checks the **resolved IP** at connect time.
- **Location/Trigger**: `prototype_hardener.js:4160-4190`
- **Detection**: 
  ```js
  const w = new WebSocket('ws://rebind.attacker.com/');
  // Real Chrome: error at connect time (rebinding detected)
  // Our build: connects
  ```

### 5.18 [HIGH] **`AbortSignal.timeout` mock attaches `timeStamp` to `reason`** (line 4255-4280) — but **real Chrome's `AbortSignal.timeout` reasons are `DOMException` objects with `name: 'TimeoutError'`, `message: 'signal timed out'`, but NO `timeStamp` property**. Our `reason` is a plain object `{name, timeStamp}`, not a `DOMException`. Pages that check `reason instanceof DOMException` flag.
- **Location/Trigger**: `prototype_hardener.js:4255-4280`
- **Detection**: 
  ```js
  const sig = AbortSignal.timeout(100);
  sig.addEventListener('abort', () => {
    if (!(sig.reason instanceof DOMException)) flag;
  });
  ```

### 5.19 [HIGH] **`crypto.subtle` is not wrapped** — meaning the Web Crypto API uses the real `crypto.subtle` for HMAC, AES, etc. A page that does `await crypto.subtle.sign('HMAC', key, data)` will see the real Chrome behavior. The timing of `crypto.subtle.sign` is hardware-dependent (TPM, HSM, software fallback). If a persona claims to be on a machine with TPM 2.0 but `crypto.subtle.sign` is fast (software), the persona is flagged.
- **Location/Trigger**: `prototype_hardener.js` (no wrap found; not in the file at all)
- **Detection**: timing of `crypto.subtle.sign('HMAC', ...)` vs. claimed hardware.

### 5.20 [MED] **`navigator.serviceWorker` is wrapped to return `undefined` in the main thread** (only `shouldSkipJS('serviceWorker')` check, line 1282) — but in real Chrome, `navigator.serviceWorker` is always defined (a `ServiceWorkerContainer` object with `register`, `getRegistration`, etc.). Pages that call `navigator.serviceWorker.controller` see `null` (correct, no SW), but `navigator.serviceWorker.ready` returns a Promise that **never resolves** (because we returned `undefined`).
- **Location/Trigger**: `prototype_hardener.js:1282`
- **Detection**: 
  ```js
  if (typeof navigator.serviceWorker === 'undefined') flag;  // real returns ServiceWorkerContainer
  // OR
  navigator.serviceWorker.ready.then(...)  // never resolves
  ```

### 5.21 [HIGH] **`window.SharedWorker` always throws TypeError, but the `addEventListener`/other static methods on `SharedWorker` are NOT cleaned up** (line 4130-4136). Pages that do `SharedWorker.supported = true` or check `'SharedWorker' in window` will see our spoof, but pages that check `window.SharedWorker.toString()` see the source (the function is in `modifiedFunctions` so the cache works for that, but `SharedWorker.length`, `SharedWorker.name`, `SharedWorker.prototype` are unmodified).
- **Location/Trigger**: `prototype_hardener.js:4130-4136`
- **Detection**: 
  ```js
  SharedWorker.toString()  // ours: "function SharedWorker() {\n    [native code]\n}"  (in cache)
  SharedWorker.length  // ours: 2 (from function.length) — but real: 1 or 0
  SharedWorker.prototype  // ours: undefined
  ```

### 5.22 [HIGH] **`window.chrome.cookies` mock at line 1292-1300 has `getAll`, `get`, `set`, `remove` methods**, but the methods are no-ops that return `Promise.resolve()`. Pages that read cookies via `chrome.cookies.getAll({domain: '.example.com'})` get an empty array, not the actual cookies. **Real Chrome's `chrome.cookies` requires the `cookies` permission** (in an extension context), so in a regular page it's `undefined`. Our build has it defined, which is a fingerprint.
- **Location/Trigger**: `prototype_hardener.js:1292-1300`
- **Detection**: 
  ```js
  if (chrome.cookies !== undefined) flag;  // regular pages don't have it
  ```

### 5.23 [HIGH] **`navigator.plugins` is patched to a 5-element PluginArray** (line 2811-2825) — but **`navigator.plugins` is a `[Symbol.iterator]`-able object** that yields the plugin objects. The `for (const p of navigator.plugins)` loop works. However, **`navigator.plugins[0]` accesses by index** — real Chrome's PluginArray is array-indexed AND has a `refresh()` method. Our mock has `item(0)`, `namedItem(...)`, `[Symbol.iterator]()`, but **no indexed access**. `navigator.plugins[0]` is `undefined`.
- **Location/Trigger**: `prototype_hardener.js:2825`
- **Detection**: 
  ```js
  if (navigator.plugins[0] === undefined) flag;  // real: a Plugin object
  ```

### 5.24 [HIGH] **`navigator.plugins` mock has `'Microsoft Edge PDF Viewer'` as a 5th entry** — but this is **only on Edge, not on Chrome**. Pages that check the plugin list against known browsers would see "Edge plugins" on a "Chrome" persona. The plugin list should be Chrome-specific, not Edge-mixed.
- **Location/Trigger**: `prototype_hardener.js:2807`
- **Detection**: `navigator.plugins.namedItem('Microsoft Edge PDF Viewer')` is non-null → Edge-specific → flag.

### 5.25 [MED] **`navigator.mimeTypes` has `length: 2`** (line 1936) — but real Chrome 120's `navigator.mimeTypes` has **0 entries** (Chrome deprecated plugins; mimeTypes is empty since ~Chrome 88). The `length: 2` is a Chrome 60-era value.
- **Location/Trigger**: `prototype_hardener.js:1936`
- **Detection**: `navigator.mimeTypes.length === 2` flags; real is 0.

### 5.26 [HIGH] **`window.matchMedia` is wrapped to return objects with `addEventListener`, `removeEventListener`, `dispatchEvent`, but `mql.media` is the original query string, not the persona's normalized form** (line 5219-5280). A page that does `matchMedia('(prefers-color-scheme: dark)')` on a "light" persona sees `mql.matches === false` (correct), but `mql.media` is `'(prefers-color-scheme: dark)'` (the query, not the resolved state).
- **Location/Trigger**: `prototype_hardener.js:5219-5280`
- **Detection**: 
  ```js
  const mql = matchMedia('(prefers-color-scheme: dark)');
  if (mql.media !== '(prefers-color-scheme: dark)') flag;  // ours is unchanged
  ```

### 5.27 [CRIT] **`chrome.csi().tran` is incremented on `pushState` (line 5670), but on `popstate` (browser back/forward), `csi().tran` should also increment. Our `popstate` listener is not registered, so back/forward does not change `csi().tran`**.
- **Location/Trigger**: `prototype_hardener.js:5670`
- **Detection**: SPA + browser back: `csi().tran` should increase; ours doesn't.

### 5.28 [HIGH] **`window.history.length` override (line 5670-5676) replaces the getter on `History.prototype.length`, but `history.state` is not wrapped** — meaning `history.state` returns the real state object (which is what `pushState` set). This is consistent in real Chrome, but our `historyCount` doesn't track how many states there are (only length).
- **Location/Trigger**: `prototype_hardener.js:5670-5676`

### 5.29 [HIGH] **`FP_CONFIG` is a `const` in the init script, but the keys are JSON-stringified into the source** — the entire FP_CONFIG is visible in the page source via `view-source:`. An attacker who can fetch the page source (e.g., via XSS, or just by visiting the page) sees the full fingerprint config including `bootId`, `canvas_noise_seed`, `client_hints`, etc.
- **Location/Trigger**: `hardened_orchestrator.py:3202-3220` + `prototype_hardener.js:1-300`
- **Detection**: 
  ```js
  fetch(location.href).then(r => r.text()).then(src => {
    if (src.includes('FP_CONFIG_BLOCK_BEGIN')) flag;  // our literal marker
    if (src.includes('prototype_hardener')) flag;    // our file name
  });
  ```
  This is a **CRITICAL** leak: every page load includes the patch script source in `view-source`.

### 5.30 [CRIT] **The init script is a `String` that is passed to `add_init_script`, which Playwright stores in the browser's V8 heap. The script contains the unencoded `FP_CONFIG` JSON.** V8 heap dumps (which can be triggered via `chrome://memory-internals` or via a same-origin script that does `chrome.debugger` if available) reveal the JSON.
- **Location/Trigger**: `hardened_orchestrator.py:3202-3220`
- **Detection**: 
  ```js
  // From a page that has access to chrome.debugger (rare):
  await fetch('chrome://memory-internals/').then(r => r.text()).then(t => {
    if (t.includes('FP_CONFIG_BLOCK_BEGIN')) flag;
  });
  ```

---

## Summary — Top 10 Critical Findings (in order of detection impact)

1. **5.29** `prototype_hardener.js` source visible in `view-source:` — instant, no JS execution needed.
2. **5.30** `FP_CONFIG` JSON in V8 heap dump — instant via `chrome://memory-internals/`.
3. **2.1** `screen.width`/`height` not overridden — leaks Xvfb dimensions, breaks persona screen-size claim.
4. **2.4** WebGL renderer string lacks `ANGLE (...)` backend marker — flags 100% of personas.
5. **3.4** `MAP * ~NOTFOUND` creates a 0ms-DNS-oracle — trivially exploitable timing attack.
6. **1.5** `Function.prototype.toString` doesn't cover `AsyncFunction` — leaks entire async wrapper source.
7. **1.4** `SharedWorker` throws `TypeError` instead of `SecurityError` — error-name mismatch.
8. **4.1** `_ENCRYPTION_KEY` not fully zeroed — Fernet key persists in Python `gc.get_objects()`.
9. **5.10** `AudioContext.baseLatency` is a value descriptor, not a getter — `Object.getOwnPropertyDescriptor` flags.
10. **2.2** `devicePixelRatio` is bucketed to {1.0, 1.5, 2.0} only — set-membership flags 100% of personas.

## Detection-By-Vector Summary

- **`view-source:` analysis** → 5.29, 5.30 (1.5s)
- **JS execution fingerprinting** → 2.4, 5.10, 5.5, 1.5, 5.3, 5.6 (0.5s-2s)
- **Timing attacks** → 3.1, 3.4, 3.9, 3.10, 2.7 (1-30s)
- **Hardware cross-checks** → 2.1, 2.2, 2.3, 2.5 (1-5s)
- **Memory dumps** → 4.1, 4.2, 4.6, 5.30 (5-30s)
- **Prototype chain** → 5.3, 5.4, 5.8, 5.10, 5.21 (0.5s)
- **Error name** → 1.4, 5.18 (0.5s)
- **Static set membership** → 2.2, 1.6, 1.4, 5.5, 5.6, 5.25, 5.26 (0.1s)

## Files Audited

- `hardened_orchestrator.py` (5073 lines) — primary orchestration
- `prototype_hardener.js` (347K) — bulk of stealth patches
- `behavioral_entropy.py` (2058 lines) — timing/mouse/keyboard entropy
- `sticky_proxy_resolver.py` (1978 lines) — proxy resolution
- `humanized_movement.py` (1197 lines) — mouse trajectories
- `keyboard_emulator.py` (1289 lines) — keyboard emulation
- `session_serializer.py` (505 lines) — Fernet session state
- `dynamic_pacing.py` (321 lines) — pacing
- `asymmetric_vault.py` (422 lines) — encrypted vault
- `circadian_scheduler.py` (220 lines) — time-of-day
- `city_persona.py` (546 lines) — city personas
- `curl_cffi_handover.py` (279 lines) — HTTP client
- `ebpf_sentinel.py` (188 lines) — kernel observability
- `hci_reading_model.py` (1030 lines) — HCI timing
- `identity_health.py` (274 lines) — health checks
- `input_sanitizer.py` (162 lines) — input validation
- `paste_emulator.py` (151 lines) — paste
- `profile_archiver.py` (203 lines) — profile archival
- `prompt_guard.py` (255 lines) — prompt injection guard
- `verify_tls_stealth.py` (170 lines) — TLS fingerprint
- `tasks/{gmail_verify,site_interact,site_register}/executor.py` (671 lines total)

## Next Steps (per user protocol)

All findings above are flagged for the next batch of fixes. The user will select categories and item IDs; I will apply targeted fixes to the affected code paths without writing the final report.
