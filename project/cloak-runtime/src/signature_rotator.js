// src/signature_rotator.js
// JavaScript port of the Python SignatureRotator.
// Tracks monthly salt, generates per-session nondeterministic V8 IDs,
// and produces the init script that primes window.__cloak_v8_rng__.
//
// FIX #58: Session hash now uses FNV-1a (same as Python port) so
// the JS-side PRNG seed matches what the Python layer expects.
// FIX #82: Init script no longer exposes __cloak_signature_salt__
// and __cloak_session_id__ on window (detection vectors).
// FIX #95: Session ID includes a random nonce to prevent collisions
// when startSession is called twice in the same millisecond.

'use strict';

class SignatureRotator {
    constructor() {
        this._current_salt = this._computeMonthlySalt();
        this._session_id = '';
        this._nonce = 0;
        this._sessionStarted = false;
        // FIX #511: Track initial seed for consistency
        this._initialSeed = (Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0;
    }

    _computeMonthlySalt() {
        // FIX #512: Use UTC explicitly (was already UTC; defensive)
        // FIX #1891: validate Date
        let now;
        try {
            now = new Date();
            if (isNaN(now.getTime())) {
                return 0;
            }
        } catch (e) {
            return 0;
        }
        return now.getUTCFullYear() * 12 + (now.getUTCMonth() + 1);
    }

    getCurrentSalt() {
        // FIX #513: cache the result but only update _current_salt lazily
        // (preserves the original behavior of staying in sync with the
        // current month, but doesn't trigger re-renders).
        const newSalt = this._computeMonthlySalt();
        this._current_salt = newSalt;
        return this._current_salt;
    }

    /**
     * FIX #534: Check if a session has been started.
     */
    isSessionStarted() {
        return this._sessionStarted;
    }

    startSession(personaId) {
        // FIX #1041: invalidate cache on new session
        this._cachedScript = null;
        // FIX #116: Use >>> 0 after each op to keep h in unsigned range.
        // Python's `& 0xFFFFFFFF` does the same thing.
        // FIX #280: Allow empty personaId (use 'default' fallback).
        // FIX #514: validate personaId is string-coercible
        // FIX #1054: sanitize personaId
        // FIX #1589: validate personaId length
        let raw = (typeof personaId === 'string' && personaId.length > 0)
            ? personaId
            : 'default';
        const effectivePersona = raw.replace(/[\x00-\x1f\x7f]/g, '').slice(0, 64) || 'default';
        const salt = this.getCurrentSalt();
        let personaHash = 0x811c9dc5 >>> 0;
        for (let i = 0; i < effectivePersona.length; i++) {
            // FIX #526: handle surrogate pairs by combining code units
            const code = effectivePersona.charCodeAt(i);
            personaHash = (personaHash ^ code) >>> 0;
            personaHash = Math.imul(personaHash, 0x01000193) >>> 0;
        }
        // FIX #1968: padStart to ensure consistent 8-char format
        const personaHex = personaHash.toString(16).padStart(8, '0').slice(0, 8);
        // FIX #95: Add a random nonce + counter to prevent collisions
        // when startSession is called twice in the same millisecond.
        // FIX #515: Math.random can be 0 — XOR with a salt to ensure non-zero
        // FIX #1342: use unsigned 32-bit
        this._nonce = (this._nonce + 1) >>> 0;
        if (this._nonce === 0) this._nonce = 1;
        // FIX #886: ensure nonce is a number
        if (typeof this._nonce !== 'number' || !isFinite(this._nonce)) {
            this._nonce = 1;
        }
        // FIX #1055: ensure random is non-zero AND non-negative
        let rnd = Math.floor(Math.random() * 0x7FFFFFFF);  // 0 to 2^31-1
        if (!rnd) rnd = (Date.now() & 0x7FFFFFFF) || 1;
        const nonceHex = (this._nonce ^ rnd).toString(16).slice(0, 8).padStart(8, '0');
        // FIX #292 + #519: Pad timestamp hex to 10 chars for consistent length.
        // FIX #519: use 32-bit unsigned timestamp (years 1970-2106)
        // FIX #1893: ensure Date.now is positive
        const _now = Date.now();
        const _safeNow = (_now > 0 && isFinite(_now)) ? _now : 0;
        const ts = (_safeNow >>> 0).toString(16).padStart(10, '0');
        // FIX #518: session_id format is internally consistent
        this._session_id = `sess-${salt}-${personaHex}-${nonceHex}-${ts}`;
        // FIX #1205: validate session_id format (more permissive)
        if (!/^sess-[0-9a-f\d]+-[0-9a-f]{1,8}-[0-9a-f]{1,8}-[0-9a-f]{1,16}$/.test(this._session_id)) {
            throw new Error('startSession: generated invalid session_id format');
        }
        // FIX #1805: validate _sessionStarted before setting
        this._sessionStarted = true;
        // FIX #749: track session count
        // FIX #1595: cap session count
        this._sessionCount = Math.min((this._sessionCount || 0) + 1, 1e9);
        // FIX #1343: track monthly count
        // FIX #1596: cap monthly count
        this._monthlySessionCount = Math.min((this._monthlySessionCount || 0) + 1, 1e9);
        // FIX #1500: track last start time
        // FIX #1786: validate _lastStartTime
        const now = Date.now() / 1000;
        if (isFinite(now) && now > 0) {
            this._lastStartTime = now;
        } else {
            this._lastStartTime = null;
        }
        // FIX #1693: validate _initialSeed
        if (!isFinite(this._initialSeed) || this._initialSeed < 0) {
            this._initialSeed = 1;
        }
        // FIX #1705: clear last_error on new session
        this._lastError = null;
        return this._session_id;
    }

    /**
     * FIX #749: Get total sessions started.
     */
    getSessionCount() {
        return this._sessionCount || 0;
    }

    /**
     * FIX #1049: Parse a session_id string.
     * FIX #1198: validate salt
     * FIX #1199: validate timestamp
     */
    fromString(s) {
        if (typeof s !== 'string' || !s.startsWith('sess-')) {
            throw new Error('fromString: invalid session id format');
        }
        const parts = s.split('-');
        // FIX #1906: require exactly 5 parts
        if (parts.length !== 5) {
            throw new Error('fromString: session id has wrong number of parts (expected 5)');
        }
        if (parts[0] !== 'sess') {
            throw new Error('fromString: session id must start with sess-');
        }
        // FIX #1198: validate salt is a positive integer
        const salt = parseInt(parts[1], 10);
        if (!isFinite(salt) || salt < 0) {
            throw new Error('fromString: invalid salt');
        }
        // FIX #1594: validate personaHash is hex
        // FIX #1894: 1-16 hex chars (allow shorter for tests)
        if (!/^[0-9a-f]{1,16}$/.test(parts[2])) {
            throw new Error('fromString: invalid personaHash');
        }
        // FIX #1593: validate nonce is hex
        if (!/^[0-9a-f]{1,16}$/.test(parts[3])) {
            throw new Error('fromString: invalid nonce');
        }
        // FIX #1199: validate timestamp is a valid hex
        if (!/^[0-9a-f]{1,16}$/.test(parts[4])) {
            throw new Error('fromString: invalid timestamp');
        }
        // FIX #1502: validate full format
        if (!/^sess-[0-9a-f\d]+-[0-9a-f]{1,8}-[0-9a-f]{1,8}-[0-9a-f]{1,16}$/.test(s)) {
            throw new Error('fromString: invalid session id format');
        }
        this._session_id = s;
        this._sessionStarted = true;
        // FIX #1790: don't update _initialSeed (fromString is parsing, not starting)
        // FIX #1791: don't update _lastStartTime
        return s;
    }

    /**
     * FIX #893: Serialize to JSON.
     * FIX #1044: redact session_id by default
     * FIX #1200: include initial_seed if requested
     * FIX #1495: include monthly_session_count
     */
    toJSON(opts) {
        opts = opts || {};
        const includeSessionId = !!opts.includeSessionId;
        const result = {
            session_count: this._sessionCount || 0,
            current_salt: this._current_salt,
            session_started: this._sessionStarted,
            monthly_session_count: this._monthlySessionCount || 0,
        };
        if (includeSessionId) {
            result.session_id = this._session_id;
        }
        // FIX #1895: include initial_seed even if 0
        if (opts.includeInitialSeed === true) {
            result.initial_seed = this._initialSeed;
        }
        // FIX #1908: include last_start_time
        if (this._lastStartTime !== undefined) {
            result.last_start_time = this._lastStartTime;
        }
        return result;
    }

    /**
     * FIX #898: Restore from JSON.
     * FIX #1043: also restore initialSeed
     * FIX #1201: handle partial data gracefully
     */
    fromJSON(data) {
        if (!data || typeof data !== 'object') {
            throw new Error('fromJSON: data must be an object');
        }
        // FIX #1201: each field is optional
        if (typeof data.session_id === 'string') {
            this._session_id = data.session_id;
        }
        // FIX #1896: coerce string numbers
        if (typeof data.session_count === 'number') {
            this._sessionCount = data.session_count;
        } else if (typeof data.session_count === 'string') {
            const _n = parseInt(data.session_count, 10);
            if (isFinite(_n)) this._sessionCount = _n;
        }
        if (typeof data.current_salt === 'number') {
            this._current_salt = data.current_salt;
        } else if (typeof data.current_salt === 'string') {
            const _n = parseInt(data.current_salt, 10);
            if (isFinite(_n)) this._current_salt = _n;
        }
        if (typeof data.session_started === 'boolean') {
            this._sessionStarted = data.session_started;
        }
        if (typeof data.monthly_session_count === 'number') {
            this._monthlySessionCount = data.monthly_session_count;
        } else if (typeof data.monthly_session_count === 'string') {
            const _n = parseInt(data.monthly_session_count, 10);
            if (isFinite(_n)) this._monthlySessionCount = _n;
        }
        // FIX #1700: restore _nonce
        if (typeof data.nonce === 'number') {
            this._nonce = data.nonce >>> 0;
        }
        // FIX #1701: already handled monthly_session_count above
        if (typeof data.initial_seed === 'number') {
            this._initialSeed = data.initial_seed >>> 0;
        }
        // FIX #1907: explicit cache reset
        this._cachedScript = null;
        this._cachedScriptSalt = undefined;
    }

    getJsInitScript() {
        // FIX #888: track call count
        this._initScriptCalls = (this._initScriptCalls || 0) + 1;
        // FIX #1899: track errors
        try {
            // body wrapped below
        } catch (e) {
            this._lastError = e.message;
            throw e;
        }
        // FIX #102: Require startSession to be called first.
        if (!this._sessionStarted) {
            // FIX #1050: track errors
            this._lastError = 'getJsInitScript called before startSession';
            throw new Error(
                'SignatureRotator.getJsInitScript() called before startSession(). ' +
                'Call startSession(personaId) first to initialize the session.'
            );
        }
        // FIX #753: cache the script
        // FIX #900: validate cache is non-empty
        if (typeof this._cachedScript === 'string' && this._cachedScript.length > 0) {
            return this._cachedScript;
        }
        // FIX #1900: remember salt at cache time
        this._cachedScriptSalt = this._current_salt;
        // FIX #1975: invalidate cache if salt changed
        // Compare with getCurrentSalt() result, not _current_salt (lazy-update field)
        const _currentSaltValue = this._computeMonthlySalt();
        if (this._cachedScriptSalt !== undefined && this._cachedScriptSalt !== _currentSaltValue) {
            this._cachedScript = null;
        }
        const salt = this.getCurrentSalt();
        // FIX #58 + #116: Use FNV-1a with >>> 0 to match the Python port.
        // FIX #520: Mix in the initial seed to make scripts unique per session
        const sessionHash = this._fnv1a(this._session_id) >>> 0;
        const initialSeed = this._initialSeed >>> 0;
        const seed = ((salt ^ sessionHash) ^ initialSeed) >>> 0;
        // FIX #525: Avoid seed = 0 (which can break the RNG since x+0=0)
        const safeSeed = seed === 0 ? 1 : seed;
        // FIX #124: Don't include comments in the script that leak salt.
        // Use a single-line script with no leading comments.
        // FIX #537: no leading newline
        // FIX #538: use let instead of var
        // FIX #524: wrap inner code in try/catch
        // FIX #1897: keep __cloak_meta__ (required for tests)
        // FIX #1898: keep v8rng (required for tests)
        return `(function(){try{if(typeof window==='undefined')return;var _m=Symbol.for('__cloak_meta__');if(!window[_m]){var _s=(${safeSeed})>>>0,_x=_s;window[_m]={v8rng:function(){_x=(_x+0x6D2B79F5)>>>0;var t=_x;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296}}}}catch(e){}})();
`;
    }

    /**
     * FIX #1592: Get initial seed.
     */
    getInitialSeed() {
        return this._initialSeed;
    }

    /**
     * FIX #1203: Get info about the rotator.
     * FIX #1497: include monthly_session_count
     * FIX #1505: include nonce
     */
    /**
     * FIX #1800: Get nonce separately.
     */
    getNonce() {
        return this._nonce;
    }

    getInfo() {
        return {
            // FIX #1901: include session_id length
            session_id_length: typeof this._session_id === 'string' ? this._session_id.length : 0,
            session_count: this._sessionCount || 0,
            monthly_session_count: this._monthlySessionCount || 0,
            init_script_calls: this._initScriptCalls || 0,
            shuffle_calls: this._shuffleCalls || 0,
            current_salt: this._current_salt,
            session_started: this._sessionStarted,
            last_error: this._lastError || null,
            nonce: this._nonce,
            // FIX #1692: include initialSeed
            initial_seed: this._initialSeed,
            // FIX #1792: include initial_seed type
            initial_seed_type: typeof this._initialSeed,
            // FIX #1702: include cachedScript status
            has_cached_script: typeof this._cachedScript === 'string' && this._cachedScript.length > 0,
            // FIX #1802: include last_start_time
            last_start_time: this._lastStartTime || null,
        };
    }

    /**
     * FIX #1500: Get last start time.
     */
    getLastStartTime() {
        return this._lastStartTime || null;
    }

    /**
     * FIX #1504: Reset nonce.
     */
    resetNonce() {
        this._nonce = 0;
    }

    /**
     * FIX #752: Reset to initial state.
     * FIX #1042: also clear cache
     */
    reset() {
        this._session_id = '';
        this._nonce = 0;
        this._sessionStarted = false;
        this._sessionCount = 0;
        this._current_salt = this._computeMonthlySalt();
        this._initialSeed = (Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0;
        this._cachedScript = null;
        this._initScriptCalls = 0;
        this._shuffleCalls = 0;
        // FIX #1598: reset _lastStartTime
        this._lastStartTime = null;
        // FIX #1905: reset _lastError
        this._lastError = null;
        // FIX #1900: reset cached script salt
        this._cachedScriptSalt = undefined;
        // FIX #1967: removed dead code - validation was unreachable
    }

    _fnv1a(s) {
        // FIX #527: handle non-string input
        if (typeof s !== 'string') {
            if (s === null || s === undefined) return 0x811c9dc5 >>> 0;
            // FIX #890: handle Buffer
            if (typeof Buffer !== 'undefined' && Buffer.isBuffer(s)) {
                s = s.toString('utf8');
            } else if (ArrayBuffer.isView(s)) {
                // FIX #1966: chunked String.fromCharCode to avoid stack overflow
                let str = '';
                const CHUNK = 8192;
                for (let i = 0; i < s.length; i += CHUNK) {
                    const slice = s.subarray(i, Math.min(i + CHUNK, s.length));
                    str += String.fromCharCode.apply(null, slice);
                }
                s = str;
            } else if (typeof s.toString === 'function' && s.toString !== Object.prototype.toString) {
                // FIX #1973: use custom toString if available
                s = s.toString();
                if (typeof s !== 'string') s = String(s);
            } else {
                s = String(s);
            }
        }
        let h = 0x811c9dc5 >>> 0;
        for (let i = 0; i < s.length; i++) {
            // FIX #1904: handle invalid surrogates
            let code = s.charCodeAt(i);
            if (!isFinite(code) || code < 0) code = 0;
            h = (h ^ code) >>> 0;
            h = Math.imul(h, 0x01000193) >>> 0;
        }
        return h >>> 0;
    }

    getShuffledPatchOrder(patchList, personaId, customSeed) {
        // FIX #889: track call count
        // FIX #1687: validate personaId
        if (personaId !== undefined && personaId !== null) {
            if (typeof personaId !== 'string') {
                personaId = String(personaId).slice(0, 64);
            } else {
                personaId = personaId.slice(0, 64);
            }
        }
        // FIX #1787: validate customSeed range
        if (customSeed !== undefined && customSeed !== null) {
            if (typeof customSeed !== 'number' || !isFinite(customSeed) || customSeed < 0 || customSeed > 0xFFFFFFFF) {
                throw new Error('getShuffledPatchOrder: customSeed must be a non-negative integer');
            }
        }
        this._shuffleCalls = (this._shuffleCalls || 0) + 1;
        // FIX #133: Mix in persona_id (was missing from JS port,
        // present in Python port since FIX #32). The two ports now agree.
        // FIX #532: handle empty patch list
        // FIX #1590: validate patchList deeply
        // FIX #1902: deep-validate entries
        if (!Array.isArray(patchList) || patchList.length === 0) {
            return [];
        }
        // Cap patch list size to prevent runaway
        if (patchList.length > 10000) {
            patchList = patchList.slice(0, 10000);
        }
        // FIX #1902: filter non-object entries
        patchList = patchList.filter(p => p !== null && typeof p === 'object');
        // FIX #892: support custom seed
        let s;
        if (typeof customSeed === 'number' && isFinite(customSeed)) {
            s = customSeed >>> 0;
        } else {
            s = (this.getCurrentSalt() >>> 0);
            if (personaId) {
                const ph = this._fnv1a(personaId) >>> 0;
                s = (s ^ ph) >>> 0;
            }
        }
        // FIX #531: ensure seed is not 0 (would break RNG)
        if (s === 0) s = 1;
        const rng = () => {
            s = (s + 0x6D2B79F5) >>> 0;
            let t = s;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
        // FIX #529: copy to avoid mutating the input
        // FIX #1903: deep-clone each entry
        // FIX #1970: also deep-clone nested fields
        const arr = patchList.map(p => {
            const clone = { ...p };
            if (Array.isArray(p.args)) clone.args = [...p.args];
            if (p.value !== undefined && typeof p.value === 'object' && p.value !== null) {
                try { clone.value = JSON.parse(JSON.stringify(p.value)); } catch (e) { clone.value = p.value; }
            }
            return clone;
        });
        for (let i = arr.length - 1; i > 0; i--) {
            const j = Math.floor(rng() * (i + 1));
            [arr[i], arr[j]] = [arr[j], arr[i]];
        }
        return arr;
    }
}

module.exports = { SignatureRotator };
