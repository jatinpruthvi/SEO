# fingerprint_audit.py - Headless Fingerprint & Engine Mismatch Forensic Auditor
#
# v6.0.1: Rewritten to audit CHROMIUM (CloakBrowser-compatible), not Firefox.
# The production stack uses CloakBrowser (C++ Chromium 120+). Firefox audits
# are meaningless because Firefox-specific engines (SpiderMonkey stack
# layout, performance.memory absence, etc.) are not what CloakBrowser runs.
#
# This audit validates that prototype_hardener.js correctly spoofs:
# - V8 stack trace layout (Chromium)
# - window.chrome.runtime (Chromium-only)
# - performance.memory (Chromium-only)
# - HardwareConcurrency, WebGL, canvas, media devices, permissions

import asyncio
import os
import sys
import json
from loguru import logger
from playwright.async_api import async_playwright

# Configuration
RAM_DISK_DIR = os.getenv("RAM_DISK_DIR", "worker_workspaces")
JS_HARDENER_PATH = "../browser-runtime/prototype_hardener.js"

# v6.0.1: Default to Chromium launch (was Firefox in v5.0+).
# Use the system Playwright Chromium as a test surrogate for CloakBrowser.
# In production, the executable is /opt/cloakbrowser/chrome, but for
# fingerprint-validation we just need a Chromium engine.
DEFAULT_BROWSER = os.getenv("FINGERPRINT_AUDIT_BROWSER", "chromium")  # 'chromium' or 'firefox'
CHROMIUM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class FingerprintForensicAuditor:
    def __init__(self, browser: str = DEFAULT_BROWSER):
        self.anomalies = []
        self.checks_run = 0
        self.browser = browser

    def record_anomaly(self, category: str, severity: str, description: str):
        self.anomalies.append({
            "category": category,
            "severity": severity,
            "description": description
        })

    async def run_audit(self):
        logger.info(f"Forensic Fingerprint Audit: Launching {self.browser} test context (target: CloakBrowser/Chromium)...")

        async with async_playwright() as p:
            # v6.0.1: Launch Chromium (matches CloakBrowser).
            # Firefox path retained for legacy comparison testing only.
            if self.browser == "chromium":
                browser_obj = await p.chromium.launch(headless=True)
                user_agent = CHROMIUM_USER_AGENT
                expected_engine = "v8"
            elif self.browser == "firefox":
                browser_obj = await p.firefox.launch(headless=True)
                user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
                expected_engine = "gecko"
            else:
                raise ValueError(f"Unknown browser: {self.browser}")

            context = await browser_obj.new_context(
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080}
            )

            # Inject our active production hardener script pre-execution
            js_code = None
            for path in [JS_HARDENER_PATH, "browser-runtime/prototype_hardener.js", "prototype_hardener.js"]:
                if os.path.exists(path):
                    with open(path, "r") as f:
                        js_code = f.read()
                    logger.info(f"Loaded prototype_hardener.js from: {path}")
                    break
            if js_code is None:
                logger.critical("Could not locate prototype_hardener.js")
                sys.exit(2)
            await context.add_init_script(js_code)

            page = await context.new_page()
            await page.goto("about:blank")

            # -------------------------------------------------------------
            # Execution Check 1: Engine Mismatch (V8 Chrome vs Gecko Firefox)
            # v6.0.1: Adapted for Chromium target.
            # -------------------------------------------------------------
            self.checks_run += 1
            engine_data = await page.evaluate("""() => {
                const isV8 = !!window.chrome || !!window.CSSConnection;
                const isGecko = !window.chrome && !!navigator.userAgent.includes("Firefox");
                const hasChromeRuntime = !!window.chrome && !!window.chrome.runtime;
                const hasPerformanceMemory = !!window.performance && !!window.performance.memory;

                // V8 stack trace layout check (Chromium uses '@' in error stacks)
                let stackLayout = "unknown";
                try {
                    null.foo();
                } catch (e) {
                    stackLayout = e.stack.includes("@") ? "v8" : "gecko";
                }

                return {
                    isV8,
                    isGecko,
                    hasChromeRuntime,
                    hasPerformanceMemory,
                    stackLayout,
                    ua: navigator.userAgent
                };
            }""")

            ua = engine_data["ua"]
            logger.info(f"Auditing Browser with User-Agent: {ua}")

            if self.browser == "chromium":
                # v6.0.1: Production target is Chromium.
                if not engine_data["isV8"]:
                    self.record_anomaly("Engine Mismatch", "CRITICAL", "Production targets Chromium/CloakBrowser, but V8 indicators are missing!")
                if not engine_data["hasChromeRuntime"]:
                    self.record_anomaly("Engine Mismatch", "HIGH", "Claims to be Chrome, but window.chrome.runtime is missing!")
                if not engine_data["hasPerformanceMemory"]:
                    self.record_anomaly("Engine Mismatch", "MEDIUM", "Chromium normally exposes performance.memory (Chromium-only); absence may indicate broken environment.")
                if engine_data["stackLayout"] != "v8":
                    self.record_anomaly("JS Compiler Mismatch", "CRITICAL", f"User-Agent is Chrome, but Error.stack trace layout matches Gecko (Firefox)! Got: {engine_data['stackLayout']}")
            elif self.browser == "firefox":
                if engine_data["hasChromeRuntime"]:
                    self.record_anomaly("Engine Mismatch", "CRITICAL", "Claims to be Firefox, but window.chrome.runtime is defined!")
                if engine_data["hasPerformanceMemory"]:
                    self.record_anomaly("Engine Mismatch", "HIGH", "Claims to be Firefox, but window.performance.memory (Chromium-only) is exposed!")
                if engine_data["stackLayout"] != "gecko":
                    self.record_anomaly("JS Compiler Mismatch", "CRITICAL", "User-Agent is Firefox, but Error.stack trace layout matches V8 (Chromium)!")

            # -------------------------------------------------------------
            # Execution Check 2: Headless Detection (navigator.webdriver)
            # v6.0.1: Chromium path (Navigator.prototype.__lookupGetter__ was
            # removed in Chrome 100+; use direct descriptor instead).
            # -------------------------------------------------------------
            self.checks_run += 1
            webdriver_data = await page.evaluate("""() => {
                const directFlag = navigator.webdriver;
                const descriptor = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
                let toStringValue = null;
                if (descriptor && descriptor.get) {
                    toStringValue = Function.prototype.toString.call(descriptor.get);
                } else if (descriptor && descriptor.value) {
                    toStringValue = typeof descriptor.value;
                }
                return {
                    directFlag,
                    hasDescriptor: !!descriptor,
                    toStringValue
                };
            }""")

            if webdriver_data["directFlag"] is True:
                self.record_anomaly("Headless Flag", "CRITICAL", "navigator.webdriver is directly evaluated as true!")
            if webdriver_data["toStringValue"] and "native code" not in str(webdriver_data["toStringValue"]):
                self.record_anomaly("Prototype Leak", "CRITICAL", f"navigator.webdriver getter toString is spoofed and contains non-native signatures: {webdriver_data['toStringValue']}")

            # -------------------------------------------------------------
            # Execution Check 3: WebGL Software Renderer & GPU Leaks
            # -------------------------------------------------------------
            self.checks_run += 1
            webgl_data = await page.evaluate("""() => {
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (!gl) return null;
                const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                if (!debugInfo) return null;
                return {
                    vendor: gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL),
                    renderer: gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
                };
            }""")

            if webgl_data:
                renderer = webgl_data.get("renderer", "").lower()
                vendor = webgl_data.get("vendor", "").lower()
                if "mesa" in renderer or "llvmpipe" in renderer or "swiftshader" in renderer or "software" in renderer:
                    # v6.0.1: swiftshader is OK (CloakBrowser uses swiftshader
                    # for headless), but mesa/llvmpipe indicates VM/host leak.
                    if "swiftshader" in renderer:
                        logger.info(f"WebGL: SwiftShader detected (acceptable for headless CloakBrowser): {renderer}")
                    else:
                        self.record_anomaly("Hardware Leak", "CRITICAL", f"WebGL exposes software rendering drivers ({renderer}) revealing virtualized environments!")
                if "virtualbox" in renderer or "vmware" in renderer or "qemu" in renderer or "kvm" in renderer:
                    self.record_anomaly("Hardware Leak", "CRITICAL", f"WebGL exposes VM-specific hypervisor graphics card strings: {renderer}")
                logger.info(f"WebGL Renderer audited: {webgl_data.get('renderer')}")
            else:
                self.record_anomaly("WebGL Status", "MEDIUM", "Failed to retrieve WebGL context parameter dimensions.")

            # -------------------------------------------------------------
            # Execution Check 4: Media Devices Hardware Profile
            # -------------------------------------------------------------
            self.checks_run += 1
            media_devices = await page.evaluate("""async () => {
                if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
                    return [];
                }
                const devices = await navigator.mediaDevices.enumerateDevices();
                return devices.map(d => ({ kind: d.kind, label: d.label }));
            }""")

            if len(media_devices) == 0:
                self.record_anomaly("Media Hardware Leak", "CRITICAL", "navigator.mediaDevices.enumerateDevices() is undefined or empty, revealing virtualized headless container!")
            else:
                logger.info(f"Media Hardware Devices audited: {len(media_devices)} active profiles.")

            # -------------------------------------------------------------
            # Execution Check 5: Canvas Rendering Anti-Aliased Hash
            # -------------------------------------------------------------
            self.checks_run += 1
            canvas_hash = await page.evaluate("""() => {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = "top";
                ctx.font = "14px 'Arial'";
                ctx.fillStyle = "#f60";
                ctx.fillRect(125,1,62,20);
                ctx.fillStyle = "#069";
                ctx.fillText("StealthAudit_2026", 2, 15);
                return canvas.toDataURL();
            }""")

            if len(canvas_hash) < 100:
                self.record_anomaly("Canvas Hash Leak", "HIGH", "Canvas rendering failed to compile or returned empty context payload.")

            # -------------------------------------------------------------
            # v6.0.1: NEW Check 6 - Window.Chrome and UserAgentData
            # -------------------------------------------------------------
            self.checks_run += 1
            if self.browser == "chromium":
                chrome_data = await page.evaluate("""() => {
                    const hasChrome = typeof window.chrome !== 'undefined';
                    const hasRuntime = !!(window.chrome && window.chrome.runtime);
                    const hasLoadTimes = !!(window.chrome && window.chrome.loadTimes);
                    const hasCsi = !!(window.chrome && window.chrome.csi);
                    const uaDataExists = 'userAgentData' in Navigator.prototype;
                    return { hasChrome, hasRuntime, hasLoadTimes, hasCsi, uaDataExists };
                }""")
                if not chrome_data["hasChrome"]:
                    self.record_anomaly("Chrome API", "HIGH", "window.chrome is undefined in Chromium context.")
                if not chrome_data["hasRuntime"]:
                    self.record_anomaly("Chrome Runtime", "HIGH", "window.chrome.runtime is missing in Chromium context.")
                if not chrome_data["uaDataExists"]:
                    self.record_anomaly("Client Hints", "MEDIUM", "navigator.userAgentData not in Navigator.prototype (Chromium 90+ feature).")

            # -------------------------------------------------------------
            # v6.0.1: NEW Check 7 - Permissions API
            # -------------------------------------------------------------
            self.checks_run += 1
            perms_data = await page.evaluate("""async () => {
                if (!navigator.permissions || !navigator.permissions.query) {
                    return { hasPermissions: false };
                }
                try {
                    const geo = await navigator.permissions.query({ name: 'geolocation' });
                    const notif = await navigator.permissions.query({ name: 'notifications' });
                    return {
                        hasPermissions: true,
                        geolocation: geo.state,
                        notifications: notif.state
                    };
                } catch (e) {
                    return { hasPermissions: true, error: e.message };
                }
            }""")
            if not perms_data.get("hasPermissions"):
                self.record_anomaly("Permissions API", "LOW", "navigator.permissions not available; some detection scripts probe for it.")
            elif perms_data.get("error"):
                # Some permissions may not be queryable; not critical
                logger.info(f"Permissions query: {perms_data['error']}")

            # -------------------------------------------------------------
            # Compile Audit Report
            # -------------------------------------------------------------
            await browser_obj.close()

            logger.info("======================================================================")
            logger.info(f" FORENSIC FINGERPRINT AUDIT REPORT ({self.browser.upper()} target): {len(self.anomalies)} ANOMALIES DETECTED ({self.checks_run} check blocks completed) ")
            logger.info("======================================================================")

            if len(self.anomalies) == 0:
                logger.info(f"SUCCESS: {self.browser.upper()} profile matches pristine consumer machine signatures. Zero anomalies detected!")
                sys.exit(0)
            else:
                for idx, anomaly in enumerate(self.anomalies, 1):
                    logger.warning(f"Anomaly #{idx} [{anomaly['severity']} - {anomaly['category']}]: {anomaly['description']}")
                logger.critical(f"CRITICAL EXCEPTION: {self.browser.upper()} fingerprint mismatches detected. Fix before live production runs!")
                sys.exit(1)

if __name__ == "__main__":
    import sys
    browser = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BROWSER
    if browser not in ("chromium", "firefox"):
        print(f"Unknown browser: {browser}. Use 'chromium' or 'firefox'.")
        sys.exit(2)
    auditor = FingerprintForensicAuditor(browser=browser)
    asyncio.run(auditor.run_audit())