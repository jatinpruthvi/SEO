# Third-Pass Deep-Dive Audit Findings
# =====================================

**Date**: 2026-07-09
**Scope**: Exhaustive verification of all fixes across the entire project for the `entrypoint.sh` deep-dive.

This document lists **8 additional issues** found in the third pass that previous audits missed.

---

## Issue #33: CRITICAL — DBUS_SESSION_BUS_ADDRESS Conflict (docker-compose overrides entrypoint)

**Severity**: CRITICAL
**Files**: `docker-compose.yml` line ~481, `browser-runtime/entrypoint.sh` line 47

**The Problem**:
- `docker-compose.yml` sets `DBUS_SESSION_BUS_ADDRESS=/dev/null` (line 481)
- `browser-runtime/entrypoint.sh` line 47 has `export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/tmp/dbus-session-bus}"`
- Docker compose environment variables are passed to the container BEFORE the entrypoint runs
- The entrypoint's `${DBUS_SESSION_BUS_ADDRESS:-default}` syntax means: "use existing value if set, otherwise use default"
- Since docker-compose sets it to `/dev/null`, the entrypoint's default is NEVER used
- The entrypoint starts `dbus-daemon` listening on `unix:path=/tmp/dbus-session-bus` (line 533)
- But the ChromeBrowser reads `DBUS_SESSION_BUS_ADDRESS=/dev/null` from the environment
- Result: **DBus daemon is started but ChromeBrowser cannot connect to it**

**Detection Vector**:
- ChromeBrowser logs "Failed to connect to D-Bus" to stderr
- AudioContext, NetworkManager, and UPower queries fail silently
- The `/dev/null` path is a "no D-Bus" signal that some anti-bot scripts detect

**Fix Required**:
1. **Option A** (recommended): Remove `DBUS_SESSION_BUS_ADDRESS=/dev/null` from `docker-compose.yml`. Let the entrypoint manage it.
2. **Option B**: Change the entrypoint to ALWAYS set the value (override the env var): `export DBUS_SESSION_BUS_ADDRESS="unix:path=/tmp/dbus-session-bus"` (no default expansion).

**Recommended**: Option A — remove the line from docker-compose.

---

## Issue #34: CRITICAL — DISPLAY env var not set in browser_env when using master Xvfb

**Severity**: CRITICAL
**File**: `browser-runtime/hardened_orchestrator.py` line 832

**The Problem**:
- The orchestrator's code at line 832 is:
  ```python
  if xvfb_process and xvfb_process.returncode is None:
      browser_env["DISPLAY"] = f":{display_num}"
  ```
- When using the entrypoint's pre-spawned master Xvfb (the new v6.0.2 path), `xvfb_process` is `None`
- This means `DISPLAY` is **never set** in `browser_env` when using the master Xvfb
- ChromeBrowser will fail to find the X display

**Detection Vector**:
- ChromeBrowser logs "cannot open display" to stderr
- Falls back to headless mode
- `navigator.webdriver` becomes `true`
- Complete invisibility failure

**Fix Required**:
Change line 832 to:
```python
if xvfb_process and xvfb_process.returncode is None:
    browser_env["DISPLAY"] = f":{display_num}"
else:
    # v6.0.2: Use entrypoint's pre-spawned master Xvfb
    master_display = os.getenv("MASTER_XVFB_DISPLAY", "")
    if master_display:
        browser_env["DISPLAY"] = master_display
    else:
        # No display available; this will fail
        browser_env["DISPLAY"] = f":{display_num}"  # fallback
```

---

## Issue #35: CRITICAL — Dockerfile missing dbus, pulseaudio, xrandr, xprop, xset, xdpyinfo

**Severity**: CRITICAL
**File**: `browser-runtime/Dockerfile`

**The Problem**:
- The entrypoint.sh uses `dbus-daemon`, `pulseaudio`, `xrandr`, `xprop`, `xset`, `xdpyinfo` extensively
- The Dockerfile only installs `xvfb` (not the related X11 utilities)
- The entrypoint has an `apt-get install` fallback (line 119) that will install these at startup
- BUT: this adds 30-60 seconds to container startup time
- AND: in some OCI instances, apt-get may be disabled or offline
- AND: `set -e` + `apt-get install` failure will exit the script (actually `|| warn` handles it, but warns are not blocking)

**Detection Vector**:
- Slower container startup (detectable via timing)
- `apt-get install` in entrypoint may fail silently
- Some X11 utilities (xrandr, xprop) may be missing, causing taskbar simulation to fail

**Fix Required**:
Add to Dockerfile's apt-get install list:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    # ... existing packages ...
    dbus \
    pulseaudio-utils \
    alsa-utils \
    libasound2 \
    x11-xserver-utils \
    x11-utils \
    # ... rest of packages ...
    fonts-noto-core \
    fonts-dejavu-core \
    fonts-dejavu-extra \
    ttf-mscorefonts-installer \
    libpulse0 \
    && rm -rf /var/lib/apt/lists/*
```

---

## Issue #36: CRITICAL — DBus daemon started with wrong path due to docker-compose override

**Severity**: CRITICAL
**File**: `browser-runtime/entrypoint.sh` line 533

**The Problem**:
- The entrypoint starts dbus-daemon with `--address="$DBUS_SESSION_BUS_ADDRESS"` (line 533)
- Since docker-compose sets `DBUS_SESSION_BUS_ADDRESS=/dev/null`, the daemon is started with `--address=/dev/null`
- This is INVALID; dbus-daemon will fail to start (or worse, start with unexpected behavior)
- The `DBUS_READY` test (line 544) will fail
- But the script continues (it's a `warn`, not a `die`)

**Detection Vector**:
- dbus-daemon fails to start, leaves a stale PID
- No D-Bus available to ChromeBrowser
- AudioContext and other DBus-dependent APIs fail

**Fix Required**:
This is fixed by Issue #33 (remove the docker-compose override). Alternatively, the entrypoint can force the value:
```bash
# v6.0.3: Always set DBus address, override any external setting
export DBUS_SESSION_BUS_ADDRESS="unix:path=/tmp/dbus-session-bus"
```

---

## Issue #37: HIGH — TZ env var conflict (compose sets UTC, entrypoint sets PERSONA_TZ)

**Severity**: HIGH
**Files**: `docker-compose.yml` (multiple `TZ: UTC`), `browser-runtime/entrypoint.sh` line 189

**The Problem**:
- `docker-compose.yml` sets `TZ: UTC` for multiple services (postgres, redis, etc.)
- For the orchestrator service, `TZ` is NOT explicitly set in compose (no `TZ:` line in orchestrator env)
- The entrypoint sets `export TZ="$PERSONA_TZ"` (line 189)
- This should work because the orchestrator service in compose doesn't override TZ
- BUT: if the orchestrator service's `environment` block doesn't include TZ, and the `x-stealth-sysctls` or other anchors set it, there's a conflict

**Detection Vector**:
- `Date.getTimezoneOffset()` may not match the persona's claimed timezone
- `Intl.DateTimeFormat().resolvedOptions().timeZone` may return UTC instead of the persona's TZ

**Fix Required**:
Add explicit `TZ` to the orchestrator's environment in docker-compose.yml:
```yaml
orchestrator:
    environment:
      - TZ=${PERSONA_TZ:-America/New_York}
```

Or set `PERSONA_TZ` env var in compose and have the entrypoint use it.

---

## Issue #38: HIGH — orchestrator's display_num fallback when using master Xvfb

**Severity**: HIGH
**File**: `browser-runtime/hardened_orchestrator.py` lines 763-810

**The Problem**:
- When the entrypoint's master Xvfb is ready, the orchestrator uses it (good)
- But if the orchestrator falls back to per-persona Xvfb (line 779+), it spawns a NEW Xvfb
- The new Xvfb will conflict with the master Xvfb for resources (display port, memory)
- The `display_num` calculation `100 + (stable_hash(persona_id) % 900)` can collide with the master's `:99`

**Detection Vector**:
- Two Xvfb processes running
- Memory waste (~50MB)
- Potential port collision

**Fix Required**:
The orchestrator's per-persona Xvfb fallback should use display numbers that avoid the master:
```python
# v6.0.3: Use display numbers 100-999, but skip the master's display
master_display = os.getenv("MASTER_XVFB_DISPLAY", ":99")
master_num = int(master_display.lstrip(":"))
display_num = 100 + (stable_hash(persona_id) % 900)
while display_num == master_num:  # avoid collision
    display_num = 100 + (stable_hash(f"{persona_id}_{random.random()}") % 900)
```

---

## Issue #39: MEDIUM — `set -e` + `mount` could fail silently with pipefail

**Severity**: MEDIUM
**File**: `browser-runtime/entrypoint.sh` line 31

**The Problem**:
- `set -eo pipefail` is set at line 31
- The `mount` commands at lines 337-342 are wrapped in `|| true` (so they won't trigger `set -e`)
- BUT: if a `mount` command's output is piped to another command that fails, `set -o pipefail` will trigger
- Example: `mount --bind X Y 2>/dev/null | grep "success"` would fail if grep finds nothing

**Detection Vector**:
- Container may exit unexpectedly during mount operations
- Emergency mock files may not be rebound

**Fix Required**:
None currently (the commands use `|| true`). But future modifications should be careful.

---

## Issue #40: LOW — `apt-get install` in set -e context could fail mid-way

**Severity**: LOW
**File**: `browser-runtime/entrypoint.sh` line 119

**The Problem**:
- `apt-get install -y --no-install-recommends ... 2>/dev/null || warn "..."` is wrapped in `|| warn`
- If apt-get install fails mid-way (network error, partial download), some packages may be installed and others not
- The script continues, but the missing packages may cause later failures
- The `warn` is logged but the script does not `die`

**Detection Vector**:
- Some X11 utilities may be missing at runtime
- `command -v xrandr` may return non-zero
- Taskbar simulation may fail silently

**Fix Required**:
After `apt-get install`, re-check that all OPTIONAL_BINS are present:
```bash
# v6.0.3: Re-check optional binaries after apt-get install
for bin in "${OPTIONAL_BINS[@]}"; do
    if ! command -v "$bin" &>/dev/null; then
        warn "Optional binary still missing after install: $bin"
    fi
done
```

---

## Summary of 3rd-Pass Findings (Issues #33-#40)

| # | Severity | File | Issue | Status |
|---|----------|------|-------|--------|
| 33 | CRITICAL | docker-compose.yml | DBUS_SESSION_BUS_ADDRESS=/dev/null overrides entrypoint | NEEDS FIX |
| 34 | CRITICAL | hardened_orchestrator.py | DISPLAY not set when using master Xvfb | NEEDS FIX |
| 35 | CRITICAL | Dockerfile | Missing dbus, pulseaudio, x11-utils, fonts | NEEDS FIX |
| 36 | CRITICAL | entrypoint.sh | DBus daemon started with wrong path due to #33 | FIXED BY #33 |
| 37 | HIGH | docker-compose.yml | TZ env var may be UTC instead of persona TZ | NEEDS FIX |
| 38 | HIGH | hardened_orchestrator.py | Per-persona Xvfb fallback collides with master | NEEDS FIX |
| 39 | MEDIUM | entrypoint.sh | set -e + mount + pipefail could fail silently | MONITORING |
| 40 | LOW | entrypoint.sh | apt-get install may fail mid-way | NEEDS FIX |

**Critical issues that block the entrypoint from working correctly**: #33, #34, #35
**High issues that affect stealth**: #37, #38
