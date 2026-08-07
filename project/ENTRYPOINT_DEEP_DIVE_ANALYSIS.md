# Deep-Dive Runtime Simulation & Conflict Analysis: `browser-runtime/entrypoint.sh`
# ===================================================================================

**Date**: 2026-07-09
**Author**: Cyber Security Architect / Principal Software Engineer
**Scope**: Exhaustive analysis of `browser-runtime/entrypoint.sh` against the full architectural stack to guarantee zero leakages and zero technical anomalies.

This document provides a forensic-level breakdown of all vulnerabilities, conflicts, and missing items, followed by the complete production-grade solution.

---

## SECTION 1: HARDWARE ENVIRONMENT SIMULATION

### 1.1 Xvfb/X11 Configuration — CRITICAL FINGERPRINT VECTORS

**Current State** (line in `hardened_orchestrator.py`):
```python
xvfb_process = await asyncio.create_subprocess_exec(
    "Xvfb", f":{display_num}", "-screen", "0",
    f"{fp.get('screen', {}).get('width', 1920)}x{fp.get('screen', {}).get('height', 1080)}x24",
    "-ac", "+extension", "GLX", "+render", "-noreset",
    ...
)
```

**Critical Issues Identified**:

#### A. Default Resolution Leaks "Bot" Fingerprint
**Problem**: A real Windows 10/11 Dell XPS 13 9370 (the persona) has a 13.3" screen with **3840×2160 native** (4K UHD) or **1920×1080 FHD** depending on the model. The default `1920x1080` in `entrypoint.sh` (via fallback in orchestrator) is correct, BUT if the persona's `fp` dict has no `screen` key, we silently fall back to a common resolution that any random browser would have.

**Detection Vector**: 
- A real Windows 10/11 desktop with a 13.3" laptop screen reports `screen.width=1920, height=1080, availWidth=1920, availHeight=1040` (40px taskbar).
- A headless Xvfb by default reports `availHeight=screen.height` (no taskbar simulation).
- Without a synthetic taskbar, `window.screen.availHeight === window.screen.height` — instant bot detection.

**Required Fix**:
1. Pass `screen` dimensions from the persona's `fp` dict (NOT fallback defaults).
2. Inject a synthetic `taskbar` reserve into the persona's `availHeight` calculation.
3. Xvfb MUST be started with `-fbdir` pointing to a writable tmpfs, NOT `/tmp` (which may be a different filesystem).

#### B. Color Depth Mismatch (24-bit vs 30-bit HDR)
**Problem**: The Xvfb command uses `x24` (24-bit color depth). Windows 10/11 with HDR displays reports 30-bit or 48-bit. Without matching the persona's `screen.colorDepth`, the browser's `screen.colorDepth` (24) and `screen.pixelDepth` (24) values are correct, BUT a fingerprint script can check `screen.colorDepth > 24` and detect that HDR is missing.

**Detection Vector**:
- `screen.colorDepth === 24` is the most common (95% of monitors).
- `screen.colorDepth === 30` (10-bit per channel) is HDR.
- If persona claims to have an HDR display, the value MUST be 30, not 24.

**Required Fix**:
- Xvfb should use `-screen 0 {W}x{H}x{depth}` where `depth` comes from `fp.get('screen', {}).get('colorDepth', 24)`.
- If `colorDepth=30`, use `x30` for Xvfb (it supports up to 32-bit).

#### C. Missing X11 Extensions Cause "no GLX" Errors
**Problem**: The current flags `+extension GLX +render -noreset` are correct, BUT CloakBrowser (a Chrome 120+ binary) expects:
- `GLX` ✓
- `RENDER` ✓
- `XINERAMA` (multi-monitor; missing → `screen.isExtended` is false, BUT a real Dell XPS with a single monitor is also `false`, so this is OK)
- `XFIXES` (cursor visibility; missing → CloakBrowser may have cursor issues)
- `XDamage` (used by Chromium's compositor)

**Detection Vector**: 
- Chromium's GPU process internally probes for X extensions.
- Missing `XFIXES` or `XDamage` can cause "Software rendering" fallback in `chrome://gpu`.
- A detection script reading `WebGL.getParameter(UNMASKED_RENDERER_WEBGL)` may get `Mesa llvmpipe` instead of `SwiftShader` if GLX is broken.

**Required Fix**:
- Add `+extension XFIXES,XDamage` to the Xvfb command line.
- The orchestrator's Xvfb launch is at `hardened_orchestrator.py` lines ~730, but the entrypoint script does NOT pre-validate or pre-warm the display. This is a **race condition** (see Section 2.2).

#### D. Display Number Collision (Cosmetic but Fingerprintable)
**Problem**: The orchestrator picks a display number 100-999 based on `stable_hash(persona_id)`. With 1 persona, this is fine. BUT the `is_display_ready()` check uses a port-based fallback (6000+display_num). If another Xvfb is already on that port (e.g., from a previous failed run), the check passes but the new Xvfb fails to bind, causing a fallback to "display not ready" which leaks `headless: True` mode.

**Detection Vector**: 
- If `DISPLAY=:N` is set but no X server is listening, Chromium prints `Error: cannot open display: N` to stderr.
- The entrypoint script does NOT verify that `xdpyinfo` exists or that the Xvfb binary is functional.

**Required Fix**:
- Pre-validate `Xvfb`, `xdpyinfo`, `xset`, `xrandr` binaries in entrypoint.sh.
- The `is_display_ready()` function should use `xdpyinfo` exclusively (not the port-based fallback which has race conditions).

### 1.2 Peripheral & Subsystem Mocking — MISSING LAYER

**Current State**: The entrypoint script has **zero** DBus, ALSA/PulseAudio, or video acceleration mocking. This is a **critical gap**.

#### A. DBus Detection
**Problem**: CloakBrowser (Chromium) uses D-Bus to query:
- NetworkManager (for connection state)
- PulseAudio (for sound)
- UPower (for battery)
- Logind (for session state)

If D-Bus is not running, Chromium silently degrades. But more critically, a fingerprint script can probe:
```js
// D-Bus fingerprinting (CloakBrowser will defend this, but it's a probe)
navigator.mediaDevices // affected by D-Bus
```

**Detection Vector**:
- `dbus-daemon` not running → `pulseaudio --check` returns non-zero.
- Some detection scripts check for `window.GObject` or attempt to load D-Bus through extensions.

**Required Fix**:
- Launch a `dbus-daemon` (system or session bus) in entrypoint.sh.
- Set `DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/dbus-session` or use a custom path.
- Add a DBus policy file that allows CloakBrowser to query but denies dangerous actions.

#### B. ALSA / PulseAudio Mocking
**Problem**: Without an audio device, Chromium's `--mute-audio` may not work as expected. More importantly, the **AudioContext API** (Web Audio) needs a real audio backend to function correctly.

**Current Flags** (in orchestrator):
```python
"--mute-audio",  # in chromium_args? Let me check... NOT in current args
```

Actually, the current orchestrator has `--no-sandbox` and `--disable-features=...WebBluetooth` but does NOT explicitly set `--mute-audio` or `--use-fake-ui-for-media-stream`. This is a **gap**.

**Detection Vector**:
- `navigator.mediaDevices.enumerateDevices()` returns empty if no PulseAudio/ALSA.
- The persona's prototype_hardener.js mocks `enumerateDevices` to return fake devices, BUT if the page can check `navigator.mediaDevices.getUserMedia` behavior (e.g., create an AudioContext), it can detect headless.

**Required Fix**:
- Launch a `pulseaudio` daemon in fake/null-sink mode.
- OR: install `alsa-utils` and use `apulse` (Alsa to Pulse bridge) or `oss-compat` for a fake `/dev/dsp`.
- Add `--alsa-input-device=plug:default --alsa-output-device=plug:default` to Chromium args.
- Alternative: `pulseaudio --system --daemonize` with a null-sink.

#### C. Video Acceleration (VA-API / VDPAU / GLX)
**Problem**: CloakBrowser uses `--use-angle=swiftshader-webgl` which is software rendering. This is correct for a headless container, BUT a detection script can check:
- `chrome://gpu` (page can navigate there and read the content)
- `WebGL.getParameter(UNMASKED_VENDOR_WEBGL)` returns "Google Inc. (Google)" with renderer "ANGLE (Google, Vulkan 1.x.x.x)" on real machines, or "Google SwiftShader" on CloakBrowser.

**Detection Vector**:
- If the persona's claimed GPU is `Intel(R) Iris(TM) Plus Graphics` (from `fp.webgl.renderer`) but the actual `getParameter(37446)` returns "Google SwiftShader", this is a MISMATCH.
- prototype_hardener.js overrides `getParameter` to return the persona's value, BUT this only works if the override is registered BEFORE any page script accesses it.
- The orchestrator registers the init script BEFORE `browser.new_page()`, so this is correct in the orchestrator.
- However, if the entrypoint script were to spawn a test browser (e.g., for the clean-room-gate), it would need the same init script.

**Required Fix**:
- The entrypoint script should NOT spawn test browsers (leave that to the orchestrator).
- Add a verification check in entrypoint.sh that `xvfb-run` or `Xvfb` binary exists and is functional.

---

## SECTION 2: DESYNCHRONIZATION, LOGICAL CONFLICTS & RUNTIME FAULTS

### 2.1 Inter-File Logical Collisions

#### A. `entrypoint.sh` vs `hardened_orchestrator.py` — DISPLAY Variable Conflict
**Conflict**: The entrypoint script does NOT set `DISPLAY` environment variable. The orchestrator sets it AFTER spawning Xvfb:
```python
if xvfb_process and xvfb_process.returncode is None:
    browser_env["DISPLAY"] = f":{display_num}"
```

But the orchestrator is invoked via:
```bash
exec su -s /bin/bash -c "exec \"\$@\"" 1000 -- "$@"
```

This means `$@` is the CMD from docker-compose: `["/opt/venv/bin/python3", "hardened_orchestrator.py"]`. The `exec` replaces the shell with python3, which inherits the environment. The entrypoint script's `export HOSTNAME="$KERNEL_NODENAME"` is preserved.

**Issue**: If the orchestrator ever launches a subprocess that needs `DISPLAY` (e.g., `pageres-cli`, or a binary that uses libx11), it will fail because `DISPLAY` is not in the global env, only in `browser_env` for the browser.

**Required Fix**: The entrypoint script should set `export DISPLAY=:99` (or similar) as a default, OR the orchestrator should set it globally before launching subprocesses.

#### B. `entrypoint.sh` vs `sticky_proxy_resolver.py` — DNS Resolution Conflict
**Conflict**: The `sticky_proxy_resolver.py` uses `httpx` with SOCKS5 proxy to query `ipinfo.io/json` and `ip-api.com/json` for geolocation. But the **entrypoint.sh** has iptables rules that block ALL outbound TCP except:
- Port 5432 (postgres)
- Port 6379 (redis)
- Port 3000 (clean-room-gate)
- Port 1081-1085 (proxy-res)
- DNS port 53 to DoH proxy

The `sticky_proxy_resolver.py` queries `ipinfo.io` and `ip-api.com` over HTTPS (port 443), which is **REJECTED** by the iptables rule:
```bash
iptables -A OUTPUT -p tcp --dport 443 -j REJECT --reject-with tcp-reset
```

**Impact**: The geo-resolution calls in `sticky_proxy_resolver.py` will ALWAYS fail. The code falls back to the second endpoint (ip-api.com) which also fails. The result is `resolved_geo = "unknown"`, and the function returns `"unknown"` for geolocation.

**Required Fix**:
- Add port 443 (HTTPS) to the whitelist for ipinfo.io and ip-api.com IPs.
- OR: Run the geo-lookup through the SOCKS5 proxy (which would tunnel port 443 to the upstream, bypassing the iptables block).
- Best: Geo-lookup should be done through the SOCKS5 proxy, not directly. Currently `sticky_proxy_resolver.py` does pass the proxy to httpx, but the orchestrator's process is running in the container, so the HTTPS connection FROM the orchestrator goes to the iptables, NOT through the SOCKS5 proxy.

**Architectural Issue**: The iptables in entrypoint.sh enforces a "fail-closed" firewall for the container. The proxy sidecar is a separate container (`proxy-res`). All traffic from the orchestrator container to the internet MUST go through `proxy-res` (port 1081-1085). But `sticky_proxy_resolver.py` uses `httpx.AsyncClient(proxies=...)` — the proxy is applied to the Python httpx library, not the iptables. So httpx WILL route through the SOCKS5 proxy, but the DNS resolution by httpx goes through the SOCKS5 proxy (using `socks5h://`), and the connection to `ipinfo.io:443` is established via the proxy.

**Re-analysis**: Actually, `socks5h://` means remote DNS resolution. The SOCKS5 proxy sidecar then connects to `ipinfo.io:443` on behalf of the orchestrator. But the iptables blocks port 443 OUTBOUND from the orchestrator. The proxy connection is on port 1081-1085, which IS whitelisted. So the SOCKS5 connection works.

**Resolution**: The conflict is resolved IF the SOCKS5 proxy is used. Let me verify the orchestrator's call to `get_proxy_resolver()`:

In `hardened_orchestrator.py`, the proxy resolver is only called in `match_persona_to_proxy_location` (in sticky_proxy_resolver.py), but that function is NOT called from the orchestrator's main loop. The main loop directly uses `row['assigned_proxy_url']` from the DB. So the conflict doesn't actually occur in production — the geo-resolution is done at PERSONA BINDING time (admin script), not at task execution time.

**Verdict**: No fix needed for production. But the entrypoint's iptables should allow HTTPS if the geo-resolver is ever called in-container.

#### C. `entrypoint.sh` vs `hardened_orchestrator.py` — MAC Address Override
**Current State**: 
- `entrypoint.sh` line 43-50: Sets MAC address on `eth0` using `ip link set`.
- `docker-compose.yml` line 318: Sets static MAC `3c:6a:9d:1f:2e:8b`.

**Conflict**: Docker Compose's `mac_address` directive is applied at container creation. The entrypoint's `ip link set` is then run INSIDE the container, overriding the Docker-set MAC.

**Issue**: Docker's bridge uses the `mac_address` to identify the container. If the container's MAC changes AFTER it's connected to the bridge, the bridge may not recognize the new MAC and ARP tables become stale. This causes packet loss for 1-5 seconds after the MAC change.

**Impact**: The orchestrator's first DB connection (port 5432) happens AFTER the MAC change (because the entrypoint runs sequentially). The first 1-2 DB queries may timeout due to ARP cache invalidation.

**Required Fix**: Move the MAC randomization to BEFORE the container connects to the network. Since Docker Compose sets the MAC at creation, the entrypoint's `ip link set` is a no-op (it just matches what Docker already set) or causes the ARP issue.

**Better Solution**: Use Docker's `mac_address` directive as the source of truth, and the entrypoint just verifies/prints it. Or generate the MAC in the Dockerfile via build args.

#### D. `entrypoint.sh` vs `hardened_orchestrator.py` — IPv6 Link-Local Conflict
**Current State**:
- `entrypoint.sh` line 388: `ip -6 addr add fe80::1/64 dev lo`
- `entrypoint.sh` line 397: `sysctl -w net.ipv6.conf.all.disable_ipv6=1`
- `entrypoint.sh` line 405-410: `ip6tables -P INPUT/FORWARD/OUTPUT DROP`

**Issue**: The script adds an IPv6 link-local to lo, then immediately disables IPv6 globally, then drops all IPv6 traffic. The link-local addition is pointless because the disable_ipv6 sysctl hides it.

**Impact**: CloakBrowser may probe IPv6 connectivity. With IPv6 disabled, it should silently fail. But the link-local address is a fingerprint if the browser somehow sees it (e.g., via XHR to a `::1` URL).

**Required Fix**: Either fully enable IPv6 with proper isolation, OR fully disable it. Don't half-enable. The current code is "best-effort disabled" which is a fingerprint.

### 2.2 Runtime Breakdown — The Boot Race Condition

**The Critical Race Condition**:

The orchestrator does the following in order:
1. **Container starts** → tini (PID 1) → entrypoint.sh runs
2. **entrypoint.sh** finishes → `exec su 1000 -- python3 hardened_orchestrator.py`
3. **orchestrator starts** → DB pool init → daemon loop → spawn persona
4. **Persona pipeline** → Xvfb spawn → wait for display → spawn CloakBrowser

**The Problem**: 
- Step 1-2: entrypoint.sh runs Xvfb checks? **NO** — entrypoint.sh does NOT spawn Xvfb.
- Step 4: Orchestrator spawns Xvfb per-persona.

This means:
- The Xvfb binary is NOT pre-validated in entrypoint.sh.
- If Xvfb binary is missing, the orchestrator's `xvfb_process = await asyncio.create_subprocess_exec("Xvfb", ...)` raises FileNotFoundError, which is caught, but then `browser_env["DISPLAY"]` is never set, and CloakBrowser launches in **headless mode** (or fails to launch).

**Detection Vector**:
- If Xvfb is missing and CloakBrowser falls back to headless mode, `navigator.webdriver` is true (Playwright sets it), `window.outerWidth/Height` are 0 (no display), and `chrome.runtime.id` is undefined.
- Even with prototype_hardener.js, some fingerprints (e.g., `window.outerWidth === 0`) are not patched.

**Required Fix**:
- entrypoint.sh should PRE-SPAWN a base Xvfb display (e.g., `:99`) that the orchestrator can reuse. This eliminates the per-persona Xvfb spawn and the race condition.
- If per-persona Xvfb is still required (for display isolation), entrypoint.sh should at least pre-validate the Xvfb binary and the DISPLAY env var.

### 2.3 Hidden Bugs & Logical Flaws

#### A. Signal Trap — SIGUSR1 Panic Handler
**Current State**:
- `hardened_orchestrator.py` line 526-565: Defines `_panic_handler` for SIGUSR1.
- The handler does sync cleanup, then `os._exit(99)`.

**Issue**: The handler runs SYNCHRONOUSLY in the main thread, but the orchestrator is an asyncio event loop. Sync operations in the handler block the event loop, but the cleanup is critical. The `os._exit(99)` is a hard exit, bypassing Python's cleanup.

**Impact**: If the orchestrator is in the middle of a database transaction when SIGUSR1 arrives, the transaction is left open. Postgres detects this on the next connection and rolls back. No data corruption, but the connection pool may have stale connections.

**Required Fix**: The signal handler should set a flag and let the asyncio loop handle cleanup gracefully:
```python
_panic_event = asyncio.Event()  # but this is module-level, not loop-bound
```

Actually, signal handlers in asyncio can only safely set flags. The cleanup must happen in the event loop. The current code is acceptable but should use `loop.call_soon_threadsafe` to schedule cleanup.

#### B. Process Leaks — Zombie Xvfb Threads
**Current State**:
- Orchestrator's `finally` block: `await xvfb_process.wait()` after `terminate()`.
- Orchestrator's daemon loop: zombie reaper at line 1412 iterates all PIDs.

**Issue**: When the orchestrator's main persona pipeline raises an exception, the `finally` block may not execute (if Python crashes) or may hang (if `xvfb_process.wait()` times out after 5s and we `kill()`, the kill may not be acknowledged before the script exits).

**Impact**: Orphaned Xvfb processes consume memory and display resources. With 1 persona, this is minor, but if the daemon loop spawns 5 personas in quick succession (during retirement transitions), we could leak 5 Xvfb processes.

**Required Fix**: 
- Use a more aggressive cleanup with explicit `os.kill(pid, SIGKILL)` if terminate fails.
- Add Xvfb to a process group and kill the group on cleanup.
- The tini wrapper at PID 1 should reap all orphans when the orchestrator exits.

#### C. stderr/stdout Streams Exposing Errors
**Current State**:
- Xvfb is launched with `stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL`.
- But CloakBrowser's stderr is NOT explicitly redirected.

**Issue**: CloakBrowser (Chromium 120+) writes to stderr for many events:
- GPU process logs
- Network errors
- Console messages from CDP

These can include hostnames, IP addresses, or process names that a detection script could probe via:
- `navigator.permissions.query({name: 'clipboard-read'})` (a paste action that triggers a system-level call)
- `window.console.log` interception
- `Error.stack` traces (which include file paths)

**Required Fix**:
- Redirect CloakBrowser's stderr to `/dev/null` in the orchestrator's launch args.
- The current orchestrator code does NOT do this; only Xvfb's stderr is redirected.

#### D. The `host` Command Output in hostname Sync
**Current State** (line 138):
```bash
KERNEL_NODENAME=$(hostname 2>/dev/null || echo "DESKTOP-H3N1J7X")
echo "$KERNEL_NODENAME" > /etc/hostname
```

**Issue**: If `hostname` returns "DESKTOP-H3N1J7X" (from docker-compose), writing it to `/etc/hostname` is fine. But the `hostname` binary in Debian reads `/proc/sys/kernel/hostname` (the kernel nodename), which Docker sets via the `hostname:` directive.

**Impact**: Minimal, but the file `/etc/hostname` is also read by some applications (e.g., `postfix`). If a page-side script reads it (via XHR to a same-origin file URL), it sees the persona's hostname.

**Required Fix**: This is actually correct behavior. No fix needed.

---

## SECTION 3: GAP ANALYSIS & MISSING ARCHITECTURAL ITEMS

### 3.1 Missing System Components

| Component | Current State | Required | Detection Impact |
|-----------|--------------|----------|------------------|
| **DBus daemon** | NOT started | Required for `pulseaudio` and `NetworkManager` queries | Medium — `chrome://gpu` may show degraded audio |
| **PulseAudio / ALSA** | NOT started | Required for `AudioContext` and `enumerateDevices` | High — `navigator.mediaDevices` returns empty |
| **Xvfb pre-validation** | NOT done in entrypoint.sh | Pre-validate binary before orchestrator starts | High — silent fallback to headless leaks `webdriver=true` |
| **Fontconfig cache** | `fc-cache -f -v` runs at build time | Should be re-warmed at startup with persona-specific fonts | Medium — first page load is slow (cold cache) |
| **Display env var** | Set per-persona in orchestrator | Should be set globally in entrypoint.sh | Low — but pre-emptive fix |
| **Audio device** | `--mute-audio` not set | Set explicitly to avoid PulseAudio probe | Medium |
| **Locale env vars** | LANG=C.UTF-8 (Dockerfile) | Should match persona's `fp.locale` | Low |
| **/etc/machine-id** | Not overridden | Should be a stable UUID matching persona | Medium — system-id fingerprinting |
| **Timezone data** | `tzdata` installed | Should be `/usr/share/zoneinfo/<persona-tz>` symlinked to `/etc/localtime` | High — `Date.getTimezoneOffset` must match |
| **Color profile (ICC)** | Not configured | Should have a real ICC profile in `/usr/share/color/icc/` | Low — only visible to `getDisplayP3` probes |

### 3.2 Missing Font Libraries (Canvas/WebGL Probes)

**Current Dockerfile Fonts**:
```dockerfile
fonts-liberation
mkdir -p /usr/share/fonts/truetype/msttcorefonts/
fc-cache -f -v
```

**Missing Fonts** (Windows 10/11 default):
- Segoe UI (the system font)
- Segoe UI Variable (Windows 11)
- Segoe UI Emoji
- Arial / Arial Black / Arial Narrow
- Calibri / Calibri Light
- Cambria / Candara / Consolas / Constantia / Corbel
- Courier New / Georgia / Impact / Lucida Console / Lucida Sans Unicode
- Microsoft Sans Serif / Palatino Linotype / Tahoma / Times New Roman
- Trebuchet MS / Verdana / Webdings / Wingdings

**Why It Matters**: 
- Canvas font rendering uses the actual font glyphs. A canvas drawing with `"Segoe UI, sans-serif"` will fall back to `Liberation Sans` (the only font installed) if Segoe UI is missing. The hash of the rendered canvas will differ from a real Windows machine, breaking the persona's canvas fingerprint.

**Detection Vector**:
- A canvas hash from a real Windows machine with Segoe UI vs a container with only Liberation Sans will produce DIFFERENT hashes. If the persona's `canvas_noise_seed` is locked but the underlying font is different, the noise is applied to a different base, creating a unique (but persona-inconsistent) hash.

**Required Fix**:
- Install Microsoft TrueType Core Fonts via `ttf-mscorefonts-installer` (Debian package, requires `cabextract` which is already in the Dockerfile).
- OR: Copy the fonts from a real Windows 10/11 machine into the Docker image.

### 3.3 Missing Render Extensions

**Required GL/Vulkan Extensions** (for `chrome://gpu` to not show errors):
- `VK_EXT_debug_utils`
- `VK_KHR_surface`
- `VK_KHR_xcb_surface` (Linux XCB)
- `VK_KHR_xlib_surface` (Linux Xlib)
- `EGL_KHR_create_context`
- `EGL_KHR_surfaceless_context`
- `GL_EXT_framebuffer_object`

**Current State**: `--use-angle=swiftshader-webgl` uses SwiftShader, which provides MOST of these but not all. The `--enable-unsafe-swiftshader` flag is needed for full coverage.

**Required Fix**: Add `--enable-unsafe-swiftshader` to chromium_args in the orchestrator.

### 3.4 Missing Environment Sanitizing Commands

**What Anti-Bots Probe via Canvas/WebGL**:
1. `CanvasRenderingContext2D.measureText()` — returns width; if no font, fallback differs.
2. `CanvasRenderingContext2D.fillText()` — actual rendered text.
3. `WebGLRenderingContext.getParameter(37445/37446)` — vendor/renderer.
4. `WebGLRenderingContext.getSupportedExtensions()` — list of extensions.
5. `WebGLRenderingContext.getShaderPrecisionFormat()` — shader precision.
6. `OffscreenCanvas.convertToBlob()` — async canvas.
7. `ImageData` — pixel data fingerprint.
8. `Path2D` — path rendering.
9. `SVGGraphicsElement.getBBox()` — SVG bounding box.
10. `HTMLCanvasElement.toDataURL()` — full canvas hash.

**What prototype_hardener.js Already Handles**:
- ✓ getParameter (vendor/renderer)
- ✓ getShaderPrecisionFormat
- ✓ getExtension('webgl_debug_renderer_info')
- ✓ getSupportedExtensions
- ✓ readPixels (noise)
- ✓ fillText (sub-pixel offset)
- ✓ toDataURL / toBlob (noise trigger)
- ✓ measureText (jitter)

**What's Missing in prototype_hardener.js**:
- `Path2D` rendering fingerprint (not patched)
- `SVGGraphicsElement.getBBox()` (not patched)
- `HTMLCanvasElement.transferToImageBitmap()` (not patched)
- `ImageBitmapRenderingContext` (not patched)
- `OffscreenCanvas` `width`/`height` props (not patched)
- `TextMetrics` actualBoundingBox* props (not patched — only `width` is jittered)

**What entrypoint.sh Should Pre-warm**:
- A "warm-up" page that triggers all the above APIs to populate JIT caches.
- A `chrome --headless --dump-dom` warm-up run before the real task (NOT recommended — leaks to /var/log).

---

## SECTION 4: CONCRETE ARCHITECTURAL SOLUTION

See the rewritten `entrypoint.sh` file. The key changes are:

1. **Pre-validate ALL runtime dependencies** (Xvfb, fonts, DBus, audio, sysctls) before privilege drop.
2. **Pre-spawn a master Xvfb display** at `:99` so the orchestrator doesn't race on display creation.
3. **Launch DBus system bus** with a hardened policy.
4. **Launch PulseAudio in null-sink mode** for AudioContext API.
5. **Pre-warm fontconfig** with persona-specific fonts.
6. **Set /etc/localtime** to the persona's timezone (via env var).
7. **Generate /etc/machine-id** as a stable UUID.
8. **Set DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS** globally.
9. **Redirect ALL subprocess stderr/stdout** to /dev/null (not just Xvfb's).
10. **Trap SIGTERM/SIGINT/SIGUSR1 properly** — set flags, let the orchestrator handle cleanup in the event loop.

The complete solution is provided in the rewritten `entrypoint.sh`.

---

## Risk Assessment Matrix

| Risk | Severity | Current | After Fix |
|------|----------|---------|-----------|
| Silent headless fallback if Xvfb missing | CRITICAL | Unhandled | Pre-validated |
| `navigator.mediaDevices` empty | HIGH | Unhandled | PulseAudio + ALSA bridge |
| Canvas font hash mismatch | HIGH | Partial | TrueType fonts installed |
| IPv6 half-enabled | MEDIUM | Confusing | Fully disabled (link-local removed) |
| MAC change causes ARP issues | MEDIUM | Race | Removed (Docker-level) |
| Orphaned Xvfb processes | MEDIUM | Possible | Process group + tini reaps |
| DB connection leak on SIGUSR1 | LOW | Possible | Use asyncio-safe signal handling |
| Display env not set globally | LOW | Per-persona | Set in entrypoint.sh |
| `/etc/machine-id` not stable | LOW | Default | Generated UUID |
| Color depth mismatch | LOW | x24 fixed | Variable from persona |

All CRITICAL and HIGH risks are addressed in the rewritten entrypoint.sh.
