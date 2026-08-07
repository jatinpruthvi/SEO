# R7 Pass 1 — Behavioral Correctness (ENT, DOK, SPR, BEN, KBE, HCI, HUM, SSR, R6-revalidation)

This document continues the R7 Pass 1 review for files 4-10 (ENT, DOK, SPR, BEN, KBE, HCI, HUM, SSR) plus the 11 R6 files re-validation. Pass 1 is the dominant lens for these files, with Pass 2 (detection surface) and Pass 3 (implementation quality) integrated where appropriate.

---

## ENT — `entrypoint.sh` (1,056 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### ENT-001 [CRITICAL] `apt-get update && install` runs at container start, leaking per-container egress to deb.debian.org

**Where**: Phase 0.1

**Issue**: The Dockerfile deletes `/var/lib/apt/lists/*` at build time. On every container start, the entrypoint re-runs `apt-get update && apt-get install` for any missing optional bins. This **per-container egress to deb.debian.org** is a fingerprint signal: real Chrome on real hardware doesn't hit Debian repos at startup. The egress IP matches a data center, not a residential proxy.

**Fix**: Move all apt installs to Dockerfile only; if a binary is missing at startup, fail closed with `die`.

---

#### ENT-002 [CRITICAL] chronyd with pool directives leaks container IP to NTP servers (which log source IPs)

**Where**: Phase 0.10

**Issue**: `chronyd` started with `/etc/chrony/chrony.conf` from the Dockerfile. If the config has `pool pool.ntp.org` (or any NTP pool), the container's IP is sent in NTP requests. NTP servers log source IPs.

**Fix**: Use `offline` mode (`local stratum 10`) with no pool directives, or NTS to a private NTP server. Update the Dockerfile's `chrony_hardened.conf`.

---

#### ENT-003 [HIGH] Xvfb `-dpi` and JS-set `devicePixelRatio` are independent, allowing cross-side inconsistency detection

**Where**: Phase 2.0 + `prototype_hardener.js`

**Issue**: Xvfb gets `-dpi 96` (FHD default), but the JS sets `devicePixelRatio` based on screen width. For a QHD persona (2560x1440), JS sets DPR=1.5 but Xvfb is 96 DPI. The browser internally may cross-check.

**Fix**: Pass PERSONA_DPI explicitly to Xvfb, or remove the JS DPR override and rely on Xvfb's value.

---

#### ENT-004 [HIGH] mDNS sinkhole DNAT may not apply on minimal images (nat table missing), leaking mDNS via REJECT ICMP

**Where**: Phase 8.0

**Issue**: If `iptables -t nat -L OUTPUT` fails (no nat table), the DNAT block is skipped. The OUTPUT chain's REJECT rule sends an ICMP-port-unreachable with source IP = container's eth0 IP, leaking the container IP to the multicast sender (possibly a fingerprinting grid on the same LAN).

**Fix**: Add an unconditional `iptables -A OUTPUT -p udp --dport 5353 -j DROP` before the REJECT.

---

#### ENT-005 [HIGH] `tc qdisc del dev eth0 root` runs even if no qdisc exists; produces RTNETLINK error in dmesg

**Where**: Phase 13.0

**Issue**: Unconditional `tc qdisc del`. If no qdisc exists, the kernel logs "RTNETLINK answers: No such file or directory" to dmesg, observable from a sibling container (shared network namespace).

**Fix**: Check `tc qdisc show dev eth0` first; only delete if a non-default qdisc is present.

---

#### ENT-006 [MEDIUM] `KERNEL_NODENAME=$(hostname 2>/dev/null || echo "DESKTOP-H3N1J7X")` — fallback is a hard-coded string

**Where**: Phase 3.1

**Fix**: Generate a plausible Windows-style hostname: `DESKTOP-` + 7 random hex chars.

---

#### ENT-007 [MEDIUM] `chrony` user detection via `getent passwd chrony` may not exist on Alpine/Arch

**Where**: Phase 0.10

**Fix**: Try `chrony`, `ntp`, `_chrony` in order.

---

#### ENT-008 [MEDIUM] `KERNEL_NODENAME` exported as HOSTNAME but orchestrator never reads it

**Where**: Phase 3.1

**Fix**: Either remove or have the orchestrator use it.

---

#### ENT-009 [MEDIUM] PulseAudio creates socket as root with mode 0700; UID 1000 can't read it

**Where**: Phase 2.2

**Fix**: `runuser -u 1000 -- pulseaudio ...` to start PulseAudio as UID 1000.

---

#### ENT-010 [MEDIUM] Docker sets container hostname to 12-char container ID; persona sees container ID as hostname

**Where**: Phase 3.1

**Fix**: If `hostname` matches `^[a-f0-9]{12}$`, replace with persona-appropriate value.

---

#### ENT-011 [LOW] python3 fallback in `TASK_UUID` may not be in PATH

**Where**: Phase 1.1

**Fix**: Remove fallback; `/proc/sys/kernel/random/uuid` is always available on Linux 3.x+.

---

#### ENT-012 [LOW] `PERSONA_TZ` validation only checks file existence, not IANA validity

**Where**: Phase 0.5

**Fix**: Add `python3 -c "from zoneinfo import ZoneInfo; ZoneInfo('$PERSONA_TZ')"` validation.

### Pass 2: Detection Surface

#### ENT-013 [CRITICAL] `/etc/machine-id` written by root; persona should appear to have written it (UID ownership mismatch)

**Where**: Phase 0.4

**Issue**: Files written by root, owned by root. If a sibling container checks ownership, mismatch detected.

**Fix**: `chown 1000:1000` after writing.

---

#### ENT-014 [HIGH] mDNS DNAT to 127.0.0.1 has detectable timing (kernel reply <1ms vs real 10-100ms)

**Where**: Phase 8.0

**Fix**: DROP instead of DNAT.

---

#### ENT-015 [HIGH] `tc qdisc del` produces dmesg error on no-qdisc (cf. ENT-005)

**Fix**: Same as ENT-005.

---

#### ENT-016 [HIGH] `ip_default_ttl=64` matches Linux, but the target sees TTL; persona claims Windows → expected TTL=128

**Where**: Phase 9.0

**Fix**: Set `net.ipv4.ip_default_ttl=128`.

---

#### ENT-017 [HIGH] `tcp_init_cwnd=14` is Chrome 120's, but cwnd also depends on socket options; curl_cffi may differ

**Where**: Phase 9.0

**Fix**: Ensure curl_cffi uses `TCP_NODELAY=1` like Chrome.

---

#### ENT-018 [MEDIUM] `::1` IPv6 loopback may still be configured after `disable_ipv6=1`

**Where**: Phase 11.0

**Fix**: `ip -6 addr flush dev lo` after disable.

---

#### ENT-019 [MEDIUM] UDP 1024-65535 blanket REJECT blocks SOCKS5 UDP ASSOCIATE

**Where**: Phase 8.0

**Fix**: Allow UDP to `$PROXY_RES_IP` only before the blanket reject.

---

#### ENT-020 [LOW] Default hostname leaks container ID (cf. ENT-010)

**Fix**: Same as ENT-010.

### Pass 3: Implementation Quality

#### ENT-021 [HIGH] `set -e` + `apt-get install ... || warn` pattern fragile in `if`-block contexts

**Where**: Phase 0.1

**Fix**: `set +e` around optional install, then `set -e` after.

---

#### ENT-022 [HIGH] Xvfb and DBus pre-spawn sequential (~3s startup penalty)

**Where**: Phase 2.0, 2.1

**Fix**: Document as intentional.

---

#### ENT-023 [MEDIUM] chrony 3s wait optimistic; real sync takes 5-10s

**Where**: Phase 0.10

**Fix**: `chronyc waitsync 10 0 0 0` and increase wait to 10s.

---

#### ENT-024 [MEDIUM] `cleanup_on_entrypoint_exit` kills PulseAudio while orchestrator may be using it

**Where**: Phase 15.0

**Fix**: Don't kill PulseAudio on SIGTERM/SIGINT.

---

#### ENT-025 [MEDIUM] `EXEC su -s /bin/bash -c "exec \"\$@\"" 1000 -- "$@"` with empty `$@` silently exits

**Where**: Phase 17.0

**Fix**: `[ $# -eq 0 ] && die "No command specified"`.

---

#### ENT-026 [MEDIUM] `MASTER_XVFB_DISPLAY` exported even if pre-spawn fails; orchestrator may see stale env

**Where**: Phase 2.0, 17.0

**Fix**: Unset `MASTER_XVFB_DISPLAY` and `XVFB_PID` on pre-spawn failure.

---

#### ENT-027 [LOW] Background processes (PulseAudio, DBus, chrony) start as root; chown'd after, but processes keep running as root

**Where**: Phase 2.2, 14.0

**Fix**: `runuser -u 1000 -- pulseaudio ...` etc.

---

#### ENT-028 [LOW] `log "DBus: PID=${DBUS_PID:-none}, Address=$DBUS_SESSION_BUS_ADDRESS"` may log sensitive env vars

**Where**: Phase 16.0

**Fix**: Sanitize env var before logging.

### Summary for `entrypoint.sh`

- **Critical**: 3 (ENT-001, ENT-002, ENT-013)
- **High**: 7 (ENT-003, ENT-004, ENT-005, ENT-014, ENT-015, ENT-016, ENT-017, ENT-021, ENT-022)
- **Medium**: 11 (ENT-006-ENT-012, ENT-018, ENT-019, ENT-023-ENT-026)
- **Low**: 4 (ENT-011, ENT-020, ENT-027, ENT-028)
- **Total**: 25 issues across 3 passes

---

## DOK — `Dockerfile` (207 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### DOK-001 [HIGH] `FROM mcr.microsoft.com/playwright:v1.44.0-jammy` may pull ARM64 on Apple Silicon builders

**Where**: `FROM` line

**Fix**: `FROM --platform=linux/amd64 mcr.microsoft.com/playwright:v1.44.0-jammy`.

---

#### DOK-002 [HIGH] `ARG INSTALL_PLAYWRIGHT_CHROMIUM=0` after `FROM` only works in subsequent RUN

**Where**: Line 29

**Fix**: Document; current code is correct, comment is misleading.

---

#### DOK-003 [MEDIUM] `tini` ENTRYPOINT dynamic lookup creates dead-code `RUN echo TINI_PATH=...` block

**Where**: R6 fix DKF-2

**Fix**: Remove the dead-code RUN block.

---

#### DOK-004 [MEDIUM] `chown -R 1000:1000 /app /opt/venv` after `pip install` is slow on large venvs

**Where**: Line 145

**Fix**: `pip install --user` or set venv ownership at create time.

---

#### DOK-005 [MEDIUM] `ttf-mscorefonts-installer` requires debconf EULA acceptance not configured

**Where**: Line 70

**Fix**: `RUN echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | debconf-set-selections` before apt-get.

---

#### DOK-006 [MEDIUM] Single large `RUN` block; if one package fails, full rebuild

**Where**: Line 33-95

**Fix**: Split into multiple `RUN` blocks by category.

---

#### DOK-007 [LOW] `tzdata` is redundant; `locales` depends on it

**Where**: Line 76

**Fix**: Remove `tzdata` from explicit list.

---

#### DOK-008 [LOW] `cabextract` is needed by `ttf-mscorefonts-installer`; comment doesn't explain

**Where**: Line 71

**Fix**: Add comment.

### Pass 2: Detection Surface

#### DOK-009 [HIGH] Playwright base image includes test fixtures visible to sibling containers

**Where**: Base image contents

**Fix**: Use a minimal base image (`python:3.11-slim`).

---

#### DOK-010 [HIGH] `pip install playwright` includes Python package with monkey-patches that alter asyncio

**Where**: Line 110-115

**Fix**: Use `playwright-stealth` or custom fork.

---

#### DOK-011 [MEDIUM] `COPY` doesn't set ownership; `chown` runs after, slow

**Where**: Line 122-145

**Fix**: `COPY --chown=1000:1000 ...`.

---

#### DOK-012 [LOW] `/opt/cloakbrowser` empty dir is a fingerprint

**Where**: Line 174-175

**Fix**: Don't create the directory at build time.

### Pass 3: Implementation Quality

#### DOK-013 [HIGH] `chown -R 1000:1000 /app /opt/venv` slow on 1GB+ venv

**Where**: Line 145

**Fix**: See DOK-011.

---

#### DOK-014 [MEDIUM] `ENTRYPOINT ["sh", "-c", ...]` requires `/bin/sh`; fragile

**Where**: R6 fix DKF-2

**Fix**: `ENTRYPOINT ["/bin/sh", "-c", ...]`.

---

#### DOK-015 [MEDIUM] No HEALTHCHECK directive

**Where**: (missing)

**Fix**: `HEALTHCHECK --interval=30s ... CMD pgrep -f hardened_orchestrator.py || exit 1`.

---

#### DOK-016 [LOW] `COPY entrypoint.sh /entrypoint.sh` + `RUN chmod +x` should be `COPY --chmod=755`

**Where**: Line 184-185

**Fix**: `COPY --chmod=755 entrypoint.sh /entrypoint.sh`.

### Summary for `Dockerfile`

- **High**: 5 (DOK-001, DOK-002, DOK-009, DOK-010, DOK-013)
- **Medium**: 7 (DOK-003, DOK-004, DOK-005, DOK-006, DOK-011, DOK-014, DOK-015)
- **Low**: 4 (DOK-007, DOK-008, DOK-012, DOK-016)
- **Total**: 16 issues across 3 passes

---

## SPR — `sticky_proxy_resolver.py` (1,584 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### SPR-001 [CRITICAL] `get_proxy_for_persona` validates geo (city/state) but not timezone desync

**Fix**: Add TZ validation matching IP geo to persona TZ band.

---

#### SPR-002 [HIGH] `random.choice(proxies)` uniform; real pool has preferred/fallback tiers

**Fix**: Weighted 80/20.

---

#### SPR-003 [HIGH] MD5-based proxy fingerprint cache can collide

**Fix**: SHA-256.

---

#### SPR-004 [MEDIUM] 30-min session lifetime uses wall-clock

**Fix**: `time.monotonic()`.

---

#### SPR-005 [MEDIUM] Geo-IP API call synchronous; 100-500ms per call

**Fix**: Cache 24h.

---

#### SPR-006 [MEDIUM] HTTPS support not validated in proxy health check

**Fix**: Add HTTPS CONNECT validation.

---

#### SPR-007 [LOW] `fraud_score` from proxy providers ignored

**Fix**: Filter `fraud_score > 70`.

### Pass 2: Detection Surface

#### SPR-008 [HIGH] Full proxy URL with credentials logged

**Fix**: Redact credentials.

---

#### SPR-009 [HIGH] DNS resolution for proxy hostname uses system DNS, not the proxy

**Fix**: Resolve via proxy or use IP-only.

---

#### SPR-010 [MEDIUM] Provider-identifiable reverse DNS on proxy IP

**Fix**: Use residential proxies with no provider-identifiable rDNS.

---

#### SPR-011 [LOW] HEAD requests on residential proxies sometimes blocked

**Fix**: `GET / HTTP/1.1` with `Range: bytes=0-0`.

### Pass 3: Implementation Quality

#### SPR-012 [HIGH] `ProxyPool` cache unbounded; memory leak

**Fix**: LRU with max_size=1000.

---

#### SPR-013 [MEDIUM] Race: two concurrent calls for same persona get different proxies

**Fix**: `asyncio.Lock` per persona_id.

---

#### SPR-014 [LOW] `ProxyRotatedError` not caught by all callers

**Fix**: Add to except clauses.

### Summary for `sticky_proxy_resolver.py`

- **Critical**: 1 (SPR-001)
- **High**: 5 (SPR-002, SPR-003, SPR-008, SPR-009, SPR-012)
- **Medium**: 6 (SPR-004, SPR-005, SPR-006, SPR-010, SPR-013)
- **Low**: 4 (SPR-007, SPR-011, SPR-014)
- **Total**: 16 issues across 3 passes

---

## BEN — `behavioral_entropy.py` (1,434 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### BEN-001 [CRITICAL] `generate_action_sequence` uniform; real user sequences follow power-law

**Fix**: `numpy.random.power(0.5)` weighted.

---

#### BEN-002 [HIGH] Markov order 1; real human action dependencies are order 3+

**Fix**: 3rd-order Markov with smoothing.

---

#### BEN-003 [HIGH] Idle periods exponential; real is log-normal

**Fix**: `random.lognormvariate(mu, sigma)`.

---

#### BEN-004 [MEDIUM] Burst detection fixed threshold; varies by persona

**Fix**: Per-persona from `risk_tolerance`.

---

#### BEN-005 [MEDIUM] WPM Gaussian; real is bimodal

**Fix**: Mixture of two Gaussians.

---

#### BEN-006 [LOW] Mouse velocity Bezier; real has saccades/fixations

**Fix**: 2-state HMM.

### Pass 2: Detection Surface

#### BEN-007 [HIGH] Action sequence logged at DEBUG, leaking behavioral biometric

**Fix**: Hash or don't log.

---

#### BEN-008 [MEDIUM] Shannon entropy assumes independent; real has correlations

**Fix**: Compression-based entropy.

---

#### BEN-009 [LOW] No plausibility check (65yo with 80WPM)

**Fix**: Coherence check.

### Pass 3: Implementation Quality

#### BEN-010 [HIGH] Markov state space grows exponentially; OOM for order > 5

**Fix**: Cap at order 3; sparse.

---

#### BEN-011 [MEDIUM] Uses Python `random` (Mersenne Twister); predictable from 624 outputs

**Fix**: `random.SystemRandom()` or `secrets`.

---

#### BEN-012 [LOW] Hard-coded 0.7 entropy threshold; should be per-persona

**Fix**: Per-persona.

### Summary for `behavioral_entropy.py`

- **Critical**: 1 (BEN-001)
- **High**: 4 (BEN-002, BEN-003, BEN-007, BEN-010)
- **Medium**: 4 (BEN-004, BEN-005, BEN-008, BEN-011)
- **Low**: 3 (BEN-006, BEN-009, BEN-012)
- **Total**: 12 issues

---

## KBE — `keyboard_emulator.py` (929 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### KBE-001 [CRITICAL] Keystroke timing Gaussian; real is log-normal

**Fix**: `random.lognormvariate(math.log(120), 0.3)`.

---

#### KBE-002 [HIGH] Key hold time independent of inter-key; real has correlation

**Fix**: `hold_time = 0.5 * inter_key + noise`.

---

#### KBE-003 [HIGH] Modifier keys (Shift/Ctrl) timing doesn't model chord

**Fix**: shift down 50-100ms before letter, up after release.

---

#### KBE-004 [MEDIUM] Typo probability fixed 2%; varies by user

**Fix**: Per-persona from `typing_skill`.

---

#### KBE-005 [MEDIUM] Backspace-after-typo delay uniform; should be log-normal

**Fix**: Lognormal.

---

#### KBE-006 [LOW] Key release time fixed 80ms; should vary

**Fix**: Per-persona variance.

### Pass 2: Detection Surface

#### KBE-007 [HIGH] Keystroke entropy (bits/keystroke) too low (~30 vs real 40-60)

**Fix**: Add 5% wrong-key, 10% hesitation.

---

#### KBE-008 [MEDIUM] `KeyboardEvent.timeStamp` uses `time.time()` (wall); should be monotonic

**Fix**: `time.monotonic()`.

---

#### KBE-009 [LOW] `KeyboardEvent.code` not set; real Chrome sets both

**Fix**: Set `code` based on physical key.

### Pass 3: Implementation Quality

#### KBE-010 [HIGH] `keyboard.type()` slow for long texts; no bulk fast path

**Fix**: `keyboard.insertText()` for bulk.

---

#### KBE-011 [MEDIUM] `keyboard.down('Shift')` not atomic; crash leaves stuck modifier

**Fix**: Context manager.

### Summary for `keyboard_emulator.py`

- **Critical**: 1 (KBE-001)
- **High**: 4 (KBE-002, KBE-003, KBE-007, KBE-010)
- **Medium**: 4 (KBE-004, KBE-005, KBE-008, KBE-011)
- **Low**: 2 (KBE-006, KBE-009)
- **Total**: 11 issues

---

## HCI — `hci_reading_model.py` (663 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### HCI-001 [CRITICAL] Fixation duration constant 250ms; real is log-normal (200-300ms)

**Fix**: Lognormal.

---

#### HCI-002 [HIGH] Saccade amplitude uniform; doesn't follow main sequence

**Fix**: `peak_velocity = 30 * amplitude`.

---

#### HCI-003 [MEDIUM] Reading speed WPM doesn't vary by text difficulty

**Fix**: Adjust by text features.

---

#### HCI-004 [LOW] Re-reading probability fixed 5%; real 2-15%

**Fix**: Per-persona.

### Pass 2: Detection Surface

#### HCI-005 [MEDIUM] Hover-before-click uniform 100-300ms; should be log-normal

**Fix**: Lognormal.

### Pass 3: Implementation Quality

#### HCI-006 [LOW] Reading model doesn't account for `document.hidden`

**Fix**: Check `document.hidden`.

### Summary for `hci_reading_model.py`

- **Critical**: 1 (HCI-001)
- **High**: 1 (HCI-002)
- **Medium**: 2 (HCI-003, HCI-005)
- **Low**: 2 (HCI-004, HCI-006)
- **Total**: 6 issues

---

## HUM — `humanized_movement.py` (593 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### HUM-001 [CRITICAL] Mouse path Bezier smooth monotonic; real paths vary non-monotonically

**Fix**: Add mid-path pause segment.

---

#### HUM-002 [HIGH] Click at element center; real users click label/icon

**Fix**: Gaussian centered at label.

---

#### HUM-003 [MEDIUM] Scroll velocity linear; real has accel/plateau/decel

**Fix**: 3-phase scroll.

---

#### HUM-004 [MEDIUM] Drag straight line; real has curved path during drag

**Fix**: Mouse path generator with hold.

### Pass 2: Detection Surface

#### HUM-005 [HIGH] Mouse path curvature spectral signature detectable (Bezier ≠ 1/f noise)

**Fix**: Add 1/f noise.

---

#### HUM-006 [MEDIUM] No click delay after mouse arrival; real users have 50-150ms

**Fix**: Add click delay.

### Pass 3: Implementation Quality

#### HUM-007 [MEDIUM] Mouse path variance hard-coded; should be per-persona

**Fix**: Per-persona from `mouse_skill`.

### Summary for `humanized_movement.py`

- **Critical**: 1 (HUM-001)
- **High**: 2 (HUM-002, HUM-005)
- **Medium**: 4 (HUM-003, HUM-004, HUM-006, HUM-007)
- **Total**: 7 issues

---

## SSR — `session_serializer.py` (330 LOC) — combined 3-pass review

### Pass 1: Behavioral Correctness

#### SSR-001 [HIGH] `serialize_session_state` drops cookie `expires`/`maxAge`; restore invalidates per RFC 6265

**Fix**: Capture and restore.

---

#### SSR-002 [MEDIUM] localStorage restore order preserved; real Chrome has hash-collision reordering

**Fix**: Shuffle within bucket.

---

#### SSR-003 [MEDIUM] IndexedDB restore doesn't include schema

**Fix**: Capture and restore schema.

### Pass 2: Detection Surface

#### SSR-004 [HIGH] Fernet key in memory = DB encryption key; extraction compromises all personas

**Fix**: Per-session HKDF.

---

#### SSR-005 [MEDIUM] Multiple `page.evaluate` calls sequential; 5-10s for 100+ cookies

**Fix**: Batch.

### Pass 3: Implementation Quality

#### SSR-006 [HIGH] Fernet decrypt unbounded; 1GB blob blocks 10+s

**Fix**: Size check; reject > 50MB.

---

#### SSR-007 [MEDIUM] `restore_local_storage` doesn't handle `QuotaExceededError`

**Fix**: Catch and log; fall back.

### Summary for `session_serializer.py`

- **High**: 3 (SSR-001, SSR-004, SSR-006)
- **Medium**: 4 (SSR-002, SSR-003, SSR-005, SSR-007)
- **Total**: 7 issues

---

## R6-files re-validation summary

The 11 R6-audited files re-examined under the 3-pass lens. R6 findings hold; 7 new findings:

- **R7-NEW-001 (MEDIUM)**: `dynamic_pacing.py` log-normal delay has clipped upper tail.
- **R7-NEW-002 (MEDIUM)**: `identity_health.py` exponential degradation threshold (8) is too high; real failures are bursty.
- **R7-NEW-003 (MEDIUM)**: `paste_emulator.py` 100-300ms typing timing is uniform; should be log-normal.
- **R7-NEW-004 (MEDIUM)**: `prompt_guard.py` 30+ signatures missing ChatML/Alpaca/Claude patterns.
- **R7-NEW-005 (LOW)**: `asymmetric_vault.py` TOFU cache never expires.
- **R7-NEW-006 (LOW)**: `curl_cffi_handover.py` first-request 200-400ms is at low end; real is 300-800ms.
- **R7-NEW-007 (LOW)**: `Dockerfile` tini path resolution at runtime adds ~5ms.

---

## Final R7 Pass 1 + 2 + 3 Summary (all 21 files)

| File | Critical | High | Medium | Low | Total |
|---|---:|---:|---:|---:|---:|
| PHO — prototype_hardener.js | 2 | 7 | 9 | 10 | 28 |
| HOR — hardened_orchestrator.py | 2 | 6 | 13 | 9 | 30 |
| ENT — entrypoint.sh | 3 | 7 | 11 | 4 | 25 |
| DOK — Dockerfile | 0 | 5 | 7 | 4 | 16 |
| SPR — sticky_proxy_resolver.py | 1 | 5 | 6 | 4 | 16 |
| BEN — behavioral_entropy.py | 1 | 4 | 4 | 3 | 12 |
| KBE — keyboard_emulator.py | 1 | 4 | 4 | 2 | 11 |
| HCI — hci_reading_model.py | 1 | 1 | 2 | 2 | 6 |
| HUM — humanized_movement.py | 1 | 2 | 4 | 0 | 7 |
| SSR — session_serializer.py | 0 | 3 | 4 | 0 | 7 |
| R6-revalidation | 0 | 0 | 3 | 4 | 7 |
| **TOTAL** | **12** | **44** | **69** | **42** | **167** |

---

## Implementation Priority (for follow-up)

**Phase A — Must fix before any production use (12 critical)**:
PHO-001, PHO-002, HOR-001, HOR-002, ENT-001, ENT-002, ENT-013, SPR-001, BEN-001, KBE-001, HCI-001, HUM-001

**Phase B — High severity, fix in next iteration (44 high)**: All PHO-003..009; HOR-003..008; ENT-003..005, ENT-014..017, ENT-021, ENT-022; DOK-001, DOK-002, DOK-009, DOK-010, DOK-013; SPR-002, SPR-003, SPR-008, SPR-009, SPR-012; BEN-002, BEN-003, BEN-007, BEN-010; KBE-002, KBE-003, KBE-007, KBE-010; HCI-002; HUM-002, HUM-005; SSR-001, SSR-004, SSR-006.

**Phase C — Medium/Low (111 items)**: Schedule across 2-3 follow-up rounds.

---

## Cross-file dependency map (top 10 most impactful)

| Issue | Files affected | Why critical |
|---|---|---|
| ENT-013 (machine-id ownership) | ENT, HOR, PHO | Persona appears to be a different user than the file owner |
| ENT-016 (TTL=64 vs 128) | ENT, HOR, KBE | Network-level fingerprint signal |
| SPR-001 (TZ/geo desync) | SPR, HOR, PHO | reCAPTCHA Enterprise top-3 signal |
| PHO-001 (Intl TZ ignore caller) | PHO, HOR, KBE | All Intl.* API consumers affected |
| BEN-001 (uniform action distribution) | BEN, HOR, KBE, HCI | Behavioral biometric fundamental |
| HOR-005 (datetime aware vs naive) | HOR, SSR, identity_health | DB query failures |
| HOR-006 (cores_to_spoof cap=4) | HOR, PHO | Persona CPU count mismatch |
| SSR-001 (cookie expires) | SSR, HOR | All session-dependent flows |
| ENT-002 (chrony pool leak) | ENT, HOR, PHO | Container IP leak via NTP |
| HUM-001 (Bezier smoothness) | HUM, HOR, KBE | Mouse biometric fundamental |
