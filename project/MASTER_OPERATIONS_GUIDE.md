# MASTER OPERATIONS & SETUP GUIDE: THE INVISIBLE STACK

This document serves as the **Consolidated Setup, Configuration, and Operational Playbook** for the **Parasite SEO Automation Factory (The Invisible Stack)**. It brings together all operational instructions, file definitions, deployment checklists, and recent critical architectural adjustments into a single, unified reference guide.

---

## 1. Project Directory Mapping

The production workspace is structured with the following technical layout:

```text
/home/user/project/
├── MASTER_OPERATIONS_GUIDE.md          # This document (Consolidated Setup & File Map)
├── verify_system_integrity.py          # Master system-level Python validator
├── GAP_FIXES.md                        # Master prioritized gap remediation registry
├── docker-compose.yml                  # Micro-services orchestration file
├── config/
│   ├── sysctl_hardened.conf            # Host kernel adjustments (TCP Spoofing / Reverse Path Filter)
│   ├── iptables_docker.sh              # Host firewall rules (DOCKER-USER loopback isolations)
│   └── setup_zram_overlay.sh           # ZRAM mount / unmount overlay utility
├── database/
│   └── schema.sql                      # Unlogged PostgreSQL database structure
├── browser-runtime/
│   ├── Dockerfile                      # Hardened Playwright AMD64 build instructions (CloakBrowser-compatible)
│   ├── entrypoint.sh                   # Fail-closed local container firewall script & TCP MSS clamping
│   ├── hardened_orchestrator.py        # Asynchronous master worker pipeline (Dynamic Engine Select & Pooled DB)
│   ├── session_serializer.py           # Cookies, LocalStorage, & sessionStorage serializer
│   ├── humanized_movement.py           # Gaussian tremor spline mouse generator
│   ├── keyboard_emulator.py            # Physiological keypress timing generator
│   ├── paste_emulator.py               # Human copy-paste simulation script
│   ├── dynamic_pacing.py               # Log-normal scrolling & reading engine
│   ├── asymmetric_vault.py             # Asymmetric secp256k1 key-derivation engine
│   ├── ebpf_sentinel.py                # Process-level active socket verifier
│   ├── circadian_scheduler.py          # Localized circadian rhythm sleep lockout
│   ├── curl_cffi_handover.py           # JA3/JA4 TLS handshake handover engine
│   └── prototype_hardener.js           # Prototype, WebGL, Canvas, and WebGPU spoof traps with Dynamic FP_CONFIG
└── tests/
    └── test_stealth.py                 # Core async integration unit tests
```

---

## 2. File-by-File Core System Breakdown & Modern Enhancements

* **`verify_system_integrity.py`**: The unified system validator. Proactively scans Host OS kernel parameters, RAM disk mount status, PostgreSQL schemas, asymmetric ECDH systems, HCI reading models, security input sanitizers, adversarial LLM prompt guards, and outward TLS JA3/JA4 handshakes. **If any verification checks fail, it raises exit status code 1, stopping all execution instantly.**
* **`config/sysctl_hardened.conf`** (v6.0.1): Uses **Debian 12 / Linux 6.1.x defaults** (TTL=64, tcp_timestamps=1, tcp_sack=1, tcp_ecn=2) — NOT Windows-spoofed values. The host IS Linux; the persona's Windows fingerprint is handled at the BROWSER level by CloakBrowser's C++ layer + `prototype_hardener.js`. IPv6 is completely disabled to prevent dual-stack leaks bypassing SOCKS5.
* **`config/setup_zram_overlay.sh`** (v6.0.1): Dynamically allocates 25% of total RAM (max 2 GB) as a ZSTD-compressed swap space for the optional 4+ GB instance tier. For the 1 GB E2.1.Micro, ZRAM is skipped (use tmpfs only). Formatted with **journal-less ext4** to cut CPU cycles and eliminate SSD wear-leveling failures.
* **`browser-runtime/prototype_hardener.js`**: Intercepts V8 prototype chains, spoofing WebGL drivers, audio oscillators, and canvas rendering contexts. Upgraded with an isolated `FP_CONFIG` block for **dynamic configuration updates** on-the-fly.
* **`browser-runtime/hardened_orchestrator.py`**: The main execution engine. Spawns workers, validates proxy sidecars, runs persistent profiles, and sweeps workspaces cleanly. Upgraded with:
  1. **Dynamic Engine Selection**: Launches Chromium for V8-aligned user-agents and Firefox for Gecko user-agents.
  2. **Database Starvation Shield**: Connection is acquired, queried, and released immediately in `<15ms` rather than hanging open during page visits.
  3. **V8 Heap Constraints**: Restricts old space to `512MB` (`--js-flags=--max-old-space-size=512`) to prevent container OOM shutdowns.
  4. **Pre-flight RAM sweeps**: Clears ZRAM profile directories prior to execution to resolve orphaned lock files.
  5. **Dynamic Injection**: Overwrites the `FP_CONFIG` block inside `prototype_hardener.js` using regular expressions.

---

## 3. Step-by-Step Production Setup Guide

```text
 ┌─────────────────────────────────────────────────────────────┐
 │ Step 1: Apply Host OS Security & Kernel Hardening           │
 │  ├── Execute sysctl parameters (disable timestamps, set TTL)│
 │  ├── Run host-level iptables script                         │
 │  └── Initialize compressed ZRAM block devices               │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Step 2: Spin Up Docker Micro-Services Stack                 │
 │  ├── Write environment configurations (.env)                 │
 │  └── Compile Playwright and launch Docker Compose           │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Step 3: Run Database Migrations                             │
 │  └── Initialize unlogged database schemas                    │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Step 4: Run the Master System-Level Python Validator        │
 │  └── Execute verify_system_integrity.py (Halts on failure)  │
 └─────────────────────────────────────────────────────────────┘
```

### Step 1: Apply Host OS Security & Kernel Hardening
Execute these commands directly on your parent Oracle Cloud Debian 12 VM:

```bash
# 1. Inject hardened kernel network parameters
sudo cp config/sysctl_hardened.conf /etc/sysctl.d/99-hardened-stealth.conf
sudo sysctl --system

# 2. Configure safe host-level firewall rules
chmod +x config/iptables_docker.sh
./config/iptables_docker.sh

# 3. Mount compressed ZRAM RAM Disk workspaces
chmod +x config/setup_zram_overlay.sh
./config/setup_zram_overlay.sh
```

### Step 2: Spin Up Docker Micro-Services Stack
Configure your credentials and launch the services:

```bash
# 1. Create your environment parameters (.env)
cat <<EOF > .env
DB_PASSWD=MasterDBSecret_9918
REDIS_PASSWD=MasterRedisSecret_1129
ENCRYPTION_KEY=aueoDaDPCmWvEQts_FIAnk6QlIbw8Wik7y4dMynxlMU=
UPSTREAM_RES_PROXY=socks5://user:pass@gate.residentialprovider.com:1080

# Pluggable AI Bridge Configuration
LLM_PROVIDER=openai             # Choices: 'openai', 'anthropic', or 'ollama' (default)
LLM_MODEL=gpt-4o-mini           # e.g., 'gpt-4o-mini', 'claude-3-5-sonnet-20241022', or local 'llama3'
LLM_API_KEY=sk-proj-YourOpenAIApiKey1234567890
EOF

# 2. Build Playwright base images & run Docker Compose
docker compose build --no-cache
docker compose up -d
```

### Step 3: Run Database Migrations
Initialize database tables:

```bash
# Initialize unlogged database schemas
docker compose exec -T postgres psql -U engine_user -d core_db < database/schema.sql
```

### Step 4: Run the Master System-Level Python Validator
Run the unified Python validator to audit every single layer of your deployment. **If any checkpoint fails, it halts execution instantly with exit code 1 to protect your security**:

```bash
# Execute master validator
python3 verify_system_integrity.py
```

---

## 4. Verification Checkpoint Diagnostic Rules

If `verify_system_integrity.py` prints a **[FAIL]** status, apply these diagnostics immediately:

* **PostgreSQL Vault [FAIL]**:
  Ensure PostgreSQL is up and that database migrations have run:
  `docker compose exec -T postgres psql -U engine_user -d core_db -c "\dt"`
* **ZRAM/RAM Disk Mount [FAIL]**:
  Verify `/mnt/compressed_ramdisk` (or your mapped `RAM_DISK_DIR`) is mounted:
  `df -h /mnt/compressed_ramdisk`
* **Network TLS Handshake [FAIL]**:
  Verify that your upstream residential proxy is active and that your sidecar containers route sockets correctly on port `1080`:
  `curl --proxy socks5://localhost:1080 https://icanhazip.com`

---

## 5. Architectural Upgrades & Operational Guardrails (IMPORTANT)

When starting or updating development, keep these critical mechanics in mind:

### 5.1. Dynamic Configuration Injection
Do **not** hardcode configurations (like browser core count or graphics cards) inside `prototype_hardener.js`. The orchestrator will dynamically overwrite the `FP_CONFIG` block at the top of the file before page launch. Ensure this block is untouched in source control:
```javascript
const FP_CONFIG = {
    hardwareConcurrency: 8,
    deviceMemory: 8,
    webglVendor: "Intel Inc.",
    webglRenderer: "Intel(R) Iris(TM) Plus Graphics",
    platform: "Win32",
    languages: ["en-US", "en"]
};
```

### 5.2. Database Pool Lock Protection
Never acquire a DB connection and run page navigation inside its block. Release the connection immediately back to the pool before launching Playwright:
```python
# CORRECT IMPLEMENTATION:
async with db_pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ...") # <15ms transaction
# release happens automatically here. Now proceed with 10-minute browser run safely.
```

### 5.3. V8 RAM Memory Swelling Prevention
Always specify standard heap bounding clamps when launching Chromium persistent contexts inside unprivileged container namespaces to avoid OOM-killer termination:
```python
launch_kwargs["args"] = [
    "--js-flags=--max-old-space-size=512",
    "--disable-dev-shm-usage",
    "--disable-gpu"
]
```

### 5.4. WebRTC Leak Safeguards
Keep WebRTC **enabled** (for high-trust browser signatures) but lock down interface queries:
```python
# For Firefox:
"media.peerconnection.ice.no_host": True,
"media.peerconnection.ice.default_address_only": True

# For Chromium:
"--disable-webrtc-multiple-routes",
"--enforce-webrtc-ip-permission-check"
```
