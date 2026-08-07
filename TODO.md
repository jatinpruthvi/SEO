# TODO.md - Parasite SEO Automation Factory (The Invisible Stack)

This document serves as the implementation roadmap and technical specification for building, deploying, and operating the high-survival Parasite SEO Browser Automation Factory on Oracle Cloud Always Free ARM architecture.

---

## 1. Project Goal

### Ultimate Objective
Deploy a cost-efficient, high-survival browser-automation cluster running up to 20 concurrent isolated personas on a single Oracle Cloud Free Tier instance (`VM.Standard.A1.Flex`, 4 OCPUs, 24GB RAM) for automated platform warming, interaction, and link-posting across high-authority sites (Reddit, Quora, Medium) using an advanced anti-detection browser stack.

### Success Criteria
1. **Concurrency**: Execute 15–20 browser instances simultaneously without exceeding 85% CPU or 90% RAM utilization (preventing Oracle VM idle/OOM termination).
2. **Stealth Compliance**: Achieve a trust score of $\ge 95\%$ on CreepJS and pass basic Pixelscan/IPhey checks under automated Playwright control.
3. **Account Longevity**: Maintain an average account survival rate of $\ge 30$ days for active profiles.
4. **Zero Identity Drift**: Ensure that browser fingerprint signatures, timezones, locales, and WebGL renderers remain perfectly static per persona across reboots and runtime executions.
5. **Zero Host Leakage**: Ensure absolute network isolation; no browser profile can communicate over the internet except through its assigned proxy sidecar.

### Constraints
* **Compute Limit**: 4 ARM64 cores, 24GB RAM, 100GB Boot Volume.
* **Network Limit**: Datacenter IPv4 ranges are heavily blacklisted; WARP is VPN-flagged; residential proxies have metered bandwidth or high per-IP cost.
* **System Stability**: Oracle reclaims "idle" instances if CPU utilization averages $< 10\%$ over a week. The system must maintain low, steady baseline tasks.

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

### Proxy Sidecars (Gost / Dante & Cloudflare WARP)
* **Purpose**: Route all egress traffic through either Cloudflare WARP (free, clean VPN) or premium US Static Residential IPs.
* **Inputs**: SOCKS5 proxy protocol requests from local containers on port 1080.
* **Outputs**: Relayed traffic to target platforms over the internet.
* **Dependencies**: Docker network isolation bridges (`egress-net`, `internal-net`).
* **Acceptance Criteria**: Must intercept and handle all DNS requests. Must reject connections if the proxy tunnel is down (kill-switch).

### Browser Worker Container (Playwright ARM64 Hardened)
* **Purpose**: Run headless browser sessions in an isolated sandbox.
* **Inputs**: Playwright automation scripts, assigned profile configs, proxy configurations.
* **Outputs**: Executed automation logs, updated session cookies.
* **Dependencies**: `mcr.microsoft.com/playwright:v1.44.0-jammy-arm64` base, virtual display server (Xvfb).
* **Acceptance Criteria**: Custom-compiled Playwright browser binary runs correctly; real TrueType fonts are loaded; system-level WebRTC and telemetry are blocked.

---

## 3. Development Tasks

### Phase 1: Environment Provisioning & Host Hardening
* [ ] **Task 1.1: OCI VM Setup**
  * Create `VM.Standard.A1.Flex` instance with 4 OCPUs, 24GB RAM, Ubuntu 24.04 LTS (ARM64).
  * Expand boot volume size to 100GB.
  * Configure public security list to only permit ingress SSH (Port 22) from the operator's IP address.
  * *Acceptance Criteria*: SSH login works; disk size is verified via `df -h`.
* [ ] **Task 1.2: Host Networking & Firewall Hardening**
  * Create robust `iptables` configuration to deny raw incoming traffic while allowing Docker bridges and loopback.
  * Append `net.ipv4.ip_default_ttl = 128` to `/etc/sysctl.conf` and apply.
  * Install `iptables-persistent` to preserve rules across reboots.
  * *Acceptance Criteria*: Verification using `ping` and `traceroute` from an external client confirms host is fully stealthed; TTL is validated as 128 via packet captures.
* [ ] **Task 1.3: RAM Disk Configuration**
  * Create `/mnt/ramdisk` directory.
  * Configure `/etc/fstab` to automatically mount a 4GB `tmpfs` RAM disk.
  * *Acceptance Criteria*: Verification using `mount | grep tmpfs` showing the 4GB mount.

### Phase 2: Docker Infrastructure Deployment
* [ ] **Task 2.1: Network Setup**
  * Define `internal-net` Docker bridge with `internal: true` constraint.
  * Define standard `egress-net` for external communication.
  * *Acceptance Criteria*: Containers inside `internal-net` cannot ping public external IPs.
* [ ] **Task 2.2: Postgres Vault Implementation**
  * Draft database initialization SQL script (`schema.sql`).
  * Create PostgreSQL service configuration with persistent host-directory volume.
  * *Acceptance Criteria*: Able to query postgres locally from host; schema is fully mapped.
* [ ] **Task 2.3: Proxy Sidecars Setup**
  * Write Dockerfile/configurations for `gost` sidecar forwarding to static residential proxies.
  * Spin up Cloudflare WARP sidecar and register clients.
  * *Acceptance Criteria*: Curling the sidecar SOCKS5 endpoints returns the correct public IP (residential/WARP respectively) and DNS queries are resolved cleanly without host leakage.

### Phase 3: Anti-Detection Browser Engineering
* [ ] **Task 3.1: Playwright Custom Dockerfile Compilation**
  * Pull official Playwright ARM64 base.
  * Configure TrueType Windows core font copying (e.g., Segoe UI, Arial) into `/usr/share/fonts/truetype/`.
  * Inject custom configurations to lock down system-level Firefox telemetry (`/etc/firefox/syspref.js`).
  * *Acceptance Criteria*: `fc-list` inside the container reveals standard Windows fonts.
* [ ] **Task 3.2: Virtual Display Setup (Xvfb)**
  * Implement `entrypoint.sh` to initialize a background Xvfb screen on `:99`.
  * *Acceptance Criteria*: Running headless browser runs successfully without missing display errors.
* [ ] **Task 3.3: Integration of `invisible_playwright` and `browserforge`**
  * Implement modular profile launching script incorporating fingerprint injection.
  * Configure strict Firefox user preferences (force DNS-over-HTTPS via Cloudflare DoH, disable WebRTC).
  * *Acceptance Criteria*: Code initializes and opens a browser context using dynamically generated, persistent fingerprints.

### Phase 4: Async Orchestration & Scheduling
* [ ] **Task 4.1: Database Persistence Logic**
  * Build python logic to securely encrypt cookies (AES-256 via cryptography's Fernet) and persist them alongside JSON profiles in PostgreSQL.
  * *Acceptance Criteria*: Unencrypted cookies are never written to disk; database encryption/decryption works seamlessly.
* [ ] **Task 4.2: Async Loop Orchestration**
  * Develop the orchestrator utilizing `asyncio` and `async_playwright` to process tasks concurrently.
  * *Acceptance Criteria*: Running 5 simulated persona tasks concurrently functions smoothly without blocking I/O.
* [ ] **Task 4.3: Queue Task Integration**
  * Integrate Redis worker logic to pull jobs off the queue.
  * Implement logic to wipe RAM disk session directories completely upon session completion.
  * *Acceptance Criteria*: `/mnt/ramdisk/profiles/` remains completely empty when no active containers are running.

### Phase 5: Self-Testing, Validation & Shadowban Auditing
* [ ] **Task 5.1: Internal Clean-Room Validation Gate**
  * Deploy a minimal Node.js server wrapping CreepJS.
  * Implement verification script to query `http://clean-room-gate:3000` pre-flight.
  * *Acceptance Criteria*: Real-time trust scores are returned; any score below 95% triggers automatic profile quarantine.
* [ ] **Task 5.2: Automated Shadowban Auditor**
  * Write a separate "Auditor" script running on a clean, isolated proxy range.
  * Program the auditor to search for unique keywords/tags posted by other personas to verify public visibility.
  * *Acceptance Criteria*: Detection of a missing post updates the target persona's database state to `limited` and alerts the operator.

---

## 4. Validation Tasks

### Test Cases
* **Fingerprint Consistency**: Launch `persona_1` ten times, recording canvas hashes, WebGL descriptors, and font enumeration.
  * *Expected Output*: Zero variance across launches.
* **DNS and WebRTC Leak Test**: Direct the browser context to `https://browserleaks.com/webrtc` and `https://browserleaks.com/dns`.
  * *Expected Output*: No local IPs (192.168.x.x, 10.x.x.x) or Oracle VM datacenter IPv4 addresses are leaked.
* **RAM Disk Performance and Pruning**: Execute 15 concurrent sessions, monitoring RAM usage.
  * *Expected Output*: Memory usage of `/mnt/ramdisk` peaks, but recovers completely to 0 bytes on termination.

### Failure Cases
* **Proxy Outage**: Disrupt the upstream residential proxy network connection.
  * *Expected Output*: Browser requests immediately fail (fail-closed/kill-switch); no raw datacenter IP is exposed.
* **Database Disconnection**: Kill the PostgreSQL container during execution.
  * *Expected Output*: Workers capture the exception, gracefully terminate active browser contexts to prevent platform flags, and log errors to syslog.

---

## 5. Monitoring Tasks

### Metrics to Track
1. **Host Performance**: Host CPU/RAM usage (tracked via custom cron script or Prometheus node-exporter).
2. **Account Performance**: Success vs. Ban/Limit rates per proxy range.
3. **Task Performance**: Queue depth, job execution latency, and CAPTCHA incidence rates.
4. **Proxy Performance**: Sidecar-to-upstream latency.

### Logging Configuration
* Implement Python `loguru` configured to output unified, structured JSON logs.
* Route logs to `/var/log/parasite_factory.log` with weekly rotation enabled.

---

## 6. Risk Register

| Risk | Impact | Probability | Mitigation |
| :--- | :--- | :--- | :--- |
| **Oracle VM Termination** (Resource reclaiming) | Critical | Medium | Maintain steady, lightweight scheduling tasks to keep average CPU usage at ~15% (avoiding idle flags), and configure automatic external backups. |
| **Subnet Ban / IP Burnout** | High | High | Run strict 1-to-3 mapping (maximum 3 personas per static residential proxy). Implement immediate quarantine if shadowbans are detected. |
| **Playwright Version Drift** | Medium | Low | Hardcode and pin exact Playwright container version (`v1.44.0-jammy-arm64`). Disable automated apt-upgrades for browser components. |
| **Database Encryption Key Loss** | Critical | Low | Backup encryption keys securely off-site in a protected vault or password manager. |

---

## 7. Deployment Tasks

### Pre-Deployment Checklist
- [ ] Confirm Oracle Cloud A1 Flex VM has Ubuntu 24.04 ARM64 installed.
- [ ] Ensure static residential proxies are active and credentials are correct.
- [ ] Verify database master password and Fernet cryptographic keys are generated and recorded securely off-site.

### Deployment Checklist
- [ ] Execute Host Hardening script (sysctl, iptables, RAM disk initialization).
- [ ] Clone repository and configure environmental `.env` parameters.
- [ ] Run `docker compose build --no-cache` to assemble standard-hardened base images.
- [ ] Initialize PostgreSQL database schemas.
- [ ] Run `docker compose up -d` to spin up services.
- [ ] Execute a test worker run using `docker compose exec orchestrator python orchestrator.py`.

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
* Rotate and archive `/var/log/parasite_factory.log`.

### Monthly
* Run manual health audits on long-living "Golden" personas.
* Clean dangling Docker layers using `docker system prune -f` to free storage.

---

## 9. Future Enhancements
* Integrate on-instance local quantized 8B Large Language Model (Ollama API wrapping LLaMA-3) to handle native, zero-cost content variation.
* Implement automated CAPTCHA-solving micro-service integration (e.g., 2Captcha/Capsolver API).
* Build a React or Retool dashboard displaying active persona counts, current proxy latencies, and task queues.

---

## 10. Completion Checklist
- [ ] Host security lists are locked down and `sysctl` spoofing is verified active.
- [ ] 4GB `tmpfs` RAM disk is persistently mounted at `/mnt/ramdisk`.
- [ ] Docker Compose is up and network isolation bridges are fully functional.
- [ ] Database vault securely stores, encrypts, and decrypts credentials/cookies.
- [ ] The custom Playwright Docker image successfully compiles with Windows fonts.
- [ ] Pre-flight checks on CreepJS score $\ge 95\%$ consistently.
- [ ] Production orchestrator executes async loops and wipes active profile directories completely.
