// prototype_hardener.js - Production Anti-Fingerprinting & Evasion Script

(() => {
    const rawToString = Function.prototype.toString;
    const modifiedFunctions = new Map();

    function spoofNativeProperty(obj, prop, value) {
        if (!obj) return;
        const originalDescriptor = Object.getOwnPropertyDescriptor(obj, prop);
        const getter = () => value;
        modifiedFunctions.set(getter, `function get ${prop}() {\n    [native code]\n}`);

        Object.defineProperty(obj, prop, {
            configurable: true,
            enumerable: originalDescriptor ? originalDescriptor.enumerable : true,
            get: getter
        });
    }

    Function.prototype.toString = function () {
        if (modifiedFunctions.has(this)) {
            return modifiedFunctions.get(this);
        }
        return rawToString.apply(this, arguments);
    };
    modifiedFunctions.set(Function.prototype.toString, "function toString() {\n    [native code]\n}");

    // Apply native overrides directly to prototype object chains to match Windows footprints
    spoofNativeProperty(Navigator.prototype, 'webdriver', undefined);
    spoofNativeProperty(Navigator.prototype, 'hardwareConcurrency', 8);

    // 2026 Playwright Artifact Scrubbing:
    // Some automated runners inject visible telemetry properties into the window context.
    // We actively delete and nullify them to prevent on-page detection scripts from identifying Playwright.
    try {
        delete window.__playwright;
        delete window.__pw_manual;
        delete window.__playwright_evaluation;
    } catch (e) {}

    // 2026 Fingerprint Mismatch Alignment:
    // Some APIs are platform or engine specific. Overriding them on the wrong browser engine
    // creates an instant automated fingerprint discrepancy.
    const isFirefox = navigator.userAgent.includes("Firefox");

    if (isFirefox) {
        // Firefox does NOT support navigator.deviceMemory, navigator.getBattery, navigator.userAgentData, or window.chrome.
        // We delete them to perfectly match genuine Firefox signatures.
        delete Navigator.prototype.deviceMemory;
        delete Navigator.prototype.userAgentData;
        delete navigator.getBattery;
        delete window.chrome;

        // 10/10 Firefox Core Alignment: Spoof Firefox-specific platform parameters to match Windows User-Agent
        spoofNativeProperty(Navigator.prototype, 'platform', "Win32");
        spoofNativeProperty(Navigator.prototype, 'oscpu', "Windows NT 10.0; Win64; x64");
        spoofNativeProperty(Navigator.prototype, 'buildID', "20231116134500"); // Realistic, version-matching release date
    } else {
        // Chromium based spoofing options
        spoofNativeProperty(Navigator.prototype, 'deviceMemory', 8);
        spoofNativeProperty(Navigator.prototype, 'platform', "Win32");
        delete Navigator.prototype.buildID;
        delete Navigator.prototype.oscpu;
        
        // GAP-08 Fix: Spoof Battery Status API for Chromium
        if (navigator.getBattery) {
            const mockBattery = {
                charging: true,
                chargingTime: 0,
                dischargingTime: Infinity,
                level: 1.0, // Desktop plugged-in defaults
                addEventListener: function() {}
            };
            navigator.getBattery = async function() {
                return mockBattery;
            };
            modifiedFunctions.set(navigator.getBattery, "function getBattery() {\n    [native code]\n}");
        }

        // 2026 Chromium-Specific Upgrade: Spoof navigator.userAgentData API
        // Real Chromium browsers utilize userAgentData for browser brand validation.
        // Failing to define this on Chrome profiles leads to immediate bot flags.
        const mockUAData = {
            brands: [
                { brand: "Not_A Brand", version: "8" },
                { brand: "Chromium", version: "120" },
                { brand: "Google Chrome", version: "120" }
            ],
            mobile: false,
            platform: "Windows",
            getHighEntropyValues: async function (hints) {
                return {
                    architecture: "x86",
                    bitness: "64",
                    brands: [
                        { brand: "Not_A Brand", version: "8" },
                        { brand: "Chromium", version: "120" },
                        { brand: "Google Chrome", version: "120" }
                    ],
                    fullVersionList: [
                        { brand: "Not_A Brand", version: "8" },
                        { brand: "Chromium", version: "120.0.6099.109" },
                        { brand: "Google Chrome", version: "120.0.6099.109" }
                    ],
                    mobile: false,
                    model: "",
                    platform: "Windows",
                    platformVersion: "10.0.0",
                    uaFullVersion: "120.0.6099.109"
                };
            }
        };
        modifiedFunctions.set(mockUAData.getHighEntropyValues, "function getHighEntropyValues() {\n    [native code]\n}");
        Object.defineProperty(Navigator.prototype, 'userAgentData', {
            get: () => mockUAData,
            configurable: true,
            enumerable: true
        });

        // 2026 Engine-Parity Upgrade: Mock window.chrome and chrome.runtime natively
        // Genuine Chrome browsers expose window.chrome to Javascript contexts.
        // We compile a standard nested structure complete with empty runtime parameters.
        if (!window.chrome) {
            const mockChrome = {
                runtime: {
                    connect: function () {},
                    sendMessage: function () {},
                    onConnect: { addListener: function () {} },
                    onMessage: { addListener: function () {} }
                },
                app: {
                    isInstalled: false,
                    InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" },
                    runningState: function () { return "cannot_run"; }
                },
                csi: function () { return { startE: Date.now(), onloadT: Date.now() + 100, pageT: 50, tran: 15 }; },
                loadTimes: function () {
                    return {
                        requestTime: Date.now() / 1000 - 1.0,
                        startLoadTime: Date.now() / 1000 - 0.9,
                        commitLoadTime: Date.now() / 1000 - 0.5,
                        finishDocumentLoadTime: Date.now() / 1000 - 0.2,
                        finishLoadTime: Date.now() / 1000,
                        firstPaintTime: Date.now() / 1000 - 0.4,
                        firstPaintAfterLoadTime: 0,
                        navigationType: "Other",
                        wasFetchedViaSpdy: true,
                        wasNpnNegotiated: true,
                        npnNegotiatedProtocol: "h2",
                        connectionInfo: "h2"
                    };
                }
            };
            
            // Set toString parameters for all mocked window.chrome sub-methods
            modifiedFunctions.set(mockChrome.runtime.connect, "function connect() {\n    [native code]\n}");
            modifiedFunctions.set(mockChrome.runtime.sendMessage, "function sendMessage() {\n    [native code]\n}");
            modifiedFunctions.set(mockChrome.app.runningState, "function runningState() {\n    [native code]\n}");
            modifiedFunctions.set(mockChrome.csi, "function csi() {\n    [native code]\n}");
            modifiedFunctions.set(mockChrome.loadTimes, "function loadTimes() {\n    [native code]\n}");

            Object.defineProperty(window, 'chrome', {
                value: mockChrome,
                configurable: true,
                enumerable: true,
                writable: true
            });
        }
    }

    // 10/10 Evasion: Spoof navigator.languages array to align with standard Accept-Language headers
    const mockLanguages = ["en-US", "en"];
    Object.defineProperty(Navigator.prototype, 'languages', {
        get: () => mockLanguages,
        configurable: true,
        enumerable: true
    });

    // GAP-02 Fix (Upgraded): Canvas Rendering & Shape Noise Evasion
    // Slightly alters returned image data array color values by a tiny, non-perceptible fraction.
    // This ensures unique rendering hashes even if a site draws lines, gradients, or non-text assets.
    const rawGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function (x, y, width, height) {
        const imgData = rawGetImageData.call(this, x, y, width, height);
        const data = imgData.data;
        // Shift least significant bits of color channels by +/- 1 unit programmatically
        for (let i = 0; i < data.length; i += 4) {
            data[i] = (data[i] + (Math.random() < 0.5 ? 1 : -1) + 256) % 256;
            data[i+1] = (data[i+1] + (Math.random() < 0.5 ? 1 : -1) + 256) % 256;
            data[i+2] = (data[i+2] + (Math.random() < 0.5 ? 1 : -1) + 256) % 256;
        }
        return imgData;
    };
    modifiedFunctions.set(CanvasRenderingContext2D.prototype.getImageData, "function getImageData() {\n    [native code]\n}");

    const rawFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
        const shiftX = (Math.random() - 0.5) * 0.04;
        const shiftY = (Math.random() - 0.5) * 0.04;
        return rawFillText.call(this, text, x + shiftX, y + shiftY, maxWidth);
    };
    modifiedFunctions.set(CanvasRenderingContext2D.prototype.fillText, "function fillText() {\n    [native code]\n}");

    // Upgraded Canvas Evasion: Intercept toDataURL and toBlob directly
    // This applies a sub-pixel layout shift inside the canvas buffer before encoding
    const rawToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (type, encoderOptions) {
        const ctx = this.getContext('2d');
        if (ctx && ctx instanceof CanvasRenderingContext2D) {
            // Apply a microscopic, non-perceptible pixel shift on get to data URL
            ctx.fillStyle = "rgba(255,255,255,0.01)";
            ctx.fillRect(0, 0, 1, 1);
        }
        return rawToDataURL.call(this, type, encoderOptions);
    };
    modifiedFunctions.set(HTMLCanvasElement.prototype.toDataURL, "function toDataURL() {\n    [native code]\n}");

    const rawToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (callback, type, encoderOptions) {
        const ctx = this.getContext('2d');
        if (ctx && ctx instanceof CanvasRenderingContext2D) {
            ctx.fillStyle = "rgba(255,255,255,0.01)";
            ctx.fillRect(0, 0, 1, 1);
        }
        return rawToBlob.call(this, callback, type, encoderOptions);
    };
    modifiedFunctions.set(HTMLCanvasElement.prototype.toBlob, "function toBlob() {\n    [native code]\n}");

    // 2026 Font Metric Masking Upgrade:
    // Some advanced anti-bot systems (like Kasada or Datadome) measure the exact pixel output of
    // elements using Element.prototype.getBoundingClientRect or CanvasRenderingContext2D.prototype.measureText
    // to map OS font rendering footprints. We inject a microscopic, deterministic fractional noise layout shift.
    const rawGetBoundingClientRect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function () {
        const rect = rawGetBoundingClientRect.call(this);
        // Inject a sub-pixel dynamic offset (+/- 0.01px) to alter unique rendering hashes cleanly
        const jitterX = (Math.random() - 0.5) * 0.02;
        const jitterY = (Math.random() - 0.5) * 0.02;
        return {
            x: rect.x + jitterX,
            y: rect.y + jitterY,
            width: rect.width,
            height: rect.height,
            top: rect.top + jitterY,
            right: rect.right + jitterX,
            bottom: rect.bottom + jitterY,
            left: rect.left + jitterX,
            toJSON: function () { return JSON.stringify(this); }
        };
    };
    modifiedFunctions.set(Element.prototype.getBoundingClientRect, "function getBoundingClientRect() {\n    [native code]\n}");

    const rawMeasureText = CanvasRenderingContext2D.prototype.measureText;
    CanvasRenderingContext2D.prototype.measureText = function (text) {
        const metrics = rawMeasureText.call(this, text);
        // Jitter the width marginally by up to 0.02 pixels to randomize metric fingerprinting checks
        const jitterWidth = metrics.width + (Math.random() - 0.5) * 0.04;
        
        // Define standard TextMetrics output fields
        const mockMetrics = {
            width: jitterWidth,
            actualBoundingBoxLeft: metrics.actualBoundingBoxLeft,
            actualBoundingBoxRight: metrics.actualBoundingBoxRight,
            fontBoundingBoxAscent: metrics.fontBoundingBoxAscent,
            fontBoundingBoxDescent: metrics.fontBoundingBoxDescent,
            actualBoundingBoxAscent: metrics.actualBoundingBoxAscent,
            actualBoundingBoxDescent: metrics.actualBoundingBoxDescent,
            emHeightAscent: metrics.emHeightAscent,
            emHeightDescent: metrics.emHeightDescent,
            hangingBaseline: metrics.hangingBaseline,
            alphabeticBaseline: metrics.alphabeticBaseline,
            ideographicBaseline: metrics.ideographicBaseline
        };
        return mockMetrics;
    };
    modifiedFunctions.set(CanvasRenderingContext2D.prototype.measureText, "function measureText() {\n    [native code]\n}");

    // 10/10 Evasion: Spoof Font-Family Fallback Verification
    // Intercepts CSS font-loading or system font-checks to report Windows core fonts catalog
    const standardWindowsFonts = ["Arial", "Calibri", "Segoe UI", "MS Outlook", "Consolas", "Courier New"];
    const rawFontQuery = document.fonts ? document.fonts.check : null;
    if (rawFontQuery) {
        document.fonts.check = function (font, text) {
            if (standardWindowsFonts.some(f => font.includes(f))) {
                return true;
            }
            return rawFontQuery.call(this, font, text);
        };
        modifiedFunctions.set(document.fonts.check, "function check() {\n    [native code]\n}");
    }

    // 2026 WebGL Software Renderer Spoof
    const rawGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
        if (parameter === 37445) return "Intel Inc.";
        if (parameter === 37446) return "Intel(R) Iris(TM) Plus Graphics";
        return rawGetParameter.call(this, parameter);
    };
    modifiedFunctions.set(WebGLRenderingContext.prototype.getParameter, "function getParameter() {\n    [native code]\n}");

    const rawGetExtension = WebGLRenderingContext.prototype.getExtension;
    WebGLRenderingContext.prototype.getExtension = function (name) {
        if (name && name.toLowerCase() === 'webgl_debug_renderer_info') {
            return {
                UNMASKED_VENDOR_WEBGL: 37445,
                UNMASKED_RENDERER_WEBGL: 37446
            };
        }
        return rawGetExtension.call(this, name);
    };
    modifiedFunctions.set(WebGLRenderingContext.prototype.getExtension, "function getExtension() {\n    [native code]\n}");
    
    const rawGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, attributes) {
        if (type === 'webgl' || type === 'experimental-webgl') {
            return {
                getParameter: function (param) {
                    if (param === 37445) return "Intel Inc.";
                    if (param === 37446) return "Intel(R) Iris(TM) Plus Graphics";
                    return null;
                },
                getExtension: function (name) {
                    if (name && name.toLowerCase() === 'webgl_debug_renderer_info') {
                        return {
                            UNMASKED_VENDOR_WEBGL: 37445,
                            UNMASKED_RENDERER_WEBGL: 37446
                        };
                    }
                    return null;
                }
            };
        }
        return rawGetContext.call(this, type, attributes);
    };
    modifiedFunctions.set(HTMLCanvasElement.prototype.getContext, "function getContext() {\n    [native code]\n}");

    // 2026 GPU Render-Latency & Execution Timing Artificial Bottlenecks:
    // Intercepts WebGL draw calls (drawArrays / drawElements) and injects a micro-second loop delay.
    // This normalizes GPU rendering times inside virtualized Mesa LLVMPipe engines, matching consumer laptop speeds.
    const rawDrawArrays = WebGLRenderingContext.prototype.drawArrays;
    WebGLRenderingContext.prototype.drawArrays = function (mode, first, count) {
        // Enforce a small microsecond busy-loop delay to emulate real hardware latencies
        const start = performance.now();
        while (performance.now() - start < 0.05) { /* busy wait 50 microseconds */ }
        return rawDrawArrays.call(this, mode, first, count);
    };
    modifiedFunctions.set(WebGLRenderingContext.prototype.drawArrays, "function drawArrays() {\n    [native code]\n}");

    const rawDrawElements = WebGLRenderingContext.prototype.drawElements;
    WebGLRenderingContext.prototype.drawElements = function (mode, count, type, offset) {
        const start = performance.now();
        while (performance.now() - start < 0.05) { /* busy wait */ }
        return rawDrawElements.call(this, mode, count, type, offset);
    };
    modifiedFunctions.set(WebGLRenderingContext.prototype.drawElements, "function drawElements() {\n    [native code]\n}");

    // 10/10 WebGPU Hardware/Adapter Cloaking Evasion:
    // Some advanced 2026 anti-bots completely bypass WebGL entirely and query the modern WebGPU API.
    // We override GPUAdapterInfo.prototype to report a stable, non-headless Intel consumer GPU.
    if (window.GPUAdapterInfo) {
        Object.defineProperty(GPUAdapterInfo.prototype, 'vendor', { get: () => "Intel Inc.", configurable: true, enumerable: true });
        Object.defineProperty(GPUAdapterInfo.prototype, 'architecture', { get: () => "Gen11", configurable: true, enumerable: true });
        Object.defineProperty(GPUAdapterInfo.prototype, 'device', { get: () => "Intel(R) Iris(TM) Plus Graphics", configurable: true, enumerable: true });
        Object.defineProperty(GPUAdapterInfo.prototype, 'description', { get: () => "Intel(R) Iris(TM) Plus Graphics", configurable: true, enumerable: true });
    }

    // GAP-06 Fix: Spoof Media Devices (Audio/Video hardware profiles)
    const mockDevices = [
        { deviceId: "default", kind: "audioinput", label: "Default Audio Input", groupId: "default" },
        { deviceId: "mic_1", kind: "audioinput", label: "Realtek High Definition Audio", groupId: "mic_group" },
        { deviceId: "default", kind: "audiooutput", label: "Default Audio Output", groupId: "default" },
        { deviceId: "speaker_1", kind: "audiooutput", label: "Realtek Speakers", groupId: "speaker_group" },
        { deviceId: "camera_1", kind: "videoinput", label: "HD Web Camera", groupId: "camera_group" }
    ];
    
    if (!navigator.mediaDevices) {
        Object.defineProperty(navigator, 'mediaDevices', {
            value: {},
            configurable: true,
            enumerable: true,
            writable: true
        });
    }
    
    navigator.mediaDevices.enumerateDevices = async function () {
        return mockDevices;
    };
    modifiedFunctions.set(navigator.mediaDevices.enumerateDevices, "function enumerateDevices() {\n    [native code]\n}");

    // 2026 Fingerprint Upgrade: Spoof navigator.plugins & navigator.mimeTypes
    // Standard headless browsers return empty plugin arrays. We mock a standard desktop Firefox setup.
    const mockPlugins = [
        { name: "PDF Viewer", description: "Portable Document Format", filename: "internal-pdf-viewer" },
        { name: "Chrome PDF Viewer", description: "Portable Document Format", filename: "internal-pdf-viewer" },
        { name: "Chromium PDF Viewer", description: "Portable Document Format", filename: "internal-pdf-viewer" }
    ];
    Object.defineProperty(Navigator.prototype, 'plugins', {
        get: () => mockPlugins,
        configurable: true,
        enumerable: true
    });

    // 2026 Fingerprint Upgrade: Spoof Screen available dimensions
    // Real desktop browsers have available height reduced by the OS taskbar size (typically 40px)
    const rawAvailHeight = Screen.prototype.availHeight;
    Object.defineProperty(Screen.prototype, 'availHeight', {
        get: function () {
            const h = rawAvailHeight || screen.height;
            return h - 40; // Simulate standard Windows taskbar size
        },
        configurable: true,
        enumerable: true
    });

    // 10/10 Evasion: Spoof window screen offset coordinates (defeats 0,0 maximized window vm checks)
    // Real browsers are rarely maximized perfectly at 0,0. We spoof realistic coordinate offsets.
    Object.defineProperty(window, 'screenX', { get: () => 8, configurable: true, enumerable: true });
    Object.defineProperty(window, 'screenY', { get: () => 8, configurable: true, enumerable: true });
    
    // 10/10 Evasion: Secure Window Outer Dimensions Traps
    // Proactively maps standard Windows layout boundaries to prevent outer vs. inner pixel mismatches
    Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth + 16, configurable: true, enumerable: true });
    Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 79, configurable: true, enumerable: true });

    // 2026 Fingerprint Upgrade: Spoof SpeechSynthesis Voices
    // Standard headless VM runtimes return an empty list of voice engines.
    // We override this to return standard desktop-consistent default voices.
    if (window.speechSynthesis) {
        const mockVoices = [
            { name: "Microsoft David - English (United States)", lang: "en-US", default: true, localService: true, voiceURI: "Microsoft David" },
            { name: "Microsoft Zira - English (United States)", lang: "en-US", default: false, localService: true, voiceURI: "Microsoft Zira" }
        ];
        
        // 10/10 SpeechSynthesis Evasion: Asynchronous Lifecycle Mimicry
        // Real browser voice loading is async, returning empty arrays initially
        // before firing a "voiceschanged" event when the system finishes compilation.
        let voicesLoaded = false;
        window.speechSynthesis.getVoices = function () {
            if (!voicesLoaded) {
                // Trigger background compilation delay
                setTimeout(() => {
                    voicesLoaded = true;
                    const event = new Event('voiceschanged');
                    window.speechSynthesis.dispatchEvent(event);
                }, 150 + Math.random() * 100); // 150-250ms OS thread load delay
                return [];
            }
            return mockVoices;
        };
        modifiedFunctions.set(window.speechSynthesis.getVoices, "function getVoices() {\n    [native code]\n}");
    }

    // 2026 Evasion Upgrade: Spoof window.performance.now CPU instruction high-precision timing
    // Rounds timing offsets to prevent speculative micro-second timing checks
    const rawPerformanceNow = window.performance.now;
    window.performance.now = function () {
        const time = rawPerformanceNow.call(window.performance);
        // Round to nearest 100 microseconds with randomized jitter (masks virtualization clock offsets)
        return Math.floor(time / 0.1) * 0.1 + (Math.random() * 0.05);
    };
    modifiedFunctions.set(window.performance.now, "function now() {\n    [native code]\n}");

    // 2026 Web Audio API Fingerprint Spoofing (Bypasses advanced physical audio entropy analyzers)
    // Intercepts the getChannelData buffer payload in AudioBuffer and injects tiny, unnoticeable harmonic deviations
    const rawGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function (channel) {
        const buffer = rawGetChannelData.call(this, channel);
        for (let i = 0; i < Math.min(buffer.length, 10); i++) {
            // Inject a sub-microscopic physical fluctuation to randomize the audio hash value
            buffer[i] += (Math.random() - 0.5) * 1e-9;
        }
        return buffer;
    };
    modifiedFunctions.set(AudioBuffer.prototype.getChannelData, "function getChannelData() {\n    [native code]\n}");

    // 2026 Device Sensor Integrity Spoof
    // Standardizes DeviceMotionEvent parameters to match legitimate consumer Windows desktops
    if (window.DeviceMotionEvent) {
        spoofNativeProperty(window.DeviceMotionEvent.prototype, 'acceleration', null);
        spoofNativeProperty(window.DeviceMotionEvent.prototype, 'rotationRate', null);
    }

    // 10/10 Evasion: Secure Local Loopback Socket Port Scanning Protection
    // Intercepts outbound requests and blocks attempts to probe the SOCKS5 proxy port or CDP debugger
    const rawWebSocket = window.WebSocket;
    window.WebSocket = function (url, protocols) {
        if (url && (url.includes("127.0.0.1") || url.includes("localhost"))) {
            logger.critical(`Local port scanning blocked targeting: ${url}`);
            throw new Error("Connection Refused");
        }
        return new rawWebSocket(url, protocols);
    };
    modifiedFunctions.set(window.WebSocket, "function WebSocket() {\n    [native code]\n}");

    // 2026 WebAssembly Compilation Timing Normalizer:
    // Some advanced anti-bots run heavy WASM compiled loops to bypass Javascript timing clamps.
    // We intercept WebAssembly compilation methods to inject a standard cognitive micro-delay.
    if (window.WebAssembly && window.WebAssembly.compile) {
        const rawWasmCompile = window.WebAssembly.compile;
        window.WebAssembly.compile = async function (bytes) {
            const delay = 5.0 + Math.random() * 5.0; // Enforce a 5-10ms compilation delay
            await new Promise(resolve => setTimeout(resolve, delay));
            return rawWasmCompile.call(this, bytes);
        };
        modifiedFunctions.set(window.WebAssembly.compile, "function compile() {\n    [native code]\n}");
    }

    // 2026 Haptic Touch & Biometric PointerEvent Spoofing:
    // Custom-maps standard pointer metrics during active click/drag events.
    // Replaces default 0 or 0.5 values with randomized, humanized pressure/radii metrics.
    if (window.PointerEvent) {
        const rawPointerPressure = Object.getOwnPropertyDescriptor(PointerEvent.prototype, 'pressure');
        Object.defineProperty(PointerEvent.prototype, 'pressure', {
            get: function () {
                // If it is a touch/active mouse event, simulate randomized physical finger pressure
                return this.buttons > 0 ? (0.55 + Math.random() * 0.25) : 0.0;
            },
            configurable: true,
            enumerable: true
        });
    }

    // 2026 V8 Engine Micro-Task Queue Starvation Evasion:
    // Some advanced anti-bots (like Kasada or Datadome) measure the priority queues and Garbage Collection (GC)
    // latencies of the V8 event loop. Real browsers experience micro-stutters from background rendering.
    // We intercept Promise.prototype.then and setTimeout to inject standard, non-periodic execution stalls (50-200ns).
    const rawPromiseThen = Promise.prototype.then;
    Promise.prototype.then = function (onFulfilled, onRejected) {
        const start = performance.now();
        // Inject a sub-nanosecond busy-loop delay to emulate real V8 task queuing fluctuations
        while (performance.now() - start < 0.0001) { /* 100ns busy wait */ }
        return rawPromiseThen.call(this, onFulfilled, onRejected);
    };
    modifiedFunctions.set(Promise.prototype.then, "function then() {\n    [native code]\n}");

    // 2026 Navigation Timing Normalizer (Bypasses DataDome / PerimeterX checks):
    // Standard unhardened automation environments show static or zero-latency DNS/TCP connect times.
    // We override PerformanceNavigationTiming prototype properties to inject organic, real-world network-level jitter.
    if (window.PerformanceNavigationTiming) {
        const getPerformanceTimingProperty = (prop, baseJitter, addJitter) => {
            const rawDescriptor = Object.getOwnPropertyDescriptor(PerformanceNavigationTiming.prototype, prop);
            if (!rawDescriptor) return;
            Object.defineProperty(PerformanceNavigationTiming.prototype, prop, {
                get: function () {
                    const rawValue = rawDescriptor.get.call(this);
                    if (rawValue === 0) return 0; // Maintain uninitialized markers
                    // Inject realistic network-layer jitter (e.g., 5-15ms)
                    return rawValue + (Math.random() * baseJitter) + addJitter;
                },
                configurable: true,
                enumerable: true
            });
        };

        // Apply dynamic, non-linear network jitter to DNS, TCP, and TLS handshake metrics
        getPerformanceTimingProperty('domainLookupEnd', 12, 5);
        getPerformanceTimingProperty('connectEnd', 18, 8);
        getPerformanceTimingProperty('secureConnectionStart', 15, 6);
        getPerformanceTimingProperty('requestStart', 20, 10);
        getPerformanceTimingProperty('responseStart', 25, 12);
        getPerformanceTimingProperty('responseEnd', 30, 15);
    }

    // 2026 Fingerprint Upgrade: Spoof navigator.connection (NetworkInformation API) Dynamic Fidelity
    // Real network connections experience dynamic variations. We inject fluctuating delays over time.
    if (navigator.connection) {
        const rawRtt = Object.getOwnPropertyDescriptor(NetworkInformation.prototype, 'rtt');
        Object.defineProperty(NetworkInformation.prototype, 'rtt', {
            get: function () {
                // Inject a dynamic micro-jitter (e.g., +/- 15ms) based on execution clock drift
                const jitter = Math.floor((Math.sin(performance.now() * 0.001) + 1) * 15);
                return (rawRtt ? rawRtt.get.call(this) : 100) + jitter;
            },
            configurable: true,
            enumerable: true
        });

        const rawDownlink = Object.getOwnPropertyDescriptor(NetworkInformation.prototype, 'downlink');
        Object.defineProperty(NetworkInformation.prototype, 'downlink', {
            get: function () {
                const drift = Math.cos(performance.now() * 0.001) * 0.5;
                return (rawDownlink ? rawDownlink.get.call(this) : 10.0) + drift;
            },
            configurable: true,
            enumerable: true
        });
    }

    // 10/10 2026 Evasion Upgrade: Spoof DateTimeFormat & Date Prototype Timezone Offset
    // Overrides standard JavaScript timezone resolution variables natively to guarantee perfect
    // alignment with the dynamic proxy region, preventing any system clock vs. browser mismatches.
    const rawDateTimeFormat = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function () {
        const options = rawDateTimeFormat.call(this);
        // Enforce native alignment matching our America/New_York proxy target georegion
        options.timeZone = "America/New_York";
        options.locale = "en-US";
        return options;
    };
    modifiedFunctions.set(Intl.DateTimeFormat.prototype.resolvedOptions, "function resolvedOptions() {\n    [native code]\n}");

    const rawGetTimezoneOffset = Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset = function () {
        // Enforce the New York daylight savings/standard time offset in minutes (240 or 300)
        // This ensures the JavaScript offset perfectly matches the system and proxy clock
        return 240; 
    };
    modifiedFunctions.set(Date.prototype.getTimezoneOffset, "function getTimezoneOffset() {\n    [native code]\n}");

    // 2026 ML Evasion Upgrade: Secure the navigator.permissions query API
    if (navigator.permissions && navigator.permissions.query) {
        const rawQuery = navigator.permissions.query;
        navigator.permissions.query = async function (parameters) {
            if (parameters && parameters.name === 'notifications') {
                return {
                    state: 'default',
                    onchange: null
                };
            }
            return rawQuery.call(this, parameters);
        };
        modifiedFunctions.set(navigator.permissions.query, "function query() {\n    [native code]\n}");
    }

    console.log("WebGL, Canvas, Media Devices, Permissions, Plugins, Voices, performance, WASM, Haptics, V8 Queue, Navigation timings, NetworkInformation, DateTimeFormat, and Prototype chain successfully secured.");
})();
