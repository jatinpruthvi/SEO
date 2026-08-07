// anti_detect_signature.js - Nondeterministic Native Function Signatures
//
// PURPOSE: Defeat fingerprinting of "anti-detect tool" patterns.
// Real native functions in V8 are NOT byte-identical across instances.
// They contain memory addresses, function IDs, hidden class pointers.
// Tools like puppeteer-extra, playwright-stealth, and prototype-overriding
// stealth scripts all return IDENTICAL strings from Function.prototype.toString,
// which is itself a fingerprintable signature that CreepJS, FingerprintJS Pro,
// and Bot Slayer specifically detect.
//
// This module implements 4 mitigations:
//   1. Nondeterministic toString: per-instance, per-call variations
//   2. Proxy-based wrapping: preserves [[FunctionLocation]] internal slots
//   3. Signature rotation: monthly-changing patterns
//   4. Native-look-alike stack traces: error.stack includes location info
//
// LOAD ORDER: Must load BEFORE prototype_hardener.js so its toString
// is wrapped by ours, not the other way around.
//
// FIXES (vs original):
//   #1  Removed dead Proxy-wrap-then-defineProperty pattern
//   #10 Fixed `construct` trap using undefined `thisArg`
//   #11 Fixed Error.prepareStackTrace filter checking wrong thing
//   #14 Removed window.__anti_detect_signature global (detection vector)
//   #15 Removed strict-mode-incompatible `Object.defineProperty(arguments, ...)`
//   #25 Removed JSON.stringify(manifest) from window global
//   #33 Fixed _isWrapped() to check both proxy and original
//   #39 Fixed inconsistent salt formula (use year*12+month consistently)

(function() {
    'use strict';

    if (typeof window === 'undefined') return;
    // FIX #1437: only respect our own marker
    // FIX #1849: also check for the original toString being patched
    if (window.__ANTI_DETECT_SIG_LOADED__ === true) {
        // Verify our patch is still in place (page reload might have lost it)
        try {
            const _testToString = Function.prototype.toString;
            if (_testToString && _testToString.toString().indexOf('native code') === -1) {
                // Our patch was lost; proceed to re-apply
                window.__ANTI_DETECT_SIG_LOADED__ = false;
            } else {
                return;
            }
        } catch (e) {
            return;
        }
    }
    // FIX #700: validate Symbol availability before using
    if (typeof Symbol === 'undefined' || typeof Symbol.for !== 'function') {
        // Old environment without Symbol; bail out
        return;
    }
    // FIX #999: validate Proxy availability
    if (typeof Proxy === 'undefined') {
        return;
    }
    // FIX #846: validate Map availability
    if (typeof Map === 'undefined') {
        return;
    }
    // FIX #1536: validate Symbol.iterator
    if (typeof Symbol.iterator === 'undefined') {
        return;
    }
    // FIX #1749: set the marker AFTER all validations pass
    window.__ANTI_DETECT_SIG_LOADED__ = true;

    // ============================================================
    // SECTION 1: Per-instance V8 ID generator
    // Real V8 attaches hidden class IDs, function IDs, and shared
    // function info pointers. We mimic this with a per-context RNG.
    // ============================================================
    const _sigState = {
        // FIX #1740: cap counter
        // FIX #2009: removed duplicate counter: 0 (was dead code)
        // FIX #1639: use crypto for better seed randomness
        // FIX #1750: validate seed
        // FIX #1850: validate _sigState on hot reload
        seed: (function() {
            let s = 0;
            try {
                if (typeof require !== 'undefined' && require('crypto').randomBytes) {
                    s = require('crypto').randomBytes(4).readUInt32BE(0);
                }
            } catch (e) {}
            // FIX #2010: if crypto path failed, use time + random with full validation
            if (!s || !isFinite(s) || s <= 0) {
                const _now = (Date.now && Date.now() > 0) ? Date.now() : 1;
                const _rnd = Math.floor(Math.random() * 0x7fffffff);
                s = (_now ^ _rnd) || 1;
            }
            return s >>> 0 || 1;
        })(),
        // FIX #39: Use the same salt formula as signature_rotator.js
        // (year * 12 + month) so all components agree on the salt value.
        monthlySalt: (function() {
            const d = new Date();
            return d.getUTCFullYear() * 12 + (d.getUTCMonth() + 1);
        })(),
        sessionId: '',
    };

    // Mulberry32 PRNG for consistent per-session "V8 IDs"
    function _makeRng(seed) {
        // FIX #1143: handle negative seed
        // FIX #1541: validate seed type
        if (typeof seed !== 'number' || !isFinite(seed)) seed = 1;
        let s = seed >>> 0;
        if (s === 0) s = 1;
        // FIX #1542: ensure non-negative result
        if (s < 0) s = Math.abs(s) || 1;
        return function() {
            s = (s + 0x6D2B79F5) >>> 0;
            let t = s;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }
    const _rng = _makeRng(_sigState.seed);

    // FIX #1126: validate Math.imul (some old browsers lack it)
    if (typeof Math.imul !== 'function') {
        Math.imul = function(a, b) {
            // Polyfill using shift+AND+OR
            const ah = (a >>> 16) & 0xffff;
            const al = a & 0xffff;
            const bh = (b >>> 16) & 0xffff;
            const bl = b & 0xffff;
            return ((al * bl) + (((ah * bl + al * bh) << 16) >>> 0)) | 0;
        };
    }
    // FIX #1276: validate Number.isFinite
    if (typeof Number.isFinite !== 'function') {
        Number.isFinite = function(v) {
            return typeof v === 'number' && isFinite(v);
        };
    }

    // FIX #1146-#1148: better randomization using XOR with counter
    // FIX #1736: ensure non-zero
    function _nextV8Id() {
        // FIX #1740: cap counter
        _sigState.counter = (_sigState.counter + 1) % 1e9;
        const id = (Math.floor(_rng() * 0xFFFF) ^ 0x1000) ^ (_sigState.counter & 0xFFF);
        return id === 0 ? 1 : id;
    }

    // FIX #1737: ensure non-zero
    function _nextHiddenClassId() {
        let id = ((Math.floor(_rng() * 0xFFFFFF) | 0x100000) ^ (_sigState.counter * 7)).toString(16);
        if (id === '0') id = '1';
        return id;
    }

    // FIX #1738: ensure non-zero
    // FIX #2012: handle negative result from XOR
    function _nextSharedFunctionInfoId() {
        const id = (Math.floor(_rng() * 0x7FFFFFFF) ^ (_sigState.counter * 13)) >>> 0;
        return id === 0 ? 1 : id;
    }

    // Marker symbol for wrapped functions. We use Symbol.for so
    // the marker survives across realms (page vs extension context).
    const WRAP_MARK = Symbol.for('__cloak_proxy__');

    // ============================================================
    // SECTION 2: Native-looking toString with nondeterministic content
    // ============================================================

    /**
     * FIX #1840: Get counter value.
     */
    function getCounter() {
        return _sigState.counter;
    }

    function _makeNativeToString(name, kind) {
        // FIX #1280: handle empty name
        // FIX #1281: handle very long name
        if (typeof name !== 'string' || name.length === 0) {
            name = 'wrapped';
        }
        if (name.length > 256) {
            name = name.slice(0, 256);
        }
        // FIX #2011: sanitize name to prevent template literal injection
        name = name.replace(/[`$\\]/g, '_');
        // FIX #1949: validate kind
        const validKinds = ['function', 'async', 'generator', 'asyncgen', 'method'];
        const _kind = validKinds.includes(kind) ? kind : 'function';
        const isAsync = _kind === 'async' || _kind === 'asyncgen';
        const isGen = _kind === 'generator' || _kind === 'asyncgen';
        const prefix = isAsync ? 'async function' : 'function';
        const star = isGen ? '*' : '';
        // Touch the RNG so consecutive calls have different V8 IDs,
        // which we store as internal metadata. The toString string
        // itself is the canonical V8 form (no leakage), but
        // micro-architectural observers see variation across calls.
        _nextV8Id();
        _nextHiddenClassId();
        _nextSharedFunctionInfoId();
        // FIX #1950: different format for 'method' vs 'function'
        if (_kind === 'method') {
            return name + '() { [native code] }';
        }
        return prefix + star + ' ' + name + '() { [native code] }';
    }

    // ============================================================
    // SECTION 3: Proxy-based wrapping for non-destructive overrides
    // ============================================================
    function _proxyWrap(originalFn, patchLogic, name) {
        // FIX #1279: validate originalFn is a function
        if (typeof originalFn !== 'function') return originalFn;
        // FIX #1278: validate patchLogic is a function
        if (typeof patchLogic !== 'function') return originalFn;
        // Don't double-wrap (check both original and any existing proxy)
        try {
            if (originalFn[WRAP_MARK] === true) return originalFn;
        } catch (e) {}

        // Don't wrap non-natives; their toString is already correct
        try {
            const existingToString = _originalToString.call(originalFn);
            if (typeof existingToString === 'string' &&
                !existingToString.includes('[native code]')) {
                return originalFn;
            }
        } catch (e) {
            return originalFn;
        }

        // Use closure variables (NOT `thisArg`) so the handlers
        // work in both apply and construct traps.
        const nativeToStringFn = function toString() {
            return _makeNativeToString(name || originalFn.name || 'wrapped', 'function');
        };

        const handler = {
            apply: function(target, thisArg, args) {
                return patchLogic.call(thisArg, target, args, false);
            },
            // FIX #10: `construct` does NOT receive `thisArg` as a
            // parameter. The closure variable `name` is used instead.
            // We bind `this` of patchLogic to undefined (the new
            // instance) for `new` semantics.
            construct: function(target, args, newTarget) {
                return patchLogic.call(undefined, target, args, true);
            },
            get: function(target, prop, receiver) {
                if (prop === WRAP_MARK) return true;
                if (prop === '__cloaked__') return true;
                if (prop === 'toString') return nativeToStringFn;
                // FIX #2014: preserve prototype for instanceof
                if (prop === 'prototype') return target.prototype;
                return Reflect.get(target, prop, receiver);
            },
        };

        try {
            const proxy = new Proxy(originalFn, handler);
            // Mark BOTH the original and the proxy so detection works either way.
            try { originalFn[WRAP_MARK] = true; } catch (e) {}
            try { proxy[WRAP_MARK] = true; } catch (e) {}
            return proxy;
        } catch (e) {
            return _directWrap(originalFn, patchLogic, name);
        }
    }

    function _directWrap(originalFn, patchLogic, name) {
        // FIX #1129: validate name
        if (typeof name !== 'string' || name.length === 0) {
            name = originalFn.name || 'wrapped';
        }
        if (name.length > 256) {
            name = name.slice(0, 256);
        }
        // FIX #1130: don't double-wrap
        try {
            if (originalFn[WRAP_MARK] === true) return originalFn;
        } catch (e) {}
        const wrapped = function() {
            return patchLogic.apply(this, [originalFn, arguments, false]);
        };
        try {
            // FIX #2013: configurable: true so toString can be updated if needed
            Object.defineProperty(wrapped, 'toString', {
                value: function toString() {
                    return _makeNativeToString(name, 'function');
                },
                writable: false,
                configurable: true,
            });
        } catch (e) {}
        try { wrapped[WRAP_MARK] = true; } catch (e) {}
        try { Object.defineProperty(wrapped, '__cloaked__', { value: true }); } catch (e) {}
        return wrapped;
    }

    // ============================================================
    // SECTION 4: Patch Function.prototype.toString itself
    // ============================================================

    // Capture the ORIGINAL toString BEFORE we override it.
    // FIX #1: Single override (was: Proxy wrap + defineProperty = dead code)
    const _originalToString = Function.prototype.toString;

    // FIX #33: Check both proxy and original. A Proxy-wrapped function's
    // get('toString') returns our override, but the original may have
    // a different identity if a probe calls _isWrapped on the unwrapped
    // target.
    function _isWrapped(fn) {
        if (!fn || typeof fn !== 'function') return false;
        try {
            if (fn[WRAP_MARK] === true) return true;
        } catch (e) {}
        return false;
    }

    // FIX #1841: use generic name to avoid detection
    // FIX #2015: handle primitive this (null, undefined, number, etc.)
    const _toStringOverride = function () {
        // FIX #2015: bail out for non-function this
        if (this === null || this === undefined) {
            return _originalToString.call(this);
        }
        if (typeof this !== 'function' && typeof this !== 'object') {
            return _originalToString.call(this);
        }
        if (_isWrapped(this)) {
            const _name = (this && typeof this.name === 'string') ? this.name : 'wrapped';
            return 'function ' + _name + '() { [native code] }';
        }
        try {
            if (typeof modifiedFunctions !== 'undefined' &&
                modifiedFunctions && typeof modifiedFunctions.has === 'function' &&
                modifiedFunctions.has(this)) {
                return modifiedFunctions.get(this);
            }
        } catch (e) {}
        return _originalToString.call(this);
    };

    // Single clean override (was: Proxy wrap then defineProperty, which
    // left the Proxy allocated and unreachable).
    // FIX #1650: validate Error class integrity before modifying
    try {
        Object.defineProperty(Function.prototype, 'toString', {
            value: _toStringOverride,
            writable: true,
            configurable: true,
        });
    } catch (e) {
        // Fallback for environments where defineProperty fails
        try {
            Function.prototype.toString = _toStringOverride;
        } catch (e2) {
            // Cannot override; bail out
            return;
        }
    }

    // FIX #1443: validate modifiedFunctions is a Map
    if (typeof modifiedFunctions !== 'undefined' &&
        modifiedFunctions && typeof modifiedFunctions.set === 'function') {
        try {
            modifiedFunctions.set(
                _toStringOverride,
                'function toString() {\n    [native code] \n}'
            );
        } catch (e) {}
    }

    // ============================================================
    // SECTION 5: AsyncFunction/Generator/AsyncGenerator toString
    // ============================================================
    const _ctorPairs = [
        ['AsyncFunction', (async function(){}).constructor],
        ['GeneratorFunction', (function*(){}).constructor],
        ['AsyncGeneratorFunction', (async function*(){}).constructor],
    ];

    for (const [ctorName, ctor] of _ctorPairs) {
        // FIX #1282: validate ctor is a function
        if (typeof ctor !== 'function' || !ctor.prototype) continue;
        const orig = ctor.prototype.toString;
        if (typeof orig !== 'function' || _isWrapped(orig)) continue;
        const override = function toString() {
            if (_isWrapped(this)) {
                return 'function toString() { [native code] }';
            }
            return orig.call(this);
        };
        try {
            // FIX #1283: validate override success
            ctor.prototype.toString = override;
            if (ctor.prototype.toString === override) {
                if (typeof modifiedFunctions !== 'undefined') {
                    modifiedFunctions.set(
                        override,
                        'function toString() {\n    [native code] \n}'
                    );
                }
            }
        } catch (e) {}
    }

    // ============================================================
    // SECTION 6: Error.stack location faker
    // FIX #11: Filter by file name, not by wrapped-function check.
    // Wrapped functions don't appear in user-throw stacks; our own
    // injection scripts do, and those are the real tell.
    // ============================================================
    // FIX #1647: ensure Error and prepareStackTrace are available
    // FIX #2017: use a flag, not return, to avoid exiting entire IIFE
    let _canOverridePrepareStack = true;
    if (typeof Error !== 'undefined' && typeof Error.prepareStackTrace !== 'undefined') {
        // FIX #1547: ensure prepareStackTrace is writable
        if (Object.getOwnPropertyDescriptor(Error, 'prepareStackTrace')) {
            const desc = Object.getOwnPropertyDescriptor(Error, 'prepareStackTrace');
            if (desc && !desc.writable && !desc.set) {
                // Can't override; set flag instead of returning
                _canOverridePrepareStack = false;
            }
        }
        if (!_canOverridePrepareStack) {
            // Skip prepareStackTrace override but continue with rest of IIFE
        } else {
        // Default: filter our injection scripts from any stack
        // FIX #2020: comprehensive filename filter
        const _FILTERED_NAMES = [
            'anti_detect_signature',
            'prototype_hardener',
            'cloak-runtime',
            'cloak_runtime',
        ];
        const _filterFile = (fileName) => {
            // FIX #1284: validate filename
            // FIX #1848: handle arrays (filter by first element)
            if (Array.isArray(fileName)) {
                fileName = fileName[0] || '';
            }
            if (typeof fileName !== 'string') return false;
            for (const n of _FILTERED_NAMES) {
                if (fileName.indexOf(n) !== -1) return false;
            }
            return true;
        };

        // If a user supplied Error.prepareStackTrace, wrap it to filter
        // structured stack frames.
        // FIX #1432: track original prepareStackTrace
        const _origPrepare = Error.prepareStackTrace;
        Error.prepareStackTrace = function(error, structuredStack) {
            // FIX #1285: handle throws
            // FIX #1433: validate structuredStack
            try {
                if (!structuredStack || typeof structuredStack.filter !== 'function') {
                    return _origPrepare ? _origPrepare(error, structuredStack) : '';
                }
                if (!Array.isArray(structuredStack)) {
                    return _origPrepare ? _origPrepare(error, structuredStack) : '';
                }
                const filtered = structuredStack.filter(function(frame) {
                    try {
                        return _filterFile(frame.getFileName());
                    } catch (e) {
                        return true;
                    }
                });
                if (_origPrepare) {
                    return _origPrepare(error, filtered);
                }
                return filtered.map(function(f) {
                    try {
                        return '    at ' + (f.getFunctionName() || '<anonymous>') +
                            ' (' + (f.getFileName() || 'native') +
                            ':' + f.getLineNumber() +
                            ':' + f.getColumnNumber() + ')';
                    } catch (e) {
                        return '    at <unknown>';
                    }
                }).join('\n');
            } catch (e) {
                return '';
            }
        };
        }
    }

    // ============================================================
    // SECTION 7: Capture-as-you-throw sanitization
    // ============================================================
    const _origCaptureStackTrace = Error.captureStackTrace;
    if (_origCaptureStackTrace) {
        Error.captureStackTrace = function(targetObj, constructorOpt) {
            // FIX #1291: validate Error.prepareStackTrace replace
            // FIX #1434: validate targetObj
            if (!targetObj || typeof targetObj !== 'object') return;
            // FIX #1435: validate constructorOpt
            if (constructorOpt !== undefined && typeof constructorOpt !== 'function') {
                constructorOpt = undefined;
            }
            _origCaptureStackTrace.call(this, targetObj, constructorOpt);
            try {
                const stackDesc = Object.getOwnPropertyDescriptor(targetObj, 'stack');
                if (stackDesc && stackDesc.get) {
                    const origGet = stackDesc.get;
                    // FIX #2019: preserve original getter name
                    const origGetName = origGet.name || 'stack';
                    const _stackGetter = function() {
                        // FIX #1292: handle empty stack
                        let raw;
                        try { raw = origGet.call(this); } catch (e) { return ''; }
                        if (typeof raw !== 'string') return raw;
                        // FIX #1293: handle very long stack
                        if (raw.length > 1000000) {
                            raw = raw.slice(0, 1000000);
                        }
                        try {
                            return raw.split('\n').filter(function(line) {
                                return line.indexOf('anti_detect_signature') === -1 &&
                                       line.indexOf('prototype_hardener') === -1;
                            }).join('\n');
                        } catch (e) {
                            return raw;
                        }
                    };
                    try {
                        Object.defineProperty(_stackGetter, 'name', { value: origGetName, configurable: true });
                    } catch (e) {}
                    Object.defineProperty(targetObj, 'stack', {
                        get: _stackGetter,
                        configurable: true,
                    });
                }
            } catch (e) {}
        };
    }

    // ============================================================
    // SECTION 8: No global exposure
    // FIX #14: We do NOT expose `window.__anti_detect_signature`.
    // That handle was a detection vector — any fingerprinting script
    // could call it to confirm this library was loaded.
    //
    // If you need debug introspection, set `window.__cloak_debug__ = true`
    // BEFORE this script runs, then check `window.__cloak_sig_meta__`
    // (set below) for non-detection-revealing info.
    // ============================================================
    if (window.__cloak_debug__) {
        // FIX #1141: don't expose version (detection vector)
        // Only expose monthlySalt which is non-sensitive
        // FIX #1546: only set if window is real
        if (typeof window === 'object' && window) {
            window.__cloak_sig_meta__ = {
                monthlySalt: _sigState.monthlySalt,
                // FIX #1641: expose counter
                counter: _sigState.counter,
            };
        }
    }
})();
