# SECOND-PASS DEEP-DIVE AUDIT FINDINGS
# =======================================

**Date**: 2026-07-09
**Context**: After the first-pass audit, the user requested a re-check to ensure no fixes were missed, no conflicts exist, and no items are missing.

This document lists **9 additional issues** found in the second pass that the first audit missed.

---

## Issue #23: `setup_guide.md` lines 19, 44 — Wrong sysctl description
**Severity**: MEDIUM (documentation drift)
**File**: `setup_guide.md`
**Issue**:
- Line 19: `│   ├── sysctl_hardened.conf            # Host kernel adjustments (disable TCP Timestamps, set TTL 128)`
- Line 44: `**Mechanism**: Disables TCP Timestamps (net.ipv4.tcp_timestamps = 0) ... Overwrites default packet TTL to 128 ...`

This is the OLD v5.0+ Windows-spoofing description. The current v6.0+ design uses Debian 12 / Linux defaults (TTL=64, timestamps=1, sack=1, ECN=2).

**Fix**: Update lines 19 and 44 to reflect v6.0+ design.

## Issue #24: `setup_guide.md` line 134 — "Ubuntu Host" should be "Debian 12 Host"
**Severity**: MEDIUM (architecture inconsistency)
**File**: `setup_guide.md`
**Issue**: Line 134: "Run these commands on your Oracle Cloud Ubuntu Host:"
**Fix**: Change to "Debian 12 Host" (or "Debian 12 Linux Host").

## Issue #25: `MASTER_OPERATIONS_GUIDE.md` line 89 — "Ubuntu VM" should be "Debian 12 VM"
**Severity**: MEDIUM (architecture inconsistency)
**File**: `MASTER_OPERATIONS_GUIDE.md`
**Issue**: Line 89: "Execute these commands directly on your parent Oracle Cloud Ubuntu VM:"
**Fix**: Change to "Debian 12 VM".

## Issue #26: `TODO.md` line 72 — "Ubuntu 24.04 minimal x86_64 OR Debian 12" should be Debian 12 only
**Severity**: LOW (clarity)
**File**: `TODO.md`
**Issue**: Line 72: "Create VM.Standard.E2.1.Micro instance (1/8 OCPU, 1 GB RAM), Ubuntu 24.04 minimal x86_64 OR Debian 12."
**Fix**: Change to "Debian 12 (Bookworm) x86_64" (clearer; remove the OR Ubuntu option which contradicts the design decision).

## Issue #27: `HOST_OS_FINAL_AUDIT.md` Issue A2 — Says tcp_timestamps=0 is correct
**Severity**: MEDIUM (documentation drift)
**File**: `HOST_OS_FINAL_AUDIT.md`
**Issue**: Lines 11-13: "Status: tcp_timestamps=0 set. But Windows clients that DO use timestamps have a specific clock skew pattern. ... No further action needed — current setting is correct."
**Fix**: Update to reflect v6.0+ design (tcp_timestamps=1 is the correct Debian 12 default).

## Issue #28: `ENTRYPOINT_CLOAKBROWSER_AUDIT.md` line 312, 316 — Old entrypoint.sh state
**Severity**: MEDIUM (documentation drift)
**File**: `ENTRYPOINT_CLOAKBROWSER_AUDIT.md`
**Issue**: In the WITHDRAWN section, line 312 says docker-compose sets `ip_default_ttl=128` (no longer true; it's now 64). Line 316 says "entrypoint.sh sets net.ipv4.tcp_timestamps=0" (no longer true; it's now 1).
**Fix**: Update the WITHDRAWN section's "The bug (as originally stated)" historical description to clarify that these were the OLD v5.0+ values.

## Issue #29: `CHROME_VS_CHROMIUM_ARM64_AUDIT.md` — Says curl_cffi_handover default is Firefox118
**Severity**: LOW (audit doc accuracy)
**File**: `CHROME_VS_CHROMIUM_ARM64_AUDIT.md`
**Issue**: Line 152: "browser-runtime/curl_cffi_handover.py (default Firefox118 OK; dynamic to chrome120 works)"
**Fix**: Update to reflect that the default was changed to chrome120 in this audit cycle.

## Issue #30: `docker-compose.yml` line 331 — INVALID init syntax
**Severity**: CRITICAL (deployment will fail or silently ignore)
**File**: `docker-compose.yml`
**Issue**: `init: ["tini", "--grace-period=90000", "--"]` is INVALID syntax. The `init:` field in Docker Compose accepts only a boolean (true/false) to enable Docker's built-in tini. The list form is ignored.
**Impact**: tini is NOT actually being used. PID 1 is `entrypoint.sh` directly, which means:
- No signal forwarding wrapper (SIGTERM to PID 1 directly)
- The `--grace-period=90000` flag is being silently ignored
- The orchestrator's `handle_graceful_shutdown` may not have time to complete

**Fix**: Replace with `init: true` (built-in tini, 5s default grace) OR install tini in Dockerfile and reference it in ENTRYPOINT (recommended for the 90s grace period we need).

## Issue #31: `hardened_orchestrator.py` line 469 — tini reference in comment
**Severity**: LOW (stale comment)
**File**: `browser-runtime/hardened_orchestrator.py`
**Issue**: Comment says "Allow some headroom: the orchestrator itself, tini, chronyd, python helpers, etc."
**Fix**: After Issue #30 is fixed, the comment is still accurate (tini is part of the PID hierarchy). But if we use `init: true` (built-in tini), the comment is still correct. No change needed.

## Issue #32: `browser-runtime/entrypoint.sh` — Mock /proc/version is "Linux version 4.4.0-19041-Microsoft"
**Severity**: LOW (intentional but needs verification)
**File**: `browser-runtime/entrypoint.sh`
**Issue**: The mock /proc/version string says "Linux version 4.4.0-19041-Microsoft" which is a Windows Subsystem for Linux (WSL) kernel string. This is the persona's claimed kernel string (Windows 10's WSL).
**Verdict**: This is CORRECT for the persona. The bind-mounted mock file is the persona's spoofed value, not the host's actual kernel. The actual Linux container's kernel version is exposed elsewhere, but the bind mount overrides it. No change needed.

---

## Summary of 2nd-Pass Findings (Issues #23-#32)

| # | Severity | File | Issue |
|---|----------|------|-------|
| 23 | MEDIUM | setup_guide.md | Wrong sysctl_hardened.conf description (TTL=128, timestamps=0) |
| 24 | MEDIUM | setup_guide.md | "Ubuntu Host" → "Debian 12 Host" |
| 25 | MEDIUM | MASTER_OPERATIONS_GUIDE.md | "Ubuntu VM" → "Debian 12 VM" |
| 26 | LOW | TODO.md | "Ubuntu 24.04 OR Debian 12" → "Debian 12" |
| 27 | MEDIUM | HOST_OS_FINAL_AUDIT.md | Issue A2 says tcp_timestamps=0 is correct |
| 28 | MEDIUM | ENTRYPOINT_CLOAKBROWSER_AUDIT.md | WITHDRAWN section describes old docker-compose / entrypoint.sh state |
| 29 | LOW | CHROME_VS_CHROMIUM_ARM64_AUDIT.md | Says curl_cffi_handover default is Firefox118 |
| 30 | **CRITICAL** | docker-compose.yml | INVALID `init:` syntax; tini not actually running |
| 31 | LOW | hardened_orchestrator.py | (No change needed; comment is still accurate) |
| 32 | LOW | entrypoint.sh | (No change needed; mock /proc/version is intentional) |

**Critical issue: #30 must be fixed (tini not actually running).**
**Documentation issues: #23-#29 should be fixed for consistency.**
