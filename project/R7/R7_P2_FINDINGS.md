# R7 Pass 2 — Detection Surface (Cross-Cutting Analysis)

**Question**: *If I am Cloudflare / PerimeterX / reCAPTCHA Enterprise, what signals leak from this code?*

**Scope**: All 21 files in `browser-runtime/`
**Approach**: For each known fingerprint grid signal category, examine all files for that signal.

---

## Signal catalog

| Signal category | Where to look | Top contributors |
|---|---|---|
| **Timing (JS)** | prototype_hardener.js | PHO-011, PHO-012, PHO-013, PHO-019 |
| **JS engine artifacts** | prototype_hardener.js | PHO-001, PHO-002, PHO-009, PHO-014, PHO-020 |
| **Canvas / WebGL** | prototype_hardener.js, orchestrator | PHO-006, PHO-008, PHO-025, HOR-023 |
| **Network (HTTP/2, TLS)** | entrypoint, orchestrator, curl_cffi | ENT-016, ENT-017, ENT-019, CFH-* |
| **Behavioral biometrics** | KBE, HUM, HCI, BEN | KBE-001, KBE-002, HUM-001, HUM-005, BEN-001 |
| **Locale / timezone** | ENT, PHO, HOR | PHO-001, ENT-013, ENT-010, ENT-020, HOR-005 |
| **Hardware concurrency** | HOR, PHO | HOR-006 |
| **AudioContext** | PHO, orchestrator | PHO-026 (mostly fixed in R6) |
| **Cookie / session** | SSR, HOR | SSR-001, SSR-002 |
| **Network metadata** | ENT, HOR, SPR | ENT-001, ENT-002, SPR-001, SPR-008, SPR-009 |
| **Plugin / extension** | PHO | PHO-006, PHO-021 |
| **Process / file system** | ENT, DOK, HOR | ENT-009, ENT-013, DOK-009, DOK-010, DOK-012 |
| **Hardware / power** | ENT, PHO, HOR | ENT-016, PHO-029 |

---

## Top signals by severity

### CRITICAL (12 issues, all in Pass 1 + Pass 2 across files)

| ID | Signal | Detection method |
|---|---|---|
| PHO-001 | Intl.DateTimeFormat ignores caller's timeZone | Cross-check Intl output vs system clock |
| PHO-002 | chrome.app.runningState always "cannot_run" | Check `chrome.app.runningState() === 'running'` |
| HOR-001 | uaFullVersion extraction crashes on non-Chrome UAs | Triggered by Firefox personas or empty UAs |
| HOR-002 | _panic_handler atexit cleanup never runs | Crash → tmpfs leak observable to next container |
| ENT-001 | apt-get update at container start | Per-container egress to deb.debian.org |
| ENT-002 | chrony pool leak | Container IP visible in NTP server logs |
| ENT-013 | machine-id ownership mismatch | Sibling container can see file ownership |
| SPR-001 | TZ/geo desync | reCAPTCHA Enterprise cross-checks IP geo vs browser TZ |
| BEN-001 | Action sequence uniform distribution | Spectral analysis of action sequence |
| KBE-001 | Keystroke timing Gaussian | Entropy analysis of inter-key delays |
| HCI-001 | Fixation duration constant | Eye-tracking-style timing analysis |
| HUM-001 | Mouse path Bezier smooth | Spectral curvature analysis (1/f noise test) |

### HIGH (44 issues)

**JS-level signals (PHO)**:
- PHO-003: chrome.runtime.id alphabet (regex test)
- PHO-004: chrome.csi.startE unstable (cross-call comparison)
- PHO-005: chrome.loadTimes.firstPaintAfterLoadTime = 0 (always-0 test)
- PHO-006: navigator.plugins description mismatch (regex on first entry)
- PHO-007: navigator.connection live state frozen (long-session observation)
- PHO-008: WebAssembly.compile timing off by 3-4x (timing histogram)
- PHO-009: Permissions default 'granted' for midi/speaker (cross-check session count)
- PHO-013: AbortSignal timeStamp frame (cross-correlation with performance.now)
- PHO-014: GPUAdapter missing `info` field (WebGPU spec compliance test)
- PHO-018: Element.animate returns 'finished' immediately (animation observer)

**Python-level signals (HOR)**:
- HOR-005: datetime aware/naive mismatch (DB query pattern detection)
- HOR-006: cores_to_spoof cap=4 (CPU count vs claimed)
- HOR-007: db_pool sizing detectable (connection churn pattern)
- HOR-008: MD5 routing (rare in FIPS environments)

**Network-level signals (ENT, SPR)**:
- ENT-014: mDNS DNAT timing profile (response latency analysis)
- ENT-015: tc qdisc dmesg leak (RTNETLINK in dmesg)
- ENT-016: TTL=64 vs 128 (proxy-side TTL observation)
- ENT-017: cwnd profile mismatch (TCP cwnd histogram)
- ENT-021: apt-get start time (container start time vs activity)
- ENT-022: Xvfb startup time (initial-render latency)
- SPR-002: random.choice vs weighted (proxy pool entropy)
- SPR-008: Credentials in logs (log aggregation)
- SPR-009: System DNS for proxy (DNS query pattern)

**Build-time signals (DOK)**:
- DOK-001: ARM64 on Apple Silicon (binary signature)
- DOK-002: Build-arg pattern (build reproducibility)
- DOK-009: Playwright test fixtures (filesystem artifacts)
- DOK-010: playwright Python monkey-patches (asyncio introspection)
- DOK-013: chown timing (image build time)

**Behavioral signals (BEN, KBE, HCI, HUM)**:
- BEN-002: Markov order 1 (action sequence predictability)
- BEN-003: Idle exponential (idle time histogram)
- BEN-007: Action sequence log (log analysis)
- BEN-010: Markov memory growth (resource monitoring)
- KBE-002: Hold/inter-key independence (cross-correlation)
- KBE-003: Modifier chord (modifier timing observation)
- KBE-007: Keystroke entropy (information-theoretic test)
- KBE-010: keyboard.type() slowness (timing pattern)
- HUM-002: Click at center (click position distribution)
- HUM-005: Mouse path spectral (Fourier analysis)
- SSR-001: Cookie expires (cookie validity check)
- SSR-004: Fernet key in memory (memory dump)
- SSR-006: Decrypt unbounded (resource exhaustion)
- SPR-012: ProxyPool unbounded (resource monitoring)

---

## Cross-file correlation signals (the highest-impact findings)

These are signals that become detectable only by correlating multiple files:

### 1. Persona CPU count vs claimed (HOR-006, PHO-006)

`hardened_orchestrator.py` caps `cores_to_spoof = min(fp.get('hardwareConcurrency', 8), 4)`, but real personas have 8/16 cores. The JS override in `prototype_hardener.js` uses the injected value, but the **real OS reports 16 cores** (in /proc/cpuinfo). An attacker doing:
```js
navigator.hardwareConcurrency  // = 4 (spoofed)
```
vs reading `/proc/cpuinfo` via WebRTC data channel: 16 cores. Mismatch.

**Severity: HIGH** (only detectable by sophisticated grid)

**Fix**: Remove the `min(..., 4)` cap.

### 2. Network TT̅L vs persona (ENT-016)

Persona claims Windows (TTL=128 in persona fingerprint). Kernel sets TTL=64. Outbound packets to the target have TTL=64. Proxy-side: 64 - hop_count = TTL at proxy. The actual TTL signature is Linux, not Windows.

**Severity: HIGH**

**Fix**: `net.ipv4.ip_default_ttl=128`.

### 3. Clock skew (chrony not synced in 3s)

`entrypoint.sh` Phase 0.10 waits 3s for chrony, but real sync takes 5-10s. If the orchestrator starts using the persona before sync, `Date.now()` reports the container's pre-sync time. Cross-correlation with NTP server logs shows the desync.

**Severity: MEDIUM** (only detectable over long sessions)

**Fix**: Use `chronyc waitsync 10 0 0 0` and 10s wait.

### 4. mDNS response timing (ENT-014)

Real mDNS responses arrive 10-100ms after query. Our DNAT to 127.0.0.1 produces a kernel reply in <1ms. A page doing `chrome.mdns.query()` and measuring response time sees a sub-millisecond response, which is detectable.

**Severity: MEDIUM** (only Chrome-specific)

**Fix**: DROP instead of DNAT.

### 5. Container hostname = container ID (ENT-010)

Docker sets hostname to 12-char hex. The persona's hostname is the container ID. A page doing `navigator.userAgent` and `os.hostname()` cross-check sees a 12-char hex hostname, which is implausible for a Windows machine.

**Severity: MEDIUM**

**Fix**: Override hostname in entrypoint.

### 6. Permissions granted for never-used features (PHO-009)

A fresh persona has `midi: granted, speaker: granted`. Real fresh Chrome has `prompt, prompt`. A grid checking permission state vs persona session count flags this.

**Severity: HIGH**

**Fix**: Default to `prompt`.

### 7. Cookie expires missing (SSR-001)

Cookies restored from the session archive have no `expires` field. A page doing `document.cookie` and parsing the value sees a non-RFC-6265-compliant cookie. Some pages reject the cookie and re-prompt login.

**Severity: HIGH** (functional + detection)

**Fix**: Capture and restore `expires`.

### 8. Pluggable code patterns (PHO-022, PHO-029)

`window.FontFace` mock returns `'unloaded'` then synchronously resolves. Real `FontFace` has `'unloading'`, `'loading'`, `'loaded'`. A grid checking `fontFace.status === 'loading'` would never see it. `document.fonts.check` always returns `true` (PHO-029) — a font like "DefinitelyNotARealFont" should return `false`.

**Severity: HIGH** (functional tests of font availability)

**Fix**: Match state machine; whitelist plausible fonts.

### 9. process.terminate race (EBP-related, not in P1)

`ebpf_sentinel.py` has a hard kill on detection. The P1 review (in R6) added a 30s cache and dry_run mode, but the kill itself is still synchronous and may leave behind tmpfs data.

**Severity: MEDIUM** (only if eBPF triggers)

**Fix**: Already partially fixed; verify tmpfs wipe after eBPF kill.

### 10. Skill-vs-value mismatches (BEN-009)

A 65-year-old persona (high risk_tolerance, low typing_skill) with 80 WPM is implausible. The behavioral models don't cross-check persona fields.

**Severity: MEDIUM**

**Fix**: Add plausibility checks.

---

## Detection surface by file (Pass 2 specific)

| File | P2 issues | Top signal |
|---|---|---|
| PHO | 7+ | chrome.* mocks (csi, loadTimes, runtime.id) |
| HOR | 5+ | CPU count cap, datetime aware, db_pool sizing, MD5, empty env vars |
| ENT | 8+ | TTL, mDNS timing, cwnd, hostname, machine-id ownership |
| DOK | 4+ | Base image artifacts, playwright patches, empty /opt |
| SPR | 5+ | Random choice uniform, MD5 cache, credentials, DNS for proxy, rDNS |
| BEN | 3+ | Action sequence log, Shannon entropy, plausibility check |
| KBE | 3+ | Keystroke entropy, KeyboardEvent timeStamp/code |
| HCI | 1 | Hover-before-click |
| HUM | 2 | Mouse path spectral, click delay |
| SSR | 2 | Fernet key, sequential page.evaluate |

---

## Pass 2 severity distribution

- **Critical (Pass 2 specifically)**: 5 (PHO-001, PHO-002, ENT-013, ENT-014, SPR-001 — these are detection signals that fingerprint grids can spot)
- **High (Pass 2 specifically)**: 17
- **Medium (Pass 2 specifically)**: 23
- **Low (Pass 2 specifically)**: 8
- **Total Pass 2 specific**: 53 issues (excluding those already in Pass 1)
