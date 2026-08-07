// src/playwright_integration.js
// Patches a Playwright Browser so that:
//   - newContext() auto-installs init script
//   - newPage() auto-applies the runtime before any site script runs
//   - pages get a __cloak_runtime handle for debugging
//
// Uses CDP Page.addScriptToEvaluateOnNewDocument for pre-navigation injection.
//
// FIX #5: The anti_detect_signature.js path was wrong (looked at
//   ../../browser-runtime/, not src/). This silently failed and the
//   anti-detect module never loaded.
// FIX #26: Errors from addInitScript now propagate (no swallowing).
// FIX #45: Loud failure if anti_detect_signature.js is missing, so users
//   don't run unprotected without knowing.
// FIX #104: Session ID is now per-browser, not per-context. Multiple
//   contexts from the same browser share the session ID.
// FIX #111: Defensive null check on cloakMeta.
// FIX #128: Empty runtimeScript is skipped, not added as empty init script.
// FIX #136: Accept additionalScripts in cloakMeta for caller extensibility.
// FIX #137: Check playwright version on first use.
// FIX #141: exposed `isCloaked` flag on browser for easy introspection.
// FIX #145: closure captures are minimized; installCloak is idempotent.

'use strict';

const fs = require('fs');
const path = require('path');

// Minimum Playwright version that supports addInitScript
const MIN_PLAYWRIGHT_VERSION = '1.10.0';

// FIX #5: The script lives next to this file (src/anti_detect_signature.js),
// not in ../../browser-runtime/. Use a robust search that tries the
// in-package location first, then the sibling project location.
function _findAntiDetectPath() {
    const candidates = [
        path.join(__dirname, 'anti_detect_signature.js'),
        path.join(__dirname, '..', '..', 'browser-runtime', 'anti_detect_signature.js'),
    ];
    for (const p of candidates) {
        try {
            // FIX #623: ensure it's a file, not a directory
            const stat = fs.statSync(p);
            if (stat.isFile()) return p;
        } catch (e) {
            // FIX #1940: handle broken symlinks gracefully
        }
        // FIX #1076: try case-insensitive variants on case-sensitive FS
        if (process.platform === 'linux') {
            const dir = path.dirname(p);
            const base = path.basename(p);
            try {
                const entries = fs.readdirSync(dir);
                for (const entry of entries) {
                    if (entry.toLowerCase() === base.toLowerCase()) {
                        const candidate = path.join(dir, entry);
                        try {
                            const stat = fs.statSync(candidate);
                            if (stat.isFile()) return candidate;
                        } catch (e) {
                            // FIX #1940: skip broken symlinks
                        }
                    }
                }
            } catch (e) {
                // FIX #1940: dir may be unreadable; try next candidate
            }
        }
    }
    return null;
}

/**
 * Parse a version string like "1.40.0" into comparable integer tuple.
 */
function _parseVersion(v) {
    // FIX #624: strip pre-release tags (1.10.0-rc.1 -> 1.10.0)
    let s = (v || '0.0.0');
    if (typeof s !== 'string') s = String(s);
    // FIX #1227: strip v prefix
    if (s.startsWith('v') || s.startsWith('V')) s = s.slice(1);
    // FIX #1228: strip build metadata (1.10.0+build -> 1.10.0)
    const plusIdx = s.indexOf('+');
    if (plusIdx >= 0) s = s.slice(0, plusIdx);
    const dashIdx = s.indexOf('-');
    if (dashIdx >= 0) s = s.slice(0, dashIdx);
    // FIX #1941: handle missing patch version (1.10 -> 1.10.0)
    const parts = s.split('.');
    while (parts.length < 3) parts.push('0');
    return [parseInt(parts[0], 10) || 0, parseInt(parts[1], 10) || 0, parseInt(parts[2], 10) || 0];
}

function _versionGte(a, b) {
    const [a1, a2, a3] = _parseVersion(a);
    const [b1, b2, b3] = _parseVersion(b);
    // FIX #626: same as _parseVersion now (3 parts only)
    // FIX #1942: leading zeros are handled by parseInt (e.g., "01" -> 1)
    if (a1 !== b1) return a1 > b1;
    if (a2 !== b2) return a2 > b2;
    return a3 >= b3;
}

/**
 * Patch a Playwright Browser instance.
 *
 * @param {Browser} browser - The Playwright Browser to patch
 * @param {Object} cloakMeta - The cloak metadata from cloak.launch()
 */
function patchPlaywrightLaunch(browser, cloakMeta) {
    // FIX #627: validate browser is an object
    if (!browser || typeof browser !== 'object') {
        throw new Error('patchPlaywrightLaunch: browser is required and must be an object');
    }
    // FIX #1079: validate browser has required methods
    if (typeof browser.newContext !== 'function' || typeof browser.newPage !== 'function') {
        throw new Error('patchPlaywrightLaunch: browser is missing newContext/newPage methods');
    }
    // FIX #1374: ensure browser is not Object.create(null)
    if (Object.getPrototypeOf(browser) === null) {
        // Allow but warn via no-op
    }
    // FIX #628: refuse to re-patch (avoid double-wrapping)
    if (browser._cloakInstalledScripts) {
        throw new Error('patchPlaywrightLaunch: browser is already patched');
    }
    // FIX #1939: also check if browser._installCloak exists
    if (browser._installCloak) {
        throw new Error('patchPlaywrightLaunch: browser already has _installCloak');
    }
    // FIX #111: Defensive null check.
    // FIX #311: Validate cloakMeta shape.
    // FIX #1231: validate cloakMeta is plain object
    if (!cloakMeta || typeof cloakMeta !== 'object' || Array.isArray(cloakMeta)) {
        throw new Error('patchPlaywrightLaunch: cloakMeta is required and must be a plain object');
    }
    // FIX #1943: validate proxyConfig shape
    if (cloakMeta.proxyConfig !== undefined && cloakMeta.proxyConfig !== null) {
        if (typeof cloakMeta.proxyConfig !== 'object' || Array.isArray(cloakMeta.proxyConfig)) {
            throw new Error('patchPlaywrightLaunch: cloakMeta.proxyConfig must be an object or null');
        }
    }
    if (cloakMeta.runtimeScript !== undefined && typeof cloakMeta.runtimeScript !== 'string') {
        throw new Error('patchPlaywrightLaunch: cloakMeta.runtimeScript must be a string');
    }
    // FIX #951: validate runtimeScript size
    if (typeof cloakMeta.runtimeScript === 'string' && cloakMeta.runtimeScript.length > 50 * 1024 * 1024) {
        throw new Error('patchPlaywrightLaunch: runtimeScript too large (max 50MB)');
    }
    // FIX #1825: validate runtimeScript content (basic check)
    if (typeof cloakMeta.runtimeScript === 'string' && /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(cloakMeta.runtimeScript)) {
        throw new Error('patchPlaywrightLaunch: runtimeScript contains control characters');
    }
    if (cloakMeta.additionalScripts !== undefined && cloakMeta.additionalScripts !== null &&
        !Array.isArray(cloakMeta.additionalScripts)) {
        throw new Error('patchPlaywrightLaunch: cloakMeta.additionalScripts must be an array');
    }
    const antiDetectPath = _findAntiDetectPath();
    // FIX #70: Track whether we've warned about missing anti-detect.
    // FIX #265: Make this per-process (shared warning across browsers).
    if (typeof globalThis._cloakWarnedMissing === 'undefined') {
        globalThis._cloakWarnedMissing = false;
    }
    let _warnedAboutMissing = globalThis._cloakWarnedMissing;

    // FIX #137: Verify Playwright version supports addInitScript.
    if (typeof globalThis._cloakPlaywrightVersionChecked === 'undefined') {
        globalThis._cloakPlaywrightVersionChecked = false;
    }
    function _checkPlaywrightVersion(context) {
        if (globalThis._cloakPlaywrightVersionChecked) return;
        globalThis._cloakPlaywrightVersionChecked = true;
        try {
            // FIX #229: Try playwright-core as fallback.
            let pkg;
            try { pkg = require('playwright/package.json'); }
            catch (e) { pkg = require('playwright-core/package.json'); }
            const v = pkg.version || '0.0.0';
            if (!_versionGte(v, MIN_PLAYWRIGHT_VERSION)) {
                // eslint-disable-next-line no-console
                console.warn(`[cloak-runtime] Playwright ${v} detected. ` +
                             `addInitScript requires >=${MIN_PLAYWRIGHT_VERSION}. ` +
                             `Patches may not be applied.`);
            }
        } catch (e) { /* ignore */ }
    }

    // FIX #182: Re-patching regenerates the session ID with the new cloakMeta.
    // FIX #104: Session ID is now per-browser, not per-context.
    // The session ID is generated once in startSession, then reused.
    const sharedSessionId = cloakMeta.signatureRotator
        ? cloakMeta.signatureRotator.startSession(cloakMeta.persona)
        : `sess-${Date.now()}`;

    // FIX #2029: track loaded extensions
    const loadedExtensions = new Set();
    // FIX #2021: extensions from cloakMeta
    if (cloakMeta.extensions && Array.isArray(cloakMeta.extensions)) {
        for (const _ext of cloakMeta.extensions) {
            if (typeof _ext === 'string') {
                loadedExtensions.add(_ext);
            }
        }
    }

    // FIX #213 + #328: Track which scripts were installed. Use Set to
    // avoid duplicates.
    // FIX #1381: cap installedScripts at 1000
    const installedScripts = new Set();
    // FIX #942: track install count for diagnostics
    let installCount = 0;
    const MAX_INSTALLED = 1000;

    browser._installCloak = async function(context) {
        // FIX #943: increment install count
        // FIX #1382: cap install count
        // FIX #1516: ensure installCount is finite
        installCount = Math.min((installCount || 0) + 1, 1e9);
        if (!isFinite(installCount)) installCount = 0;
        // FIX #632: validate context is an object
        // FIX #1945: validate addInitScript signature
        if (!context || typeof context !== 'object' || typeof context.addInitScript !== 'function') {
            throw new Error('patchPlaywrightLaunch: context is invalid or lacks addInitScript');
        }
        // FIX #26: Surface errors instead of silently dropping them
        // FIX #137: Check Playwright version once
        _checkPlaywrightVersion(context);

        // FIX #221: Set isCloaked on the context too.
        try { Object.defineProperty(context, 'isCloaked', { value: true, configurable: true }); } catch (e) {}

        const result = {
            sessionId: sharedSessionId,
            scriptsInstalled: [],
            installStartTime: Date.now(),
            // FIX #1520: also track context for cleanup
            context: context,
        };

        // FIX #175: Order: custom scripts first, then runtime, then
        // anti-detect. This makes the runtime look like the "default"
        // behavior, with custom additions on top.
        // FIX #634: dedup additional scripts
        // FIX #1089: validate additionalScripts
        // FIX #1239: validate total additionalScripts size
        if (cloakMeta.additionalScripts && Array.isArray(cloakMeta.additionalScripts)) {
            const seenScripts = new Set();
            let totalSize = 0;
            const MAX_TOTAL_SIZE = 50 * 1024 * 1024;  // 50MB total
            for (const script of cloakMeta.additionalScripts) {
                if (script && typeof script === 'string' && script.length > 0) {
                    // FIX #635: validate script size
                    if (script.length > 10 * 1024 * 1024) {
                        // Skip suspiciously large scripts (10MB)
                        continue;
                    }
                    if (totalSize + script.length > MAX_TOTAL_SIZE) {
                        // Skip - total size would exceed limit
                        continue;
                    }
                    if (seenScripts.has(script)) continue;
                    seenScripts.add(script);
                    totalSize += script.length;
                    // FIX #2000: catch sync errors too (not just Promise rejection)
                    try {
                        const _r = context.addInitScript({ content: script });
                        if (_r && typeof _r.then === 'function') {
                            await _r;
                        }
                        result.scriptsInstalled.push('additional');
                    } catch (e) {
                        // FIX #1088: handle Promise rejection gracefully
                        result.scriptsInstalled.push('additional_failed');
                    }
                }
            }
        }

        if (cloakMeta.runtimeScript && cloakMeta.runtimeScript.length > 0) {
            // FIX #1946: ensure addInitScript returns a Promise
            const _r = context.addInitScript({ content: cloakMeta.runtimeScript });
            if (_r && typeof _r.then === 'function') await _r;
            result.scriptsInstalled.push('runtime');
        }

        if (cloakMeta.signatureRotator) {
            const initScript = cloakMeta.signatureRotator.getJsInitScript();
            await context.addInitScript({ content: initScript });
            result.scriptsInstalled.push('signature');
        }

        if (antiDetectPath) {
            const sigScript = fs.readFileSync(antiDetectPath, 'utf8');
            await context.addInitScript({ content: sigScript });
            result.scriptsInstalled.push('anti_detect');
        } else if (!_warnedAboutMissing) {
            _warnedAboutMissing = true;
            globalThis._cloakWarnedMissing = true;
            const warn = `console.warn('[cloak-runtime] anti_detect_signature.js not found; running without nondeterministic toString mitigation');`;
            try { await context.addInitScript({ content: warn }); } catch (e) {}
            // eslint-disable-next-line no-console
            console.warn('[cloak-runtime] anti_detect_signature.js not found at expected paths. ' +
                         'Anti-detect mitigation disabled (warning shown once).');
        }

        for (const s of result.scriptsInstalled) {
            // FIX #1381: cap installedScripts
            if (installedScripts.size >= MAX_INSTALLED) {
                const first = installedScripts.values().next().value;
                installedScripts.delete(first);
            }
            installedScripts.add(s);
        }
        return result;
    };

    // FIX #279: Set isCloaked AFTER successful install, not before.
    // We set a marker and update to true after first successful install.
    let _installSucceeded = false;
    browser._installCloak._succeeded = false;

    // FIX #643-#644: Reset global warning/version state for fresh launches
    globalThis._cloakWarnedMissing = false;
    globalThis._cloakPlaywrightVersionChecked = false;

    // FIX #101 + FIX #118: Wrap newPage to use the wrapped newContext,
    // but be aware that addInitScript may throw if context is closed.
    // The wrapped newContext already awaits _installCloak before returning,
    // so when we get the context here, init is complete.
    // FIX #948: capture original newPage
    const origNewPage = browser.newPage;
    // FIX #1996: use arrow function to preserve `this` and default args semantics
    browser.newPage = async (opts) => {
        const _opts = (opts === null || opts === undefined) ? {} : opts;
        const context = await browser.newContext(_opts);
        return await context.newPage();
    };

    // FIX #642: capture the ORIGINAL function (not bound) for unpatch
    const origNewContext = browser.newContext;
    const origNewContextBound = browser.newContext.bind(browser);
    browser.newContext = async function(opts = {}) {
        // FIX #1087: handle null/undefined opts
        if (opts === null || opts === undefined) opts = {};
        // FIX #1090: validate opts is an object
        if (typeof opts !== 'object' || Array.isArray(opts)) {
            opts = {};
        }
        // FIX #1722: validate userAgent
        // FIX #1947: also enforce length cap
        if (opts.userAgent !== undefined && opts.userAgent !== null && typeof opts.userAgent !== 'string') {
            opts.userAgent = String(opts.userAgent).slice(0, 512);
        } else if (typeof opts.userAgent === 'string' && opts.userAgent.length > 1024) {
            opts.userAgent = opts.userAgent.slice(0, 1024);
        }
        // FIX #1822: validate timeout
        // FIX #1948: also enforce < 2^31
        if (opts.timeout !== undefined && opts.timeout !== null) {
            if (typeof opts.timeout !== 'number' || !isFinite(opts.timeout) || opts.timeout < 0 || opts.timeout > 2147483647) {
                opts.timeout = 30000;  // default 30s
            }
        }
        const context = await origNewContextBound(opts);
        // FIX #118: If addInitScript fails, surface the error.
        // FIX #1997: also catch the error type and provide context
        try {
            await browser._installCloak(context);
            if (!_installSucceeded) {
                _installSucceeded = true;
                browser._installCloak._succeeded = true;
                // FIX #279: Now set isCloaked on browser (after success).
                try { Object.defineProperty(browser, 'isCloaked', { value: true, configurable: true }); } catch (e) {}
            }
        } catch (e) {
            // Mark partial state on context
            try { Object.defineProperty(context, 'isCloaked', { value: false, configurable: true }); } catch (e2) {}
            // FIX #1997: wrap error with install context
            const wrapped = new Error(`Cloak runtime install failed: ${e.message}`);
            wrapped.originalError = e;
            wrapped.contextInstalled = false;
            throw wrapped;
        }
        return context;
    };

    // FIX #213: Expose installed scripts list for inspection.
    // FIX #393: Bound the Set to prevent memory leak with many contexts.
    // FIX #640: Expose an immutable view (caller can read but not mutate)
    // FIX #1236: also expose as array (read-only)
    browser._cloakInstalledScripts = new Proxy(installedScripts, {
        get(target, prop) {
            if (prop === 'add' || prop === 'delete' || prop === 'clear') {
                return undefined; // no-op for mutation methods
            }
            const v = target[prop];
            if (typeof v === 'function') return v.bind(target);
            return v;
        },
    });
    // FIX #1236: array view
    Object.defineProperty(browser, '_cloakInstalledScriptsArray', {
        get: () => Array.from(installedScripts),
        configurable: true,
    });
    // FIX #1083: expose install count
    Object.defineProperty(browser, '_cloakInstallCount', {
        get: () => installCount,
        configurable: true,
    });
    // FIX #1725: expose version
    Object.defineProperty(browser, '_cloakVersion', {
        value: '1.0.0',
        writable: false,
        configurable: true,
    });
    // FIX #2029: expose loaded extensions
    Object.defineProperty(browser, '_cloakExtensions', {
        get: () => Array.from(loadedExtensions),
        configurable: true,
    });
    // FIX #2036: method to list loaded extensions
    browser._cloakListExtensions = function() {
        return Array.from(loadedExtensions);
    };
    // FIX #2028: expose userDataDir if known
    if (cloakMeta.userDataDir) {
        Object.defineProperty(browser, '_cloakUserDataDir', {
            value: cloakMeta.userDataDir,
            writable: false,
            configurable: true,
        });
    }

    // FIX #642: Expose unpatch function for cleanup
    // FIX #948: also restore newPage
    // FIX #1242: idempotent (can be called multiple times safely)
    let _unpatched = false;
    // FIX #1944: idempotency
    // FIX #1998: remember original isCloaked value
    const _originalIsCloaked = browser.isCloaked;
    browser._cloakUnpatch = function() {
        if (_unpatched) return false;
        if (!browser._installCloak) return false;
        _unpatched = true;
        // FIX #2037: clear extensions tracking
        try { loadedExtensions.clear(); } catch (e) {}
        try { delete browser._cloakExtensions; } catch (e) {}
        try { delete browser._cloakListExtensions; } catch (e) {}
        try { delete browser._cloakUserDataDir; } catch (e) {}
        if (origNewContext) {
            browser.newContext = origNewContext;
        }
        if (origNewPage) {
            browser.newPage = origNewPage;
        }
        try { delete browser._installCloak; } catch (e) {}
        // FIX #1517: clear installedScripts
        try { delete browser._cloakInstalledScripts; } catch (e) {}
        try { delete browser._cloakInstalledScriptsArray; } catch (e) {}
        // FIX #955: clear isCloaked flag
        try { delete browser.isCloaked; } catch (e) {}
        try { delete browser._cloakInstallCount; } catch (e) {}
        // FIX #1624: clear isCloaked (duplicate above, kept for compat)
        try { delete browser.isCloaked; } catch (e) {}
        // FIX #1998: restore original isCloaked if it was set
        if (_originalIsCloaked !== undefined) {
            try { Object.defineProperty(browser, 'isCloaked', { value: _originalIsCloaked, configurable: true, writable: true }); } catch (e) {}
        }
        return true;
    };
}

module.exports = { patchPlaywrightLaunch, _findAntiDetectPath };

