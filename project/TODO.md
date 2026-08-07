# TODO.md - Parasite SEO Automation Factory (The Invisible Stack)

> **v6.0.1 Architecture Note**: This stack now runs on **OCI VM.Standard.E2.1.Micro (AMD64/x86_64)** with **Debian 12** host OS, **CloakBrowser** (custom C++ Chromium 120+ binary), and 1 persona in MAX_INVISIBILITY mode. Earlier v5.0+ ARM64 Ampere A1 plan is **SUPERSEDED** because CloakBrowser is x86_64-only. CloakBrowser is Chrome (not Chromium) for proprietary codecs, Widevine, and full anti-bot fidelity.

This document serves as the implementation roadmap and technical specification for building, deploying, and operating the high-survival Parasite SEO Browser Automation Factory on Oracle Cloud Always Free x86_64 architecture.

---

## 1. Project Goal

### Ultimate Objective
Deploy a cost-efficient, high-survival browser-automation cluster running **1 persona** on a single Oracle Cloud Always Free instance (`VM.Standard.E2.1.Micro`, 1/8 OCPU, 1 GB RAM, AMD64) for automated platform warming, interaction, and link-posting across high-authority sites (Reddit, Quora, Medium) using CloakBrowser + `prototype_hardener.js` for absolute invisibility.

### Success Criteria
1. **Invisibility**: Achieve a trust score of ≥ 95% on CreepJS, Pixelscan, IPhey, and pass reCAPTCHA Enterprise, Cloudflare Turnstile, Akamai, and PerimeterX with the 1-persona stack.
2. **Account Longevity**: Maintain an average account survival rate of ≥ 30 days for active profiles.
3. **Zero Identity Drift**: Browser fingerprint signatures, timezones, locales, and WebGL renderers remain perfectly static per persona across reboots and runtime executions.
4. **Zero Host Leakage**: Absolute network isolation; the browser profile can only communicate over the internet through its assigned SOCKS5 proxy sidecar.
5. **Zero Architecture Paradox**: Host kernel sysctls match host OS (Debian 12 = Linux defaults). Persona (Windows 10/11) is spoofed at the BROWSER level only.

### Constraints
* **Compute Limit**: VM.Standard.E2.1.Micro = 1/8 OCPU, 1 GB RAM, ~50 GB boot volume.
* **Architecture**: x86_64 (AMD EPYC) only. CloakBrowser has no ARM64 build.
* **Network Limit**: Datacenter IPv4 ranges are heavily blacklisted; WARP is VPN-flagged; residential proxies have metered bandwidth or high per-IP cost.
* **System Stability**: Oracle reclaims "idle" instances if CPU utilization averages < 10% over a week. The system must maintain low, steady baseline tasks.

---

## 2. Architecture Tasks

### Database Vault (PostgreSQL)
* **Purpose**: Securely persist persona metadata, immutable fingerprints, encrypted session cookies, proxies, and task states.
* **Inputs**: Persona ID, generated fingerprints, session cookies (raw JSON), proxy specifications, execution states.
* **Outputs**: Decrypted profile credentials, proxy bindings, historical audit data.
* **Dependencies**: PostgreSQL 16-Alpine image, private host volume mapping.
* **Acceptance Criteria**: Encryption keys are stored as environment variables (Fernet AES-256); session cookies are encrypted on-disk and decrypted on-the-fly.

### Message Queue & Scheduler (Redis)
* **Purpose**: Host the asynchronous job queue and maintain rate-limiting schedules.
* **Inputs**: Task definitions (persona ID, URL, action type, payload).
* **Outputs**: Dequeued tasks dispatched to the python orchestrator.
* **Dependencies**: Redis 7-Alpine container with password protection enabled.
* **Acceptance Criteria**: Supports at-least-once task delivery and handles task retries safely.

### Proxy Sidecars (GOST)
* **Purpose**: Route all egress traffic through premium US Static Residential IPs (SOCKS5).
* **Inputs**: SOCKS5 proxy protocol requests from local containers on port 1081-1085.
* **Outputs**: Relayed traffic to target platforms over the internet.
* **Dependencies**: Docker network isolation bridges (`egress-net`, `internal-net`).
* **Acceptance Criteria**: Must intercept and handle all DNS requests. Must reject connections if the proxy tunnel is down (kill-switch).

### Browser Worker Container (CloakBrowser / Chromium 120+ Hardened)
* **Purpose**: Run headless browser sessions in an isolated sandbox.
* **Inputs**: Playwright automation scripts, assigned profile configs, proxy configurations.
* **Outputs**: Executed automation logs, updated session cookies.
* **Dependencies**:
  - `mcr.microsoft.com/playwright:v1.44.0-jammy` base (AMD64, NOT -arm64)
  - **CloakBrowser** (custom C++ Chromium 120+, host-deployed at `/opt/cloakbrowser/chrome`)
  - virtual display server (Xvfb) for headed mode
* **Acceptance Criteria**:
  - CloakBrowser binary runs with the persona's UA (Chrome 120)
  - Real TrueType fonts are loaded
  - System-level WebRTC and telemetry are blocked
  - `prototype_hardener.js` is registered BEFORE first `browser.new_page()`

---

## 3. Development Tasks

### Phase 1: Environment Provisioning & Host Hardening
* [ ] **Task 1.1: OCI VM Setup**
  - Create `VM.Standard.E2.1.Micro` instance (1/8 OCPU, 1 GB RAM) with **Debian 12 (Bookworm) x86_64** as the host OS.
  - **NOTE**: We use **Debian 12 (Bookworm)** as the host OS — no snapd, no Ubuntu MOTD artifacts. Do NOT use Ubuntu; the host must be Debian 12 for the v6.0+ design.
  - Expand boot volume to ~50 GB.
  - Configure public security list to only permit ingress SSH (Port 22) from the operator's IP address.
  - *Acceptance Criteria*: SSH login works; disk size is verified via `df -h`.
* [ ] **Task 1.2: Host Networking & Firewall Hardening (Debian 12 defaults)**
  - Apply `config/hardened_host_os.sh` (Debian 12 / Linux 6.1.x defaults, NOT Windows).
  - `ip_default_ttl=64` (Linux, NOT 128)
  - `tcp_timestamps=1` (Linux, NOT 0)
  - `tcp_sack=1` (Linux, NOT 0)
  - `tcp_ecn=2` (active, Linux default)
  - Run host-level `config/iptables_docker.sh` (FORWARD MSS clamp = 1200).
  - Install `iptables-persistent` to preserve rules across reboots.
  - *Acceptance Criteria*: `verify_system_integrity.py` passes all Debian 12 sysctl checks.
* [ ] **Task 1.3: In-Memory Workspace Configuration**
  - Use `tmpfs` mounted at `/mnt/ramdisk` (200M, configurable).
  - Per-task tmpfs lifecycle managed in `entrypoint.sh` (pre-mount).
  - *Acceptance Criteria*: `mount | grep tmpfs` shows the configured tmpfs.

### Phase 2: Docker Infrastructure Deployment
* [ ] **Task 2.1: Network Setup**
  - Define `internal-net` Docker bridge with `internal: true` constraint.
  - Define `egress-net` for external communication.
  - *Acceptance Criteria*: Containers inside `internal-net` cannot ping public external IPs.
* [ ] **Task 2.2: Postgres Vault Implementation**
  - Draft database initialization SQL script (`schema.sql`).
  - Create PostgreSQL service configuration with `tmpfs` volume (128M).
  - *Acceptance Criteria*: Able to query postgres locally from host; schema is fully mapped.
* [ ] **Task 2.3: Proxy Sidecars Setup**
  - GOST SOCKS5 listeners on ports 1081-1085 (one per persona, even though we run 1).
  - Spin up Cloudflare WARP sidecar as fallback (optional).
  - *Acceptance Criteria*: Curling the sidecar SOCKS5 endpoints returns the correct public IP.

### Phase 3: Anti-Detection Browser Engineering (CloakBrowser)
* [ ] **Task 3.1: CloakBrowser Installation**
  - Install CloakBrowser binary at host path `/opt/cloakbrowser/chrome`.
  - Verify x86_64 architecture: `file /opt/cloakbrowser/chrome` should report "x86-64".
  - Configure bind-mount in `docker-compose.yml`: `./opt/cloakbrowser:/opt/cloakbrowser:ro`.
  - *Acceptance Criteria*: Playwright can launch CloakBrowser with `executable_path=/opt/cloakbrowser/chrome`.
* [ ] **Task 3.2: TrueType Font Setup (Windows fonts)**
  - Configure TrueType Windows core font copying (e.g., Segoe UI, Arial) into `/usr/share/fonts/truetype/`.
  - *Acceptance Criteria*: `fc-list` inside the container reveals standard Windows fonts.
* [ ] **Task 3.3: Virtual Display Setup (Xvfb)**
  - Implement `entrypoint.sh` to initialize a background Xvfb screen on `:99` (or persona-specific).
  - *Acceptance Criteria*: Running headful browser runs successfully without missing display errors.
* [ ] **Task 3.4: Integration of `prototype_hardener.js`**
  - Implement modular profile launching script incorporating fingerprint injection.
  - Inject `prototype_hardener.js` to protect native JavaScript properties.
  - Register init script BEFORE first `browser.new_page()` call.
  - *Acceptance Criteria*: Code initializes and opens a browser context using dynamically generated, persistent fingerprints; CreepJS score ≥ 95%.

### Phase 4: Async Orchestration & Scheduling
* [ ] **Task 4.1: Database Persistence Logic**
  - Build Python logic to securely encrypt cookies (AES-256 via cryptography's Fernet) and persist them alongside JSON profiles in PostgreSQL.
  - Integrated multi-storage serializer to preserve localStorage and sessionStorage profiles.
  - *Acceptance Criteria*: Unencrypted cookies are never written to disk; database encryption/decryption works seamlessly.
* [ ] **Task 4.2: Async Loop Orchestration (1 persona)**
  - Develop the orchestrator utilizing `asyncio` and `async_playwright` to process tasks sequentially (1 persona, not concurrent).
  - Integrate custom copy-paste emulators to type long documents naturally.
  - *Acceptance Criteria*: Running 1 persona task with 5 sub-operations functions smoothly without blocking I/O.
* [ ] **Task 4.3: Queue Task Integration**
  - Integrate Redis worker logic to pull jobs off the queue.
  - Implement logic to wipe RAM disk session directories completely upon session completion.
  - *Acceptance Criteria*: `/mnt/ramdisk/persona_<uuid>/` is wiped clean on container teardown.

### Phase 5: Self-Testing, Validation & Shadowban Auditing
* [ ] **Task 5.1: Internal Clean-Room Validation Gate**
  - Deploy a minimal Node.js server wrapping CreepJS at `creepjs-gate/server.js`.
  - Implement verification script to query `http://clean-room-gate:3000` pre-flight.
  - *Acceptance Criteria*: Real-time trust scores are returned; any score below 95% triggers automatic profile quarantine.
* [ ] **Task 5.2: Automated Shadowban Auditor**
  - Write a separate "Auditor" script running on a clean, isolated proxy range.
  - Program the auditor to search for unique keywords/tags posted by other personas to verify public visibility.
  - *Acceptance Criteria*: Detection of a missing post updates the target persona's database state to `limited` and alerts the operator.
* [ ] **Task 5.3: System Integrity Validator (`verify_system_integrity.py`)**
  - Validates Debian 12 sysctl defaults (TTL=64, timestamps=1, sack=1, ECN≥1).
  - Validates TLS handshake impersonation as `chrome120` (CloakBrowser-compatible).
  - *Acceptance Criteria*: All checks pass on a correctly-configured Debian 12 host.

---

## 4. Validation Tasks

### Test Cases
* **Fingerprint Consistency**: Launch `persona_1` ten times, recording canvas hashes, WebGL descriptors, and font enumeration.
  - *Expected Output*: Zero variance across launches.
* **DNS and WebRTC Leak Test**: Direct the browser context to `https://browserleaks.com/webrtc` and `https://browserleaks.com/dns`.
  - *Expected Output*: No local IPs (192.168.x.x, 10.x.x.x) or Oracle VM datacenter IPv4 addresses are leaked.
* **RAM Disk Performance and Pruning**: Execute 1 persona session, monitoring RAM usage.
  - *Expected Output*: Memory usage of `/mnt/ramdisk` peaks during the session, but recovers completely to 0 bytes on termination.
* **TLS JA3 Match**: Run `verify_tls_stealth.py`; verify JA3 hash matches Chrome 120's known pattern.
  - *Expected Output*: JA3 hash is in the Chrome 120 cluster on ja3er.com.

### Failure Cases
* **Proxy Outage**: Disrupt the upstream residential proxy network connection.
  - *Expected Output*: Browser requests immediately fail (fail-closed/kill-switch); no raw datacenter IP is exposed.
* **Database Disconnection**: Kill the PostgreSQL container during execution.
  - *Expected Output*: Workers capture the exception, gracefully terminate active browser contexts to prevent platform flags, and log errors to syslog.

---

## 5. Monitoring Tasks

### Metrics to Track
1. **Host Performance**: Host CPU/RAM usage (tracked via custom cron script or Prometheus node-exporter).
2. **Account Performance**: Success vs. Ban/Limit rates per proxy range.
3. **Task Performance**: Queue depth, job execution latency, and CAPTCHA incidence rates.
4. **Proxy Performance**: Sidecar-to-upstream latency.

### Logging Configuration
* Implement Python `loguru` configured to output unified, structured JSON logs.
* Route logs to container stdout (with `logging: driver: none` for forensic hygiene).

---

## 6. Risk Register

| Risk | Impact | Probability | Mitigation |
| :--- | :--- | :--- | :--- |
| **Oracle VM Termination** (Resource reclaiming) | Critical | Medium | Maintain steady, lightweight scheduling tasks to keep average CPU usage at ~15% (avoiding idle flags), and configure automatic external backups. |
| **Subnet Ban / IP Burnout** | High | High | Run strict 1-to-3 mapping (maximum 3 personas per static residential proxy). Implement immediate quarantine if shadowbans are detected. |
| **CloakBrowser Update Drift** | Medium | Low | Hardcode and pin exact CloakBrowser binary version. Disable automated apt-upgrades for browser components. |
| **Database Encryption Key Loss** | Critical | Low | Backup encryption keys securely off-site in a protected vault or password manager. |
| **Host-Paradox Fingerprint** (Linux kernel claiming Windows behavior) | Critical | High | Use Debian 12 sysctl defaults (TTL=64, timestamps=1). Spoof Windows at BROWSER level only (CloakBrowser C++ + prototype_hardener.js). |

---

## 7. Deployment Tasks

### Pre-Deployment Checklist
- [ ] Confirm Oracle Cloud E2.1.Micro VM has **Debian 12 Bookworm** installed (x86_64).
- [ ] CloakBrowser binary installed at `/opt/cloakbrowser/chrome` on host.
- [ ] Ensure static residential proxies are active and credentials are correct.
- [ ] Verify database master password and Fernet cryptographic keys are generated and recorded securely off-site.

### Deployment Checklist
- [ ] Execute Host Hardening script (`config/hardened_host_os.sh`).
- [ ] Clone repository and configure environmental `.env` parameters.
- [ ] Run `docker compose build --no-cache` to assemble standard-hardened base images.
- [ ] Initialize PostgreSQL database schemas.
- [ ] Run `docker compose up -d` to spin up services.
- [ ] Execute `python3 verify_system_integrity.py` to confirm all integrity checks pass.
- [ ] Execute a test worker run using `docker compose exec orchestrator python3 hardened_orchestrator.py`.

### Rollback Checklist
- [ ] Run `docker compose down -v` to safely stop containers and drop temporary volumes.
- [ ] Restore PostgreSQL state from the last secure logical backup.
- [ ] Flush host rules and reload fallback iptables rules via `iptables-restore < /etc/iptables/rules.v4.bak`.

---

## 8. Maintenance Tasks

### Daily
* Check daily post success/ban ratios.
* Verify RAM disk free space.
* Review system exception logs for warning anomalies.

### Weekly
* Perform secure PostgreSQL logical dump (`pg_dump`) and transfer encrypted archive off-site.
* Rotate and archive orchestrator logs.

### Monthly
* Run manual health audits on long-living "Golden" personas.
* Clean dangling Docker layers using `docker system prune -f` to free storage.
* Verify CloakBrowser version against upstream; update if security patches are available.

---

## 9. Future Enhancements
* Integrate on-instance local quantized 8B Large Language Model (Ollama API wrapping LLaMA-3) to handle native, zero-cost content variation.
* Implement automated CAPTCHA-solving micro-service integration (e.g., 2Captcha/Capsolver API).
* Build a React or Retool dashboard displaying active persona counts, current proxy latencies, and task queues.

---

## 10. Completion Checklist
- [ ] Host security lists are locked down and Debian 12 sysctls are verified active.
- [ ] `tmpfs` RAM disk is mounted at `/mnt/ramdisk` (200M).
- [ ] Docker Compose is up and network isolation bridges are fully functional.
- [ ] Database vault securely stores, encrypts, and decrypts credentials/cookies.
- [ ] CloakBrowser binary successfully launches from `/opt/cloakbrowser/chrome`.
- [ ] Pre-flight checks on CreepJS score ≥ 95% consistently.
- [ ] Production orchestrator executes async loops and wipes active profile directories completely.
- [ ] `verify_system_integrity.py` passes ALL checks (sysctls, TLS, DB, etc.).
