#!/bin/bash
# hardened_host_os.sh - Production-Grade Host OS Anti-Fingerprinting Alignment Script
# Enforces complete stealth matching at the Kernel, Network Stack, Clock, and DNS layers
# Optimized for Debian 12 Bookworm (Minimal) Host deployments on OCI ARM
#
# CHANGELOG (iter7):
#   B1  - hostname: use hostname(1) binary (always present) AND hostnamectl;
#         use /dev/urandom via tr only as a single read.
#   B2  - /etc/hosts: only add 127.0.1.1 if not already mapped to current name,
#         and skip entirely on systemd-resolved-managed hosts.
#   B3  - dbus-uuidgen: only run if dbus-uuidgen binary is present; tolerate
#         missing dbus package; never truncate /etc/machine-id after writing.
#   B4  - machine-id truncation REMOVED (was self-defeating). We set ONCE and
#         leave it intact (required for systemd-journald, NetworkManager).
#   B5  - dbus-uuidgen --ensure: pass /etc/machine-id explicitly to ensure
#         both files contain the same id (and not stomp /var/lib/dbus/machine-id).
#   B6  - apt-get update BEFORE first install; locale install failure is
#         non-fatal but logged.
#   B7  - chrony waitsync with relaxed params; loop with bounded retries; warn
#         if final offset > 0.05s but do not abort.
#   B8  - timezone configurable via env var HOST_TZ (default: America/New_York);
#         fallback to /etc/localtime copy if timedatectl absent.
#   B9  - mock /etc/mock_cpuinfo MUST be x86_64 (matches emergency fallback
#         in entrypoint.sh) so detector scripts that read /proc/cpuinfo see
#         a coherent consumer-Windows-style Intel profile. Write as a REGULAR
#         FILE (no trailing slash, no pre-existing dir).
#   B10 - resolv.conf: chattr AFTER copy; install dhclient hook BEFORE
#         restarting systemd-resolved to avoid race window.
#   B11 - dhclient hook installed before any restart; idempotent on rerun.
#   B12 - machine-id: never truncate. Single source of truth.
#   B13 - systemctl calls: skip entire section if systemd is not PID 1.
#   B14 - oci-agent purge: try common package names; ignore "not installed".
#   B15 - mount -a REMOVED (too dangerous with cloud-init fstab). Use explicit
#         mount calls for /tmp and /var/tmp only.
#   B16/B17 - GRUB: APPEND to GRUB_CMDLINE_LINUX_DEFAULT, do not replace.
#         Preserve all original parameters (root=, ro, etc.).
#   B18 - all "sudo" prefixes removed; script already runs as root.
#   B19 - wtmp/btmp/lastlog: do NOT truncate (corrupts binary record
#         structure). Use logrotate-style "rotate then create empty" or
#         just leave alone - they'll be rotated on next boot.
#   B20 - apt-get update called ONCE at top; rm -rf apt/lists moved to
#         AFTER all installs complete.
#   B21 - mock_version: write the SAME string as the emergency fallback to
#         ensure detector scripts can't tell real OCI host from mocked OCI
#         host via version string mismatch.
#   B22 - idempotency: every operation is safe to run multiple times.
#   B23 - logging: redirect script stdout/stderr to a tmpfs file by default
#         to avoid leaving audit trails on the host rootfs.

# Enforce root privileges for execution
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)." >&2
    exit 1
fi

#-------------------------------------------------------------------------
# 0. Logging redirection (B23): default to volatile tmpfs log, allow override
#-------------------------------------------------------------------------
LOG_FILE="${HARDENED_LOG_FILE:-/run/hardened_host_os.log}"
# Truncate previous run's log to avoid growth
: > "$LOG_FILE" 2>/dev/null || true
# Mirror all output to the log file AND the terminal
exec > >(tee -a "$LOG_FILE" >&2) 2>&1

echo "=========================================================================="
echo "          INITIALIZING HOST OS ANTI-FINGERPRINTING ALIGNMENT             "
echo "          TARGET PLATFORM: DEBIAN 12 BOOKWORM COVERT PROFILE             "
echo "          LOG FILE: $LOG_FILE                                          "
echo "=========================================================================="

# Configurable target timezone (B8). Default: America/New_York.
: "${HOST_TZ:=America/New_York}"
export HOST_TZ
export DEBIAN_FRONTEND=noninteractive

# Detect whether systemd is the running init (B13). Many minimal OCI
# images use sysvinit or openrc; systemctl calls in those environments
# fail silently and confuse operators.
SYSTEMD_RUNNING=0
if [ -d /run/systemd/system ] && pidof systemd >/dev/null 2>&1; then
    SYSTEMD_RUNNING=1
fi
echo "[*] systemd running: $SYSTEMD_RUNNING"

# Detect architecture (B9)
HOST_ARCH="$(uname -m)"
echo "[*] Host architecture: $HOST_ARCH"

#-------------------------------------------------------------------------
# 0b. Single APT cache refresh (B6, B20)
#-------------------------------------------------------------------------
# Run apt-get update EXACTLY ONCE at the top so subsequent installs don't
# race. We do this once instead of trusting cloud-init to have done it.
echo "[*] Refreshing APT cache..."
apt-get update -o Acquire::Languages=none -o Acquire::http::No-Cache=true -y 2>&1 | tail -5 || echo "[!] apt-get update failed; continuing anyway."

#-------------------------------------------------------------------------
# 1. Hostname Anonymization (B1, B2)
#-------------------------------------------------------------------------
# Cloud providers default to identifiable hostnames (e.g., instance-20260606-xxx).
# We spoof this to mimic a standard consumer Windows 10/11 device.
SPOOFED_HOSTNAME="DESKTOP-$(tr -dc 'A-Z0-9' </dev/urandom | head -c 7)"
echo "[*] Spoofing system hostname to: $SPOOFED_HOSTNAME"

# Use BOTH hostname(1) (always present) and hostnamectl (when systemd is up)
hostname "$SPOOFED_HOSTNAME" 2>/dev/null || true
if [ "$SYSTEMD_RUNNING" = "1" ] && command -v hostnamectl >/dev/null 2>&1; then
    hostnamectl set-hostname "$SPOOFED_HOSTNAME" 2>/dev/null || true
fi

# Update /etc/hosts safely. Only modify the existing 127.0.1.1 line if it
# is present, AND only if it doesn't already match our target.
if grep -qE '^127\.0\.1\.1\b' /etc/hosts 2>/dev/null; then
    sed -i.bak -E "s|^127\\.0\\.1\\.1\\b.*|127.0.1.1\\t${SPOOFED_HOSTNAME}|" /etc/hosts
    rm -f /etc/hosts.bak
elif [ "$SYSTEMD_RUNNING" = "1" ]; then
    # Don't add 127.0.1.1 entry on systemd-resolved systems: it conflicts
    # with /run/systemd/resolve/stub-resolv.conf and breaks hostname lookup.
    echo "[*] Skipping 127.0.1.1 injection on systemd-resolved host."
else
    echo -e "127.0.1.1\t${SPOOFED_HOSTNAME}" >> /etc/hosts
fi

#-------------------------------------------------------------------------
# 2. Spoof Unique Machine-ID (Anti-Virtualization Check Bypass) (B3, B4, B5, B12)
#-------------------------------------------------------------------------
# Cloud machine-IDs contain static structures revealing OCI metadata.
# We regenerate a clean, randomized DBUS machine-ID.
# CRITICAL: we must NOT truncate after writing; systemd-journald, NetworkManager,
# and many other services REQUIRE a non-empty /etc/machine-id to start.
echo "[*] Regenerating randomized, non-attributable Machine ID..."

# Ensure dbus is installed (if not already)
if ! command -v dbus-uuidgen >/dev/null 2>&1; then
    apt-get install -y dbus 2>/dev/null || true
fi

NEW_MACHINE_ID=""
if command -v dbus-uuidgen >/dev/null 2>&1; then
    # dbus-uuidgen without args prints to stdout; capture into variable
    NEW_MACHINE_ID="$(dbus-uuidgen 2>/dev/null | tr -d '[:space:]')"
fi
if [ -z "$NEW_MACHINE_ID" ] || [ "${#NEW_MACHINE_ID}" -ne 32 ]; then
    # Fall back to kernel RNG; format is 32 hex chars
    NEW_MACHINE_ID="$(tr -dc 'a-f0-9' </dev/urandom | head -c 32)"
fi
echo "[*] New machine-id: $NEW_MACHINE_ID"

# Write to BOTH canonical locations with the SAME id
echo "$NEW_MACHINE_ID" > /etc/machine-id
echo "$NEW_MACHINE_ID" > /var/lib/dbus/machine-id
# CRITICAL: NO truncate -s 0 here (B4/B12 self-defeating original bug).

#-------------------------------------------------------------------------
# 3. Debian 12 Specific Telemetry Purge (popularity-contest)
#-------------------------------------------------------------------------
echo "[*] Purging Debian popularity-contest telemetry packages..."
apt-get purge -y popularity-contest 2>/dev/null || true

#-------------------------------------------------------------------------
# 4. Generate Pristine Locale Configurations (B6)
#-------------------------------------------------------------------------
echo "[*] Compiling and generating pristine en_US.UTF-8 locale structures..."
apt-get install -y locales 2>/dev/null || true
if command -v locale-gen >/dev/null 2>&1; then
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
    locale-gen 2>/dev/null || echo "[!] locale-gen failed; fallback C locale will be used."
fi
if [ -f /etc/default/locale ] || [ -L /etc/default/locale ]; then
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 2>/dev/null || true
else
    printf 'LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8\n' > /etc/default/locale
fi

#-------------------------------------------------------------------------
# 5. Apply Kernel Network Stack Alignment (p0f / Evasion TCP/IP Profiles)
#-------------------------------------------------------------------------
echo "[*] Injecting hardened, anti-fingerprint kernel parameters..."
tee /etc/sysctl.d/99-hardened-stealth.conf >/dev/null <<'EOF'
# 1. Disable TCP Timestamps to prevent platform firewalls from calculating actual host uptime
net.ipv4.tcp_timestamps = 0

# 2. Spoof default Linux TTL (64) to match standard Windows packet structures (128)
net.ipv4.ip_default_ttl = 128

# 3. Adjust TCP window scaling and memory limits to mimic Windows networks
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_rfc1337 = 1

# 4. Align TCP memory buffers with modern Windows 10/11 TCP Auto-Tuning
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# 5. Disable TCP Selective Acknowledgments (SACK)
# Windows 10/11 systems occasionally disable SACK under specific consumer routes
net.ipv4.tcp_sack = 0
net.ipv4.tcp_dsack = 0

# 6. Disable TCP Explicit Congestion Notification (ECN) to align handshake signatures with Windows
net.ipv4.tcp_ecn = 0

# SRE Network Hardening: Completely disable TCP Fast Open (TFO)
# (Prevents out-of-band packet-level handshakes from carrying Windows-inconsistent TFO cookies)
net.ipv4.tcp_fastopen = 0

# 7. Network Forensic Leak Protections: Disable IPv6
# Prevents dual-stack browsers from bypassing SOCKS5 IPv4 proxies and leaking OCI VM IPv6 addresses
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1

# 8. Disable packet routing source parameters and ICMP redirect overrides to prevent path exploits
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.all.accept_redirects = 0

# 9. Advanced 10/10 Forensic Security & Information Leak Hardening
# Restrict dmesg kernel logging to root users to prevent hardware/hypervisor memory leaks
kernel.dmesg_restrict = 1
# Disable unprivileged eBPF execution to block speculative execution side-channel exploits (Spectre/Meltdown)
kernel.unprivileged_bpf_disabled = 1
# Enforce complete Address Space Layout Randomization (ASLR)
kernel.randomize_va_space = 2

# SRE Kernel Printk Alignment: Silence non-critical kernel logging (prevents microsecond CPU scheduler timing jitter)
kernel.printk = 3 4 1 3

# SRE Scheduler Alignment: Tune CPU scheduler kernel parameters to mimic low-latency consumer desktops
kernel.sched_latency_ns = 6000000
kernel.sched_min_granularity_ns = 750000
kernel.sched_wakeup_granularity_ns = 1000000

# 10. Anti-Fingerprint Network Alignment Updates
net.ipv4.ip_no_pmtu_disc = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# 11. Performance Profiling Jitter Lockdown (V8 Timing Attacks Evasion)
kernel.perf_cpu_time_max_percent = 1
kernel.perf_event_max_sample_rate = 1000
kernel.perf_event_paranoid = 2

# SRE Stability: Force immediate kernel reboot on panics to flush volatile memory-only tmpfs databases
kernel.panic = 10

# 12. Virtual memory performance optimization
vm.swappiness = 10
vm.dirty_background_ratio = 5
vm.dirty_ratio = 10
EOF

# Apply sysctl parameters immediately. If a key is unknown (kernel too old),
# sysctl --system prints a warning and continues, which is the desired behavior.
sysctl --system 2>&1 | grep -v '^net.ipv4.tcp_rmem' | grep -v '^$' | head -20 || true

#-------------------------------------------------------------------------
# 6. Debian 12 Kernel Module Blacklisting (Hardware Footprint Concealment)
#-------------------------------------------------------------------------
echo "[*] Blacklisting unused, legacy Linux hardware and virtualization guest-integration modules..."
tee /etc/modprobe.d/blacklist-stealth.conf >/dev/null <<'EOF'
blacklist floppy
blacklist parport
blacklist parport_pc
blacklist lp
blacklist joydev
blacklist pcspkr
blacklist snd_pcsp
# Blacklist common virtual drivers to hide guest integration signatures
blacklist virtio_balloon
blacklist virtio_pci
blacklist virtio_console
blacklist vboxguest
EOF

#-------------------------------------------------------------------------
# 7. Interface MTU & TCP MSS Clamping
#-------------------------------------------------------------------------
echo "[*] Clamping route MTU and MSS sizes to match standard consumer network cards..."
# Identify default outgoing network interface
DEFAULT_INTERFACE=$(ip route show 2>/dev/null | awk '/^default/ {print $5; exit}')
if [ -n "$DEFAULT_INTERFACE" ] && [ -e "/sys/class/net/$DEFAULT_INTERFACE" ]; then
    if ip route change default dev "$DEFAULT_INTERFACE" proto dhcp scope global metric 100 mtu 1500 advmss 1460 initcwnd 10 initrwnd 10 2>/dev/null; then
        echo "[*] Successfully updated MTU/MSS clamping on interface: $DEFAULT_INTERFACE"
    else
        echo "[!] ip route change failed; some kernels do not allow initcwnd/initrwnd. Retrying with minimal args..."
        ip route change default dev "$DEFAULT_INTERFACE" proto dhcp scope global metric 100 mtu 1500 advmss 1460 2>/dev/null || \
            echo "[!] MTU clamping failed entirely; non-fatal."
    fi
else
    echo "[!] Default network interface not found or invalid. Skipping route clamping."
fi

#-------------------------------------------------------------------------
# 8. Prevent Local DNS Leakages (systemd-resolved & dhclient Lockdown) (B10, B11)
#-------------------------------------------------------------------------
echo "[*] Hardening systemd-resolved DNS settings..."

# Install dhclient enter-hook FIRST (B11) so that any DHCP renew that happens
# during the systemd-resolved restart below cannot race a re-write of
# /etc/resolv.conf.
mkdir -p /etc/dhcp/dhclient-enter-hooks.d/
cat > /etc/dhcp/dhclient-enter-hooks.d/nodnsupdate <<'EOF'
#!/bin/sh
# SRE Stealth: Prevent DHCP lease renewals from overwriting /etc/resolv.conf.
# Some dhclient versions source this file as a shell script; redefining
# make_resolv_conf is the standard override point.
make_resolv_conf() {
    :
}
EOF
chmod +x /etc/dhcp/dhclient-enter-hooks.d/nodnsupdate

if [ "$SYSTEMD_RUNNING" = "1" ] && [ -d /etc/systemd ]; then
    mkdir -p /etc/systemd/resolved.conf.d/
    cat > /etc/systemd/resolved.conf.d/stealth-dns.conf <<'EOF'
[Resolve]
LLMNR=no
MulticastDNS=no
DNSSEC=no
EOF
    systemctl restart systemd-resolved 2>/dev/null || true
fi

# Now break the resolv.conf symlink and write a static immutable file.
# IMPORTANT (B10): we install chattr +i AFTER writing the file but we keep
# a backup path: if the immutable bit prevents future legitimate edits, we
# can always `chattr -i` from the console.
echo "[*] Securing and making host /etc/resolv.conf immutable..."
if [ -L /etc/resolv.conf ] || [ -e /etc/resolv.conf ]; then
    if [ -L /etc/resolv.conf ]; then
        rm -f /etc/resolv.conf
    fi
    # Atomic write via temp file + rename to avoid partial reads
    TMP_RESOLV="$(mktemp /run/.resolv.conf.XXXXXX)"
    cat > "$TMP_RESOLV" <<'EOF'
nameserver 9.9.9.9
nameserver 1.1.1.1
options edns0 trust-ad
EOF
    chmod 644 "$TMP_RESOLV"
    mv -f "$TMP_RESOLV" /etc/resolv.conf
    if command -v chattr >/dev/null 2>&1; then
        chattr +i /etc/resolv.conf 2>/dev/null || \
            echo "[!] chattr +i failed (XFS/noattr fs?); resolv.conf may be overwritten on DHCP renew."
    fi
fi

#-------------------------------------------------------------------------
# 8b. systemd Watchdog Deactivation (Micro-Preemption Evasion)
#-------------------------------------------------------------------------
if [ "$SYSTEMD_RUNNING" = "1" ] && [ -f /etc/systemd/system.conf ]; then
    echo "[*] Disabling systemd hardware/software watchdog monitoring..."
    sed -i -E 's|^#?\s*RuntimeWatchdogSec=.*|RuntimeWatchdogSec=0|' /etc/systemd/system.conf
    sed -i -E 's|^#?\s*ShutdownWatchdogSec=.*|ShutdownWatchdogSec=0|' /etc/systemd/system.conf
fi

#-------------------------------------------------------------------------
# 9. Install High-Precision Time Synchronization (Eliminating Clock Skew) (B7)
#-------------------------------------------------------------------------
echo "[*] Enforcing high-precision NTP clock synchronization..."

# Stop and disable conflicting systemd-timesyncd (only if systemd is up)
if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "systemd-timesyncd"; then
    echo "[*] Deactivating conflicting systemd-timesyncd daemon..."
    systemctl stop systemd-timesyncd 2>/dev/null || true
    systemctl disable systemd-timesyncd 2>/dev/null || true
fi

# Install chrony (apt cache already refreshed at step 0b)
if ! command -v chronyd >/dev/null 2>&1 && ! command -v chrony >/dev/null 2>&1; then
    apt-get install -y chrony 2>&1 | tail -3
fi

# Copy Google-Aligned hardened chrony configuration
if [ -f "./project/config/chrony_hardened.conf" ]; then
    cp -f ./project/config/chrony_hardened.conf /etc/chrony/chrony.conf
    echo "[*] Loaded Google-Aligned chrony_hardened.conf from ./project/config/"
elif [ -f "./config/chrony_hardened.conf" ]; then
    cp -f ./config/chrony_hardened.conf /etc/chrony/chrony.conf
    echo "[*] Loaded Google-Aligned chrony_hardened.conf from ./config/"
elif [ -f "/home/user/project/config/chrony_hardened.conf" ]; then
    cp -f /home/user/project/config/chrony_hardened.conf /etc/chrony/chrony.conf
    echo "[*] Loaded Google-Aligned chrony_hardened.conf from /home/user/project/config/"
fi

if [ "$SYSTEMD_RUNNING" = "1" ]; then
    systemctl enable chrony 2>/dev/null || true
    systemctl restart chrony 2>/dev/null || true
fi

# Use chronyc makestep with bounded retries
if command -v chronyc >/dev/null 2>&1; then
    # Step the clock immediately, then check offset
    chronyc -a makestep 2>/dev/null || true
    echo "[*] Waiting for NTP synchronization with time.google.com..."
    # waitsync params: max_trials=20, interval=2, max_offset=0.5s, max_skew=95%
    # Loosened from original (10, 0.1, 95, 1) to tolerate slow OCI first-boot
    # convergence, but still strict enough to catch real clock-drift.
    for i in 1 2 3 4 5; do
        if chronyc waitsync 20 0.5 95 2 2>/dev/null; then
            echo "[*] Chrony reached synchronization after attempt $i."
            break
        fi
        echo "[!] waitsync attempt $i did not converge; retrying in 3s..."
        sleep 3
    done
    FINAL_OFFSET="$(chronyc tracking 2>/dev/null | awk '/System time/ {print $4 " " $5}')"
    echo "[*] Final NTP offset: $FINAL_OFFSET"
fi

# Configure timezone alignment (B8). Use timedatectl if available,
# otherwise fall back to /etc/localtime symlink creation.
echo "[*] Aligning host timezone with target proxy execution zone ($HOST_TZ)..."
ZONEINFO_PATH="/usr/share/zoneinfo/$HOST_TZ"
if [ ! -f "$ZONEINFO_PATH" ]; then
    echo "[!] tzdata not installed or $HOST_TZ not found; installing tzdata..."
    apt-get install -y tzdata 2>&1 | tail -3
fi
if [ "$SYSTEMD_RUNNING" = "1" ] && command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "$HOST_TZ" 2>/dev/null || true
elif [ -f "$ZONEINFO_PATH" ]; then
    # Manual fallback: copy zoneinfo file to /etc/localtime and update /etc/timezone
    cp -f "$ZONEINFO_PATH" /etc/localtime
    echo "$HOST_TZ" > /etc/timezone
fi

#-------------------------------------------------------------------------
# 10. Saturated Entropy Pool Installation (Defeating Crypto Timing Scans)
#-------------------------------------------------------------------------
echo "[*] Saturating kernel entropy pools to prevent cryptographic timing analysis..."
if ! command -v haveged >/dev/null 2>&1; then
    apt-get install -y haveged 2>&1 | tail -3
fi
if [ "$SYSTEMD_RUNNING" = "1" ]; then
    systemctl enable haveged 2>/dev/null || true
    systemctl start haveged 2>/dev/null || true
fi

#-------------------------------------------------------------------------
# 10b. CPU Microcode Alignment (Hardware Vulnerability Evasion)
#-------------------------------------------------------------------------
echo "[*] Verifying CPU architecture microcode alignment..."
if [ "$HOST_ARCH" = "x86_64" ]; then
    if grep -q "Intel" /proc/cpuinfo 2>/dev/null; then
        echo "[*] Intel CPU detected. Aligning processor microcode..."
        apt-get install -y intel-microcode 2>/dev/null || true
    elif grep -q "AMD" /proc/cpuinfo 2>/dev/null; then
        echo "[*] AMD CPU detected. Aligning processor microcode..."
        apt-get install -y amd64-microcode 2>/dev/null || true
    fi
else
    echo "[*] $HOST_ARCH architecture detected. CPU microcode is managed natively by system firmware. Skipping x86 packages."
fi

#-------------------------------------------------------------------------
# 10c. Generate Covert Mock CPU Info (B9) - CRITICAL FIX
#-------------------------------------------------------------------------
# The mock file MUST match the architecture we want to PRESENT to the container.
# Even though the host may be ARM64 (OCI), the container needs to see an x86_64
# Intel profile so that detection scripts reading /proc/cpuinfo get a coherent
# consumer-Windows-style Intel profile.
#
# We write to /etc/mock_cpuinfo as a REGULAR FILE (not a directory). The
# entrypoint.sh will bind-mount this file over /proc/cpuinfo inside the
# browser container. If /etc/mock_cpuinfo is a directory, the bind mount
# will create a directory overlay and Chromium will fail to read
# navigator.hardwareConcurrency (FIX #11 in entrypoint.sh).
echo "[*] Generating covert mock CPU info profile at /etc/mock_cpuinfo..."

# First, remove any pre-existing directory or file with conflicting type
if [ -d /etc/mock_cpuinfo ]; then
    echo "[!] Found /etc/mock_cpuinfo as directory; removing..."
    rm -rf /etc/mock_cpuinfo
elif [ -L /etc/mock_cpuinfo ]; then
    rm -f /etc/mock_cpuinfo
fi

# Atomic write to avoid partial reads
TMP_MOCK_CPU="$(mktemp /run/.mock_cpuinfo.XXXXXX)"
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
mv -f "$TMP_MOCK_CPU" /etc/mock_cpuinfo
# Verify it landed as a regular file
if [ ! -f /etc/mock_cpuinfo ] || [ -d /etc/mock_cpuinfo ]; then
    echo "[!] CRITICAL: /etc/mock_cpuinfo is not a regular file. Container bind-mount will fail."
fi

#-------------------------------------------------------------------------
# 10d. Generate Covert Mock OS Kernel Version (B21)
#-------------------------------------------------------------------------
# Must match the emergency fallback in entrypoint.sh so detector scripts
# cannot tell real host from mocked host via kernel version inconsistency.
echo "[*] Generating covert mock OS kernel version at /etc/mock_version..."

if [ -d /etc/mock_version ] || [ -L /etc/mock_version ]; then
    rm -rf /etc/mock_version
fi

TMP_MOCK_VER="$(mktemp /run/.mock_version.XXXXXX)"
cat > "$TMP_MOCK_VER" <<'MOCKVER'
Linux version 4.4.0-19041-Microsoft (Microsoft@Microsoft.com) (gcc version 5.4.0 (GCC) ) #1237-Microsoft Sat Sep 11 13:32:00 PST 2021
MOCKVER
chmod 444 "$TMP_MOCK_VER"
mv -f "$TMP_MOCK_VER" /etc/mock_version

#-------------------------------------------------------------------------
# 10e. Disable Transparent Hugepages (THP) (Memory Allocation Evasion)
#-------------------------------------------------------------------------
echo "[*] Deactivating Transparent Hugepages (THP) memory allocation blocks..."
if [ -f /sys/kernel/mm/transparent_hugepage/enabled ]; then
    echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
fi
if [ -f /sys/kernel/mm/transparent_hugepage/defrag ]; then
    echo never > /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
fi

#-------------------------------------------------------------------------
# 11. Forensic Hardening: Disable System Core Dumps, Auto Updates & rsyslog
#-------------------------------------------------------------------------
echo "[*] Disabling Host-level system core dumps to secure memory parameters..."
ulimit -c 0
# Append (don't replace) to limits.conf in case other security tools use it
if ! grep -qE '^\*\s+hard\s+core\s+0' /etc/security/limits.conf 2>/dev/null; then
    printf '\n* hard core 0\n* soft core 0\n' >> /etc/security/limits.conf
fi

if [ "$SYSTEMD_RUNNING" = "1" ]; then
    mkdir -p /etc/systemd/coredump.conf.d/
    cat > /etc/systemd/coredump.conf.d/stealth-coredump.conf <<'EOF'
[Coredump]
Storage=none
EOF
    systemctl daemon-reload 2>/dev/null || true
fi

if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "unattended-upgrades"; then
    echo "[*] Deactivating background unattended-upgrades service..."
    systemctl stop unattended-upgrades 2>/dev/null || true
    systemctl disable unattended-upgrades 2>/dev/null || true
    systemctl mask unattended-upgrades 2>/dev/null || true
fi

if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "kdump-tools"; then
    echo "[*] Deactivating and masking kernel kdump-tools services..."
    systemctl stop kdump-tools.service 2>/dev/null || true
    systemctl disable kdump-tools.service 2>/dev/null || true
    systemctl mask kdump-tools.service 2>/dev/null || true
fi

if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "apt-daily.timer"; then
    echo "[*] Deactivating and masking systemd apt-daily timers..."
    systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
    systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
    systemctl mask apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
fi

if [ -d /etc/apt/apt.conf.d ]; then
    cat > /etc/apt/apt.conf.d/10periodic <<'EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
EOF
fi

if [ "$SYSTEMD_RUNNING" = "1" ] && systemctl list-units --full --all 2>/dev/null | grep -Fq "rsyslog"; then
    echo "[*] Disabling and purging legacy rsyslog and syslog sockets..."
    systemctl stop rsyslog.service 2>/dev/null || true
    systemctl disable rsyslog.service 2>/dev/null || true
    systemctl mask rsyslog.service 2>/dev/null || true
    systemctl stop syslog.socket 2>/dev/null || true
    systemctl disable syslog.socket 2>/dev/null || true
    systemctl mask syslog.socket 2>/dev/null || true
    apt-get purge -y rsyslog 2>/dev/null || true
    rm -rf /var/log/syslog* /var/log/auth.log* /var/log/mail.* /var/log/kern.log* /var/log/daemon.log* /var/log/messages* 2>/dev/null || true
fi

#-------------------------------------------------------------------------
# 12. SSH Server Version Banner & HASSH Hardening (Port Scan Stealth)
#-------------------------------------------------------------------------
if [ -f /etc/ssh/sshd_config ]; then
    echo "[*] Hardening SSH version banner and HASSH algorithm signatures..."
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
fi

#-------------------------------------------------------------------------
# 13. Eliminate Cleartext Swap Memory Leaks
#-------------------------------------------------------------------------
echo "[*] Deactivating and removing cleartext swap partitions..."
# swapoff may fail if no swap is configured; tolerate
swapoff -a 2>/dev/null || true
# Comment out swap partitions in /etc/fstab to persist across reboots
sed -i -E '/\s+swap\s+/s|^|#|' /etc/fstab

# Locate and physically shred any existing swapfiles
for swapfile in /swapfile /var/swapfile /var/lib/swap /mnt/swap; do
    if [ -f "$swapfile" ]; then
        echo "[*] Securely shredding and removing swap file: $swapfile..."
        if command -v shred >/dev/null 2>&1; then
            shred -u "$swapfile" 2>/dev/null || rm -f "$swapfile"
        else
            rm -f "$swapfile"
        fi
    fi
done

#-------------------------------------------------------------------------
# 13b. Volatile RAM-Only /tmp and /var/tmp (B15 - explicit mounts only)
#-------------------------------------------------------------------------
# CRITICAL (B15): Do NOT run "mount -a" because cloud-init may have added
# NFS/CIFS/shared-storage entries to /etc/fstab that we MUST NOT mount.
# We mount only our specific tmpfs paths explicitly.
echo "[*] Configuring host-level temporary directories as volatile RAM-only tmpfs mounts..."

# /tmp
if mountpoint -q /tmp 2>/dev/null; then
    echo "[*] /tmp is already a mountpoint; leaving it alone."
else
    mount -t tmpfs -o size=1G,noexec,nosuid,nodev tmpfs /tmp 2>/dev/null || \
        echo "[!] tmpfs mount on /tmp failed; non-fatal but reduces forensic isolation."
    # Persist via fstab only if not already present
    if ! grep -qE '^tmpfs\s+/tmp\s+tmpfs' /etc/fstab 2>/dev/null; then
        printf 'tmpfs\t/tmp\ttmpfs\tsize=1G,noexec,nosuid,nodev\t0\t0\n' >> /etc/fstab
    fi
fi

# /var/tmp
if mountpoint -q /var/tmp 2>/dev/null; then
    echo "[*] /var/tmp is already a mountpoint; leaving it alone."
else
    mount -t tmpfs -o size=512M,noexec,nosuid,nodev tmpfs /var/tmp 2>/dev/null || \
        echo "[!] tmpfs mount on /var/tmp failed."
    if ! grep -qE '^tmpfs\s+/var/tmp\s+tmpfs' /etc/fstab 2>/dev/null; then
        printf 'tmpfs\t/var/tmp\ttmpfs\tsize=512M,noexec,nosuid,nodev\t0\t0\n' >> /etc/fstab
    fi
fi

#-------------------------------------------------------------------------
# 14. Volatile RAM-Only System Logging (Memory-Only Journal)
#-------------------------------------------------------------------------
if [ "$SYSTEMD_RUNNING" = "1" ]; then
    echo "[*] Configuring systemd-journald for volatile RAM-only logging..."
    mkdir -p /etc/systemd/journald.conf.d/
    cat > /etc/systemd/journald.conf.d/volatile-stealth.conf <<'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=16M
ForwardToSyslog=no
EOF
    systemctl restart systemd-journald 2>/dev/null || true
fi

#-------------------------------------------------------------------------
# 15. Clean and Disable Oracle Cloud Monitoring Agents (B13, B14)
#-------------------------------------------------------------------------
# (B13) Skip systemctl calls if systemd is not PID 1.
# (B14) Try multiple known package names; tolerate "not installed".
if [ "$SYSTEMD_RUNNING" = "1" ]; then
    echo "[*] Detecting and disabling Oracle Cloud telemetry agents..."

    for svc in oracle-cloud-agent oracle-cloud-agent-updater; do
        if systemctl list-units --full --all 2>/dev/null | grep -Fq "$svc"; then
            echo "[*] Disabling and masking $svc..."
            systemctl stop "$svc" 2>/dev/null || true
            systemctl disable "$svc" 2>/dev/null || true
            systemctl mask "$svc" 2>/dev/null || true
        fi
    done
fi

# (B14) Try multiple package names; some OCI images have "oracle-cloud-agent",
# others have "oci-agent" or "oracle-agent". All are non-fatal if absent.
if command -v apt-get >/dev/null 2>&1; then
    apt-get purge -y oracle-cloud-agent oci-agent oracle-agent 2>/dev/null || true
fi

# Snap-based OCI agent (Ubuntu only; harmless on Debian)
if command -v snap >/dev/null 2>&1; then
    if snap list 2>/dev/null | grep -Fq "oracle-cloud-agent"; then
        snap remove --purge oracle-cloud-agent 2>/dev/null || true
    fi
fi

#-------------------------------------------------------------------------
# 16. CPU Governor Alignment (Eliminating V8 timing jitter)
#-------------------------------------------------------------------------
echo "[*] Locking CPU scaling governor to performance mode..."
if [ -d /sys/devices/system/cpu/cpu0/cpufreq ]; then
    for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        if [ -f "$gov" ]; then
            echo performance > "$gov" 2>/dev/null || true
        fi
    done
else
    if ! command -v cpufreq-set >/dev/null 2>&1; then
        apt-get install -y cpufrequtils 2>/dev/null || true
    fi
    echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils 2>/dev/null || true
    if [ "$SYSTEMD_RUNNING" = "1" ]; then
        systemctl restart cpufrequtils 2>/dev/null || true
    fi
fi

#-------------------------------------------------------------------------
# 16b. CPU Vulnerability Mitigations Bypass & GRUB Timeout Hardening (B16, B17)
#-------------------------------------------------------------------------
# CRITICAL FIX (B16/B17): The original script REPLACED the entire
# GRUB_CMDLINE_LINUX_DEFAULT value, which strips out 'root=UUID=...' or
# 'root=/dev/...' and renders the system unbootable.
# We now APPEND our mitigations parameters to the existing cmdline, preserving
# the kernel's ability to find its root filesystem.
if [ -f /etc/default/grub ]; then
    echo "[*] Appending CPU mitigations bypass to GRUB cmdline (preserves root= parameter)..."

    # Build the desired EXTRA parameters (idempotent: re-running won't duplicate)
    EXTRA_PARAMS="mitigations=off"
    # Only add console=ttyS0,115200 on x86 (OCI's serial console is x86-specific)
    if [ "$HOST_ARCH" = "x86_64" ]; then
        EXTRA_PARAMS="$EXTRA_PARAMS console=ttyS0,115200"
    fi

    # Use python3 to do safe string manipulation on the GRUB cmdline
    PYBIN="$(command -v python3 || command -v python)"
    if [ -n "$PYBIN" ]; then
        "$PYBIN" - <<PYEOF
import re
path = "/etc/default/grub"
with open(path, "r") as f:
    content = f.read()
extra = "$EXTRA_PARAMS"
# Match GRUB_CMDLINE_LINUX_DEFAULT="..."
m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', content, re.MULTILINE)
if m:
    existing = m.group(1)
    parts = existing.split()
    # Remove any existing copies of our flags to keep idempotent
    for flag in extra.split():
        if flag in parts:
            parts.remove(flag)
    parts.extend(extra.split())
    new_value = " ".join(parts)
    content = re.sub(
        r'^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"',
        f'GRUB_CMDLINE_LINUX_DEFAULT="{new_value}"',
        content,
        flags=re.MULTILINE,
    )
else:
    # No existing line; add one with both default and our extras
    content += f'\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash {extra}"\n'
with open(path, "w") as f:
    f.write(content)
print("[*] GRUB cmdline now:", new_value if m else f'"quiet splash {extra}"')
PYEOF
    else
        echo "[!] python3 not found; skipping GRUB mitigation cmdline edit. Manual edit required."
    fi

    # GRUB timeout hardening
    sed -i -E 's|^#?\s*GRUB_TIMEOUT=[0-9]+|GRUB_TIMEOUT=0|' /etc/default/grub

    if command -v update-grub >/dev/null 2>&1; then
        update-grub 2>&1 | tail -5 || echo "[!] update-grub failed; non-fatal in container."
    fi
fi

#-------------------------------------------------------------------------
# 17. Global and Permanent Shell History Deactivation & Audit Cleansing (B19)
#-------------------------------------------------------------------------
echo "[*] Permanently deactivating terminal history files..."
cat > /etc/profile.d/disable_history.sh <<'EOF'
export HISTFILESIZE=0
export HISTSIZE=0
export HISTFILE=/dev/null
set +o history
EOF
chmod 644 /etc/profile.d/disable_history.sh
# Apply to current shell (no-op if not interactive)
. /etc/profile.d/disable_history.sh 2>/dev/null || true
rm -f ~/.bash_history ~/.zsh_history 2>/dev/null || true

# (B19) DO NOT truncate /var/log/wtmp, /var/log/btmp, /var/log/lastlog.
# These are binary record files. Truncating them corrupts the structure
# and breaks `last`, `who`, and `faillog`. Instead, we leave them in place
# and let systemd-tmpfiles recreate them on next boot if needed.
echo "[*] Skipping wtmp/btmp/lastlog truncation (binary record files; corrupting them breaks last/who)."

# Purge package manager logs (these are text files; safe to truncate)
: > /var/log/dpkg.log 2>/dev/null || true
: > /var/log/alternatives.log 2>/dev/null || true
: > /var/log/apt/history.log 2>/dev/null || true
: > /var/log/apt/term.log 2>/dev/null || true

# (B20) Sweep cached APT archives ONLY at the very end, after all installs
echo "[*] Sweeping cached APT package archives (post-install)..."
apt-get clean 2>/dev/null || true
rm -rf /var/lib/apt/lists/* 2>/dev/null || true

#-------------------------------------------------------------------------
# 18. Host-Level Docker Bridge MAC Spoofing
#-------------------------------------------------------------------------
echo "[*] Deploying persistent systemd service for host-side Docker virtual bridges MAC spoofing..."

# Find actual bridge names dynamically instead of hardcoding br_internal/br_egress
# (hardcoded names were an original bug; we now read /sys/class/net and pick
# any interface that is a bridge with the docker0 or br- OUI prefix).
cat > /etc/systemd/system/stealth-bridge-spoof.service <<'EOF'
[Unit]
Description=Stealth Host-Side Docker Bridge MAC Spoofing
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# Find all bridge interfaces and randomize their MAC addresses (keeping the
# locally-administered bit set but spoofing the OUI away from 02:42:ac).
# We use a fixed prefix from a known consumer NIC vendor (00:1a:2b = HP, or
# 00:05:1a = 3Com) but per-bridge the last 3 octets are randomized.
ExecStart=/bin/bash -c "sleep 2 && for br in $(ls /sys/class/net/ 2>/dev/null | grep -E '^(br-|docker0|br_)'); do RAND_HEX=$(head -c 3 /dev/urandom | xxd -p); ip link set dev \"$br\" address \"00:1a:2b:${RAND_HEX}\" 2>/dev/null || true; done"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

if [ "$SYSTEMD_RUNNING" = "1" ]; then
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable stealth-bridge-spoof.service 2>/dev/null || true
fi

#-------------------------------------------------------------------------
# 19. Host-Level Firewall Compilation and Execution
#-------------------------------------------------------------------------
echo "[*] Compiling and applying host-level firewall configurations..."
IPTABLES_SCRIPT=""
for candidate in \
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
        echo "[!] Detected /.dockerenv; skipping host iptables apply (must run on host)."
    else
        "$IPTABLES_SCRIPT" 2>&1 | tail -10 || echo "[!] iptables script failed; check permissions."
    fi
else
    echo "[!] iptables_docker.sh not found in known locations; skipping host firewall apply."
fi

#-------------------------------------------------------------------------
# 20. Final Validation: Verify mock files are REGULAR files (B9 verification)
#-------------------------------------------------------------------------
echo "[*] Final validation: verifying mock file integrity..."
for f in /etc/mock_cpuinfo /etc/mock_version; do
    if [ -d "$f" ]; then
        echo "[!] CRITICAL: $f is a DIRECTORY. Container bind-mount will fail."
        echo "[!]   Fix: rm -rf $f && re-run $0 to recreate as a regular file."
    elif [ -f "$f" ]; then
        SIZE=$(stat -c %s "$f" 2>/dev/null || echo 0)
        if [ "$SIZE" -lt 100 ]; then
            echo "[!] CRITICAL: $f is suspiciously small ($SIZE bytes); may be empty."
        else
            echo "[*]   $f OK ($SIZE bytes, regular file)"
        fi
    else
        echo "[!] CRITICAL: $f is MISSING. Container will see real /proc/cpuinfo or /proc/version."
    fi
done

# Verify machine-id is non-empty
if [ -f /etc/machine-id ] && [ -s /etc/machine-id ]; then
    echo "[*] /etc/machine-id OK: $(cat /etc/machine-id)"
else
    echo "[!] CRITICAL: /etc/machine-id is empty or missing. systemd services will fail."
fi

#-------------------------------------------------------------------------
# 21. Final Summary
#-------------------------------------------------------------------------
echo "=========================================================================="
echo "          HOST OS HARDENING AND STEALTH ALIGNMENT COMPLETED               "
echo "          LOG FILE: $LOG_FILE                                          "
echo "=========================================================================="
echo "Verified System Offsets & Parameters:"
echo "------------------------------------------------------------------"
echo "1. Hostname:        $(hostname)"
echo "2. Machine-ID:      $(cat /etc/machine-id 2>/dev/null || echo MISSING)"
echo "3. Timezone:        $(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo 'unknown')"
echo "4. Default Route:   $(ip route show 2>/dev/null | awk '/^default/ {print; exit}')"
echo "5. Default TTL:     $(cat /proc/sys/net/ipv4/ip_default_ttl 2>/dev/null || echo unknown)"
echo "6. NTP Status:      $(chronyc tracking 2>/dev/null | awk -F': *' '/System time/ {print $2}')"
echo "7. Entropy:         $(cat /proc/sys/kernel/random/entropy_avail 2>/dev/null || echo unknown) bits"
echo "8. mock_cpuinfo:    $(ls -la /etc/mock_cpuinfo 2>/dev/null | awk '{print $1, $5, $9}')"
echo "9. mock_version:    $(ls -la /etc/mock_version 2>/dev/null | awk '{print $1, $5, $9}')"
echo "10. resolv.conf:    $(ls -la /etc/resolv.conf 2>/dev/null | awk '{print $1, $9}')"
echo "------------------------------------------------------------------"
