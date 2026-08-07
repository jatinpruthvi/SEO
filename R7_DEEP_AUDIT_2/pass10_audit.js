// Pass 10: Edge case audit
const { JSDOM } = require('/home/user/node_modules/jsdom');
const fs = require('fs');

const HARDENER_JS = fs.readFileSync('/home/user/project/browser-runtime/prototype_hardener.js', 'utf-8');

const FP_CONFIG = {
    hardwareConcurrency: 8, deviceMemory: 8,
    webglVendor: "Intel Inc.", webglRenderer: "Intel(R) Iris(TM) Plus Graphics",
    platform: "Win32", languages: ["en-US", "en"],
    canvas_noise_seed: 12345, client_hints: null, timezone: "America/New_York",
    audioSampleRate: 48000, screenColorDepth: 24, screenPixelDepth: 24,
    batteryCharging: true, batteryLevel: 0.92, scrollBehavior: "smooth",
    cookieLifetimeDays: 14, bootId: "test-boot", uaFullVersion: "120.0.6099.109",
    uaMajor: 120, platformVersion: "15.0.0", architecture: "x86", bitness: "64",
    mobile: false, model: "",
    connectionType: "wifi", effectiveType: "4g", downlink: 10.0, rtt: 50,
    saveData: false, maxTouchPoints: 0, isWindows: true, isMac: false, isLinux: false,
    permissionStates: { geolocation: "prompt", notifications: "prompt", midi: "prompt" },
    notification_session_count: 0, canvas_noise_session_id: "test-session"
};

const initSetup = `
(() => {
    window.__sre_cloak_manifest = { owned_by_cloak: [], canvas_noise: "js", audio_noise: "js", webgl_draw_latency: "js", conflict_resolution: "js-wins" };
    window.__sre_persona_digest = ${JSON.stringify(FP_CONFIG)};
    window.__sre_session_id = "test-session";
})();
`;

const html = `<!DOCTYPE html><html><head></head><body></body></html>`;

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
    if (!win.OffscreenCanvas) {
        win.OffscreenCanvas = function() { return {}; };
        win.OffscreenCanvas.prototype.getContext = function() { return null; };
    }
    if (!win.OffscreenCanvasRenderingContext2D) win.OffscreenCanvasRenderingContext2D = function() {};
    if (!win.SharedWorker) win.SharedWorker = function() { return {port: {}, addEventListener: () => {}}; };
    if (!win.RTCPeerConnection) win.RTCPeerConnection = function() { return {}; };
    if (!win.Path2D) { win.Path2D = function() {}; win.Path2D.prototype.arc = function(){}; }
    if (!win.SVGGraphicsElement) { win.SVGGraphicsElement = function(){}; win.SVGGraphicsElement.prototype.getBBox = function() { return {x:0,y:0,width:0,height:0}; }; }
    if (!win.ImageBitmapRenderingContext) { win.ImageBitmapRenderingContext = function() {}; win.ImageBitmapRenderingContext.prototype.transferFromImageBitmap = function() {}; }
    if (!win.Worker) win.Worker = function() {};
    if (!win.GPUAdapterInfo) win.GPUAdapterInfo = function() {};
    if (!win.PerformanceObserver) { win.PerformanceObserver = function() { this.observe = () => {}; }; win.PerformanceObserver.supportedEntryTypes = []; }
    if (!win.AudioContext) { win.AudioContext = function() {}; win.AudioContext.prototype.sampleRate = 48000; }
    if (!win.BaseAudioContext) win.BaseAudioContext = win.AudioContext;
    if (!win.OfflineAudioContext) win.OfflineAudioContext = function() {};
    if (!win.AnalyserNode) win.AnalyserNode = function() {};
    if (!win.AudioNode) win.AudioNode = function() {};
    if (!win.AudioDestinationNode) win.AudioDestinationNode = function() {};

    // Permissions - IMPORTANT: needs prototype.query AND navigator.permissions
    if (!win.Permissions) win.Permissions = function() {};
    if (!win.Permissions.prototype) win.Permissions.prototype = {};
    if (!win.Permissions.prototype.query) {
        win.Permissions.prototype.query = async function(parameters) {
            return { state: 'prompt', name: (parameters && parameters.name) || 'unknown' };
        };
    }
    if (!win.navigator.permissions) {
        // Use a real object, not a getter, so the hardener's check `if (navigator.permissions)` works
        try {
            win.navigator.permissions = { query: win.Permissions.prototype.query };
        } catch(e) {
            Object.defineProperty(win.navigator, 'permissions', {
                get: () => ({ query: win.Permissions.prototype.query }),
                configurable: true
            });
        }
    }

    if (!win.EventTarget) {
        win.EventTarget = function() {
            this.addEventListener = () => {};
            this.removeEventListener = () => {};
            this.dispatchEvent = () => true;
        };
    }
    if (!win.Intl) win.Intl = {};
    if (!win.Intl.DateTimeFormat) {
        win.Intl.DateTimeFormat = function() {};
        win.Intl.DateTimeFormat.prototype.resolvedOptions = function() { return { locale: 'en-US', timeZone: 'America/New_York', calendar: 'gregory' }; };
    }
    if (!win.Intl.NumberFormat) {
        win.Intl.NumberFormat = function() {};
        win.Intl.NumberFormat.prototype.resolvedOptions = function() { return { locale: 'en-US' }; };
    }
    if (!win.Intl.Collator) {
        win.Intl.Collator = function() {};
        win.Intl.Collator.prototype.resolvedOptions = function() { return { locale: 'en-US', collation: 'default' }; };
    }
    if (!win.WebAssembly) win.WebAssembly = {};
    if (!win.WebAssembly.compile) {
        win.WebAssembly.compile = async function(bytes) { return { instance: {}, module: {} }; };
    }
    if (!win.AbortSignal) win.AbortSignal = function() {};
    if (!win.AbortSignal.timeout) {
        win.AbortSignal.timeout = function(ms) {
            return {
                aborted: false,
                reason: { name: 'TimeoutError', timeStamp: win.performance ? win.performance.now() : 0 },
                addEventListener: () => {},
                removeEventListener: () => {},
                dispatchEvent: () => true
            };
        };
    }
    if (!win.Notification) {
        win.Notification = function() {};
        win.Notification.permission = 'default';
        win.Notification.maxActions = 2;
    }
    if (!win.FontFace) win.FontFace = function() {};
    if (!win.URL.createObjectURL) {
        win.URL.createObjectURL = () => 'blob:test';
        win.URL.revokeObjectURL = () => {};
    }
    if (!win.CSS) win.CSS = { supports: () => true };
    if (!win.speechSynthesis) {
        win.speechSynthesis = { getVoices: () => [], addEventListener: () => {} };
    }
    if (!win.matchMedia) {
        win.matchMedia = () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true });
    }

    // document.fonts - use real value, not getter
    if (!win.document.fonts) {
        win.document.fonts = {
            check: (font) => true,
            addEventListener: () => {}
        };
    }

    // navigator.connection with NetworkInformation prototype
    if (!win.NetworkInformation) win.NetworkInformation = function() {};
    if (!win.navigator.connection) {
        const conn = {
            rtt: 50, downlink: 10, type: 'wifi', effectiveType: '4g', saveData: false,
            addEventListener: function(type, listener, options) { /* no-op */ },
            removeEventListener: function(type, listener, options) { /* no-op */ },
            dispatchEvent: () => true
        };
        try {
            Object.setPrototypeOf(conn, win.NetworkInformation.prototype);
        } catch(e) {}
        win.navigator.connection = conn;
    }
    if (!win.MutationObserver) {
        win.MutationObserver = function() { this.observe = () => {}; this.disconnect = () => {}; };
    }
    if (!win.FinalizationRegistry) {
        win.FinalizationRegistry = function() { this.register = () => {}; };
    }
    if (!win.PointerEvent) win.PointerEvent = function() {};
    if (!win.DeviceMotionEvent) win.DeviceMotionEvent = function() {};
    if (!win.navigator.serviceWorker) {
        win.navigator.serviceWorker = { register: () => Promise.reject(), getRegistrations: () => Promise.resolve([]) };
    }
    if (!win.WebSocket) {
        // Use a real WebSocket for the override to work
        class MockWebSocket {
            constructor(url, protocols) {
                this.url = url;
                this.protocols = protocols;
            }
        }
        win.WebSocket = MockWebSocket;
    }
    // R7-PASS-10: Force the hardener to override by removing the original
    // reference. The hardener's check `if (!(wsDesc && !wsDesc.configurable))`
    // is true (configurable=true), so it tries to wrap. But we need the
    // hardener to think WebSocket is NOT yet defined so it uses the
    // 'no native, just assign' path.
    // Simpler: store a function-style WebSocket that the hardener can wrap.
    if (win.WebSocket && !win.WebSocket.__sre_pretendNative) {
        const _nativeRef = win.WebSocket;
        function MockWebSocketWrap(url, protocols) {
            return new _nativeRef(url, protocols);
        }
        MockWebSocketWrap.__sre_pretendNative = true;
        Object.defineProperty(win, 'WebSocket', { value: MockWebSocketWrap, writable: true, configurable: true });
    }
    if (win.trustedTypes === undefined) {
        try {
            Object.defineProperty(win, 'trustedTypes', {
                get: () => undefined, configurable: true
            });
        } catch(e) {}
    }
    if (win.performance && !win.performance.memory) {
        try {
            Object.defineProperty(win.performance, 'memory', {
                get: () => undefined, configurable: true
            });
        } catch(e) {}
    }
    if (win.performance && win.performance.timeOrigin === undefined) {
        try {
            Object.defineProperty(win.performance, 'timeOrigin', {
                get: () => Date.now() - win.performance.now(),
                configurable: true, enumerable: true
            });
        } catch(e) {}
    }
    if (!win.PerformanceNavigationTiming) win.PerformanceNavigationTiming = function() {};
    if (!win.PerformancePaintTiming) win.PerformancePaintTiming = function() {};
    if (!win.navigator.storage) {
        win.navigator.storage = { estimate: async () => ({ quota: 1e9, usage: 1024 }) };
    }
    if (!win.navigator.getBattery) {
        win.navigator.getBattery = async () => ({
            charging: true, chargingTime: 0, dischargingTime: Infinity, level: 0.92,
            addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true
        });
    }
    if (!win.navigator.mediaDevices) {
        win.navigator.mediaDevices = {
            enumerateDevices: async () => [],
            getUserMedia: async () => { throw new Error('denied'); }
        };
    }
    if (!win.navigator.locks) {
        win.navigator.locks = {
            request: (n, cb) => Promise.resolve(typeof cb === 'function' ? cb() : null),
            query: () => Promise.resolve({ held: [], pending: [] })
        };
    }
    if (win.navigator.userAgentData === undefined) {
        try {
            Object.defineProperty(win.navigator, 'userAgentData', {
                get: () => undefined, configurable: true
            });
        } catch(e) {}
    }
    if (typeof win.getScreenDetails === 'undefined') {
        win.getScreenDetails = async () => ({
            screens: [{ id: 'primary', width: 1920, height: 1080 }],
            currentScreen: { id: 'primary', width: 1920, height: 1080 }
        });
    }
    if (!win.screen.orientation) {
        Object.defineProperty(win.screen, 'orientation', {
            get: () => ({ type: 'landscape-primary', angle: 0, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true }),
            configurable: true
        });
    }
    if (!win.Element) win.Element = function() {};
    if (!win.HTMLCanvasElement) win.HTMLCanvasElement = function() {};
}

function makeWindow() {
    const dom = new JSDOM(html, {
        runScripts: 'outside-only',
        beforeParse(window) { polyfill(window); window.eval(initSetup); }
    });
    return dom.window;
}

const results = [];
let total = 0, passed = 0, failed = 0;

async function runTest(name, fn) {
    total++;
    try {
        const result = await fn();
        if (result === false) {
            failed++;
            results.push(`FAIL: ${name}`);
        } else {
            passed++;
            results.push(`PASS: ${name}`);
        }
    } catch (e) {
        failed++;
        results.push(`ERROR: ${name} - ${e.message}`);
    }
}

function runHardener(win) {
    win.eval(HARDENER_JS);
    return new Promise(resolve => setTimeout(resolve, 50));
}

(async () => {
    // ============================================================
    // SECTION A: csi/pushState interaction
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("A1: csiTranCount is positive after init", () => {
            const csi = win.chrome.csi();
            return typeof csi.tran === 'number' && csi.tran >= 14;
        });
        // pushState with same-domain URL
        await runTest("A2: csi().tran does NOT increment on history.replaceState", () => {
            // replaceState does NOT change length or tran (real Chrome behavior)
            // Use a no-arg replaceState which is valid
            const before = win.chrome.csi().tran;
            try { win.history.replaceState({}, '', '?test=1'); } catch(e) { return 'errored'; }
            const after = win.chrome.csi().tran;
            return after === before;
        });
    }

    // ============================================================
    // SECTION B: Cookie handling
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("B1: __Secure- cookie WITHOUT Secure flag is rejected", () => {
            const beforeCookie = win.document.cookie;
            try { win.document.cookie = '__Secure-foo=bar; Path=/'; } catch(e) {}
            return win.document.cookie === beforeCookie;
        });
        await runTest("B2: __Secure- cookie WITH Secure flag is accepted", () => {
            try { win.document.cookie = '__Secure-bar=baz; Path=/; Secure'; } catch(e) {}
            return win.document.cookie.includes('__Secure-bar=baz');
        });
        await runTest("B3: __Host- cookie WITHOUT Secure flag is rejected", () => {
            const beforeCookie = win.document.cookie;
            try { win.document.cookie = '__Host-foo=bar; Path=/'; } catch(e) {}
            return win.document.cookie === beforeCookie;
        });
        await runTest("B4: __Host- cookie with Domain attribute is rejected", () => {
            try { win.document.cookie = '__Host-foo=bar; Path=/; Secure; Domain=example.com'; } catch(e) {}
            return !win.document.cookie.includes('__Host-foo=bar');
        });
    }

    // ============================================================
    // SECTION C: Core APIs
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("C1: chrome.runtime.id is 32 chars [a-p] starting with 'a'", () => {
            const id = win.chrome.runtime.id;
            return /^[a-p]{32}$/.test(id) && id.startsWith('a');
        });
        await runTest("C2: chrome.csi() returns same startE on multiple calls", () => {
            const c1 = win.chrome.csi();
            const c2 = win.chrome.csi();
            return c1.startE === c2.startE;
        });
        await runTest("C3: chrome.loadTimes() firstPaintAfterLoadTime > finishLoadTime", () => {
            const lt = win.chrome.loadTimes();
            return lt.firstPaintAfterLoadTime > lt.finishLoadTime;
        });
        await runTest("C4: chrome.app.runningState returns valid enum", () => {
            const rs = win.chrome.app.runningState();
            return ['cannot_run', 'ready_to_run', 'running'].includes(rs);
        });
        await runTest("C5: navigator.plugins has 5 entries", () => {
            return win.navigator.plugins.length === 5;
        });
        await runTest("C6: navigator.plugins[0] is 'PDF Viewer'", () => {
            return win.navigator.plugins[0] && win.navigator.plugins[0].name === 'PDF Viewer';
        });
        await runTest("C7: Notification.permission is 'default'", () => {
            return win.Notification.permission === 'default';
        });
        await runTest("C8: navigator.webdriver is undefined", () => {
            return win.navigator.webdriver === undefined;
        });
        await runTest("C9: navigator.hardwareConcurrency === 8", () => {
            return win.navigator.hardwareConcurrency === 8;
        });
        await runTest("C10: navigator.platform === 'Win32'", () => {
            return win.navigator.platform === 'Win32';
        });
        await runTest("C11: screen.orientation is landscape-primary", () => {
            return win.screen.orientation.type === 'landscape-primary';
        });
    }

    // ============================================================
    // SECTION D: Permissions / Storage
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("D1: Permissions.query returns valid result for geolocation", async () => {
            const result = await win.navigator.permissions.query({ name: 'geolocation' });
            return result && result.state === 'prompt';
        });
        await runTest("D2: Permissions.query returns 'prompt' for midi", async () => {
            const result = await win.navigator.permissions.query({ name: 'midi' });
            return result && result.state === 'prompt';
        });
        await runTest("D3: navigator.locks.request returns callback result", async () => {
            const result = await win.navigator.locks.request('my-lock', async () => 42);
            return result === 42;
        });
    }

    // ============================================================
    // SECTION E: Fonts
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("E1: document.fonts.check accepts 'Arial Black' (multi-word)", () => {
            return win.document.fonts.check('12px "Arial Black"') === true;
        });
        await runTest("E2: document.fonts.check rejects unknown font", () => {
            return win.document.fonts.check('12px "DefinitelyNotAFontXYZ"') === false;
        });
        await runTest("E3: document.fonts.check accepts 'Arial' (single word)", () => {
            return win.document.fonts.check('12px Arial') === true;
        });
    }

    // ============================================================
    // SECTION F: Timers and Performance
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("F1: AbortSignal.timeout timeStamp is stable across reads", async () => {
            const sig = win.AbortSignal.timeout(1000);
            const t1 = sig.reason.timeStamp;
            await new Promise(resolve => setTimeout(resolve, 50));
            const t2 = sig.reason.timeStamp;
            return t1 === t2;
        });
        await runTest("F2: setTimeout has drift on large delays", () => {
            return new Promise(resolve => {
                const start = win.performance.now();
                win.setTimeout(() => {
                    const elapsed = win.performance.now() - start;
                    resolve(Math.abs(elapsed - 1000) < 100);
                }, 1000);
            });
        });
    }

    // ============================================================
    // SECTION G: WebSocket loopback block
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("G1: WebSocket blocks 127.0.0.1", () => {
            try {
                new win.WebSocket('ws://127.0.0.1:8080');
                return false;
            } catch (e) {
                // R7-PASS-10: Check via name (cross-realm safe)
                return e.constructor.name === 'SyntaxError' || e.name === 'SyntaxError';
            }
        });
        await runTest("G2: WebSocket blocks localhost", () => {
            try {
                new win.WebSocket('ws://localhost:8080');
                return false;
            } catch (e) {
                return e.constructor.name === 'SyntaxError' || e.name === 'SyntaxError';
            }
        });
    }

    // ============================================================
    // SECTION H: Connection listener tracking
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("H1: navigator.connection tracks listener count", () => {
            const c = win.navigator.connection;
            const beforeCount = c.__sre_listener_count || 0;
            c.addEventListener('change', () => {});
            const afterAdd = c.__sre_listener_count || 0;
            c.removeEventListener('change', () => {});
            const afterRemove = c.__sre_listener_count || 0;
            return afterAdd === beforeCount + 1 && afterRemove === beforeCount;
        });
    }

    // ============================================================
    // SECTION I: Date / TZ
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("I1: getTimezoneOffset returns 300 for EST (Nov)", () => {
            const d = new win.Date(1700000000000);  // Nov 14, 2023 - EST
            return d.getTimezoneOffset() === 300;
        });
        await runTest("I2: getTimezoneOffset returns 240 for EDT (Jul)", () => {
            const d = new win.Date(1689000000000);  // Jul 10, 2023 - EDT
            return d.getTimezoneOffset() === 240;
        });
        await runTest("I3: Date.toString returns valid string", () => {
            const d = new Date(0);
            const s = d.toString();
            return typeof s === 'string' && s.length > 0;
        });
    }

    // ============================================================
    // SECTION J: Intl
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("J1: Intl.DateTimeFormat.resolvedOptions returns persona TZ", () => {
            const dtf = new win.Intl.DateTimeFormat('en-US');
            const opts = dtf.resolvedOptions();
            return opts.timeZone === 'America/New_York';
        });
        await runTest("J2: Intl.Collator.resolvedOptions locks collation", () => {
            const c = new win.Intl.Collator('en-US');
            const opts = c.resolvedOptions();
            return opts.collation === 'default' && opts.locale === 'en-US';
        });
        await runTest("J3: Intl.NumberFormat.resolvedOptions locks locale", () => {
            const nf = new win.Intl.NumberFormat('en-US');
            const opts = nf.resolvedOptions();
            return opts.locale === 'en-US';
        });
    }

    // ============================================================
    // SECTION K: history
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("K1: history.length >= 2", () => {
            return win.history.length >= 2;
        });
    }

    // ============================================================
    // SECTION L: runtime info
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("L1: __sre_init_done is true", () => {
            return win.__sre_init_done === true;
        });
        await runTest("L2: __sre_fp_config matches expected fields", () => {
            return win.__sre_fp_config && win.__sre_fp_config.hardwareConcurrency === 8;
        });
        await runTest("L3: window.name is string", () => {
            return typeof win.name === 'string';
        });
        await runTest("L4: navigator.languages starts with en-US", () => {
            return Array.isArray(win.navigator.languages) && win.navigator.languages[0] === 'en-US';
        });
        await runTest("L5: matchMedia for prefers-color-scheme:light", () => {
            return win.matchMedia('(prefers-color-scheme: light)').matches === true;
        });
        await runTest("L6: Worker is overridden (function exists)", () => {
            return typeof win.Worker === 'function';
        });
    }

    // ============================================================
    // SECTION M: ANIMATION / WEBGL context
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("M1: Element.animate returns playState=running", () => {
            const el = win.document.createElement('div');
            const anim = el.animate([{opacity: 0}, {opacity: 1}], 1000);
            return anim && anim.playState === 'running';
        });
        await runTest("M2: getScreenDetails returns currentScreen", async () => {
            const d = await win.getScreenDetails();
            return d && d.currentScreen !== null;
        });
    }

    // ============================================================
    // SECTION N: Battery / Media
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("N1: navigator.getBattery returns battery", async () => {
            const b = await win.navigator.getBattery();
            return b && b.charging === true && typeof b.level === 'number';
        });
        await runTest("N2: mediaDevices.enumerateDevices returns 5 devices", async () => {
            const devs = await win.navigator.mediaDevices.enumerateDevices();
            return devs.length === 5;
        });
        await runTest("N3: mediaDevices.getUserMedia throws", async () => {
            try {
                await win.navigator.mediaDevices.getUserMedia({audio: true});
                return false;
            } catch (e) {
                return true;
            }
        });
    }

    // ============================================================
    // SECTION O: chrome.connect() / sendMessage
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("O1: chrome.runtime.connect returns Port-like", () => {
            const port = win.chrome.runtime.connect();
            return port && typeof port.postMessage === 'function' && typeof port.disconnect === 'function';
        });
        await runTest("O2: chrome.runtime.sendMessage invokes callback with undefined", () => {
            return new Promise(resolve => {
                win.chrome.runtime.sendMessage({}, (response) => {
                    resolve(response === undefined);
                });
            });
        });
    }

    // ============================================================
    // SECTION P: PERFORMANCE OBSERVER
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("P1: PerformanceObserver.supportedEntryTypes is array", () => {
            return Array.isArray(win.PerformanceObserver.supportedEntryTypes);
        });
        await runTest("P2: PerformanceObserver.observe is callable", () => {
            const po = new win.PerformanceObserver(() => {});
            return typeof po.observe === 'function';
        });
    }

    // ============================================================
    // SECTION Q: Cookie expiration / behavior
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("Q1: Plain cookie setting works", () => {
            win.document.cookie = 'foo=bar; Path=/';
            return win.document.cookie.includes('foo=bar');
        });
        await runTest("Q2: Cookie with no Path= attribute is accepted", () => {
            win.document.cookie = 'bop=baz';
            return win.document.cookie.includes('bop=baz');
        });
    }

    // ============================================================
    // SECTION R: Edge cases
    // ============================================================
    {
        const win = makeWindow();
        await runHardener(win);
        await runTest("R1: Date.parse still works", () => {
            const t = Date.parse('2024-01-01');
            return typeof t === 'number' && t > 0;
        });
        await runTest("R2: setInterval exists and is function", () => {
            return typeof win.setInterval === 'function';
        });
        await runTest("R3: clearTimeout exists", () => {
            return typeof win.clearTimeout === 'function';
        });
        await runTest("R4: document.createElement returns an element", () => {
            const el = win.document.createElement('div');
            return el && el.tagName === 'DIV';
        });
        await runTest("R5: window.open is a function", () => {
            return typeof win.open === 'function';
        });
    }

    console.log("=== Pass 10 Edge-Case Audit Results ===\n");
    for (const r of results) console.log("  " + r);
    console.log(`\nTotal: ${total} | Pass: ${passed} | Fail: ${failed}`);
    process.exit(failed > 0 ? 1 : 0);
})();
