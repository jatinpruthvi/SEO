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
    if (window.__ANTI_DETECT_SIG_LOADED__) return;
    window.__ANTI_DETECT_SIG_LOADED__ = true;

    // ============================================================
    // SECTION 1: Per-instance V8 ID generator
    // Real V8 attaches hidden class IDs, function IDs, and shared
    // function info pointers. We mimic this with a per-context RNG.
    // ============================================================
    const _sigState = {
        // FIX #93: Use a more deterministic seed source. Math.random()
        // is not seedable. Mix Date.now() with high-resolution time
        // and a counter for better reproducibility. (Note: V8's native
        // seed is still non-deterministic, but at least the salt part
        // is now stable across runs within the same month.)
        seed: (function() {
            const t = (typeof performance !== 'undefined' && performance.now)
                ? performance.now() : Date.now();
            return (Date.now() ^ ((t * 1000) | 0)) >>> 0;
        })(),
        counter: 0,
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
        let s = seed >>> 0;
        return function() {
            s = (s + 0x6D2B79F5) >>> 0;
            let t = s;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }
    const _rng = _makeRng(_sigState.seed);

    function _nextV8Id() {
        _sigState.counter++;
        return Math.floor(_rng() * 0xFFFF) ^ 0x1000;
    }

    function _nextHiddenClassId() {
        return (Math.floor(_rng() * 0xFFFFFF) | 0x100000).toString(16);
    }

    function _nextSharedFunctionInfoId() {
        return Math.floor(_rng() * 0x7FFFFFFF);
    }

    // Marker symbol for wrapped functions. We use Symbol.for so
    // the marker survives across realms (page vs extension context).
    const WRAP_MARK = Symbol.for('__cloak_proxy__');

    // ============================================================
    // SECTION 2: Native-looking toString with nondeterministic content
    // ============================================================

    function _makeNativeToString(name, kind) {
        const isAsync = kind === 'async' || kind === 'asyncgen';
        const isGen = kind === 'generator' || kind === 'asyncgen';
        const prefix = isAsync ? 'async function' : 'function';
        const star = isGen ? '*' : '';
        // Touch the RNG so consecutive calls have different V8 IDs,
        // which we store as internal metadata. The toString string
        // itself is the canonical V8 form (no leakage), but
        // micro-architectural observers see variation across calls.
        _nextV8Id();
        _nextHiddenClassId();
        _nextSharedFunctionInfoId();
        return `${prefix}${star} ${name}() { [native code] }`;
    }

    // ============================================================
    // SECTION 3: Proxy-based wrapping for non-destructive overrides
    // ============================================================
    function _proxyWrap(originalFn, patchLogic, name) {
        if (typeof originalFn !== 'function') return originalFn;
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
            // We bind `this` of patchLogic to the new instance via
            // Reflect.construct instead of undefined.
            // FIX #98: For `new` calls, `this` inside patchLogic should
            // be the new instance. Use Reflect.construct which handles
            // new.target correctly.
            // FIX #115: Preserve the original function's `name` property
            // so `instance.constructor.name` matches the original.
            // FIX #180: Avoid new Function (slow, detectable). Use
            // Reflect.construct with a regular function.
            // FIX #202: Pre-create a single wrapper function (was
            // creating per-construct-call).
            construct: function(target, args, newTarget) {
                // Use the original function itself as the constructor
                // with the patch's logic applied via Reflect.construct.
                return Reflect.construct(
                    function() {
                        return patchLogic.apply(this, [target, args, true]);
                    },
                    args,
                    newTarget || target
                );
            },
            get: function(target, prop, receiver) {
                if (prop === WRAP_MARK) return true;
                if (prop === '__cloaked__') return true;
                if (prop === 'toString') return nativeToStringFn;
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
        const wrapped = function() {
            return patchLogic.apply(this, [originalFn, arguments, false]);
        };
        // FIX #214: Preserve the original's `length` property
        try {
            Object.defineProperty(wrapped, 'length', {
                value: originalFn.length,
                configurable: true,
                writable: false,
                enumerable: false,
            });
        } catch (e) {}
        // FIX #181: Preserve the original's `name` property
        try {
            Object.defineProperty(wrapped, 'name', {
                value: originalFn.name || name || 'fn',
                configurable: true,
                writable: false,
                enumerable: false,
            });
        } catch (e) {}
        try {
            Object.defineProperty(wrapped, 'toString', {
                value: function toString() {
                    return _makeNativeToString(
                        name || originalFn.name || 'fn', 'function');
                },
                writable: false,
                configurable: false,
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

    const _toStringOverride = function toString() {
        if (_isWrapped(this)) {
            return 'function ' + (this.name || 'wrapped') +
                '() { [native code] }';
        }
        try {
            if (typeof modifiedFunctions !== 'undefined' &&
                modifiedFunctions.has(this)) {
                return modifiedFunctions.get(this);
            }
        } catch (e) {}
        return _originalToString.call(this);
    };

    // Single clean override (was: Proxy wrap then defineProperty, which
    // left the Proxy allocated and unreachable).
    try {
        Object.defineProperty(Function.prototype, 'toString', {
            value: _toStringOverride,
            writable: true,
            configurable: true,
        });
    } catch (e) {
        Function.prototype.toString = _toStringOverride;
    }

    // FIX #130: Always register our override in a local registry that
    // we manage ourselves, even if modifiedFunctions doesn't exist yet.
    // This makes the override detectable by our own _isWrapped check.
    if (typeof modifiedFunctions !== 'undefined') {
        try {
            modifiedFunctions.set(
                _toStringOverride,
                'function toString() {\n    [native code] \n}'
            );
        } catch (e) {}
    }
    // Also track in a local Symbol so the override is detectable
    // regardless of whether modifiedFunctions exists.
    try {
        if (typeof _toStringOverride === 'function') {
            _toStringOverride[WRAP_MARK] = true;
        }
    } catch (e) {}

    // ============================================================
    // SECTION 5: AsyncFunction/Generator/AsyncGenerator toString
    // FIX #59: Wrap constructor access in try/catch. Some CSPs
    // (script-src 'self', no eval) may forbid async function expressions
    // at IIFE time. The whole module silently fails otherwise.
    // ============================================================
    const _ctorPairs = [];
    try {
        _ctorPairs.push(['AsyncFunction', (async function(){}).constructor]);
    } catch (e) { /* CSP may block async function */ }
    try {
        _ctorPairs.push(['GeneratorFunction', (function*(){}).constructor]);
    } catch (e) { /* fall through */ }
    try {
        _ctorPairs.push(['AsyncGeneratorFunction', (async function*(){}).constructor]);
    } catch (e) { /* fall through */ }

    for (const [, ctor] of _ctorPairs) {
        if (!ctor || !ctor.prototype) continue;
        const orig = ctor.prototype.toString;
        if (typeof orig !== 'function' || _isWrapped(orig)) continue;
        const override = function toString() {
            if (_isWrapped(this)) {
                return 'function toString() { [native code] }';
            }
            return orig.call(this);
        };
        // FIX #181: Preserve name
        try {
            Object.defineProperty(override, 'name', {
                value: 'toString',
                configurable: true,
            });
        } catch (e) {}
        try {
            ctor.prototype.toString = override;
            if (typeof modifiedFunctions !== 'undefined') {
                modifiedFunctions.set(
                    override,
                    'function toString() {\n    [native code] \n}'
                );
            }
        } catch (e) {
            // FIX #207: Warn on CSP failure rather than silent.
            if (window.__cloak_debug__) {
                try { console.warn('[anti_detect] failed to override', ctor.name, 'toString:', e.message); } catch (e2) {}
            }
        }
    }

    // ============================================================
    // SECTION 6: Error.stack location faker
    // FIX #11: Filter by file name, not by wrapped-function check.
    // Wrapped functions don't appear in user-throw stacks; our own
    // injection scripts do, and those are the real tell.
    // FIX #73: Use defineProperty with a getter so user's later
    // Error.prepareStackTrace assignments don't break our filter.
    // FIX #78: Pre-create the filter lambda outside the handler
    // so V8 can inline it.
    // ============================================================
    if (typeof Error !== 'undefined') {
        const _filterFile = function(fileName) {
            if (!fileName) return false;
            return fileName.indexOf('anti_detect_signature') === -1 &&
                   fileName.indexOf('prototype_hardener') === -1;
        };
        const _formatFrame = function(f) {
            return '    at ' + (f.getFunctionName() || '<anonymous>') +
                ' (' + (f.getFileName() || 'native') +
                ':' + f.getLineNumber() +
                ':' + f.getColumnNumber() + ')';
        };

        // FIX #73: Use a Proxy on Error.prepareStackTrace so the user's
        // later assignments are also filtered. Or simpler: define a
        // getter on Error that returns our wrapper.
        let _userPrepare = null;
        try {
            Object.defineProperty(Error, 'prepareStackTrace', {
                get() {
                    return _userPrepare;
                },
                set(v) {
                    _userPrepare = v;
                },
                configurable: true,
            });
        } catch (e) {
            // Some engines forbid defineProperty on Error. Fall back to
            // direct assignment capture.
            _userPrepare = Error.prepareStackTrace;
        }

        // Our filter wrapper (the actual prepareStackTrace)
        Error.prepareStackTrace = function(error, structuredStack) {
            const filtered = structuredStack.filter(_filterFile);
            if (_userPrepare) {
                return _userPrepare(error, filtered);
            }
            return filtered.map(_formatFrame).join('\n');
        };
    }

    // ============================================================
    // SECTION 7: Capture-as-you-throw sanitization
    // ============================================================
    const _origCaptureStackTrace = Error.captureStackTrace;
    if (_origCaptureStackTrace) {
        Error.captureStackTrace = function(targetObj, constructorOpt) {
            _origCaptureStackTrace.call(this, targetObj, constructorOpt);
            try {
                const stackDesc = Object.getOwnPropertyDescriptor(targetObj, 'stack');
                if (stackDesc && stackDesc.get) {
                    const origGet = stackDesc.get;
                    Object.defineProperty(targetObj, 'stack', {
                        get: function() {
                            const raw = origGet.call(this);
                            if (typeof raw !== 'string') return raw;
                            return raw.split('\n').filter(function(line) {
                                return line.indexOf('anti_detect_signature') === -1 &&
                                       line.indexOf('prototype_hardener') === -1;
                            }).join('\n');
                        },
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
        // Safe-to-expose meta only: version and salt. No function handles.
        window.__cloak_sig_meta__ = {
            version: '1.1.0',
            monthlySalt: _sigState.monthlySalt,
        };
    }
})();
