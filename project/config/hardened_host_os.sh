#!/bin/bash
# hardened_host_os.sh v6.0 - Debian 12 Host OS Hardening
# =============================================================================
# Maximum Invisibility Profile. Optimized for:
#   - 1 concurrent persona (MAX_CONCURRENT_PERSONAS=1)
#   - 1-persona ephemeral in-memory profile layout
#   - CloakBrowser (C++) + prototype_hardener.js (JS)
#   - Targets: Google reCAPTCHA Enterprise, Cloudflare Turnstile, Akamai,
#     PerimeterX, Chrome/Google account login tracking on Reddit/Quora/Medium
#
# v6.0 ARCHITECTURAL CLARIFICATION:
# =============================================================================
# This script configures the HOST (Debian 12) operating system. The host's
# kernel-level TCP stack, network interfaces, and system files should
# reflect REAL LINUX/DEBIAN BEHAVIOR, not Windows.
#
# Why? Because:
#   1. The host is actually Debian 12 (Linux kernel). A Linux host
#      configured to emit Windows-style TCP packets creates an
#      impossible/improbable fingerprint that anti-bot scripts
#      can detect.
#   2. The "persona" is the BROWSER's claimed OS (Windows 10/11), not
#      the host. CloakBrowser's C++ layer + prototype_hardener.js
#      handle the persona's Windows fingerprint at the browser level.
#   3. The container reads MOCK files (cpuinfo, dmi_id, boot_id,
#      osrelease) via bind-mount, so the browser sees persona-consistent
#      values regardless of what the host actually has.
#
# The host's job is to:
#   - Have a stable, realistic Linux/Debian kernel profile
#   - Provide sufficient entropy for TLS/ASLR
#   - Mock persona files for container bind-mount
#   - Apply network hardening (iptables, scrubbing) without
#     changing the kernel's TCP signature
#   - Hide virtualization/host detection vectors
#
# What this script does NOT do (now correctly):
#   - Set TCP sysctls to Windows values (was v5.0+ mistake)
#   - Try to make the host look like Windows at kernel level
#
# CHANGELOG (v5.0+ -> v6.0):
#   L1 - REMOVED all Windows-mimic sysctls (was v5.0+ error). Host is
#        Debian 12; it should look like Debian 12 to anyone probing
#        the host's TCP stack.
#   L2 - Replaced with Debian 12 / Linux 5.10+ defaults (or hardened
#        variants that match Linux defaults).
#   L3 - Kept all mock files (cpuinfo, dmi_id, boot_id, osrelease) —
#        these are the persona-consistent values the container reads.
#   L4 - Kept entropy, ethtool offload, packet scrubbing, GRUB
#        cmdline, hostname anonymization, machine-id spoofing.
#   L5 - Documented the namespace boundary clearly: the host's
#        sysctls DO NOT propagate to the container's net.* namespace.
#        The container sets its own (which is also Linux/Debian-style,
#        since the container is Linux).
# =============================================================================

# Set umask early (B46)
umask 022

# A1: Strict error handling — exit on any error, undefined variable, or pipe failure
set -euo pipefail

# Enforce root privileges
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)." >&2
    exit 1
fi

# Detect chroot (B47)
if [ -d /proc/1/root ] && [ "$(stat -c %i /proc/1/root 2>/dev/null)" != "$(stat -c %i / 2>/dev/null)" ]; then
    echo "ERROR: This script appears to be running inside a chroot. Aborting." >&2
    exit 1
fi

# Verify Debian 12 (B44)
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" != "debian" ] || [ "${VERSION_ID:-}" != "12" ]; then
        echo "WARNING: This script is designed for Debian 12 Bookworm."
        echo "WARNING: Detected: ${PRETTY_NAME:-unknown}"
        if [ "${ALLOW_NON_DEBIAN:-0}" != "1" ]; then
            echo "ERROR: Set ALLOW_NON_DEBIAN=1 to override." >&2
            exit 1
        fi
    fi
fi

# Single-instance lockfile (B26)
LOCK_FILE="/run/hardened_host_os_v6.lock"
if [ -e "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "ERROR: Another instance of hardened_host_os.sh is running (PID $OLD_PID)." >&2
        exit 1
    fi
    rm -f "$LOCK_FILE"
fi
echo "$$" > "$LOCK_FILE"

#-------------------------------------------------------------------------
# 0. Logging redirection (B23-B25)
#-------------------------------------------------------------------------
LOG_FILE="${HARDENED_LOG_FILE:-/run/hardened_host_os_v6.log}"

if ! ( : > "$LOG_FILE.test" ) 2>/dev/null; then
    echo "WARNING: Log file $LOG_FILE not writable. Falling back to /dev/null."
    LOG_FILE="/dev/null"
fi
rm -f "$LOG_FILE.test" 2>/dev/null || true
: > "$LOG_FILE" 2>/dev/null || true

exec > >(tee "$LOG_FILE") 2>&1

# Track per-section success/failure (B26, B41)
FAIL_COUNTER=0
WARN_COUNTER=0
SECTION_TOTAL=0
SECTION_PASSED=0
current_section=""

mark_pass() {
    SECTION_TOTAL=$((SECTION_TOTAL + 1))
    SECTION_PASSED=$((SECTION_PASSED + 1))
    echo "[PASS] $current_section"
}

mark_fail() {
    SECTION_TOTAL=$((SECTION_TOTAL + 1))
    FAIL_COUNTER=$((FAIL_COUNTER + 1))
    echo "[FAIL] $current_section -- $1"
}

mark_warn() {
    WARN_COUNTER=$((WARN_COUNTER + 1))
    echo "[WARN] $current_section -- $1"
}

TMP_FILES=()
cleanup() {
    local rc=$?
    rm -f "${TMP_FILES[@]}" 2>/dev/null || true
    rm -f "$LOCK_FILE" 2>/dev/null || true
    if [ $rc -ne 0 ] && [ "$FAIL_COUNTER" -gt 0 ]; then
        echo ""
        echo "ERROR: Script exited with $FAIL_COUNTER failed section(s)."
    fi
    exit $rc
}
trap cleanup EXIT INT TERM

register_tmp() {
    local f
    f="$(mktemp "$1")"
    TMP_FILES+=("$f")
    echo "$f"
}

# A4: Helper to make mock files immutable (prevent tampering by
# package managers or attackers). Only works on filesystems that
# support chattr +i (ext4, xfs — not tmpfs).
apply_immutable() {
    if command -v chattr >/dev/null 2>&1; then
        chattr +i "$1" 2>/dev/null || true
    fi
}

# C18 fix: apply_immutable() freezes these files with `chattr +i`, but
# nothing ever removed that flag. A second run of this script therefore
# failed in a silent and confusing way: `rm -f` and `mv -f` both fail on
# an immutable file, so the old value survived, the new value was never
# installed, and mark_fail() reported a provisioning error.
#
# That mattered more than it looks. Anyone cloning or reimaging a
# provisioned host inherits the SAME frozen boot_id, cpuinfo, version,
# osrelease and DMI on every copy - so "spin up a fresh host" did not
# produce a fresh identity.
#
# remove_immutable() is the inverse of apply_immutable(). Call it before
# removing or replacing any path that an earlier run may have frozen.
remove_immutable() {
    if command -v chattr >/dev/null 2>&1; then
        chattr -i "$1" 2>/dev/null || true
    fi
}

# C18 fix: same, for a directory whose *contents* were frozen. The DMI
# mock is a directory and each file inside it is individually immutable,
# so `rm -rf` on the directory fails until every child is unfrozen.
remove_immutable_tree() {
    [ -e "$1" ] || return 0
    if [ -d "$1" ]; then
        for _rmi_f in "$1"/*; do
            [ -e "$_rmi_f" ] && remove_immutable "$_rmi_f"
        done
    fi
    remove_immutable "$1"
}

APT_FAILED=0

echo "=========================================================================="
echo "  HOST OS HARDENING v6.0 (DEBIAN 12 HOST, 1-PERSONA MAX INVISIBILITY)    "
echo "  HOST KERNEL: Linux/Debian 12 (real, not spoofed)                       "
echo "  PERSONA OS: Windows 10/11 (spoofed by CloakBrowser + prototype_hardener)"
echo "  LOG: $LOG_FILE                                                          "
echo "=========================================================================="

# Configurable target timezone (B8)
: "${HOST_TZ:=America/New_York}"
export HOST_TZ
export DEBIAN_FRONTEND=noninteractive

# UUID for /etc/mock_boot_id. ACCURACY (C15): generated once per HOST and
# then frozen, not per persona. Every container that mounts the resulting
# file sees this same value for the life of the host.
: "${PERSONA_UUID:=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "00000000-0000-0000-0000-000000000000")}"
export PERSONA_UUID

# Detect systemd (B13, B47)
SYSTEMD_RUNNING=0
if [ -r /proc/1/comm ] && [ "$(cat /proc/1/comm 2>/dev/null)" = "systemd" ]; then
    SYSTEMD_RUNNING=1
elif pidof systemd >/dev/null 2>&1; then
    SYSTEMD_RUNNING=1
fi
echo "[*] systemd running: $SYSTEMD_RUNNING"

HOST_ARCH="$(uname -m)"
echo "[*] Host architecture: $HOST_ARCH"

KERNEL_VERSION="$(uname -r)"
echo "[*] Host kernel: $KERNEL_VERSION (Debian 12 default: 6.1.x)"

#-------------------------------------------------------------------------
# 0b. Single APT cache refresh (Debian repositories)
#-------------------------------------------------------------------------
current_section="0b. APT cache refresh (Debian 12 repositories)"
echo "[*] Refreshing APT cache from Debian 12 repositories..."
if apt-get update -o Acquire::Languages=none -o Acquire::http::No-Cache=true -y 2>&1 | tail -5; then
    mark_pass
else
    mark_warn "apt-get update failed; subsequent installs may fail"
    APT_FAILED=1
fi

#-------------------------------------------------------------------------
# 1. Hostname Anonymization (realistic Debian hostname pattern)
#-------------------------------------------------------------------------
current_section="1. Hostname Anonymization"
# Use a realistic Debian-style hostname. Real Debian systems commonly
# use names like "debian", "workstation", or custom names with hyphens.
# Avoid "DESKTOP-XXXXXXX" (Windows pattern) — that would be inconsistent
# with a Debian host.
SPOOFED_HOSTNAME="debian-$(tr -dc 'a-z0-9' </dev/urandom | head -c 8)"
echo "[*] Setting hostname to: $SPOOFED_HOSTNAME"

hostname "$SPOOFED_HOSTNAME" 2>/dev/null || mark_warn "hostname(1) failed"
if [ "$SYSTEMD_RUNNING" = "1" ] && command -v hostnamectl >/dev/null 2>&1; then
    hostnamectl set-hostname "$SPOOFED_HOSTNAME" 2>/dev/null || mark_warn "hostnamectl failed"
fi

if [ -w /etc/hosts ]; then
    if grep -qE '^127\.0\.1\.1\b' /etc/hosts 2>/dev/null; then
        sed -i.bak -E "s|^127\\.0\\.1\\.1\\b.*|127.0.1.1\\t${SPOOFED_HOSTNAME}|" /etc/hosts && \
            rm -f /etc/hosts.bak
    else
        printf '127.0.1.1\t%s\n' "$SPOOFED_HOSTNAME" >> /etc/hosts
    fi
    mark_pass
else
    mark_warn "/etc/hosts not writable"
fi

#-------------------------------------------------------------------------
# 2. Spoof Unique Machine-ID (Debian uses systemd machine-id)
#-------------------------------------------------------------------------
current_section="2. Machine ID Spoof"
echo "[*] Regenerating randomized Machine ID..."

if ! command -v dbus-uuidgen >/dev/null 2>&1 && [ "$APT_FAILED" = "0" ]; then
    apt-get install -y dbus 2>/dev/null || APT_FAILED=1
fi

NEW_MACHINE_ID=""
if command -v dbus-uuidgen >/dev/null 2>&1; then
    NEW_MACHINE_ID="$(dbus-uuidgen 2>/dev/null | tr -d '[:space:]')"
fi
if [ -z "$NEW_MACHINE_ID" ] || [ "${#NEW_MACHINE_ID}" -ne 32 ]; then
    NEW_MACHINE_ID="$(tr -dc 'a-f0-9' </dev/urandom | head -c 32)"
fi
echo "[*] New machine-id: $NEW_MACHINE_ID"

if [ -w /etc/machine-id ] && [ -w /var/lib/dbus/machine-id ]; then
    printf '%s\n' "$NEW_MACHINE_ID" > /etc/machine-id
    printf '%s\n' "$NEW_MACHINE_ID" > /var/lib/dbus/machine-id
    mark_pass
else
    mark_fail "machine-id files not writable"
fi

#-------------------------------------------------------------------------
# 3. Debian 12 Telemetry Purge
#-------------------------------------------------------------------------
current_section="3. Telemetry Purge"
if [ "$APT_FAILED" = "0" ]; then
    apt-get purge -y popularity-contest 2>/dev/null && mark_pass || mark_warn "popularity-contest not installed"
else
    mark_warn "Skipping due to APT failure"
fi

#-------------------------------------------------------------------------
# 4. Generate Pristine Locale Configurations
#-------------------------------------------------------------------------
current_section="4. Locale Configuration"
if [ "$APT_FAILED" = "0" ]; then
    apt-get install -y locales 2>/dev/null || APT_FAILED=1
fi
if command -v locale-gen >/dev/null 2>&1; then
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
    if locale-gen 2>/dev/null; then
        update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 2>/dev/null || \
            printf 'LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8\n' > /etc/default/locale
        mark_pass
    else
        mark_warn "locale-gen failed; fallback C locale will be used"
    fi
else
    mark_fail "locale-gen binary missing"
fi

#-------------------------------------------------------------------------
# 5. Apply Kernel Network Stack Alignment (L1, L2 — Linux/Debian defaults)
#-------------------------------------------------------------------------
current_section="5. sysctl Network Stack (Debian 12 / Linux defaults)"

# L1, L5: CRITICAL ARCHITECTURAL NOTE
# These host-level sysctls apply to the HOST kernel. The orchestrator
# container has its own net.* namespace and sets its own sysctls via
# docker-compose.yml. The container does NOT inherit net.* sysctls
# from the host. This is correct behavior.
#
# The persona's Windows fingerprinting is handled by:
#   1. CloakBrowser's C++ layer (modifies navigator.*, V8 engine, etc.)
#   2. prototype_hardener.js (modifies all JS-level APIs)
#   3. Mock /proc, /sys files (read by container via bind-mount)
# NOT by the host kernel's sysctls.
#
# We configure the host's sysctls to match Linux/Debian 12 defaults
# (kernel 6.1.x). The few values we set are hardening-related
# (security improvements that don't change the kernel's signature).

install -m 644 /dev/null /etc/sysctl.d/99-hardened-stealth-v6.conf 2>/dev/null || \
    : > /etc/sysctl.d/99-hardened-stealth-v6.conf

cat > /etc/sysctl.d/99-hardened-stealth-v6.conf <<'EOF'
# ============================================================================
# hardened_host_os.sh v6.0 - Debian 12 / Linux 6.1.x Host Kernel Profile
#
# IMPORTANT: This file configures the HOST (Debian 12) kernel, NOT the
# persona's claimed OS. The persona (Windows 10/11) is spoofed at the
# browser level by CloakBrowser's C++ layer and prototype_hardener.js.
#
# These values match Debian 12 / Linux 6.1.x defaults. The few hardening
# values we set (security-related) don't change the kernel's TCP signature.
# ============================================================================

# ----- TCP Window / Buffer (Linux/Debian defaults) -----
# Linux defaults: rmem_max=212992, wmem_max=212992, tcp_rmem=4096 87380 6291456,
# tcp_wmem=4096 16384 4194304. We keep these as Debian defaults — the host
# IS Linux, so it should look like Linux.
net.core.rmem_max = 212992
net.core.wmem_max = 212992
net.ipv4.tcp_rmem = 4096 87380 6291456
net.ipv4.tcp_wmem = 4096 16384 4194304

# ----- TCP Window Scale (Linux default) -----
# Linux default: tcp_window_scaling=1, tcp_adv_win_scale=1.
# We keep these as Linux defaults. Setting tcp_adv_win_scale=2 would make
# the host look like Windows, which is inconsistent with being Debian.
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_adv_win_scale = 1

# ----- TCP MSS / MTU Probing (Linux default) -----
# Linux default: tcp_mtu_probing=0 in kernels >= 4.4. Keep as default.
net.ipv4.tcp_mtu_probing = 0
# Linux default initial MSS: 1024. We keep this — host is Linux.
# (The persona's claimed 1380 MSS is at the browser level, not the host.)
net.ipv4.tcp_base_mss = 1024

# ----- TCP Slow Start (Linux default) -----
# Linux default: tcp_slow_start_after_idle=1. Keep as default.
net.ipv4.tcp_slow_start_after_idle = 1

# ----- TCP Metrics (Linux default) -----
# Linux default: tcp_no_metrics_save=0 (save per-route metrics). Keep.
net.ipv4.tcp_no_metrics_save = 0

# ----- TCP ECN (Linux default) -----
# Linux default: tcp_ecn=1. Keep.
net.ipv4.tcp_ecn = 1

# ----- TCP Timestamps (Linux default) -----
# Linux default: tcp_timestamps=1. Keep.
# (Disabling timestamps would make the host look like Windows, which
# is inconsistent with being Debian.)
net.ipv4.tcp_timestamps = 1

# ----- TCP SACK / DSACK / FACK (Linux defaults) -----
# Linux default: all enabled. Keep.
net.ipv4.tcp_sack = 1
net.ipv4.tcp_dsack = 1
net.ipv4.tcp_fack = 1

# ----- TCP TIME_WAIT / Tw_reuse (Linux defaults) -----
# Linux default: tcp_tw_reuse=2. Keep.
net.ipv4.tcp_tw_reuse = 2
# Safety net for TIME_WAIT bucket overflow
net.ipv4.tcp_max_tw_buckets = 32768

# ----- TCP F-RTO (Linux default) -----
# Linux default: tcp_frto=0. Keep.
net.ipv4.tcp_frto = 0

# ----- TCP Reordering / App Win / Low Latency (Linux defaults) -----
# Linux defaults: tcp_reordering=3, tcp_app_win=31, tcp_low_latency=0.
net.ipv4.tcp_reordering = 3
net.ipv4.tcp_app_win = 31
net.ipv4.tcp_low_latency = 0

# ----- TCP Retries (Linux defaults) -----
# Linux defaults: tcp_syn_retries=6, tcp_synack_retries=5,
# tcp_orphan_retries=0, tcp_retries2=15.
net.ipv4.tcp_syn_retries = 6
net.ipv4.tcp_synack_retries = 5
net.ipv4.tcp_orphan_retries = 0
net.ipv4.tcp_retries2 = 15
# Linux default: tcp_fin_timeout=60. Keep.
net.ipv4.tcp_fin_timeout = 60

# ----- TCP Delayed ACK segments (Linux default) -----
# Linux default: tcp_delack_seg=0. Keep.
net.ipv4.tcp_delack_seg = 0

# ----- TCP Fast Open (Linux default) -----
# Linux default: tcp_fastopen=1 (enabled, but most systems disable it).
# Keep as Linux default.
net.ipv4.tcp_fastopen = 1
net.ipv4.tcp_fastopen_blackhole_timeout_sec = 0

# ----- TCP Pacing ratios (Linux defaults) -----
# Linux defaults: tcp_pacing_ss_ratio=200, tcp_pacing_ca_ratio=120.
net.ipv4.tcp_pacing_ss_ratio = 200
net.ipv4.tcp_pacing_ca_ratio = 120

# ----- TCP Workaround Signed Windows (Linux default) -----
# Linux default: tcp_workaround_signed_windows=0. Keep.
net.ipv4.tcp_workaround_signed_windows = 0

# ----- TCP Initial Congestion Window (Linux default) -----
# Linux default: tcp_init_cwnd=10 (RFC 6928 + later bumps).
net.ipv4.tcp_init_cwnd = 10

# ----- TCP Keep-Alive (Linux defaults) -----
# Linux defaults: tcp_keepalive_time=7200, tcp_keepalive_intvl=75,
# tcp_keepalive_probes=9.
net.ipv4.tcp_keepalive_time = 7200
net.ipv4.tcp_keepalive_intvl = 75
net.ipv4.tcp_keepalive_probes = 9

# ----- TCP SYN backlog (Linux defaults) -----
# Linux default: tcp_max_syn_backlog depends on net.core.somaxconn.
net.ipv4.tcp_max_syn_backlog = 4096
# Linux default: somaxconn=4096. Keep.
net.core.somaxconn = 4096

# ----- Ephemeral Port Range (Linux default) -----
# Linux default: 32768-60999. Keep — host IS Linux.
# (The persona's claimed 49152-65535 is at the browser level.)
net.ipv4.ip_local_port_range = 32768 60999

# ----- Default TTL (Linux default) -----
# Linux default: ip_default_ttl=64. Keep — host IS Linux.
# (The persona's claimed TTL=128 is at the browser level, set in
# prototype_hardener.js via the network connection's first hop.)
net.ipv4.ip_default_ttl = 64

# ----- Path MTU Discovery (Linux default) -----
# Linux default: ip_no_pmtu_disc=0. Keep.
net.ipv4.ip_no_pmtu_disc = 0

# C9: net.ipv4.tcp_early_demux = 1 (Debian default, explicit)
net.ipv4.tcp_early_demux = 1

# ----- IPv6 (Linux default: enabled) -----
# Real Debian 12 has IPv6 enabled by default. Disable only if explicitly
# needed (we don't need to — the container handles its own IPv6 policy).
# We leave IPv6 enabled at the host level to match Debian defaults.
# net.ipv6.conf.all.disable_ipv6 = 1  # COMMENTED OUT to match Linux defaults
# net.ipv6.conf.default.disable_ipv6 = 1
# net.ipv6.conf.lo.disable_ipv6 = 1

# ----- Source Routing / Redirects (security hardening — same as before) -----
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0

# ----- Reverse Path Filtering (Linux default: enabled) -----
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# ----- Martian Logging (Linux default: disabled) -----
net.ipv4.conf.all.log_martians = 0
# Linux default: ICMP echo enabled (responds to pings).
net.ipv4.icmp_echo_ignore_all = 0
net.ipv4.icmp_ignore_bogus_error_responses = 1

# ----- RSS / RPS (Linux defaults) -----
net.core.rps_sock_flow_entries = 32768
net.core.rps_flow_cnt = 32768

# ----- busy_poll (Linux defaults: 0) -----
# Real Linux: 0. Windows doesn't have these. Keep as 0 (Linux default).
net.core.busy_poll = 0
net.core.busy_read = 0
net.core.busy_poll_usecs = 0

# ----- conntrack (Linux defaults scaled for 1 persona) -----
net.netfilter.nf_conntrack_max = 262144
net.netfilter.nf_conntrack_buckets = 65536
net.netfilter.nf_conntrack_tcp_timeout_established = 432000
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 120
net.netfilter.nf_conntrack_tcp_timeout_close_wait = 60
net.netfilter.nf_conntrack_tcp_timeout_fin_wait = 120
net.netfilter.nf_conntrack_tcp_timeout_syn_sent = 120
net.netfilter.nf_conntrack_tcp_timeout_syn_recv = 60

# ----- Kernel Hardening (security — doesn't change TCP signature) -----
# H6: kernel.kptr_restrict = 1 (Debian default). Was 2 (more restrictive)
# which deviates from Debian 12 defaults.
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
# C3: kernel.printk = 4 4 1 7 (Debian 12 default) was 3 4 1 3.
kernel.printk = 4 4 1 7
kernel.perf_cpu_time_max_percent = 1
kernel.perf_event_max_sample_rate = 1000
kernel.perf_event_paranoid = 2
# C1: kernel.panic = 30 (closer to server default) was 10.
kernel.panic = 30
# L3: kernel.yama.ptrace_scope = 1 (Debian default) was 2.
kernel.yama.ptrace_scope = 1
# C8: kernel.unprivileged_userns_clone = 1 — needed for Docker containers
# to create user namespaces. Without this, container start may fail.
kernel.unprivileged_userns_clone = 1

# ----- Virtual Memory (Linux defaults for a server, with swappiness tuned) -----
# Linux server default: vm.swappiness=60. For a stealth host, we set
# vm.swappiness=10 to reduce swap usage (less disk I/O fingerprint).
# This is a tuning change, not a fingerprint change — the OS still
# behaves like Linux.
vm.swappiness = 10
vm.dirty_background_ratio = 10
vm.dirty_ratio = 20
# Forensic hardening: erase memory on free (helps with in-memory data
# recovery prevention). Doesn't change the kernel's TCP signature.
vm.erase_memory_on_free = 1

# ----- File System (H7: Debian 12 defaults) -----
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
EOF

# Filter sysctl output to show only warnings/errors
SYSCTL_OUTPUT=$(sysctl --system 2>&1)
SYSCTL_ERRORS=$(echo "$SYSCTL_OUTPUT" | grep -iE 'error|warn|unknown|invalid|fail' || true)
if [ -n "$SYSCTL_ERRORS" ]; then
    echo "$SYSCTL_ERRORS" | head -20
    mark_warn "Some sysctl keys were rejected (see above)"
else
    mark_pass
fi

#-------------------------------------------------------------------------
# 6. Kernel Module Blacklisting (B45)
#-------------------------------------------------------------------------
current_section="6. Kernel Module Blacklist"
cat > /etc/modprobe.d/blacklist-stealth.conf <<'EOF'
blacklist floppy
blacklist parport
blacklist parport_pc
blacklist lp
blacklist joydev
blacklist pcspkr
blacklist snd_pcsp
# B45: Do NOT blacklist virtio_balloon, virtio_pci, virtio_console, or
# virtio_net on OCI - required for VM operation.
blacklist vboxguest
blacklist vmw_ballon
EOF
mark_pass

#-------------------------------------------------------------------------
# 7. Interface MTU & TCP MSS Clamping (host-side for egress)
#-------------------------------------------------------------------------
current_section="7. MTU/MSS Clamping (host-side egress)"
DEFAULT_INTERFACE=$(ip route show 2>/dev/null | awk '/^default/ {print $5; exit}')
if [ -n "$DEFAULT_INTERFACE" ] && [ -e "/sys/class/net/$DEFAULT_INTERFACE" ]; then
    # The host's MTU/MSS only affects the host's own TCP stack
    # (e.g., for DNS queries from the host itself). The container has
    # its own iptables MSS clamp in entrypoint.sh. We set this to
    # Debian defaults (mtu 1500, advmss 1460).
    ROUTE_CMD="ip route change default dev $DEFAULT_INTERFACE mtu 1500 advmss 1460"
    if eval "$ROUTE_CMD" 2>/dev/null; then
        echo "[*] MTU/MSS clamping applied to $DEFAULT_INTERFACE"
        mark_pass
    else
        mark_warn "MTU clamping failed; non-fatal"
    fi

    # ethtool: Real Linux servers often have TSO/GSO/GRO enabled by
    # default. For a stealth host, we keep them ENABLED (matches Linux
    # server defaults). The persona's Windows browser-side offload
    # state is handled at the browser level.
    if command -v ethtool >/dev/null 2>&1; then
        echo "[*] ethtool: TSO/GSO/GRO/SG defaults (Linux server profile)"
        # Don't disable — keep Linux server defaults
    else
        if [ "$APT_FAILED" = "0" ]; then
            apt-get install -y ethtool 2>/dev/null || APT_FAILED=1
        fi
    fi
else
    mark_warn "Default network interface not found"
fi

#-------------------------------------------------------------------------
# 8. Prevent Local DNS Leakages
#-------------------------------------------------------------------------
current_section="8. DNS Hardening"

# NetworkManager dns=none
if [ -d /etc/NetworkManager ]; then
    mkdir -p /etc/NetworkManager/conf.d/
    cat > /etc/NetworkManager/conf.d/stealth-dns.conf <<'EOF'
[main]
dns=none
systemd-resolved=false
EOF
    if [ "$SYSTEMD_RUNNING" = "1" ]; then
        systemctl reload NetworkManager 2>/dev/null || true
    fi
fi

# systemd-resolved disable
if [ "$SYSTEMD_RUNNING" = "1" ] && [ -d /etc/systemd ]; then
    mkdir -p /etc/systemd/resolved.conf.d/
    cat > /etc/systemd/resolved.conf.d/stealth-dns.conf <<'EOF'
[Resolve]
LLMNR=no
MulticastDNS=no
DNSSEC=no
DNSOverTLS=opportunistic
EOF
    systemctl stop systemd-resolved 2>/dev/null || true
    systemctl disable systemd-resolved 2>/dev/null || true
fi

# Static /etc/resolv.conf
if [ -L /etc/resolv.conf ] || [ -e /etc/resolv.conf ]; then
    if [ -L /etc/resolv.conf ]; then
        rm -f /etc/resolv.conf
    fi
    TMP_RESOLV=$(register_tmp "/run/.resolv.conf.XXXXXX")
    cat > "$TMP_RESOLV" <<'EOF'
nameserver 9.9.9.9
nameserver 1.1.1.1
options edns0 trust-ad
EOF
    chmod 644 "$TMP_RESOLV"
    if mv -f "$TMP_RESOLV" /etc/resolv.conf 2>/dev/null; then
        mark_pass
    else
        mark_warn "Could not write static /etc/resolv.conf"
    fi
else
    mark_warn "/etc/resolv.conf missing; cannot harden"
fi

#-------------------------------------------------------------------------
# 8b. systemd Watchdog Deactivation
#-------------------------------------------------------------------------
current_section="8b. systemd Watchdog"
if [ "$SYSTEMD_RUNNING" = "1" ] && [ -w /etc/systemd/system.conf ]; then
    sed -i -E 's|^#?\s*RuntimeWatchdogSec=.*|RuntimeWatchdogSec=0|' /etc/systemd/system.conf
    sed -i -E 's|^#?\s*ShutdownWatchdogSec=.*|ShutdownWatchdogSec=0|' /etc/systemd/system.conf
    mark_pass
elif [ "$SYSTEMD_RUNNING" != "1" ]; then
    echo "[*] Skipping (no systemd)"
    mark_pass
else
    mark_warn "/etc/systemd/system.conf not writable"
fi

#-------------------------------------------------------------------------
# 9. NTP Synchronization (H31)
#-------------------------------------------------------------------------
current_section="9. NTP Synchronization"

# Disable systemd-timesyncd in favor of chrony
if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "systemd-timesyncd"; then
    systemctl stop systemd-timesyncd 2>/dev/null || true
    systemctl disable systemd-timesyncd 2>/dev/null || true
fi

if [ "$APT_FAILED" = "0" ]; then
    apt-get install -y chrony rng-tools5 2>&1 | tail -3 || APT_FAILED=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || echo /home/user/project/config)"
if [ -f "$SCRIPT_DIR/chrony_hardened.conf" ]; then
    cp -f "$SCRIPT_DIR/chrony_hardened.conf" /etc/chrony/chrony.conf
    echo "[*] Loaded chrony_hardened.conf from $SCRIPT_DIR"
else
    mark_warn "chrony_hardened.conf not found at $SCRIPT_DIR"
fi

if [ "$SYSTEMD_RUNNING" = "1" ]; then
    systemctl enable chrony 2>/dev/null || true
    systemctl restart chrony 2>/dev/null || mark_warn "chrony restart failed"
fi

# Drift detection. Real browsers have <50ms drift; real Linux hosts
# with chrony should also have low drift.
if command -v chronyc >/dev/null 2>&1; then
    chronyc -a makestep 2>/dev/null || true
    echo "[*] Waiting for NTP synchronization..."
    for i in 1 2 3 4 5; do
        if chronyc waitsync 30 3.0 95 2 2>/dev/null; then
            echo "[*] Chrony reached synchronization after attempt $i (lenient pass)."
            if chronyc waitsync 10 0.1 95 1 2>/dev/null; then
                echo "[*] Chrony confirmed tight synchronization (<100ms)."
            else
                mark_warn "Chrony tight sync failed; offset may be > 100ms"
            fi
            break
        fi
        sleep 3
    done
    FINAL_OFFSET_US=$(chronyc tracking 2>/dev/null | awk '/System time/ {print $4 * 1000000}')
    if [ -n "$FINAL_OFFSET_US" ]; then
        echo "[*] Final NTP offset: ${FINAL_OFFSET_US} microseconds"
        if awk "BEGIN {exit !($FINAL_OFFSET_US > 50000)}" 2>/dev/null; then
            mark_warn "NTP offset > 50ms; anti-bot Date.now() fingerprint may leak"
        fi
    fi
    mark_pass
else
    mark_warn "chronyc not available"
fi

# Configure timezone
current_section="9b. Timezone Alignment"
ZONEINFO_PATH="/usr/share/zoneinfo/$HOST_TZ"
if [ ! -r "$ZONEINFO_PATH" ]; then
    if [ "$APT_FAILED" = "0" ]; then
        apt-get install -y tzdata 2>&1 | tail -3
    fi
fi
if [ -r "$ZONEINFO_PATH" ]; then
    if [ "$SYSTEMD_RUNNING" = "1" ] && command -v timedatectl >/dev/null 2>&1; then
        timedatectl set-timezone "$HOST_TZ" 2>/dev/null || true
    else
        cp -f "$ZONEINFO_PATH" /etc/localtime
        echo "$HOST_TZ" > /etc/timezone
    fi
    mark_pass
else
    mark_fail "$ZONEINFO_PATH not found"
fi

#-------------------------------------------------------------------------
# 10. Saturated Entropy Pool
#-------------------------------------------------------------------------
current_section="10. Entropy Pool (haveged + rngd)"

if [ "$APT_FAILED" = "0" ]; then
    apt-get install -y haveged rng-tools5 2>/dev/null || APT_FAILED=1
fi

if [ "$SYSTEMD_RUNNING" = "1" ]; then
    systemctl enable haveged 2>/dev/null || true
    systemctl start haveged 2>/dev/null || true

    if [ -e /dev/hwrng ]; then
        systemctl enable rng-tools 2>/dev/null || true
        systemctl start rng-tools 2>/dev/null || true
    else
        echo "[*] /dev/hwrng not present; rngd skipped (haveged only)"
    fi
    mark_pass
elif [ -x /usr/sbin/haveged ]; then
    /usr/sbin/haveged --Foreground --verbose=0 2>/dev/null &
    sleep 1
    mark_pass
else
    mark_warn "haveged not installed and not running"
fi

# Verify entropy pool is full
sleep 2
ENTROPY_BITS=$(cat /proc/sys/kernel/random/entropy_avail 2>/dev/null || echo 0)
echo "[*] Entropy pool: $ENTROPY_BITS bits"
if [ "$ENTROPY_BITS" -lt 1000 ]; then
    mark_warn "Entropy pool low ($ENTROPY_BITS bits); TLS handshakes may stall"
fi

# Early entropy injection (addrandom equivalent)
if [ -r /var/lib/systemd/random-seed ]; then
    if command -v systemd-random-seed &>/dev/null; then
        systemd-random-seed load 2>/dev/null || true
    fi
fi
if [ -w /dev/urandom ]; then
    dd if=/dev/urandom of=/dev/urandom bs=1 count=32 2>/dev/null || true
fi
if [ -x /usr/sbin/rngd ] && [ -e /dev/hwrng ]; then
    /usr/sbin/rngd -f -r /dev/hwrng 2>/dev/null &
    sleep 1
fi

#-------------------------------------------------------------------------
# 10b. CPU Microcode Alignment
#-------------------------------------------------------------------------
current_section="10b. CPU Microcode"
if [ "$HOST_ARCH" = "x86_64" ]; then
    if [ "$APT_FAILED" = "0" ]; then
        if grep -q "Intel" /proc/cpuinfo 2>/dev/null; then
            apt-get install -y intel-microcode 2>/dev/null && mark_pass || mark_warn "intel-microcode install failed"
        elif grep -q "AMD" /proc/cpuinfo 2>/dev/null; then
            apt-get install -y amd64-microcode 2>/dev/null && mark_pass || mark_warn "amd64-microcode install failed"
        else
            mark_warn "CPU vendor not recognized"
        fi
    else
        mark_warn "Skipped due to APT failure"
    fi
else
    echo "[*] $HOST_ARCH: microcode managed by firmware"
    mark_pass
fi

#-------------------------------------------------------------------------
# 10c. Mock CPU Info (per-persona — container reads this via bind-mount)
#-------------------------------------------------------------------------
current_section="10c. Mock CPU Info (persona-consistent)"
# The container reads /etc/mock_cpuinfo via bind-mount as /proc/cpuinfo.
# This is the persona's claimed CPU, NOT the host's actual CPU.
# Format: Intel i7-8559U (4 cores, 8 threads) — matches the persona's
# hardwareConcurrency=4 in prototype_hardener.js.
# C18: unfreeze before removing. On a re-run this file carries chattr +i
# from apply_immutable() and `rm` would fail silently, leaving the old
# CPU model in place.
remove_immutable /etc/mock_cpuinfo
if [ -d /etc/mock_cpuinfo ]; then
    rm -rf /etc/mock_cpuinfo || { mark_fail "Cannot remove existing /etc/mock_cpuinfo"; }
elif [ -L /etc/mock_cpuinfo ]; then
    rm -f /etc/mock_cpuinfo
fi

TMP_MOCK_CPU=$(register_tmp "/run/.mock_cpuinfo.XXXXXX")
cat > "$TMP_MOCK_CPU" <<'MOCKCPU'
processor	: 0
vendor_id	: GenuineIntel
cpu family	: 6
model		: 158
model name	: Intel(R) Core(TM) i7-8559U CPU @ 2.70GHz
stepping	: 10
microcode	: 0xffffffff
cpu MHz		: 2700.000
cache size	: 8192 KB
physical id	: 0
siblings	: 8
core id		: 0
cpu cores	: 4
apicid		: 0
initial apicid	: 0
fpu		: yes
fpu_exception	: yes
cpuid level	: 22
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault invpcid_single pti ssbd ibrs ibpb stibp tpr_shadow vnmi flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp
bogomips	: 5400.00
clflush size	: 64
cache_alignment	: 64
address sizes	: 39 bits physical, 48 bits virtual
power management:

processor	: 1
vendor_id	: GenuineIntel
cpu family	: 6
model		: 158
model name	: Intel(R) Core(TM) i7-8559U CPU @ 2.70GHz
stepping	: 10
microcode	: 0xffffffff
cpu MHz		: 2700.000
cache size	: 8192 KB
physical id	: 0
siblings	: 8
core id		: 1
cpu cores	: 4
apicid		: 2
initial apicid	: 2
fpu		: yes
fpu_exception	: yes
cpuid level	: 22
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault invpcid_single pti ssbd ibrs ibpb stibp tpr_shadow vnmi flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp
bogomips	: 5400.00
clflush size	: 64
cache_alignment	: 64
address sizes	: 39 bits physical, 48 bits virtual
power management:

processor	: 2
vendor_id	: GenuineIntel
cpu family	: 6
model		: 158
model name	: Intel(R) Core(TM) i7-8559U CPU @ 2.70GHz
stepping	: 10
microcode	: 0xffffffff
cpu MHz		: 2700.000
cache size	: 8192 KB
physical id	: 0
siblings	: 8
core id		: 2
cpu cores	: 4
apicid		: 4
initial apicid	: 4
fpu		: yes
fpu_exception	: yes
cpuid level	: 22
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault invpcid_single pti ssbd ibrs ibpb stibp tpr_shadow vnmi flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp
bogomips	: 5400.00
clflush size	: 64
cache_alignment	: 64
address sizes	: 39 bits physical, 48 bits virtual
power management:

processor	: 3
vendor_id	: GenuineIntel
cpu family	: 6
model		: 158
model name	: Intel(R) Core(TM) i7-8559U CPU @ 2.70GHz
stepping	: 10
microcode	: 0xffffffff
cpu MHz		: 2700.000
cache size	: 8192 KB
physical id	: 0
siblings	: 8
core id		: 3
cpu cores	: 4
apicid		: 6
initial apicid	: 6
fpu		: yes
fpu_exception	: yes
cpuid level	: 22
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault invpcid_single pti ssbd ibrs ibpb stibp tpr_shadow vnmi flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp
bogomips	: 5400.00
clflush size	: 64
cache_alignment	: 64
address sizes	: 39 bits physical, 48 bits virtual
power management:
MOCKCPU
chmod 444 "$TMP_MOCK_CPU"

if mv -f "$TMP_MOCK_CPU" /etc/mock_cpuinfo 2>/dev/null && [ -f /etc/mock_cpuinfo ] && [ ! -d /etc/mock_cpuinfo ]; then
    SIZE=$(stat -c %s /etc/mock_cpuinfo 2>/dev/null || echo 0)
    if [ "$SIZE" -ge 1000 ]; then
        apply_immutable /etc/mock_cpuinfo
        mark_pass
    else
        mark_fail "mock_cpuinfo too small: $SIZE bytes"
    fi
else
    mark_fail "Could not create /etc/mock_cpuinfo"
fi

#-------------------------------------------------------------------------
# 10d. Mock Kernel Version (per-persona — container reads as /proc/version)
#-------------------------------------------------------------------------
current_section="10d. Mock Kernel Version (persona-consistent)"
# The persona claims Windows. The container will see this as its
# /proc/version when the orchestrator's entrypoint.sh bind-mounts
# /etc/mock_version over it.
# C18: unfreeze before removing.
remove_immutable /etc/mock_version
if [ -d /etc/mock_version ] || [ -L /etc/mock_version ]; then
    rm -rf /etc/mock_version
fi

TMP_MOCK_VER=$(register_tmp "/run/.mock_version.XXXXXX")
cat > "$TMP_MOCK_VER" <<'MOCKVER'
Linux version 4.4.0-19041-Microsoft (Microsoft@Microsoft.com) (gcc version 5.4.0 (GCC) ) #1237-Microsoft Sat Sep 11 13:32:00 PST 2021
MOCKVER
chmod 444 "$TMP_MOCK_VER"

if mv -f "$TMP_MOCK_VER" /etc/mock_version 2>/dev/null && [ -f /etc/mock_version ] && [ ! -d /etc/mock_version ]; then
    SIZE=$(stat -c %s /etc/mock_version 2>/dev/null || echo 0)
    if [ "$SIZE" -ge 80 ]; then
        apply_immutable /etc/mock_version
        mark_pass
    else
        mark_fail "mock_version too small: $SIZE bytes"
    fi
else
    mark_fail "Could not create /etc/mock_version"
fi

#-------------------------------------------------------------------------
# 10d2. Mock DMI ID (per-persona — container reads as /sys/class/dmi/id)
#-------------------------------------------------------------------------
current_section="10d2. Mock DMI ID (persona-consistent)"
# Real Windows desktop DMI strings. The persona claims to be a
# Dell XPS 13 9370 (a real consumer Windows laptop).
# C18: every file inside is frozen individually by apply_immutable(), so
# `rm -rf` on the directory fails until the whole tree is unfrozen.
remove_immutable_tree /etc/mock_dmi_id
if [ -d /etc/mock_dmi_id ]; then
    rm -rf /etc/mock_dmi_id
fi

mkdir -p /etc/mock_dmi_id

DMI_PRODUCT_UUID="4c4c4544-$(printf '%04x' $((RANDOM % 65536)))-$(printf '%04x' $((RANDOM % 65536)))-8048-b2c04f${RANDOM}$(printf '%04x' $((RANDOM % 65536)))"
DMI_PRODUCT_SERIAL="$(tr -dc 'A-Z0-9' </dev/urandom | head -c 7)"

TMP_MOCK_DMI_DIR=$(register_tmp -d "/run/.mock_dmi.XXXXXX")
cat > "$TMP_MOCK_DMI_DIR/sys_vendor" <<'EOF'
Dell Inc.
EOF
cat > "$TMP_MOCK_DMI_DIR/product_name" <<'EOF'
XPS 13 9370
EOF
cat > "$TMP_MOCK_DMI_DIR/product_version" <<'EOF'
1.0
EOF
cat > "$TMP_MOCK_DMI_DIR/product_serial" <<EOF
${DMI_PRODUCT_SERIAL}
EOF
cat > "$TMP_MOCK_DMI_DIR/product_uuid" <<EOF
${DMI_PRODUCT_UUID}
EOF
cat > "$TMP_MOCK_DMI_DIR/board_vendor" <<'EOF'
Dell Inc.
EOF
cat > "$TMP_MOCK_DMI_DIR/board_name" <<'EOF'
0KHN0P
EOF
cat > "$TMP_MOCK_DMI_DIR/board_version" <<'EOF'
A00
EOF
cat > "$TMP_MOCK_DMI_DIR/bios_vendor" <<'EOF'
Dell Inc.
EOF
cat > "$TMP_MOCK_DMI_DIR/bios_version" <<'EOF'
1.11.0
EOF
cat > "$TMP_MOCK_DMI_DIR/bios_date" <<'EOF'
09/14/2021
EOF
cat > "$TMP_MOCK_DMI_DIR/chassis_vendor" <<'EOF'
Dell Inc.
EOF
cat > "$TMP_MOCK_DMI_DIR/chassis_type" <<'EOF'
9
EOF
cat > "$TMP_MOCK_DMI_DIR/chassis_version" <<'EOF'
1.0
EOF

chmod -R 444 "$TMP_MOCK_DMI_DIR"
if cp -r "$TMP_MOCK_DMI_DIR"/. /etc/mock_dmi_id/ 2>/dev/null; then
    if [ -f /etc/mock_dmi_id/product_uuid ] && [ -d /etc/mock_dmi_id ]; then
        # A4: Make all DMI files immutable
        for dmi_file in /etc/mock_dmi_id/*; do
            [ -f "$dmi_file" ] && apply_immutable "$dmi_file"
        done
        mark_pass
        echo "[*]   DMI Product UUID: $DMI_PRODUCT_UUID"
        echo "[*]   DMI Product Serial: $DMI_PRODUCT_SERIAL"
    else
        mark_fail "DMI ID files not created"
    fi
else
    mark_fail "Could not create /etc/mock_dmi_id"
fi

#-------------------------------------------------------------------------
# 10d3. Mock boot_id (container reads as /proc/sys/kernel/random/boot_id)
#-------------------------------------------------------------------------
current_section="10d3. Mock boot_id"
# ACCURACY (C15): this script runs ONCE at host provisioning. It writes
# a single value to /etc/mock_boot_id, makes it immutable, and every
# container bind-mounts that same file read-only. It is therefore a
# host-wide constant, NOT a per-persona or per-session value, and it
# never changes. The container cannot rewrite it (ro mount + chattr +i).
# C18: unfreeze before removing. This is the one that silently kept the
# OLD value on every re-run, which is why cloned hosts all shared a boot_id.
remove_immutable /etc/mock_boot_id
if [ -f /etc/mock_boot_id ]; then
    rm -f /etc/mock_boot_id
fi

TMP_MOCK_BOOT=$(register_tmp "/run/.mock_boot_id.XXXXXX")
printf '%s\n' "$PERSONA_UUID" > "$TMP_MOCK_BOOT"
chmod 444 "$TMP_MOCK_BOOT"

if mv -f "$TMP_MOCK_BOOT" /etc/mock_boot_id 2>/dev/null && [ -f /etc/mock_boot_id ]; then
    apply_immutable /etc/mock_boot_id
    echo "[*]   Mock boot_id: $PERSONA_UUID"
    mark_pass
else
    mark_fail "Could not create /etc/mock_boot_id"
fi

#-------------------------------------------------------------------------
# 10d4. Mock osrelease (host-level constant — container reads it as
# /proc/sys/kernel/osrelease). ACCURACY (C15): not per-persona; written
# once here, frozen, and shared read-only by every container on this host.
#-------------------------------------------------------------------------
current_section="10d4. Mock osrelease (persona-consistent)"
# C18: unfreeze before removing.
remove_immutable /etc/mock_osrelease
if [ -f /etc/mock_osrelease ]; then
    rm -f /etc/mock_osrelease
fi

TMP_MOCK_OSREL=$(register_tmp "/run/.mock_osrelease.XXXXXX")
cat > "$TMP_MOCK_OSREL" <<'EOF'
4.4.0-19041-Microsoft
EOF
chmod 444 "$TMP_MOCK_OSREL"

if mv -f "$TMP_MOCK_OSREL" /etc/mock_osrelease 2>/dev/null && [ -f /etc/mock_osrelease ]; then
    apply_immutable /etc/mock_osrelease
    mark_pass
else
    mark_fail "Could not create /etc/mock_osrelease"
fi

#-------------------------------------------------------------------------
# 10e. Disable Transparent Hugepages
#-------------------------------------------------------------------------
current_section="10e. THP Disable"
THP_OK=0
if [ -f /sys/kernel/mm/transparent_hugepage/enabled ]; then
    echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null && THP_OK=1
fi
if [ -f /sys/kernel/mm/transparent_hugepage/defrag ]; then
    echo never > /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null && THP_OK=1
fi
if [ "$THP_OK" = "1" ]; then
    mark_pass
else
    mark_warn "THP sysfs not writable"
fi

#-------------------------------------------------------------------------
# 11. Forensic Hardening: Core Dumps, Auto Updates, rsyslog
#-------------------------------------------------------------------------
current_section="11. Forensic Hardening"
ulimit -c 0
if [ -w /etc/security/limits.conf ] && ! grep -qE '^\*\s+hard\s+core\s+0' /etc/security/limits.conf 2>/dev/null; then
    printf '\n* hard core 0\n* soft core 0\n' >> /etc/security/limits.conf
fi

if [ "$SYSTEMD_RUNNING" = "1" ] && [ -d /etc/systemd ]; then
    mkdir -p /etc/systemd/coredump.conf.d/
    cat > /etc/systemd/coredump.conf.d/stealth-coredump.conf <<'EOF'
[Coredump]
Storage=none
ProcessSizeMax=0
ExternalSizeMax=0
EOF
    systemctl daemon-reload 2>/dev/null || true
fi

# Disable periodic auto-updates
if [ -d /etc/apt/apt.conf.d ]; then
    cat > /etc/apt/apt.conf.d/10periodic <<'EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
EOF
fi

disable_service() {
    local svc="$1"
    if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "$svc"; then
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        systemctl mask "$svc" 2>/dev/null || true
    fi
}

disable_service unattended-upgrades
disable_service kdump-tools.service
disable_service apt-daily.timer
disable_service apt-daily-upgrade.timer
disable_service rsyslog.service
disable_service syslog.socket

rm -rf /var/log/syslog* /var/log/auth.log* /var/log/mail.* /var/log/kern.log* /var/log/daemon.log* /var/log/messages* 2>/dev/null || true
if [ "$APT_FAILED" = "0" ]; then
    apt-get purge -y rsyslog 2>/dev/null || true
fi

# Hard-disable persistent journal storage
if [ "$SYSTEMD_RUNNING" = "1" ] && [ -d /etc/systemd/journald.conf.d ]; then
    cat > /etc/systemd/journald.conf.d/stealth-volatile.conf <<'EOF'
[Journal]
Storage=none
ForwardToSyslog=no
ForwardToKMsg=no
ForwardToConsole=no
EOF
    systemctl restart systemd-journald 2>/dev/null || true
fi
mark_pass

#-------------------------------------------------------------------------
# 12. SSH Server Version Banner & HASSH Hardening
#-------------------------------------------------------------------------
current_section="12. SSH Hardening"
if [ -f /etc/ssh/sshd_config ] && [ -w /etc/ssh/sshd_config ]; then
    sed -i -E 's|^#?\s*DebianBanner\s+yes|DebianBanner no|' /etc/ssh/sshd_config
    if ! grep -q "^KexAlgorithms" /etc/ssh/sshd_config; then
        cat >> /etc/ssh/sshd_config <<'EOF'

# SRE HASSH Spoofing parameters
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group14-sha256
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com
MACs hmac-sha2-512-etm@openssh.com
EOF
    fi
    if [ "$SYSTEMD_RUNNING" = "1" ]; then
        systemctl restart sshd 2>/dev/null || true
    fi
    mark_pass
elif [ ! -f /etc/ssh/sshd_config ]; then
    echo "[*] No sshd_config (SSH not installed)"
    mark_pass
else
    mark_warn "/etc/ssh/sshd_config not writable"
fi

#-------------------------------------------------------------------------
# 13. Eliminate Cleartext Swap Memory Leaks
#-------------------------------------------------------------------------
current_section="13. Swap Disable"
swapoff -a 2>/dev/null || true
if [ -w /etc/fstab ]; then
    sed -i -E '/\s+swap\s+/s|^|#|' /etc/fstab
fi

for swapfile in /swapfile /var/swapfile /var/lib/swap /mnt/swap; do
    if [ -f "$swapfile" ]; then
        if command -v shred >/dev/null 2>&1; then
            shred -u "$swapfile" 2>/dev/null || rm -f "$swapfile"
        else
            rm -f "$swapfile"
        fi
    fi
done
mark_pass

#-------------------------------------------------------------------------
# 13b. Volatile RAM-Only /tmp and /var/tmp
#-------------------------------------------------------------------------
current_section="13b. tmpfs mounts"
if mountpoint -q /tmp 2>/dev/null; then
    echo "[*] /tmp already a mountpoint"
else
    if mount -t tmpfs -o size=1G,noexec,nosuid,nodev tmpfs /tmp 2>/dev/null; then
        if [ -w /etc/fstab ] && ! grep -qE '^tmpfs\s+/tmp\s+tmpfs' /etc/fstab 2>/dev/null; then
            printf 'tmpfs\t/tmp\ttmpfs\tsize=1G,noexec,nosuid,nodev\t0\t0\n' >> /etc/fstab
        fi
    else
        mark_warn "tmpfs mount on /tmp failed"
    fi
fi

if mountpoint -q /var/tmp 2>/dev/null; then
    echo "[*] /var/tmp already a mountpoint"
else
    if mount -t tmpfs -o size=512M,noexec,nosuid,nodev tmpfs /var/tmp 2>/dev/null; then
        if [ -w /etc/fstab ] && ! grep -qE '^tmpfs\s+/var/tmp\s+tmpfs' /etc/fstab 2>/dev/null; then
            printf 'tmpfs\t/var/tmp\ttmpfs\tsize=512M,noexec,nosuid,nodev\t0\t0\n' >> /etc/fstab
        fi
    else
        mark_warn "tmpfs mount on /var/tmp failed"
    fi
fi
mark_pass

#-------------------------------------------------------------------------
# 14. Host-Level iptables MSS clamp + Packet Scrubbing
#-------------------------------------------------------------------------
current_section="14. Host MSS clamp + Packet Scrubbing"

# This is the HOST's iptables. The container has its own iptables
# in entrypoint.sh that does the per-SYN MSS clamp. The host's clamp
# here is for host-side traffic (e.g., apt-get, DNS).
if command -v iptables >/dev/null 2>&1; then
    DEFAULT_INTERFACE=$(ip route show 2>/dev/null | awk '/^default/ {print $5; exit}')
    if [ -n "$DEFAULT_INTERFACE" ]; then
        # OUTPUT chain: host-originated traffic
        iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN \
            -o "$DEFAULT_INTERFACE" \
            -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
    fi

    # A5: FORWARD chain: container-originated traffic that the host
    # routes. The mangle FORWARD chain affects packets that traverse
    # the host (e.g., container -> WAN via the host's bridge).
    # This is a belt-and-suspenders MSS clamp: if the container's
    # iptables in entrypoint.sh fails, the host still clamps.
    if [ -n "$DEFAULT_INTERFACE" ]; then
        iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN \
            -o "$DEFAULT_INTERFACE" \
            -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
    fi

    # Drop abnormal TCP flag combinations that real browsers never send
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags ALL ALL -j DROP 2>/dev/null || true
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags ALL NONE -j DROP 2>/dev/null || true
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,FIN SYN,FIN -j DROP 2>/dev/null || true
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN,RST -j DROP 2>/dev/null || true
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags FIN,RST FIN,RST -j DROP 2>/dev/null || true
    iptables -t mangle -A OUTPUT -p tcp --tcp-flags URG,PSH,FIN URG,PSH,FIN -j DROP 2>/dev/null || true
    echo "[*] iptables: TCP flag combination scrubbing active"
fi

#-------------------------------------------------------------------------
# 15. OCI Agent Disable
#-------------------------------------------------------------------------
current_section="15. OCI Agent Disable"
if [ "$SYSTEMD_RUNNING" = "1" ]; then
    for svc in oracle-cloud-agent oracle-cloud-agent-updater; do
        if systemctl list-units --full --all 2>/dev/null | grep -Fq "$svc"; then
            systemctl stop "$svc" 2>/dev/null || true
            systemctl disable "$svc" 2>/dev/null || true
            systemctl mask "$svc" 2>/dev/null || true
        fi
    done
fi

if [ "$APT_FAILED" = "0" ]; then
    for pkg in oracle-cloud-agent oci-agent oracle-agent; do
        if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
            apt-get purge -y "$pkg" 2>/dev/null && echo "[*] Purged $pkg"
        fi
    done
fi

pkill -9 -f oracle-cloud-agent 2>/dev/null || true
pkill -9 -f oci-agent 2>/dev/null || true

if command -v snap >/dev/null 2>&1; then
    if snap list 2>/dev/null | grep -Fq "oracle-cloud-agent"; then
        snap remove --purge oracle-cloud-agent 2>/dev/null || true
    fi
fi
mark_pass

#-------------------------------------------------------------------------
# 16. CPU Governor Alignment
#-------------------------------------------------------------------------
current_section="16. CPU Governor"
if [ -d /sys/devices/system/cpu/cpu0/cpufreq ]; then
    GOV_OK=0
    for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        if [ -f "$gov" ] && echo performance > "$gov" 2>/dev/null; then
            GOV_OK=1
        fi
    done
    if [ "$GOV_OK" = "1" ]; then
        mark_pass
    else
        mark_warn "Could not set governor to performance"
    fi
else
    if [ "$APT_FAILED" = "0" ]; then
        # H3: Debian 12 uses 'linux-cpupower' (replaced cpufrequtils).
        apt-get install -y linux-cpupower 2>/dev/null || APT_FAILED=1
    fi
    if [ -d /etc/default ]; then
        echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils 2>/dev/null || true
    fi
    if [ "$SYSTEMD_RUNNING" = "1" ]; then
        systemctl restart cpufrequtils 2>/dev/null || true
    fi
    mark_warn "cpufreq sysfs not present"
fi

#-------------------------------------------------------------------------
# 16b. GRUB cmdline — Debian/Linux defaults (mitigations ON, TSC, nohz)
#-------------------------------------------------------------------------
current_section="16b. GRUB cmdline (Linux defaults: mitigations=auto, TSC, nohz)"
if [ -f /etc/default/grub ] && [ -w /etc/default/grub ]; then
    # For a Debian 12 host, the GRUB cmdline should reflect Linux defaults.
    # We set:
    # - clocksource=tsc: force TSC clock (Linux default for x86_64 with
    #   invariant TSC; real desktops have this; cloud VMs may use kvm-clock)
    # - tsc=reliable: marks TSC as reliable for timekeeping
    # - nohz_full=1-3 rcu_nocbs=1-3: isolate orchestrator cores from
    #   kernel timer ticks (helps with stealth — fewer interrupts)
    #
    # We DO NOT set mitigations=off (was v5.0+ mistake). Real Debian
    # systems have mitigations=on by default for security. Disabling
    # them is a security risk AND a fingerprint vector.
    EXTRA_PARAMS="clocksource=tsc tsc=reliable nohz_full=1-3 rcu_nocbs=1-3"
    if [ "$HOST_ARCH" = "x86_64" ]; then
        EXTRA_PARAMS="$EXTRA_PARAMS console=ttyS0,115200"
    fi

    PYBIN="$(command -v python3 || command -v python)"
    if [ -n "$PYBIN" ]; then
        PY_OUTPUT=$("$PYBIN" - <<PYEOF 2>&1
import re, sys
path = "/etc/default/grub"
try:
    with open(path, "r") as f:
        content = f.read()
except Exception as e:
    print(f"ERROR: Cannot read {path}: {e}", file=sys.stderr)
    sys.exit(2)
extra = "$EXTRA_PARAMS"
m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', content, re.MULTILINE)
if m:
    existing = m.group(1)
    parts = existing.split()
    for flag in extra.split():
        if flag in parts:
            parts.remove(flag)
    if "mitigations=off" in parts:
        parts.remove("mitigations=off")
    parts.extend(extra.split())
    new_value = " ".join(parts)
    content = re.sub(
        r'^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"',
        f'GRUB_CMDLINE_LINUX_DEFAULT="{new_value}"',
        content,
        flags=re.MULTILINE,
    )
else:
    content += f'\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash {extra}"\n'
    new_value = f"quiet splash {extra}"
try:
    with open(path, "w") as f:
        f.write(content)
    print("[*] GRUB cmdline now:", new_value)
except Exception as e:
    print(f"ERROR: Cannot write {path}: {e}", file=sys.stderr)
    sys.exit(3)
PYEOF
)
        PY_RC=$?
        echo "$PY_OUTPUT"
        if [ $PY_RC -ne 0 ]; then
            mark_fail "Python GRUB editor failed (rc=$PY_RC)"
        else
            sed -i -E 's|^#?\s*GRUB_TIMEOUT=[0-9]+|GRUB_TIMEOUT=0|' /etc/default/grub
            if command -v update-grub >/dev/null 2>&1; then
                if [ -d /boot/efi ] || [ -d /sys/firmware/efi ]; then
                    UGRUB_OUT=$(update-grub 2>&1)
                    UGRUB_RC=$?
                    if [ $UGRUB_RC -eq 0 ]; then
                        mark_pass
                    else
                        mark_warn "update-grub returned $UGRUB_RC"
                    fi
                else
                    if update-grub 2>&1 | tail -3; then
                        mark_pass
                    else
                        mark_warn "update-grub failed in BIOS mode"
                    fi
                fi
            else
                mark_warn "update-grub not available"
            fi
        fi
    else
        mark_warn "python3 not found; GRUB cmdline edit skipped"
    fi
elif [ -f /etc/default/grub ]; then
    mark_warn "/etc/default/grub not writable"
else
    echo "[*] No /etc/default/grub (no GRUB installed)"
    mark_pass
fi

#-------------------------------------------------------------------------
# 17. Global Shell History Deactivation & Audit Cleansing
#-------------------------------------------------------------------------
current_section="17. History & Audit"
if [ -d /etc/profile.d ]; then
    cat > /etc/profile.d/disable_history.sh <<'EOF'
export HISTFILESIZE=0
export HISTSIZE=0
export HISTFILE=/dev/null
set +o history
EOF
    chmod 644 /etc/profile.d/disable_history.sh
fi
. /etc/profile.d/disable_history.sh 2>/dev/null || true
rm -f ~/.bash_history ~/.zsh_history 2>/dev/null || true

echo "[*] Skipping wtmp/btmp/lastlog truncation (binary record files)."

: > /var/log/dpkg.log 2>/dev/null || true
: > /var/log/alternatives.log 2>/dev/null || true
: > /var/log/apt/history.log 2>/dev/null || true
: > /var/log/apt/term.log 2>/dev/null || true

if [ "$APT_FAILED" = "0" ]; then
    apt-get clean 2>/dev/null || true
    rm -rf /var/lib/apt/lists/* 2>/dev/null || true
fi
mark_pass

#-------------------------------------------------------------------------
# 18. Host-Level Docker Bridge MAC Spoofing
#-------------------------------------------------------------------------
current_section="18. Docker Bridge MAC Spoof"
cat > /etc/systemd/system/stealth-bridge-spoof.service <<'EOF'
[Unit]
Description=Stealth Host-Side Docker Bridge MAC Spoofing
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'sleep 5 && for try in 1 2 3 4 5; do bridges=$(ls /sys/class/net/ 2>/dev/null | grep -E "^(br-|docker0|br_)"); if [ -n "$bridges" ]; then break; fi; sleep 5; done; for br in $bridges; do RAND_HEX=$(od -An -tx1 -N3 /dev/urandom | tr -d " \n"); ip link set dev "$br" address "00:1a:2b:${RAND_HEX}" 2>/dev/null || true; done'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

if [ "$SYSTEMD_RUNNING" = "1" ]; then
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable stealth-bridge-spoof.service 2>/dev/null || true
    mark_pass
else
    mark_warn "No systemd; service not installed"
fi

#-------------------------------------------------------------------------
# 19. Host-Level Firewall Compilation and Execution
#-------------------------------------------------------------------------
current_section="19. Host Firewall"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || echo /home/user/project/config)"
IPTABLES_SCRIPT=""
for candidate in \
    "$SCRIPT_DIR/iptables_docker.sh" \
    /home/user/project/config/iptables_docker.sh \
    ./project/config/iptables_docker.sh \
    ./config/iptables_docker.sh \
    /opt/project/config/iptables_docker.sh; do
    if [ -f "$candidate" ]; then
        IPTABLES_SCRIPT="$candidate"
        break
    fi
done

if [ -n "$IPTABLES_SCRIPT" ]; then
    chmod +x "$IPTABLES_SCRIPT"
    if [ -f /.dockerenv ]; then
        echo "[!] Detected /.dockerenv; skipping host iptables apply."
        mark_warn "Script running inside a container"
    else
        if "$IPTABLES_SCRIPT" 2>&1 | tail -10; then
            mark_pass
        else
            mark_warn "iptables script returned non-zero"
        fi
    fi
else
    mark_warn "iptables_docker.sh not found"
fi

#-------------------------------------------------------------------------
# 20. Final Validation
#-------------------------------------------------------------------------
current_section="20. Final Validation"

# Verify all critical Debian/Linux sysctls are in place
echo "[*] Validating Debian 12 / Linux 6.1.x kernel profile..."

declare -a EXPECTED_SYSCTLS=(
    "net.ipv4.tcp_window_scaling:1"
    "net.ipv4.tcp_adv_win_scale:1"
    "net.ipv4.tcp_timestamps:1"
    "net.ipv4.tcp_sack:1"
    "net.ipv4.tcp_tw_reuse:2"
    "net.ipv4.tcp_ecn:1"
    "net.ipv4.ip_default_ttl:64"
    "net.ipv4.tcp_slow_start_after_idle:1"
    "net.ipv4.tcp_init_cwnd:10"
    "net.ipv4.ip_local_port_range:32768	60999"
    "net.ipv4.tcp_fin_timeout:60"
)

VALIDATION_FAILED=0
for entry in "${EXPECTED_SYSCTLS[@]}"; do
    key="${entry%%:*}"
    expected="${entry#*:}"
    actual=$(sysctl -n "$key" 2>/dev/null || echo "MISSING")
    if [ "$actual" = "$expected" ]; then
        echo "  [OK]   $key = $actual"
    else
        echo "  [MISMATCH] $key expected '$expected' got '$actual'"
        VALIDATION_FAILED=1
    fi
done

if [ "$VALIDATION_FAILED" = "1" ]; then
    mark_warn "Some Linux sysctls are not as expected (may require reboot)"
else
    echo "[*] All Debian 12 sysctls validated."
fi

# Validate mock files
for f in /etc/mock_cpuinfo /etc/mock_version /etc/mock_boot_id /etc/mock_osrelease; do
    if [ -d "$f" ]; then
        mark_fail "$f is a DIRECTORY (will break container bind-mount)"
    elif [ -f "$f" ]; then
        SIZE=$(stat -c %s "$f" 2>/dev/null || echo 0)
        echo "[*]   $f OK ($SIZE bytes)"
    else
        mark_fail "$f is MISSING"
    fi
done

# Validate mock DMI directory
if [ -d /etc/mock_dmi_id ]; then
    DMI_FILES_OK=1
    for f in product_uuid product_serial sys_vendor product_name bios_version; do
        if [ ! -f "/etc/mock_dmi_id/$f" ] || [ ! -s "/etc/mock_dmi_id/$f" ]; then
            mark_fail "/etc/mock_dmi_id/$f missing or empty"
            DMI_FILES_OK=0
        fi
    done
    if [ "$DMI_FILES_OK" = "1" ]; then
        echo "[*] /etc/mock_dmi_id: all critical files present"
    fi
else
    mark_fail "/etc/mock_dmi_id is not a directory"
fi

# machine-id check
if [ -f /etc/machine-id ] && [ -s /etc/machine-id ]; then
    MID_CONTENT=$(cat /etc/machine-id)
    if [ "${#MID_CONTENT}" -eq 33 ] || [ "${#MID_CONTENT}" -eq 32 ]; then
        echo "[*] /etc/machine-id OK: $MID_CONTENT"
    else
        mark_warn "/etc/machine-id has unexpected length: ${#MID_CONTENT}"
    fi
else
    mark_fail "/etc/machine-id is empty or missing"
fi

#-------------------------------------------------------------------------
# 21. Final Summary
#-------------------------------------------------------------------------
echo "=========================================================================="
echo "  HOST OS HARDENING v6.0 COMPLETED (DEBIAN 12 / LINUX KERNEL PROFILE)   "
echo "  LOG: $LOG_FILE                                                          "
echo "=========================================================================="
echo "Sections: $SECTION_PASSED passed / $SECTION_TOTAL total / $FAIL_COUNTER failed / $WARN_COUNTER warned"
echo "------------------------------------------------------------------"
if [ -s /etc/machine-id ]; then
    MID_DISPLAY=$(cat /etc/machine-id)
else
    MID_DISPLAY="MISSING"
fi
echo "1. Hostname:           $(hostname)"
echo "2. Machine-ID:         $MID_DISPLAY"
echo "3. Timezone:           $(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo 'unknown')"
echo "4. Default Route:      $(ip route show 2>/dev/null | awk '/^default/ {print; exit}')"
echo "5. Default TTL:        $(cat /proc/sys/net/ipv4/ip_default_ttl 2>/dev/null || echo unknown)"
echo "6. NTP Status:         $(chronyc tracking 2>/dev/null | awk -F': *' '/System time/ {print $2}')"
echo "7. Entropy:            $(cat /proc/sys/kernel/random/entropy_avail 2>/dev/null || echo unknown) bits"
echo "8. mock_cpuinfo:       $(ls -la /etc/mock_cpuinfo 2>/dev/null | awk '{print $1, $5, $9}')"
echo "9. mock_version:       $(ls -la /etc/mock_version 2>/dev/null | awk '{print $1, $5, $9}')"
echo "10. mock_dmi_id:       $(ls -ld /etc/mock_dmi_id 2>/dev/null | awk '{print $1, $9}')"
echo "11. mock_boot_id:      $(cat /etc/mock_boot_id 2>/dev/null || echo MISSING)"
echo "12. mock_osrelease:    $(cat /etc/mock_osrelease 2>/dev/null || echo MISSING)"
echo "13. resolv.conf:       $(ls -la /etc/resolv.conf 2>/dev/null | awk '{print $1, $9}')"
echo "14. Persona UUID:      $PERSONA_UUID"
echo "------------------------------------------------------------------"
echo ""
echo "v6.0 ARCHITECTURAL NOTES:"
echo "  - Host kernel runs Debian 12 / Linux 6.1.x defaults (NOT Windows)."
echo "  - Host sysctls do NOT propagate to container net.* namespace."
echo "  - Container has its own sysctls in docker-compose.yml (also Linux)."
echo "  - Container's iptables MSS clamp is in browser-runtime/entrypoint.sh."
echo "  - Persona (Windows 10/11) is spoofed at the BROWSER level:"
echo "    1. CloakBrowser C++ layer (V8 engine, navigator.*, WebGL, etc.)"
echo "    2. prototype_hardener.js (all JS-level APIs, Intl, etc.)"
echo "    3. Mock /proc and /sys files (read by container via bind-mount)"
echo "  - Mock files at /etc/mock_* are bind-mounted into the container:"
echo "    /etc/mock_cpuinfo     -> container /proc/cpuinfo"
echo "    /etc/mock_version     -> container /proc/version"
echo "    /etc/mock_dmi_id      -> container /sys/class/dmi/id"
echo "    /etc/mock_boot_id     -> container /proc/sys/kernel/random/boot_id"
echo "    /etc/mock_osrelease   -> container /proc/sys/kernel/osrelease"
echo ""

if [ "$FAIL_COUNTER" -gt 0 ]; then
    echo "ERROR: $FAIL_COUNTER critical section(s) failed."
    exit 1
fi
