# R7 Pass 1 — Behavioral Correctness Review

**Question per finding**: *Does this code produce output that matches what real Chrome 120 (or real user behavior) actually produces?*

**Scope**: All 21 files in `browser-runtime/`
**Severity**: C/H/M/L (no floor — every issue is logged)
**Date**: 2026-07-14

---

## File index

| Tag | File | LOC | Status |
|---|---|---:|---|
| PHO | `prototype_hardener.js` | 3,151 | ✅ done |
| HOR | `hardened_orchestrator.py` | 2,114 | ⏳ next |
| ENT | `entrypoint.sh` | 1,055 | ⏳ |
| DOK | `Dockerfile` | 207 | ⏳ |
| SPR | `sticky_proxy_resolver.py` | 1,584 | ⏳ |
| BEN | `behavioral_entropy.py` | 1,434 | ⏳ |
| KBE | `keyboard_emulator.py` | 929 | ⏳ |
| HCI | `hci_reading_model.py` | 663 | ⏳ |
| HUM | `humanized_movement.py` | 593 | ⏳ |
| SSR | `session_serializer.py` | 330 | ⏳ |
| R6x11 | 11 R6-audited files | ~2,000 | ⏳ |

---

## PHO — `prototype_hardener.js` (3,151 LOC)

### PHO-001 [CRITICAL] `Intl.DateTimeFormat.prototype.format` ignores user's `timeZone` option

**Where**: lines ~1293 (the `Intl.DateTimeFormat.prototype.format` override)

**Issue**: The override creates a *new* `Intl.DateTimeFormat` with `timeZone: tz` and ignores whatever `options.timeZone` the caller passed. If a page does:
```js
new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles' }).format(new Date())
```
…it gets formatted as if the timezone were the persona's TZ (`America/New_York` in FP_CONFIG), not LA. This is detectable: a US-east-coast page that does timezone math on its own `format()` output and compares to the system clock will see a mismatch.

Real Chrome honors the caller's `timeZone` option, falling back to the system TZ only when none is provided.

**Fix**: Only inject the persona TZ if the caller did not pass a `timeZone` option. Same for `formatToParts`.

---

### PHO-002 [CRITICAL] `window.chrome.app.InstallState` enum values are wrong order/case

**Where**: line ~390 (window.chrome.app mock)

**Issue**: The mock uses:
```js
InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" }
```
Real Chrome 120's `chrome.app.InstallState` is:
```js
{ DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" }
```
That part matches. BUT: real Chrome's `runningState()` returns one of `"cannot_run"`, `"ready_to_run"`, `"running"` — three states. The mock only returns `"cannot_run"`. Pages that check `chrome.app.runningState() === 'running'` will always get false.

**Severity justification [CRITICAL]**: A blanket `"cannot_run"` breaks any page that gates feature availability on the running state.

**Fix**: Track a per-instance `runningState` and rotate through the three values (or at least allow the two non-default ones).

---

### PHO-003 [HIGH] `chrome.runtime.id` is a hard-coded 32-char lowercase string

**Where**: `fakeId = "abcdefghijklmnopqrstuvwxyzabcdef"` (line ~376)

**Issue**: Real Chrome 120's extension IDs are 32-character lowercase a-p (base16 from a limited alphabet), and **the first character is always 'a' for app IDs, 'm' for extension IDs**. The hard-coded `abcdefghijklmnopqrstuvwxyzabcdef` is alphabetically a-p only by coincidence (z is past p), but anti-bot grids check the **prefix char**: a real extension ID starts with one of [a-p], the rest are [a-p0-9]. The string `abcdefghijklmnopqrstuvwxyzabcdef` includes `y` and `z` which are **not valid in a real Chrome extension ID** (the ID alphabet is `[a-p]`).

A site doing `chrome.runtime.id.match(/^[a-p]{32}$/)` will return `null` for our mock but a real string for a real Chrome.

**Fix**: Generate the ID from a persona-seeded PRNG over `[a-p]`. Or use a hard-coded valid ID like `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaap"`.

---

### PHO-004 [HIGH] `chrome.csi` returns values that don't match a real browser session

**Where**: lines ~410-420 (csiMock)

**Issue**: 
- `startE` is a fresh `performance.now()` each call. Real Chrome's `csi().startE` is stable across calls in a page (it's the page-load time, captured once). If a page calls `chrome.csi()` twice in 50ms and gets wildly different `startE` values, it's a tell.
- `tran: 15` is hard-coded. Real `tran` is the count of navigations since the renderer started, which monotonically increases by the count of `pushState`/`replaceState`/actual navigations.
- `onloadT = start + 120 + Math.floor(seededRandom() * 80)` → 120-200ms after start. Real Chrome's `onloadT` is typically 100-500ms after `startE` and is correlated with the actual page load time, not seeded.

**Fix**: Cache `startE` at first call. Increment `tran` on `pushState`. Derive `onloadT` from actual `load` event timing.

---

### PHO-005 [HIGH] `chrome.loadTimes.firstPaintAfterLoadTime: 0` is a hard-coded zero

**Where**: line ~447 (loadTimesMock)

**Issue**: Real Chrome 120 always reports `firstPaintAfterLoadTime` as the time of the first paint that occurred **after** the `load` event. A value of `0` means "never happened" which is detectably wrong for any non-trivial page (the page paints after load all the time). Real values are typically 50-500ms after `finishLoadTime`.

**Fix**: `firstPaintAfterLoadTime: finL + 0.080 + seededRandom() * 0.250` (or use `commitL + 0.1` as a fallback).

---

### PHO-006 [HIGH] `navigator.plugins` mock has 5 plugins but they all share the same filename for the PDF ones

**Where**: lines ~660-670 (PluginArrayMock)

**Issue**: Real Chrome 120 plugin array:
```js
[
  { name: "PDF Viewer", filename: "internal-pdf-viewer", description: "" },
  { name: "Chrome PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
  { name: "Chromium PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
  { name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" },
  { name: "Native Client", filename: "internal-nacl-plugin", description: "" }
]
```
But the mock has them with `description: "Portable Document Format"` for the *first* one (PDF Viewer) when real Chrome's first entry has empty description. Also real Chrome's order is alphabetical by name and they share filename "internal-pdf-viewer" — that part is correct. But the descriptions are subtly wrong.

**Fix**: Match real Chrome's exact description values per plugin entry.

---

### PHO-007 [HIGH] `navigator.connection` `liveJitterState.type` never changes despite being a "live" state

**Where**: line ~1100-1110

**Issue**: The `liveJitterState.type` is initialized to `FP_CONFIG.connectionType` ("wifi") and never updated. The `_scheduleConnectionJitter` function updates `rtt`, `downlink`, and `effectiveType`, but not `type`. The `type` change in real Chrome happens on WiFi↔Cellular transition, which is rare but observable. A static "wifi" forever is technically fine (most users are on wifi) but if combined with the `effectiveType` rolling through 4g/3g values, the pair is inconsistent: a real wifi connection has `type=wifi, effectiveType=4g` OR `type=wifi, effectiveType=wifi`. We never have `type=wifi, effectiveType=4g, then type=wifi, effectiveType=3g` because the 3g hint contradicts wifi.

**Fix**: When rolling `effectiveType`, constrain the cycle based on `type` (wifi → only 4g/wifi; cellular → 3g/4g/slow-2g).

---

### PHO-008 [HIGH] `WebAssembly.compile` timing formula is log2-based but capped at very small values

**Where**: line ~985 (wasm compile override)

**Issue**: 
```js
const baseTime = Math.log2(bytes ? bytes.length : 1000) * 2;
const jitter = seededRandom() * 5;
const totalTime = baseTime + jitter;
```
For a 100KB WASM module, `log2(100000) * 2 ≈ 33`. So total ≈ 33-38ms. But real Chrome's `WebAssembly.compile` on a 100KB module is typically 5-15ms on a modern CPU. The formula over-estimates by 3-4x for typical module sizes. This is detectable: a fingerprint grid that measures `WebAssembly.compile` time on a known-size module and cross-references against expected ranges will see our values are too high.

**Fix**: Use a more realistic formula. Real Chrome's compile time scales roughly as `bytes / (cpu_mhz * 0.1)` ms. For a 4GHz CPU and 100KB module: `100000 / 400000 = 0.25ms`. For 1MB: 2.5ms. Use `bytes / 250000` ms as a base, with ±20% jitter.

---

### PHO-009 [HIGH] `permissions.query` returns 'granted' for midi/speaker even though we said the persona hasn't interacted

**Where**: line ~1340 (Permissions.query override)

**Issue**: The mock returns the state from `FP_CONFIG.permissionStates`, but the defaults include `midi: "granted"` and `speaker: "granted"`. Real Chrome only shows these as `granted` after the user has explicitly granted them. A fresh persona (no interaction history) would have these as `prompt`, not `granted`. The defaults give the persona an "experienced user" permission profile on first launch.

**Fix**: Either: (a) make these default to `prompt` and let the orchestrator grant them as the persona "interacts"; or (b) explicitly mark them as "user has previously granted" by setting `permissionStates.midi = "granted"` only when persona has interacted with MIDI-aware sites.

---

### PHO-010 [MEDIUM] `Notification.permission` computed from `notification_session_count` is unstable across reloads

**Where**: line ~530 (Notification.permission state machine)

**Issue**: The state is computed at script init from `FP_CONFIG.notification_session_count`. If the orchestrator sets this to e.g. 5, the state is "30% denied". The persona roll is computed from `masterSeed`. If `masterSeed` is stable (per-persona), then the state is stable across reloads of the same persona. ✓ But if the orchestrator changes `notification_session_count` mid-session (it doesn't, but could), the state would change. More importantly, a persona with `notification_session_count=0` and a persona with `notification_session_count=10` who have the same `masterSeed` would both get the same state if the roll < 0.30, but the state's *meaning* is different. The state machine logic doesn't account for this.

**Fix**: Document the invariant: `notification_session_count` is read once at init and never changes.

---

### PHO-011 [MEDIUM] `document.hasFocus` setTimeout loop uses `Math.random()` instead of seeded random

**Where**: line ~480-505 (focus flip loop)

**Issue**: 
```js
if (Math.random() < 0.5) {
    _isFocused = !_isFocused;
}
...
const nextDelay = 30000 + Math.floor(Math.random() * 90000);
```
The focus flip state is supposed to be per-persona (driven by masterSeed), but the *flip decision* and *next delay* both use unseeded `Math.random()`. Across multiple browser launches of the same persona, the focus flip pattern will be completely different, which contradicts the "per-persona determinism" goal.

**Fix**: Replace `Math.random()` with `seededRandom()` (a per-persona seeded RNG).

---

### PHO-012 [MEDIUM] `_scheduleConnectionJitter` uses unseeded `Math.random()` and `Math.sqrt/log` for gaussian

**Where**: line ~1145-1160

**Issue**: Same as PHO-011. The "drift" values (rtt, downlink) are seeded once at init (from `masterSeed + 2731`), but the *drift updates* and *event firing probability* (0.001 chance of effective type change) use unseeded `Math.random()`. The 0.1% chance of network change is not per-persona.

**Fix**: Use a per-persona seeded RNG for the live update values and probabilities.

---

### PHO-013 [MEDIUM] `AbortSignal.timeout` timeStamp override is incomplete

**Where**: line ~1000 (AbortSignal.timeout override)

**Issue**:
```js
const startPerf = window.performance.now();
Object.defineProperty(rawReason, 'timeStamp', {
    get: () => window.performance.now() - startPerf,
    configurable: true
});
```
This sets `timeStamp` to "elapsed since AbortSignal.timeout was called". Real DOM events' `timeStamp` is "elapsed since document load" (or `timeOrigin`). The override is in the wrong reference frame. If a page does:
```js
const t0 = performance.now();
const sig = AbortSignal.timeout(100);
sig.reason.timeStamp - t0  // should be ~0 if it's "elapsed since call"
                              // but pages expect "since page load"
```
…the page will see values much larger than expected (because both t0 and timeStamp are "since page load" but t0 is small and timeStamp is also small). The mismatch is subtle.

**Fix**: Use `window.performance.now()` directly (already in the page-load reference frame).

---

### PHO-014 [MEDIUM] `navigator.gpu.requestAdapter` returns a static object with no `info` field

**Where**: line ~858 (requestAdapter override)

**Issue**: Real Chrome's `GPUAdapter` has an `info` property directly (not just `requestAdapterInfo()`). The mock returns an adapter object without `info`, only `requestAdapterInfo()`. A page doing `adapter.info.vendor` (the new WebGPU API) gets `undefined`. This was renamed in Chrome 110+; the old `requestAdapterInfo` is being deprecated.

**Fix**: Add `info: { vendor, architecture, device, description }` to the adapter.

---

### PHO-015 [MEDIUM] `getScreenDetails` returns `currentScreen: null` but real Chrome returns the primary screen

**Where**: line ~510 (getScreenDetails override)

**Issue**: Real Chrome's `window.getScreenDetails()` resolves with `currentScreen` set to the screen containing the window. The mock returns `currentScreen: null`, which is invalid per the spec — the property is required and must be a `ScreenDetails` object.

**Fix**: Set `currentScreen: screens[0]` (the primary screen, which is the only one in this multi-monitor-less setup).

---

### PHO-016 [MEDIUM] `document.cookie` mock doesn't preserve cookie attributes

**Where**: line ~410-440 (document.cookie override)

**Issue**: The mock extracts `name=value` and discards everything else (path, domain, expires, secure, samesite). A page doing `document.cookie = "foo=bar; path=/; expires=Thu, 01 Jan 2026 00:00:00 GMT; Secure"` gets `foo=bar` stored, but the attributes are gone. A subsequent read shows just `foo=bar`, missing the attributes. Real Chrome's `document.cookie` getter only returns name=value pairs (not attributes), but a `Set-Cookie` parser that reads via `document.cookie` won't see the attributes anyway, so this is actually correct. **However**: the mock also rejects cookies starting with `__Secure-` or `__Host-` from being SET (it silently drops them). A real browser rejects setting a `__Secure-` cookie without the `Secure` flag, but accepts them when `Secure` IS set. The mock's filter is overly broad.

**Fix**: Only drop `__Secure-*`/`__Host-*` cookies if they don't have the Secure attribute set in the input.

---

### PHO-017 [MEDIUM] `navigator.locks.request` mock returns wrong shape

**Where**: line ~456 (navigator.locks override)

**Issue**: Real `navigator.locks.request(name, callback)` returns the **result of the callback** (whatever the callback returns), not `{ name, mode, held: false }`. The mock wraps the callback result in an object, breaking legitimate use cases:
```js
const result = await navigator.locks.request('my-lock', async () => 42);
// Real: result === 42
// Mock: result === { name: 'my-lock', mode: 'exclusive', held: false }
```

**Fix**: Return `await callback()` directly. The `{ name, mode, held }` shape is what `navigator.locks.query()` returns, not `request()`.

---

### PHO-018 [MEDIUM] `Element.animate()` mock returns shape that's wrong for some use cases

**Where**: line ~720 (Element.animate override)

**Issue**: The mock returns `{ playState: 'finished', ... }`. But real `Element.animate()` returns a `Animation` object with `playState: 'running'` initially. Code that does:
```js
const anim = el.animate(...);
console.log(anim.playState);  // expect 'running', get 'finished'
```
…will be detectably broken.

**Fix**: Set `playState: 'running'` initially.

---

### PHO-019 [MEDIUM] `setTimeout` drift only added to delay=0 calls, not larger delays

**Where**: line ~895 (setTimeout override)

**Issue**: 
```js
if (delay === 0 || delay === undefined) {
    const drift = _setTimeoutDriftBase + (Math.random() * 2.5);  // 1.5-4ms
    actualDelay = Math.round(drift);
} else if (delay < 4) {
    actualDelay = delay + Math.round(_setTimeoutDriftBase * (delay / 4));
}
```
For delays >= 4ms, no drift is added. But real Chrome has small drift (0.1-0.5ms) on all timer firings, not just the 0ms case. A fingerprint grid calling `setTimeout(fn, 100)` and measuring when fn actually fires will see exactly 100ms, not 100.2-100.4ms.

**Fix**: Add small drift (0.1-0.5ms) to all timer firings, not just the zero case.

---

### PHO-020 [MEDIUM] `CSS.supports` override returns `true` for too many properties

**Where**: line ~580 (CSS.supports override)

**Issue**: 
```js
const commonProps = ['display', 'color', 'background', 'font', 'margin', 'padding', 'border', 'width', 'height'];
if (commonProps.some(p => property.startsWith(p))) return true;
```
This returns `true` for any property starting with these strings. So `CSS.supports('displayzzzz', 'foo')` returns `true`. Real CSS.supports does property name validation first; an invalid property name returns `false`.

**Fix**: Exact match on the common property list, not startsWith.

---

### PHO-021 [LOW] `window.chrome.runtime.connect` and `sendMessage` are empty no-ops

**Where**: line ~380

**Issue**: Empty `function() {}` callbacks. Real Chrome's `chrome.runtime.connect` returns a `Port` object with `name`, `postMessage`, `onMessage`, `disconnect`, etc. A page that does:
```js
const port = chrome.runtime.connect({ name: 'foo' });
port.postMessage({ hello: 'world' });  // No-op in mock, real Chrome would queue
port.onMessage.addListener(...);  // Mock returns { addListener: function() {} }
```
…would not crash, but the page would silently fail to communicate. This is mostly fine for stealth (most pages don't depend on extension messaging) but a strict behavioral check would notice.

**Fix**: Either return a no-op Port mock with all expected methods, or document the limitation.

---

### PHO-022 [LOW] `window.FontFace` mock is missing `disconnect` and `load()` async behavior

**Where**: line ~822 (FontFace override)

**Issue**: The mock has `status: 'unloaded'` and `loaded: Promise.resolve(this)`, but the real `FontFace` has `status` that transitions to `'loading'` then `'loaded'`. Code checking `fontFace.status === 'loading'` would never see that state in the mock.

**Fix**: Set `status: 'loaded'` directly (skip the loading phase) for simplicity.

---

### PHO-023 [LOW] `trustedTypes.createPolicy` mock returns same shape regardless of policy

**Where**: line ~556 (trustedTypes override)

**Issue**: Real `trustedTypes.createPolicy(name, rules)` calls the rules' `createHTML` etc. as functions to sanitize input. The mock just returns the input as-is. A page that depends on trusted-types sanitization (e.g., DOMPurify via TT) will work but won't be sanitized. This is a behavioral mismatch but not a detection vector (the policies return the same shape).

**Fix**: Document the limitation. For stealth this is correct.

---

### PHO-024 [LOW] `navigator.connection` `change` event fires but listeners weren't registered

**Where**: line ~1170

**Issue**: The drift loop dispatches `change` events on `navigator.connection`, but most pages never register a listener (they read `rtt`/`downlink` synchronously). The dispatched event has no effect and is wasteful. More importantly, if a real listener is registered, it will fire with no real change in values (since the dispatched event is from a setTimeout, not an actual network state change).

**Fix**: Only dispatch the change event if at least one listener is registered (track via `addEventListener` count).

---

### PHO-025 [LOW] `WebGL getSupportedExtensions` returns hard-coded list that doesn't include SwiftShader-specific extensions

**Where**: line ~775 (mockExtensions)

**Issue**: The list is reasonable for a real Chrome 120 on a real GPU, but CloakBrowser uses SwiftShader, which has slightly different extension support (it has `WEBGL_compressed_texture_etc1` but not all the `EXT_*` ones). If the manifest says `webgl_draw_latency === 'cloak-disabled'`, that means SwiftShader is in use, but the extensions list says otherwise.

**Fix**: Detect SwiftShader via the `cloakManifest.webgl_draw_latency` flag and adjust the extension list.

---

### PHO-026 [LOW] `Intl.Collator.prototype.resolvedOptions` only locks `locale`, not other options

**Where**: line ~1271 (Intl.Collator)

**Issue**: 
```js
options.locale = primaryLocale;
```
Real `resolvedOptions()` returns `{ locale, usage, sensitivity, ignorePunctuation, numeric, caseFirst, collation }`. By only overwriting `locale`, the other fields (especially `collation`) leak the real system settings. A page doing:
```js
new Intl.Collator('en-US').resolvedOptions().collation
// Real: 'default' or system-specific
// Mock: real value (not overridden)
```
…sees the host's collation settings, not the persona's.

**Fix**: Lock at least `collation` and `numeric` to standard values.

---

### PHO-027 [LOW] `window.orientation` override only applies to Windows, not other platforms

**Where**: line ~705 (window.orientation)

**Issue**: The override is inside `if (FP_CONFIG.isWindows)`. On Mac or Linux, `window.orientation` is not defined. Real Mac/Linux Chrome also doesn't have `window.orientation` defined. So this is correct. But the comment says "v6.1" and the conditional logic is unclear. Document the intent.

**Fix**: No code change; just comment cleanup.

---

### PHO-028 [LOW] `navigator.userAgentData.getHighEntropyValues` filters out unknown hints silently

**Where**: line ~310 (mockUAData.getHighEntropyValues)

**Issue**: 
```js
for (const hint of hints) {
    if (hint in full) filtered[hint] = full[hint];
}
```
If a hint is requested that isn't in `full`, it's silently dropped. Real Chrome would include `null` or a default value. A page requesting all hints and checking for `null` would not see the same shape.

**Fix**: Include a default `null` for unknown hints, matching real Chrome's behavior.

---

### PHO-029 [LOW] `document.fonts.check` always returns `true` for any font query

**Where**: line ~838 (document.fonts.check override)

**Issue**: The override ignores the `font` argument and returns `true` always. Real Chrome's `document.fonts.check("12px 'NonExistentFont'")` returns `false`. A page doing font availability probing will see all fonts as available, which is detectable (e.g., "12px 'DefinitelyNotARealFont'" should be `false`).

**Fix**: Match against a whitelist of plausible font names. If the requested font isn't in the whitelist, return `false`.

---

### PHO-030 [LOW] `screen.orientation` mock's `addEventListener` is a no-op

**Where**: line ~696 (fakeOrientation)

**Issue**: Same as PHO-024: an event listener registered on the mock never fires. The mock dispatches no events.

**Fix**: Add a no-op event dispatch loop if needed, or document.

---

### Cross-file findings for `prototype_hardener.js`

| Finding | Related files |
|---|---|
| PHO-001 (Intl TZ ignore caller option) | None — JS-only |
| PHO-003 (chrome.runtime.id alphabet) | `verify_tls_stealth.py` should also check the ID alphabet |
| PHO-004 (chrome.csi unstable) | `hardened_orchestrator.py` may rely on stable csi values |
| PHO-009 (Permissions defaults) | `identity_health.py` permission state should be consistent |
| PHO-013 (AbortSignal timeStamp frame) | `humanized_movement.py` may use AbortSignal for race detection |
| PHO-018 (Element.animate state) | `hci_reading_model.py` may use animation for scroll-snap |

---

### Summary for `prototype_hardener.js`

- **Critical**: 2 (PHO-001, PHO-002)
- **High**: 7 (PHO-003, PHO-004, PHO-005, PHO-006, PHO-007, PHO-008, PHO-009)
- **Medium**: 9 (PHO-010 through PHO-020)
- **Low**: 10 (PHO-021 through PHO-030)
- **Total**: 28 issues

---

## HOR — `hardened_orchestrator.py` (2,115 LOC)

### HOR-001 [CRITICAL] `uaFullVersion` extraction crashes on non-Chrome UAs

**Where**: line ~1120 (target_config template)

**Issue**:
```python
uaFullVersion: {json.dumps(fp.get('navigator', {}).get('userAgent', '').split('Chrome/')[1].split(' ')[0] if 'Chrome/' in fp.get('navigator', {}).get('userAgent', '') else "120.0.6099.109")},
```
When the fingerprint is a Firefox persona (`userAgent` doesn't contain "Chrome/"), this evaluates correctly. BUT when `userAgent` is **empty string** (`""`), `"Chrome/" in ""` is `False`, so it falls through to the default `"120.0.6099.109"`. When `userAgent` is `None` (missing from fingerprint JSON), `"Chrome/" in None` raises `TypeError: argument of type 'NoneType' is not iterable`. The whole template string construction crashes. This blocks all tasks for personas with missing UA fields.

**Fix**: Use `.get()` with explicit `or ''` default and check `isinstance`.

---

### HOR-002 [CRITICAL] `_panic_handler` calls `os._exit(99)` but `atexit` registered handler may not run

**Where**: line ~360 (`_panic_handler`)

**Issue**: 
```python
atexit.register(_async_panic_cleanup)
...
os._exit(99)
```
`os._exit()` is documented to bypass atexit handlers. The atexit registration is therefore useless — the cleanup function is registered but never called because `_exit()` doesn't run atexit. The intent (per the comment) is to run async cleanup in a daemon thread before exit, but the implementation does not do that.

**Fix**: Run the cleanup synchronously in a thread before calling `os._exit`:
```python
t = threading.Thread(target=_async_panic_cleanup)
t.start()
t.join(timeout=3.0)
os._exit(99)
```

---

### HOR-003 [HIGH] `_verify_dmesg_blocked` returns `True` on any exception, silently allowing kernel leak

**Where**: line ~245

**Issue**:
```python
def _verify_dmesg_blocked():
    try:
        result = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and result.stdout.strip():
            logger.warning("dmesg is readable; host kernel version may leak.")
            return False
        return True
    except Exception:
        return True
```
When `dmesg` raises an exception (e.g., `OSError: [Errno 13] Permission denied`), we return `True` (blocked). The orchestrator continues. But the actual state is "dmesg failed for a reason other than 'no output'" — which could be a permission denied (good) OR a segfault (bad). Returning `True` lumps these together. A stricter implementation would log the exception type.

**Fix**: Log the exception type, and only return `True` if the exception indicates "permission denied" or "command not found", not on generic OSError.

---

### HOR-004 [HIGH] `check_proxy_integrity` may use icanhazip.com as default if `IP_CHECK_URL` env not set and overridden

**Where**: line ~310

**Issue**:
```python
ip_check_url = os.getenv("IP_CHECK_URL", "http://172.18.0.1:8888/ip")
```
The default `172.18.0.1:8888` is a custom internal endpoint. If the env var is not set, we use this default. If the env var IS set to `""` (empty string), `os.getenv` returns `""` (not the default) and we use an empty URL, which causes httpx to throw a `MissingSchema` exception. The exception is caught and `False` is returned, blocking the launch. This is fail-closed but the failure mode is confusing (the operator set the env var explicitly).

**Fix**: Treat empty string the same as not set: `ip_check_url = os.getenv("IP_CHECK_URL") or "http://172.18.0.1:8888/ip"`.

---

### HOR-005 [HIGH] Persona rotation uses `(datetime.datetime.utcnow() - created_at)` without timezone awareness

**Where**: line ~870

**Issue**:
```python
age_hours = (datetime.datetime.utcnow() - created_at).total_seconds() / 3600
```
`datetime.datetime.utcnow()` returns a **naive** datetime (no tzinfo). If `created_at` is stored as `TIMESTAMP WITH TIME ZONE` in PostgreSQL, asyncpg returns it as a **tz-aware** datetime. The subtraction raises `TypeError: can't subtract offset-naive and offset-aware datetimes`. The exception is caught, but the rotation check is silently skipped, meaning personas never get rotated.

**Fix**: Use `datetime.datetime.now(datetime.timezone.utc)` to match asyncpg's aware datetimes, or convert `created_at` to naive: `created_at.replace(tzinfo=None)` before subtraction.

---

### HOR-006 [HIGH] `cors_to_spoof = min(fp.get('navigator', {}).get('hardwareConcurrency', 8), 4)` caps at 4

**Where**: line ~1100

**Issue**:
```python
cores_to_spoof = min(fp.get('navigator', {}).get('hardwareConcurrency', 8), 4)
```
Real Chrome users have a distribution of core counts: ~30% have 4, ~50% have 8, ~15% have 16, ~5% have 2/12/24/32. Capping at 4 means **a persona with `hardwareConcurrency=16` reports 4 in the FP_CONFIG injected into the browser**. The real CPU has 16 cores; if the JS layer's override leaks the real value (e.g., via `navigator.hardwareConcurrency` getter not being patched, see PHO-001 in JS review), the persona's `hardwareConcurrency` field will be 4 while the real CPU's is 16, and the persona's behavior (multi-threaded loops, e.g., on heavy sites) will be wrong.

**Fix**: Use the value from `persona_digest["hardware_concurrency"]` (which has the realistic distribution from R6 fix), or remove the `min(..., 4)` cap.

---

### HOR-007 [HIGH] `db_pool.acquire()` calls may not honor `min_size=2, max_size=4` under high churn

**Where**: line ~1500 (db_pool init)

**Issue**: The pool is configured for 2-4 connections. Each persona task does ~3-5 sequential queries (acquire → fetchrow → release, repeat). With 1 persona this is fine. But the daemon also has the queue polling loop running every 5s (`DAEMON_POLL_INTERVAL=5`), which acquires a connection each time. If a task is in progress and using 1 connection, the daemon's poll needs a 2nd. If 2 tasks are briefly overlapping (during cleanup), we'd need 3. The pool is sized for this, but **if asyncpg encounters a transient connection error, the pool may close all connections and try to recreate them, but `min_size=2` means it must succeed in creating 2 new connections before any acquire returns**. If the DB is briefly unreachable, the pool blocks until timeout.

**Fix**: Document the pool's behavior. Set `min_size=1, max_size=4` to avoid the strict-2 requirement, and let the pool grow on demand.

---

### HOR-008 [HIGH] `socks5_port_for_persona` uses `hashlib.md5` for routing, but `md5` is broken in some hardened containers

**Where**: line ~165 (SOCKS5 port mapping)

**Issue**: `hashlib.md5(persona_id.encode('utf-8'))` is used to deterministically map a persona to a SOCKS5 port. On some Debian-derived images, `md5` is in the `libgcrypt` blacklist (FIPS mode). The exception is caught silently by `hashlib.md5`'s default fallback to OpenSSL, but if OpenSSL is also FIPS-mode, the call may raise. The fallback in `int(...hexdigest()[:8], 16)` will then crash.

**Fix**: Use `hashlib.sha256` (always available) instead of `md5`.

---

### HOR-009 [MEDIUM] `first_navigation_delay` always uses log-normal but may produce values outside [min, max]

**Where**: line ~870 (in `run_persona_pipeline`)

**Issue**:
```python
def _human_like_delay(min_s: float, max_s: float) -> float:
    mean = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 4
    delay = math.exp(random.gauss(math.log(mean), sigma / mean))
    return max(min_s, min(max_s, delay))
```
The log-normal generator clamps the output to `[min_s, max_s]` via `max(..., min(..., delay))`. This means values at the edges of the distribution are clipped, **creating a non-log-normal distribution with mass accumulating at the edges**. A page measuring the actual delay will see a clipped distribution. The clipping is necessary to prevent negative or extreme values, but the **clipping should be done in log-space, not in the output space**, to preserve the distribution shape.

**Fix**: Generate `gauss_val`, clamp `gauss_val` to e.g. ±2 sigma in log-space, then exp. This preserves the log-normal shape at the cost of slightly narrower tails.

---

### HOR-010 [MEDIUM] `_fernet_decrypt_bytes` uses signal-based timeout which conflicts with async

**Where**: line ~280

**Issue**:
```python
signal.setitimer(signal.ITIMER_REAL, timeout_s)
```
This uses SIGALRM to enforce a timeout. But the orchestrator is async and runs in the main thread; SIGALRM fires in the main thread and interrupts whatever coroutine is running. The signal handler raises `TimeoutError`, which is caught by the `try/finally` to clear the timer. However, if the orchestrator is awaiting something in the event loop (e.g., `await db_pool.acquire()`), the SIGALRM fires during the await, the timeout handler raises an exception, and the await is interrupted. The decryption timeout is correctly enforced, but the side effects include: the signal handler running in the middle of an event loop iteration, and any coroutine waiting on a Future is now in an undefined state.

**Fix**: Use `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=timeout_s)` instead of SIGALRM.

---

### HOR-011 [MEDIUM] `compositor_jitter_sleep` uses `random.uniform` not seeded random

**Where**: line ~795

**Issue**:
```python
jitter = random.uniform(-BE_COMPOSITOR_JITTER_MS / 1000.0, BE_COMPOSITOR_JITTER_MS / 1000.0)
```
This is unseeded `random`. Across calls, the jitter is uniformly distributed in the full range. For a per-persona determinism guarantee, the jitter should be seeded by the persona. Two launches of the same persona will have different jitter sequences.

**Fix**: Use a per-persona seeded RNG: `persona_jitter_rng = random.Random(stable_hash(persona_id))`.

---

### HOR-012 [MEDIUM] Quiescence delay doesn't account for first launch of persona

**Where**: line ~890 (in `run_persona_pipeline`)

**Issue**:
```python
quiescence = _human_like_delay(
    PERSONA_QUIESCENCE_BEFORE_TASK_S_MIN,  # default 30
    PERSONA_QUIESCENCE_BEFORE_TASK_S_MAX   # default 180
)
```
A first-launch persona (never used before) should have a longer quiescence: a real user takes longer to "settle in" on a new account than to resume a familiar one. A 5th-launch persona could plausibly have 5-15s quiescence. The current code always uses the same range regardless of persona history.

**Fix**: Reduce the range for `task_count > 3` personas. E.g., `min_s = 30 if persona.task_count < 3 else 10; max_s = 180 if persona.task_count < 3 else 90`.

---

### HOR-013 [MEDIUM] `_clear_browser_storage` uses a hard-coded 2-second sleep, not async signal

**Where**: line ~370

**Issue**:
```python
await asyncio.sleep(2.0)
```
The comment says "Wait for async writes to complete before tmpfs wipe" but the implementation just sleeps a fixed 2 seconds. This is wasteful (real writes complete in <100ms typically) and unreliable (a slow page with pending IndexedDB writes may need 5+ seconds).

**Fix**: Poll the IndexedDB transaction list or use a `Promise.all` to wait for all pending writes:
```python
await page.evaluate("""
    async () => {
        if (window.__pendingWrites) {
            await Promise.allSettled(window.__pendingWrites);
        }
    }
""")
```

---

### HOR-014 [MEDIUM] `_verify_single_tmpfs_invariant` uses `subprocess.run` synchronously in async context

**Where**: line ~210

**Issue**: The function uses `subprocess.run(...)` which blocks the event loop for up to 5 seconds (the `timeout=5`). If the orchestrator is awaiting something else and the daemon loop is also running, this blocks both.

**Fix**: Use `await asyncio.create_subprocess_exec("mount", ...)` and `await proc.communicate()`.

---

### HOR-015 [MEDIUM] Session pacing `_human_like_delay(60.0, 115.0)` for short sessions produces too-wide a range

**Where**: line ~1340

**Issue**: The "short session" 30% probability bucket is `[60, 115]` seconds. A real user's short browsing session is typically 30-180s, not 60-115s. The narrow range is detectable: every short session is between 1 and 2 minutes, never 30s or 3 minutes. Anti-bot grids cataloging session-length distributions would see this artificial bimodality.

**Fix**: Widen the short session range: `30-180s`. Adjust the log-normal to `mean=90, sigma=50`.

---

### HOR-016 [MEDIUM] `cors_to_spoof` (sic, typo) variable name should be `cores_to_spoof`

**Where**: line ~1100

**Issue**: Typo: `cors_to_spoof` instead of `cores_to_spoof`. Not a behavioral bug, but the typo is in the line that defines the value injected into FP_CONFIG.

**Fix**: Rename.

---

### HOR-017 [MEDIUM] Cold-start schema auto-init uses `conn.execute(clean_sql)` which may not handle multi-statement SQL

**Where**: line ~1530

**Issue**:
```python
await conn.execute(clean_sql)
```
`conn.execute()` runs a single statement. If `schema.sql` contains multiple statements (CREATE TABLE, CREATE INDEX, etc.), only the first runs. Subsequent statements are silently dropped. The schema init would appear to succeed but tables beyond the first would be missing.

**Fix**: Use `conn.execute(schema_content)` (no clean_sql stripping) and let asyncpg handle multi-statement if possible, OR split on `;\n` and run each statement.

---

### HOR-018 [MEDIUM] `parse_proxy_url` doesn't validate the SOCKS scheme

**Where**: line ~285

**Issue**:
```python
parsed = urllib.parse.urlparse(proxy_url)
server = parsed.hostname
port = parsed.port
if not server or not port:
    return None
result = {"server": f"{server}:{port}"}
```
The function accepts any URL with a host:port, including `http://`, `https://`. It doesn't check that the scheme is `socks5` or `socks5h`. If the operator accidentally passes `http://proxy:8080`, the result `{"server": "proxy:8080"}` is passed to Playwright's `proxy` field, which would attempt to use HTTP proxying, but the `chromium_args` include `--proxy-server=direct://` defaults that may conflict.

**Fix**: Check `parsed.scheme in ('socks5', 'socks5h')` and reject others.

---

### HOR-019 [MEDIUM] `orchestrator_daemon_loop` doesn't close the db_pool on loop exit (other than signal)

**Where**: line ~1620+

**Issue**: If the daemon loop exits due to a non-signal cause (e.g., a `KeyboardInterrupt` from the main thread, or a `RuntimeError`), the `db_pool` is never closed. The asyncpg connection pool hangs open, potentially blocking shutdown.

**Fix**: Wrap the main loop in `try/finally` that closes `db_pool`.

---

### HOR-020 [MEDIUM] The `atexit` cleanup handler is registered but never called

**Where**: line ~410 (`_panic_handler`)

**Issue**: Same as HOR-002, but for the `handle_graceful_shutdown` path: if the daemon loop is interrupted by a non-signal condition (e.g., the main thread is killed via `kill -9`), neither the signal handler nor the atexit handler runs. The DB pool and tmpfs cleanup are skipped.

**Fix**: Add a `try/finally` around the `loop.run_until_complete(orchestrator_daemon_loop())` call that explicitly closes the pool.

---

### HOR-021 [MEDIUM] Session state encryption uses default executor without limiting concurrency

**Where**: line ~1280

**Issue**:
```python
loop.run_in_executor(None, get_serializer().encrypt_payload, raw_session_dict)
```
`None` means use the default `ThreadPoolExecutor` which has no bound on thread count. If 10 tasks are encrypted in parallel, 10 threads are spawned. Each Fernet encryption is CPU-bound, so this starves the GIL.

**Fix**: Use a module-level `ProcessPoolExecutor(max_workers=MAX_CRYPTO_WORKERS)` (already defined as env var).

---

### HOR-022 [MEDIUM] `task_count` check for retirement uses `>=` which is one task too many

**Where**: line ~1310

**Issue**:
```python
if new_count and new_count >= MAX_TASKS_PER_PERSONA:
```
If `MAX_TASKS_PER_PERSONA=10`, the persona is retired **after** completing the 10th task. So the persona does 10 tasks total, not 9. The variable name `MAX_TASKS_PER_PERSONA` suggests "max is 10", but actual max is 10. The intent of "max 10 tasks before retirement" is met (the 10th task triggers retirement, but completes first), but the persona continues to exist in the DB for 1 extra task.

**Fix**: Change to `>` to retire BEFORE the 10th task, or rename to `RETIREMENT_THRESHOLD` for clarity.

---

### HOR-023 [LOW] `webgl_vendor` default doesn't match persona platform

**Where**: line ~1100

**Issue**:
```python
webgl_vendor = fp.get('webgl', {}).get('vendor', 'Intel Inc.')
webgl_renderer = fp.get('webgl', {}).get('renderer', 'Intel(R) Iris(TM) Plus Graphics')
```
The default is Intel hardware on all platforms. A Mac persona with no `webgl` field in fingerprint gets Intel defaults, which is wrong (Macs have AMD or Apple Silicon GPUs). The real JS override (PHO-025) uses these defaults, which is a Mac-fingerprint mismatch.

**Fix**: Platform-aware default: `webgl_renderer = "Apple M1"` if platform is Mac.

---

### HOR-024 [LOW] `persona_digest` is not cleared from memory after task

**Where**: line ~1130+ (multiple locations)

**Issue**: The `persona_digest` dict (containing persona secrets, session IDs, etc.) is built per-task and lives until the function returns. After the task, the dict is still in memory until garbage collected. A page doing `JSON.stringify(window.__sre_fp_config)` will not see this, but a debugger attached to the process could.

**Fix**: Set `persona_digest = None` and `gc.collect()` in a `finally` block.

---

### HOR-025 [LOW] `MASTER_XVFB_DISPLAY` env var is read twice with different defaults

**Where**: lines ~1000, ~1015, ~1080

**Issue**: 
- Line 1000: `master_display = os.getenv("MASTER_XVFB_DISPLAY", "")`
- Line 1015: re-read with default "99" elsewhere
- Line 1080: re-read again
This is not a bug per se, but the inconsistency makes the code harder to reason about.

**Fix**: Read once at module init: `_MASTER_XVFB_DISPLAY = os.getenv("MASTER_XVFB_DISPLAY", ":99")` and reuse.

---

### HOR-026 [LOW] `_compute_persona_digest` uses `hashlib.sha256(persona_id.encode('utf-8'))` for stable values, but the digest is recomputed on every task

**Where**: line ~700

**Issue**: The function is called per-task. The result is deterministic (same persona_id → same digest), so this is correct. But the comment says "locked at persona creation time" — implying it should be stored in the DB once. The recompute on every task is wasted CPU and produces a fresh `notification_session_count` from the DB query (which is correct), but the other values (hardware_concurrency, battery_level, etc.) are recomputed unnecessarily.

**Fix**: Either store the digest in the DB and read it once, or accept the recompute as a design choice and update the comment.

---

### HOR-027 [LOW] `_panic_handler` uses `os._exit(99)` but the comment says "exit with code 99" — but `os._exit` does NOT return 99 to the parent if atexit hooks are partially run

**Where**: line ~410

**Issue**: The supervisor (Docker, systemd, etc.) interprets exit code 99 as "do not restart". But `os._exit(99)` may or may not propagate depending on whether the async cleanup thread (which doesn't actually run, see HOR-002) interfered. The exit code is correct, but the lack of cleanup is a stealth concern (tmpfs contents may persist briefly).

**Fix**: Clean tmpfs synchronously BEFORE `os._exit`:
```python
if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
    shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
os._exit(99)
```

---

### HOR-028 [LOW] `WEBRTC_MODERN_FLAGS` constant is defined but uses `--enable-features=WebRtcHideLocalIpsWithMdns` which is the OLD flag

**Where**: line ~310

**Issue**: The flag `WebRtcHideLocalIpsWithMdns` was renamed in Chrome 110+ to `WebRtcAllowInputVolumeAdjustment` (which is the new one). The first flag in the list is deprecated and may not be recognized by Chrome 120, producing a startup warning. The second flag in the list is correct but the first is wrong.

**Fix**: Use only the modern flag: `["--enable-features=WebRtcAllowInputVolumeAdjustment", "--force-webrtc-ip-handling-policy=default_public_interface_only"]`. Or remove the first flag entirely.

---

### HOR-029 [LOW] `tc qdisc del` in panic handler doesn't check if `eth0` is the right interface

**Where**: line ~330

**Issue**: 
```python
subprocess.run(["tc", "qdisc", "del", "dev", "eth0", "root"], ...)
```
Hard-coded `eth0`. If the container uses `eth1`, `ens5`, or `veth*` (Docker default), this command fails silently and leaves the netem rule in place. The next container may inherit it.

**Fix**: Detect the interface dynamically: `subprocess.run(["ip", "route", "show", "default"], ...)` and parse.

---

### HOR-030 [LOW] `_clear_browser_storage` runs `await page.evaluate(...)` without timeout

**Where**: line ~370

**Issue**: The evaluate call has no timeout. A page with a synchronous infinite loop in JS would hang the entire orchestrator. The `try/except` catches the eventual error, but the orchestrator is unresponsive in the meantime.

**Fix**: Add `await asyncio.wait_for(page.evaluate(...), timeout=5.0)`.

---

### Summary for `hardened_orchestrator.py`

- **Critical**: 2 (HOR-001, HOR-002)
- **High**: 6 (HOR-003, HOR-004, HOR-005, HOR-006, HOR-007, HOR-008)
- **Medium**: 13 (HOR-009 through HOR-022)
- **Low**: 9 (HOR-023 through HOR-030)
- **Total**: 30 issues

---

## Continuing the R7 review

The remaining files (entrypoint.sh, Dockerfile, sticky_proxy_resolver.py, behavioral_entropy.py, keyboard_emulator.py, hci_reading_model.py, humanized_movement.py, session_serializer.py, and 11 R6 files) will be added in subsequent edits to this document.

Cumulative count so far:
- **PHO**: 28 issues
- **HOR**: 30 issues
- **Total so far**: 58 issues
- **Estimated remaining**: 100-140 issues across 19 files
- **Final estimate**: 160-200 issues across 21 files

