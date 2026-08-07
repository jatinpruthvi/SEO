# Dockerfile - CloakBrowser + cloak-runtime + VNC/noVNC
#
# This image provides:
#   - Chromium with stealth patches (using a Chromium binary; replace with
#     CloakBrowser binary when available in your environment)
#   - cloak-runtime npm package installed globally
#   - Xvfb + x11vnc for VNC access
#   - noVNC for browser-based VNC access
#   - A demo script that launches a hardened browser and visits CreepJS
#
# Build:    docker build -t cloak-runtime .
# Run VNC:  docker run -d -p 5900:5900 -p 6080:6080 cloak-runtime
# Then:     open http://localhost:6080/vnc.html in any browser
#           OR connect with VNC client to localhost:5900

FROM mcr.microsoft.com/playwright:v1.40.0-jammy

LABEL maintainer="CloakBrowser Project"
LABEL description="Anti-fingerprinting browser runtime with VNC access"

# ============================================================
# 1. Install VNC and X11 components
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        xdotool \
        scrot \
        fluxbox \
        xterm \
        curl \
        wget \
        ca-certificates \
        procps \
        net-tools \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# 2. Install noVNC for browser-based VNC access
# ============================================================
ENV NOVNC_VERSION=1.4.0
RUN cd /opt && \
    wget -q https://github.com/novnc/noVNC/archive/v${NOVNC_VERSION}.tar.gz && \
    tar xzf v${NOVNC_VERSION}.tar.gz && \
    rm v${NOVNC_VERSION}.tar.gz && \
    mv noVNC-${NOVNC_VERSION} novnc && \
    cd /opt/novnc && \
    ln -sf /usr/share/novnc/utils/launch.sh /usr/local/bin/novnc-launch

# websockify for noVNC websocket bridge
RUN pip3 install --no-cache-dir websockify

# ============================================================
# 3. Install cloak-runtime
# ============================================================
WORKDIR /opt/cloak-runtime
COPY package.json ./
COPY src/ ./src/
COPY manifests/ ./manifests/
COPY test/ ./test/
RUN npm install --omit=dev --no-audit --no-fund

# Copy anti_detect_signature.js into the runtime
COPY ../browser-runtime/anti_detect_signature.js /opt/cloak-runtime/src/anti_detect_signature.js

# Make cloak available globally
RUN npm link

# ============================================================
# 4. Set up VNC password (default: cloak)
# ============================================================
RUN mkdir -p /root/.vnc && \
    x11vnc -storepasswd cloak /root/.vnc/passwd

# ============================================================
# 5. Create the demo persona runner
# ============================================================
RUN cat > /opt/cloak-runtime/demo.js << 'JSEOF'
// demo.js - Demonstrate cloak-runtime by visiting CreepJS
// and printing the patch state.
const { cloak } = require('cloak-runtime');

(async () => {
    console.log('cloak-runtime demo starting...');
    console.log('Available personas:', cloak.listPersonas());

    const browser = await cloak.launch({
        persona: 'chrome-128-win',
        headless: process.env.HEADLESS !== '0',
    });

    const context = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
    });

    // Install cloak runtime into this context
    if (browser._installCloak) {
        const sid = await browser._installCloak(context);
        console.log('Session ID:', sid);
    }

    const page = await context.newPage();

    console.log('Navigating to https://abrahamjuliot.github.io/creepjs/ ...');
    try {
        await page.goto('https://abrahamjuliot.github.io/creepjs/', {
            waitUntil: 'networkidle',
            timeout: 60000,
        });
        await page.waitForTimeout(15000);  // CreepJS takes ~15s to fully run

        const result = await page.evaluate(() => ({
            userAgent: navigator.userAgent,
            webdriver: navigator.webdriver,
            platform: navigator.platform,
            languages: navigator.languages,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory: navigator.deviceMemory,
            screenWidth: screen.width,
            screenHeight: screen.height,
            colorDepth: screen.colorDepth,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            cloakSalt: window.__cloak_signature_salt__,
            cloakSession: window.__cloak_session_id__,
            cloakRuntime: window.__cloak_runtime__?.name,
            patchCount: window.__cloak_runtime__?.patchCount,
        }));

        console.log('\n=== Fingerprint Report ===');
        console.log(JSON.stringify(result, null, 2));
    } catch (e) {
        console.error('Navigation failed:', e.message);
    } finally {
        await browser.close();
    }
})();
JSEOF

# ============================================================
# 6. Create the entrypoint script that starts VNC + Chromium
# ============================================================
RUN cat > /opt/cloak-runtime/entrypoint.sh << 'BASHEOF'
#!/bin/bash
# entrypoint.sh - Start Xvfb, Fluxbox, VNC, noVNC, and the demo

set -e

export DISPLAY=:99
export RESOLUTION=${RESOLUTION:-1920x1080x24}

echo "[cloak] Starting Xvfb on :99 at $RESOLUTION..."
Xvfb :99 -screen 0 $RESOLUTION &
XVFB_PID=$!
sleep 1

echo "[cloak] Starting Fluxbox (window manager)..."
fluxbox &
FLUXBOX_PID=$!
sleep 1

echo "[cloak] Starting x11vnc on port 5900..."
x11vnc -display :99 \
       -forever \
       -shared \
       -rfbport 5900 \
       -rfbauth /root/.vnc/passwd \
       -bg \
       -o /var/log/x11vnc.log

echo "[cloak] Starting noVNC on port 6080..."
/opt/novnc/utils/launch.sh --listen 6080 --vnc localhost:5900 --web /opt/novnc &
NOVNC_PID=$!
sleep 2

echo ""
echo "============================================="
echo "  cloak-runtime is running!"
echo "  VNC: localhost:5900  (password: cloak)"
echo "  noVNC: http://localhost:6080/vnc.html"
echo "============================================="
echo ""

# If DEMO=1, run the demo
if [ "${DEMO:-0}" = "1" ]; then
    echo "[cloak] DEMO=1, running demo.js..."
    cd /opt/cloak-runtime
    node demo.js
fi

# If INTERACTIVE=1, drop to a shell with Chromium available
if [ "${INTERACTIVE:-0}" = "1" ]; then
    echo "[cloak] INTERACTIVE=1, launching Chromium..."
    cd /root
    exec chromium-browser \
        --no-sandbox \
        --disable-dev-shm-usage \
        --load-extension=/opt/cloak-runtime/src \
        "https://abrahamjuliot.github.io/creepjs/"
fi

# If no mode set, just keep the VNC running
if [ "${DEMO:-0}" != "1" ] && [ "${INTERACTIVE:-0}" != "1" ]; then
    echo "[cloak] VNC mode only. Connect to view desktop."
    echo "[cloak] To run demo: docker exec -e DEMO=1 <container> /opt/cloak-runtime/entrypoint.sh"
    echo "[cloak] Or set DEMO=1 or INTERACTIVE=1 environment variable"
    # Keep running
    tail -f /var/log/x11vnc.log &
    wait $XVFB_PID
fi
BASHEOF
RUN chmod +x /opt/cloak-runtime/entrypoint.sh

# ============================================================
# 7. Expose ports
# ============================================================
EXPOSE 5900 6080

# ============================================================
# 8. Set up healthcheck
# ============================================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:6080/ > /dev/null || exit 1

# ============================================================
# 9. Default command
# ============================================================
CMD ["/opt/cloak-runtime/entrypoint.sh"]
