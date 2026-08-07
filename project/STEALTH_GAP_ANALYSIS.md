# Stealth Gap Analysis — What Is Still Missing for True Invisibility

**Date**: 2026-07-13
**File analyzed**: `browser-runtime/sticky_proxy_resolver.py` (and related files)
**Perspective**: Pure stealth / anti-fingerprinting — what anti-bot grids
(reCAPTCHA Enterprise, Cloudflare Turnstile, Akamai Bot Manager,
PerimeterX/HUMAN) can STILL detect on a persona launched through our stack.

This is NOT a code-quality audit. It is a list of fingerprinting vectors
that have not yet been closed, ordered by how likely they are to cause
a real bot detection in 2026.

---

## Category 1: HIGH-PRIORITY Detection Vectors (likely to flag in 2026)

### INVIS-1: WebRTC leaks the container's internal IP even with `--disable-webrtc`
**Likelihood**: HIGH (5/5 grids check this)
**Current state**: `hardened_orchestrator.py` sets `--disable-webrtc` flag.
**The problem**: Even with the flag off, several JS APIs leak the local
network interface through alternative paths:
- `navigator.mediaDevices.enumerateDevices()` returns network labels
  that include MAC-derived host info on some Chromium builds.
- `RTCPeerConnection` may not be fully removed (the flag is best-effort).
- `chrome://webrtc-internals` is unreachable from JS, but the
  internal `webrtc::` traces in DevTools leak.

**What's missing**: A JS-level `navigator.mediaDevices` mock that returns
ONLY devices consistent with the persona (no IPv6 link-local addresses
like `fe80::` or `169.254.x.x` link-local IPs).

### INVIS-2: `navigator.connection` values are static and don't drift
**Likelihood**: HIGH
**Current state**: `prototype_hardener.js:2048-2097` overrides
`connection.type`, `effectiveType`, `downlink`, `rtt`, `saveData`.
**The problem**: A real Chrome on a residential connection has these
values that **change every few seconds** (rtt fluctuates, downlink
varies with network conditions). Our static values are detectable
over a session — the distribution of rtt values is too narrow
(essentially constant).

**What's missing**: A **jitter layer** that adds realistic noise
to rtt (±10-30ms) and downlink (±0.5-2 Mbps) on a slow timer
(every 5-15 seconds). The noise should follow a normal distribution
centered on the persona's base values.

### INVIS-3: `Intl.DateTimeFormat` is not aligned with proxy geo + UTC offset
**Likelihood**: HIGH (reCAPTCHA Enterprise top-5)
**Current state**: `hardened_orchestrator.py` sets `timezone_id=timezone`
in the Playwright launch, and `prototype_hardener.js:2113-2127` locks
`Intl.NumberFormat` to persona locale.
**The problem**: `Intl.DateTimeFormat().resolvedOptions().timeZone`
is set by Playwright, but:
- `Date.getTimezoneOffset()` returns the **current** UTC offset,
  which drifts with DST. A persona in `America/Chicago` in July
  returns `-300` (CDT), but the same persona in January returns
  `-360` (CST). If the persona was created in winter and the
  task runs in summer, this is a fingerprint.
- `Intl.DateTimeFormat('en-US', {hour12: false})` etc. are NOT
  locked — they can leak the container's actual locale.

**What's missing**: Lock `Date.getTimezoneOffset()` to a per-persona
value (not Playwright's, which is the real system TZ), and
override `Intl.DateTimeFormat` to return only persona-consistent
options.

### INVIS-4: `performance.now()` resolution is detectable
**Likelihood**: HIGH (FingerprintJS Pro and CRE use this)
**Current state**: `prototype_hardener.js:1700+` overrides
`Math.random` with seeded PRNG; `performance.now()` is used as-is.
**The problem**: Modern Chrome's `performance.now()` has a
**resolution** of 5μs (high-precision time) or 100μs (reduced-precision).
Anti-bot grids can:
- Call `performance.now()` in a tight loop and measure the
  granularity. A 5μs resolution shows real Chrome; a 100μs
  resolution shows privacy-mode Chrome.
- Call `performance.now()` before/after a `setTimeout(0)` and
  measure drift. Real Chrome has ~1-4ms drift; automated
  environments often have <0.1ms drift.

**What's missing**: Override `performance.now()` to clamp
resolution to a per-persona value (e.g., 100μs) and add
realistic drift on `setTimeout(0)`.

### INVIS-5: Hardware concurrency (CPU cores) is suspicious if it matches the host
**Likelihood**: MEDIUM-HIGH
**Current state**: `prototype_hardener.js:425` sets
`navigator.hardwareConcurrency` to per-persona value.
**The problem**: A persona with `hardwareConcurrency=4` running
on an 8-core E2.1.Micro VM is statistically unlikely (1 in C(8,4)
≈ 70, but per-persona lock means **every** persona has the same
value). Multiple personas with the **same** hardwareConcurrency
across different geo regions is a clear linkability signal.

**What's missing**: Per-persona random selection from a weighted
distribution (e.g., 30% of personas get 4 cores, 50% get 8,
20% get 16, weighted by US consumer hardware census data).

### INVIS-6: Canvas noise is per-persona, not per-session
**Likelihood**: MEDIUM-HIGH
**Current state**: `prototype_hardener.js:266` uses
`FP_CONFIG.canvas_noise_seed` (per-persona).
**The problem**: Real Chrome's canvas output is **deterministic
per session** (depends on GPU drivers, font rendering, etc.) but
**changes across sessions** for the same user (due to driver
updates, font cache changes). Our per-persona seed means the
same canvas output every time for that persona, which is
**unnatural** for a real user.

**What's missing**: Add a **session-scoped** canvas noise
offset (e.g., based on the browser launch timestamp) on top
of the per-persona seed. So `seed + session_offset` produces
slightly different canvas outputs each session for the same
persona, mimicking driver update effects.

### INVIS-7: AudioContext noise has the same per-persona issue as canvas
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:1695-1730` patches
`AudioBuffer.getChannelData` with per-persona noise.
**The problem**: Same as INVIS-6 — per-persona but not
per-session. Real users have audio output that varies
slightly across sessions.

**What's missing**: Same session-scoped offset pattern.

### INVIS-8: WebGL parameters (UNMASKED_RENDERER, vendor) leak SwiftShader info
**Likelihood**: MEDIUM (Akamai Bot Manager uses this)
**Current state**: `prototype_hardener.js:1156-1180` overrides
`getParameter` for vendor/renderer.
**The problem**: CloakBrowser uses SwiftShader for WebGL on
the E2.1.Micro VM (no GPU passthrough). SwiftShader's
internal version, build date, and the specific
`getShaderPrecisionFormat` values are a fingerprint
distinctive of **headless/containerized Chrome** even
with vendor/renderer spoofed. Akamai and PerimeterX
have signature databases of SwiftShader values.

**What's missing**: A `webgl-debug-renderer-info` mock that
returns **real consumer GPU strings** (e.g.,
"ANGLE (Intel, Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.5)")
that are also consistent with the persona's claimed
screen size and platform.

### INVIS-9: `navigator.plugins` and `navigator.mimeTypes` are not fully mocked
**Likelihood**: MEDIUM
**Current state**: Partially handled via `chrome.runtime` mock
at `prototype_hardener.js:598-680`.
**The problem**: `navigator.plugins.length` is checked by
CRE. Real Chrome 120 has 5 plugins (Chrome PDF Plugin, Chrome
PDF Viewer, Native Client, etc.). Our `chrome.runtime` mock
may set plugin count to 0 or a wrong number, which is
detectable.

**What's missing**: Full `navigator.plugins` array with the
correct 5 Chrome 120 plugins, including their filenames
(`internal-pdf-viewer`, `mhjfbmdgcfjbbpaeojofohoefgiehjai`),
descriptions, and `mimeTypes` array.

### INVIS-10: `Notification.permission` returns 'default' but the UI shows a prompt
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:840-846` sets
`Notification.permission` to per-persona value.
**The problem**: A real Chrome user has `Notification.permission`
matching what they actually clicked. If the persona says
"notifications: prompt" (default) but the JS query returns
"denied" or "granted", the cross-check is detectable.

**What's missing**: A consistent state machine for notification
permission that matches the persona's history (e.g., a fresh
persona has "default" forever; a seasoned persona may have
"denied" from a past site).

---

## Category 2: MEDIUM-PRIORITY Detection Vectors (subtle but real)

### INVIS-11: `document.hasFocus()` returns true even when window is backgrounded
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:821-836` overrides
`document.hidden` and `document.visibilityState`.
**The problem**: When the browser tab is in the background
(say, during a long proxy handshake), `document.hasFocus()`
returns false. Real users occasionally background tabs;
automation never does. Persistent `true` is a signal.

**What's missing**: Add a probabilistic `hasFocus() = false`
event (1-3% of the time) to mimic user tab-switching.

### INVIS-12: `window.name` is empty (real Chrome has the URL after navigation)
**Likelihood**: MEDIUM
**Current state**: Not handled in `prototype_hardener.js`.
**The problem**: `window.name` is a string that persists
across navigations within the same tab. Real Chrome often
has it set to the target URL or a marketing tag from a
previous site. Automation usually has it empty or a
default value.

**What's missing**: A pre-navigation `window.name` setter
that fills it with a plausible value (the navigation target
URL, or an empty string if the persona has just opened a
new tab).

### INVIS-13: `history.length` is exactly 1 (about:blank only)
**Likelihood**: MEDIUM
**Current state**: `hardened_orchestrator.py` adds about:blank
to history. The persona should have a longer history.
**The problem**: A real user opening a browser typically has
`history.length >= 3` (welcome tab, settings, then target).
Our about:blank approach gives `history.length == 1` or
`2` (if we navigate to about:blank first).

**What's missing**: A pre-navigation sequence that adds 2-3
plausible URLs to the history (e.g., `chrome://newtab/`,
`https://www.google.com/`, then the target) before the
task URL.

### INVIS-14: `navigator.cookieEnabled` is true, but cookie lifetime is non-deterministic
**Likelihood**: MEDIUM
**Current state**: `hardened_orchestrator.py:1203` sets
`cookieLifetimeDays` per persona. But the actual cookie TTL
on the server side may not match.
**The problem**: Real Chrome's `document.cookie` has a
consistent TTL (set by the server). If our JS-side cookie
lifetime is randomized but the server's Set-Cookie is fixed,
the two values diverge over time.

**What's missing**: Lock the cookie TTL on the server side
to match the persona's per-task TTL. This is a backend
issue, not a JS issue.

### INVIS-15: `navigator.languages` does not include regional variants
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:768-774` sets
`languages` to `[en-US, en]`.
**The problem**: Real US users may have `en-US, en, es-MX`
(if they have Spanish keyboard) or `en-US, en, fr-CA`
(near Canada). Our default is too clean.

**What's missing**: Persona-specific language arrays with
realistic regional variants.

### INVIS-16: `Date.now()` and `Date().toString()` format are locale-dependent
**Likelihood**: MEDIUM
**Current state**: Not explicitly handled.
**The problem**: `(new Date()).toString()` returns format
like `"Sun Jul 13 2026 14:23:45 GMT-0400 (Eastern Daylight Time)"`.
The timezone abbreviation (`EDT`, `PDT`, `BST`) is locale-
sensitive. Anti-bot grids check that the abbreviation matches
the persona's `Intl.DateTimeFormat().resolvedOptions().timeZone`.

**What's missing**: Override `Date.prototype.toString` to
return a consistent abbreviation matching the persona's TZ.

### INVIS-17: `chrome.app` mock is incomplete
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:622-630` mocks
`window.chrome.app` minimally.
**The problem**: Real Chrome has `chrome.app.install`,
`chrome.app.isInstalled`, `chrome.app.runningState()`.
Anti-bot grids check for these. A partial mock is detectable.

**What's missing**: Full `chrome.app` mock with the correct
method signatures and return values.

### INVIS-18: `chrome.csi()` and `chrome.loadTimes()` use `performance.now()`
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:631-680` mocks these
but the values may not be consistent with the persona's
actual navigation timing.
**The problem**: `chrome.csi().startE` is supposed to be
the navigation start time. If our value is off by more
than ~50ms from the actual `performance.timing.navigationStart`,
the mock is detectable.

**What's missing**: Wire the mock values to actual
`performance.timing` values (read from real Chrome, not
synthesized).

### INVIS-19: `navigator.locks` and `navigator.mediaSession` not handled
**Likelihood**: MEDIUM
**Current state**: Not handled in `prototype_hardener.js`.
**The problem**: Chrome 120 has `navigator.locks` (Web Locks
API) and `navigator.mediaSession` (media metadata API).
Real Chrome returns them as objects. Some anti-bot grids
check for their presence.

**What's missing**: Stub `navigator.locks.request` to a
no-op and `navigator.mediaSession.metadata` setter as a
no-op.

### INVIS-20: `navigator.userAgentData` (Client Hints) entropy
**Likelihood**: MEDIUM
**Current state**: `prototype_hardener.js:520-540` mocks
`userAgentData` with per-persona brands.
**The problem**: Real Chrome's `userAgentData` has more
fields: `platformVersion`, `architecture`, `bitness`, `model`,
`uaFullVersion`. The values must be internally consistent
(e.g., a Mac persona should have `architecture: "arm"` or
`"x86"`, but not both). Our `client_hints` field stores
these, but they may not be fully wired through to all
`userAgentData` queries.

**What's missing**: Verify that every `navigator.userAgentData`
getter returns values consistent with the persona.

---

## Category 3: LOWER-PRIORITY But Worth Tracking

### INVIS-21: `navigator.deviceMemory` is too clean
Real Chrome 120 has 2/4/8/16 GB; some have 0.5/1 GB on
low-end devices. Our `deviceMemory` is 4 or 8 (hardcoded
binary). Real distribution has 0.5/1/2/4/8/16.

### INVIS-22: `screen.colorDepth` is always 24
Real Chrome can be 24 or 30 (HDR displays). The persona
should reflect this.

### INVIS-23: `screen.isExtended` not handled
Multi-monitor setups have `screen.isExtended === true`.
Our mock should reflect this for some personas.

### INVIS-24: `window.crossOriginIsolated` not set
Pages with `Cross-Origin-Opener-Policy` headers can be
cross-origin isolated. `window.crossOriginIsolated` is
`true` for isolated contexts. Real Chrome may or may not
be in this state; we should reflect that.

### INVIS-25: `navigator.ink` (Stylus API) not handled
Real Chrome on supported devices has `navigator.ink`. We
currently restrict it but the restriction may be too
strict (real Chrome on non-touch desktops has `ink` as
`undefined`, not throwing).

### INVIS-26: `navigator.managed` not handled
Real Chrome on enterprise-managed devices has
`navigator.managed === true` (or undefined for non-managed).
We currently restrict it to undefined, which is correct
for unmanaged personas but should be a per-persona choice.

### INVIS-27: `window.speechSynthesis.getVoices()` mock is too clean
Real Chrome has 20-50+ voices on most platforms (depends
on installed TTS engines). Our mock returns a few hand-
crafted voices. A more realistic mock would return a
platform-typical list (e.g., 5-10 voices for Windows,
20+ for macOS).

### INVIS-28: `navigator.clipboard.read()` / `write()` not handled
Clipboard API is checked by some anti-bot grids. A
fresh persona should return `NotAllowedError` for
`read()` and `Promise<undefined>` for `write()`.

### INVIS-29: `caches.match()` not handled
Service Worker Cache API queries. We clear them in JS
but real Chrome has specific cache names that anti-bot
grids check.

### INVIS-30: `navigator.serviceWorker` controller not handled
Real Chrome has `navigator.serviceWorker.controller`
set to the active SW (or null). If the page registers
an SW, the controller changes. Our hardener doesn't
track this.

### INVIS-31: `navigator.webdriver` may still be detectable
Real Chrome has `navigator.webdriver === undefined`.
Our hardener should set this; verify it's not
overridden by CloakBrowser.

### INVIS-32: `window.opener` not handled
Cross-window opener detection. If a popup is opened
from another window, `window.opener` is set. Our
hardener should ensure the value matches the persona's
expected behavior (e.g., a fresh persona has no opener).

### INVIS-33: `window.outerWidth` / `outerHeight` are persona-dependent
Real Chrome on a MacBook has `outerWidth = 1440 + chrome`.
On Windows it's `screen.width + 8`. Our `platformOuterHeightDelta`
in `prototype_hardener.js:1531` is hardcoded; per-persona
randomization is missing.

### INVIS-34: `navigator.pdfViewerEnabled` not handled
Real Chrome has `pdfViewerEnabled === true` by default.
Anti-bot grids check this.

### INVIS-35: `navigator.maxTouchPoints` should be 0 for desktop, >0 for touch
Currently `prototype_hardener.js:430` sets it per-persona,
but the value must be consistent with `navigator.platform`
(Mac touchbar = 0, iPad = 5, etc.).

### INVIS-36: `Intl.PluralRules` not locked
Real Chrome has `Intl.PluralRules` with locale-specific
rules. A spoofed locale can produce wrong results for
specific numbers.

### INVIS-37: `Intl.RelativeTimeFormat` not locked
Same as PluralRules.

### INVIS-38: `Intl.ListFormat` not locked
Same as PluralRules.

### INVIS-39: `Intl.DateTimeFormat` `formatToParts` not locked
When `formatToParts` is called, it returns a structured
array of `{type, value}`. Real Chrome returns parts
in a specific order; our mock may not match.

### INVIS-40: `navigator.userAgent` may have trailing junk
Some automation frameworks add `HeadlessChrome` or
`Automation` to the UA. Our UA must be exactly
`Mozilla/5.0 ... Chrome/120.0.0.0 Safari/537.36` with
no trailing text.

### INVIS-41: `document.documentMode` not handled
Real Chrome has `document.documentMode === undefined`.
Internet Explorer has an integer. Our hardener should
verify undefined, not set a value.

### INVIS-42: `window.chrome.csi().onloadT` is the page load time
Must be consistent with the actual `load` event time
within ~50ms.

### INVIS-43: `navigator.productSub` should be `"20030107"` for Chrome
We have this at `prototype_hardener.js:434` — verified
correct.

### INVIS-44: `navigator.vendor` should be `"Google Inc."` for Chrome
Verified at `prototype_hardener.js:435`.

### INVIS-45: `navigator.appName` should be `"Netscape"`
Real Chrome returns `"Netscape"` (legacy compatibility).
Some automation frameworks return `"Chrome"` or
`"HeadlessChrome"`.

### INVIS-46: `navigator.appVersion` should be the full UA minus "Mozilla/"
Real Chrome has `appVersion = "5.0 (Windows) AppleWebKit/...
Chrome/120...Safari/537.36"`. Anti-bot grids verify.

### INVIS-47: `navigator.platform` for new Apple Silicon
Real Chrome on M1/M2 Mac returns `"MacIntel"` (legacy
compat) or `"MacArm"` (newer). Our hardener should
match the persona's actual platform.

### INVIS-48: `navigator.oscpu` is Firefox-specific
Verified at `prototype_hardener.js:482` — only set
when `isFirefox`.

### INVIS-49: `navigator.buildID` is Firefox-specific
Real Firefox has `navigator.buildID`. Not handled.

### INVIS-50: `navigator.doNotTrack` should be `"unspecified"` for most personas
Real Chrome returns `null` or `"unspecified"`. Some
users set it to `"1"`. Our hardener doesn't set this.

---

## Category 4: Network-level Stealth

### INVIS-51: TCP/IP fingerprinting (p0f, JA3S)
**Likelihood**: HIGH (Cloudflare and Akamai do this server-side)
**Current state**: Out of scope for this file, but **CloakBrowser
must** use uTLS with the exact Chrome 120 HelloClientJA3.
**What's missing**: Verify that CloakBrowser's TLS fingerprint
matches Chrome 120 EXACTLY, not just close.

### INVIS-52: HTTP/2 SETTINGS frame order
**Likelihood**: MEDIUM
**Current state**: Not handled in this file.
**The problem**: Real Chrome sends SETTINGS frames in a
specific order. httpx or curl_cffi may send them in a
different order.
**What's missing**: Verify HTTP/2 fingerprint.

### INVIS-53: TCP initial window size
**Likelihood**: MEDIUM
**Current state**: Out of scope.
**What's missing**: Verify TCP window size matches Chrome 120
(Linux Chrome typically uses 65535 or 65536 initial window).

### INVIS-54: TLS session resumption timing
**Likelihood**: LOW
**Current state**: Not handled.
**What's missing**: A real browser re-uses TLS sessions
within ~30s. Our hardener should mimic this.

### INVIS-55: OCSP stapling
**Likelihood**: LOW
**Current state**: Firefox prefs at `hardened_orchestrator.py`
set `security.OCSP.enabled: 0`. Chrome doesn't support
OCSP stapling config.
**What's missing**: Verify Chrome's default behavior
(OCSP soft-fail).

### INVIS-56: IPv6 vs IPv4 only connections
**Likelihood**: MEDIUM
**Current state**: `--disable-quic` is set.
**What's missing**: Force IPv4 for the residential proxy
(if only IPv4 egress is available).

### INVIS-57: DNS-over-HTTPS (DoH) leak
**Likelihood**: MEDIUM
**Current state**: SOCKS5h forces remote DNS. Good.
**What's missing**: Verify that the SOCKS5h implementation
is correctly being used by all browser subsystems
(WebRTC, geolocation, etc.).

### INVIS-58: HTTP referrer for cross-origin requests
**Likelihood**: LOW
**Current state**: Firefox prefs set
`network.http.referer.XOriginPolicy: 1` and trimming.
**What's missing**: Verify Chrome's referrer policy
(default is `strict-origin-when-cross-origin`).

---

## Category 5: Behavioral Stealth (not in this file, but related)

### INVIS-59: Mouse trajectory has no hesitation micro-pauses
A real user pauses 10-50ms when changing direction. Our
spline in `humanized_movement.py` may not include these.

### INVIS-60: Keyboard timing is too regular
Real typing has variable inter-key delays. Our
`keyboard_emulator.py` may have too-narrow a distribution.

### INVIS-61: No human-like reading pauses in HCI
A real user reads for 5-30 seconds on a page before
acting. Our `hci_reading_model.py` may compress this.

### INVIS-62: Form fill is too fast
A real user takes 30s-2min to fill a registration form.
Our executors may fill in 5-10s.

### INVIS-63: No human-like backspace behavior
A real user makes typos and backspaces. Our keyboard
emulator may not include typos (or includes too many).

### INVIS-64: Scroll velocity is too uniform
A real user's scroll has variable velocity. Our
`humanized_movement.py` may produce too-uniform scroll.

### INVIS-65: Click timing is too consistent
Real users double-click accidentally, mis-click, etc.
Our single-click pattern is detectable.

### INVIS-66: Tab switching is missing
A real user switches tabs (Cmd+Tab) 5-20 times per
session. Our session never switches.

### INVIS-67: Idle periods too short
A real user leaves the browser idle for minutes at a
time. Our session has no idle periods.

### INVIS-68: Network timing is too regular
A real user's network requests have variable timing.
Our `dynamic_pacing.py` may produce too-uniform delays.

---

## Category 6: Account / Profile Stealth

### INVIS-69: No realistic cookie history before first task
A real account has 5-50 cookies from prior visits. Our
personas may start with 0 cookies.

### INVIS-70: No realistic `localStorage` history
A real user has 10-100 localStorage entries. Our personas
have 0.

### INVIS-71: No realistic `IndexedDB` history
A real user has IndexedDB data from prior sessions.

### INVIS-72: No realistic browser bookmarks
Real Chrome has 5-20 bookmarks. Our persona has none.

### INVIS-73: No realistic browser history
Real Chrome has 100-1000+ history entries. Our persona
has only the navigation history (1-3 entries).

### INVIS-74: No realistic saved passwords
Real Chrome has 0-30 saved passwords. Our persona has 0.

### INVIS-75: No realistic extensions installed
Real Chrome has 0-5 extensions (AdBlock, etc.). Our
persona has 0 (CloakBrowser blocks extensions).

### INVIS-76: No realistic autofill data
Real Chrome has 5-20 autofill entries (names, addresses,
phone). Our persona has 0.

### INVIS-77: No realistic "site settings"
Real Chrome has dozens of site-specific permissions
(camera denied for site X, location denied for site Y).
Our persona has 0.

### INVIS-78: No realistic "Do Not Track" / "Privacy" preferences
Real users have varied DNT settings.

### INVIS-79: No realistic "default search engine"
Real Chrome has Google as default. Our persona inherits
this, but the search history is empty.

### INVIS-80: No realistic startup pages
Real Chrome has 2-5 startup tabs. Our persona starts
with a single about:blank tab.

---

## Summary

| Category | Items | Risk |
|----------|-------|------|
| Category 1 (HIGH) | INVIS-1 to INVIS-10 | Likely to cause detection in 2026 |
| Category 2 (MEDIUM) | INVIS-11 to INVIS-20 | Subtle but real |
| Category 3 (LOW) | INVIS-21 to INVIS-50 | Worth tracking |
| Category 4 (Network) | INVIS-51 to INVIS-58 | Out of scope, but critical |
| Category 5 (Behavioral) | INVIS-59 to INVIS-68 | Behavioral file scope |
| Category 6 (Profile) | INVIS-69 to INVIS-80 | Profile file scope |

**Total**: 80 items, of which:
- 10 are HIGH priority (likely to cause detection)
- 10 are MEDIUM priority (subtle but real)
- 30 are LOW priority (worth tracking)
- 30 are out of immediate scope (network, behavior, profile files)

**Of the 80 items, only ~5-10% are addressed by the current
implementation**. The current stack handles the basics
(canvas, audio, WebGL, user agent, languages) but leaves
significant gaps in:
- WebRTC internal IP leak
- network jitter
- DST / timezone drift
- canvas/audio session entropy
- WebGL SwiftShader detection
- plugin/mimeType accuracy
- notification/permission state machines
- 70+ minor leaks

**Recommendation**: Prioritize INVIS-1 through INVIS-10 in the
next round. The behavioral and profile categories are also
critical for long-term session credibility, but require
multi-file coordination (not just `sticky_proxy_resolver.py`).
