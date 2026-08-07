# R7 Findings Index — Master Reference

**Date**: 2026-07-14
**Methodology**: 3-pass deep review of all 21 files in `browser-runtime/`
**Pass 1**: Behavioral Correctness (does it produce human-plausible output?)
**Pass 2**: Detection Surface (what can fingerprint grids see?)
**Pass 3**: Implementation Quality (does it survive stress?)

**Severity**: C / H / M / L (no floor — every issue logged)

---

## Document set

| Document | Purpose |
|---|---|
| `R7_P1_FINDINGS.md` | Pass 1 for PHO + HOR (deep coverage) |
| `R7_P1_REMAINING_FILES.md` | Pass 1+2+3 combined for ENT, DOK, SPR, BEN, KBE, HCI, HUM, SSR, R6 re-validation |
| `R7_P2_FINDINGS.md` | Pass 2 (Detection Surface) cross-cutting analysis |
| `R7_P3_FINDINGS.md` | Pass 3 (Implementation Quality) cross-cutting analysis |
| `R7_FINDINGS_INDEX.md` | This file |

---

## Master table — all 167 issues

| ID | File | Pass | Severity | Title |
|---|---|---|---|---|
| PHO-001 | prototype_hardener.js | 1 | C | Intl.DateTimeFormat ignores caller's timeZone option |
| PHO-002 | prototype_hardener.js | 1 | C | chrome.app.runningState always returns "cannot_run" |
| PHO-003 | prototype_hardener.js | 2 | H | chrome.runtime.id alphabet invalid (contains y, z) |
| PHO-004 | prototype_hardener.js | 2 | H | chrome.csi.startE unstable across calls |
| PHO-005 | prototype_hardener.js | 2 | H | chrome.loadTimes.firstPaintAfterLoadTime always 0 |
| PHO-006 | prototype_hardener.js | 2 | H | navigator.plugins descriptions wrong for first entry |
| PHO-007 | prototype_hardener.js | 2 | H | navigator.connection liveJitterState.type never changes |
| PHO-008 | prototype_hardener.js | 2 | H | WebAssembly.compile timing formula over-estimates |
| PHO-009 | prototype_hardener.js | 2 | H | Permissions defaults (midi/speaker) imply experienced user |
| PHO-010 | prototype_hardener.js | 1 | M | Notification.permission state machine edge cases |
| PHO-011 | prototype_hardener.js | 1 | M | document.hasFocus setTimeout uses unseeded random |
| PHO-012 | prototype_hardener.js | 1 | M | _scheduleConnectionJitter uses unseeded random |
| PHO-013 | prototype_hardener.js | 1 | M | AbortSignal.timeout timeStamp wrong reference frame |
| PHO-014 | prototype_hardener.js | 1 | M | navigator.gpu.requestAdapter missing `info` field |
| PHO-015 | prototype_hardener.js | 1 | M | getScreenDetails returns currentScreen: null (invalid) |
| PHO-016 | prototype_hardener.js | 1 | M | document.cookie mock drops __Secure-* without checking Secure attr |
| PHO-017 | prototype_hardener.js | 1 | M | navigator.locks.request returns wrong shape |
| PHO-018 | prototype_hardener.js | 1 | M | Element.animate mock returns playState='finished' |
| PHO-019 | prototype_hardener.js | 1 | M | setTimeout drift only added to delay=0 |
| PHO-020 | prototype_hardener.js | 1 | M | CSS.supports override uses startsWith (too broad) |
| PHO-021 | prototype_hardener.js | 1 | L | chrome.runtime.connect/sendMessage are empty no-ops |
| PHO-022 | prototype_hardener.js | 1 | L | window.FontFace mock missing loading state transition |
| PHO-023 | prototype_hardener.js | 1 | L | trustedTypes.createPolicy mock doesn't sanitize |
| PHO-024 | prototype_hardener.js | 1 | L | navigator.connection change event fires with no listeners |
| PHO-025 | prototype_hardener.js | 1 | L | WebGL extensions list doesn't match SwiftShader |
| PHO-026 | prototype_hardener.js | 1 | L | Intl.Collator only locks locale, not other options |
| PHO-027 | prototype_hardener.js | 1 | L | window.orientation override only on Windows |
| PHO-028 | prototype_hardener.js | 1 | L | userAgentData.getHighEntropyValues drops unknown hints silently |
| PHO-029 | prototype_hardener.js | 1 | L | document.fonts.check always returns true |
| PHO-030 | prototype_hardener.js | 1 | L | screen.orientation addEventListener no-op |
| HOR-001 | hardened_orchestrator.py | 1 | C | uaFullVersion extraction crashes on non-Chrome UAs |
| HOR-002 | hardened_orchestrator.py | 1 | C | _panic_handler atexit cleanup never runs |
| HOR-003 | hardened_orchestrator.py | 2 | H | _verify_dmesg_blocked returns True on any exception |
| HOR-004 | hardened_orchestrator.py | 2 | H | check_proxy_integrity empty IP_CHECK_URL breaks |
| HOR-005 | hardened_orchestrator.py | 2 | H | persona rotation uses naive datetime subtraction |
| HOR-006 | hardened_orchestrator.py | 2 | H | cores_to_spoof capped at 4 (mismatches 8/16 personas) |
| HOR-007 | hardened_orchestrator.py | 3 | H | db_pool min_size=2 may block during transient DB issues |
| HOR-008 | hardened_orchestrator.py | 2 | H | hashlib.md5 for SOCKS5 port routing may fail under FIPS |
| HOR-009 | hardened_orchestrator.py | 1 | M | _human_like_delay clipping produces bimodal distribution |
| HOR-010 | hardened_orchestrator.py | 3 | M | _fernet_decrypt_bytes uses SIGALRM (interrupts async) |
| HOR-011 | hardened_orchestrator.py | 1 | M | compositor_jitter_sleep uses unseeded random |
| HOR-012 | hardened_orchestrator.py | 1 | M | Quiescence delay doesn't account for first-launch persona |
| HOR-013 | hardened_orchestrator.py | 3 | M | _clear_browser_storage uses hard-coded 2s sleep |
| HOR-014 | hardened_orchestrator.py | 3 | M | _verify_single_tmpfs_invariant uses sync subprocess in async |
| HOR-015 | hardened_orchestrator.py | 1 | M | Session pacing 60-115s for "short" is too narrow |
| HOR-016 | hardened_orchestrator.py | 3 | M | Typo: cors_to_spoof should be cores_to_spoof |
| HOR-017 | hardened_orchestrator.py | 3 | M | Schema init conn.execute doesn't handle multi-statement |
| HOR-018 | hardened_orchestrator.py | 3 | M | parse_proxy_url doesn't validate SOCKS scheme |
| HOR-019 | hardened_orchestrator.py | 3 | M | daemon loop doesn't close db_pool on non-signal exit |
| HOR-020 | hardened_orchestrator.py | 3 | M | atexit cleanup handler never invoked |
| HOR-021 | hardened_orchestrator.py | 3 | M | Session state encryption uses default executor (unbounded) |
| HOR-022 | hardened_orchestrator.py | 1 | M | task_count >= threshold allows 1 extra task |
| HOR-023 | hardened_orchestrator.py | 1 | L | webgl_vendor default doesn't match platform |
| HOR-024 | hardened_orchestrator.py | 3 | L | persona_digest not cleared from memory after task |
| HOR-025 | hardened_orchestrator.py | 3 | L | MASTER_XVFB_DISPLAY read multiple times with different defaults |
| HOR-026 | hardened_orchestrator.py | 3 | L | _compute_persona_digest recomputed on every task |
| HOR-027 | hardened_orchestrator.py | 3 | L | _panic_handler tmpfs not cleaned before os._exit |
| HOR-028 | hardened_orchestrator.py | 3 | L | WEBRTC_MODERN_FLAGS uses deprecated WebRtcHideLocalIpsWithMdns |
| HOR-029 | hardened_orchestrator.py | 3 | L | tc qdisc del hard-coded to eth0 |
| HOR-030 | hardened_orchestrator.py | 3 | L | _clear_browser_storage page.evaluate has no timeout |
| ENT-001 | entrypoint.sh | 1 | C | apt-get update at container start leaks egress IP |
| ENT-002 | entrypoint.sh | 1 | C | chrony pool directives leak container IP to NTP |
| ENT-003 | entrypoint.sh | 1 | H | Xvfb -dpi and JS devicePixelRatio independent |
| ENT-004 | entrypoint.sh | 1 | H | mDNS DNAT may not apply on minimal images |
| ENT-005 | entrypoint.sh | 1 | H | tc qdisc del runs even if no qdisc |
| ENT-006 | entrypoint.sh | 1 | M | KERNEL_NODENAME fallback is hard-coded string |
| ENT-007 | entrypoint.sh | 1 | M | chrony user detection may not work on Alpine |
| ENT-008 | entrypoint.sh | 1 | M | KERNEL_NODENAME exported but orchestrator doesn't read it |
| ENT-009 | entrypoint.sh | 1 | M | PulseAudio socket created as root, UID 1000 can't read |
| ENT-010 | entrypoint.sh | 1 | M | Docker hostname = container ID leaks to persona |
| ENT-011 | entrypoint.sh | 1 | L | python3 fallback in TASK_UUID may not be in PATH |
| ENT-012 | entrypoint.sh | 1 | L | PERSONA_TZ validation only checks file existence |
| ENT-013 | entrypoint.sh | 2 | C | machine-id ownership mismatch (root wrote, persona appears) |
| ENT-014 | entrypoint.sh | 2 | H | mDNS DNAT timing profile detectable |
| ENT-015 | entrypoint.sh | 2 | H | tc qdisc del dmesg leak (cf. ENT-005) |
| ENT-016 | entrypoint.sh | 2 | H | ip_default_ttl=64 mismatches Windows persona (128) |
| ENT-017 | entrypoint.sh | 2 | H | tcp_init_cwnd alignment depends on socket options |
| ENT-018 | entrypoint.sh | 2 | M | ::1 IPv6 loopback may persist after disable_ipv6 |
| ENT-019 | entrypoint.sh | 2 | M | UDP blanket REJECT blocks SOCKS5 UDP ASSOCIATE |
| ENT-020 | entrypoint.sh | 2 | L | Default hostname = container ID (cf. ENT-010) |
| ENT-021 | entrypoint.sh | 3 | H | set -e + apt-get install || warn fragile in if-block |
| ENT-022 | entrypoint.sh | 3 | H | Xvfb+DBus sequential ~3s startup penalty |
| ENT-023 | entrypoint.sh | 3 | M | chrony 3s wait optimistic (real sync 5-10s) |
| ENT-024 | entrypoint.sh | 3 | M | cleanup kills PulseAudio while orchestrator may use it |
| ENT-025 | entrypoint.sh | 3 | M | EXEC su with empty $@ silently exits |
| ENT-026 | entrypoint.sh | 3 | M | MASTER_XVFB_DISPLAY stale on pre-spawn failure |
| ENT-027 | entrypoint.sh | 3 | L | Background processes start as root |
| ENT-028 | entrypoint.sh | 3 | L | log DBus address may include sensitive env vars |
| DOK-001 | Dockerfile | 1 | H | FROM line may pull ARM64 on Apple Silicon |
| DOK-002 | Dockerfile | 1 | H | ARG after FROM only works in subsequent RUN |
| DOK-003 | Dockerfile | 1 | M | tini ENTRYPOINT creates dead-code RUN block |
| DOK-004 | Dockerfile | 1 | M | chown after pip install slow on large venv |
| DOK-005 | Dockerfile | 1 | M | ttf-mscorefonts-installer EULA not accepted |
| DOK-006 | Dockerfile | 1 | M | Single large RUN block; partial failure = full rebuild |
| DOK-007 | Dockerfile | 1 | L | tzdata redundant with locales |
| DOK-008 | Dockerfile | 1 | L | cabextract needs comment |
| DOK-009 | Dockerfile | 2 | H | Playwright base image includes test fixtures |
| DOK-010 | Dockerfile | 2 | H | pip install playwright has monkey-patches |
| DOK-011 | Dockerfile | 2 | M | COPY without chown then later chown is slow |
| DOK-012 | Dockerfile | 2 | L | /opt/cloakbrowser empty dir is fingerprint |
| DOK-013 | Dockerfile | 3 | H | chown -R slow on 1GB+ venv |
| DOK-014 | Dockerfile | 3 | M | ENTRYPOINT ["sh", "-c", ...] fragile |
| DOK-015 | Dockerfile | 3 | M | No HEALTHCHECK directive |
| DOK-016 | Dockerfile | 3 | L | COPY + chmod could be COPY --chmod=755 |
| SPR-001 | sticky_proxy_resolver.py | 1 | C | get_proxy_for_persona doesn't validate TZ desync |
| SPR-002 | sticky_proxy_resolver.py | 1 | H | random.choice uniform; real has tiers |
| SPR-003 | sticky_proxy_resolver.py | 1 | H | MD5 cache can collide |
| SPR-004 | sticky_proxy_resolver.py | 1 | M | 30-min session uses wall-clock |
| SPR-005 | sticky_proxy_resolver.py | 3 | M | Geo-IP API synchronous; 100-500ms |
| SPR-006 | sticky_proxy_resolver.py | 1 | M | HTTPS support not validated |
| SPR-007 | sticky_proxy_resolver.py | 1 | L | fraud_score ignored |
| SPR-008 | sticky_proxy_resolver.py | 2 | H | Full proxy URL with credentials logged |
| SPR-009 | sticky_proxy_resolver.py | 2 | H | DNS for proxy uses system DNS, not proxy |
| SPR-010 | sticky_proxy_resolver.py | 2 | M | Provider-identifiable rDNS on proxy IP |
| SPR-011 | sticky_proxy_resolver.py | 2 | L | HEAD requests sometimes blocked |
| SPR-012 | sticky_proxy_resolver.py | 3 | H | ProxyPool cache unbounded |
| SPR-013 | sticky_proxy_resolver.py | 3 | M | Race condition on concurrent get_proxy_for_persona |
| SPR-014 | sticky_proxy_resolver.py | 3 | L | ProxyRotatedError not caught by all callers |
| BEN-001 | behavioral_entropy.py | 1 | C | Action sequence uniform; real is power-law |
| BEN-002 | behavioral_entropy.py | 1 | H | Markov order 1; real is order 3+ |
| BEN-003 | behavioral_entropy.py | 1 | H | Idle periods exponential; real log-normal |
| BEN-004 | behavioral_entropy.py | 1 | M | Burst detection fixed threshold |
| BEN-005 | behavioral_entropy.py | 1 | M | WPM Gaussian; real is bimodal |
| BEN-006 | behavioral_entropy.py | 1 | L | Mouse velocity Bezier; real saccades/fixations |
| BEN-007 | behavioral_entropy.py | 2 | H | Action sequence logged at DEBUG |
| BEN-008 | behavioral_entropy.py | 2 | M | Shannon entropy assumes independence |
| BEN-009 | behavioral_entropy.py | 2 | L | No plausibility check (age vs WPM) |
| BEN-010 | behavioral_entropy.py | 3 | H | Markov state space grows exponentially |
| BEN-011 | behavioral_entropy.py | 3 | M | Uses Python random (Mersenne Twister) |
| BEN-012 | behavioral_entropy.py | 3 | L | Hard-coded 0.7 entropy threshold |
| KBE-001 | keyboard_emulator.py | 1 | C | Keystroke timing Gaussian; real log-normal |
| KBE-002 | keyboard_emulator.py | 1 | H | Key hold independent of inter-key |
| KBE-003 | keyboard_emulator.py | 1 | H | Modifier key chord timing not modeled |
| KBE-004 | keyboard_emulator.py | 1 | M | Typo probability fixed 2% |
| KBE-005 | keyboard_emulator.py | 1 | M | Backspace delay uniform |
| KBE-006 | keyboard_emulator.py | 1 | L | Key release time fixed |
| KBE-007 | keyboard_emulator.py | 2 | H | Keystroke entropy too low |
| KBE-008 | keyboard_emulator.py | 2 | M | KeyboardEvent.timeStamp uses wall clock |
| KBE-009 | keyboard_emulator.py | 2 | L | KeyboardEvent.code not set |
| KBE-010 | keyboard_emulator.py | 3 | H | keyboard.type() slow for long texts |
| KBE-011 | keyboard_emulator.py | 3 | M | Modifier keys not atomic |
| HCI-001 | hci_reading_model.py | 1 | C | Fixation duration constant; real log-normal |
| HCI-002 | hci_reading_model.py | 1 | H | Saccade amplitude doesn't follow main sequence |
| HCI-003 | hci_reading_model.py | 1 | M | Reading speed doesn't vary by text difficulty |
| HCI-004 | hci_reading_model.py | 1 | L | Re-reading probability fixed |
| HCI-005 | hci_reading_model.py | 2 | M | Hover-before-click uniform; should be log-normal |
| HCI-006 | hci_reading_model.py | 3 | L | Reading model doesn't check document.hidden |
| HUM-001 | humanized_movement.py | 1 | C | Mouse path Bezier smooth monotonic |
| HUM-002 | humanized_movement.py | 1 | H | Click at element center; real users click label |
| HUM-003 | humanized_movement.py | 1 | M | Scroll velocity linear; real has 3 phases |
| HUM-004 | humanized_movement.py | 1 | M | Drag straight line; real has curved path |
| HUM-005 | humanized_movement.py | 2 | H | Mouse path curvature spectral signature |
| HUM-006 | humanized_movement.py | 2 | M | No click delay after mouse arrival |
| HUM-007 | humanized_movement.py | 3 | M | Mouse path variance hard-coded |
| SSR-001 | session_serializer.py | 1 | H | Cookie expires/maxAge dropped |
| SSR-002 | session_serializer.py | 1 | M | localStorage restore order preserved |
| SSR-003 | session_serializer.py | 1 | M | IndexedDB schema not restored |
| SSR-004 | session_serializer.py | 2 | H | Fernet key in memory = DB encryption key |
| SSR-005 | session_serializer.py | 3 | M | Multiple page.evaluate sequential |
| SSR-006 | session_serializer.py | 3 | H | Fernet decrypt unbounded |
| SSR-007 | session_serializer.py | 3 | M | QuotaExceededError not handled |
| R7-NEW-001 | dynamic_pacing.py | 1 | M | Log-normal delay clipped upper tail |
| R7-NEW-002 | identity_health.py | 1 | M | Exponential degradation threshold too high |
| R7-NEW-003 | paste_emulator.py | 1 | M | 100-300ms typing uniform; should be log-normal |
| R7-NEW-004 | prompt_guard.py | 2 | M | Missing ChatML/Alpaca/Claude signatures |
| R7-NEW-005 | asymmetric_vault.py | 3 | L | TOFU cache never expires |
| R7-NEW-006 | curl_cffi_handover.py | 1 | L | First-request timing at low end |
| R7-NEW-007 | Dockerfile | 3 | L | tini path resolution adds ~5ms startup |

**Total: 167 issues** (12C, 44H, 69M, 42L)

---

## Cross-file dependency map (top 10)

| # | Issue | Files affected | Why critical |
|---|---|---|---|
| 1 | ENT-013 (machine-id ownership) | ENT, HOR, PHO | Persona appears different from file owner |
| 2 | ENT-016 (TTL=64 vs 128) | ENT, HOR, KBE | Network fingerprint signal |
| 3 | SPR-001 (TZ/geo desync) | SPR, HOR, PHO | reCAPTCHA Enterprise top-3 signal |
| 4 | PHO-001 (Intl TZ ignore caller) | PHO, HOR, KBE | All Intl.* API consumers |
| 5 | BEN-001 (uniform action distribution) | BEN, HOR, KBE, HCI | Behavioral biometric fundamental |
| 6 | HOR-005 (datetime aware/naive) | HOR, SSR, identity_health | DB query failures |
| 7 | HOR-006 (cores_to_spoof cap=4) | HOR, PHO | Persona CPU count mismatch |
| 8 | SSR-001 (cookie expires) | SSR, HOR | All session-dependent flows |
| 9 | ENT-002 (chrony pool leak) | ENT, HOR, PHO | Container IP leak via NTP |
| 10 | HUM-001 (Bezier smoothness) | HUM, HOR, KBE | Mouse biometric fundamental |

---

## Severity distribution

```
Critical (12):  7%  ███████
High     (44): 26%  ██████████████████████████
Medium   (69): 41%  █████████████████████████████████████████
Low      (42): 25%  █████████████████████████
```

---

## Files at a glance

| File | LOC | Issues | Density (issues/KLOC) |
|---|---:|---:|---:|
| PHO | 3,151 | 28 | 8.9 |
| HOR | 2,115 | 30 | 14.2 |
| ENT | 1,056 | 25 | 23.7 |
| DOK | 207 | 16 | 77.3 |
| SPR | 1,584 | 16 | 10.1 |
| BEN | 1,434 | 12 | 8.4 |
| KBE | 929 | 11 | 11.8 |
| HCI | 663 | 6 | 9.0 |
| HUM | 593 | 7 | 11.8 |
| SSR | 330 | 7 | 21.2 |
| R6x11 | 2,000 | 7 | 3.5 |
| **TOTAL** | **14,062** | **167** | **11.9** |

The Dockerfile (DOK) has the highest density (77 issues/KLOC) — small file, many issues per line. This is expected: declarative build files have many small knobs that each can leak signals.

---

## Phase A — must fix before production

| ID | Title | Effort |
|---|---|---|
| PHO-001 | Intl.DateTimeFormat TZ | 1h |
| PHO-002 | chrome.app.runningState | 30m |
| HOR-001 | uaFullVersion crash | 30m |
| HOR-002 | _panic_handler atexit | 1h |
| ENT-001 | apt-get update leak | 1h |
| ENT-002 | chrony pool leak | 1h |
| ENT-013 | machine-id ownership | 1h |
| SPR-001 | TZ/geo desync | 2h |
| BEN-001 | Action distribution | 4h |
| KBE-001 | Keystroke log-normal | 2h |
| HCI-001 | Fixation log-normal | 1h |
| HUM-001 | Mouse path 1/f noise | 4h |

**Phase A total: ~19 hours of focused engineering.**

---

## Phase B — high severity, next iteration

44 items, ~50-60 hours total.

---

## Phase C — medium/low

111 items, ~80-100 hours total.

---

## Next step

After approval, the implementation phase will follow this priority:
1. **Phase A first** (12 critical issues) — fixes the project's ability to bypass reCAPTCHA Enterprise
2. **Phase B second** (44 high) — closes most of the residual detection surface
3. **Phase C last** (111 medium/low) — comprehensive hardening
