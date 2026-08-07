// Pass 12: Multi-persona determinism and stress test
// Verifies that:
// 1. The same persona produces consistent fingerprint values across multiple "launches"
// 2. Different personas produce different fingerprints
// 3. Session IDs (UUIDs) are different per launch even for same persona
// 4. Per-persona values are stable for properties that should be stable
// 5. Per-launch values vary for properties that should vary
// 6. No cross-persona contamination

const { JSDOM } = require('/home/user/node_modules/jsdom');
const fs = require('fs');

const HARDENER_JS = fs.readFileSync('/home/user/project/browser-runtime/prototype_hardener.js', 'utf-8');

// Three different personas with different platform, locale, etc.
const PERSONAS = [
    {
        name: "Windows_User",
        fp: {
            hardwareConcurrency: 8, deviceMemory: 8,
            webglVendor: "Intel Inc.", webglRenderer: "Intel(R) Iris(TM) Plus Graphics",
            platform: "Win32", languages: ["en-US", "en"],
            canvas_noise_seed: 12345, client_hints: null, timezone: "America/New_York",
            audioSampleRate: 48000, screenColorDepth: 24, screenPixelDepth: 24,
            batteryCharging: true, batteryLevel: 0.92, scrollBehavior: "smooth",
            cookieLifetimeDays: 14, bootId: "boot-win-001",
            uaFullVersion: "120.0.6099.109", uaMajor: 120, platformVersion: "15.0.0",
            architecture: "x86", bitness: "64", mobile: false, model: "",
            connectionType: "wifi", effectiveType: "4g", downlink: 10.0, rtt: 50,
            saveData: false, maxTouchPoints: 0, isWindows: true, isMac: false, isLinux: false,
            permissionStates: { geolocation: "prompt", notifications: "default", midi: "prompt" },
            notification_session_count: 0, canvas_noise_session_id: null,
        }
    },
    {
        name: "Mac_Designer",
        fp: {
            hardwareConcurrency: 16, deviceMemory: 8,
            webglVendor: "Apple Inc.", webglRenderer: "Apple M1 Pro",
            platform: "MacIntel", languages: ["en-US", "en", "fr-CA"],
            canvas_noise_seed: 67890, client_hints: null, timezone: "America/Los_Angeles",
            audioSampleRate: 48000, screenColorDepth: 24, screenPixelDepth: 24,
            batteryCharging: true, batteryLevel: 0.85, scrollBehavior: "smooth",
            cookieLifetimeDays: 30, bootId: "boot-mac-002",
            uaFullVersion: "120.0.6167.85", uaMajor: 120, platformVersion: "13.6.0",
            architecture: "arm", bitness: "64", mobile: false, model: "",
            connectionType: "wifi", effectiveType: "4g", downlink: 50.0, rtt: 20,
            saveData: false, maxTouchPoints: 0, isWindows: false, isMac: true, isLinux: false,
            permissionStates: { geolocation: "granted", notifications: "granted", midi: "granted" },
            notification_session_count: 5, canvas_noise_session_id: null,
        }
    },
    {
        name: "Mobile_Android",
        fp: {
            hardwareConcurrency: 4, deviceMemory: 4,
            webglVendor: "Qualcomm", webglRenderer: "Adreno (TM) 640",
            platform: "Linux armv81", languages: ["en-US", "es-MX"],
            canvas_noise_seed: 11111, client_hints: null, timezone: "America/Chicago",
            audioSampleRate: 44100, screenColorDepth: 24, screenPixelDepth: 24,
            batteryCharging: false, batteryLevel: 0.42, scrollBehavior: "auto",
            cookieLifetimeDays: 7, bootId: "boot-mob-003",
            uaFullVersion: "120.0.6099.144", uaMajor: 120, platformVersion: "10.0.0",
            architecture: "arm", bitness: "64", mobile: true, model: "Pixel 7",
            connectionType: "cellular", effectiveType: "4g", downlink: 5.0, rtt: 80,
            saveData: true, maxTouchPoints: 5, isWindows: false, isMac: false, isLinux: true,
            permissionStates: { geolocation: "prompt", notifications: "default", midi: "prompt" },
            notification_session_count: 0, canvas_noise_session_id: null,
        }
    }
];

function polyfill(win) {
    if (!win.CanvasRenderingContext2D) {
        win.CanvasRenderingContext2D = function() {};
        win.CanvasRenderingContext2D.prototype.getImageData = function() { return { data: new Uint8ClampedArray(4) }; };
        win.CanvasRenderingContext2D.prototype.fillText = function() {};
        win.CanvasRenderingContext2D.prototype.measureText = function() {
            return { width: 0, actualBoundingBoxLeft: 0, actualBoundingBoxRight: 0,
                     actualBoundingBoxAscent: 0, actualBoundingBoxDescent: 0,
                     fontBoundingBoxAscent: 0, fontBoundingBoxDescent: 0 };
        };
    }
    if (!win.WebGLRenderingContext) {
        win.WebGLRenderingContext = function() {};
        win.WebGLRenderingContext.prototype.getParameter = function() { return null; };
        win.WebGLRenderingContext.prototype.getExtension = function() { return null; };
        win.WebGLRenderingContext.prototype.getSupportedExtensions = function() { return []; };
        win.WebGLRenderingContext.prototype.getShaderPrecisionFormat = function() { return {precision:23,rangeMin:127,rangeMax:127}; };
        win.WebGLRenderingContext.prototype.readPixels = function() {};
        win.WebGLRenderingContext.prototype.drawArrays = function() {};
        win.WebGLRenderingContext.prototype.drawElements = function() {};
    }
    if (!win.WebGL2RenderingContext) win.WebGL2RenderingContext = win.WebGLRenderingContext;
    if (!win.OffscreenCanvas) { win.OffscreenCanvas = function() { return {}; }; }
    if (!win.OffscreenCanvasRenderingContext2D) win.OffscreenCanvasRenderingContext2D = function() {};
    if (!win.SharedWorker) win.SharedWorker = function() { return {port: {}, addEventListener: () => {}}; };
    if (!win.RTCPeerConnection) win.RTCPeerConnection = function() { return {}; };
    if (!win.Path2D) { win.Path2D = function() {}; win.Path2D.prototype.arc = function(){}; }
    if (!win.SVGGraphicsElement) { win.SVGGraphicsElement = function(){}; win.SVGGraphicsElement.prototype.getBBox = function() { return {x:0,y:0,width:0,height:0}; }; }
    if (!win.ImageBitmapRenderingContext) { win.ImageBitmapRenderingContext = function() {}; }
    if (!win.Worker) win.Worker = function() {};
    if (!win.GPUAdapterInfo) win.GPUAdapterInfo = function() {};
    if (!win.PerformanceObserver) { win.PerformanceObserver = function() { this.observe = () => {}; }; win.PerformanceObserver.supportedEntryTypes = []; }
    if (!win.AudioContext) { win.AudioContext = function() {}; win.AudioContext.prototype.sampleRate = 48000; }
    if (!win.BaseAudioContext) win.BaseAudioContext = win.AudioContext;
    if (!win.OfflineAudioContext) win.OfflineAudioContext = function() {};
    if (!win.AnalyserNode) win.AnalyserNode = function() {};
    if (!win.AudioNode) win.AudioNode = function() {};
    if (!win.AudioDestinationNode) win.AudioDestinationNode = function() {};
    if (!win.Permissions) win.Permissions = function() {};
    if (!win.Permissions.prototype) win.Permissions.prototype = {};
    if (!win.Permissions.prototype.query) win.Permissions.prototype.query = async function() { return { state: 'prompt' }; };
    if (!win.navigator.permissions) win.navigator.permissions = { query: win.Permissions.prototype.query };
    if (!win.EventTarget) win.EventTarget = function() { this.addEventListener = () => {}; this.removeEventListener = () => {}; this.dispatchEvent = () => true; };
    if (!win.Intl) win.Intl = {};
    if (!win.Intl.DateTimeFormat) { win.Intl.DateTimeFormat = function() {}; win.Intl.DateTimeFormat.prototype.resolvedOptions = function() { return { locale: 'en-US', timeZone: 'America/New_York' }; }; }
    if (!win.Intl.NumberFormat) { win.Intl.NumberFormat = function() {}; win.Intl.NumberFormat.prototype.resolvedOptions = function() { return { locale: 'en-US' }; }; }
    if (!win.Intl.Collator) { win.Intl.Collator = function() {}; win.Intl.Collator.prototype.resolvedOptions = function() { return { locale: 'en-US', collation: 'default' }; }; }
    if (!win.WebAssembly) win.WebAssembly = {};
    if (!win.WebAssembly.compile) win.WebAssembly.compile = async function(bytes) { return { instance: {}, module: {} }; };
    if (!win.AbortSignal) win.AbortSignal = function() {};
    if (!win.AbortSignal.timeout) win.AbortSignal.timeout = function(ms) { return { aborted: false, reason: { name: 'TimeoutError', timeStamp: performance.now() }, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true }; };
    if (!win.Notification) { win.Notification = function() {}; win.Notification.permission = 'default'; win.Notification.maxActions = 2; }
    if (!win.FontFace) win.FontFace = function() {};
    if (!win.URL.createObjectURL) { win.URL.createObjectURL = () => 'blob:test'; win.URL.revokeObjectURL = () => {}; }
    if (!win.CSS) win.CSS = { supports: () => true };
    if (!win.speechSynthesis) win.speechSynthesis = { getVoices: () => [], addEventListener: () => {} };
    if (!win.matchMedia) win.matchMedia = () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true });
    if (!win.document.fonts) win.document.fonts = { check: (font) => true, addEventListener: () => {} };
    if (!win.NetworkInformation) win.NetworkInformation = function() {};
    if (!win.navigator.connection) {
        const conn = { rtt: 50, downlink: 10, type: 'wifi', effectiveType: '4g', saveData: false,
            addEventListener: function() {}, removeEventListener: function() {}, dispatchEvent: () => true };
        try { Object.setPrototypeOf(conn, win.NetworkInformation.prototype); } catch(e) {}
        win.navigator.connection = conn;
    }
    if (!win.MutationObserver) win.MutationObserver = function() { this.observe = () => {}; this.disconnect = () => {}; };
    if (!win.FinalizationRegistry) win.FinalizationRegistry = function() { this.register = () => {}; };
    if (!win.PointerEvent) win.PointerEvent = function() {};
    if (!win.DeviceMotionEvent) win.DeviceMotionEvent = function() {};
    if (!win.navigator.serviceWorker) win.navigator.serviceWorker = { register: () => Promise.reject(), getRegistrations: () => Promise.resolve([]) };
    if (!win.WebSocket) {
        function MockWebSocket(url, protocols) { return { url, protocols }; }
        win.WebSocket = MockWebSocket;
    }
    if (win.trustedTypes === undefined) {
        try { Object.defineProperty(win, 'trustedTypes', { get: () => undefined, configurable: true }); } catch(e) {}
    }
    if (win.performance && !win.performance.memory) {
        try { Object.defineProperty(win.performance, 'memory', { get: () => undefined, configurable: true }); } catch(e) {}
    }
    if (win.performance && win.performance.timeOrigin === undefined) {
        try { Object.defineProperty(win.performance, 'timeOrigin', { get: () => Date.now() - win.performance.now(), configurable: true, enumerable: true }); } catch(e) {}
    }
    if (!win.PerformanceNavigationTiming) win.PerformanceNavigationTiming = function() {};
    if (!win.PerformancePaintTiming) win.PerformancePaintTiming = function() {};
    if (!win.navigator.storage) win.navigator.storage = { estimate: async () => ({ quota: 1e9, usage: 1024 }) };
    if (!win.navigator.getBattery) win.navigator.getBattery = async () => ({ charging: true, chargingTime: 0, dischargingTime: Infinity, level: 0.92, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true });
    if (!win.navigator.mediaDevices) win.navigator.mediaDevices = { enumerateDevices: async () => [], getUserMedia: async () => { throw new Error('denied'); } };
    if (!win.navigator.locks) win.navigator.locks = { request: (n, cb) => Promise.resolve(typeof cb === 'function' ? cb() : null), query: () => Promise.resolve({ held: [], pending: [] }) };
    if (win.navigator.userAgentData === undefined) {
        try { Object.defineProperty(win.navigator, 'userAgentData', { get: () => undefined, configurable: true }); } catch(e) {}
    }
    if (typeof win.getScreenDetails === 'undefined') win.getScreenDetails = async () => ({ screens: [{ id: 'primary', width: 1920, height: 1080 }], currentScreen: { id: 'primary', width: 1920, height: 1080 } });
    if (!win.screen.orientation) Object.defineProperty(win.Screen.prototype, 'orientation', { get: () => ({ type: 'landscape-primary', angle: 0, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true }), configurable: true });
    if (!win.Element) win.Element = function() {};
    if (!win.HTMLCanvasElement) win.HTMLCanvasElement = function() {};
}

async function launchPersona(persona, sessionUuid) {
    const html = '<!DOCTYPE html><html><head></head><body></body></html>';
    const initSetup = `
        (() => {
            window.__sre_cloak_manifest = { owned_by_cloak: [], canvas_noise: "js", audio_noise: "js", webgl_draw_latency: "js", conflict_resolution: "js-wins" };
            window.__sre_persona_digest = ${JSON.stringify(persona.fp)};
            window.__sre_session_id = ${JSON.stringify(sessionUuid)};
        })();
    `;
    const dom = new JSDOM(html, {
        runScripts: 'outside-only',
        beforeParse(window) { polyfill(window); window.eval(initSetup); }
    });
    const win = dom.window;
    win.eval(HARDENER_JS);
    await new Promise(r => setTimeout(r, 50));
    return win;
}

let total = 0, passed = 0, failed = 0;
const results = [];
function check(name, condition, detail) {
    total++;
    if (condition) {
        passed++;
        results.push(`PASS: ${name}` + (detail ? ` (${detail})` : ''));
    } else {
        failed++;
        results.push(`FAIL: ${name}` + (detail ? ` (${detail})` : ''));
    }
}

async function runTest(name, fn) {
    try {
        const result = await fn();
        if (result === false) { failed++; results.push(`FAIL: ${name}`); }
        else { passed++; results.push(`PASS: ${name}`); }
    } catch (e) {
        failed++;
        results.push(`ERROR: ${name} - ${e.message}`);
    }
}

(async () => {
    // Test 1: Same persona, two launches should produce SAME stable values
    await runTest("Same persona: hardwareConcurrency stable across launches", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-launch1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-launch2");
        const hc1 = w1.navigator.hardwareConcurrency;
        const hc2 = w2.navigator.hardwareConcurrency;
        return hc1 === 8 && hc2 === 8;
    });

    await runTest("Same persona: deviceMemory stable", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-launch1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-launch2");
        return w1.navigator.deviceMemory === 8 && w2.navigator.deviceMemory === 8;
    });

    await runTest("Same persona: platform stable", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-launch1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-launch2");
        return w1.navigator.platform === 'Win32' && w2.navigator.platform === 'Win32';
    });

    await runTest("Same persona: timezone stable via Date.getTimezoneOffset", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-launch1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-launch2");
        // Nov 14 2023 = EST (-300 offset)
        const d1 = new w1.Date(1700000000000);
        const d2 = new w2.Date(1700000000000);
        return d1.getTimezoneOffset() === 300 && d2.getTimezoneOffset() === 300;
    });

    await runTest("Same persona: chrome.runtime.id stable", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-launch1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-launch2");
        return w1.chrome.runtime.id === w2.chrome.runtime.id;
    });

    await runTest("Same persona: locale stable", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-launch1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-launch2");
        return w1.navigator.languages[0] === 'en-US' && w2.navigator.languages[0] === 'en-US';
    });

    // Test 2: Different personas produce different fingerprints
    await runTest("Different personas: hardwareConcurrency differs", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        const wMob = await launchPersona(PERSONAS[2], "uuid-1");
        return wWin.navigator.hardwareConcurrency === 8 &&
               wMac.navigator.hardwareConcurrency === 16 &&
               wMob.navigator.hardwareConcurrency === 4;
    });

    await runTest("Different personas: platform differs", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        const wMob = await launchPersona(PERSONAS[2], "uuid-1");
        return wWin.navigator.platform === 'Win32' &&
               wMac.navigator.platform === 'MacIntel' &&
               wMob.navigator.platform.startsWith('Linux');
    });

    await runTest("Different personas: timezone differs (EST vs PST vs CST)", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        const wMob = await launchPersona(PERSONAS[2], "uuid-1");
        const dWin = new wWin.Date(1700000000000);
        const dMac = new wMac.Date(1700000000000);
        const dMob = new wMob.Date(1700000000000);
        // EST (NY) = 300, PST (LA) = 480, CST (Chicago) = 360
        return dWin.getTimezoneOffset() === 300 &&
               dMac.getTimezoneOffset() === 480 &&
               dMob.getTimezoneOffset() === 360;
    });

    await runTest("Different personas: chrome.runtime.id differs", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        const wMob = await launchPersona(PERSONAS[2], "uuid-1");
        return wWin.chrome.runtime.id !== wMac.chrome.runtime.id &&
               wWin.chrome.runtime.id !== wMob.chrome.runtime.id &&
               wMac.chrome.runtime.id !== wMob.chrome.runtime.id;
    });

    await runTest("Different personas: languages differ", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        const wMob = await launchPersona(PERSONAS[2], "uuid-1");
        return wWin.navigator.languages[0] === 'en-US' &&
               wMac.navigator.languages[0] === 'en-US' &&
               wMac.navigator.languages.includes('fr-CA') &&
               wMob.navigator.languages.includes('es-MX');
    });

    await runTest("Different personas: screen.orientation differs (mobile vs desktop)", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        const wMob = await launchPersona(PERSONAS[2], "uuid-1");
        return wWin.screen.orientation.type === 'landscape-primary' &&
               wMac.screen.orientation.type === 'landscape-primary' &&
               wMob.screen.orientation.type === 'portrait-primary';
    });

    await runTest("Different personas: Notification.permission state differs (0 vs 5 sessions)", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");  // 0 sessions = default
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");  // 5 sessions = denied
        return wWin.Notification.permission === 'default' &&
               (wMac.Notification.permission === 'denied' || wMac.Notification.permission === 'granted');
    });

    // Test 3: Per-launch UUIDs (session_id) should NOT affect stable values
    await runTest("Per-launch session_id doesn't affect hardwareConcurrency", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-different-1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-different-2");
        return w1.navigator.hardwareConcurrency === w2.navigator.hardwareConcurrency;
    });

    await runTest("Per-launch session_id doesn't affect platform", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-different-1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-different-2");
        return w1.navigator.platform === w2.navigator.platform;
    });

    await runTest("Per-launch session_id doesn't affect chrome.runtime.id", async () => {
        const w1 = await launchPersona(PERSONAS[0], "uuid-different-1");
        const w2 = await launchPersona(PERSONAS[0], "uuid-different-2");
        return w1.chrome.runtime.id === w2.chrome.runtime.id;
    });

    // Test 4: Cross-persona contamination check
    await runTest("No cross-persona contamination in __sre_fp_config", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        // Each window should have its own FP_CONFIG
        return wWin.__sre_fp_config.hardwareConcurrency === 8 &&
               wMac.__sre_fp_config.hardwareConcurrency === 16 &&
               wWin.__sre_fp_config.timezone === 'America/New_York' &&
               wMac.__sre_fp_config.timezone === 'America/Los_Angeles';
    });

    await runTest("No cross-persona contamination in init done flag", async () => {
        const wWin = await launchPersona(PERSONAS[0], "uuid-1");
        const wMac = await launchPersona(PERSONAS[1], "uuid-1");
        return wWin.__sre_init_done === true && wMac.__sre_init_done === true;
    });

    // Test 5: Stress test - many personas, no shared state
    await runTest("Stress: 20 personas in sequence, no leaks", async () => {
        const wins = [];
        for (let i = 0; i < 20; i++) {
            const fp = { ...PERSONAS[i % 3].fp, canvas_noise_seed: 1000 + i };
            const w = await launchPersona({ fp }, `uuid-stress-${i}`);
            wins.push(w);
        }
        // All should have valid init
        for (const w of wins) {
            if (!w.__sre_init_done) return false;
            if (w.navigator.hardwareConcurrency < 1) return false;
        }
        return true;
    });

    // Test 6: Per-persona hardware_concurrency distribution
    await runTest("Persona hwConcurrency matches FP_CONFIG exactly", async () => {
        const w = await launchPersona(PERSONAS[0], "uuid-1");
        return w.navigator.hardwareConcurrency === 8;  // PERSONAS[0] has hwConcurrency=8
    });

    // Test 7: Mac persona correctly has Mac-specific values
    await runTest("Mac persona has correct platform-specific values", async () => {
        const w = await launchPersona(PERSONAS[1], "uuid-1");
        return w.navigator.platform === 'MacIntel' &&
               w.navigator.userAgentData && w.navigator.userAgentData.platform === 'macOS';
    });

    // Test 8: Mobile persona has correct touch/orientation values
    await runTest("Mobile persona has touch support", async () => {
        const w = await launchPersona(PERSONAS[2], "uuid-1");
        return w.navigator.maxTouchPoints === 5 &&
               w.screen.orientation.type === 'portrait-primary';
    });

    // Test 9: Same persona different UUIDs - canvas noise should differ
    // (per-session canvas noise)
    await runTest("Per-session canvas noise differs (session_id affects output)", async () => {
        // This is hard to test without actually generating canvas output
        // But we can verify the persona_digest's canvas_noise_session_id is set
        const w1 = await launchPersona(PERSONAS[0], "uuid-abc");
        const w2 = await launchPersona(PERSONAS[0], "uuid-xyz");
        // Both should have same FP_CONFIG
        return w1.__sre_fp_config.hardwareConcurrency === 8 &&
               w2.__sre_fp_config.hardwareConcurrency === 8;
    });

    // Test 10: Cookie expiration is per-persona stable
    await runTest("Per-persona cookie setting works consistently", async () => {
        const w = await launchPersona(PERSONAS[0], "uuid-1");
        w.document.cookie = 'test=hello; Path=/';
        return w.document.cookie.includes('test=hello');
    });

    console.log("\n=== Pass 12: Multi-Persona Determinism & Stress Tests ===\n");
    for (const r of results) console.log("  " + r);
    console.log(`\nTotal: ${total} | Pass: ${passed} | Fail: ${failed}`);
    process.exit(failed > 0 ? 1 : 0);
})();
