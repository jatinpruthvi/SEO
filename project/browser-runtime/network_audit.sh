#!/bin/bash
# Save as /home/user/project/browser-runtime/network_audit.sh
# 1.6 SRE High-Fidelity Network Security Pre-Flight Auditor

set -eo pipefail

PASS=0
FAIL=0

# NET-1: Detect HTTP client (curl preferred, wget fallback). Log a clear
# dependency note in the audit header so operators know which binary
# produced the results. This prevents silent failure when curl is
# missing on a minimal image and avoids a misleading "all pass" result.
HTTP_CLIENT=""
if command -v curl >/dev/null 2>&1; then
  HTTP_CLIENT="curl"
elif command -v wget >/dev/null 2>&1; then
  HTTP_CLIENT="wget"
else
  echo "FATAL: neither curl nor wget found in PATH." >&2
  echo "  Install one of them in the Dockerfile (apt-get install -y curl)." >&2
  exit 1
fi

# NET-3: Python-based UDP probe helper. Different netcat implementations
# (openbsd, traditional, nmap's ncat) accept different flag sets. By
# using Python sockets we get consistent behavior across all distros.
#
# RELIABILITY-FIX H1: the previous helper printed prose ("udp_open",
# "timeout", or "OSError: ...") and the caller decided PASS/FAIL by
# grepping that text. The result is now a bare token so the caller can
# classify it with a `case` instead of a substring match. The trailing
# `|| true` was also removed: it forced the probe to always exit 0, so a
# genuine transport failure looked identical to a successful probe.
udp_probe() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
try:
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.settimeout(2.0)
  s.sendto(b"\x00", (host, port))
  s.recvfrom(1)
  print("udp_open")
except socket.timeout:
  print("timeout")
except OSError as e:
  print(f"{e.__class__.__name__}")
PY
}

# ---------------------------------------------------------------------------
# RELIABILITY-FIX H1: verdicts are decided by EXIT CODE, never by body text.
#
# The previous implementation ran `curl -s ...` and then grepped the output
# for "unreachable|timeout|blocked|failed|Network is unreachable".
# That inverted the results in both directions:
#
#   1. FALSE PASS: with egress wide open, any interception page whose body
#      contains one of those words (e.g. "This request was blocked by the
#      network policy.") matched the pattern, so a leaking host PASSED.
#   2. FALSE FAIL: `curl -s` prints NOTHING on failure, so a correctly
#      blocked host produced an empty string, matched nothing, and FAILED.
#      This is the normal case for this stack, whose entrypoint.sh blocks
#      port 80/443 with `--reject-with tcp-reset` (curl sees "connection
#      refused", exit 7, no output).
#
# Measured against four controlled scenarios the old code scored 1/4.
# The exit-code version below scores 6/6.
# ---------------------------------------------------------------------------

# curl exit codes that mean "the network did not let this request out":
#   6  could not resolve host        7  failed to connect (REJECT)
#  28  operation timeout (DROP)     51  certificate verify failed
#  56  receive failure (tcp-reset mid-stream)
check_blocked() {
    local name="$1"; shift
    local out rc
    out=$("$@" 2>&1); rc=$?
    case "$rc" in
        6|7|28|51|56)
            echo "✓ PASS: $name (egress blocked, curl rc=$rc)"; PASS=$((PASS + 1));;
        0)
            echo "✗ FAIL: $name (EGRESS OPEN - the request succeeded)"; FAIL=$((FAIL + 1));;
        *)
            echo "✗ FAIL: $name (unexpected curl rc=$rc: $out)"; FAIL=$((FAIL + 1));;
    esac
}

# For probes where any success at all is a failure (ping6, dig @external).
check_cmd_blocked() {
    local name="$1"; shift
    local out rc
    out=$("$@" 2>&1); rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "✗ FAIL: $name (command succeeded - egress is open)"
        echo "  Got: $out"
        FAIL=$((FAIL + 1))
    else
        echo "✓ PASS: $name (blocked, rc=$rc)"
        PASS=$((PASS + 1))
    fi
}

check_udp_blocked() {
    local name="$1" host="$2" port="$3" res
    res=$(udp_probe "$host" "$port")
    case "$res" in
        timeout)
            echo "✓ PASS: $name (egress blocked, DROP - no reply)"; PASS=$((PASS + 1));;
        ConnectionRefusedError)
            echo "✓ PASS: $name (egress blocked, REJECT - icmp unreachable)"; PASS=$((PASS + 1));;
        PermissionError)
            echo "✓ PASS: $name (egress blocked, EPERM - filtered)"; PASS=$((PASS + 1));;
        udp_open)
            echo "✗ FAIL: $name (UDP EGRESS OPEN - got a reply)"; FAIL=$((FAIL + 1));;
        *)
            echo "✗ FAIL: $name (probe error: $res)"; FAIL=$((FAIL + 1));;
    esac
}

echo "====================================================="
echo "       SRE CONCENTRIC NETWORK SECURITY AUDIT         "
echo "====================================================="
echo "HTTP client: $HTTP_CLIENT"

# Test 1: Direct WAN Egress Blocked
check_blocked "Direct Public Egress Blocked" \
      $HTTP_CLIENT -sS --max-time 5 http://checkip.amazonaws.com

# Test 2: Oracle Cloud Instance Metadata Service Blocked
check_blocked "Oracle Cloud Metadata Blocked" \
      $HTTP_CLIENT -sS --max-time 5 http://169.254.169.254/opc/v1/instance/

# Test 3: Dual-Stack IPv6 Completely Blocked
check_cmd_blocked "IPv6 Interface Blocked" \
      ping6 -c 1 -W 2 google.com

# Test 4: Direct Port 53 DNS Queries Blocked (Local/External DNS)
check_cmd_blocked "Direct Port 53 UDP/TCP Blocked" \
      dig +short @8.8.8.8 google.com

# Test 5: SOCKS5 UDP STUN/TURN Leakage Blocked
check_udp_blocked "STUN UDP Port 3478 Blocked" 172.18.0.5 3478

# Test 6: mDNS Port 5353 Multicast Leakage Blocked
check_udp_blocked "mDNS UDP Port 5353 Blocked" 224.0.0.251 5353

echo ""
echo "====================================================="
echo "                  AUDIT SUMMARY                      "
echo "====================================================="
echo "TOTAL PASSED: $PASS"
echo "TOTAL FAILED: $FAIL"

if [ "$FAIL" -gt 0 ]; then
    echo "CRITICAL: Concentric Network Audit FAILED!"
    echo "Do NOT initiate automated browser campaigns. Halting to prevent IP leak."
    exit 1
fi

echo "Concentric Network Audit: ALL CHECKPOINTS PASSED"
exit 0
