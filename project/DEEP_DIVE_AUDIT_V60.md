# Deep-Dive Audit Report: hardened_host_os.sh v6.0

## Remaining Issues Identified (Final Pass)

### CRITICAL Category

#### Issue C1: `kernel.panic = 10` is TOO LOW for a server
**Current value**: 10 seconds
**Issue**: Real Debian 12 server default is 0 (wait forever) or 30-60 seconds.
**Detection vector**: p0f/Nmap don't probe this, but it's a behavioral anomaly.
**FIX**: Change to 30 (or keep at 0 if you want to manually investigate panics).

#### Issue C2: `net.core.rmem_max = 212992` is Linux default but may leak cloud VM
**Current value**: 212992
**Issue**: Real Linux desktop: 212992. Real Linux server: 212992. Cloud VM: 212992.
**No actual issue** — this is the Debian default. Keep.

#### Issue C3: `kernel.printk = 3 4 1 3` may be unusual
**Current value**: 3 4 1 3
**Issue**: Debian 12 default: 4 4 1 7 (or similar). Our value is more restrictive.
**FIX**: Change to 4 4 1 7 to match Debian default.

#### Issue C4: `vm.dirty_background_ratio = 10, vm.dirty_ratio = 20` may be wrong
**Current value**: 10 and 20
**Issue**: Debian 12 default: depends on memory size. For systems with < 1GB memory, default is 10/20. For larger systems, it's typically vm.dirty_bytes/vm.dirty_background_bytes (ratio 0 with byte-based limits).
**FIX**: Verify defaults; the current value is reasonable for a small-memory stealth host.

#### Issue C5: Missing `kernel.kexec_load_disabled`
**Current**: Not set
**Default**: 0 (kexec enabled)
**Issue**: Disabling kexec load is a security hardening. Not a fingerprint vector.
**OPTIONAL FIX**: Set to 1.

#### Issue C6: Missing `kernel.modules_disabled`
**Current**: Not set
**Default**: 0 (module loading enabled)
**Issue**: Disabling module loading after boot is a security hardening. Not a fingerprint vector.
**OPTIONAL FIX**: Set to 1 (but this may break legitimate module loading).

#### Issue C7: Missing `kernel.sysrq`
**Current**: Not set
**Default**: 16 (enabled for keyboard)
**Issue**: Disabling sysrq is a security hardening. Not a fingerprint vector.
**OPTIONAL FIX**: Set to 0 (disabled).

#### Issue C8: Missing `kernel.unprivileged_userns_clone`
**Current**: Not set
**Default**: 0 on most kernels (unprivileged user namespaces disabled)
**Issue**: Enabling unprivileged user namespaces is needed for the container to work.
**FIX**: Set to 1 (enabled, needed for Docker).

#### Issue C9: Missing `net.ipv4.tcp_early_demux`
**Current**: Not set
**Default**: 1 (enabled in modern kernels)
**Issue**: This is a fingerprint vector. Real Debian 12 has it enabled by default.
**FIX**: Set explicitly to 1.

#### Issue C10: Missing `net.ipv4.tcp_l3mdev_accept`
**Current**: Not set
**Default**: 0 (disabled)
**Issue**: Low-signal vector.
**No action needed**.

#### Issue C11: `kernel.perf_cpu_time_max_percent = 1` may be too restrictive
**Current value**: 1
**Issue**: Debian default is 25 (allows 25% CPU for perf events).
**FIX**: Change to 25 (Debian default) or leave at 1 for stealth.

### HIGH Category

#### Issue H1: `apt-get install -y chrony rng-tools5` should be on separate lines
**Current**: Both in one command
**Issue**: If one fails, the other may not install.
**No action needed** — Bash handles this correctly.

#### Issue H2: `grub_timeout` set to 0 — boot menu invisible
**Current**: GRUB_TIMEOUT=0
**Issue**: For a stealth host, this is correct. No boot menu.
**No action needed**.

#### Issue H3: `cpufrequtils` package may not exist in Debian 12
**Current**: `apt-get install -y cpufrequtils`
**Issue**: `cpufrequtils` may be deprecated; Debian 12 uses `cpufrequtils` or `powercap-utils`.
**FIX**: Verify package name; alternative is `linux-cpupower` or use the sysfs directly.

#### Issue H4: `nohz_full=1-3` requires kernel support
**Current**: In GRUB cmdline
**Issue**: If kernel doesn't have NO_HZ_FULL support, this is ignored.
**FIX**: Verify kernel config has NO_HZ_FULL=y.

#### Issue H5: `rcu_nocbs=1-3` requires CONFIG_RCU_NOCB_CPU
**Current**: In GRUB cmdline
**Issue**: If kernel doesn't have RCU_NOCB_CPU, this is ignored.
**FIX**: Verify kernel config.

#### Issue H6: `kernel.kptr_restrict = 2` may be too restrictive
**Current value**: 2 (hide all kernel pointers)
**Issue**: Debian 12 default: 1 (hide pointers for non-root).
**FIX**: Set to 1 (Debian default) or keep at 2 for stealth.

#### Issue H7: Missing `fs.protected_*` settings
**Current**: Not set
**Default**: Debian 12 has these enabled (protected_hardlinks, protected_symlinks).
**FIX**: Add explicit settings.

#### Issue H8: `kernel.unprivileged_bpf_disabled = 1` — verify kernel supports
**Current value**: 1
**Issue**: Older kernels may not have this. Debian 12 with kernel 6.1+ supports it.
**FIX**: Verify kernel version >= 5.x.

#### Issue H9: Missing `net.ipv4.tcp_comp_sack_*` settings
**Current**: Not set
**Default**: Linux 6.1+ has these enabled.
**Issue**: Not a fingerprint vector.
**No action needed**.

#### Issue H10: `kernel.sched_latency_ns` etc. not set (correctly removed)
**Current**: Not set
**Issue**: This is intentional (L1 in v6.0 changelog).
**No action needed** — this was correctly identified as a Linux-only setting that would leak if probed.

### MEDIUM Category

#### Issue M1: `hostname "debian-XXXX"` uses lowercase + 8 chars
**Current**: `debian-$(tr -dc 'a-z0-9' </dev/urandom | head -c 8)`
**Issue**: Real Debian hostnames often have 4-12 chars. 8 is fine.
**No action needed**.

#### Issue M2: `machine-id` is 32 hex chars
**Current**: 32 hex chars (tr -dc 'a-f0-9' | head -c 32)
**Issue**: systemd machine-id format is exactly 32 hex chars with trailing newline (33 bytes).
**FIX**: Verify the trailing newline is written.

#### Issue M3: `dbus-uuidgen` output verification
**Current**: `tr -d '[:space:]'` strips newlines
**Issue**: If dbus-uuidgen output has more than 32 chars, we fall through to manual generation.
**No action needed** — the logic is correct.

#### Issue M4: `mock_version` file format
**Current**: Linux version string (Microsoft spoofed)
**Issue**: Format matches what `uname -r` would return for Windows. Verified correct.

#### Issue M5: `mock_dmi_id` UUID format
**Current**: 4c4c4544-XXXX-XXXX-8048-b2c04fXXXXXX (Dell UUID format)
**Issue**: 4c4c4544 is Dell's OUI. Format is correct.
**No action needed**.

#### Issue M6: `mock_boot_id` is 36-char UUID
**Current**: 36-char UUID
**Issue**: Real boot_id format is correct.

#### Issue M7: `mock_osrelease` is 20 chars
**Current**: "4.4.0-19041-Microsoft"
**Issue**: Matches Windows 10 kernel version. Correct.

#### Issue M8: `mock_cpuinfo` has 4 processors
**Current**: 4 processors
**Issue**: Matches the persona's hardwareConcurrency=4. Correct.

#### Issue M9: SSH hardening
**Current**: Debian banner off, curve25519 KEX, chacha20-poly1305
**Issue**: This matches real Debian 12 server defaults.
**No action needed**.

#### Issue M10: `unattended-upgrades` disabled
**Current**: Disabled and masked
**Issue**: Real Debian 12 servers often have this enabled.
**FIX**: Could be a detection vector. But for stealth, disabled is correct.

#### Issue M11: `apt-daily.timer` disabled
**Current**: Disabled and masked
**Issue**: Real Debian 12 has this enabled.
**FIX**: Could be a detection vector. But for stealth, disabled is correct.

#### Issue M12: `chrony` time source
**Current**: time.cloudflare.com, pool.ntp.org
**Issue**: Debian 12 default uses `0.debian.pool.ntp.org`. Cloudflare is custom.
**FIX**: Could be changed to Debian defaults for less fingerprint, but cloudflare is also legitimate.

### LOW Category (Cosmetic / Hardening)

#### Issue L1: `kernel.dmesg_restrict = 1` — verify container can still read
**Current**: 1 (restricted)
**Issue**: Container running in same host can read dmesg if it has CAP_SYSLOG.
**No action needed** — the orchestrator's entrypoint.sh checks this.

#### Issue L2: `kernel.kptr_restrict = 2` — too strict for debugging
**Current**: 2
**Issue**: Hides all kernel pointers. Debian default is 1.
**FIX**: Set to 1 to match Debian default.

#### Issue L3: `kernel.yama.ptrace_scope = 2` — restricts ptrace
**Current**: 2 (only root can ptrace)
**Issue**: Debian default is 1. Our value is more restrictive.
**FIX**: Set to 1 (Debian default) or keep at 2 for stealth.

#### Issue L4: Missing `kernel.apparmor_restrict_unprivileged_userns_disable`
**Current**: Not set
**Default**: 0
**FIX**: Could set for AppArmor hardening.

#### Issue L5: Missing `kernel.apparmor_restrict_unprivileged_unconfined`
**Current**: Not set
**Default**: 0
**FIX**: Could set for AppArmor hardening.

### ARCHITECTURAL Issues

#### Issue A1: Script runs as root, but has no `set -u` (undefined variable check)
**Current**: No `set -u`
**Issue**: If a variable is unset, bash silently uses empty string.
**FIX**: Add `set -euo pipefail` at the top.

#### Issue A2: `register_tmp -d` may not exist in older bash
**Current**: `register_tmp -d "/run/.mock_dmi.XXXXXX"`
**Issue**: `mktemp -d` syntax requires GNU coreutils 8+ (Debian 12 has it).
**No action needed**.

#### Issue A3: `mock_*` files in /etc/ may be overwritten by apt
**Current**: Files in /etc/
**Issue**: When packages install, they may overwrite /etc files.
**FIX**: Move to /var/lib/mock/ or similar. But bind-mounting from /etc/mock_* is what docker-compose.yml expects.

#### Issue A4: `chattr +i` for immutability of mock files
**Current**: Not set
**Issue**: Mock files can be modified by attackers or package managers.
**FIX**: Add `chattr +i` on mock files (if supported on the filesystem).

#### Issue A5: No verification that the iptables MSS clamp is in the right chain
**Current**: Adds to mangle OUTPUT chain
**Issue**: mangle OUTPUT affects host-originated packets only, not forwarded.
**FIX**: Add to mangle FORWARD chain as well (for container-originated traffic).

#### Issue A6: `chrony_hardened.conf` content not verified
**Current**: Loaded from file
**Issue**: If the file content is wrong, chrony may not sync properly.
**FIX**: Verify the chrony config file content.

#### Issue A7: `default_qdisc` not set
**Current**: Not set
**Default**: fq_codel
**Issue**: Not a fingerprint vector.
**No action needed**.

#### Issue A8: `tcp_congestion_control` not set in host script
**Current**: Set in entrypoint.sh to cubic
**Issue**: Host default is cubic on Debian 12. No mismatch.
**No action needed**.

#### Issue A9: Missing `setcap` / file capabilities cleanup
**Current**: Not addressed
**Issue**: Some packages install binaries with setcap. Could leak.
**FIX**: Optional, run `find / -xdev -type f -exec getcap {} \; 2>/dev/null` and review.

#### Issue A10: Missing audit rules (auditd)
**Current**: Not set
**Issue**: For forensic, audit rules can be tightened.
**FIX**: Optional hardening.

### DEBIAN REPOSITORY SPECIFIC Issues

#### Issue D1: The user mentioned "Ubuntu/Debian repositories" — verify
**Current**: Script uses `apt-get` with default repositories.
**Issue**: Debian 12 default sources.list includes:
  - deb http://deb.debian.org/debian bookworm main
  - deb http://deb.debian.org/debian bookworm-updates main
  - deb http://security.debian.org/debian-security bookworm-security main
**Status**: Correct. The script doesn't modify sources.list, so it uses whatever the host has.

#### Issue D2: Ubuntu vs Debian apt behavior
**Current**: Uses `apt-get`
**Issue**: apt-get works the same on Ubuntu and Debian. No issue.

#### Issue D3: Some packages may be in `contrib` or `non-free`
**Current**: `apt-get install -y <pkg>` uses `main` by default
**Issue**: If a package is only in `contrib` or `non-free`, install fails.
**FIX**: Ensure sources.list has all components enabled.

### Summary of Recommended Changes

Based on this deep-dive audit, the following are **recommended changes**:

1. **C1**: `kernel.panic = 10` → `kernel.panic = 30` (closer to server default)
2. **C3**: `kernel.printk = 3 4 1 3` → `kernel.printk = 4 4 1 7` (Debian default)
3. **C8**: Add `kernel.unprivileged_userns_clone = 1` (needed for containers)
4. **C9**: Add `net.ipv4.tcp_early_demux = 1` (Debian default, explicit)
5. **H3**: Verify `cpufrequtils` package exists in Debian 12 (alternative: `linux-cpupower`)
6. **H6**: `kernel.kptr_restrict = 2` → `kernel.kptr_restrict = 1` (Debian default)
7. **H7**: Add `fs.protected_hardlinks = 1` and `fs.protected_symlinks = 1`
8. **A1**: Add `set -euo pipefail` at script start
9. **A4**: Add `chattr +i` on mock files (where supported)
10. **A5**: Add mangle FORWARD chain MSS clamp too

The remaining items are either:
- Default values that are correct (no change needed)
- Hardening that doesn't affect the fingerprint
- Container-level concerns (handled by entrypoint.sh)
