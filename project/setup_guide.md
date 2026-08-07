# Setup & Operational Guide: The Invisible Stack

> **v6.0.1 Architecture Note**: This stack now runs on **OCI VM.Standard.E2.1.Micro (AMD64/x86_64)** with **Debian 12** host OS, **CloakBrowser** (custom C++ Chromium 120+ binary), and 1 persona in MAX_INVISIBILITY mode. Earlier v5.0+ ARM64 Ampere A1 plan is **SUPERSEDED** because CloakBrowser is x86_64-only.

Welcome to the **setup and operational guide** for the **Parasite SEO Automation Factory (The Invisible Stack)**. This manual provides a step-by-step walkthrough to deploy, secure, and operate this high-density browser-automation system on an Oracle Cloud Always Free x86_64 instance (`VM.Standard.E2.1.Micro`, 1/8 OCPU, 1 GB RAM, AMD64).

---

## 1. Project Directory Structure

Your project is structured inside `/home/user/project` with the following layout:

```text
/home/user/project/
├── TODO.md                             # Master roadmap, checklists, and tasks
├── docker-compose.yml                  # Services orchestration (Postgres, Redis, worker)
├── setup_guide.md                      # This document (Technical setup and file breakdown)
├── config/
│   ├── sysctl_hardened.conf            # Host kernel adjustments (Debian 12 / Linux defaults: TTL=64, timestamps=1, ECN=2)
│   ├── iptables_docker.sh              # Safe host firewall routing (DOCKER-USER chain isolation)
│   └── setup_zram_overlay.sh           # In-memory ZRAM block device mounting utility
├── database/
│   └── schema.sql                      # SQL database migrations (unlogged performance structures)
├── browser-runtime/
│   ├── Dockerfile                      # Hardened Playwright AMD64 image build blueprint (CloakBrowser-compatible)
│   ├── entrypoint.sh                   # Virtual display frame (Xvfb) & egress SOCKS5 kill-switch & TCP MSS Clamping
│   ├── prototype_hardener.js           # Anti-detection native JS prototype shield with Dynamic FP_CONFIG
│   ├── session_serializer.py           # Multi-storage state (Cookies + LocalStorage + Session) serializer
│   ├── humanized_movement.py           # Gaussian Noise Splines & target deceleration mouse generator
│   ├── paste_emulator.py               # Human copy-paste simulated paste engine
│   ├── asymmetric_vault.py             # Asymmetric (secp256k1 + AES-GCM) secure session vault
│   ├── ebpf_sentinel.py                # Host socket routing verification guard
│   └── hardened_orchestrator.py        # Asynchronous worker orchestrator loop (Pooled DB, Memory-clamped)
└── tests/
    └── test_stealth.py                 # Structural, cryptographic, and network integrity tests
```

---

## 2. File-by-File Breakdown & Technical Purpose

### `config/sysctl_hardened.conf`
* **Purpose**: Applies host-level kernel network configuration adjustments matching Debian 12 / Linux 6.1.x defaults.
* **Mechanism** (v6.0+): Uses **Linux defaults** (`net.ipv4.tcp_timestamps = 1`, `net.ipv4.tcp_sack = 1`, `net.ipv4.ip_default_ttl = 64`, `net.ipv4.tcp_ecn = 2`) — NOT Windows-spoofed values. The host IS Linux; the persona's Windows fingerprinting is handled at the BROWSER level by CloakBrowser's C++ layer + `prototype_hardener.js`. Setting host sysctls to Windows values (TTL=128, timestamps=0) would create a paradox (Linux kernel claiming Windows behavior) which is itself a fingerprint vector.

### `config/iptables_docker.sh`
* **Purpose**: Configures the host OS's firewall without disrupting Docker's bridge networks.
* **Mechanism**: Binds secure packet checks directly to Docker's internal `DOCKER-USER` chain. This protects the VM's incoming ports from unauthorized external access while preserving container-to-container communication.

### `config/setup_zram_overlay.sh`
* **Purpose**: Initializes compressed, high-performance in-memory disk workspaces.
* **Mechanism**: Loads the ZRAM kernel module and formats an 8GB compressed swap space using the highly efficient **ZSTD** compression algorithm. By mounting our active browser runtimes inside this compressed memory workspace, we protect the host SSD from write-wear and turn a 4GB RAM allocation into a **12GB high-speed memory pool**.

### `database/schema.sql`
* **Purpose**: Defines the database schemas for identity mapping and execution queues.
* **Mechanism**: Configures transactional tracking states and establishes an `UNLOGGED` database table for high-frequency status queries. This completely bypasses PostgreSQL's slow write-ahead log (WAL) logging, accelerating performance.

### `browser-runtime/Dockerfile`
* **Purpose**: Assembles the hardened Playwright AMD64 container runtime (compatible with CloakBrowser).
* **Mechanism**: Uses the official Playwright AMD64 base image (`mcr.microsoft.com/playwright:v1.44.0-jammy` — NOT `-arm64`) and injects genuine consumer TrueType fonts directly into the container system paths (`/usr/share/fonts/truetype/`) to pass canvas and text-rendering enumeration tests. The actual production browser is CloakBrowser (Chrome 120+), bind-mounted from the host at `/opt/cloakbrowser/chrome`.

### `browser-runtime/entrypoint.sh`
* **Purpose**: Starts the display buffer and establishes container-level egress routing firewalls.
* **Mechanism**: Spawns a background Xvfb frame buffer on display `:99` to simulate a genuine hardware screen. It then sets local container `iptables` rules to drop any outgoing traffic that attempts to bypass your designated loopback or proxy sidecar bridges, ensuring a **foolproof proxy kill-switch**.
* **MSS Clamping**: Clamps TCP MSS to `1360` to completely eliminate packet fragmentation over SOCKS5 encapsulation, passing SIVT network-layer audits.

### `browser-runtime/prototype_hardener.js`
* **Purpose**: Intercepts and shields JavaScript prototype objects.
* **Mechanism**: Overwrites Function's native prototype string mappings. When advanced platforms inspect your browser, it returns unaltered, native-compiled strings (`"[native code]"`), hiding any automation variables or selenium bindings.
* **Upgraded**: Now includes a dynamic `FP_CONFIG` block at the top of the file, allowing the orchestrator to inject and overwrite WebGL renderers, platform strings, cores, memory, and language arrays on the fly.

### `browser-runtime/session_serializer.py`
* **Purpose**: Encrypts and serializes multi-storage session configurations.
* **Mechanism**: Programmatically captures cookies alongside `localStorage` and `sessionStorage` variables, packages them into an encrypted payload, and saves them to PostgreSQL to prevent "new device" login security locks.

### `browser-runtime/paste_emulator.py`
* **Purpose**: Simulates realistic human copy-paste actions.
* **Mechanism**: Focuses target input selectors, programmatically injects text into the browser clipboard API, and emulates realistic keyboard inputs (`Ctrl+V`) with random delays to avoid mechanical typing triggers.

### `browser-runtime/humanized_movement.py`
* **Purpose**: Generates organic mouse trajectories.
* **Mechanism**: Interpolates coordinates using cubic Bezier curves, adds realistic physiological hand tremors (Gaussian noise), and applies target deceleration delays as the cursor approaches its destination.

### `browser-runtime/asymmetric_vault.py`
* **Purpose**: Implements asymmetric envelope encryption for credentials at rest.
* **Mechanism**: Derives a shared symmetric AES-GCM key on-the-fly via ECDH (Elliptic Curve Diffie-Hellman) using secp256k1 public/private keys. This encrypts and secures session credentials inside PostgreSQL, removing plain environment-variable key dependencies.

### `browser-runtime/ebpf_sentinel.py`
* **Purpose**: Kernel-level network socket routing validator.
* **Mechanism**: Queries psutil to verify that all active socket connections are routed through SOCKS5 proxy ports. If any socket is detected attempting to route directly over the host's WAN interface, the process is immediately terminated.

### `browser-runtime/hardened_orchestrator.py`
* **Purpose**: Coordinates task execution.
* **Mechanism**: Retrieves pending tasks, queries identity parameters from PostgreSQL, maps temporary directories on ZRAM, configures SOCKS5 remote DNS routing, dynamically instantiates browser engines (Firefox vs Chromium) based on User-Agent, and manages the end-to-end task execution pipeline.
* **Performance Upgrades**: Implemented instant DB connection release (protects from connection pool starvation) and V8 heap constraints (`max-old-space-size=512`) to prevent container memory swelling and OOM shutdowns.

---

## 3. Step-by-Step Deployment Roadmap

Follow these phases to deploy and run the system on your VM.

```text
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 1: Host Machine Hardening & Environment Setup         │
 │  ├── Apply kernel-level network hardening config            │
 │  ├── Secure incoming ports via iptables script              │
 │  └── Mount compressed ZRAM block devices                    │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 2: Docker Stack Orchestration                         │
 │  ├── Configure environment variables (.env)                 │
 │  ├── Build hardened Playwright images                       │
 │  └── Spin up PostgreSQL database & Redis clusters           │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 3: DB Migrations & Functional Testing                 │
 │  ├── Execute SQL database schema migrations                 │
 │  └── Run the automated QA system integrity tests            │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Phase 4: Operational Task Execution                         │
 │  └── Initialize tasks & execute async orchestrator worker   │
 └─────────────────────────────────────────────────────────────┘
```

### Phase 1: Host Machine Hardening & Environment Setup
Run these commands on your Oracle Cloud Debian 12 Host:

```bash
# 1. Apply Hardened Kernel Configurations
sudo cp config/sysctl_hardened.conf /etc/sysctl.d/99-hardened-stealth.conf
sudo sysctl --system

# 2. Execute Hardened, Docker-safe iptables Rules
chmod +x config/iptables_docker.sh
./config/iptables_docker.sh

# 3. Mount compressed ZRAM memory block devices
chmod +x config/setup_zram_overlay.sh
./config/setup_zram_overlay.sh
```

### Phase 2: Docker Stack Orchestration
Assemble your configurations and spin up your micro-services:

```bash
# 1. Create your environmental variables file
cat <<EOF > .env
DB_PASSWD=MasterDBSecret_9918
REDIS_PASSWD=MasterRedisSecret_1129
ENCRYPTION_KEY=aueoDaDPCmWvEQts_FIAnk6QlIbw8Wik7y4dMynxlMU=
UPSTREAM_RES_PROXY=socks5://user:pass@gate.residentialprovider.com:1080
EOF

# 2. Build hardened Playwright base container images
docker compose build --no-cache

# 3. Spin up PostgreSQL database & Redis
docker compose up -d postgres redis clean-room-gate proxy-sidecar-res proxy-sidecar-warp
```

### Step 3: Run Database Migrations
Initialize database tables:

```bash
# Initialize unlogged database schemas
docker compose exec -T postgres psql -U engine_user -d core_db < database/schema.sql
```

### Phase 3: DB Migrations & Functional Testing
Initialize database tables and run verification checks:

```bash
# 1. Execute SQL database schema migrations
docker compose exec -T postgres psql -U engine_user -d core_db < database/schema.sql

# 2. Run the automated QA system integrity tests
cd tests
PYTHONPATH=../browser-runtime pytest test_stealth.py
```

### Phase 4: Operational Task Execution
Ready to run your automation pipeline:

```bash
# Execute your asynchronous python orchestrator worker
python3 browser-runtime/hardened_orchestrator.py
```

---

## 4. Verification & Operational Troubleshooting

### Check RAM Disk Free Space
Regularly monitor RAM disk usage to ensure active sessions have sufficient volatile memory to execute:
```bash
df -h /mnt/compressed_ramdisk
```

### Check Active Connections
Ensure that browser processes route traffic through proxy sidecars (port 1080) and block direct WAN access:
```bash
sudo netstat -natp | grep 1080
```

### Clean Stale Docker Cache
Keep your block storage usage optimized on your free-tier tenancy:
```bash
docker system prune -af --volumes
```
