# DEEP-DIVE RUNTIME SIMULATION & CONFLICT ANALYSIS: `browser-runtime/entrypoint.sh`
# ===================================================================================

**Date**: 2026-07-09
**Author**: Cyber Security Architect / Principal Software Engineer (Stealth Browser Automation)
**Scope**: Exhaustive forensic analysis of `browser-runtime/entrypoint.sh` (v6.0.1, 775 lines) against the full architectural stack.
**Goal**: Guarantee zero leakages and zero technical anomalies that could expose the automation stack to elite anti-bot systems (Google reCAPTCHA Enterprise, Cloudflare Turnstile, Akamai, PerimeterX).

---

## EXECUTIVE SUMMARY

The current `entrypoint.sh` (v6.0.1) has been significantly improved from the baseline, but a rigorous deep-dive reveals **CRITICAL ISSUES** that would compromise invisibility. This analysis covers:

1. **9 Critical/High-severity issues** in the current script
2. **3 Major inter-file conflicts** between entrypoint and orchestrator
3. **4 Boot race conditions** that could cause headless fallback detection
4. **6 Hidden bugs** in signal handling, process management, and iptables
5. **12 Gap items** in font libraries, render extensions, and environment sanitization
6. **Complete production-grade replacement** addressing all findings

The issues are organized by severity and the architectural layer they affect.

---

## SECTION 1: HARDWARE ENVIRONMENT SIMULATION

### 1.1 Xvfb/X11 Configuration — CRITICAL ISSUES FOUND

**Current State** (lines ~330-390):
```bash
XVFB_CMD=(
    "Xvfb" ":$MASTER_XVFB_DISPLAY"
    "-screen" "0" "${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}x${PERSONA_COLOR_DEPTH}"
    "-ac" "+extension" "GLX" "+extension" "RENDER" "+extension" "XFIXES" "+extension" "XDamage" "+extension" "XINERAMA"
    "+render" "-noreset" "-nolisten" "tcp" "-dpi" "96"
    "-screen" "0" "${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}x${PERSONA_COLOR_DEPTH}"
)
```

#### Issue 1.1.1: **CRITICAL — Duplicate `-screen` flag**
**Problem**: The `-screen` flag is specified TWICE in the XVFB_CMD array. Xvfb parses the LAST occurrence and ignores the first. This means the first definition (with extensions) is effectively a no-op, and only the bare resolution flag is used.

**Detection Vector**: Anti-bot scripts can detect that extensions are missing by querying:
```js
const exts = window.chrome?.webgl?.getSupportedExtensions();
// Real Chrome reports ~30+ extensions; SwiftShader/headless reports ~15
```

**Fix**: Remove the duplicate `-screen` flag.

#### Issue 1.1.2: **CRITICAL — Missing `-shmem` flag**
**Problem**: Xvfb defaults to using MIT-SHM for shared memory operations. In a Docker container with `/dev/shm` limited to 512MB (per `shm_size: '512m'` in docker-compose.yml), heavy Chromium rendering can exhaust shared memory.

**Detection Vector**: Chromium silently falls back to non-shared rendering when `/dev/shm` is full, causing "GPU process exited" errors visible in `chrome://gpu`.

**Fix**: Add `-shmem` to force shared memory operations OR ensure `/dev/shm` is properly sized.

#### Issue 1.1.3: **HIGH — `-dpi 96` may not match persona**
**Problem**: A real Dell XPS 13 9370 with a 4K display has a DPI of 315 (or 192 for FHD). Setting `-dpi 96` is the default for a generic monitor but doesn't match a 4K persona.

**Detection Vector**:
```js
window.devicePixelRatio // Returns 1 for 96 DPI, 2 for 192 DPI, 3.15 for 4K
```

A persona claiming a 4K display but reporting `devicePixelRatio: 1` is a fingerprint inconsistency.

**Fix**: Calculate DPI from persona screen dimensions and pass via `PERSONA_DPI` env var.

#### Issue 1.1.4: **HIGH — Missing RANDR/Composite extensions**
**Problem**: Chromium queries RANDR (Resize and Rotate) and Composite extensions for multi-monitor detection. Without them, `screen.isExtended` will always be `false`, which is correct for a single-monitor persona but the `getMonitorInfo()` call may fail.

**Detection Vector**:
```js
// Chromium logs "XRandR: XRRGetScreenResources" to stderr when RANDR is queried
// If RANDR is missing, Chromium logs a warning
```

**Fix**: Add `+extension RANDR` to the Xvfb command.

#### Issue 1.1.5: **MEDIUM — `availHeight` taskbar simulation missing**
**Problem**: The comment in the script (line ~360) mentions "synthetic taskbar" but the code does NOT implement it. A real Windows 10/11 desktop has a 40px taskbar, so `screen.availHeight = screen.height - 40`.

**Detection Vector**:
```js
screen.availHeight // Headless Xvfb: same as screen.height
                    // Real Windows: screen.height - 40 (taskbar)
```

**Fix**: Use `xrandr` to set a virtual workarea that excludes a 40px taskbar region.

### 1.2 Peripheral & Subsystem Mocking — MAJOR GAPS FOUND

**Current State** (lines ~440-490):
```bash
# DBus session bus launch
dbus-daemon --config-file "$DBUS_CONF_DIR/session.conf" --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile &

# PulseAudio null-sink launch
pulseaudio --system --daemonize --disallow-exit --exit-idle-time=-1 ...
pacmd load-module module-null-sink sink_name=null0
```

#### Issue 1.2.1: **CRITICAL — `dbus-daemon --nofork` blocks the entrypoint**
**Problem**: The `dbus-daemon --nofork` command is run with `&` (backgrounded), BUT the script continues immediately. The `sleep 0.5` is insufficient for dbus-daemon to fully initialize its socket and accept connections.

**Detection Vector**:
- When ChromeBrowser tries to connect to D-Bus during startup, it may fail with "Failed to connect to socket /tmp/dbus-session-bus: Connection refused"
- ChromeBrowser falls back to "no D-Bus" mode, which is a fingerprint

**Fix**: Increase `sleep 0.5` to at least 2.0 seconds, and add a `dbus-send` test to verify the socket is accepting connections.

#### Issue 1.2.2: **CRITICAL — `pulseaudio --system` may fail in containers**
**Problem**: Running `pulseaudio --system` requires:
1. The `pulseaudio` system-mode configuration in `/etc/pulse/system.pa`
2. The `pulse` system user to exist
3. Write access to `/var/run/pulse/`

In a Debian 12 container, none of these are configured by default. The fallback `pulseaudio --daemonize` (user mode) will run, but `--system` will fail silently.

**Detection Vector**:
- `pactl info` returns "Connection refused" or "No such file or directory"
- ChromeBrowser's `--use-fake-device-for-media-stream` may not work correctly

**Fix**: Remove the `--system` attempt and use only `--daemonize` (user mode) with proper XDG_RUNTIME_DIR.

#### Issue 1.2.3: **HIGH — `pacmd` requires PulseAudio to be running**
**Problem**: The script runs `pacmd load-module module-null-sink` immediately after launching pulseaudio. If pulseaudio hasn't fully started, pacmd will fail silently (no error output).

**Detection Vector**:
- `pacmd list-sinks` returns empty
- ChromeBrowser's `enumerateDevices()` returns empty audio devices

**Fix**: Add a `pactl info` readiness check before `pacmd` commands, with retries.

#### Issue 1.2.4: **HIGH — No video acceleration layer**
**Problem**: The script does NOT set up any video acceleration (VA-API, VDPAU, or NVIDIA). Chromium uses `--use-angle=swiftshader-webgl` (per orchestrator), which is correct, but the entrypoint should ensure the required Mesa/Vulkan libraries are available.

**Detection Vector**:
- `chrome://gpu` shows "Software only" for video decode
- Some fingerprint scripts check `MediaCapabilities.decodingInfo` for hardware acceleration support

**Fix**: Add `libva2`, `libvdpau1`, and `mesa-vulkan-drivers` to the Dockerfile. In the entrypoint, verify these libraries are loadable.

#### Issue 1.2.5: **MEDIUM — No input device mocking**
**Problem**: Xvfb does not provide input devices. ChromeBrowser may probe for `/dev/input/event*` and find none.

**Detection Vector**:
- `navigator.maxTouchPoints` returns 0 (correct for desktop) but `InputDeviceCapabilities` may be empty
- Some scripts check `EventSource` for keyboard/mouse events

**Fix**: This is acceptable for desktop personas. No fix needed.

---

## SECTION 2: DESYNCHRONIZATION, LOGICAL CONFLICTS & RUNTIME FAULTS

### 2.1 Inter-File Logical Collisions

#### Conflict 2.1.1: **CRITICAL — `DISPLAY` variable is set TWICE**
**Problem**: The entrypoint exports `DISPLAY=:99` (line 47), and the orchestrator's `is_display_ready()` checks for display readiness at display numbers `100-999` (line ~700 of orchestrator). The orchestrator also spawns its OWN Xvfb at a random display number (line ~730 of orchestrator), but the entrypoint has ALREADY spawned Xvfb at `:99`.

**Detection Vector**:
- Two Xvfb processes running (entrypoint's `:99` and orchestrator's random `:N`)
- The orchestrator's Xvfb spawn takes 5+ seconds, during which the entrypoint's Xvfb at `:99` is idle
- If the orchestrator's Xvfb spawn fails, it falls back to the entrypoint's Xvfb, but the persona's `browser_env["DISPLAY"]` is set to the orchestrator's number, not `:99`

**Impact**: **INCONSISTENT STATE** — the script has pre-spawned Xvfb, but the orchestrator doesn't use it.

**Fix**: Either:
1. Remove the per-persona Xvfb spawn from the orchestrator and use the entrypoint's Xvfb
2. Remove the pre-spawn from the entrypoint and let the orchestrator handle it
3. Make the orchestrator's Xvfb spawn respect the entrypoint's `MASTER_XVFB_DISPLAY` if it's available

**RECOMMENDATION**: Option 1 — the entrypoint pre-spawns, the orchestrator uses the pre-spawned display.

#### Conflict 2.1.2: **HIGH — `XDG_RUNTIME_DIR` is set BEFORE pulseaudio**
**Problem**: The entrypoint sets `XDG_RUNTIME_DIR="/tmp/xdg-runtime-1000"` (line 48) and exports it. However, PulseAudio's `PULSE_RUNTIME_PATH` is set to `"/tmp/pulse-runtime"` (line 50). These are DIFFERENT directories.

PulseAudio, when started with `pulseaudio --daemonize`, looks for its socket in `$XDG_RUNTIME_DIR/pulse/` by default, NOT in `$PULSE_RUNTIME_PATH`.

**Detection Vector**:
- `pactl info` shows "Server: No" (no connection to PulseAudio)
- ChromeBrowser's audio queries fail silently

**Fix**: Set `PULSE_RUNTIME_PATH` to `$XDG_RUNTIME_DIR/pulse` OR set `XDG_RUNTIME_DIR` to the same path as `PULSE_RUNTIME_PATH`.

#### Conflict 2.1.3: **HIGH — `sticky_proxy_resolver.py` uses raw `socks5://` but orchestrator uses `socks5h://`**
**Problem**: Looking at `sticky_proxy_resolver.py` (line 28):
```python
adapted_proxy_url = proxy_url.replace("socks5://", "socks5h://")
```

This is correct — it converts to `socks5h://` for remote DNS resolution. But the `compile_dynamic_sticky_proxy_string()` method (line 153) returns a URL that may not have the `h` suffix, and the orchestrator's proxy parsing may not handle this correctly.

**Detection Vector**:
- DNS resolution may leak to the local system resolver
- The proxy URL may not parse correctly in some code paths

**Fix**: Ensure all proxy URLs use `socks5h://` consistently.

#### Conflict 2.1.4: **MEDIUM — `TZ` is set TWICE**
**Problem**: The entrypoint sets `export TZ="$PERSONA_TZ"` (line ~190), but the orchestrator also sets `browser_env["TZ"] = timezone` (line ~790 of orchestrator). The orchestrator's `timezone` variable comes from the database, which may differ from `PERSONA_TZ` if the env var is not set correctly.

**Detection Vector**:
- `Date.getTimezoneOffset()` may return a different value than expected
- `Intl.DateTimeFormat().resolvedOptions().timeZone` may not match

**Fix**: Ensure `PERSONA_TZ` is the single source of truth. The entrypoint sets it, the orchestrator reads it from the env var (not from the database).

### 2.2 Runtime Breakdown — The Boot Race Condition

**Current Boot Sequence**:
1. `tini` (PID 1) starts → calls `/entrypoint.sh`
2. `entrypoint.sh`:
   - Disables core dumps
   - Validates binaries
   - Generates machine-id
   - Sets timezone/locale
   - Validates mock files
   - Pre-mounts tmpfs
   - **Pre-spawns Xvfb at :99** (line ~330)
   - Pre-warms fontconfig
   - **Launches DBus** (line ~440)
   - **Launches PulseAudio** (line ~470)
   - Sets up iptables
   - Drops to UID 1000
   - `exec su 1000 -- python3 hardened_orchestrator.py`
3. `hardened_orchestrator.py`:
   - Imports modules
   - Creates DB pool
   - Enters daemon loop
   - For each task: **spawns a NEW Xvfb** (line ~730)
   - Launches ChromeBrowser

**Race Conditions Identified**:

#### Race 2.2.1: **CRITICAL — Double Xvfb spawn**
**Problem**: The entrypoint spawns Xvfb at `:99`, but the orchestrator spawns another Xvfb at a random `:100-999`. Both run simultaneously, consuming memory and creating display confusion.

**Impact**: 2 Xvfb processes × ~50MB each = 100MB wasted on a 1GB E2.1.Micro instance.

**Fix**: Remove the per-persona Xvfb spawn from the orchestrator. Use the entrypoint's Xvfb.

#### Race 2.2.2: **CRITICAL — DBus not ready when orchestrator starts**
**Problem**: The entrypoint launches `dbus-daemon --nofork` with `&` and only waits 0.5 seconds. The orchestrator's first action (importing `aiofiles`, `httpx`, etc.) takes ~2-3 seconds. By then, DBus should be ready. BUT if the orchestrator's DB pool initialization takes longer (e.g., 5+ seconds due to Postgres startup), and a task fires before DBus is fully initialized, the first persona launch will fail.

**Fix**: Increase the DBus wait to 2.0 seconds, and add a `dbus-send` readiness test.

#### Race 2.2.3: **HIGH — Fontconfig warm-up incomplete**
**Problem**: The entrypoint runs `fc-cache -f -v` (line ~400) to warm the font cache. But this scans `/usr/share/fonts/`, which in a minimal Docker image only has `fonts-liberation` (per the Dockerfile). The cache is "warm" but only for Liberation fonts.

**Detection Vector**:
- First canvas/font rendering operation takes 200-500ms (cold cache) instead of 5-10ms (warm cache)
- This timing difference is detectable via `performance.now()` measurements

**Fix**: Install additional fonts in the Dockerfile (`fonts-noto-core`, `fonts-noto-color-emoji`, `fonts-dejavu-core`). Run `fc-cache -f` AFTER font installation.

#### Race 2.2.4: **MEDIUM — Privilege drop order**
**Problem**: The entrypoint drops to UID 1000 via `exec su -s /bin/bash -c "exec \"\$@\"" 1000 -- "$@"`. The `exec` replaces the current shell. BUT the `trap cleanup_xvfb_and_dbus SIGTERM SIGINT SIGHUP` is set BEFORE the `exec`. The trap is inherited by the child process (orchestrator), so the orchestrator's SIGTERM handler is the entrypoint's cleanup function, NOT the orchestrator's own handler.

**Detection Vector**: 
- When `docker stop` sends SIGTERM, the entrypoint's trap fires, kills Xvfb/DBus, and exits with code 1
- The orchestrator never gets to run its graceful shutdown (which updates the database)

**Fix**: Use `exec` WITHOUT the trap, OR reset the trap in the orchestrator's startup code.

### 2.3 Hidden Bugs & Logical Flaws

#### Bug 2.3.1: **CRITICAL — `set -eo pipefail` + `apt-get install` failure**
**Problem**: The entrypoint has `set -eo pipefail` (line 38). The `apt-get install` at line ~85 is wrapped in `|| true`:
```bash
apt-get install -y --no-install-recommends ... 2>/dev/null || warn "..."
```

BUT `set -e` does NOT trigger on commands in `if` statements or after `||`/`&&`. However, the `for` loop at line ~110 iterates over `CRITICAL_BINS` and calls `die` if a binary is missing. The `die` function exits with code 1.

**Impact**: If `dbus-daemon` is not installed and cannot be installed via apt, the script should warn and continue, not exit. But `set -e` combined with the `for` loop will exit on the first missing critical binary.

**Fix**: Change the logic to distinguish between "required" and "optional" binaries. Only `die` on truly critical ones (Xvfb, iptables, ip).

#### Bug 2.3.2: **HIGH — `persona_digest` is computed by Python, not the entrypoint**
**Problem**: The entrypoint computes `PERSONA_MACHINE_ID` from `PERSONA_ENTROPY_SEED` using Python. But the orchestrator also computes `persona_digest` (locked values like `deviceMemory`, `scroll_behavior`) from the persona ID.

**Impact**: The entrypoint's `PERSONA_MACHINE_ID` is based on the entropy seed (regenerated each container), while the orchestrator's `persona_digest` is based on the persona ID (stable across containers). These are DIFFERENT derivations and may produce inconsistent values.

**Fix**: Pass the persona ID to the entrypoint via env var, and derive ALL persona values from the same source.

#### Bug 2.3.3: **HIGH — `fc-cache` is run TWICE**
**Problem**: The entrypoint runs `fc-cache -f -v` at line ~340 (before Xvfb) and again at line ~400 (after Xvfb). This is redundant and adds 5-10 seconds to startup.

**Fix**: Remove the first `fc-cache` call.

#### Bug 2.3.4: **MEDIUM — `chown` may fail silently on read-only filesystems**
**Problem**: The entrypoint has `chown -R 1000:1000 /app /opt/venv 2>/dev/null || true` (line ~700). The `2>/dev/null` suppresses errors, and `|| true` ensures the script continues. But if the filesystem is read-only (which it is — `read_only: true` in docker-compose.yml), the `chown` will fail, and the orchestrator (running as UID 1000) will not be able to write to `/app`.

**Detection Vector**:
- Orchestrator fails to write logs to `/app/logs/`
- Orchestrator fails to write session archives to `/app/sessions/`

**Fix**: Move writeable directories to tmpfs (e.g., `/tmp/app` instead of `/app`) OR remove the `read_only: true` from docker-compose.yml.

#### Bug 2.3.5: **MEDIUM — `mktemp` not used for temp files**
**Problem**: The entrypoint uses fixed paths like `/tmp/dbus-conf/session.conf` (line ~430) and `/tmp/emerg_mock/cpuinfo` (line ~220). If two containers start simultaneously (e.g., during a persona rotation), they will share these paths.

**Fix**: Use `mktemp -d` to create unique directories.

#### Bug 2.3.6: **LOW — `pkill pulseaudio` may kill unrelated processes**
**Problem**: The cleanup function at line ~740 uses `pkill -TERM pulseaudio`, which kills ALL pulseaudio processes on the system, not just the one started by this entrypoint.

**Fix**: Track the specific PID and kill only that PID.

---

## SECTION 3: GAP ANALYSIS & MISSING ARCHITECTURAL ITEMS

### 3.1 Missing Font Libraries

**Currently in Dockerfile** (line 36):
```dockerfile
fonts-liberation
```

**Missing for Windows 10/11 persona fidelity**:
| Font | Importance | Reason |
|------|-----------|--------|
| Segoe UI | CRITICAL | Default Windows 10/11 system font |
| Segoe UI Variable | HIGH | Windows 11 system font |
| Segoe UI Emoji | HIGH | Emoji rendering fingerprint |
| Tahoma | MEDIUM | Legacy Windows font |
| Calibri | MEDIUM | Microsoft Office default |
| Verdana | LOW | Legacy web font |
| Arial | HIGH | Common Windows font |
| Times New Roman | LOW | Common but not in Liberation |

**Fix**: Add to Dockerfile:
```dockerfile
RUN apt-get install -y --no-install-recommends \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-color-emoji \
    fonts-dejavu-core \
    fonts-dejavu-extra \
    fonts-freefont-ttf \
    ttf-mscorefonts-installer
```

The `ttf-mscorefonts-installer` package downloads and installs the Microsoft TrueType Core Fonts (Arial, Times New Roman, Courier New, etc.).

### 3.2 Missing Render Extensions

**Currently in Xvfb command**:
```
+extension GLX, RENDER, XFIXES, XDamage, XINERAMA
```

**Missing for full ChromeBrowser compatibility**:
| Extension | Importance | Reason |
|-----------|-----------|--------|
| RANDR | HIGH | Multi-monitor detection |
| Composite | MEDIUM | Compositor acceleration |
| DRI3 | MEDIUM | Direct rendering infrastructure |
| Present | MEDIUM | Buffer presentation |

**Fix**: Add to Xvfb command:
```
+extension RANDR,Composite,DRI3,Present
```

### 3.3 Missing Environment Sanitization

**What anti-bots probe via canvas/WebGL that the current setup doesn't address**:

1. **`Path2D` rendering fingerprint** — prototype_hardener.js does NOT override `Path2D` methods
2. **`SVGGraphicsElement.getBBox()` fingerprint** — Not overridden
3. **`ImageBitmapRenderingContext` fingerprint** — Not overridden
4. **`TextMetrics.actualBoundingBox*` properties** — Only `width` is jittered
5. **`OffscreenCanvas.transferToImageBitmap()`** — Not overridden
6. **`WebGL2.computeContext` for GPU compute** — May leak hardware info
7. **`OffscreenCanvas.getContext('webgl2')`** — May have different rendering than main canvas
8. **Web Audio `OfflineAudioContext`** — Different from `AudioContext`

**Fix**: These are in `prototype_hardener.js`, not `entrypoint.sh`. The entrypoint's role is to ensure the environment supports these APIs (which it does, via Xvfb + PulseAudio + DBus).

### 3.4 Missing Configuration Validation

The entrypoint does NOT validate:
1. **CloakBrowser binary exists** at `/opt/cloakbrowser/chrome`
2. **Playwright Chromium fallback** at `~/.cache/ms-playwright/chromium-*`
3. **CloakBrowser is executable** (has execute permission)
4. **CloakBrowser version** is compatible with the orchestrator
5. **Google Fonts fallback** (if persona requires specific fonts)
6. **TLS certificate** for the DoH proxy
7. **GOST proxy** binary is available (for fallback)

**Fix**: Add a pre-flight check section.

### 3.5 Missing Entropy Seeding

**Problem**: The entrypoint generates `PERSONA_ENTROPY_SEED` from `/dev/urandom`, but does NOT seed the kernel's random pool. This means the persona's `Math.random()` calls (in JS) and Python's `random` module will use different entropy than the entrypoint's.

**Fix**: Add `dd if=/dev/urandom of=/dev/random bs=1 count=32 2>/dev/null || true` to add entropy to the kernel pool (if possible).

---

## SECTION 4: CONCRETE ARCHITECTURAL SOLUTIONS

See the rewritten `browser-runtime/entrypoint.sh` that addresses ALL findings. The key changes are:

1. **Removed duplicate `-screen` flag** in Xvfb command
2. **Added RANDR, Composite, DRI3, Present extensions** to Xvfb
3. **Added DPI calculation** from persona screen dimensions
4. **Removed redundant `fc-cache` call**
5. **Increased DBus wait time** to 2.0 seconds with readiness test
6. **Fixed PulseAudio user-mode launch** (removed `--system`)
7. **Added pre-flight CloakBrowser validation**
8. **Added Microsoft TrueType Core Fonts** recommendation (Dockerfile change)
9. **Added taskbar simulation** via xrandr
10. **Added environment sanitization** for race conditions
11. **Fixed privilege drop order** to not inherit traps
12. **Added per-process PID tracking** for clean shutdown
13. **Added mktemp for unique paths**
14. **Added shared memory validation**
15. **Added entropy seeding**

The complete solution is provided in the next file write.
