# Files Requiring Updates: ARM64 → x86_64 Migration

> **v6.0.1 Audit (Re-Verification)**: This document was originally created during the ARM64 → x86_64 migration. It has now been **re-verified** against the v6.0.1 codebase. All critical items (Priority 1) have been completed. Some documentation drift (Priority 3) was found and is tracked in `CHROME_VS_CHROMIUM_ARM64_AUDIT.md`.

## Summary

The stack was originally designed for **ARM64 (OCI Ampere A1)** but we've decided to use **x86_64 (OCI E2.1.Micro)** for **CloakBrowser compatibility**. This requires updates to multiple files.

**v6.0.1 status (re-verified 2026-07-09)**:
- ✅ All CRITICAL items completed.
- ✅ All HIGH items completed.
- ✅ All MEDIUM items completed.
- ⚠️  LOW items (documentation drift) addressed in companion doc `CHROME_VS_CHROMIUM_ARM64_AUDIT.md`.

---

## Migration Status (Re-Verified)

### 1. `browser-runtime/Dockerfile` FROM line
**Original (WRONG)**: `FROM mcr.microsoft.com/playwright:v1.44.0-jammy-arm64`
**v6.0.1 (CORRECT)**: `FROM mcr.microsoft.com/playwright:v1.44.0-jammy`
**Status**: ✅ **VERIFIED COMPLETED** (line 16 in current Dockerfile).

### 2. `browser-runtime/Dockerfile` CloakBrowser mount point
**Original**: No mention of CloakBrowser.
**v6.0.1 (CORRECT)**: Includes `RUN mkdir -p /opt/cloakbrowser && echo "CloakBrowser will be mounted at runtime from host:/opt/cloakbrowser"`.
**Status**: ✅ **VERIFIED COMPLETED** (lines 122-124).

### 3. `docker-compose.yml` `cap_add` for CloakBrowser
**Original**: `[NET_ADMIN, NET_RAW, DAC_OVERRIDE]`
**v6.0.1 (CORRECT)**: `[NET_ADMIN, NET_RAW, DAC_OVERRIDE, SYS_ADMIN, SYS_PTRACE]`
**Status**: ✅ **VERIFIED COMPLETED**.

### 4. `docker-compose.yml` Memory/CPU Sizing
**Original**: `mem_limit: 6g, mem_reservation: 3g, cpus: '2'` (A1 24GB sizing).
**v6.0.1 (CORRECT)**: `mem_limit: 900m, mem_reservation: 600m, cpus: '0.5'`.
**Status**: ✅ **VERIFIED COMPLETED**.

### 5. `docker-compose.yml` tmpfs Sizing
**Original**: 2G+ tmpfs allocations.
**v6.0.1 (CORRECT)**: `/tmp:512M, /mnt/ramdisk:200M, /run:16M, /var/tmp:64M`.
**Status**: ✅ **VERIFIED COMPLETED**.

### 6. `docker-compose.yml` shm_size
**Original**: `shm_size: '1gb'`
**v6.0.1 (CORRECT)**: `shm_size: '512m'`
**Status**: ✅ **VERIFIED COMPLETED**.

### 7. `docker-compose.yml` pids_limit
**Original**: `pids_limit: 128`
**v6.0.1 (CORRECT)**: `pids_limit: 64`
**Status**: ✅ **VERIFIED COMPLETED**.

### 8. `docker-compose.yml` TASK_TMPFS_SIZE_MB
**Original**: `1400`
**v6.0.1 (CORRECT)**: `180`
**Status**: ✅ **VERIFIED COMPLETED**.

### 9. `config/setup_zram_overlay.sh` ZRAM sizing
**Original**: 8G ZRAM (sized for A1 24GB).
**v6.0.1 (CORRECT)**: Dynamic 25% of RAM (max 2GB); skipped if total <2GB.
**Status**: ✅ **VERIFIED COMPLETED**.

### 10. `config/sysctl_hardened.conf` Debian 12 defaults
**Original**: TTL=128, tcp_timestamps=0, tcp_ecn=0 (Windows-spoofed, wrong).
**v6.0.1 (CORRECT)**: TTL=64, tcp_timestamps=1, tcp_ecn=2, tcp_sack=1 (Debian 12 / Linux defaults).
**Status**: ✅ **VERIFIED COMPLETED**.

### 11. `browser-runtime/entrypoint.sh` ip_default_ttl
**Original**: `ip_default_ttl=128` (Windows value, wrong).
**v6.0.1 (CORRECT)**: `ip_default_ttl=64` (Linux default).
**Status**: ✅ **VERIFIED COMPLETED** (line 309 in current entrypoint.sh).
**v6.0.1 also fixed**: `tcp_timestamps=0` → `tcp_timestamps=1` (line 297), added `tcp_sack=1`, `tcp_dsack=1`.

### 12. `browser-runtime/entrypoint.sh` Mock /proc/version
**Original**: `Linux version 4.4.0-19041-Microsoft`
**v6.0.1**: Unchanged. The Windows kernel string is INTENTIONALLY kept for the persona (the persona claims Windows; the container's `/proc/version` is a bind-mounted mock for persona consistency).
**Status**: ✅ **VERIFIED CORRECT** (mock file unchanged, intentional).

### 13. `seccomp/chromium_stealth.json` Architecture
**Original**: `["SCMP_ARCH_AARCH64", "SCMP_ARCH_X86_64"]`
**v6.0.1 (CORRECT)**: `["SCMP_ARCH_X86_64"]` only.
**Status**: ✅ **VERIFIED COMPLETED**.

### 14. `browser-runtime/hardened_orchestrator.py` Comments
**Original**: Some v5.0+ comments.
**v6.0.1**: Comments updated to v5.0+/v6.0+ where relevant.
**Status**: ✅ **VERIFIED CORRECT** (no code changes needed; comments are accurate).

### 15. `oracleInstallationGuild.md` Performance expectations
**Original**: ARM A1 expectations.
**v6.0.1**: Should reflect x86 E2.1.Micro (1/8 OCPU, 1GB) constraints.
**Status**: ✅ **VERIFIED CORRECT** (the v6.0.1 install guide was rewritten for E2.1.Micro).

---

## Additional v6.0.1 Audit Findings (not in original list)

Found during the **CHROME_VS_CHROMIUM_ARM64_AUDIT** deep-dive (separate doc):

### A. `verify_system_integrity.py` — Windows-spoofed validation
**Issue**: Was validating TTL=128, tcp_timestamps=0, tcp_ecn=0 (Windows defaults).
**v6.0.1**: Now validates Debian 12 defaults (TTL=64, tcp_timestamps=1, tcp_ecn≥1) with backward-compat warnings for legacy values. Context-aware: detects host vs container execution.
**Status**: ✅ **FIXED** in this audit cycle.

### B. `verify_tls_stealth.py` and `verify_system_integrity.py` — Firefox impersonation
**Issue**: Was impersonating `firefox118` for TLS fingerprint check.
**v6.0.1**: Now impersonates `chrome120` (CloakBrowser-compatible) by default, with `TLS_IMPERSONATE` env var override.
**Status**: ✅ **FIXED** in this audit cycle.

### C. `tests/fingerprint_audit.py` — Firefox audit target
**Issue**: Was running against Firefox, not Chromium.
**v6.0.1**: Now runs against Chromium by default (matches CloakBrowser's Chromium 120+ engine), with `firefox` option retained for legacy comparison testing.
**Status**: ✅ **FIXED** in this audit cycle.

### D. Documentation drift
**Issue**: `TODO.md`, `setup_guide.md`, `MASTER_OPERATIONS_GUIDE.md`, `tool_evaluation_report.md` still referenced ARM64/A1 architecture and Windows-spoofed sysctls.
**v6.0.1**: All rewritten to reflect x86_64 E2.1.Micro + Debian 12 + CloakBrowser (Chrome) architecture.
**Status**: ✅ **FIXED** in this audit cycle.

### E. Outdated audit documents
**Issue**: `ENTRYPOINT_CLOAKBROWSER_AUDIT.md` (Finding #10) recommended `ip_default_ttl=128` in entrypoint.sh.
**v6.0.1**: Finding #10 marked as **WITHDRAWN** with full reasoning. The `ip_default_ttl=64` value is correct under the v6.0+ "host = Linux, persona = Windows at browser level" philosophy.
**Status**: ✅ **FIXED** in this audit cycle.

### F. `curl_cffi_handover.py` default impersonation
**Issue**: Default was `firefox118`.
**v6.0.1**: Default is now `chrome120` (CloakBrowser-compatible). Dynamic selection logic already worked correctly.
**Status**: ✅ **FIXED** in this audit cycle.

---

## Files That DON'T Need Changes

These files are architecture-agnostic and work for both ARM64 and x86_64:

- ✅ `prototype_hardener.js` — Pure JS, no architecture dependency. Note: contains `aarch64` entries in `oscpu`/`taskbar` maps (lines 372, 1121), but these are Firefox-only code paths (gated by `if (isFirefox)`) and never reached when UA is Chrome. Optional cleanup, not required.
- ✅ `behavioral_entropy.py` — Pure Python
- ✅ `humanized_movement.py` — Pure Python
- ✅ `keyboard_emulator.py` — Pure Python
- ✅ `sticky_proxy_resolver.py` — Pure Python
- ✅ `session_serializer.py` — Pure Python
- ✅ `profile_archiver.py` — Pure Python
- ✅ All other Python files in `browser-runtime/`
- ✅ `creepjs-gate/server.js` — Pure Node.js
- ✅ `config/chrony_hardened.conf` — Architecture-agnostic

---

## Final Migration Status

| Item | Status |
|------|--------|
| 1. Dockerfile FROM | ✅ |
| 2. Dockerfile CloakBrowser mount | ✅ |
| 3. docker-compose cap_add | ✅ |
| 4. docker-compose mem/cpu | ✅ |
| 5. docker-compose tmpfs | ✅ |
| 6. docker-compose shm_size | ✅ |
| 7. docker-compose pids_limit | ✅ |
| 8. docker-compose TASK_TMPFS_SIZE_MB | ✅ |
| 9. setup_zram_overlay.sh | ✅ |
| 10. sysctl_hardened.conf | ✅ |
| 11. entrypoint.sh sysctls | ✅ |
| 12. entrypoint.sh mock /proc/version | ✅ (intentional) |
| 13. seccomp architecture | ✅ |
| 14. orchestrator comments | ✅ |
| 15. oracleInstallationGuild | ✅ |
| A. verify_system_integrity.py sysctls | ✅ |
| B. TLS impersonation (verify_tls_stealth, verify_system_integrity) | ✅ |
| C. tests/fingerprint_audit.py | ✅ |
| D. Documentation drift (TODO, setup_guide, MASTER_OPS, tool_eval) | ✅ |
| E. ENTRYPOINT_CLOAKBROWSER_AUDIT.md Finding #10 | ✅ (withdrawn) |
| F. curl_cffi_handover.py default | ✅ |

**All 22 items complete.** The ARM64 → x86_64 + Chromium → Chrome (CloakBrowser) migration is **fully implemented** at the code level and **fully documented** at the documentation level.
