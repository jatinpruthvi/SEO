# Stealth Validation & Forensic Audit Guide: The Invisible Stack

This document serves as the master **Verification, Validation, and Forensic Audit Guide** for the **Parasite SEO Automation Factory (The Invisible Stack)**. It outlines the specific step-by-step checks, commands, and forensic scripts required to verify that the Host OS, container network, and browser execution layers are completely aligned and invisible to advanced SIVT (Sophisticated Invalid Traffic) and anti-bot systems in **2026**.

---

## 1. The Stealth Validation Matrix

Use this matrix to trace which structural defense maps to each forensic verification check:

```text
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │                             STEALTH VALIDATION MATRIX                                  │
 ├─────────────────────┬────────────────────────────────────┬─────────────────────────────┤
 │    Audit Layer      │         Detection Vector           │    Verification Check       │
 ├─────────────────────┼────────────────────────────────────┼─────────────────────────────┤
 │ Host Operating Sys  │ TCP Handshake Packet TTL/Timestamp │ p0f Passive TCP Audit       │
 │ Host Clock & Time   │ System Clock Skew & Drift          │ NTP Offset & Sync Tracking  │
 │ Container Network   │ Raw IP & Datacenter DNS Leakage    │ DNS-over-HTTPS & STUN Probe │
 │ Browser Javascript  │ Prototype pollution & webdriver=true│ V8 Prototype Integrity Test │
 │ Device Hardware     │ Mesa LLVMPipe WebGL Software GPU   │ WebGL Renderer Query        │
 │ Physical Entropy    │ Mathematical Canvas / Audio hashes │ Canvas & Audio Noise Audit  │
 │ Behavioral Pacing   │ Static timing / linear scrolls     │ Log-Normal Deceleration Test│
 └─────────────────────┴────────────────────────────────────┴─────────────────────────────┘
```

---

## 2. Step-by-Step Validation & Verification Commands

Execute the following sequential verification phases prior to launching live automation tasks:

```text
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 1: Host OS Forensic Auditing (Debian 12)              │
 │  ├── Verify TCP packet default TTL is 64 (Linux default)    │
 │  ├── Confirm TCP timestamps are enabled (1, Linux default)  │
 │  └── Check system clock drift and NTP synchronization       │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 2: Container Network & Leak Prevention Auditing       │
 │  ├── Execute test run to check active container interfaces   │
 │  └── Audit container outbound routes for proxy kill-switches│
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 3: Client-Side Fingerprint Auditing                    │
 │  ├── Run unit tests to check prototype overrides            │
 │  ├── Verify canvas and WebGL hardware strings are spoofed   │
 │  └── Validate Web Audio API channel buffer noise            │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 4: Network Handshake (JA3/JA4) Auditing               │
 │  └── Verify outbound JA3 ciphers and ALPN frames match      │
 │      Chrome 120 (CloakBrowser-compatible)                    │
 └─────────────────────────────────────────────────────────────┘
```

---

### Phase 1: Host OS Forensic Auditing

#### Test 1.1: Verify TCP Packet TTL & Timestamps (Debian 12 defaults)
This test ensures that the host kernel parameters match Debian 12 / Linux 6.1.x defaults. The host IS Linux; the persona's Windows fingerprint is handled at the BROWSER level by CloakBrowser's C++ layer + `prototype_hardener.js`.

**v6.0.1 design rule**: Setting host sysctls to Windows values (TTL=128, timestamps=0) creates a Linux kernel claiming Windows behavior — this is itself a fingerprint vector. The host must look like Linux.

* **Command**:
  ```bash
  sysctl net.ipv4.ip_default_ttl net.ipv4.tcp_timestamps
  ```
* **Expected Output** (Debian 12 / Linux):
  ```text
  net.ipv4.ip_default_ttl = 64
  net.ipv4.tcp_timestamps = 1
  ```
* **Failure Remediation**: Re-run the host hardening script:
  ```bash
  sudo ./project/config/hardened_host_os.sh
  ```

  **Note**: The persona's claimed Windows TTL=128 is enforced at the browser/application level by CloakBrowser's C++ layer, not by the host kernel. The first hop (container → residential proxy) uses host TTL=64 (Linux); the second hop (proxy → target) uses the persona's claimed TTL=128.

#### Test 1.2: Verify High-Precision Clock Synchronization
This test ensures that your Host clock does not drift, preventing clock-skew detection flags.

* **Command**:
  ```bash
  chronyc tracking
  ```
* **Expected Output**:
  ```text
  Reference ID    : ...
  Stratum         : ...
  System time     : 0.000010234 seconds slow of NTP time  <-- Must be < 0.010 seconds (10ms)
  Leap status     : Normal
  ```
* **Failure Remediation**: Step the clock manually: `sudo chronyc -a makestep`.

---

### Phase 2: Container Network & Leak Prevention Auditing

#### Test 2.1: Audit DNS and IP Leakage (STUN / WebRTC)
Verify that container-level name resolutions and WebRTC requests do not bypass the proxy sidecars and leak your OCI VM's real IP and datacenter DNS.

* **Command**:
  Run this verification curl inside the active browser container to ensure it cannot reach any external endpoint except through port `1080`:
  ```bash
  docker compose exec -T orchestrator curl -sS https://icanhazip.com
  ```
* **Expected Output**:
  ```text
  curl: (7) Failed to connect to icanhazip.com port 443: Connection refused  <-- Success (Fails Closed)
  ```
* **Failure Remediation**: Re-run the container-level iptables firewall script:
  ```bash
  docker compose exec -T orchestrator /entrypoint.sh
  ```

---

### Phase 3: Client-Side Fingerprint Auditing

#### Test 3.1: Execute Automated Stealth Tests
This runs our automated QA suite to verify that all prototype-proxy traps, Web Audio noise modulators, asymmetric vaults, and pacing engines are fully loaded and operational.

* **Command**:
  ```bash
  cd /home/user/project/browser-runtime
  python3 -m unittest discover -s ../tests -p "test_*.py"
  ```
* **Expected Output**:
  ```text
  .........
  ----------------------------------------------------------------------
  Ran 9 tests in 0.019s

  OK
  ```
* **Failure Remediation**: Check `/home/user/project/browser-runtime/prototype_hardener.js` files and verify system python dependencies are installed.

---

### Phase 4: Network Handshake (JA3/JA4) Auditing

#### Test 4.1: Verify JA3/JA4 TLS Handshake Mimicry (Chrome 120 / CloakBrowser)
This verification check ensures that your HTTP connections mimic standard Chrome 120 handshakes at the SSL socket level, bypassing JA3/JA4 signature inspection. The production stack uses **CloakBrowser (Chromium 120+)**, so the TLS fingerprint must match Chrome 120's TLS ClientHello / JA3 / JA4 patterns.

* **Command**:
  Run our forensics verification script on the host machine:
  ```bash
  python3 /home/user/project/browser-runtime/verify_tls_stealth.py
  ```
* **Expected Output**:
  ```text
  ================== FORENSIC NETWORK REPORT ==================
  1. IP Address:      185.xxx.xxx.xxx  (Your Premium Residential Egress IP)
  2. JA3 Hash:        cd08e31494f9531f560d64c695473da9  (Chrome 120 cluster)
  3. JA3 User Agent:  Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
  4. TLS Target:      chrome120
  5. Handshake Match: True
  =============================================================
  ```
* **v6.0.1 change**: Default impersonation target is `chrome120` (was `firefox118` in v5.0+). Override via `TLS_IMPERSONATE` env var if needed.
* **Failure Remediation**: Verify `curl_cffi` is installed and the `HybridHandoverEngine` is active inside the execution thread. Verify the proxy is correctly routing egress traffic.

---

## 3. Operational Troubleshooting & Remediation Guide

### Problem A: CPU Throttling and Concurrency Drops
* *Symptom*: Browser pages time out, or average CPU utilization on the OCI instance spikes above 85%, risking always-free resource reclamation.
* *Diagnostic Command*:
  ```bash
  top -b -n 1 | head -n 20
  ```
* *Action Plan*: 
  1. Decrease `MAX_CONCURRENT_PERSONAS` inside `hardened_orchestrator.py` from `15` to `8–10`.
  2. Ensure the media request interception blocklist is active inside your Playwright launch profile to completely block loading images, video, and unneeded stylesheets.

### Problem B: Stale / Bloated ZRAM memory disk space
* *Symptom*: Worker threads crash with "No space left on device" errors when writing session parameters.
* *Diagnostic Command*:
  ```bash
  df -h /mnt/compressed_ramdisk
  ```
* *Action Plan*: Run our background pruning cron script to clear redundant profile caches and temp files:
  ```bash
  /home/user/project/browser-runtime/prune_profiles.sh
  ```

### Problem C: Database Connection Refused (Pool Starvation)
* *Symptom*: Orchestrator worker loops print `asyncpg.exceptions.TooManyConnectionsError`.
* *Diagnostic Command*:
  ```bash
  docker compose logs postgres | tail -n 30
  ```
* *Action Plan*: Ensure the database pool size limits are strictly configured inside `hardened_orchestrator.py` (`min_size=2`, `max_size=20`), and that connection blocks are properly wrapped inside `async with db_pool.acquire() as conn` context blocks to ensure absolute, safe cleanups.
