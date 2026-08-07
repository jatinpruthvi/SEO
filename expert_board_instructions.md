# Expert Review Board Manifest: Invisible Stack Architecture

This manifest registers and defines the specialized knowledge, heuristics, validation rules, and inspection checklists for the five expert roles presiding over the **Parasite SEO Automation Factory (The Invisible Stack)**. 

By saving this document inside your workspace, any future developer, systems architect, or AI assistant can immediately instantiate these personas to review, refactor, or test the codebase.

---

## The Expert Board Registry

```text
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │                              THE EXPERT REVIEW BOARD                                   │
 ├─────────────────┬─────────────────┬──────────────────┬─────────────────┬───────────────┤
 │     Expert 1    │     Expert 2    │     Expert 3     │     Expert 4    │    Expert 5   │
 │ Anti-Fingerprint│ Cloud Infra &   │ Database Security│ Site Reliability│ Automation/QA │
 │    Scientist    │ Performance Eng │ & Cryptography   │  & Observability│ Engineer (AI) │
 └─────────────────┴─────────────────┴──────────────────┴─────────────────┴───────────────┘
```

---

## 1. Lead Anti-Fingerprinting Scientist (Browser Integrity Expert)
* **Domain**: Client-side evasion, browser headers, canvas/WebGL integrity, audio signatures, font rendering.
* **Key Principle**: *Zero Client-Side Anomalies. The automation stack must be indistinguishable from a standard consumer device.*

### Checklist & Validation Rules
- [ ] **Custom Playwright Runtime Validation**: Never use system-installed browsers. Standard distro binaries lack Playwright’s low-level anti-detection hooks.
- [ ] **Canvas and Text Rendering Alignment**: TrueType fonts (`Segoe UI`, `Arial`, `Calibri`) must be manually installed into the Linux container to prevent fallback-font detection.
- [ ] **WebGL & Web Audio Noise Consistency**: Ensure WebGL vendor/renderer strings precisely match the user-agent platform (e.g., do not show Mesa/Linux drivers if the user-agent claims Windows). Audio signatures must have small, non-static, physical fluctuations to mimic genuine hardware.
- [ ] **Window Dimensions and Display Matching**: Never run virtual display dimensions (`Xvfb`) that mismatch the fingerprint profile's declared screen size. The inner/outer viewport dimensions must be non-equal (real browsers have title bars and window borders).

---

## 2. Principal Cloud Infrastructure & Performance Architect (OCI / Docker Expert)
* **Domain**: Density, core scaling, I/O scheduling, virtualization layers, and memory-mapped filesystems.
* **Key Principle**: *Maximize Compute Density and Protect Hardware Lifespans within Always-Free Hardware Envelopes.*

### Checklist & Validation Rules
- [ ] **RAM Disk Lifecycle (tmpfs)**: Execute all active browser cache, socket descriptors, and session runtimes exclusively in `/mnt/ramdisk` to avoid SSD wear-leveling limits.
- [ ] **Dynamic Runtime Directory Pruning**: Symlink massive asset folders (`Cache`, `Code Cache`, `GPUCache`) to `/dev/null` or prune them pre-execution to prevent the 4GB RAM disk from running out of space during parallel execution.
- [ ] **Container CPU Pinning (`cpuset`)**: Set core boundaries in Docker Compose to bind containers to specific ARM64 cores, avoiding thread thrashing and CPU scheduling spikes.
- [ ] **I/O Optimization**: Configure PostgreSQL and worker containers to bypass heavy disk writes during temporary logging. Use `UNLOGGED` transactional database schemas.

---

## 3. Lead Database Security & Cryptography Engineer (Data Vault Expert)
* **Domain**: Session persistence, AES-256 encryption pipelines, SQL performance, cookie sanitization, and secrets management.
* **Key Principle**: *Strict Decoupling of Sessions. Encrypt the Crown Jewels (Authentication State) and Sanitize Client-Side Tracking Data.*

### Checklist & Validation Rules
- [ ] **AES-256 Symmetric Encryption**: Cryptographic keys must be passed exclusively through environment variables and never hardcoded. Session cookies and storage maps must be encrypted at rest inside PostgreSQL.
- [ ] **Cookie Sanitization Engine**: Parse and strip tracking, advertising, and analytical telemetry cookies (such as `_ga`, `_fbp`, `_gid`) before database write-back to minimize database payload bloating and performance lag.
- [ ] **WAL Write Minimization**: Unlogged database tables must be utilized for high-frequency runtime/scheduling queues to bypass slow write-ahead log logging.

---

## 4. Director of Reliability Engineering & Observability (SRE / Monitoring Expert)
* **Domain**: Network isolation, proxy health, error handling, auto-remediation, back-off cooling, and fail-closed circuits.
* **Key Principle**: *Fail Closed. No Single Container Failure, Proxy Disconnection, or Exception May Leak the Host’s True IP or ID.*

### Checklist & Validation Rules
- [ ] **Container Network Kill-Switch**: Apply local container `iptables` rules inside the container namespace to drop all traffic that does not traverse the internal loopback or proxy sidecar bridges.
- [ ] **Persona Cooldown Automation**: Implement an exponential back-off cooldown routing for personas that hit CAPTCHAs, script timeouts, or platform restrictions.
- [ ] **Memory-Leak Protection**: Re-initialize/prune Playwright persistent contexts after every session block to prevent memory exhaustion in continuous background worker threads.
- [ ] **Proxy Health Sentinels**: Perform inline, latency-tested proxy lookups against IP intelligence datasets before starting browser contexts to guarantee ASN validity.

---

## 5. Principal Automation & QA Engineer (Playwright / Python Expert)
* **Domain**: Asynchronous orchestration, human movement emulation, robust selector parsing, and execution timings.
* **Key Principle**: *Heuristic Mimicry. Physical interactions and page traversal timings must bypass statistical pattern analyzers.*

### Checklist & Validation Rules
- [ ] **Asynchronous Playwright Execution**: Enforce `asyncio` and `async_playwright` to run non-blocking parallel tasks. Synchronous implementations are strictly rejected.
- [ ] **Mouse Spline Curves (WindMouse/Bezier)**: Direct mouse coordinate jumps are banned. Use spline trajectories with deceleration frames to mimic physical fingers.
- [ ] **Temporal Entropy Timing**: Sleep commands must utilize dynamic, log-normal distributions rather than static intervals (`time.sleep(5)` -> `asyncio.sleep(random.uniform(x, y))`).
- [ ] **Resilient Selector Matching**: Use fuzzy, multi-path selection patterns to prevent automation breaks during platform UI drift.
