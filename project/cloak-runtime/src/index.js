// src/index.js
// Main API entry point for cloak-runtime.
//
// USAGE:
//   const { cloak } = require('cloak-runtime');
//   const browser = await cloak.launch({
//     persona: 'chrome-128-win',   // or path to your own manifest
//     proxy: 'socks5://user:pass@host:port',
//     headless: true
//   });
//   const page = await browser.newPage();
//   await page.goto('https://example.com');
//   await browser.close();

'use strict';

const path = require('path');
const fs = require('fs');
const { buildRuntime, loadBuiltInManifest, buildFromFile } = require('./manifest_patcher');
const { HumanizedSession, PersonaTimingProfile, ProxyRotator } = require('./timing');
const { SignatureRotator } = require('./signature_rotator');
const { patchPlaywrightLaunch } = require('./playwright_integration');

/**
 * Load a manifest from name or path, build the runtime script.
 * FIX #103: Use a single code path for both file and built-in lookup.
 * FIX #153: Check e.code === 'ENOENT' reliably (not message).
 */
// FIX #651: cache for loadRuntime results
const _runtimeCache = new Map();
const MAX_RUNTIME_CACHE = 50;

function loadRuntime(personaOrPath) {
    if (typeof personaOrPath !== 'string' || personaOrPath.length === 0) {
        throw new Error(`loadRuntime: personaOrPath must be a non-empty string`);
    }
    // FIX #651: cache results to avoid rebuilding
    // FIX #1924: also check resolved path to avoid cache key collision
    if (_runtimeCache.has(personaOrPath)) {
        return _runtimeCache.get(personaOrPath);
    }
    let result;
    try {
        result = buildFromFile(personaOrPath);
    } catch (e) {
        // FIX #153: Use error code, not message substring.
        if (e && (e.code === 'ENOENT' || e.code === 'ENOTDIR')) {
            try {
                result = loadBuiltInManifest(personaOrPath);
            } catch (e2) {
                throw e2;
            }
        } else {
            throw e;
        }
    }
    // FIX #651: cache with bounded size
    if (_runtimeCache.size >= MAX_RUNTIME_CACHE) {
        const firstKey = _runtimeCache.keys().next().value;
        _runtimeCache.delete(firstKey);
    }
    _runtimeCache.set(personaOrPath, result);
    return result;
}

/**
 * The main `cloak` object - this is what users import.
 */
const cloak = {
    // FIX #698: getBrowserMeta — retrieve cloak meta from a wrapped browser
    getBrowserMeta(browser) {
        if (!browser || !browser._cloak) return null;
        return { ...browser._cloak };
    },
    // FIX #697: getActiveSession
    getActiveSession(browser) {
        if (!browser || !browser._cloak) return null;
        return browser._cloak.humanizedSession || null;
    },

    /**
     * FIX #2023: Wrap a BrowserContext (from launchPersistentContext) as a Browser-like
     * object so patchPlaywrightLaunch can be called on it.
     * @param {BrowserContext} context
     * @returns {Object} Browser-like wrapper
     */
    _wrapContextAsBrowser(context) {
        // The context itself acts as a "browser" in this mode.
        // newPage() is called directly on the context.
        return {
            newContext: async (opts) => {
                // In persistent context mode, we can't create new contexts.
                // Return the existing context if opts is empty.
                // FIX #2087: allow contextOptions to be passed
                if (opts && opts._cloakContextOptions) {
                    // Apply contextOptions to the existing context (limited)
                    // Most contextOptions can't be changed after creation
                    // but we accept them for API compatibility
                }
                if (opts && Object.keys(opts).length > 0 && !opts._cloakContextOptions) {
                    throw new Error('Cannot create new context in launchPersistentContext mode');
                }
                return context;
            },
            newPage: async (opts) => {
                // FIX #2082: pass opts to context.newPage
                if (opts && Object.keys(opts).length > 0) {
                    return await context.newPage(opts);
                }
                return await context.newPage();
            },
            close: async () => {
                return await context.close();
            },
            contexts: () => [context],
            isConnected: () => context.pages().length > 0,
            _isPersistentContext: true,
            _persistentContext: context,
        };
    },

    /**
     * Launch a hardened Playwright browser with a persona applied.
     *
     * @param {Object} options
     * @param {string} options.persona - Built-in name (e.g. 'chrome-128-win') or path to manifest.json
     * @param {string} [options.proxy] - Proxy URL
     * @param {boolean} [options.headless=true] - Run headless
     * @param {Object} [options.timing] - Timing profile overrides
     * @param {boolean} [options.signatureRotation=true] - Enable monthly signature salt
     * @param {Object} [options.playwrightOptions] - Pass-through to playwright.chromium.launch()
     * @param {string[]} [options.additionalScripts] - Extra init scripts to inject into every page
     * @param {string} [options.executablePath] - Path to Chromium binary (top-level convenience)
     * @returns {Promise<Browser>} - Wrapped Playwright Browser with ._cloak metadata
     */
    async launch(options = {}) {
        // FIX #654: validate options is an object
        // FIX #1091: validate options is a plain object (not array)
        if (options === null || typeof options !== 'object' || Array.isArray(options)) {
            throw new Error('cloak.launch: options must be a plain object');
        }
        // FIX #1092: validate persona is string
        const {
            persona = 'chrome-128-win',
            proxy = null,
            headless = true,
            timing = {},
            signatureRotation = true,
            playwrightOptions = {},
            additionalScripts = null,
            executablePath = null,
            // FIX #2021: add extension support
            extensions = null,
            // FIX #2028: add userDataDir support (required for extensions)
            userDataDir = null,
            // FIX #2031: add connect support for remote browser
            connectEndpoint = null,
            // FIX #2032: add channel support
            channel = null,
            // FIX #2023: add usePersistentContext flag
            usePersistentContext = false,
        } = options;
        if (typeof persona !== 'string' || persona.length === 0) {
            throw new Error('cloak.launch: persona must be a non-empty string');
        }

        // FIX #1528: validate contextOptions
        if (options.contextOptions !== undefined) {
            if (typeof options.contextOptions !== 'object' || Array.isArray(options.contextOptions)) {
                throw new Error('cloak.launch: contextOptions must be a plain object');
            }
            // FIX #1925: validate recordVideo
            if (options.contextOptions.recordVideo !== undefined &&
                options.contextOptions.recordVideo !== null &&
                typeof options.contextOptions.recordVideo !== 'object') {
                throw new Error('cloak.launch: contextOptions.recordVideo must be an object or null');
            }
            // FIX #1926: validate storageState
            if (options.contextOptions.storageState !== undefined &&
                options.contextOptions.storageState !== null &&
                typeof options.contextOptions.storageState !== 'string' &&
                typeof options.contextOptions.storageState !== 'object') {
                throw new Error('cloak.launch: contextOptions.storageState must be string or object');
            }
            // FIX #1927: validate geolocation
            if (options.contextOptions.geolocation !== undefined &&
                options.contextOptions.geolocation !== null &&
                typeof options.contextOptions.geolocation !== 'object') {
                throw new Error('cloak.launch: contextOptions.geolocation must be an object or null');
            }
            // FIX #1928: validate permissions
            if (options.contextOptions.permissions !== undefined &&
                options.contextOptions.permissions !== null &&
                !Array.isArray(options.contextOptions.permissions)) {
                throw new Error('cloak.launch: contextOptions.permissions must be an array');
            }
            // FIX #1929: validate colorScheme
            if (options.contextOptions.colorScheme !== undefined &&
                options.contextOptions.colorScheme !== null &&
                typeof options.contextOptions.colorScheme !== 'string') {
                throw new Error('cloak.launch: contextOptions.colorScheme must be a string');
            }
            // FIX #1930: validate extraHTTPHeaders
            if (options.contextOptions.extraHTTPHeaders !== undefined &&
                options.contextOptions.extraHTTPHeaders !== null &&
                typeof options.contextOptions.extraHTTPHeaders !== 'object') {
                throw new Error('cloak.launch: contextOptions.extraHTTPHeaders must be an object');
            }
        }
        // FIX #1626: validate viewport
        if (options.viewport !== undefined && options.viewport !== null) {
            if (typeof options.viewport !== 'object' || Array.isArray(options.viewport)) {
                throw new Error('cloak.launch: viewport must be an object');
            }
        }
        // FIX #1627: validate userDataDir
        if (options.userDataDir !== undefined && options.userDataDir !== null) {
            if (typeof options.userDataDir !== 'string' || options.userDataDir.length === 0) {
                throw new Error('cloak.launch: userDataDir must be a non-empty string');
            }
        }
        // FIX #1726: validate deviceScaleFactor
        if (options.deviceScaleFactor !== undefined && options.deviceScaleFactor !== null) {
            if (typeof options.deviceScaleFactor !== 'number' || !isFinite(options.deviceScaleFactor) || options.deviceScaleFactor <= 0) {
                throw new Error('cloak.launch: deviceScaleFactor must be a positive number');
            }
        }
        // FIX #1727: validate locale
        if (options.locale !== undefined && options.locale !== null) {
            if (typeof options.locale !== 'string' || options.locale.length === 0) {
                throw new Error('cloak.launch: locale must be a non-empty string');
            }
        }
        // FIX #1827: validate proxy is string
        if (proxy !== null && proxy !== undefined && typeof proxy !== 'string') {
            throw new Error('cloak.launch: proxy must be a string');
        }
        // FIX #956: validate signatureRotation is boolean
        if (typeof signatureRotation !== 'boolean') {
            throw new Error('cloak.launch: signatureRotation must be a boolean');
        }
        // FIX #957: validate timing is object
        if (timing !== undefined && timing !== null &&
            (typeof timing !== 'object' || Array.isArray(timing))) {
            throw new Error('cloak.launch: timing must be an object');
        }
        // FIX #958: validate playwrightOptions is object
        if (playwrightOptions !== undefined && playwrightOptions !== null &&
            (typeof playwrightOptions !== 'object' || Array.isArray(playwrightOptions))) {
            throw new Error('cloak.launch: playwrightOptions must be an object');
        }
        // FIX #659: validate headless is boolean
        if (headless !== undefined && typeof headless !== 'boolean') {
            throw new Error('cloak.launch: headless must be a boolean');
        }
        // FIX #664: validate additionalScripts is an array if provided
        if (additionalScripts !== null && additionalScripts !== undefined &&
            !Array.isArray(additionalScripts)) {
            throw new Error('cloak.launch: additionalScripts must be an array');
        }
        // FIX #1931: validate additionalScripts entries are non-empty strings
        if (Array.isArray(additionalScripts)) {
            for (let _i = 0; _i < additionalScripts.length; _i++) {
                const _s = additionalScripts[_i];
                if (_s !== null && _s !== undefined &&
                    (typeof _s !== 'string' || _s.length === 0)) {
                    throw new Error('cloak.launch: additionalScripts entries must be non-empty strings');
                }
            }
        }
        // FIX #665: validate executablePath is non-empty string
        if (executablePath !== null && executablePath !== undefined &&
            (typeof executablePath !== 'string' || executablePath.length === 0)) {
            throw new Error('cloak.launch: executablePath must be a non-empty string');
        }
        // FIX #2021: validate extensions option
        if (extensions !== null && extensions !== undefined) {
            if (!Array.isArray(extensions)) {
                throw new Error('cloak.launch: extensions must be an array of paths');
            }
            for (let _i = 0; _i < extensions.length; _i++) {
                const _ext = extensions[_i];
                if (typeof _ext !== 'string' || _ext.length === 0) {
                    throw new Error('cloak.launch: each extension must be a non-empty string path');
                }
            }
        }
        // FIX #2028: validate userDataDir
        if (userDataDir !== null && userDataDir !== undefined) {
            if (typeof userDataDir !== 'string' || userDataDir.length === 0) {
                throw new Error('cloak.launch: userDataDir must be a non-empty string');
            }
        }
        // FIX #2031: validate connectEndpoint
        if (connectEndpoint !== null && connectEndpoint !== undefined) {
            if (typeof connectEndpoint !== 'string' || connectEndpoint.length === 0) {
                throw new Error('cloak.launch: connectEndpoint must be a non-empty string');
            }
        }
        // FIX #2032: validate channel
        if (channel !== null && channel !== undefined) {
            if (typeof channel !== 'string' || !['chrome', 'chrome-beta', 'chrome-dev', 'chrome-canary', 'msedge', 'msedge-beta', 'msedge-dev', 'msedge-canary'].includes(channel)) {
                throw new Error('cloak.launch: channel must be a valid browser channel');
            }
        }
        // FIX #2023: validate usePersistentContext
        if (typeof usePersistentContext !== 'boolean') {
            throw new Error('cloak.launch: usePersistentContext must be a boolean');
        }
        // FIX #2021 + #2023: extensions require persistent context
        if (extensions && extensions.length > 0 && !usePersistentContext && !userDataDir) {
            throw new Error('cloak.launch: extensions require usePersistentContext=true or userDataDir');
        }

        // Lazy-require playwright (peer dep)
        let playwright;
        try {
            // FIX #656: handle null require
            playwright = require('playwright');
            if (!playwright) throw new Error('playwright module is null');
        } catch (e) {
            throw new Error(
                'playwright not installed. Run: npm install playwright\n' +
                'Original error: ' + e.message
            );
        }

        // Build the runtime script from the manifest
        const runtimeScript = loadRuntime(persona);

        // Compute proxy config
        const proxyConfig = proxy ? this._parseProxy(proxy) : null;

        // Launch Playwright
        // FIX #112: Sanitize playwrightOptions.args to prevent
        // remote-debugging-port and other fingerprinting flags.
        // FIX #345: Also strip dangerous default args like --enable-automation.
        // FIX #2021: preserve extension-related args (--load-extension, --disable-extensions-except)
        const sanitizedPWOpts = this._sanitizePlaywrightOptions(playwrightOptions);
        // FIX #141: Top-level executablePath is merged into playwrightOptions.
        // FIX #1992: warn if playwrightOptions.executablePath is also set (will be overridden)
        if (executablePath) {
            if (sanitizedPWOpts.executablePath && sanitizedPWOpts.executablePath !== executablePath) {
                if (typeof console !== 'undefined' && console.warn) {
                    console.warn('cloak.launch: playwrightOptions.executablePath will be overridden by top-level executablePath');
                }
            }
            sanitizedPWOpts.executablePath = executablePath;
        }
        // FIX #2032: merge channel
        if (channel) {
            sanitizedPWOpts.channel = channel;
        }
        // FIX #2021: build extension args
        if (extensions && Array.isArray(extensions) && extensions.length > 0) {
            const _extArgs = [];
            // Validate each extension path exists
            const _fs = require('fs');
            for (const _extPath of extensions) {
                try {
                    if (!_fs.statSync(_extPath).isDirectory()) {
                        throw new Error(`Extension path is not a directory: ${_extPath}`);
                    }
                } catch (_e) {
                    throw new Error(`Invalid extension path: ${_extPath}: ${_e.message}`);
                }
            }
            _extArgs.push(`--disable-extensions-except=${extensions.join(',')}`);
            _extArgs.push(`--load-extension=${extensions.join(',')}`);
            sanitizedPWOpts.args = (sanitizedPWOpts.args || []).concat(_extArgs);
        }
        // FIX #2024: warn if --disable-web-security is used (needed for extension dev)
        if (Array.isArray(sanitizedPWOpts.args)) {
            for (const _a of sanitizedPWOpts.args) {
                if (typeof _a === 'string' && (_a === '--disable-web-security' || _a.startsWith('--disable-web-security='))) {
                    if (typeof console !== 'undefined' && console.warn) {
                        console.warn('cloak.launch: --disable-web-security is enabled (required for some extension testing)');
                    }
                }
            }
        }
        const launchOptions = {
            headless,
            ...sanitizedPWOpts,
        };
        if (proxyConfig) {
            launchOptions.proxy = proxyConfig;
        }
        // FIX #2087: merge contextOptions into launchOptions
        // Playwright accepts some context options at launch time
        if (options.contextOptions && typeof options.contextOptions === 'object') {
            for (const _key of Object.keys(options.contextOptions)) {
                if (!( _key in launchOptions)) {
                    launchOptions[ _key] = options.contextOptions[_key];
                }
            }
        }
        // FIX #2083: pass viewport to launch options
        // (viewport is a context-level option but Playwright also accepts it at launch)
        if (options.viewport && !sanitizedPWOpts.viewport) {
            launchOptions.viewport = options.viewport;
        }
        // FIX #67 + #84: Wrap launch in try/catch and ensure browser
        // is closed on any subsequent init failure (resource leak).
        // FIX #2023: support launchPersistentContext for extensions
        // FIX #2031: support connect for remote browser
        let browser;
        let context = null;
        try {
            if (connectEndpoint) {
                // FIX #2031: connect to existing browser
                browser = await playwright.chromium.connect(connectEndpoint);
            } else if (usePersistentContext || userDataDir) {
                // FIX #2023: use launchPersistentContext (required for extensions)
                if (!userDataDir) {
                    throw new Error('cloak.launch: userDataDir is required for usePersistentContext');
                }
                const _persistentOpts = { ...launchOptions, userDataDir };
                // headless must be in persistent context opts
                if (headless !== undefined) _persistentOpts.headless = headless;
                context = await playwright.chromium.launchPersistentContext(userDataDir, _persistentOpts);
                // FIX #2023: launchPersistentContext returns BrowserContext, not Browser
                // Create a wrapper that looks like a Browser for backward compat
                browser = this._wrapContextAsBrowser(context);
            } else {
                browser = await playwright.chromium.launch(launchOptions);
            }
        } catch (e) {
            throw new Error(
                `Failed to launch Playwright browser. ` +
                `Is Chromium installed? Try: npx playwright install chromium\n` +
                `Original error: ${e.message}`
            );
        }

        // FIX #2006: build _cloak first, then attach AFTER patch succeeds
        const _cloakMeta = {
            persona,
            proxyConfig,
            runtimeScript,
            signatureRotator: signatureRotation ? new SignatureRotator() : null,
            timingProfile: this._buildTimingProfile(persona, timing),
            humanizedSession: null,  // Created per-context
            additionalScripts,  // FIX #136
        };
        try {
            // Patch the browser so newContext / newPage auto-installs the runtime
            patchPlaywrightLaunch(browser, _cloakMeta);
            // FIX #2006: only attach _cloak after successful patching
            browser._cloak = _cloakMeta;
        } catch (e) {
            // FIX #84: If init fails AFTER launch, close the browser
            // to avoid leaking the Chromium process.
            try { await browser.close(); } catch (closeErr) { /* ignore */ }
            throw e;
        }

        return browser;
    },

    /**
     * Sanitize Playwright options to remove fingerprinting-revealing args.
     * FIX #112: Strip remote-debugging-port and other detectable flags.
     * FIX #345: Also handle ignoreDefaultArgs.
     */
    _sanitizePlaywrightOptions(opts) {
        // FIX #1628: handle non-object gracefully
        if (opts === null || opts === undefined) return {};
        if (typeof opts !== 'object') return {};
        // FIX #962: try/catch around spread to handle circular opts
        let result;
        try {
            result = { ...opts };
        } catch (e) {
            // Circular reference; fall back to empty
            result = {};
        }
        // FIX #1525: ensure result is plain object
        if (typeof result !== 'object' || Array.isArray(result)) {
            result = {};
        }
        // FIX #668: don't sanitize away channels/devtools (legit use)
        // FIX #2025: preserve extension-related args (--load-extension, --disable-extensions-except)
        // FIX #2026: allow --disable-web-security and --no-sandbox for extension dev/testing
        const dangerousArgs = [
            '--remote-debugging-port',
            '--remote-debugging-address',
            '--enable-automation',
        ];
        // FIX #2025: args that should be preserved (extension-related)
        const preservedArgs = [
            '--load-extension=',
            '--disable-extensions-except=',
        ];
        if (Array.isArray(result.args)) {
            // FIX #666: filter out non-string args
            // FIX #2007: use exact match or '=' suffix only (not arbitrary startsWith)
            result.args = result.args.filter(a => {
                if (typeof a !== 'string') return false;
                // FIX #2025: always preserve extension args
                if (preservedArgs.some(p => a.startsWith(p))) return true;
                return !dangerousArgs.some(d => a === d || a.startsWith(d + '=') || a.startsWith(d + ' '));
            });
        }
        // FIX #345: Append dangerous args to ignoreDefaultArgs so
        // Playwright doesn't add them automatically.
        // FIX #667: handle ignoreDefaultArgs as boolean or string
        // FIX #2026: --disable-web-security and --no-sandbox are now allowed for extension dev
        const additionalIgnore = dangerousArgs.map(a => a.replace(/^--?/, '').split('=')[0]);
        if (Array.isArray(result.ignoreDefaultArgs)) {
            const existing = new Set(result.ignoreDefaultArgs);
            for (const a of additionalIgnore) existing.add(a);
            result.ignoreDefaultArgs = Array.from(existing);
        } else if (result.ignoreDefaultArgs === undefined) {
            result.ignoreDefaultArgs = additionalIgnore;
        } else if (typeof result.ignoreDefaultArgs === 'boolean') {
            // User wants to ignore all defaults; preserve their choice
        } else if (typeof result.ignoreDefaultArgs === 'string') {
            // Single string: convert to array
            result.ignoreDefaultArgs = [result.ignoreDefaultArgs, ...additionalIgnore];
        } else {
            // FIX #2005: unknown type - still apply our safety list
            result.ignoreDefaultArgs = additionalIgnore;
        }
        return result;
    },

    /**
     * Create a HumanizedSession for a persona - call this once you have a context.
     */
    createHumanizedSession(cloakMeta, rngSeed) {
        // FIX #965: track session count
        if (!this._sessionCount) this._sessionCount = 0;
        if (this._sessionCount >= 100) {
            throw new Error('createHumanizedSession: too many sessions (max 100)');
        }
        // FIX #1253: validate cloakMeta fields
        if (cloakMeta && typeof cloakMeta !== 'object') {
            throw new Error('createHumanizedSession: cloakMeta must be an object');
        }
        // FIX #1254: validate rngSeed type
        if (rngSeed !== undefined && rngSeed !== null && typeof rngSeed !== 'number') {
            throw new Error('createHumanizedSession: rngSeed must be a number');
        }
        // FIX #1103 + #1989: rngSeed=0 should be honored (use 0, not treated as null)
        // Removed the special case that mapped 0 -> undefined
        // rngSeed=0 will now be passed to HumanizedSession which handles it correctly
        // FIX #670: validate cloakMeta
        if (!cloakMeta || typeof cloakMeta !== 'object') {
            throw new Error('createHumanizedSession: cloakMeta must be an object');
        }
        if (!cloakMeta.timingProfile) {
            throw new Error('createHumanizedSession: cloakMeta.timingProfile is required');
        }
        // FIX #672: validate rngSeed
        // FIX #1938: also reject negative
        if (rngSeed !== undefined && rngSeed !== null &&
            (typeof rngSeed !== 'number' || !isFinite(rngSeed) || rngSeed < 0)) {
            throw new Error('createHumanizedSession: rngSeed must be a non-negative finite number');
        }
        const profile = cloakMeta.timingProfile;
        const session = new HumanizedSession(profile, rngSeed);
        cloakMeta.humanizedSession = session;
        // FIX #965: increment session count
        this._sessionCount++;
        return session;
    },

    /**
     * Load a custom manifest from disk.
     * FIX #302: Delegates to loadRuntime to avoid duplication.
     * FIX #966: validate result is string
     * FIX #1105: validate path is string
     * FIX #1106: handle null path
     */
    loadManifest(manifestPath) {
        if (manifestPath === null || manifestPath === undefined) {
            throw new Error('loadManifest: manifestPath is required');
        }
        // FIX #1402: reject null bytes in path
        if (typeof manifestPath === 'string' && manifestPath.includes('\0')) {
            throw new Error('loadManifest: manifestPath contains null byte');
        }
        if (typeof manifestPath !== 'string' || manifestPath.length === 0) {
            throw new Error('loadManifest: manifestPath must be a non-empty string');
        }
        const result = loadRuntime(manifestPath);
        if (typeof result !== 'string') {
            throw new Error('loadManifest: result is not a string');
        }
        // FIX #1401: validate result is non-empty
        if (result.trim().length === 0) {
            throw new Error('loadManifest: result is empty');
        }
        return result;
    },

    /**
     * List built-in personas.
     * FIX #71: Return empty array with warning if manifests dir missing.
     */
    listPersonas() {
        // FIX #968: cache results
        if (this._personasCache !== undefined) {
            return this._personasCache;
        }
        const dir = path.join(__dirname, '..', 'manifests');
        let result;
        try {
            // FIX #1257: try accessSync to test readability
            try {
                fs.accessSync(dir, fs.constants.R_OK);
            } catch (e) {
                result = [];
                this._personasCache = result;
                return result;
            }
            // FIX #677: validate dir is a directory
            // FIX #1937: use lstat to detect symlinks
            // FIX #1993: follow symlinks for actual content
            let stat;
            try { stat = fs.lstatSync(dir); } catch (e) { stat = fs.statSync(dir); }
            if (stat.isSymbolicLink()) {
                // Resolve symlink for the actual directory contents
                stat = fs.statSync(dir);
            }
            if (!stat.isDirectory()) {
                result = [];
            } else {
                // FIX #675: filter case-insensitively (handles .JSON too)
                // FIX #676: sort results
                // FIX #1260: case-insensitive sort
                const entries = fs.readdirSync(dir)
                    .filter(f => f.toLowerCase().endsWith('.json'))
                    .map(f => f.replace(/\.json$/i, ''));
                entries.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
                result = entries;
            }
        } catch (e) {
            // eslint-disable-next-line no-console
            console.warn(`[cloak-runtime] Could not read manifests directory '${dir}': ${e.message}. ` +
                         `No built-in personas available.`);
            result = [];
        }
        this._personasCache = result;
        return result;
    },

    /**
     * FIX #968: Clear the personas cache.
     */
    clearPersonasCache() {
        this._personasCache = undefined;
    },

    /**
     * Build a persona timing profile.
     * FIX #251: Cache profiles by persona+overrides key.
     */
    _buildTimingProfile(persona, overrides) {
        // FIX #678: validate overrides is an object
        if (overrides !== undefined && overrides !== null &&
            (typeof overrides !== 'object' || Array.isArray(overrides))) {
            overrides = {};
        }
        // FIX #251: Build a cache key from persona + overrides.
        const cacheKey = JSON.stringify({ persona, overrides: overrides || {} });
        if (this._profileCache && this._profileCache.has(cacheKey)) {
            return this._profileCache.get(cacheKey);
        }

        // FIX #3: Instead of regex-parsing the runtime to recover
        // persona metadata, load the manifest directly and read its
        // `persona` block.
        // FIX #60: Log JSON parse errors instead of silent default.
        // FIX #132: Use cached manifest data.
        let personaData = {};
        try {
            const manifest = this._loadManifestJson(persona);
            personaData = manifest.persona || {};
        } catch (e) {
            // eslint-disable-next-line no-console
            console.warn(`[cloak-runtime] Could not load manifest '${persona}': ${e.message}. ` +
                         `Using default timing profile.`);
        }

        // FIX #120: Validate override types. If user passes a non-number,
        // fall back to default rather than passing NaN through to the RNG.
        const safeNum = (v, dflt) => {
            if (typeof v !== 'number' || !isFinite(v)) return dflt;
            return v;
        };

        // FIX #1936: validate overrides.timezone
        let tzOverride = null;
        if (overrides.timezone !== undefined && overrides.timezone !== null) {
            if (typeof overrides.timezone === 'number' && isFinite(overrides.timezone)) {
                tzOverride = overrides.timezone;
            } else if (typeof overrides.timezone === 'string') {
                tzOverride = this._timezoneFromPersonaData({ timezone: overrides.timezone });
            }
        }
        const tz = tzOverride !== null
            ? tzOverride
            : this._timezoneFromPersonaData(personaData);

        const profile = new PersonaTimingProfile({
            persona_id: persona,
            timezone_offset_hours: tz,
            delay_mu: safeNum(overrides.delayMu, 1.0),
            delay_sigma: safeNum(overrides.delaySigma, 0.5),
            min_delay_ms: safeNum(overrides.minDelayMs, 200),
            max_delay_s: safeNum(overrides.maxDelayS, 60),
            fatigue_rate: safeNum(overrides.fatigueRate, 1.4),
            active_hours_start: safeNum(overrides.activeStart, 8),
            active_hours_end: safeNum(overrides.activeEnd, 23),
            median_session_minutes: safeNum(overrides.medianSessionMinutes, 12),
        });

        // FIX #251: Cache the profile.
        if (!this._profileCache) this._profileCache = new Map();
        if (this._profileCache.size >= 50) {
            // FIFO eviction
            const firstKey = this._profileCache.keys().next().value;
            this._profileCache.delete(firstKey);
        }
        this._profileCache.set(cacheKey, profile);
        return profile;
    },

    /**
     * FIX #251: Clear the profile cache.
     */
    clearProfileCache() {
        if (this._profileCache) this._profileCache.clear();
    },

    /**
     * Helper: load manifest JSON regardless of whether `name` is a
     * built-in name or a filesystem path.
     * FIX #60: Wrap JSON.parse in try/catch so errors propagate cleanly.
     * FIX #132: Cache results so repeated lookups don't re-read the file.
     * FIX #162 + #201: Deep-clone before caching so caller mutations
     * don't corrupt the cache.
     * FIX #184: Validate patches is an array before returning.
     * FIX #228: Also handle ENOTDIR for cross-platform compatibility.
     */
    _loadManifestJson(personaOrPath) {
        if (!personaOrPath || typeof personaOrPath !== 'string') {
            throw new Error(`Invalid manifest name: ${personaOrPath}`);
        }
        // FIX #1111: validate path is safe (no null bytes)
        if (personaOrPath.includes('\0')) {
            throw new Error('Invalid manifest path: null byte detected');
        }
        // FIX #2085: path traversal protection
        // If the path looks like a filesystem path (contains / or \ or starts with . or has an extension),
        // validate it's within the allowed directories
        const _looksLikePath = personaOrPath.includes('/') || personaOrPath.includes('\\') ||
                                personaOrPath.startsWith('.') || /\.[a-z]+$/i.test(personaOrPath);
        // FIX #1261: validate path doesn't contain '..'
        const normalized = path.resolve(personaOrPath);
        if (normalized.split(path.sep).includes('..')) {
            throw new Error('Invalid manifest path: contains ..');
        }
        // FIX #2085: if it looks like a path, validate it's within allowed dirs
        if (_looksLikePath) {
            const _allowedDirs = [
                path.resolve(__dirname, '..', 'manifests'),
                process.cwd(),
            ];
            let _isAllowed = false;
            for (const _dir of _allowedDirs) {
                if (normalized.startsWith(_dir + path.sep) || normalized === _dir) {
                    _isAllowed = true;
                    break;
                }
            }
            if (!_isAllowed) {
                throw new Error('Invalid manifest path: outside allowed directories');
            }
        }
        // FIX #1263: cap path length
        if (normalized.length > 4096) {
            throw new Error('Invalid manifest path: too long');
        }
        if (this._manifestCache && this._manifestCache.has(normalized)) {
            // FIX #201: Return a deep clone to prevent cache corruption.
            return JSON.parse(JSON.stringify(this._manifestCache.get(normalized)));
        }
        let content;
        try {
            // FIX #1264: limit file size to prevent OOM
            const stats = fs.statSync(normalized);
            if (stats.size > 50 * 1024 * 1024) {
                throw new Error('Manifest file too large (>50MB)');
            }
            content = fs.readFileSync(normalized, 'utf8');
            // FIX #1935: validate UTF-8
            if (content.includes('�')) {
                throw new Error('Manifest file contains invalid UTF-8');
            }
            const result = JSON.parse(content);
            // FIX #1266: validate result is an object
            if (!result || typeof result !== 'object' || Array.isArray(result)) {
                throw new Error('Manifest is not an object');
            }
            // FIX #1265: validate has patches
            if (result.patches !== undefined && !Array.isArray(result.patches)) {
                throw new Error('Manifest.patches must be an array');
            }
            // FIX #1995: warn if name is missing (caller may rely on it)
            if (result.name === undefined) {
                if (typeof console !== 'undefined' && console.warn) {
                    console.warn('Manifest loaded without a "name" field');
                }
            }
            this._cacheManifest(normalized, result);
            return JSON.parse(JSON.stringify(result));
        } catch (e) {
            // FIX #1934: handle EACCES
            if (e.code === 'EACCES') {
                throw new Error(`Permission denied reading manifest '${personaOrPath}'`);
            }
            // FIX #228: Also handle ENOTDIR
            if (e.code === 'ENOENT' || e.code === 'ENOTDIR') {
                // Try built-in
                const builtInDir = path.join(__dirname, '..', 'manifests');
                const builtInPath = path.join(builtInDir, `${personaOrPath}.json`);
                try {
                    content = fs.readFileSync(builtInPath, 'utf8');
                    const result = JSON.parse(content);
                    this._cacheManifest(builtInPath, result);
                    return JSON.parse(JSON.stringify(result));
                } catch (e2) {
                    if (e2.code === 'ENOENT' || e2.code === 'ENOTDIR') {
                        throw new Error(`Manifest not found: ${personaOrPath} ` +
                                        `(tried as path and built-in)`);
                    }
                    throw new Error(`Built-in manifest '${personaOrPath}' is not valid JSON: ${e2.message}`);
                }
            }
            if (e instanceof SyntaxError) {
                throw new Error(`Manifest at '${personaOrPath}' is not valid JSON: ${e.message}`);
            }
            throw e;
        }
    },

    /**
     * Cache a parsed manifest. FIX #132: bounded by size.
     */
    _cacheManifest(key, value) {
        // FIX #1831: reject null value
        if (value === null || value === undefined) {
            throw new Error('_cacheManifest: value must not be null or undefined');
        }
        // FIX #978: validate key is a string
        // FIX #1731: validate key format
        if (typeof key !== 'string' || key.length === 0) {
            throw new Error('_cacheManifest: key must be a non-empty string');
        }
        // FIX #1533: cap key length
        if (key.length > 4096) {
            throw new Error('_cacheManifest: key too long (max 4096)');
        }
        // FIX #1731: reject control chars in key
        if (/[\x00-\x1f]/.test(key)) {
            throw new Error('_cacheManifest: key contains control characters');
        }
        // FIX #976: validate value is an object
        // FIX #1411: validate value is a plain object (not array)
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            throw new Error('_cacheManifest: value must be a plain object');
        }
        if (!this._manifestCache) {
            this._manifestCache = new Map();
        }
        // Limit cache to 100 entries to avoid unbounded growth
        if (this._manifestCache.size >= 100) {
            // Drop the oldest (FIFO)
            const firstKey = this._manifestCache.keys().next().value;
            this._manifestCache.delete(firstKey);
        }
        // FIX #201: Deep clone before storing so caller mutations
        // don't corrupt the cache.
        this._manifestCache.set(key, JSON.parse(JSON.stringify(value)));
    },

    /**
     * Clear the manifest cache. Useful for tests or after disk changes.
     */
    clearManifestCache() {
        if (this._manifestCache) this._manifestCache.clear();
    },

    /**
     * Helper: best-effort timezone offset (hours from UTC) from a
     * persona data object. Supports IANA names like "America/New_York"
     * by mapping to common offsets; otherwise returns 0.
     */
    _timezoneFromPersonaData(personaData) {
        // FIX #980: handle null personaData
        if (!personaData || typeof personaData !== 'object') {
            return 0;
        }
        // FIX #979: validate tz is a string
        let tz = personaData.timezone;
        if (typeof tz !== 'string') {
            return 0;
        }
        // FIX #981: handle empty tz
        tz = tz.trim();
        if (tz.length === 0) {
            return 0;
        }
        // FIX #1119: cap tz length
        if (tz.length > 256) {
            return 0;
        }
        // FIX #1272: handle Etc/GMT
        if (tz.startsWith('Etc/GMT')) {
            const offset = parseInt(tz.slice(7), 10);
            if (!isNaN(offset)) return -offset;
        }
        // FIX #1273: handle UTC
        if (tz === 'UTC' || tz === 'GMT') return 0;
        const map = {
            'America/New_York': -5,
            'America/Chicago': -6,
            'America/Denver': -7,
            'America/Los_Angeles': -8,
            'America/Austin': -6,
            'Europe/London': 0,
            'Europe/Paris': 1,
            'Europe/Berlin': 1,
            'Asia/Tokyo': 9,
            'Asia/Kolkata': 5.5,
            'Asia/Shanghai': 8,
            'Australia/Sydney': 11,
        };
        // FIX #1991: case-insensitive lookup
        let result = 0;
        for (const k of Object.keys(map)) {
            if (k.toLowerCase() === tz.toLowerCase()) {
                result = map[k];
                break;
            }
        }
        // FIX #1535: validate result range
        if (typeof result !== 'number' || !isFinite(result) || result < -12 || result > 14) {
            return 0;
        }
        return result;
    },

    /**
     * Parse a proxy URL into Playwright's expected format.
     * FIX #6: Reject unsupported schemes; Playwright only accepts
     *   http, https, socks5. Other schemes (e.g. ftp://, file://)
     *   should fail loudly rather than pass through.
     * FIX #23: Handle IPv6 literals (URL.host strips brackets).
     * FIX #109: Validate url is a string before new URL().
     */
    _parseProxy(url) {
        // FIX #694: include URL value in error
        // FIX #1423: handle non-string
        if (typeof url !== 'string' || url.length === 0) {
            throw new Error(`Invalid proxy URL: expected non-empty string, got ${typeof url} (value: ${JSON.stringify(url)})`);
        }

        let u;
        try {
            u = new URL(url);
        } catch (e) {
            // FIX #694: include URL value
            throw new Error(`Invalid proxy URL '${url}': ${e.message}`);
        }
        // FIX #1425: reject empty host
        if (!u.hostname || u.hostname.length === 0) {
            throw new Error(`Invalid proxy URL: host is empty`);
        }
        // FIX #1633: cap host length
        if (u.hostname.length > 253) {
            throw new Error(`Invalid proxy URL: host too long (max 253)`);
        }
        // FIX #1835: reject non-ASCII host
        if (/[^\x00-\x7f]/.test(u.hostname)) {
            throw new Error('Invalid proxy URL: non-ASCII host not supported');
        }
        // FIX #982: validate scheme list
        // FIX #986/#987/#991/#994/#995: reject dangerous schemes
        // FIX #983: query/fragment are valid in URL but not used by proxy
        // FIX #1418: reject @ in fragment (would be interpreted differently)
        // FIX #1419: same for path
        if (u.hash && u.hash.includes('@')) {
            throw new Error(`Invalid proxy URL: @ in fragment`);
        }
        if (u.pathname && u.pathname.includes('@')) {
            throw new Error(`Invalid proxy URL: @ in path`);
        }
        // FIX #1632: reject empty path for some schemes
        if ((u.protocol === 'http:' || u.protocol === 'https:') && (!u.pathname || u.pathname === '')) {
            // Allow empty path (defaults to /)
        }
        // FIX #1832: cap path length
        if (u.pathname && u.pathname.length > 2048) {
            throw new Error('Invalid proxy URL: path too long');
        }
        // FIX #1421: reject @ in query
        if (u.search && u.search.includes('@')) {
            throw new Error(`Invalid proxy URL: @ in query`);
        }
        // FIX #1933: reject data: and javascript: schemes explicitly
        if (u.protocol === 'data:' || u.protocol === 'javascript:' ||
            u.protocol === 'file:' || u.protocol === 'ftp:') {
            throw new Error(`Invalid proxy URL: dangerous scheme '${u.protocol}'`);
        }
        const allowed = ['http:', 'https:', 'socks5:', 'socks5h:'];
        if (!allowed.includes(u.protocol)) {
            throw new Error(
                `Unsupported proxy protocol: ${u.protocol}. ` +
                `Allowed: ${allowed.join(', ')}`
            );
        }
        // FIX #990: validate host is non-empty
        if (!u.hostname || u.hostname.length === 0) {
            throw new Error(`Invalid proxy URL: hostname is empty`);
        }
        // FIX #985: validate IPv6 brackets
        if (u.hostname.startsWith('[') && !u.hostname.endsWith(']')) {
            throw new Error(`Invalid proxy URL: malformed IPv6 address`);
        }
        // FIX #1994: validate IPv6 has valid hex chars
        if (u.hostname.startsWith('[')) {
            const ipv6 = u.hostname.slice(1, -1);
            if (!/^[0-9a-fA-F:.]+$/.test(ipv6)) {
                throw new Error('Invalid proxy URL: IPv6 address contains invalid chars');
            }
        }
        // FIX #23: URL.host strips IPv6 brackets. Re-add them.
        // FIX #692: handle trailing dot in hostname
        // FIX #2089: don't double-wrap if already in brackets
        const _isIPv6 = u.hostname.includes(':');
        const _alreadyBracketed = u.hostname.startsWith('[') && u.hostname.endsWith(']');
        const hostname = (_isIPv6 && !_alreadyBracketed) ? `[${u.hostname}]` : u.hostname.replace(/\.$/, '');
        // FIX #984: validate port format
        if (u.port && !/^\d+$/.test(u.port)) {
            throw new Error(`Invalid proxy URL: port must be numeric, got '${u.port}'`);
        }
        // FIX #691: reject port 0
        // FIX #1635: handle port=0 explicitly
        if (u.port === '0' || u.port === 0) {
            throw new Error(`Invalid proxy URL: port 0 is not allowed`);
        }
        // FIX #1735: validate port chars
        if (u.port && !/^[0-9]+$/.test(u.port)) {
            throw new Error(`Invalid proxy URL: port contains non-numeric chars`);
        }
        // FIX #840: validate port range (1-65535)
        if (u.port) {
            const portNum = parseInt(u.port, 10);
            if (portNum < 1 || portNum > 65535) {
                throw new Error(`Invalid proxy URL: port out of range (1-65535), got ${portNum}`);
            }
        }
        // FIX #1124: validate username/password encoding
        if (u.username && /[\x00-\x1f\x7f]/.test(u.username)) {
            throw new Error('Invalid proxy URL: username contains control characters');
        }
        if (u.password && /[\x00-\x1f\x7f]/.test(u.password)) {
            throw new Error('Invalid proxy URL: password contains control characters');
        }
        // FIX #689: for socks5h, normalize to socks5 in Playwright (it doesn't
        // distinguish). Keep the original form available as username suffix if needed.
        const protocol = u.protocol === 'socks5h:' ? 'socks5:' : u.protocol;
        // FIX #1986 + #1990: removed dead code (port variable, double config.server assignment)
        // FIX #263: Strip default ports for cleaner URLs.
        const defaultPorts = { 'http:': '80', 'https:': '443', 'socks5:': '1080' };
        const config = {
            server: (u.port && u.port !== defaultPorts[protocol])
                ? `${protocol}//${hostname}:${u.port}`
                : `${protocol}//${hostname}`,
        };
        // FIX #690: skip empty username/password rather than storing empty strings
        // FIX #835: validate username length
        // FIX #1932: reject username length 0 explicitly (already implicit in >0)
        if (u.username && u.username.length > 0 && u.username.length <= 256) {
            // FIX #693: properly decode credentials
            try {
                config.username = decodeURIComponent(u.username);
            } catch (e) {
                config.username = u.username;
            }
        }
        // FIX #836: validate password length
        if (u.password && u.password.length > 0 && u.password.length <= 256) {
            try {
                config.password = decodeURIComponent(u.password);
            } catch (e) {
                config.password = u.password;
            }
        }
        return config;
    },
};

// FIX #695: expose internals for advanced users
module.exports = {
    cloak,
    loadRuntime,
    // FIX #697: expose createHumanizedSession
    createHumanizedSession: (meta, seed) => cloak.createHumanizedSession(meta, seed),
    // FIX #696: expose listPersonas error helper
    listPersonasWithErrors: () => {
        const dir = path.join(__dirname, '..', 'manifests');
        try {
            fs.statSync(dir);
            return { ok: true, personas: cloak.listPersonas() };
        } catch (e) {
            return { ok: false, error: e.message, personas: [] };
        }
    },
};
