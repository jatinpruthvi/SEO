#!/bin/bash
# Host firewall setup securing incoming ports while preserving Docker bridge routing channels

# Prevent execution if running inside docker containers
if [ -f /.dockerenv ]; then
    echo "ERROR: This script must be executed on the Host OS, not inside a container."
    exit 1
fi

echo "Applying hardened, Docker-safe iptables firewall configuration..."

# 1. Set default policies to deny unrequested incoming traffic
sudo iptables -P INPUT DROP
sudo iptables -P FORWARD ACCEPT # Required for Docker bridge routing

# 2. Preserve essential loopback and established connection channels
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 3. Secure SSH (Port 22) - Replace with your static IP if possible using: -s <YOUR_IP>
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# 4. Create custom Docker chain checks (Self-healing initialization)
sudo iptables -N DOCKER-USER 2>/dev/null
sudo iptables -F DOCKER-USER

# Allow established container traffic
sudo iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# ---------------------------------------------------------------------------
# RELIABILITY-FIX M4: sidecar addresses are parameterised instead of hardcoded.
# These are the *egress-net* addresses (docker-compose.yml: egress-net is
# 172.19.0.0/24, internal-net is 172.18.0.0/24). The previous file hardcoded
# them in two rules; if the subnet ever changes, the whitelist silently stops
# matching and every container's egress dies with no obvious cause.
# Override with: EGRESS_PROXY_IP=10.0.0.5 sudo -E ./iptables_docker.sh
# ---------------------------------------------------------------------------
EGRESS_PROXY_IP="${EGRESS_PROXY_IP:-172.19.0.5}"   # proxy-sidecar-res on egress-net
EGRESS_DOH_IP="${EGRESS_DOH_IP:-172.19.0.6}"       # doh-proxy on egress-net

# ---------------------------------------------------------------------------
# ORDER MATTERS (RELIABILITY-FIX M4).
#
# The whitelist rules used to be appended BEFORE every DROP rule below. -j ACCEPT
# terminates chain traversal, so the sidecars bypassed the DNS, mDNS, LLMNR,
# SSDP and multicast blocks entirely -- including the ones added specifically to
# stop local-network discovery leaks. All DROPs are now installed first, and the
# whitelists punch through only what the sidecars actually need.
# ---------------------------------------------------------------------------

# DROP any container packet trying to route directly to external WAN interfaces bypassing proxy sidecars
# (Ensures container-level IP leak protection even if container-level iptables fails)
# Note: Allows ports 53 (DNS) and standard egress through our designated proxy gateway sidecars
# Includes both the default docker0 bridge and all custom compose networks (br-+ interfaces)
sudo iptables -A DOCKER-USER -i docker0 -p tcp ! --dport 1080 -j DROP
sudo iptables -A DOCKER-USER -i br-+ -p tcp ! --dport 1080 -j DROP

# CHAMELEON (GAP-3.2, GAP-3.4): Block all DNS and mDNS from containers.
# Force all DNS through the DoH proxy (172.19.0.6:443). Block mDNS/LLMNR
# which are local network discovery protocols that leak the host's
# network topology.
sudo iptables -A DOCKER-USER -i br-+ -p udp --dport 53 -j DROP
sudo iptables -A DOCKER-USER -i br-+ -p tcp --dport 53 -j DROP
sudo iptables -A DOCKER-USER -i docker0 -p udp --dport 53 -j DROP
sudo iptables -A DOCKER-USER -i docker0 -p tcp --dport 53 -j DROP
sudo iptables -A DOCKER-USER -i br-+ -p udp --dport 5353 -j DROP  # mDNS
sudo iptables -A DOCKER-USER -i br-+ -p udp --dport 5355 -j DROP  # LLMNR
sudo iptables -A DOCKER-USER -i docker0 -p udp --dport 5353 -j DROP
sudo iptables -A DOCKER-USER -i docker0 -p udp --dport 5355 -j DROP

# R7-DEEP-AUDIT-2 ROUND 1 FIX (Multicast/SSDP Discovery Leaks):
# Block all multicast egress to prevent local network discovery leaks.
# Real Chrome and Chromium can use multicast (239.255.255.250 for
# SSDP, 224.0.0.251 for mDNS, 224.0.0.22 for IGMP, etc.) to discover
# local network devices. Even in a network namespace, if multicast
# routing is not blocked, local device names (routers, printers,
# smart TVs) leak to the page, which can be used as a fingerprint
# to detect non-residential connections.
# - 224.0.0.0/4 is the entire multicast range (RFC 5771)
# - 239.0.0.0/8 is administratively scoped multicast
# - ff00::/8 is IPv6 multicast (already blocked via IPv6 DROP)
sudo iptables -A DOCKER-USER -i br-+ -d 224.0.0.0/4 -j DROP
sudo iptables -A DOCKER-USER -i br-+ -d 239.0.0.0/8 -j DROP
sudo iptables -A DOCKER-USER -i docker0 -d 224.0.0.0/4 -j DROP
sudo iptables -A DOCKER-USER -i docker0 -d 239.0.0.0/8 -j DROP
# Block SSDP specifically (port 1900) - UPnP discovery protocol
sudo iptables -A DOCKER-USER -i br-+ -p udp --dport 1900 -j DROP
sudo iptables -A DOCKER-USER -i docker0 -p udp --dport 1900 -j DROP

# RELIABILITY-FIX M4: UDP kill-switch catch-all.
# Every DROP above is `-p tcp` (or a single UDP port). UDP to any other port on
# any external destination -- QUIC/HTTP3 on 443, IKE/500, SNMP/161, or plain
# exfiltration on a high port -- was therefore ALLOWED at the host layer, even
# though this file's comment claims it is the backstop for "even if
# container-level iptables fails". Now default-deny UDP as well.
# (The per-container policy in entrypoint.sh is `-P OUTPUT DROP`, so this is
# defence in depth -- it becomes load-bearing if a container ever runs without
# NET_ADMIN, or that section is skipped.)
sudo iptables -A DOCKER-USER -i br-+ -p udp -j DROP
sudo iptables -A DOCKER-USER -i docker0 -p udp -j DROP

# ---------------------------------------------------------------------------
# SRE Network Hardening Whitelists (installed LAST - see ORDER MATTERS above).
# ---------------------------------------------------------------------------
# 1. Whitelist proxy-sidecar-res egress to connect to any port on the WAN
# (Required for GOST to establish forward connections to your upstream residential proxy ports)
# UDP is permitted because SOCKS5 UDP ASSOCIATE needs it, but only AFTER the
# 53 / 5353 / 5355 / 1900 / multicast DROPs above, so the sidecar can no longer
# use those for local-network discovery.
sudo iptables -A DOCKER-USER -s "$EGRESS_PROXY_IP" -j ACCEPT

# 2. Whitelist doh-proxy to connect to port 443 (DoH) over the WAN
# (Required for cloudflared to resolve upstream DNS queries securely over HTTPS)
sudo iptables -A DOCKER-USER -s "$EGRESS_DOH_IP" -p tcp --dport 443 -j ACCEPT

# Block IPv6 to prevent IPv6-only IP-leak vectors
sudo ip6tables -A DOCKER-USER -i br-+ -j DROP 2>/dev/null || true
sudo ip6tables -A DOCKER-USER -i docker0 -j DROP 2>/dev/null || true

# Host-Level SRE Fail-Safe: Clamp TCP MSS for all forwarded container packets to 1200
# This guarantees that even if a container network namespace (like the SOCKS5 proxy sidecars)
# has no local iptables rules, the host kernel clamps all TCP handshakes to prevent fragmentation
# timing leaks over encrypted residential tunnels and WireGuard (WARP) egress interfaces.
sudo iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200

# Save rules persistently across reboots
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo iptables-save | sudo tee /etc/iptables/rules.v4 > /dev/null
echo "iptables rules successfully applied and saved."
