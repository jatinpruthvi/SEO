# GAP_FIXES.md - Zero-Gap Production Remediation Plan

This document maps, tracks, and prioritizes the structural and execution-level gaps identified during the comprehensive systems audit of the **Parasite SEO Automation Factory (The Invisible Stack)**.

---

## Gap Registry & Prioritization Matrix

```text
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │                              GAP PRIORITIZATION MATRIX                                 │
 ├──────────┬──────────────────────────────────────┬──────────────┬───────────────────────┤
 │  Gap ID  │             Description              │   Severity   │        Status         │
 ├──────────┼──────────────────────────────────────┼──────────────┼───────────────────────┤
 │ GAP-01   │ Remote SOCKS5 DNS Leaking            │   CRITICAL   │ FIXED (Engine Config) │
 │ GAP-02   │ Browser Canvas Rendering Identifiers │     HIGH     │ FIXED (Stealth Hook)  │
 │ GAP-03   │ Database Connection Pool Exhaustion │     HIGH     │ FIXED (Asyncpg Pool)  │
 │ GAP-04   │ Proxy Health Sentinel Timeout Leak   │    MEDIUM    │ FIXED (Sentinel Test) │
 │ GAP-05   │ Missing Dead Letter Task Recovery    │    MEDIUM    │ RESOLVED (Architecture)│
 └──────────┴──────────────────────────────────────┴──────────────┴───────────────────────┘
```

---

## Critical Gaps

### GAP-01: Remote SOCKS5 DNS Leaking
* **Severity**: **CRITICAL**
* **Root Cause**: Playwright's proxy launch configurations (`proxy={"server": "socks5://..."}`) do not inherently force DNS name resolutions to occur at the remote proxy endpoint. Firefox can fallback to resolving DNS requests locally via the container's virtual network interface, leaking the Oracle Cloud nameserver/host IP to external monitoring firewalls.
* **Affected Files**: `project/browser-runtime/hardened_orchestrator.py`
* **Recommended Fix**: Inject `network.proxy.socks_remote_dns = true` inside the custom Firefox user preferences within Playwright to guarantee that all name resolutions occur cleanly at the egress residential endpoint.
* **Validation Steps**: Open a session, navigate to `https://browserleaks.com/dns`, and verify that only the proxy's DNS server IP is visible.
* **Acceptance Criteria**: Host OCI DNS servers are 100% hidden.
* **Status**: **FIXED**

---

## High Gaps

### GAP-02: Canvas Rendering Fingerprint Clusters
* **Severity**: **HIGH**
* **Root Cause**: While browser headers and WebGL descriptors are randomized per persona, the underlying hardware graphics rendering card remains identical across containers. This causes all personas running on the same VM to generate identical canvas rendering hashes, leading to cross-profile clustering bans.
* **Affected Files**: `project/browser-runtime/prototype_hardener.js`
* **Recommended Fix**: Overwrite the native `toDataURL` and `getImageData` prototype functions on `HTMLCanvasElement` and `CanvasRenderingContext2D` to inject non-perceptible sub-pixel noise, randomizing the returned hash values.
* **Validation Steps**: Launch two parallel profiles and check their rendering signatures against CreepJS canvas audits.
* **Acceptance Criteria**: Canvas hashes are unique and non-static across personas.
* **Status**: **FIXED**

### GAP-03: Database Connection Starvation & Pool Exhaustion
* **Severity**: **HIGH**
* **Root Cause**: `hardened_orchestrator.py` opens a new, raw database connection (`asyncpg.connect`) on every loop execution. Under high parallel concurrency, this can exhaust PostgreSQL's max connection limits, throwing `TooManyConnections` exceptions and halting worker scripts.
* **Affected Files**: `project/browser-runtime/hardened_orchestrator.py`
* **Recommended Fix**: Implement connection pools (`asyncpg.create_pool`) within the main orchestrator daemon and acquire temporary connections programmatically on-demand.
* **Validation Steps**: Simulate 15 parallel workers querying the database concurrently and track active connection limits in PostgreSQL.
* **Acceptance Criteria**: System maintains connection stability without throwing pool starvation errors.
* **Status**: **FIXED**

---

## Medium Gaps

### GAP-04: Proxy Health Sentinel Timeout Leak
* **Severity**: **MEDIUM**
* **Root Cause**: `check_proxy_integrity` acts as a static stub returning `True`. If a premium residential proxy goes offline, the browser will attempt to navigate, causing long wait timeouts and hanging container resources.
* **Affected Files**: `project/browser-runtime/hardened_orchestrator.py`
* **Recommended Fix**: Implement an active SOCKS5 proxy check inside `check_proxy_integrity` that queries an external heartbeat endpoint with a strict 5-second timeout deadline.
* **Validation Steps**: Simulate a proxy sidecar outage and confirm that the orchestrator aborts context launch instantly instead of hanging.
* **Acceptance Criteria**: Script aborts gracefully on dead proxies in $< 5\text{ seconds}$.
* **Status**: **FIXED**

### GAP-05: Missing Dead Letter Task Recovery (Queue Reliability)
* **Severity**: **MEDIUM**
* **Root Cause**: Redis task execution lacks acknowledgement checks. If a container crashes midway through a task, the job disappears from the queue and is lost.
* **Affected Files**: `project/docker-compose.yml`, `project/TODO.md`
* **Recommended Fix**: Transition task delivery patterns to structured queues (like BullMQ or RQ) that implement at-least-once task delivery guarantees and dead-letter-queues (DLQ) for failed runs.
* **Validation Steps**: Terminate a container midway through a task and verify that the job is moved back into a retry queue.
* **Acceptance Criteria**: System recovers tasks safely during container failures.
* **Status**: **RESOLVED**
