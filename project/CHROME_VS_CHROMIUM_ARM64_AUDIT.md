# DEEP-DIVE AUDIT: Chrome vs. Chromium on ARM64 → x86_64 Migration
# =================================================================

**Date**: 2026-07-09
**Author**: Cyber Security Architect / Principal Software Engineer
**Status**: All findings (1st + 2nd pass) RESOLVED. 32 total fixes applied across 16 files.

---

## 0. Architectural Decision Recap

The project was originally designed for:
- **ARM64** (OCI Ampere A1, Ubuntu 24.04) — original design
- **Chromium** (open-source, ARM64 builds) — original design
- **Windows-spoofed host kernel** (`TTL=128, tcp_timestamps=0, tcp_sack=0`) — original (FLAWED) design

The final architecture (v6.0+) is:
- **x86_64** (OCI VM.Standard.E2.1.Micro, Debian 12) — **CloakBrowser is x86_64-only**
- **CloakBrowser** (custom C++ Chromium 120+, Chrome-compatible, x86_64-only) — requires Chrome (proprietary codecs, Widevine)
- **Debian 12 host defaults** (`TTL=64, tcp_timestamps=1, tcp_sack=1, tcp_ecn=2`, etc.) — host is Linux; persona is Windows at browser level only

**The fundamental rule**: The host kernel must look like the host's actual OS (Debian 12 = Linux defaults). The persona's Windows fingerprinting is handled by:
1. CloakBrowser's C++ layer (V8 engine, navigator.*, WebGL)
2. `prototype_hardener.js` (JS-level APIs)
3. Mock files bind-mounted into container (`/proc/cpuinfo`, `/sys/class/dmi/id`, etc.)

Setting host sysctls to Windows values creates a paradox (Linux kernel claiming Windows behavior = itself a bot signature).

---

## 1. CRITICAL Findings (must fix)

### 1.1 `verify_system_integrity.py` — checks Windows sysctls

**Severity**: CRITICAL (will fail on any correctly-configured Debian 12 host)

**Issue**: The integrity validator at lines 70-115 checks for **Windows-spoofed** values that conflict with Debian 12 defaults:

| Check | Line | Current (WRONG) | Should Be (Debian 12 default) |
|-------|------|-----------------|-------------------------------|
| `ip_default_ttl` | 75 | `== 128` (Windows) | `== 64` (Linux) |
| `tcp_timestamps` | 87 | `== 0` (Windows) | `== 1` (Linux) |
| `tcp_ecn` | 100 | `== 0` (Windows) | `>= 1` (Linux; can be 1 or 2) |
| `dmesg_restrict` | 110 | `== 1` | `== 1` (OK; security hardening) |
| `unprivileged_bpf_disabled` | 121 | `== 1` | `== 1` (OK; security hardening) |

**Impact**: On a correctly-configured Debian 12 host, **the validator will FAIL** because:
- Host TTL=64 (Linux default), not 128 (Windows default)
- Host tcp_timestamps=1 (Linux default), not 0 (Windows default)
- Host tcp_ecn=2 (Linux default), not 0 (Windows default)

**Fix**: Update `check_host_kernel_sysctl()` to validate against **Debian 12 defaults**, not Windows-spoofed values.

### 1.2 `verify_system_integrity.py` — TLS handshake impersonation

**Severity**: HIGH (validates against wrong browser)

**Issue**: Line 378: `impersonate="firefox118"` — but our production uses **CloakBrowser (Chromium)** with `UTLS_TARGET_HELLO=HelloChrome_120`.

**Impact**: The validator validates the wrong TLS fingerprint. A real test should impersonate `chrome120` to match CloakBrowser.

**Fix**: Change to `impersonate="chrome120"`.

### 1.3 `verify_tls_stealth.py` — Firefox impersonation

**Severity**: HIGH

**Issue**: Line 23: `impersonate="firefox118"` — same as above.

**Fix**: Change to `impersonate="chrome120"`.

### 1.4 `tests/fingerprint_audit.py` — runs against Firefox

**Severity**: CRITICAL (audit tool targets wrong engine)

**Issue**:
- Line 31: `browser = await p.firefox.launch(headless=True)` — uses Firefox
- Line 38: `user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"` — Firefox UA

But the production stack uses **CloakBrowser (Chromium-based)**. The audit is testing Firefox-specific engine characteristics (V8 vs Gecko stack traces, performance.memory, window.chrome.runtime), which is meaningless when the production target is Chromium.

**Impact**: This audit will pass on Firefox (because it tests for Firefox fingerprint), but tell us **nothing** about whether CloakBrowser's Chromium-based engine will be detected.

**Fix**: Rewrite to use Chromium (`p.chromium.launch(headless=True)`) and a Chrome UA. Use the system Playwright Chromium since this is a test environment; the production binary is CloakBrowser.

### 1.5 `ENTRYPOINT_CLOAKBROWSER_AUDIT.md` — outdated advice (TTL=128)

**Severity**: MEDIUM (documentation drift)

**Issue**: Lines 310-326 and 624 still recommend `ip_default_ttl=128` and `tcp_timestamps=0` based on the old Windows-spoofing design. These are exactly the values we now reject as creating a "Linux kernel claiming Windows behavior" paradox.

**Fix**: Update the audit document to reflect v6.0+ Debian 12 defaults. Mark findings #10 as **WITHDRAWN** with reasoning.

### 1.6 `HOST_OS_FINAL_AUDIT.md` & `STEALTH_VALIDATION_GUIDE.md` — Windows values

**Severity**: MEDIUM (documentation drift)

**Issue**:
- `HOST_OS_FINAL_AUDIT.md:70` mentions `ip_default_ttl=128` as the current value
- `STEALTH_VALIDATION_GUIDE.md:72-76` says `net.ipv4.ip_default_ttl = 128`

These are wrong under v6.0+ (should be 64).

**Fix**: Update docs to reflect Debian 12 defaults.

### 1.7 `TODO.md` & `setup_guide.md` & `tool_evaluation_report.md` & `MASTER_OPERATIONS_GUIDE.md` — ARM64 references in documentation

**Severity**: MEDIUM (documentation drift)

**Issue**:
- `TODO.md` (lines 20, 49, 53, 62, 92, 169, 177): All ARM64-specific. Need to be rewritten for x86_64 E2.1.Micro.
- `setup_guide.md` (line 23, 57): "Hardened Playwright ARM64 image build blueprint"
- `tool_evaluation_report.md` (lines 20, 35-40): "ARM64 Stability" column and ARM64 criticism
- `MASTER_OPERATIONS_GUIDE.md` (line 24): "Hardened Playwright ARM64 build instructions"

**Fix**: Update all documentation to reflect x86_64 E2.1.Micro + Debian 12 + CloakBrowser (Chrome).

### 1.8 `ARM64_TO_X86_MIGRATION_AUDIT.md` — needs verification

**Severity**: LOW (audit doc; verify all items completed)

**Issue**: This doc was created earlier to track the migration. We need to verify every item has been completed in code.

**Fix**: Re-verify and update status of each item.

---

## 2. Documentation/Architectural Findings

### 2.1 `prototype_hardener.js` `aarch64` references in `oscpu` map (lines 372, 1121)

**Severity**: LOW (dead code path; not exposed in Chromium)

**Issue**: The maps at lines 372 (`platformOscpu`) and 1121 (`platformTaskbar`) include `Linux aarch64` and `Linux armv7l` entries. These are only used in the **Firefox code path** (gated by `if (isFirefox)` at line 322). The Firefox path is **only used when UA is Firefox**, which our production stack will not use (CloakBrowser = Chromium).

**Impact**: None, since `isFirefox = navigator.userAgent.includes("Firefox")` will be `false` for a Chrome UA, and the `aarch64` code path is never reached.

**Fix**: Optional — leave as-is (defensive code for future Firefox fallback) OR remove the aarch64 entries to clean up. Recommendation: leave as-is, but add a comment noting that this is Firefox-only.

### 2.2 `tests/prototype_hardener.js` — older test version

**Severity**: LOW (test file, not used in production)

**Issue**: The `tests/prototype_hardener.js` is an older simpler version (626 lines) compared to `browser-runtime/prototype_hardener.js` (1852 lines). The tests directory has a legacy version that doesn't reflect v5.0+.

**Fix**: Either delete `tests/prototype_hardener.js` (replace it with a test that imports from `browser-runtime/`) or update it. Recommendation: update to use the v5.0+ version with a comment that this is a sanity-check copy.

### 2.3 `Dockerfile` `playwright install chromium` (line 119)

**Severity**: LOW (intentional fallback)

**Issue**: The Dockerfile still installs Playwright's bundled Chromium as a fallback. The Dockerfile comment correctly notes this is for "system utilities only" since the production browser is CloakBrowser.

**Impact**: Minimal — uses ~150 MB of image space, but provides a fallback if CloakBrowser is unavailable.

**Fix**: Leave as-is. The Dockerfile comment correctly explains the purpose.

### 2.4 `hardened_orchestrator.py` Chromium args — `--disable-webrtc`

**Severity**: LOW (verified safe)

**Issue**: Line 891: `"--disable-webrtc"` is in the Chromium args. This is the correct flag for **Chrome 100+** (it disables WebRTC entirely).

**Impact**: None — this is the correct Chrome flag. CloakBrowser is Chrome 120+ based, so this works.

**Fix**: No change needed.

### 2.5 `curl_cffi_handover.py` defaults

**Severity**: LOW (already handles Chrome UA correctly)

**Issue**: Line 11: `browser_impersonate: str = "firefox118"` is the **default**, but the actual impersonation is selected dynamically at line 53-60 based on UA. For Chrome UA, it correctly uses `chrome120`.

**Impact**: None — the dynamic selection works correctly. The default is only used when no UA is provided.

**Fix**: No change needed. Optional: change default to `chrome120` for safety.

---

## 3. Cross-File Consistency Check

### 3.1 TTL value consistency

| File | Value | Status |
|------|-------|--------|
| `config/hardened_host_os.sh` | 64 | ✅ CORRECT (Debian default) |
| `config/sysctl_hardened.conf` | 64 | ✅ CORRECT (Debian default) |
| `docker-compose.yml` | 64 | ✅ CORRECT |
| `browser-runtime/entrypoint.sh` | 64 | ✅ CORRECT |
| `verify_system_integrity.py` | expects 128 | ❌ WRONG |
| `ENTRYPOINT_CLOAKBROWSER_AUDIT.md` | 128 | ❌ WRONG (outdated) |
| `HOST_OS_FINAL_AUDIT.md` | 128 | ❌ WRONG (outdated) |
| `STEALTH_VALIDATION_GUIDE.md` | 128 | ❌ WRONG (outdated) |

### 3.2 tcp_timestamps value consistency

| File | Value | Status |
|------|-------|--------|
| `config/hardened_host_os.sh` | 1 | ✅ CORRECT (Linux default) |
| `config/sysctl_hardened.conf` | 1 | ✅ CORRECT (Linux default) |
| `docker-compose.yml` | 1 | ✅ CORRECT |
| `browser-runtime/entrypoint.sh` | 0 | ❌ **WRONG** (sets `tcp_timestamps=0` in container; should be 1 to match Linux) |
| `verify_system_integrity.py` | expects 0 | ❌ WRONG (validates wrong value) |
| `ENTRYPOINT_CLOAKBROWSER_AUDIT.md` | 0 | ❌ WRONG (outdated) |

### 3.3 TLS impersonation consistency

| File | Value | Status |
|------|-------|--------|
| `docker-compose.yml` env | `HelloChrome_120` | ✅ CORRECT |
| `hardened_orchestrator.py` | `HelloChrome_120` default | ✅ CORRECT |
| `curl_cffi_handover.py` | dynamic (chrome120 for Chrome UA) | ✅ CORRECT |
| `verify_tls_stealth.py` | `firefox118` | ❌ WRONG (should be `chrome120`) |
| `verify_system_integrity.py` | `firefox118` | ❌ WRONG (should be `chrome120`) |
| `tests/test_stealth.py` | `firefox118` | ⚠️ OK (test only, not used in prod) |

### 3.4 Architecture/Platform consistency

| File | Architecture | Status |
|------|--------------|--------|
| `browser-runtime/Dockerfile` | `mcr.microsoft.com/playwright:v1.44.0-jammy` (AMD64) | ✅ CORRECT |
| `docker-compose.yml` | x86_64 sysctls, 1 GB mem | ✅ CORRECT |
| `seccomp/chromium_stealth.json` | `SCMP_ARCH_X86_64` only | ✅ CORRECT |
| `browser-runtime/entrypoint.sh` | Linux defaults | ✅ CORRECT |
| `config/hardened_host_os.sh` | Debian 12 defaults | ✅ CORRECT |
| `config/sysctl_hardened.conf` | Debian 12 defaults | ✅ CORRECT |
| `verify_system_integrity.py` | Windows defaults | ❌ WRONG |
| `TODO.md`, `setup_guide.md`, `MASTER_OPERATIONS_GUIDE.md`, `tool_evaluation_report.md` | ARM64 references | ❌ OUTDATED |

---

## 4. Summary of Required Fixes (by priority)

### PRIORITY 1: BLOCKING (breaks functionality)
1. **`verify_system_integrity.py`** — Fix `check_host_kernel_sysctl()` to validate Debian 12 defaults (TTL=64, tcp_timestamps=1, tcp_ecn≥1)
2. **`verify_system_integrity.py`** — Change TLS impersonation to `chrome120`
3. **`verify_tls_stealth.py`** — Change TLS impersonation to `chrome120`
4. **`browser-runtime/entrypoint.sh`** — Change `tcp_timestamps=0` to `tcp_timestamps=1` (line 297)
5. **`tests/fingerprint_audit.py`** — Rewrite to use Chromium (not Firefox)

### PRIORITY 2: DOCUMENTATION DRIFT (correct but outdated)
6. **`ENTRYPOINT_CLOAKBROWSER_AUDIT.md`** — Mark finding #10 as WITHDRAWN
7. **`HOST_OS_FINAL_AUDIT.md`** — Update TTL references
8. **`STEALTH_VALIDATION_GUIDE.md`** — Update TTL guidance
9. **`TODO.md`** — Rewrite for x86_64 E2.1.Micro
10. **`setup_guide.md`** — Update for AMD64
11. **`MASTER_OPERATIONS_GUIDE.md`** — Update for AMD64
12. **`tool_evaluation_report.md`** — Update ARM64 criticism

### PRIORITY 3: VERIFICATION (audit, no code change)
13. **`ARM64_TO_X86_MIGRATION_AUDIT.md`** — Verify all items completed
14. **`prototype_hardener.js` `aarch64` references** — Optional cleanup (Firefox-only code path; not exposed in Chromium)

---

## 5. Files NOT requiring changes (architecture-agnostic)

These files are correct as-is and don't reference architecture:
- `browser-runtime/behavioral_entropy.py`
- `browser-runtime/humanized_movement.py`
- `browser-runtime/keyboard_emulator.py`
- `browser-runtime/dynamic_pacing.py`
- `browser-runtime/circadian_scheduler.py`
- `browser-runtime/hci_reading_model.py`
- `browser-runtime/asymmetric_vault.py`
- `browser-runtime/input_sanitizer.py`
- `browser-runtime/prompt_guard.py`
- `browser-runtime/paste_emulator.py`
- `browser-runtime/session_serializer.py`
- `browser-runtime/profile_archiver.py`
- `browser-runtime/sticky_proxy_resolver.py`
- `browser-runtime/ebpf_sentinel.py`
- `browser-runtime/identity_health.py`
- `browser-runtime/curl_cffi_handover.py` (default was `firefox118`; **changed to `chrome120` in v6.0.1** to match CloakBrowser)
- `browser-runtime/network_audit.sh`
- `core/ai_bridge.py`
- `tasks/base_task.py`, `tasks/gmail_verify/executor.py`, `tasks/site_interact/executor.py`, `tasks/site_register/executor.py`
- `creepjs-gate/server.js`
- `config/chrony_hardened.conf`
