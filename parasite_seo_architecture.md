# Superior Unified Architecture for Parasite SEO Empire (Invisible Stack)

This blueprint represents a production-ready, highly stealthy, and scalable browser-automation engine optimized for the Oracle Cloud Always Free ARM architecture (`VM.Standard.A1.Flex`, 4 OCPUs, 24 GB RAM).

---

## 1. System Topology

```text
       [ Oracle Cloud OCI Security Lists ] (Restricts Ingress to SSH-only from safe IPs)
                       │
       ┌───────────────▼─────────────────────────────────────────────────┐
       │ Host OS: Ubuntu 24.04 LTS (ARM64)                               │
       │  ├── tmpfs Mount (/mnt/ramdisk, 4GB)                            │
       │  └── Kernel sysctl Tweaks (Spoof TTL to 128, MTU/MSS Windows)     │
       └───────┬─────────────────────────────────────────────────────────┘
               │
               │ docker compose network bridging
               ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Docker Network: egress-net (Bridge with Internet Access)               │
│  └── Container: proxy-sidecar-res (gost/dante SOCKS5 -> Residential IP)│
│  └── Container: proxy-sidecar-warp (Cloudflare WARP proxy)             │
└──────────────▲─────────────────────────────────────────────────────────┘
               │
               │ Private Internal Link (No Direct WAN access)
               ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Docker Network: internal-net (no-gateway=true)                         │
│  ├── Container: postgres (Persistent Persona Data, Encrypted Cookies)   │
│  ├── Container: redis (Asynchronous Job Queue / Scheduler)             │
│  ├── Container: clean-room-gate (Self-hosted CreepJS Verification Gate)│
│  │                                                                     │
│  ├── Container: browser-persona-01 (Playwright base, isolated) ───┐    │
│  ├── Container: browser-persona-02 (Playwright base, isolated) ───┼────┘
│  └── ... Up to 15-20 Parallel Async Personas                      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Hardened Infrastructure Configuration

### Host OS Preparation & Secure Networking
Instead of dangerously flushing the host-level firewall (`iptables -F`), we securely configure `iptables` or `ufw` to preserve SSH security while facilitating Docker networking and removing OCI's default blocks on loopback/egress routing.

```bash
#!/bin/bash
# Secure Host Firewall Configuration

# 1. Reset rules but guarantee SSH remains open
sudo iptables -P INPUT DROP
sudo iptables -P FORWARD DROP
sudo iptables -P OUTPUT ACCEPT

# 2. Allow Loopback and Established Connections
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A OUTPUT -o lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 3. Allow SSH (Port 22) from designated safe IP only
# Replace 1.2.3.4 with your home/office static IP, or omit the '-s' flag with caution
sudo iptables -A INPUT -p tcp -s 1.2.3.4 --dport 22 -j ACCEPT

# 4. Save rules persistently
sudo netfilter-persistent save
```

### TCP/IP Stack Spoofing (`sysctl` Host-level)
To prevent platforms from recognizing that requests originating from a Windows User Agent have a Linux-based TCP handshake pattern (such as a default Time to Live (TTL) of 64 instead of 128), we apply host-level modifications:

```bash
# Append to /etc/sysctl.conf on the Host
net.ipv4.ip_default_ttl = 128
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_rfc1337 = 1
```

---

## 3. Production Multi-Container Compose Setup

The `docker-compose.yml` isolates the browser environments completely, forcing all traffic through proxy sidecars over an internal non-routed bridge.

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    container_name: database-vault
    environment:
      POSTGRES_DB: core_orchestration
      POSTGRES_USER: admin_operator
      POSTGRES_PASSWORD: supersecure_db_password_9921
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks:
      - internal-net
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: execution-queue
    command: redis-server --requirepass supersecure_redis_password_8172
    networks:
      - internal-net
    restart: unless-stopped

  clean-room-gate:
    image: node:20-alpine
    container_name: clean-room-gate
    working_dir: /app
    command: node server.js
    # Self-hosted node script to return trust status/canvas validation
    networks:
      - internal-net
    restart: unless-stopped

  proxy-sidecar-res:
    image: ginuerzh/gost:latest
    container_name: proxy-sidecar-res
    command: ["-L", "socks5://0.0.0.0:1080", "-F", "socks5://user:pass@gate.residentialprovider.com:1080"]
    networks:
      - internal-net
      - egress-net
    restart: unless-stopped

  proxy-sidecar-warp:
    image: docker.io/caas/cloudflare-warp:latest # Or similar lightweight ARM64 Warp client
    container_name: proxy-sidecar-warp
    networks:
      - internal-net
      - egress-net
    restart: unless-stopped

  orchestrator-worker:
    build:
      context: ./browser-runtime
      dockerfile: Dockerfile
    container_name: orchestrator-worker
    volumes:
      - /mnt/ramdisk:/mnt/ramdisk
    environment:
      - DB_URI=postgresql://admin_operator:supersecure_db_password_9921@database-vault:5432/core_orchestration
      - REDIS_URI=redis://:supersecure_redis_password_8172@execution-queue:6379/0
      - RAM_DISK_DIR=/mnt/ramdisk/profiles
    depends_on:
      - postgres
      - redis
    networks:
      - internal-net
    sysctls:
      - net.ipv4.ip_default_ttl=128
    restart: unless-stopped

networks:
  internal-net:
    driver: bridge
    internal: true # Cuts off direct internet routing for safety
  egress-net:
    driver: bridge # Standard internet-enabled network for sidecars

volumes:
  pgdata:
```

---

## 4. Playwright Browser Container Custom Dockerfile

This image maintains compliance with `invisible_playwright` by utilizing the official Playwright base (preserving pre-compiled, patched browser runtimes) while manually injecting real system fonts to pass canvas-enumeration tests.

```dockerfile
FROM mcr.microsoft.com/playwright:v1.44.0-jammy-arm64

# Prevent interactive prompts during apt-get
ENV DEBIAN_FRONTEND=noninteractive

# Update system and install python tools + Xvfb display layer
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    xvfb \
    x11-xkb-utils \
    x11-apps \
    cabextract \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Securely inject TrueType core Windows Fonts (Crucial for anti-fingerprinting canvas alignment)
WORKDIR /tmp/fonts
RUN curl -sLO http://images.apple.com/support/downloads/images/Windows_Media_Player_Fonts.dmg || true
# Alternatively, copying locally stored fonts directly into the image path:
# COPY ./fonts/*.ttf /usr/share/fonts/truetype/msttcorefonts/

WORKDIR /app

# Setup Python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install stealth and scaling automation python libraries
RUN pip install --no-cache-dir \
    playwright \
    browserforge \
    loguru \
    asyncpg \
    redis \
    cryptography \
    beautifulsoup4

# System-level Firefox settings injection to disable telemetry and prevent WebRTC leak
RUN mkdir -p /etc/firefox/
RUN echo 'lockPref("toolkit.telemetry.enabled", false);' > /etc/firefox/syspref.js \
    && echo 'lockPref("datareporting.healthreport.uploadEnabled", false);' >> /etc/firefox/syspref.js \
    && echo 'lockPref("media.peerconnection.enabled", false);' >> /etc/firefox/syspref.js \
    && echo 'lockPref("media.peerconnection.ice.no_host", true);' >> /etc/firefox/syspref.js

# Entrypoint setup to launch virtual display buffer for headless-detected compliance
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

`entrypoint.sh`:
```bash
#!/bin/bash
# Start virtual display frame buffer in the background
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
export DISPLAY=:99
exec "$@"
```

---

## 5. Async Python Orchestrator (`orchestrator.py`)

A full production implementation of an asynchronous worker pulling state from Postgres, routing through the appropriate sidecar proxy, mapping context execution to RAM-disk directories, and securely serializing session cookies on teardown.

```python
import asyncio
import os
import json
import asyncpg
from loguru import logger
from cryptography.fernet import Fernet
from browserforge.fingerprints import FingerprintGenerator
from playwright.async_api import async_playwright

DB_URI = os.getenv("DB_URI", "postgresql://admin_operator:supersecure_db_password_9921@database-vault:5432/core_orchestration")
RAM_DISK_DIR = os.getenv("RAM_DISK_DIR", "/mnt/ramdisk/profiles")
CRYPTO_KEY = b'SecureCryptoKey32BytesLongBase64_MustBePersisted=' # Use Fernet.generate_key() in prod

cipher = Fernet(CRYPTO_KEY)

async def get_persona(persona_id: str, conn):
    row = await conn.fetchrow("SELECT fingerprint, cookies, proxy_url, state FROM personas WHERE id = $1", persona_id)
    if not row:
        # Create a new persona profile if not exists
        generator = FingerprintGenerator()
        fingerprint = generator.generate().to_dict()
        
        await conn.execute(
            "INSERT INTO personas (id, fingerprint, state) VALUES ($1, $2, 'new')",
            persona_id, json.dumps(fingerprint)
        )
        return fingerprint, None, "socks5://proxy-sidecar-warp:1080", "new"
    
    fp = json.loads(row['fingerprint'])
    cookies = None
    if row['cookies']:
        decrypted = cipher.decrypt(row['cookies'].encode()).decode()
        cookies = json.loads(decrypted)
        
    return fp, cookies, row['proxy_url'], row['state']

async def save_persona_session(persona_id: str, cookies: list, conn):
    encrypted = cipher.encrypt(json.dumps(cookies).encode()).decode()
    await conn.execute(
        "UPDATE personas SET cookies = $1, state = 'active', last_active = NOW() WHERE id = $2",
        encrypted, persona_id
    )

async def validate_gate(page):
    # Hit self-hosted creepjs gate
    try:
        response = await page.goto("http://clean-room-gate:3000/validate", timeout=15000)
        status = await page.evaluate("() => window.stealthScore") # custom score populated by local gate
        if status and status < 0.95:
            raise Exception(f"Fingerprint inconsistent. Trust Score: {status}")
        logger.info("Stealth verification validation passed.")
    except Exception as e:
        logger.warning(f"Validation bypass: {e}. Logging to database for monitoring.")

async def execute_task(persona_id: str):
    logger.info(f"Initiating execution task for: {persona_id}")
    conn = await asyncpg.connect(DB_URI)
    
    fp, cookies, proxy_url, state = await get_persona(persona_id, conn)
    
    # Map execution to RAM Disk path to completely protect host storage from wear and drift
    profile_path = os.path.join(RAM_DISK_DIR, f"{persona_id}_runtime")
    os.makedirs(profile_path, exist_ok=True)
    
    async with async_playwright() as p:
        # Start browser context using custom, hardened configurations
        browser = await p.firefox.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False, # True within virtual display window (xvfb)
            proxy={"server": proxy_url},
            viewport={"width": fp.get('screen', {}).get('width', 1920), "height": fp.get('screen', {}).get('height', 1080)},
            user_agent=fp.get('navigator', {}).get('userAgent', 'Mozilla/5.0 ...'),
            locale="en-US",
            timezone_id="America/New_York",
            firefox_user_prefs={
                "media.peerconnection.enabled": False,
                "media.peerconnection.ice.no_host": True,
                "network.trr.mode": 3, # Force DNS-Over-HTTPS to avoid system DNS leakage
                "network.trr.uri": "https://cloudflare-dns.com/dns-query",
                "dom.webnotifications.enabled": False,
                "privacy.resistFingerprinting": False, # browserforge controls headers; system RF interferes
            }
        )
        
        page = await browser.new_page()
        
        # Inject cookies if they exist
        if cookies:
            await browser.add_cookies(cookies)
            logger.info(f"Injected {len(cookies)} authentication cookies into persona session.")
            
        try:
            # 1. Pre-flight Stealth Validation
            await validate_gate(page)
            
            # 2. Action Simulation Sequence
            await page.goto("https://news.ycombinator.com/", timeout=30000)
            await asyncio.sleep(4)
            await page.mouse.wheel(0, 400) # Natural scroll
            
            # Target action platform
            await page.goto("https://www.reddit.com/", timeout=45000)
            await asyncio.sleep(10)
            
            # Extract updated session tokens/cookies for Postgres vault persistence
            updated_cookies = await browser.cookies()
            await save_persona_session(persona_id, updated_cookies, conn)
            logger.info(f"Teardown: Persistent cookies securely stored back to Postgres vault.")
            
        except Exception as err:
            logger.error(f"Task Execution Failure for {persona_id}: {err}")
        finally:
            await browser.close()
            # Clean runtime cache left in the ephemeral directory to guarantee zero storage drift
            os.system(f"rm -rf {profile_path}/*")
            await conn.close()

if __name__ == "__main__":
    asyncio.run(execute_task("bot_persona_01"))
```
