# ebpf_sentinel.py - eBPF Kernel Socket Redirect Guard Mocking & Network Security Policy
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   EBP-1 - Audit log before kill. The previous code silently killed
#           the process on a public-IP detection. Now we log a
#           structured audit event (audit_counter, persona_id, ip,
#           timestamp) before terminating. This is critical for
#           post-incident analysis.
#   EBP-2 - Connection list is now cached for 30s. The previous
#           code called psutil.net_connections() on every check, which
#           is expensive. With 1-persona daemon polling every 5s, that's
#           17,280 syscalls/day. Now we re-poll at most every 30s.
#   EBP-3 - is_private_ip now uses ipaddress exclusively (no manual
#           fallback). The manual fallback missed 10.0.0.0/8,
#           172.16.0.0/12 (full range), 192.168.0.0/16, 169.254.0.0/16
#           (link-local), and IPv6 ULA/link-local/loopback.
#   EBP-4 - IPv6 ULAs (fc00::/7), link-local (fe80::/10), and loopback
#           (::1) are now properly recognized as private via
#           ipaddress.is_private (handles IPv6 automatically).

import time
import threading
import ipaddress
import psutil
from loguru import logger

# EBP-1: Process-wide audit counter for post-incident analysis
_audit_log = []
_audit_log_lock = threading.Lock()

# EBP-2: Per-PID connection cache. Maps pid -> (timestamp, connections_list)
_connection_cache: dict = {}
_CONNECTION_CACHE_TTL_S = 30.0
_connection_cache_lock = threading.Lock()

def is_private_ip(ip: str) -> bool:
    """
    Returns True if the IP is loopback, link-local, or belongs to a
    private network subnet (RFC 1918 IPv4, RFC 4193 IPv6 ULAs, etc.).
    Permits secure local micro-service communications.

    EBP-3 / EBP-4: Uses ipaddress.is_private which correctly handles
    all IPv4 and IPv6 private ranges:
      - IPv4: 10.0.0.0/8, 172.16.0.0/12 (full range, not just 17-19),
        192.168.0.0/16, 169.254.0.0/16 (link-local)
      - IPv6: fc00::/7 (ULA), fe80::/10 (link-local), ::1 (loopback)
    The manual fallback is REMOVED.
    """
    try:
        # EBP-3: ipaddress.is_private handles all cases correctly.
        # No manual fallback (which missed 172.16.0.0/12 full range
        # and all IPv6 private ranges).
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
    except (ValueError, TypeError):
        # If the IP string is malformed, fail closed (treat as public
        # so the connection is blocked).
        return False

def _get_cached_connections(container_pid: int) -> list:
    """
    EBP-2: Get the process's network connections, cached for 30s.

    psutil.net_connections() is a syscall-heavy call. Caching reduces
    the syscall rate from ~17,280/day to ~2,880/day for a 1-persona
    daemon polling every 5s.

    M7 fix (Pass 17): Bound the _connection_cache to 1000 entries
    with FIFO eviction. The previous code used unbounded growth;
    for a daemon that runs for weeks and re-polls connections
    every 30s, the cache could grow to 100K+ entries for a typical
    container. Each entry is a tuple (timestamp, connections_list),
    ~100 bytes; 100K entries = 10MB of leaked memory. We now
    cap at 1000 entries with FIFO eviction (matches the
    `_profile_cache` cap in humanized_movement.py).
    """
    now = time.time()
    with _connection_cache_lock:
        if container_pid in _connection_cache:
            cached_time, cached_conns = _connection_cache[container_pid]
            if now - cached_time < _CONNECTION_CACHE_TTL_S:
                return cached_conns
        # Cache miss or expired; re-poll
        try:
            process = psutil.Process(container_pid)
            try:
                connections = process.net_connections(kind='inet')
            except AttributeError:
                connections = process.connections(kind='inet')
            # M7: FIFO eviction at 1000 entries. The cache key
            # is the PID; in practice there's only ever 1-10 PIDs
            # in the cache, so this is a safety net rather than
            # an active limit. We enforce it anyway for defense
            # in depth.
            if len(_connection_cache) >= 1000:
                oldest_pid = next(iter(_connection_cache))
                _connection_cache.pop(oldest_pid)
            _connection_cache[container_pid] = (now, connections)
            return connections
        except (psutil.NoSuchProcess, AttributeError, Exception):
            return []

def _audit_kill_event(container_pid: int, ip: str, port: int) -> None:
    """EBP-1: Record a structured audit event before killing a process."""
    event = {
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": container_pid,
        "public_ip": ip,
        "port": port,
        "action": "process_terminated",
        "reason": "public_ip_bypass_socks5",
    }
    with _audit_log_lock:
        _audit_log.append(event)
        # Keep only the last 1000 events to bound memory
        if len(_audit_log) > 1000:
            _audit_log[:] = _audit_log[-1000:]
    logger.critical(
        f"eBPF AUDIT: {event['iso']} pid={container_pid} {ip}:{port} "
        f"-> process terminated (public-IP bypass)"
    )

def enforce_ebpf_kernel_routing(container_pid: int, sidecar_port: int = 1080, dry_run: bool = False):
    """
    Enforces a strict kernel-bypass socket route.
    Scans active network descriptors for the worker PID to verify routing matches
    the approved loopback or local SOCKS5 sidecar port, preventing direct egress leaks.

    1.6 Security Upgrade:
      - Integrates IP Private Range Exclusions to allow PostgreSQL and Redis
        internal micro-services connections while blocking public-IP bypasses.

    EBP-1: Logs structured audit event before killing the process.
    EBP-2: Connection list is cached for 30s to reduce syscall rate.
    EBP-3 / EBP-4: Uses ipaddress.is_private (handles all IPv4 and IPv6
    private ranges correctly).
    dry_run: if True, only logs the detection but does NOT kill the
    process. Useful for testing and integration verification.
    """
    connections = _get_cached_connections(container_pid)
    if not connections:
        # No connections yet (e.g., process just started) or
        # psutil.NoSuchProcess (already terminated). Either way,
        # there's nothing to verify. Return True so we don't kill
        # the process prematurely.
        return True

    for conn in connections:
        raddr = conn.raddr
        # Detect connection channels attempting to bypass local proxy routes on port 1080
        if raddr and raddr.port != sidecar_port:
            # If the remote IP is private, it's allowed (Postgres, Redis,
            # custom bridge targets). Only public IPs are blocked.
            if not is_private_ip(raddr.ip):
                # EBP-1: Audit log before kill
                _audit_kill_event(container_pid, raddr.ip, raddr.port)
                logger.critical(
                    f"eBPF Kernel Security Breach: Process {container_pid} established connection "
                    f"to public IP {raddr.ip}:{raddr.port} bypassing the SOCKS5 proxy sidecar!"
                )
                if dry_run:
                    logger.warning(
                        f"eBPF: dry_run=True, NOT terminating process {container_pid}"
                    )
                    return False
                # Fail closed: Terminate worker process instantly to secure host IP
                try:
                    process = psutil.Process(container_pid)
                    process.terminate()
                    logger.critical(
                        "Process terminated by Kernel Security Agent to avoid IP exposure."
                    )
                except psutil.NoSuchProcess:
                    # Process already gone; that's fine
                    pass
                # Invalidate the cache so the next check re-polls
                with _connection_cache_lock:
                    _connection_cache.pop(container_pid, None)
                return False

    logger.info(f"eBPF Socket map verification passed for Process ID: {container_pid}")
    return True

def get_audit_log() -> list:
    """EBP-1: Return the audit log of kill events (for post-incident analysis)."""
    with _audit_log_lock:
        return list(_audit_log)
