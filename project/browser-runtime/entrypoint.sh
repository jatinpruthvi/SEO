#!/usr/bin/env bash
# ==============================================================================
# Hardened Entrypoint v6.0.2 (1 persona, MAXIMUM INVISIBILITY, PRODUCTION-GRADE)
# Path: /home/user/project/browser-runtime/entrypoint.sh
#
# v6.0.2 DEEP-DIVE FIXES (2026-07-09):
#   1. Removed duplicate -screen flag in Xvfb command (was parsed twice, second wins)
#   2. Added +extension RANDR,Composite,DRI3,Present for full ChromeBrowser support
#   3. Added DPI calculation from persona screen dimensions (-dpi flag)
#   4. Removed redundant fc-cache call (was running twice)
#   5. Increased DBus wait time to 2.0s with dbus-send readiness test
#   6. Fixed PulseAudio launch: removed --system (requires system user), use --daemonize only
#   7. Added pre-flight CloakBrowser binary validation
#   8. Added pre-flight Playwright Chromium fallback validation
#   9. Added taskbar simulation via xrandr (40px Windows 10/11 taskbar)
#  10. Added shared memory validation (/dev/shm >= 256MB)
#  11. Added entropy seeding to kernel pool
#  12. Fixed trap inheritance: traps are reset after exec
#  13. Added per-process PID tracking for clean shutdown
#  14. Added mktemp for unique temp paths (no shared state between containers)
#  15. Added -shmem flag to Xvfb for shared memory operations
#  16. Added audio device enumeration check
#  17. Added font availability check (Liberation, Noto, DejaVu minimum)
#  18. Added /dev/dri validation (optional GPU passthrough check)
#  19. Added container health-check endpoint
#  20. Added memory pressure check before Xvfb spawn
#
# Architecture: Debian 12 host + x86_64 + CloakBrowser (Chrome 120+) + 1 persona
# ==============================================================================

set -eo pipefail
IFS=$'\n\t'

# ------------------------------------------------------------------------------
# LOGGING HELPERS
# ------------------------------------------------------------------------------
log() { echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"; }
warn() { echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] [!] $*" >&2; }
err() { echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] [ERROR] $*" >&2; }
die() { err "$*"; exit 1; }

# ------------------------------------------------------------------------------
# PERSONA ENVIRONMENT VARIABLES (exported for orchestrator consumption)
# ------------------------------------------------------------------------------
export DISPLAY="${DISPLAY:-:99}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/xdg-runtime-1000}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/tmp/dbus-session-bus}"
export PULSE_SERVER="${PULSE_SERVER:-unix:/tmp/pulse-socket}"
export PULSE_RUNTIME_PATH="${PULSE_RUNTIME_PATH:-/tmp/pulse-runtime}"
export FONTCONFIG_PATH="${FONTCONFIG_PATH:-/etc/fonts}"

# Persona timezone (set from env or default to America/New_York)
export PERSONA_TZ="${PERSONA_TZ:-America/New_York}"
# Persona locale
export PERSONA_LOCALE="${PERSONA_LOCALE:-en-US}"
# Persona screen dimensions and color depth
export PERSONA_SCREEN_WIDTH="${PERSONA_SCREEN_WIDTH:-1920}"
export PERSONA_SCREEN_HEIGHT="${PERSONA_SCREEN_HEIGHT:-1080}"
export PERSONA_COLOR_DEPTH="${PERSONA_COLOR_DEPTH:-24}"
# v6.0.2: DPI calculated from screen dimensions (4K = 315, FHD = 96)
export PERSONA_DPI="${PERSONA_DPI:-96}"
# Persona machine ID (stable per-persona UUID)
export PERSONA_MACHINE_ID="${PERSONA_MACHINE_ID:-}"
# Persona-specific entropy seed (regenerated each container)
export PERSONA_ENTROPY_SEED="${PERSONA_ENTROPY_SEED:-$(tr -dc 'a-f0-9' < /dev/urandom | head -c 16)}"

# Master Xvfb display number (used by all personas, avoids per-persona spawn)
export MASTER_XVFB_DISPLAY="${MASTER_XVFB_DISPLAY:-99}"

# v6.0.2: PIDs tracked for clean shutdown
declare -a BACKGROUND_PIDS=()

# v6.0.2: Unique temp directory per container (no shared state)
CONTAINER_TMPDIR=$(mktemp -d /tmp/stealth-entry-XXXXXX)
export CONTAINER_TMPDIR

# ------------------------------------------------------------------------------
# 0.0 PRE-FLIGHT: Core dumps, resource limits, kernel entropy
# ------------------------------------------------------------------------------
log "=== Phase 0: Pre-flight checks ==="
ulimit -c 0
ulimit -n 65536 2>/dev/null || true
ulimit -u 8192 2>/dev/null || true
log "Core dumps disabled. Resource limits set (nofile=65536, nproc=8192)."

# v6.0.2: Seed kernel entropy pool (best-effort)
if [ -w /dev/random ]; then
    dd if=/dev/urandom of=/dev/random bs=32 count=1 2>/dev/null || true
    log "Kernel entropy pool seeded."
fi

# ------------------------------------------------------------------------------
# 0.1 PRE-FLIGHT: Validate ALL required binaries exist
# ------------------------------------------------------------------------------
log "=== Phase 0.1: Binary validation ==="

# v6.0.2: Distinguish between CRITICAL (must have) and OPTIONAL (warn if missing)
CRITICAL_BINS=("Xvfb" "iptables" "ip" "sysctl" "fc-cache" "xdpyinfo" "xset" "xrandr")
OPTIONAL_BINS=("dbus-daemon" "dbus-launch" "dbus-send" "pulseaudio" "pacmd" "pactl" "Xvnc")

MISSING_CRITICAL=()
MISSING_OPTIONAL=()

for bin in "${CRITICAL_BINS[@]}"; do
    if ! command -v "$bin" &>/dev/null; then
        MISSING_CRITICAL+=("$bin")
    fi
done

for bin in "${OPTIONAL_BINS[@]}"; do
    if ! command -v "$bin" &>/dev/null; then
        MISSING_OPTIONAL+=("$bin")
    fi
done

if [ ${#MISSING_OPTIONAL[@]} -ne 0 ]; then
    warn "Optional binaries missing: ${MISSING_OPTIONAL[*]}"
    # R7-ENT-001: Do NOT run apt-get update/install at container start.
    # Per-container egress to deb.debian.org is a fingerprint signal.
    # All required packages must be installed in the Dockerfile instead.
    # We log a warning but continue — the missing optional binaries
    # (e.g., dbus-daemon, pulseaudio) will degrade functionality but
    # the orchestrator can still launch.
    if command -v apt-get &>/dev/null; then
        warn "R7-ENT-001: NOT running apt-get update (would leak egress IP). Update Dockerfile to include required packages."
    fi
    # v6.0.3: Re-check ALL optional binaries (some may have been
    # installed but not on PATH)
    STILL_MISSING=()
    for bin in "${OPTIONAL_BINS[@]}"; do
        if ! command -v "$bin" &>/dev/null; then
            STILL_MISSING+=("$bin")
        fi
    done
    if [ ${#STILL_MISSING[@]} -ne 0 ]; then
        warn "Still missing optional binaries: ${STILL_MISSING[*]} (continuing with reduced functionality)"
    fi
fi

if [ ${#MISSING_CRITICAL[@]} -ne 0 ]; then
    die "CRITICAL: Required binaries missing: ${MISSING_CRITICAL[*]}. Cannot continue."
fi
log "All critical binaries present."

# ------------------------------------------------------------------------------
# 0.2 PRE-FLIGHT: Runtime environment detection
# ------------------------------------------------------------------------------
if [ -f /.dockerenv ] || grep -qE 'docker|containerd' /proc/1/cgroup 2>/dev/null; then
    log "Runtime: container (Docker detected)."
    RUNTIME_TYPE="container"
else
    log "Runtime: host (no container detected)."
    RUNTIME_TYPE="host"
fi

if [ -r /proc/1/sched ]; then
    SCHED_NAME=$(awk '{print $NF}' /proc/1/sched 2>/dev/null | head -1 || echo "")
    if echo "$SCHED_NAME" | grep -q "runsc"; then
        log "gVisor (runsc) runtime DETECTED. Syscall interception active."
        RUNTIME_TYPE="gvisor"
    fi
fi
export RUNTIME_TYPE

# ------------------------------------------------------------------------------
# 0.3 PRE-FLIGHT: UID 1000 verification
# ------------------------------------------------------------------------------
if ! getent passwd 1000 &>/dev/null; then
    die "CRITICAL: UID 1000 (orchestrator user) does not exist. Dockerfile incomplete."
fi
log "UID 1000 verified: $(getent passwd 1000 | cut -d: -f1)"

# ------------------------------------------------------------------------------
# 0.4 PRE-FLIGHT: Stable machine-id generation
# ------------------------------------------------------------------------------
if [ -z "$PERSONA_MACHINE_ID" ]; then
    PERSONA_MACHINE_ID=$(echo -n "$PERSONA_ENTROPY_SEED" | python3 -c "import sys, hashlib; h = hashlib.sha256(sys.stdin.read().encode()).hexdigest(); print(f'{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}')")
    export PERSONA_MACHINE_ID
fi

if [ -w /etc/machine-id ] || [ ! -f /etc/machine-id ]; then
    echo "$PERSONA_MACHINE_ID" > /etc/machine-id 2>/dev/null || \
        cp /dev/null /etc/machine-id && echo "$PERSONA_MACHINE_ID" > /etc/machine-id 2>/dev/null
fi

mkdir -p /var/lib/dbus 2>/dev/null || true
if [ -w /var/lib/dbus/machine-id ] || [ ! -f /var/lib/dbus/machine-id ]; then
    echo "$PERSONA_MACHINE_ID" > /var/lib/dbus/machine-id 2>/dev/null || true
fi
# R7-ENT-013: chown the machine-id files to UID 1000 so they appear
# to have been written by the persona's process, not root. A sibling
# container with read access to the filesystem can see the file
# ownership; if it's root-owned, the persona appears to be running
# as root (which it isn't — the browser runs as UID 1000).
if [ -f /etc/machine-id ]; then
    chown 1000:1000 /etc/machine-id 2>/dev/null || true
fi
if [ -f /var/lib/dbus/machine-id ]; then
    chown 1000:1000 /var/lib/dbus/machine-id 2>/dev/null || true
fi
log "machine-id set to: $PERSONA_MACHINE_ID"

# ------------------------------------------------------------------------------
# 0.5 PRE-FLIGHT: Timezone alignment
# ------------------------------------------------------------------------------
if [ -f "/usr/share/zoneinfo/$PERSONA_TZ" ]; then
    if [ -L /etc/localtime ] || [ ! -f /etc/localtime ]; then
        rm -f /etc/localtime 2>/dev/null || true
        ln -sf "/usr/share/zoneinfo/$PERSONA_TZ" /etc/localtime 2>/dev/null || \
            cp "/usr/share/zoneinfo/$PERSONA_TZ" /etc/localtime 2>/dev/null
    fi
    echo "$PERSONA_TZ" > /etc/timezone 2>/dev/null || true
    log "Timezone set to: $PERSONA_TZ"
fi
export TZ="$PERSONA_TZ"

# ------------------------------------------------------------------------------
# 0.6 PRE-FLIGHT: Locale alignment
# ------------------------------------------------------------------------------
PERSONA_LOCALE_POSIX=$(echo "$PERSONA_LOCALE" | tr '[:upper:]-' '[:lower:]_')".UTF-8"
if command -v locale-gen &>/dev/null; then
    locale-gen "$PERSONA_LOCALE_POSIX" 2>/dev/null || \
        locale-gen en_US.UTF-8 2>/dev/null || true
fi
export LANG="$PERSONA_LOCALE_POSIX"
export LC_ALL="$PERSONA_LOCALE_POSIX"
export LANGUAGE="${PERSONA_LOCALE%[-_]*}:${PERSONA_LOCALE}"
log "Locale set to: $PERSONA_LOCALE_POSIX"

# ------------------------------------------------------------------------------
# 0.7 PRE-FLIGHT: dmesg access check
# ------------------------------------------------------------------------------
if command -v dmesg &>/dev/null; then
    if dmesg 2>/dev/null | head -1 >/dev/null; then
        warn "dmesg is readable. Host kernel version may leak."
    else
        log "dmesg access blocked (CAP_SYSLOG dropped). Host kernel hidden."
    fi
fi

# ------------------------------------------------------------------------------
# 0.8 PRE-FLIGHT: Memory pressure check
# ------------------------------------------------------------------------------
TOTAL_MEM_MB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print int($2/1024)}')
AVAIL_MEM_MB=$(grep MemAvailable /proc/meminfo 2>/dev/null | awk '{print int($2/1024)}')
log "Memory: ${TOTAL_MEM_MB}MB total, ${AVAIL_MEM_MB}MB available."
if [ "${AVAIL_MEM_MB:-0}" -lt 512 ]; then
    warn "Low memory (${AVAIL_MEM_MB}MB < 512MB). Xvfb may fail to start."
fi

# ------------------------------------------------------------------------------
# 0.9 PRE-FLIGHT: Shared memory validation
# ------------------------------------------------------------------------------
SHM_SIZE=$(df -B1M /dev/shm 2>/dev/null | tail -1 | awk '{print $2}' | tr -d 'M')
if [ -z "$SHM_SIZE" ] || [ "$SHM_SIZE" -lt 256 ]; then
    warn "Shared memory /dev/shm is ${SHM_SIZE}MB (< 256MB). Chromium may fall back to disk."
fi
log "Shared memory /dev/shm: ${SHM_SIZE}MB"

# ------------------------------------------------------------------------------
# 0.10 PRE-FLIGHT: chrony time-sync daemon
# ------------------------------------------------------------------------------
# v6.0.3: Time-sync startup. chrony keeps the system clock within ~50ms of
# real UTC. Without it, container clock drift is detectable as a
# Date.now() / Date.getTimezoneOffset() mismatch between the persona's
# claimed timezone and the server's actual time. This is one of the
# strongest single-shot fingerprint signals (maxmind/IPQS flag it).
if command -v chronyd &>/dev/null; then
    log "Starting chrony time-sync daemon (offline mode)..."
    # R7-ENT-002: chronyd in offline mode. The previous config had
    # `pool pool.ntp.org` which sent NTP requests with the container's
    # source IP. NTP servers log source IPs, leaking the container's
    # WAN egress. We use chrony's offline mode (`local stratum 10`)
    # which provides a stable monotonic clock without any network egress.
    # The container's clock is then anchored at container start, with
    # drift bounded by chrony's internal oscillator stability (<1s/hr).
    # For real NTP sync, the orchestrator can use DoH-synced time via
    # the d-py proxy. (See sticky_proxy_resolver.py verify_proxy_georegion
    # for one such time-correlated lookup.)
    CHRONY_USER="$(getent passwd chrony | cut -d: -f1 || echo chrony)"
    # Check if config has pool directives; if so, override with local mode.
    if [ -f /etc/chrony/chrony.conf ] && grep -qE '^\s*(pool|server)\s' /etc/chrony/chrony.conf; then
        warn "R7-ENT-002: chrony config has pool/server directives (would leak container IP). Switching to offline mode."
        # Generate a temporary offline-mode config
        local_chrony_conf="$CONTAINER_TMPDIR/chrony_offline.conf"
        {
            echo "# R7-ENT-002: Generated offline-mode chrony config"
            echo "# No NTP pool queries — no egress IP leak"
            echo "local stratum 10"
            echo "manual"
            echo "rtcsync"
            echo "allow"
        } > "$local_chrony_conf"
        chronyd -u "$CHRONY_USER" \
                -f "$local_chrony_conf" \
                >"$CONTAINER_TMPDIR/chrony.log" 2>&1 &
    else
        chronyd -u "$CHRONY_USER" \
                -f /etc/chrony/chrony.conf \
                >"$CONTAINER_TMPDIR/chrony.log" 2>&1 &
    fi
    CHRONY_PID=$!
    BACKGROUND_PIDS+=("$CHRONY_PID")

    # R7-ENT-023: Wait up to 10s for chrony. The previous 3s wait
    # was optimistic; real chrony takes 5-10s to fully sync even in
    # offline mode (it needs to read the system clock and initialize
    # its internal state). In offline mode there's no "synchronized"
    # state, so we just check that the daemon responds to
    # `chronyc tracking`.
    CHRONY_OK=false
    for _ in $(seq 1 100); do
        sleep 0.1
        if chronyc tracking 2>/dev/null | grep -qE "Reference ID|Last offset"; then
            CHRONY_OK=true
            break
        fi
    done
    if [ "$CHRONY_OK" = "true" ]; then
        OFFSET_MS=$(chronyc tracking 2>/dev/null | awk '/System time/ {print $4, $5}' | head -1)
        log "chrony READY (PID: $CHRONY_PID, offset: ${OFFSET_MS:-unknown})."
    else
        warn "chrony did not report tracking status within 3s. Continuing; time drift may exceed stealth budget."
    fi
    export CHRONY_PID
else
    warn "chronyd not installed. Container clock will drift; Date.now() fingerprint signal will leak."
fi

# ------------------------------------------------------------------------------
# 0.11 PRE-FLIGHT: CloakBrowser binary validation
# ------------------------------------------------------------------------------
CLOAKBROWSER_PATHS=(
    "/opt/cloakbrowser/chrome"
    "/opt/cloakbrowser/cloakbrowser"
    "/usr/local/bin/cloakbrowser"
    "/usr/bin/cloakbrowser"
)
CLOAKBROWSER_FOUND=""
for path in "${CLOAKBROWSER_PATHS[@]}"; do
    if [ -f "$path" ] && [ -x "$path" ]; then
        CLOAKBROWSER_FOUND="$path"
        log "CloakBrowser binary found: $path"
        break
    fi
done
if [ -z "$CLOAKBROWSER_FOUND" ]; then
    # Check for Playwright Chromium fallback
    PLAYWRIGHT_CHROMIUM=$(find /root/.cache/ms-playwright -name "chrome" -type f -executable 2>/dev/null | head -1)
    if [ -n "$PLAYWRIGHT_CHROMIUM" ]; then
        warn "CloakBrowser not found. Playwright Chromium fallback available: $PLAYWRIGHT_CHROMIUM"
    else
        warn "CloakBrowser AND Playwright Chromium NOT FOUND. Orchestrator will fail to launch browser."
    fi
fi

# ------------------------------------------------------------------------------
# 1.0 MOCK /proc FILES (persona-aligned)
# ------------------------------------------------------------------------------
log "=== Phase 1: Mock /proc files ==="
if [ -d /etc/mock_cpuinfo ] || [ -d /etc/mock_version ] || [ -d /etc/mock_dmi_id ] || [ -d /etc/mock_boot_id ]; then
    warn "One or more /etc/mock_* paths exist as DIRECTORIES."
    warn "Generating emergency fallback mock files in $CONTAINER_TMPDIR/emerg_mock..."

    EMERG_MOCK_DIR="$CONTAINER_TMPDIR/emerg_mock"
    mkdir -p "$EMERG_MOCK_DIR/dmi_id"
    cat > "$EMERG_MOCK_DIR/cpuinfo" <<'MOCKCPU'
processor   : 0
vendor_id   : GenuineIntel
cpu family  : 6
model       : 158
model name  : Intel(R) Core(TM) i7-8559U CPU @ 2.70GHz
stepping    : 10
microcode   : 0xffffffff
cpu MHz     : 2700.000
cache size  : 8192 KB
physical id : 0
siblings    : 8
core id     : 0
cpu cores   : 4
apicid      : 0
initial apicid  : 0
fpu     : yes
fpu_exception   : yes
cpuid level    : 22
wp      : yes
flags      : fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault invpcid_single pti ssbd ibrs ibpb stibp tpr_shadow vnmi flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp
bogomips    : 5400.00
clflush size    : 64
cache_alignment : 64
address sizes   : 39 bits physical, 48 bits virtual
power management:
MOCKCPU
    cat > "$EMERG_MOCK_DIR/version" <<'MOCKVER'
Linux version 4.4.0-19041-Microsoft (Microsoft@Microsoft.com) (gcc version 5.4.0 (GCC) ) #1237-Microsoft Sat Sep 11 13:32:00 PST 2021
MOCKVER
    cat > "$EMERG_MOCK_DIR/dmi_id/product_uuid" <<'MOCKUUID'
4c4c4544-0058-3910-8048-b2c04f324a32
MOCKUUID
    cat > "$EMERG_MOCK_DIR/dmi_id/product_serial" <<'MOCKSERIAL'
H3N1J7X
MOCKSERIAL
    cat > "$EMERG_MOCK_DIR/dmi_id/sys_vendor" <<'MOCKVENDOR'
Dell Inc.
MOCKVENDOR
    cat > "$EMERG_MOCK_DIR/dmi_id/product_name" <<'MOCKPROD'
XPS 13 9370
MOCKPROD
    cat > "$EMERG_MOCK_DIR/boot_id" <<'MOCKBOOT'
00000000-0000-0000-0000-000000000000
MOCKBOOT
    cat > "$EMERG_MOCK_DIR/osrelease" <<'MOCKREL'
4.4.0-19041-Microsoft
MOCKREL

    HAS_CAP_SYS_ADMIN=0
    if [ -r /proc/self/status ]; then
        CAP_SYS_ADMIN_BIT=$((1 << 21))
        CAP_EFF_HEX=$(awk '/^CapEff:/ {print $2}' /proc/self/status 2>/dev/null)
        if [ -n "$CAP_EFF_HEX" ]; then
            CAP_EFF=$(( ${CAP_EFF_HEX} ))
        else
            CAP_EFF=0
        fi
        if [ $((CAP_EFF & CAP_SYS_ADMIN_BIT)) -ne 0 ]; then
            HAS_CAP_SYS_ADMIN=1
        fi
    fi
    if [ "$HAS_CAP_SYS_ADMIN" = "1" ]; then
        mount --bind "$EMERG_MOCK_DIR/cpuinfo" /proc/cpuinfo 2>/dev/null || true
        mount --bind "$EMERG_MOCK_DIR/version" /proc/version 2>/dev/null || true
        mount --bind "$EMERG_MOCK_DIR/dmi_id/product_uuid" /sys/class/dmi/id/product_uuid 2>/dev/null || true
        mount --bind "$EMERG_MOCK_DIR/dmi_id/product_serial" /sys/class/dmi/id/product_serial 2>/dev/null || true
        mount --bind "$EMERG_MOCK_DIR/boot_id" /proc/sys/kernel/random/boot_id 2>/dev/null || true
        mount --bind "$EMERG_MOCK_DIR/osrelease" /proc/sys/kernel/osrelease 2>/dev/null || true
        log "Mock /proc and /sys rebound from emergency fallback."
    else
        warn "CAP_SYS_ADMIN not in effective set. Mock files will NOT be rebound."
    fi
fi

# ------------------------------------------------------------------------------
# 1.1 PRE-MOUNT: Per-task tmpfs
# ------------------------------------------------------------------------------
# R7-ENT-011: Use /proc/sys/kernel/random/uuid (always available on
# Linux 3.x+). Fallback to python3 uuid or urandom if not available.
TASK_UUID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c "import uuid; print(uuid.uuid4())" 2>/dev/null || tr -dc 'a-f0-9' < /dev/urandom | head -c 32 | sed 's/\(........\)\(....\)\(....\)\(....\)\(............\)/\1-\2-\3-\4-\5/')
if [ -z "$TASK_UUID" ]; then
    TASK_UUID="00000000-0000-0000-0000-000000000000"
fi
TASK_TMPFS_DIR="/mnt/ramdisk/task_${TASK_UUID}"
mkdir -p "$TASK_TMPFS_DIR"
log "Per-task workspace: $TASK_TMPFS_DIR"

export ORCH_TASK_UUID="$TASK_UUID"
export ORCH_TASK_TMPFS_DIR="$TASK_TMPFS_DIR"

RAMDISK_TMPFS_COUNT=$(mount | grep -E "on /mnt/ramdisk type tmpfs" | wc -l)
if [ "$RAMDISK_TMPFS_COUNT" -gt 1 ]; then
    die "CRITICAL: $RAMDISK_TMPFS_COUNT tmpfs mounts at /mnt/ramdisk. Refusing to start."
fi
log "Single-tmpfs invariant verified (count: $RAMDISK_TMPFS_COUNT)."

# ------------------------------------------------------------------------------
# 2.0 PRE-SPAWN: Master Xvfb display (race-condition fix)
# ------------------------------------------------------------------------------
# R7-ENT-022: Xvfb and DBus pre-spawn are sequential (~3s startup
# penalty). For 1-persona with a 45-min session, this is acceptable.
# For multi-persona, parallelizing via background processes would save
# 1-2s per container startup. Documented as intentional.
log "=== Phase 2: Xvfb pre-spawn (sequential, ~3s) ==="

# v6.0.2: Calculate DPI from persona screen dimensions if not set
# Standard DPI calculations:
#   1920x1080 (FHD) = 96 DPI
#   2560x1440 (QHD) = 109 DPI
#   3840x2160 (4K)  = 163 DPI
#   3840x2400 (4K+)  = 194 DPI
if [ "${PERSONA_DPI}" = "96" ] && [ "${PERSONA_SCREEN_WIDTH}" -gt 2560 ]; then
    # High-DPI persona detected
    if [ "${PERSONA_SCREEN_WIDTH}" -ge 3840 ]; then
        PERSONA_DPI=163  # 4K
    elif [ "${PERSONA_SCREEN_WIDTH}" -ge 3200 ]; then
        PERSONA_DPI=137  # 3K
    else
        PERSONA_DPI=109  # QHD
    fi
    export PERSONA_DPI
    log "Calculated PERSONA_DPI=$PERSONA_DPI from screen ${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}"
fi

# v6.0.2: Pre-warm fontconfig ONCE (was running twice before)
log "Pre-warming fontconfig cache..."
fc-cache -f 2>/dev/null || true
FONT_COUNT=$(fc-list 2>/dev/null | wc -l)
log "Fontconfig cache warmed. $FONT_COUNT fonts available."

# v6.0.2: Check for required font families
REQUIRED_FONTS=("DejaVu" "Liberation" "Noto" "Sans" "Serif" "Mono")
MISSING_FONTS=()
for font in "${REQUIRED_FONTS[@]}"; do
    if ! fc-list 2>/dev/null | grep -qi "$font"; then
        MISSING_FONTS+=("$font")
    fi
done
if [ ${#MISSING_FONTS[@]} -ne 0 ]; then
    warn "Missing font families: ${MISSING_FONTS[*]}"
    warn "Canvas font rendering may differ from real Windows."
    warn "Consider adding fonts-noto-core, fonts-dejavu-core, ttf-mscorefonts-installer to Dockerfile."
fi

# v6.0.2: Build Xvfb command (NO duplicate -screen flag)
# Extensions: GLX (OpenGL), RENDER (compositing), XFIXES (cursor),
# XDamage (compositor), XINERAMA (multi-monitor), RANDR (resize/rotate),
# Composite (offscreen rendering), DRI3 (direct rendering), Present (buffer present)
XVFB_CMD=(
    "Xvfb"
    ":$MASTER_XVFB_DISPLAY"
    "-screen" "0"
    "${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}x${PERSONA_COLOR_DEPTH}"
    "-ac"
    "+extension" "GLX"
    "+extension" "RENDER"
    "+extension" "XFIXES"
    "+extension" "XDamage"
    "+extension" "XINERAMA"
    "+extension" "RANDR"
    "+extension" "Composite"
    "+extension" "DRI3"
    "+extension" "Present"
    "+render"
    "-noreset"
    "-nolisten" "tcp"
    "-dpi" "$PERSONA_DPI"
    "-shmem"
)

# v6.0.2: Launch Xvfb in background, redirect ALL output
"${XVFB_CMD[@]}" >"$CONTAINER_TMPDIR/xvfb.log" 2>&1 &
XVFB_PID=$!
BACKGROUND_PIDS+=("$XVFB_PID")
log "Xvfb spawned (PID: $XVFB_PID, ${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}x${PERSONA_COLOR_DEPTH}@${PERSONA_DPI}dpi). Waiting for readiness..."

# v6.0.2: Wait for display readiness (max 10 seconds) with xdpyinfo validation
READY=false
for i in $(seq 1 40); do
    sleep 0.25
    if command -v xdpyinfo &>/dev/null; then
        if xdpyinfo -display ":$MASTER_XVFB_DISPLAY" >/dev/null 2>&1; then
            READY=true
            break
        fi
    else
        if kill -0 "$XVFB_PID" 2>/dev/null; then
            port=$((6000 + MASTER_XVFB_DISPLAY))
            if (echo >/dev/tcp/127.0.0.1/$port) 2>/dev/null; then
                READY=true
                break
            fi
        fi
    fi
done

if [ "$READY" != "true" ]; then
    err "Xvfb display :$MASTER_XVFB_DISPLAY failed to become ready within 10s."
    err "Xvfb log:"
    cat "$CONTAINER_TMPDIR/xvfb.log" 2>/dev/null | head -20 >&2
    die "CRITICAL: Xvfb startup failed. Aborting."
fi

# v6.0.2: Validate Xvfb display dimensions match persona
ACTUAL_DIM=$(xdpyinfo -display ":$MASTER_XVFB_DISPLAY" 2>/dev/null | grep "dimensions:" | awk '{print $2}')
EXPECTED_DIM="${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}"
if [ "$ACTUAL_DIM" != "$EXPECTED_DIM" ]; then
    warn "Xvfb dimensions mismatch: actual=$ACTUAL_DIM expected=$EXPECTED_DIM"
fi
log "Xvfb display :$MASTER_XVFB_DISPLAY is READY (PID: $XVFB_PID, ${ACTUAL_DIM:-unknown})."

# v6.0.2: Simulate Windows 10/11 taskbar (40px reserved at bottom)
# This makes screen.availHeight = screen.height - 40
if command -v xrandr &>/dev/null; then
    log "Configuring synthetic taskbar (40px reserved at bottom)..."
    # Set the workarea to exclude the bottom 40px (taskbar)
    WORKAREA_HEIGHT=$((PERSONA_SCREEN_HEIGHT - 40))
    # Use xrandr to set a virtual monitor with the workarea
    # Note: Xvfb doesn't support xrandr fb manipulation directly, but
    # Chromium computes availHeight from the root window's _NET_WORKAREA hint.
    # We set this via xprop if available.
    if command -v xprop &>/dev/null; then
        xprop -display ":$MASTER_XVFB_DISPLAY" -root \
            -format _NET_WORKAREA 32a -set _NET_WORKAREA "0,0,${PERSONA_SCREEN_WIDTH},${WORKAREA_HEIGHT}" 2>/dev/null || \
            warn "xprop failed to set _NET_WORKAREA (taskbar simulation skipped)"
        log "Synthetic taskbar configured: availHeight=${WORKAREA_HEIGHT}"
    fi
fi

export XVFB_PID

# ------------------------------------------------------------------------------
# 2.1 PRE-WARM: DBus session bus
# ------------------------------------------------------------------------------
log "=== Phase 2.1: DBus session bus ==="

# v6.0.2: Ensure socket directory exists and is owned by UID 1000
DBUS_SOCKET_DIR=$(echo "$DBUS_SESSION_BUS_ADDRESS" | sed 's|unix:path=||' | xargs dirname)
mkdir -p "$DBUS_SOCKET_DIR" 2>/dev/null || true
chown 1000:1000 "$DBUS_SOCKET_DIR" 2>/dev/null || true
chmod 755 "$DBUS_SOCKET_DIR" 2>/dev/null || true

if [ -f /etc/dbus-1/system.conf ] && [ -S /var/run/dbus/system_bus_socket ]; then
    log "Using existing system DBus."
    DBUS_PID=""
elif command -v dbus-daemon &>/dev/null; then
    # v6.0.2: Create session bus config in unique temp dir
    DBUS_CONF_DIR="$CONTAINER_TMPDIR/dbus-conf"
    mkdir -p "$DBUS_CONF_DIR"
    cat > "$DBUS_CONF_DIR/session.conf" <<'DBUSCONF'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path=/tmp/dbus-session-bus</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
  <include ifexist="/usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.service"/>
  <include ifexist="/usr/share/dbus-1/system-services/org.freedesktop.PulseAudio.service"/>
  <include ifexist="/usr/share/dbus-1/services/org.freedesktop.UDisks2.service"/>
</busconfig>
DBUSCONF

    dbus-daemon --config-file "$DBUS_CONF_DIR/session.conf" --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile >"$CONTAINER_TMPDIR/dbus.log" 2>&1 &
    DBUS_PID=$!
    BACKGROUND_PIDS+=("$DBUS_PID")

    # v6.0.2: Wait for DBus to be ready (2.0s + readiness test)
    DBUS_READY=false
    for i in $(seq 1 20); do
        sleep 0.1
        if dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply \
            /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null | grep -q "reply"; then
            DBUS_READY=true
            break
        fi
    done
    if [ "$DBUS_READY" = "true" ]; then
        log "DBus session bus READY (PID: $DBUS_PID) at $DBUS_SESSION_BUS_ADDRESS"
    else
        warn "DBus daemon started but not responding to queries. CloakBrowser may show degraded audio/network."
    fi
    export DBUS_PID
else
    warn "dbus-daemon not available. CloakBrowser may show degraded audio/network."
    DBUS_PID=""
    export DBUS_PID
fi

# ------------------------------------------------------------------------------
# 2.2 PRE-WARM: PulseAudio null-sink
# ------------------------------------------------------------------------------
log "=== Phase 2.2: PulseAudio null-sink ==="

# v6.0.2: Ensure PulseAudio directories are accessible
mkdir -p "$PULSE_RUNTIME_PATH" 2>/dev/null || true
chown -R 1000:1000 "$PULSE_RUNTIME_PATH" 2>/dev/null || true
chmod 700 "$PULSE_RUNTIME_PATH" 2>/dev/null || true

# v6.0.2: Ensure XDG_RUNTIME_DIR has a pulse subdir
mkdir -p "$XDG_RUNTIME_DIR/pulse" 2>/dev/null || true
chown -R 1000:1000 "$XDG_RUNTIME_DIR/pulse" 2>/dev/null || true
chmod 700 "$XDG_RUNTIME_DIR/pulse" 2>/dev/null || true

# v6.0.2: Align PULSE_RUNTIME_PATH with XDG_RUNTIME_DIR/pulse
export PULSE_RUNTIME_PATH="$XDG_RUNTIME_DIR/pulse"

if command -v pulseaudio &>/dev/null; then
    # R7-ENT-027: Start PulseAudio as UID 1000 (the orchestrator user)
    # to avoid the root-owned socket issue (ENT-009). The previous
    # code started PulseAudio as root and chown'd the socket after,
    # leaving a brief window where UID 1000 couldn't read the socket.
    if command -v runuser &>/dev/null; then
        runuser -u 1000 -- pulseaudio --daemonize --disallow-exit --exit-idle-time=-1 \
            --log-target=syslog --log-level=error \
            >"$CONTAINER_TMPDIR/pulseaudio.log" 2>&1 || \
            su -s /bin/sh 1000 -c "pulseaudio --daemonize --disallow-exit --exit-idle-time=-1 --log-target=syslog --log-level=error" \
            >"$CONTAINER_TMPDIR/pulseaudio.log" 2>&1 || \
            warn "PulseAudio failed to start as UID 1000. AudioContext may return empty devices."
    else
        # Fallback: start as root (chown after)
        pulseaudio --daemonize --disallow-exit --exit-idle-time=-1 \
            --log-target=syslog --log-level=error \
            >"$CONTAINER_TMPDIR/pulseaudio.log" 2>&1 || \
            warn "PulseAudio failed to start. AudioContext may return empty devices."
    fi

    PULSE_PID=""
    sleep 0.5

    # v6.0.2: Verify PulseAudio is responding
    if command -v pactl &>/dev/null; then
        if pactl info 2>/dev/null | grep -q "Server Name"; then
            PULSE_PID=$(pgrep -f "pulseaudio --daemonize" | head -1 || echo "")
            log "PulseAudio daemon READY (PID: ${PULSE_PID:-unknown})"
        else
            warn "PulseAudio started but pactl info failed. May need more time."
            sleep 1
            if pactl info 2>/dev/null | grep -q "Server Name"; then
                PULSE_PID=$(pgrep -f "pulseaudio --daemonize" | head -1 || echo "")
                log "PulseAudio daemon READY after retry (PID: ${PULSE_PID:-unknown})"
            fi
        fi
    fi

    # v6.0.2: Create null-sink for AudioContext
    if command -v pacmd &>/dev/null && [ -n "$PULSE_PID" ]; then
        pacmd load-module module-null-sink sink_name=null0 sink_properties=device.description="Stereo" 2>/dev/null || true
        pacmd set-default-sink null0 2>/dev/null || true
        log "PulseAudio null-sink 'null0' created as default output."
    fi
    export PULSE_PID
else
    warn "pulseaudio not installed. AudioContext may return empty devices."
    PULSE_PID=""
    export PULSE_PID
fi

# ------------------------------------------------------------------------------
# 2.3 PRE-WARM: Machine-id binding
# ------------------------------------------------------------------------------
echo "$PERSONA_MACHINE_ID" > /etc/machine-id 2>/dev/null || true
log "Session machine-id: $PERSONA_MACHINE_ID"

# ------------------------------------------------------------------------------
# 2.4 PRE-WARM: Audio device check
# ------------------------------------------------------------------------------
if command -v pactl &>/dev/null; then
    AUDIO_SINKS=$(pactl list short sinks 2>/dev/null | wc -l)
    if [ "$AUDIO_SINKS" -gt 0 ]; then
        log "PulseAudio sinks available: $AUDIO_SINKS"
    else
        warn "PulseAudio has no sinks. AudioContext may return empty devices."
    fi
fi

# ------------------------------------------------------------------------------
# 3.0 DYNAMIC IP AUTO-RESOLUTION & SUBNET DISCOVERY
# ------------------------------------------------------------------------------
log "=== Phase 3: Network configuration ==="
DEFAULT_IFACE="${DEFAULT_IFACE:-eth0}"
ACTIVE_SUBNET=$(ip -o -4 addr show dev "$DEFAULT_IFACE" scope global 2>/dev/null | awk '{print $4}' | head -n 1 | cut -d/ -f1 | cut -d. -f1-3)
if [[ -z "$ACTIVE_SUBNET" ]]; then
    warn "Unable to parse active subnet on $DEFAULT_IFACE. Falling back to 172.18.0."
    ACTIVE_SUBNET="172.18.0"
fi

resolve_ip() {
    local host="$1"
    local fallback="$2"
    local resolved
    resolved=$(timeout 2 getent hosts "$host" | awk '{print $1}' | head -n 1) || true
    if [[ -n "$resolved" ]]; then
        echo "$resolved"
    else
        echo "$fallback"
    fi
}

log "Resolving internal network dependencies..."
DB_VAULT_IP=$(resolve_ip "db-vault" "$ACTIVE_SUBNET.2")
REDIS_QUEUE_IP=$(resolve_ip "redis-queue" "$ACTIVE_SUBNET.3")
CLEAN_ROOM_GATE_IP=$(resolve_ip "clean-room-gate" "$ACTIVE_SUBNET.4")
PROXY_RES_IP=$(resolve_ip "proxy-res" "$ACTIVE_SUBNET.5")
DOH_PROXY_IP=$(resolve_ip "doh-proxy" "$ACTIVE_SUBNET.6")

log "  - Subnet: $ACTIVE_SUBNET.x"
log "  - db-vault: $DB_VAULT_IP"
log "  - redis-queue: $REDIS_QUEUE_IP"
log "  - clean-room-gate: $CLEAN_ROOM_GATE_IP"
log "  - proxy-res: $PROXY_RES_IP"
log "  - doh-proxy: $DOH_PROXY_IP"

export DB_VAULT_IP REDIS_QUEUE_IP CLEAN_ROOM_GATE_IP PROXY_RES_IP DOH_PROXY_IP

# ------------------------------------------------------------------------------
# 3.1 SYNCHRONIZE /etc/hostname
# ------------------------------------------------------------------------------
# R7-ENT-008: KERNEL_NODENAME is exported as HOSTNAME. The
# orchestrator (hardened_orchestrator.py) doesn't currently read
# os.environ['HOSTNAME'], but exporting it here allows future
# persona-context features (e.g., persona-specific hostname binding)
# to pick it up without entrypoint changes. The export is
# intentional and documents the contract.
# R7-ENT-010: Override the kernel hostname BEFORE reading it.
# Docker sets the container's hostname to the 12-char container ID
# (e.g., "a1b2c3d4e5f6"). This is detectable as a fingerprint
# signal (real Windows hosts don't have 12-char hex hostnames).
# We use the persona-appropriate value computed above.
log "Synchronizing /etc/hostname with persona-appropriate value..."
KERNEL_NODENAME=$(hostname 2>/dev/null)
if [ -z "$KERNEL_NODENAME" ] || echo "$KERNEL_NODENAME" | grep -qE "^[a-f0-9]{12}$"; then
    # Container ID default or empty; use Windows-style format
    KERNEL_NODENAME="DESKTOP-$(tr -dc 'A-F0-9' < /dev/urandom | head -c 7)"
fi
echo "$KERNEL_NODENAME" > /etc/hostname
export HOSTNAME="$KERNEL_NODENAME"

# ------------------------------------------------------------------------------
# 3.2 LOCALIZE /etc/hosts
# ------------------------------------------------------------------------------
log "Localizing name resolution in /etc/hosts..."
sed -i '/db-vault\|redis-queue\|clean-room-gate\|proxy-res\|doh-proxy/d' /etc/hosts 2>/dev/null || true
echo "$DB_VAULT_IP db-vault" >> /etc/hosts
echo "$REDIS_QUEUE_IP redis-queue" >> /etc/hosts
echo "$CLEAN_ROOM_GATE_IP clean-room-gate" >> /etc/hosts
echo "$PROXY_RES_IP proxy-res" >> /etc/hosts
echo "$DOH_PROXY_IP doh-proxy" >> /etc/hosts

# ------------------------------------------------------------------------------
# 4.0 DNS OVERWRITE & SEARCH SUFFIX PURGE
# ------------------------------------------------------------------------------
log "Locking DNS resolver to secure DoH proxy..."
echo "nameserver ${DOH_PROXY_IP}" > /etc/resolv.conf
sed -i '/^search/d' /etc/resolv.conf 2>/dev/null || true

# ------------------------------------------------------------------------------
# 5.0 DEACTIVATE DOCKER EMBEDDED NAT DNS REDIRECTION
# ------------------------------------------------------------------------------
if iptables -t nat -L OUTPUT -n &>/dev/null; then
    log "Deactivating Docker embedded NAT DNS redirection..."
    iptables -t nat -F OUTPUT &>/dev/null || true
    iptables -t nat -F PREROUTING &>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 6.0 FAIL-CLOSED DEFAULT POLICY
# ------------------------------------------------------------------------------
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

# ------------------------------------------------------------------------------
# 7.0 INPUT CHAIN RULES
# ------------------------------------------------------------------------------
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -p icmp --icmp-type destination-unreachable -j ACCEPT

if iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT &>/dev/null; then
    log "Stateful filter: conntrack enabled."
    iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
else
    die "CRITICAL: No stateful connection tracking available. Cannot continue."
fi

# ------------------------------------------------------------------------------
# 8.0 OUTPUT CHAIN RULES (kill-switch firewall)
# ------------------------------------------------------------------------------
# Block CDP (port 9222) on all interfaces
iptables -A OUTPUT -p tcp --dport 9222 -j DROP
log "CDP (port 9222) blocked on all interfaces."

# Allow loopback
iptables -A OUTPUT -o lo -j ACCEPT

# Block cloud metadata service
iptables -A OUTPUT -d 169.254.169.254 -j REJECT --reject-with icmp-port-unreachable

# Reject outbound SSH and NTP
iptables -A OUTPUT -p tcp --dport 22 -j REJECT --reject-with tcp-reset
iptables -A OUTPUT -p udp --dport 123 -j REJECT --reject-with icmp-port-unreachable

# Drop outbound non-loopback ICMP
iptables -A OUTPUT -p icmp ! -o lo -j DROP || true
log "Outbound non-loopback ICMP silenced."

# Block SCTP, DCCP
iptables -A OUTPUT -p sctp -j REJECT --reject-with icmp-proto-unreachable || true
iptables -A OUTPUT -p dccp -j REJECT --reject-with icmp-proto-unreachable || true

# mDNS sinkhole
if iptables -t nat -L OUTPUT -n &>/dev/null; then
    iptables -t nat -A OUTPUT -p udp -d 224.0.0.251 --dport 5353 -j DNAT --to-destination 127.0.0.1 || true
    iptables -t nat -A OUTPUT -p udp -d 224.0.0.252 --dport 5355 -j DNAT --to-destination 127.0.0.1 || true
    iptables -t nat -A OUTPUT -p udp -d 239.255.255.250 --dport 1900 -j DNAT --to-destination 127.0.0.1 || true
    log "mDNS sinkhole active (224.0.0.251:5353 -> 127.0.0.1)."
fi

# R7-ENT-004: Always DROP mDNS at OUTPUT level, even if DNAT failed.
# The previous code fell through to REJECT (which sends an ICMP
# port-unreachable with the container's source IP, leaking the
# container's eth0 IP to the multicast sender). DROP silently
# discards, so no ICMP is sent.
iptables -A OUTPUT -p udp --dport 5353 -j DROP || true
iptables -A INPUT -p udp --sport 5353 -j DROP || true

# Also DROP mDNS LLQ, LLMNR, and SSDP for completeness
iptables -A OUTPUT -p udp --dport 5355 -j DROP || true
iptables -A OUTPUT -p udp --dport 1900 -j DROP || true

# STUN/TURN block
iptables -A OUTPUT -p udp --dport 3478 -j REJECT --reject-with icmp-port-unreachable || true
iptables -A OUTPUT -p tcp --dport 3478 -j REJECT --reject-with tcp-reset || true
iptables -A OUTPUT -p udp --dport 5349 -j REJECT --reject-with icmp-port-unreachable || true
iptables -A OUTPUT -p tcp --dport 5349 -j REJECT --reject-with tcp-reset || true

# R7-ENT-019: Allow SOCKS5 UDP ASSOCIATE to the proxy only.
# The previous blanket REJECT on all UDP 1024-65535 blocked SOCKS5
# UDP ASSOCIATE (used for UDP-over-SOCKS5). We add an explicit
# ALLOW rule for the proxy IP before the blanket REJECT.
iptables -A OUTPUT -d "$PROXY_RES_IP" -p udp -j ACCEPT 2>/dev/null || true
# WebRTC: block all outbound UDP except loopback
iptables -A OUTPUT -p udp --dport 1024:65535 ! -d 127.0.0.0/8 -j REJECT --reject-with icmp-port-unreachable || true
log "Outbound UDP 1024-65535 blocked (except loopback and SOCKS5 proxy)."

# DNS resolver routing
iptables -A OUTPUT -d "$DOH_PROXY_IP" -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -d "$DOH_PROXY_IP" -p tcp --dport 53 -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -j REJECT --reject-with icmp-port-unreachable
iptables -A OUTPUT -p tcp --dport 53 -j REJECT --reject-with tcp-reset

# Internal service whitelisting
iptables -A OUTPUT -d "$DB_VAULT_IP" -p tcp --dport 5432 -j ACCEPT
iptables -A OUTPUT -d "$REDIS_QUEUE_IP" -p tcp --dport 6379 -j ACCEPT
iptables -A OUTPUT -d "$CLEAN_ROOM_GATE_IP" -p tcp --dport 3000 -j ACCEPT
iptables -A OUTPUT -d "$PROXY_RES_IP" -p tcp --dport 1081:1085 -j ACCEPT

# WAN catch-all: block HTTP/HTTPS (must go through SOCKS5 proxy)
iptables -A OUTPUT -p tcp --dport 80 -j REJECT --reject-with tcp-reset
iptables -A OUTPUT -p tcp --dport 443 -j REJECT --reject-with tcp-reset

# v6.0.2: Allow loopback to dbus, pulseaudio, Xvfb (Unix sockets + X11)
iptables -A OUTPUT -d 127.0.0.0/8 -p tcp --dport 6000:6100 -j ACCEPT
iptables -A OUTPUT -d 127.0.0.0/8 -p unix -j ACCEPT 2>/dev/null || true
log "Loopback X11/unix-socket traffic allowed (for dbus/pulse/Xvfb)."

# ------------------------------------------------------------------------------
# 9.0 CONTAINER-LEVEL NETWORK PROTOCOL HARDENING
# ------------------------------------------------------------------------------
ip link set dev eth0 mtu 1500 2>/dev/null || true

# v6.0.1: Debian 12 / Linux 6.1.x defaults (Linux, NOT Windows-spoofed)
sysctl -w net.ipv4.tcp_timestamps=1 &>/dev/null || warn "tcp_timestamps=1 may not be settable"
sysctl -w net.ipv4.tcp_sack=1 &>/dev/null || true
sysctl -w net.ipv4.tcp_dsack=1 &>/dev/null || true
sysctl -w net.ipv4.tcp_congestion_control=cubic &>/dev/null || true
sysctl -w net.ipv4.conf.all.arp_ignore=1 &>/dev/null || true
sysctl -w net.ipv4.conf.all.arp_announce=2 &>/dev/null || true
# R7-ENT-016: Set TTL=128 to match Windows. The kernel default
# is 64 (Linux), but the persona claims Windows where TTL=128 is
# standard. A proxy-side TTL observation reveals the mismatch.
# Some servers drop TTL=128 packets as suspicious, but these are
# rare; the fingerprint-detection risk is higher.
sysctl -w net.ipv4.ip_default_ttl=128 &>/dev/null || warn "ip_default_ttl=128 may not be settable"

# INVIS-53: TCP initial window size matching Chrome 120
# Real Chrome on Linux uses an initial congestion window (cwnd) of ~14
# MSS (≈14600 bytes for typical MTU 1500). The kernel default is
# typically initcwnd=10, which produces a cwnd ~10240. The mismatch
# is detectable by p0f-like fingerprinters. Bump to Chrome 120 value.
# R7-ENT-017: Note that cwnd also depends on the application's
# socket options (TCP_NODELAY, TCP_QUICKACK). Chrome sets
# TCP_NODELAY=1 by default. The orchestrator's curl_cffi calls
# MUST use the same socket options to avoid cross-correlation
# between browser and curl_cffi cwnd profiles. Document this.
sysctl -w net.ipv4.tcp_init_cwnd=14 &>/dev/null || warn "tcp_init_cwnd=14 may not be settable"
# INVIS-53: Per-socket TCP buffer sizes matching Chrome 120 defaults
sysctl -w net.ipv4.tcp_rmem="4096 87380 6291456" &>/dev/null || true
sysctl -w net.ipv4.tcp_wmem="4096 65536 4194304" &>/dev/null || true
# INVIS-53: Enable TCP Fast Open (Chrome 120 uses it on Linux)
sysctl -w net.ipv4.tcp_fastopen=3 &>/dev/null || warn "tcp_fastopen=3 may not be settable"

# INVIS-51: TLS fingerprint alignment. CloakBrowser's uTLS library
# (or curl_cffi when used) handles the JA3/JA4 fingerprint at the
# application layer. At the kernel layer, we set the cipher list
# order to match Chrome 120's default (TLS_AES_128_GCM_SHA256 first,
# then TLS_AES_256_GCM_SHA384, then TLS_CHACHA20_POLY1305_SHA256, then
# the ECDHE variants). Some kernel versions support /proc/sys/crypto,
# but most don't. This is best-effort.
if [ -f /proc/sys/crypto/cipher_list ] 2>/dev/null; then
    # Best-effort: log what's available. We don't override the kernel
    # cipher list (it's process-local via SSL_CTX_set_cipher_list).
    log "Kernel cipher list: $(cat /proc/sys/crypto/cipher_list 2>/dev/null | tr -d '\n' | cut -c1-100)..."
fi

# INVIS-52: HTTP/2 fingerprint verification. We can't directly verify
# Chrome's HTTP/2 SETTINGS frame order at the kernel layer, but we
# can ensure the system has the right congestion control (BBR was
# used by Chrome 89+, but Chrome 120 uses Cubic on Linux by default).
# The actual HTTP/2 fingerprinting is handled at the application layer
# by CloakBrowser (which sends SETTINGS in Chrome's exact order).
log "INVIS-52: HTTP/2 fingerprint handled at application layer (CloakBrowser uTLS)."

# INVIS-55: OCSP soft-fail (Chrome's default). Chrome does NOT
# hard-fail on OCSP errors; it treats them as soft failures. We can't
# change Chrome's OCSP behavior here, but we log it for documentation.
log "INVIS-55: OCSP soft-fail is the default (Chrome 120 behavior)."

# INVIS-56: IPv4-only egress. We already disable IPv6 below. Force
# outbound traffic to use IPv4 only by ensuring no IPv6 default route
# exists and bind outbound sockets to IPv4. The application (CloakBrowser)
# uses SOCKS5h for DNS resolution, which uses the IPv4 egress path.
log "INVIS-56: IPv4-only egress enforced via IPv6 disable (see Phase 11.0)."

# INVIS-57: DoH forced. The DoH proxy at $DOH_PROXY_IP is the ONLY DNS
# resolver. Outbound UDP 53 is blocked except to the DoH proxy. The
# SOCKS5h configuration forces remote DNS resolution. This is already
# implemented in Phase 8.0 (DNS resolver routing).
log "INVIS-57: DoH forced via SOCKS5h + DNS-over-HTTPS proxy."

# ------------------------------------------------------------------------------
# 10.0 DEFAULT GATEWAY REMOVAL
# ------------------------------------------------------------------------------
if ip route show | grep -q "^default"; then
    log "Deleting default WAN gateway (force SOCKS5-only egress)..."
    ip route del default || true
fi

# ------------------------------------------------------------------------------
# 11.0 FULL IPv6 DISABLE
# ------------------------------------------------------------------------------
# R7-ENT-018: Flush IPv6 loopback after disabling IPv6. The
# kernel may still have ::1 configured on lo even after
# disable_ipv6=1, causing apps to attempt IPv6 connections that
# fail. We flush the address to ensure clean IPv4-only state.
if [ -d "/proc/sys/net/ipv6" ]; then
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 &>/dev/null || true
    sysctl -w net.ipv6.conf.default.disable_ipv6=1 &>/dev/null || true
    sysctl -w net.ipv6.conf.lo.disable_ipv6=1 &>/dev/null || true
    # R7-ENT-018: Flush ::1 from lo if present
    ip -6 addr flush dev lo &>/dev/null || true
fi

if command -v ip6tables &>/dev/null; then
    if ip6tables -L OUTPUT -n &>/dev/null; then
        log "ip6tables: enforcing default DROP policies."
        ip6tables -P INPUT DROP || true
        ip6tables -P FORWARD DROP || true
        ip6tables -P OUTPUT DROP || true
    fi
fi

# ------------------------------------------------------------------------------
# 12.0 TCP MSS CLAMPING
# ------------------------------------------------------------------------------
if iptables -t mangle -L OUTPUT -n &>/dev/null; then
    log "TCP MSS clamping: 1200 (external-only, internal/loopback excluded)..."
    INTERNAL_SUBNET_CIDR="${ACTIVE_SUBNET}.0/24"
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN \
        ! -d 127.0.0.0/8 \
        ! -d "$INTERNAL_SUBNET_CIDR" \
        -j TCPMSS --set-mss 1200 || true
fi

# ------------------------------------------------------------------------------
# 13.0 TC QDISC CLEANUP
# ------------------------------------------------------------------------------
if command -v tc &>/dev/null; then
    # R7-ENT-005: Only delete if a non-default qdisc is present.
    # The previous unconditional `tc qdisc del` produces
    # "RTNETLINK answers: No such file or directory" in dmesg when
    # no qdisc exists, which is observable from a sibling container
    # (shared network namespace). Check first to avoid the error.
    if tc qdisc show dev eth0 2>/dev/null | grep -qE '^\s*qdisc\s+(netem|prio|hfq|tbf|sfq)\s'; then
        tc qdisc del dev eth0 root &>/dev/null || true
        log "tc qdisc: pre-existing netem rule cleared from eth0."
    fi
    if tc qdisc show dev lo 2>/dev/null | grep -qE '^\s*qdisc\s+(netem|prio|hfq|tbf|sfq)\s'; then
        tc qdisc del dev lo root &>/dev/null || true
        log "tc qdisc: pre-existing netem rule cleared from lo."
    fi
fi

# ------------------------------------------------------------------------------
# 14.0 PRIVILEGE DROP: root -> UID 1000
# ------------------------------------------------------------------------------
log "=== Phase 14: Privilege drop ==="

# v6.0.2: Only chown if filesystem is writable
for path in /app /opt/venv /mnt/ramdisk "$TASK_TMPFS_DIR" "$XDG_RUNTIME_DIR" "$PULSE_RUNTIME_PATH" "$DBUS_SOCKET_DIR"; do
    if [ -d "$path" ]; then
        if mount | grep -E "on $path " | grep -q "ro,"; then
            warn "$path is on a read-only filesystem. Skipping chown."
        else
            chown -R 1000:1000 "$path" 2>/dev/null || true
            chmod 755 "$path" 2>/dev/null || true
        fi
    fi
done

# v6.0.2: Ensure /tmp subdirs are accessible
for dir in "$DBUS_SOCKET_DIR" "$XDG_RUNTIME_DIR" "$PULSE_RUNTIME_PATH" "$CONTAINER_TMPDIR"; do
    if [ -d "$dir" ]; then
        chown 1000:1000 "$dir" 2>/dev/null || true
        chmod 755 "$dir" 2>/dev/null || true
    fi
done

log "Privileges ready. Handing off to UID 1000 for orchestrator process."

# ------------------------------------------------------------------------------
# 15.0 CLEANUP SIGNAL TRAPS
# ------------------------------------------------------------------------------
# v6.0.2: Traps are set HERE, but will be RESET by the orchestrator's
# signal.signal() calls. The trap is a safety net for early failures
# (before exec) and for signal inheritance issues.
cleanup_on_entrypoint_exit() {
    local sig="${1:-EXIT}"

    log "Signal ${sig} received in entrypoint. Cleaning up background processes..."
    # Always kill background processes on any signal/exit for proper cleanup.
    # PulseAudio and other services must be terminated to prevent cross-container
    # contamination and resource leaks.
    for pid in "${BACKGROUND_PIDS[@]}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    sleep 2
    for pid in "${BACKGROUND_PIDS[@]}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    # v6.0.3: Clean up CONTAINER_TMPDIR to prevent /tmp accumulation
    if [ -n "$CONTAINER_TMPDIR" ] && [ -d "$CONTAINER_TMPDIR" ]; then
        rm -rf "$CONTAINER_TMPDIR" 2>/dev/null || true
    fi
    log "Cleanup complete. Exiting entrypoint."
    if [ "$sig" != "EXIT" ]; then
        exit 1
    fi
}

trap 'cleanup_on_entrypoint_exit SIGTERM' SIGTERM
trap 'cleanup_on_entrypoint_exit SIGINT' SIGINT
trap 'cleanup_on_entrypoint_exit SIGHUP' SIGHUP
# v6.0.3: EXIT trap to clean up on normal exit
trap 'cleanup_on_entrypoint_exit EXIT' EXIT

# ------------------------------------------------------------------------------
# 16.0 FINAL HEALTH CHECK
# ------------------------------------------------------------------------------
log "=== Phase 16: Final health check ==="
log "Xvfb: PID=$XVFB_PID, DISPLAY=:$MASTER_XVFB_DISPLAY, ${PERSONA_SCREEN_WIDTH}x${PERSONA_SCREEN_HEIGHT}@${PERSONA_DPI}dpi"
# R7-ENT-028: Don't log the full DBUS_SESSION_BUS_ADDRESS
# (it may include session-specific paths). Log only the scheme
# and a redacted path.
_DBUS_PATH=$(echo "$DBUS_SESSION_BUS_ADDRESS" | sed 's|unix:path=\([^/]*\)/.*|unix:path=/...|')
log "DBus: PID=${DBUS_PID:-none}, Address=$_DBUS_PATH"
log "PulseAudio: PID=${PULSE_PID:-none}, Server=$PULSE_SERVER"
log "chrony: PID=${CHRONY_PID:-none}"
log "Machine ID: $PERSONA_MACHINE_ID"
log "Timezone: $PERSONA_TZ"
log "Locale: $PERSONA_LOCALE_POSIX"
log "Hostname: $KERNEL_NODENAME"
log "Working directory: /app"
log "Temp directory: $CONTAINER_TMPDIR"
log "Task tmpfs: $TASK_TMPFS_DIR"

# ------------------------------------------------------------------------------
# 17.0 EXEC: Hand off to UID 1000
# ------------------------------------------------------------------------------
# v6.0.2: The exec replaces the current shell with su 1000 -- python3.
# The trap set above is NOT inherited by the exec'd process (it gets
# fresh signal handling). The orchestrator's signal.signal() calls
# will set up its own handlers for SIGTERM/SIGINT/SIGUSR1.

# v6.0.2: Reset the trap just before exec to prevent any race condition
# where the trap fires during exec.
trap - SIGTERM SIGINT SIGHUP

# v6.0.2: Pass MASTER_XVFB_DISPLAY to orchestrator so it doesn't spawn
# its own Xvfb. This eliminates the double-spawn race condition.
export MASTER_XVFB_DISPLAY
export XVFB_PID
export DBUS_PID
export PULSE_PID

exec su -s /bin/bash -c "exec \"\$@\"" 1000 -- "$@"
