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
udp_probe() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY' 2>&1 || true
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
  print(f"{e.__class__.__name__}: {e}")
PY
}

check() {
    local name="$1"
    local cmd="$2"
    local expected="$3"

    # Run the check with timeout to prevent blocking on network drops
    if result=$(eval "$cmd" 2>&1); then
        if echo "$result" | grep -q "$expected"; then
            echo "✓ PASS: $name"
            PASS=$((PASS + 1))
        else
            # NET-2: surface the *type* of failure so REJECT vs DROP
            # policies are distinguishable in the audit log.
            if echo "$result" | grep -qiE "connection refused|connect: refused"; then
                echo "✓ PASS: $name (REJECT — refused, egress filtered)"
                PASS=$((PASS + 1))
            elif echo "$result" | grep -qiE "timeout|operation timed out"; then
                echo "✓ PASS: $name (DROP — silent timeout, egress filtered)"
                PASS=$((PASS + 1))
            else
                echo "✗ FAIL: $name"
                echo "  Expected match: $expected"
                echo "  Got: $result"
                FAIL=$((FAIL + 1))
            fi
        fi
    else
        # Command failed (which is often correct for DROP policies)
        if echo "$result" | grep -q "$expected"; then
            echo "✓ PASS: $name"
            PASS=$((PASS + 1))
        else
            # NET-2: same REJECT/DROP disambiguation for the failure branch
            if echo "$result" | grep -qiE "connection refused|connect: refused"; then
                echo "✓ PASS: $name (REJECT — refused, egress filtered)"
                PASS=$((PASS + 1))
            elif echo "$result" | grep -qiE "timeout|operation timed out"; then
                echo "✓ PASS: $name (DROP — silent timeout, egress filtered)"
                PASS=$((PASS + 1))
            else
                echo "✗ FAIL: $name"
                echo "  Command failed. Expected match: $expected"
                echo "  Got: $result"
                FAIL=$((FAIL + 1))
            fi
        fi
    fi
}

echo "====================================================="
echo "       SRE CONCENTRIC NETWORK SECURITY AUDIT         "
echo "====================================================="
echo "HTTP client: $HTTP_CLIENT"

# Test 1: Direct WAN Egress Blocked
check "Direct Public Egress Blocked" \
      "$HTTP_CLIENT -s --max-time 2 http://checkip.amazonaws.com" \
      "unreachable\|timeout\|blocked\|failed\|Network is unreachable"

# Test 2: Oracle Cloud Instance Metadata Service Blocked
check "Oracle Cloud Metadata Blocked" \
      "$HTTP_CLIENT -s --max-time 2 http://169.254.169.254/opc/v1/instance/" \
      "unreachable\|timeout\|blocked\|failed\|Network is unreachable"

# Test 3: Dual-Stack IPv6 Completely Blocked
check "IPv6 Interface Blocked" \
      "ping6 -c 1 -W 2 google.com" \
      "unreachable\|blocked\|failed\|Network is unreachable\|unknown host"

# Test 4: Direct Port 53 DNS Queries Blocked (Local/External DNS)
check "Direct Port 53 UDP/TCP Blocked" \
      "dig +short @8.8.8.8 google.com" \
      "unreachable\|timeout\|refused\|SERVFAIL\|Network is unreachable"

# Test 5: SOCKS5 UDP STUN/TURN Leakage Blocked
# NET-3: use the Python udp_probe helper instead of `nc` so the check
# works the same on Alpine, Debian, and busybox images.
check "STUN UDP Port 3478 Blocked" \
      "udp_probe 172.18.0.5 3478" \
      "timeout\|unreachable\|blocked\|failed\|refused\|PermissionError\|Network is unreachable"

# Test 6: mDNS Port 5353 Multicast Leakage Blocked
check "mDNS UDP Port 5353 Blocked" \
      "udp_probe 224.0.0.251 5353" \
      "timeout\|unreachable\|blocked\|failed\|refused\|PermissionError\|Network is unreachable"

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
