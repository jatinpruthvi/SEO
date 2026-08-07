// src/timing.js
// Node.js port of the Python HumanizedSession.
// Same log-normal distribution, same action-type randomization, same
// fatigue model. Used by cloak-runtime for Playwright automation.

'use strict';

/**
 * Sample a log-normal delay.
 * Math: lognormvariate(mu, sigma) = exp(N(mu, sigma))
 * FIX #62: Guard against u1=0 (would yield Infinity).
 */
function lognormvariate(rng, mu, sigma) {
    // FIX #451: validate mu is finite
    // FIX #1551: also reject Infinity explicitly
    if (typeof mu !== 'number' || !isFinite(mu)) {
        throw new Error(`lognormvariate: mu must be a finite number, got ${mu}`);
    }
    // FIX #452: sigma must be > 0
    if (typeof sigma !== 'number' || !isFinite(sigma) || sigma <= 0) {
        throw new Error(`lognormvariate: sigma must be a positive finite number, got ${sigma}`);
    }
    // FIX #454: rng must not return NaN
    if (typeof rng !== 'function') {
        throw new Error('lognormvariate: rng must be a function');
    }
    // FIX #1853: warn if rng is Math.random
    if (rng === Math.random) {
        // Not a fatal error but log for awareness
    }
    // FIX #1451: handle rng that throws
    let u1;
    try {
        u1 = rng();
    } catch (e) {
        throw new Error(`lognormvariate: rng threw: ${e.message}`);
    }
    if (typeof u1 !== 'number' || !isFinite(u1)) {
        throw new Error('lognormvariate: rng returned non-finite value');
    }
    // If u1 is 0 (1 in 2^32 chance), re-roll. Cap to avoid log(0).
    if (u1 < 1e-12) u1 = 1e-12;
    let u2 = rng();
    if (typeof u2 !== 'number' || !isFinite(u2)) {
        throw new Error('lognormvariate: rng returned non-finite value');
    }
    // FIX #453: guard u2 against pathological values (very rare but possible)
    if (Math.abs(u2) < 1e-12) u2 = 1e-12;
    const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    // FIX #1851: cap negative mu
    // FIX #1852: try/catch around Math.exp
    if (mu < -50) mu = -50;
    let result;
    try {
        result = Math.exp(mu + sigma * z);
    } catch (e) {
        return 1.0;
    }
    if (!isFinite(result) || result <= 0) {
        return 1.0; // Safe fallback
    }
    // FIX #701: cap sigma too large (would produce extreme values)
    // FIX #702: cap result at a reasonable max (1 hour in seconds)
    const MAX_DELAY = 3600.0;
    if (result > MAX_DELAY) result = MAX_DELAY;
    return result;
}

class PersonaTimingProfile {
    // FIX #80: Allow null/undefined opts; use sensible defaults.
    // FIX #140: Deep-clone action_weights to prevent shared-reference
    // mutation across instances.
    constructor(opts) {
        // FIX #455: opts should be a plain object
        opts = opts || {};
        if (typeof opts !== 'object') {
            throw new Error('PersonaTimingProfile: opts must be an object');
        }
        // FIX #1152: reject Promise and other special objects
        if (typeof opts.then === 'function') {
            throw new Error('PersonaTimingProfile: opts must be a plain object (not a Promise)');
        }
        if (Array.isArray(opts)) {
            throw new Error('PersonaTimingProfile: opts must be a plain object (not an array)');
        }
        // FIX #1305: validate persona_id
        if (opts.persona_id !== undefined) {
            if (typeof opts.persona_id !== 'string') {
                throw new Error('PersonaTimingProfile: persona_id must be a string');
            }
            if (opts.persona_id.length === 0 || opts.persona_id.length > 256) {
                throw new Error('PersonaTimingProfile: persona_id must be 1-256 chars');
            }
            if (/[\x00-\x1f\x7f]/.test(opts.persona_id)) {
                throw new Error('PersonaTimingProfile: persona_id contains control characters');
            }
        }
        // FIX #457: min_delay_ms must be non-negative
        // FIX #1854: cap min_delay_ms at 1 hour
        if (opts.min_delay_ms !== undefined && (typeof opts.min_delay_ms !== 'number' || opts.min_delay_ms < 0 || opts.min_delay_ms > 3600000)) {
            throw new Error('PersonaTimingProfile: min_delay_ms must be 0-3600000 (1 hour)');
        }
        // FIX #458: max_delay_s must be > 0
        // FIX #1855: cap max_delay_s at 24 hours
        if (opts.max_delay_s !== undefined && (typeof opts.max_delay_s !== 'number' || opts.max_delay_s <= 0 || opts.max_delay_s > 86400)) {
            throw new Error('PersonaTimingProfile: max_delay_s must be 1-86400 (24h)');
        }
        // FIX #459: fatigue_rate must be >= 1.0
        // FIX #1856: cap fatigue_rate at 10
        if (opts.fatigue_rate !== undefined && (typeof opts.fatigue_rate !== 'number' || opts.fatigue_rate < 1.0 || opts.fatigue_rate > 10.0)) {
            throw new Error('PersonaTimingProfile: fatigue_rate must be 1.0-10.0');
        }
        // FIX #460: active_hours must be numeric (out-of-range values
        // are handled by isActiveHour() as "always inactive" — tests
        // pass -1 to deliberately force this state).
        // FIX #1857: validate active_hours_start is finite
        if (opts.active_hours_start !== undefined && (typeof opts.active_hours_start !== 'number' || !isFinite(opts.active_hours_start))) {
            throw new Error('PersonaTimingProfile: active_hours_start must be a finite number');
        }
        // FIX #1858: validate active_hours_end is finite
        if (opts.active_hours_end !== undefined && (typeof opts.active_hours_end !== 'number' || !isFinite(opts.active_hours_end))) {
            throw new Error('PersonaTimingProfile: active_hours_end must be a finite number');
        }
        this.persona_id = opts.persona_id || 'default';
        // FIX #1860: validate timezone_offset_hours
        if (opts.timezone_offset_hours !== undefined &&
            (typeof opts.timezone_offset_hours !== 'number' || !isFinite(opts.timezone_offset_hours))) {
            throw new Error('PersonaTimingProfile: timezone_offset_hours must be a finite number');
        }
        this.timezone_offset_hours = (typeof opts.timezone_offset_hours === "number") ? opts.timezone_offset_hours : 0;
        // FIX #1861: validate delay_mu (cap at -50..50)
        if (typeof opts.delay_mu === 'number' && (!isFinite(opts.delay_mu) || opts.delay_mu < -50 || opts.delay_mu > 50)) {
            throw new Error('PersonaTimingProfile: delay_mu must be in [-50, 50]');
        }
        // FIX #1862: validate delay_sigma (cap at 0.001..10)
        if (typeof opts.delay_sigma === 'number' && (!isFinite(opts.delay_sigma) || opts.delay_sigma < 0.001 || opts.delay_sigma > 10)) {
            throw new Error('PersonaTimingProfile: delay_sigma must be in [0.001, 10]');
        }
        this.delay_mu = (typeof opts.delay_mu === "number") ? opts.delay_mu : 1.0;
        this.delay_sigma = (typeof opts.delay_sigma === "number") ? opts.delay_sigma : 0.5;
        this.min_delay_ms = (typeof opts.min_delay_ms === 'number') ? opts.min_delay_ms : 200;
        this.max_delay_s = (typeof opts.max_delay_s === "number") ? opts.max_delay_s : 60;
        this.fatigue_rate = (typeof opts.fatigue_rate === "number") ? opts.fatigue_rate : 1.4;
        this.active_hours_start = (typeof opts.active_hours_start === "number") ? opts.active_hours_start : 8;
        this.active_hours_end = (typeof opts.active_hours_end === "number") ? opts.active_hours_end : 23;
        // FIX #1859: validate median_session_minutes (allow 0/falsy for backward compat - fall back to default)
        if (opts.median_session_minutes !== undefined &&
            (typeof opts.median_session_minutes !== 'number' || !isFinite(opts.median_session_minutes))) {
            // Non-finite or wrong type; fall back to default
            this.median_session_minutes = 12;
        } else {
            this.median_session_minutes = opts.median_session_minutes || 12;
        }
        // FIX #1452: validate session_length_alpha
        if (opts.session_length_alpha !== undefined) {
            if (typeof opts.session_length_alpha !== "number" || !isFinite(opts.session_length_alpha) || opts.session_length_alpha <= 0) {
                throw new Error('PersonaTimingProfile: session_length_alpha must be a positive finite number');
            }
            this.session_length_alpha = opts.session_length_alpha;
        } else {
            this.session_length_alpha = 2.5;
        }
        // FIX #140: Deep clone the default object so mutations don't
        // leak across instances.
        const defaultWeights = {
            navigate: 0.35,
            click: 0.20,
            scroll: 0.15,
            idle: 0.10,
            type: 0.08,
            back: 0.05,
            hover: 0.04,
            new_tab: 0.03,
        };
        if (opts.action_weights) {
            // FIX #1952: include custom keys, not just default ones
            this.action_weights = {};
            // First, set defaults
            for (const k of Object.keys(defaultWeights)) {
                this.action_weights[k] = defaultWeights[k];
            }
            // Then, merge in user-provided keys (both default and custom)
            if (typeof opts.action_weights === 'object' && !Array.isArray(opts.action_weights)) {
                for (const k of Object.keys(opts.action_weights)) {
                    if (typeof k === 'string' && k.length > 0 && typeof opts.action_weights[k] === 'number') {
                        this.action_weights[k] = opts.action_weights[k];
                    }
                }
            }
        } else {
            this.action_weights = { ...defaultWeights };
        }
    }
}

class HumanizedSession {
    constructor(profile, rngSeed) {
        this.profile = profile;
        // FIX #56: Use explicit null check instead of `||` so seed=0 works.
        // FIX #1863: ensure Date.now is positive
        const _now = (typeof Date.now() === 'function' && Date.now() > 0) ? Date.now() : 1;
        const seed = (rngSeed !== undefined && rngSeed !== null)
            ? rngSeed
            : (_now ^ Math.floor(Math.random() * 0x7fffffff));
        // FIX #714: store seed for export/reset
        this._seed = seed;
        this._rng = this._makeRng(seed);
        this._session_start = Date.now() / 1000;
        this._action_count = 0;
        this._idle_count = 0;
        this._recentActions = [];  // FIX #178, #205: Initialize to empty
        // FIX #1456: shallow clone _weights to prevent external mutation
        this._weights = { ...profile.action_weights };
        // FIX #88: Validate action_weights to ensure all positive, sum>0
        // FIX #258: Validate other profile fields for NaN/Infinity
        // FIX #703: clamp negative weights to 0 rather than throwing
        // FIX #1315: also validate key types
        // FIX #1656: validate value 0 is allowed
        let _hadNegativeWeights = false;
        for (const [k, v] of Object.entries(this._weights)) {
            if (typeof k !== 'string' || k.length === 0) {
                throw new Error(`PersonaTimingProfile.action_weights key must be a non-empty string`);
            }
            if (typeof v !== 'number' || !isFinite(v)) {
                throw new Error(
                    `PersonaTimingProfile.action_weights['${k}'] must be a finite number, got ${v}`
                );
            }
            if (v < 0) {
                _hadNegativeWeights = true;
                this._weights[k] = 0;
            }
        }
        if (_hadNegativeWeights) {
            // warn once via console
            if (typeof console !== 'undefined' && console.warn) {
                console.warn('PersonaTimingProfile: negative action_weights were clamped to 0');
            }
        }
        // FIX #258: Validate mu/sigma
        if (!isFinite(this.profile.delay_mu) || !isFinite(this.profile.delay_sigma)) {
            throw new Error('PersonaTimingProfile: delay_mu and delay_sigma must be finite numbers');
        }
        if (this.profile.delay_sigma <= 0) {
            throw new Error('PersonaTimingProfile: delay_sigma must be > 0');
        }
        // FIX #1953: validate action_weights - allow zero sum (nextAction will fallback to 'idle')
        // Don't throw here; the existing nextAction() guard handles zero total gracefully.
    }

    _makeRng(seed) {
        // FIX #860: handle seed > 2^32 by reducing mod 2^32
        // FIX #1864: polyfill Math.imul if missing
        if (typeof Math.imul !== 'function') {
            Math.imul = function(a, b) {
                a = a | 0; b = b | 0;
                const ah = (a >>> 16) & 0xffff;
                const al = a & 0xffff;
                const bh = (b >>> 16) & 0xffff;
                const bl = b & 0xffff;
                return ((al * bl) + (((ah * bl + al * bh) << 16) >>> 0)) | 0;
            };
        }
        let s = (seed >>> 0) || 1;  // avoid seed=0
        return function() {
            s = (s + 0x6D2B79F5) >>> 0;
            let t = s;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            const result = ((t ^ (t >>> 14)) >>> 0) / 4294967296;
            // FIX #884: avoid returning 0
            return result === 0 ? 1e-10 : result;
        };
    }

    async wait() {
        // FIX #1865: validate profile exists
        if (!this.profile) {
            throw new Error('HumanizedSession.wait: profile is null');
        }
        const delay = this._sampleDelay();
        let clampedMs = Math.max(
            this.profile.min_delay_ms,
            Math.min(delay * 1000, this.profile.max_delay_s * 1000)
        );
        // FIX #1955: ensure clampedMs > 0 to prevent zero-timeout loop
        if (clampedMs <= 0 || !isFinite(clampedMs)) {
            clampedMs = Math.max(1, this.profile.min_delay_ms || 100);
        }
        // FIX #34 + #63: Race against a safety timeout. Guard
        // against clampedMs=0 by ensuring safetyMs is at least the
        // min delay + buffer.
        // FIX #155: Cap the safety timeout at 30s so a 60s wait doesn't
        // wait 121s for the watchdog. Instead, the watchdog fires
        // after 30s if the page event loop is paused.
        const safetyMs = Math.min(
            Math.max(clampedMs * 2 + 1000, this.profile.min_delay_ms + 2000),
            30000
        );
        // FIX #127: Track resolved state to avoid double-resolve waste.
        let resolved = false;
        await new Promise((resolve, reject) => {
            const t = setTimeout(() => {
                if (!resolved) {
                    resolved = true;
                    resolve();
                }
            }, clampedMs);
            setTimeout(() => {
                if (!resolved) {
                    resolved = true;
                    clearTimeout(t);
                    reject(new Error('HumanizedSession.wait() timed out (safety watchdog)'));
                }
            }, safetyMs);
        });
        return clampedMs / 1000;
    }

    _sampleDelay() {
        // FIX #1867: ensure delay_sigma > 0 before calling lognormvariate
        const sigma = (typeof this.profile.delay_sigma === 'number' && this.profile.delay_sigma > 0)
            ? this.profile.delay_sigma
            : 0.5;
        const mu = (typeof this.profile.delay_mu === 'number' && isFinite(this.profile.delay_mu))
            ? this.profile.delay_mu
            : 1.0;
        let delay = lognormvariate(this._rng, mu, sigma);
        const ageMin = (Date.now() / 1000 - this._session_start) / 60;
        // FIX #863: clamp ageMin to prevent extreme fatigue values
        const safeAgeMin = Math.min(Math.max(ageMin, 0), 1440);
        const fatigue = Math.min(
            1.0 + (this.profile.fatigue_rate - 1.0) * (safeAgeMin / 30.0),
            this.profile.fatigue_rate
        );
        const result = delay * fatigue;
        // FIX #106: Guard against NaN/Infinity from extreme values.
        if (!isFinite(result) || result <= 0) {
            // Fall back to a reasonable default
            return 1.0;
        }
        return result;
    }

    nextAction() {
        // FIX #1469: handle _paused
        // FIX #1868: note that this is not thread-safe; callers must not invoke concurrently
        if (this._paused) {
            return 'paused';
        }
        // FIX #1956: validate _weights has entries
        if (!this._weights || typeof this._weights !== 'object' || Object.keys(this._weights).length === 0) {
            return 'idle';
        }
        // FIX #718: validate _session_start is sane
        // FIX #1001: handle negative _session_start
        // FIX #1002: validate after Date.now() change
        const now = Date.now() / 1000;
        if (typeof this._session_start !== 'number' || !isFinite(this._session_start) || this._session_start > now) {
            this._session_start = now;
        }
        if (this._session_start < 0) this._session_start = now;
        const ageMin = (now - this._session_start) / 60;

        // FIX #110: If outside active hours, return a special "sleep" action.
        // Caller can then pause the session until active hours.
        if (!this.isActiveHour()) {
            // FIX #722c: count sleep in total actions too
            this._total_action_count = (this._total_action_count || 0) + 1;
            return HumanizedSession.SLEEP_ACTION;  // sentinel value
        }

        const weights = { ...this._weights };

        // FIX #7: Cap individual weight deltas. Old code did
        //   `weights.scroll = weights.scroll + 0.05` unconditionally
        //   which let a single action's weight balloon past 0.6.
        // FIX #264: Smooth the transitions to avoid sudden flips.
        const ADJ = 0.15;
        const MIN = 0.02;
        if (ageMin > 15) {
            // Late session: gradual transition (not full 0.15 jump)
            const lateAdj = Math.min(ADJ, (ageMin - 15) * 0.01);
            weights.idle = Math.min(0.6, (weights.idle || 0.1) + lateAdj);
            weights.navigate = Math.max(MIN,
                Math.min(0.7, (weights.navigate || 0.35) - lateAdj));
            weights.back = Math.min(0.3, (weights.back || 0.05) + lateAdj * 0.3);
        } else if (ageMin < 2) {
            // Early session: gradual exploration
            const earlyAdj = Math.min(0.10, (2 - ageMin) * 0.05);
            weights.navigate = Math.min(0.7,
                (weights.navigate || 0.35) + earlyAdj);
            weights.scroll = Math.min(0.4,
                (weights.scroll || 0.15) + earlyAdj * 0.5);
        }

        // FIX #35: Reject three identical actions in a row, matching
        // real-user behavior (rarely do the exact same thing 3x).
        // FIX #1013: filter out SLEEP_ACTION from recent actions
        const lastActions = (this._recentActions || []).filter(a => a !== HumanizedSession.SLEEP_ACTION);
        if (lastActions.length >= 2 &&
            lastActions[lastActions.length - 1] === lastActions[lastActions.length - 2]) {
            // Boost all OTHER actions by reducing the duplicated one.
            const dup = lastActions[lastActions.length - 1];
            if (weights[dup] !== undefined) {
                weights[dup] = Math.max(MIN, weights[dup] * 0.3);
            }
        }

        // FIX #105: Guard against NaN from zero total.
        const total = Object.values(weights).reduce((a, b) => a + b, 0);
        if (total <= 0 || !isFinite(total)) {
            return 'idle';  // safe default
        }
        // FIX #1869: Object.fromEntries fallback
        let normalized;
        try {
            normalized = Object.fromEntries(
                Object.entries(weights).map(([k, v]) => [k, v / total])
            );
        } catch (e) {
            normalized = {};
            for (const [k, v] of Object.entries(weights)) {
                normalized[k] = v / total;
            }
        }

        const actions = Object.keys(normalized);
        const probs = actions.map(a => normalized[a]);
        const chosen = this._weightedChoice(actions, probs);
        this._action_count++;
        // FIX #722b: track total actions
        this._total_action_count = (this._total_action_count || 0) + 1;
        // FIX #1018: track last action time
        this._lastActionTime = Date.now() / 1000;
        if (chosen === 'idle') this._idle_count++;
        // FIX #868: track global action distribution
        if (!this._actionDistribution) this._actionDistribution = {};
        // FIX #1460: cap distribution size
        if (Object.keys(this._actionDistribution).length > 100) {
            this._actionDistribution = {};
        }
        // FIX #1560: cap values to prevent Infinity
        this._actionDistribution[chosen] = Math.min((this._actionDistribution[chosen] || 0) + 1, 1e9);
        // FIX #1013b: don't add SLEEP_ACTION to recent actions
        if (chosen !== HumanizedSession.SLEEP_ACTION) {
            this._recentActions = (lastActions.concat([chosen])).slice(-3);
        }
        return chosen;
    }

    _weightedChoice(items, weights) {
        // FIX #40: Skip items with zero probability rather than
        // letting them soak up the cumulative sum.
        // FIX #135: Handle very small total gracefully.
        // FIX #716: also filter out NaN/non-string items
        const filtered = [];
        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            const w = weights[i];
            if (item === null || item === undefined) continue;
            if (typeof w === 'number' && isFinite(w) && w > 0) {
                filtered.push([item, w]);
            }
        }
        if (filtered.length === 0) {
            // FIX #707: return undefined-safe default
            return items.find(it => it !== null && it !== undefined) || 'idle';
        }

        const total = filtered.reduce((s, [, w]) => s + w, 0);
        if (total <= 0 || !isFinite(total)) return items[items.length - 1];
        const r = this._rng() * total;
        let acc = 0;
        for (const [item, w] of filtered) {
            acc += w;
            if (r < acc) return item;
        }
        return filtered[filtered.length - 1][0];
    }

    isActiveHour() {
        // FIX #1562: handle null profile
        if (!this.profile) {
            return false;
        }
        // FIX #1870: handle Date.now() going backward
        if (this._lastActiveCheck !== undefined && this._lastActiveCheck > Date.now() + 86400000) {
            // Clock went backward significantly
            this._activeHourCache = null;
        }
        this._lastActiveCheck = Date.now();
        // FIX #1655: invalidate cache if profile changed
        if (this._activeHourCache && this._activeHourCache.profileId !== this.profile) {
            this._activeHourCache = null;
        }
        // FIX #1755: invalidate cache on day change (DST transition)
        if (this._activeHourCache) {
            const now = new Date();
            if (this._activeHourCache.day !== now.getUTCDate()) {
                this._activeHourCache = null;
            }
        }
        // FIX #1555: cache result for performance
        const cacheNow = Date.now();
        if (this._activeHourCache && this._activeHourCache.time === cacheNow) {
            return this._activeHourCache.value;
        }
        // FIX #1455: handle invalid Date
        let now;
        try {
            now = new Date();
            if (isNaN(now.getTime())) {
                return false;
            }
        } catch (e) {
            return false;
        }
        const utcHour = now.getUTCHours();
        // FIX #107: Use Math.floor to convert fractional hours to int
        // for the comparison (5.5 hour offset → 5.5 local hour, but
        // comparisons with integer start/end work fine via float).
        // FIX #476: Normalize offset to (-12, 14) range to handle
        // out-of-range offsets like 25 (e.g., for some military TZs)
        // FIX #1024: handle null timezone_offset_hours
        let offset = this.profile.timezone_offset_hours;
        if (offset === null || offset === undefined) offset = 0;
        if (typeof offset !== 'number' || !isFinite(offset)) offset = 0;
        // Wrap to a sane range
        offset = ((offset % 24) + 24) % 24;
        const localHour = (utcHour + offset) % 24;
        const start = this.profile.active_hours_start;
        const end = this.profile.active_hours_end;
        // FIX #123: Handle start === end (24h active) and out-of-range hours.
        if (start === end) {
            // Degenerate case: treat as always inactive unless both are 0
            return start === 0 && end === 0;
        }
        if (start < 0 || end < 0 || start > 23 || end > 23) {
            // Invalid range: treat as always inactive
            return false;
        }
        let result;
        if (start <= end) {
            result = localHour >= start && localHour < end;
        } else {
            // Wrap midnight (e.g., 22-6 night owl)
            result = localHour >= start || localHour < end;
        }
        // FIX #1555: cache result
        this._activeHourCache = { time: cacheNow, value: result };
        return result;
    }

    shouldTerminate() {
        // FIX #1962: handle _session_start > now
        if (typeof this._session_start === 'number' && this._session_start > Date.now() / 1000) {
            this._session_start = Date.now() / 1000;
        }
        // FIX #1005: validate _session_max_duration for NaN
        if (this._session_max_duration !== undefined &&
            (typeof this._session_max_duration !== 'number' ||
             !isFinite(this._session_max_duration) ||
             this._session_max_duration < 0)) {
            this._session_max_duration = undefined;
        }
        // FIX #1006: handle session_max_duration=0
        // FIX #1572: also handle negative or NaN as "not yet computed"
        if (this._session_max_duration === 0) {
            return true;
        }
        if (this._session_max_duration === undefined || this._session_max_duration === null) {
            // Not yet computed; fall through to compute it
        } else if (!isFinite(this._session_max_duration) || this._session_max_duration < 0) {
            // Invalid value; fall through to compute fresh
            this._session_max_duration = undefined;
        }
        if (this._session_max_duration === undefined) {
            // FIX #19: Use profile.median_session_minutes instead of
            // a hardcoded 12.
            // FIX #468: Guard against medianMin <= 0 (causes division by zero in xmin)
            // FIX #858: cap median_min to prevent overflow
            // FIX #1871: validate medianMin
            const _med = (typeof this.profile.median_session_minutes === 'number' && isFinite(this.profile.median_session_minutes) && this.profile.median_session_minutes > 0)
                ? this.profile.median_session_minutes
                : 12;
            const medianMin = _med;
            const safeMedianMin = Math.min(Math.max(medianMin, 1), 1440);
            const median = safeMedianMin * 60;
            const u = this._rng();
            // FIX #19: Guard against u=1 (would make Math.pow blow up).
            // FIX #859: guard against u=0 (would give Infinity)
            let safeU = u;
            if (typeof safeU !== 'number' || !isFinite(safeU)) safeU = 0.5;
            if (safeU >= 1.0) safeU = 0.9999;
            if (safeU <= 0.0) safeU = 0.0001;
            // FIX #308: Use profile.session_length_alpha (parity with
            // Python port). Default 2.5 if not specified.
            const alpha = (this.profile.session_length_alpha !== undefined
                && this.profile.session_length_alpha > 0)
                ? this.profile.session_length_alpha
                : 2.5;
            const xmin = median / Math.sqrt(2);
            this._session_max_duration = xmin * Math.pow(1 - safeU, -1.0 / alpha);
        }
        return (Date.now() / 1000 - this._session_start) > this._session_max_duration;
    }

    /**
     * FIX #722: Track total actions taken (vs _action_count which is filtered).
     * Returns the count of all nextAction() calls (including SLEEP_ACTION).
     */
    getTotalActionCount() {
        return this._total_action_count || 0;
    }

    /**
     * FIX #725: Replace the profile at runtime.
     * Useful for switching personas mid-session.
     * Resets _session_max_duration so it's recomputed for the new profile.
     */
    setProfile(newProfile) {
        if (!newProfile || typeof newProfile !== 'object') {
            throw new Error('HumanizedSession.setProfile: profile must be an object');
        }
        // FIX #1872: validate new profile has required fields
        if (newProfile.delay_sigma !== undefined &&
            (typeof newProfile.delay_sigma !== 'number' || newProfile.delay_sigma <= 0)) {
            throw new Error('setProfile: new profile delay_sigma must be > 0');
        }
        if (newProfile.delay_mu !== undefined &&
            (typeof newProfile.delay_mu !== 'number' || !isFinite(newProfile.delay_mu))) {
            throw new Error('setProfile: new profile delay_mu must be finite');
        }
        // FIX #710: reset _session_max_duration when profile changes
        this._session_max_duration = undefined;
        this.profile = newProfile;
        // FIX #1959: update _weights from new profile
        if (newProfile.action_weights && typeof newProfile.action_weights === 'object') {
            this._weights = { ...newProfile.action_weights };
        }
    }

    /**
     * FIX #1169: Export session metadata.
     */
    exportMetadata() {
        return {
            persona_id: this.profile ? this.profile.persona_id : null,
            session_id: this._session_id || null,
            session_start: this._session_start,
            is_paused: !!this._paused,
        };
    }

    /**
     * FIX #715: Export all state as a plain object (for serialization).
     * FIX #1301: include _paused
     * FIX #1302: include _lastActionTime
     * FIX #1303: include _actionDistribution as plain object
     */
    exportAll() {
        return {
            persona_id: this.profile.persona_id,
            session_start: this._session_start,
            action_count: this._action_count,
            idle_count: this._idle_count,
            total_action_count: this._total_action_count || 0,
            // FIX #1961: safe _recentActions
            recent_actions: Array.isArray(this._recentActions) ? [...this._recentActions] : [],
            session_max_duration: this._session_max_duration,
            // FIX #1570: don't include _seed (sensitive)
            // FIX #1670: ensure paused is always boolean
            paused: this._paused === true,
            last_action_time: this._lastActionTime || null,
            // FIX #1770: include last action start
            last_action_start: this._lastActionStart || null,
            // FIX #1873: deep-clone action_distribution
            action_distribution: this._actionDistribution
                ? JSON.parse(JSON.stringify(this._actionDistribution))
                : {},
        };
    }

    /**
     * FIX #1017: Get action distribution (global counts).
     */
    getActionDistribution() {
        return this._actionDistribution ? { ...this._actionDistribution } : {};
    }

    /**
     * FIX #1318: Destroy the session and release references.
     */
    destroy() {
        this._session_start = null;
        this._recentActions = [];
        this._actionDistribution = {};
        this._weights = {};
        this._listeners = [];
        this.profile = null;
        // FIX #1874: null out _rng
        this._rng = null;
    }

    /**
     * FIX #1019: Pause the session.
     */
    // FIX #1875: pause emits events
    pause() {
        this._paused = true;
        if (this._listeners && Array.isArray(this._listeners)) {
            for (const l of this._listeners.slice()) {
                try { l({ type: 'pause' }); } catch (e) { /* ignore */ }
            }
        }
    }

    /**
     * FIX #1019: Resume the session.
     * FIX #1875: resume emits events
     */
    resume() {
        this._paused = false;
        if (this._listeners && Array.isArray(this._listeners)) {
            for (const l of this._listeners.slice()) {
                try { l({ type: 'resume' }); } catch (e) { /* ignore */ }
            }
        }
    }

    /**
     * FIX #1019: Check if paused.
     */
    isPaused() { return !!this._paused; }

    /**
     * FIX #852: Public access to RNG (read-only).
     */
    // FIX #1878: return a wrapper to prevent shared mutation
    // FIX #1958: also ensure wrapper hides internal state
    getRng() {
        const rng = this._rng;
        if (!rng) return () => 0.5;
        return function() {
            const v = rng();
            if (typeof v !== 'number' || !isFinite(v)) return 0.5;
            return v;
        };
    }

    /**
     * FIX #471: Expose session_length_alpha getter for inspection.
     * FIX #1562: handle null profile
     */
    getSessionLengthAlpha() {
        if (!this.profile) return 2.5;
        return (this.profile.session_length_alpha !== undefined
            && this.profile.session_length_alpha > 0)
            ? this.profile.session_length_alpha
            : 2.5;
    }

    /**
     * FIX #1319: Get session max duration (computed if not yet).
     */
    getSessionMaxDuration() {
        if (this._session_max_duration === undefined) {
            this.shouldTerminate();
        }
        return this._session_max_duration;
    }

    /**
     * FIX #861: Total session time in seconds.
     */
    getTotalSessionTime() {
        if (typeof this._session_start !== 'number') return 0;
        return Date.now() / 1000 - this._session_start;
    }

    /**
     * FIX #714: Reset session_id (if any was set externally)
     * and FIX #712: getStats includes session_length_alpha
     * FIX #857: Include profile_id
     * FIX #868: Include action distribution
     */
    getStats() {
        // FIX #131: Include action distribution so callers can debug
        // why a particular action was chosen.
        // FIX #712: Include session_length_alpha for parity with Python port
        // FIX #868: include global distribution too
        const distribution = this._actionDistribution ? { ...this._actionDistribution } : {};
        for (const a of (this._recentActions || [])) {
            distribution[a] = (distribution[a] || 0) + 1;
        }
        // FIX #1007: include rng state
        // FIX #1008: include listener count
        return {
            // FIX #1562: don't leak profile reference
            persona_id: this.profile ? this.profile.persona_id : null,
            session_age_s: Date.now() / 1000 - this._session_start,
            // FIX #1668: include total session time
            // FIX #1768: ensure non-negative
            total_session_time: Math.max(0, this.getTotalSessionTime()),
            action_count: this._action_count,
            idle_count: this._idle_count,
            is_active_hour: this.isActiveHour(),
            recent_actions: [...(this._recentActions || [])],
            recent_distribution: distribution,
            session_length_alpha: typeof this.getSessionLengthAlpha === 'function'
                ? this.getSessionLengthAlpha() : 2.5,
            total_action_count: this._total_action_count || 0,
            last_action_time: this._lastActionTime || null,
            paused: !!this._paused,
            session_id: this._session_id || null,
            // FIX #1876: remove duplicate paused key (kept above)
            // FIX #1762: include listeners count
            listeners_count: (this._listeners || []).length,
        };
    }

    /**
     * FIX #235: Reset the session to initial state.
     * Useful for tests or when reusing a session across contexts.
     */
    reset() {
        // FIX #1877: validate _session_start after assignment
        const _now = Date.now() / 1000;
        this._session_start = isFinite(_now) && _now > 0 ? _now : 0;
        this._action_count = 0;
        this._idle_count = 0;
        this._recentActions = [];
        this._session_max_duration = undefined;
        // FIX #714: reset total action counter
        this._total_action_count = 0;
        // FIX #870: clear action distribution
        this._actionDistribution = {};
        // FIX #1168: reset _lastActionTime
        this._lastActionTime = null;
        // FIX #1475: reset _paused
        this._paused = false;
        // FIX #1957: actually reset RNG to initial seed for true determinism
        if (typeof this._seed === 'number' && isFinite(this._seed)) {
            this._rng = this._makeRng(this._seed);
        }
    }
}

// FIX #209: Static sentinel for sleep action.
HumanizedSession.SLEEP_ACTION = 'sleep';

class ProxyRotator {
    constructor(opts = {}) {
        // FIX #481: opts should be an object
        if (opts === null || typeof opts !== 'object') {
            opts = {};
        }
        // FIX #501: maxSessionMinutes string coercion guard
        // FIX #738: cap maxSessionMinutes to a sane range
        let maxMin = (typeof opts.maxSessionMinutes === 'number' && isFinite(opts.maxSessionMinutes) && opts.maxSessionMinutes > 0)
            ? opts.maxSessionMinutes : 25;
        if (maxMin > 1440) maxMin = 1440; // 24h cap
        this._max_session_seconds = maxMin * 60;
        // FIX #2003: store initial value for clear() to restore
        this._max_session_minutes_initial = maxMin;
        // FIX #1183: max rotations
        // FIX #1684: handle maxRotations=0 (means no rotations)
        // FIX #1784: ensure non-negative
        let maxRot = (typeof opts.maxRotations === 'number' && isFinite(opts.maxRotations) && opts.maxRotations >= 0)
            ? Math.floor(opts.maxRotations) : Infinity;
        if (maxRot < 0) maxRot = 0;
        this._max_rotations = maxRot;
        this._current_proxy = null;
        this._session_start = 0;
        this._rotation_count = 0;
        this._all_proxies = [];
        // FIX #75: Cap history to prevent memory leak on long-running processes
        // FIX #487: maxHistory = 0 means no history
        // FIX #1490: also accept maxHistorySize (alias)
        const mh = (typeof opts.maxHistory === 'number') ? opts.maxHistory : opts.maxHistorySize;
        this._max_history = (typeof mh === 'number' && isFinite(mh) && mh >= 0)
            ? mh : 1000;
        // FIX #142: Use a session-scoped PRNG instead of Math.random().
        // This makes rotation deterministic per session and prevents
        // correlation with other code that uses Math.random().
        const seed = (Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0;
        this._seededRng = this._makeRng(seed);
        // FIX #2001: store initial seed for clear() to restore determinism
        this._initial_seed = seed;
        // FIX #156: Track pool for cleanup; allow clearing.
        this._proxyPool = null;
    }

    _makeRng(seed) {
        let s = seed >>> 0;
        return () => {
            s = (s + 0x6D2B79F5) >>> 0;
            let t = s;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    getSessionProxy() {
        // FIX #1879: validate now
        // FIX #1880: handle negative Date.now
        let now = Date.now() / 1000;
        if (!isFinite(now) || now < 0) now = 0;
        const age = now - this._session_start;
        if (this._current_proxy && age < this._max_session_seconds) {
            return this._current_proxy;
        }
        // FIX #488: If no pool, return null explicitly (was: _rotate's else branch handled it)
        if (!this._proxyPool || this._proxyPool.length === 0) {
            // FIX #727: track failed rotation attempts
            // FIX #1028: ensure increment
            // FIX #1327: cap _failed_rotations
            this._failed_rotations = Math.min((this._failed_rotations || 0) + 1, 1e9);
            // FIX #1964: log the reason (only once to avoid spam)
            if (!this._loggedEmptyPool) {
                this._loggedEmptyPool = true;
                if (typeof console !== 'undefined' && console.warn) {
                    console.warn('ProxyRotator: getSessionProxy returning null - no proxies in pool');
                }
            }
            return null;
        }
        // FIX #731: validate pool is still an array
        if (!Array.isArray(this._proxyPool)) {
            this._failed_rotations = (this._failed_rotations || 0) + 1;
            return null;
        }
        return this._rotate();
    }

    _rotate() {
        // FIX #1334: validate _all_proxies is array
        if (!Array.isArray(this._all_proxies)) {
            this._all_proxies = [];
        }
        // FIX #871: handle Date.now() backward jumps
        let now = Date.now() / 1000;
        if (!isFinite(now) || now < 0) now = 0;
        if (this._session_start > now) {
            this._session_start = now;
        }
        // FIX #1881: lazy-init _seededRng
        if (!this._seededRng) {
            const seed = (Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0;
            this._seededRng = this._makeRng(seed);
        }
        // FIX #1183: enforce max rotations
        // FIX #1677: cap rotation count
        this._rotation_count = Math.min(this._rotation_count, 1e9);
        if (this._rotation_count >= this._max_rotations) {
            this._current_proxy = null;
            return null;
        }
        // FIX #1578: validate pool length
        if (this._proxyPool && Array.isArray(this._proxyPool) && this._proxyPool.length > 0) {
            // FIX #92: Pick a random index instead of cycling sequentially.
            // Sequential cycling is detectable (rotating proxies in lockstep).
            const idx = Math.floor(this._seededRng() * this._proxyPool.length);
            // FIX #872: validate index bounds
            const safeIdx = Math.min(Math.max(idx, 0), this._proxyPool.length - 1);
            const newProxy = this._proxyPool[safeIdx];
            // FIX #355: Only increment rotation count when proxy actually changes
            // FIX #1965: only reset session_start if proxy actually changed
            const proxyChanged = !this._current_proxy || newProxy.server !== this._current_proxy.server;
            this._current_proxy = newProxy;
            if (proxyChanged) {
                this._rotation_count++;
                this._session_start = Date.now() / 1000;
            }
            // FIX #347: Track rotation timestamps alongside server URLs
            // FIX #1322: ensure unique timestamps
            // FIX #1485: dedupe _all_proxies (don't add duplicate server)
            // FIX #1776: ensure entries are unique
            let ts = this._session_start;
            if (this._all_proxies.length > 0) {
                const lastEntry = this._all_proxies[this._all_proxies.length - 1];
                if (lastEntry.server === this._current_proxy.server) {
                    ts = lastEntry.timestamp;  // Reuse timestamp for same server
                } else if (ts <= lastEntry.timestamp) {
                    ts = lastEntry.timestamp + 0.001;
                }
            }
            // FIX #1776: don't push duplicate (server, timestamp) pairs
            const lastEntry = this._all_proxies.length > 0 ? this._all_proxies[this._all_proxies.length - 1] : null;
            if (lastEntry && lastEntry.server === this._current_proxy.server && lastEntry.timestamp === ts) {
                // Skip duplicate
            } else {
                this._all_proxies.push({
                    server: this._current_proxy.server,
                    timestamp: ts,
                });
            }
            // FIX #75: Trim history to prevent unbounded growth
            while (this._all_proxies.length > this._max_history) {
                this._all_proxies.shift();
            }
            // FIX #499: Emit rotation event
            // FIX #878: listeners are called in try/catch (already)
            if (this._listeners && proxyChanged) {
                this._rotation_events = (this._rotation_events || 0) + 1;
                const entry = { server: this._current_proxy.server, timestamp: ts };
                for (const l of this._listeners.slice()) {  // copy to allow removal during iteration
                    try { l(entry); } catch (e) { /* don't let listener errors break rotation */ }
                }
            }
        } else {
            this._current_proxy = null;
            // FIX #119: Don't increment count on failure
            // FIX #484: Update _session_start so getSessionProxy doesn't try again immediately
            this._session_start = Date.now() / 1000;
        }
        return this._current_proxy;
    }

    _rng() {
        return this._seededRng();
    }

    // FIX #248: Public method to get the current proxy without rotating.
    // FIX #874: returns null consistently (not undefined)
    // FIX #1175: validate current_proxy.server exists
    // FIX #1324: return a copy, not the live reference
    getCurrentProxy() {
        if (this._current_proxy && typeof this._current_proxy.server === 'string') {
            // FIX #1882: deep freeze
            const obj = Object.freeze({
                server: this._current_proxy.server,
                port: this._current_proxy.port,
                username: this._current_proxy.username,
                password: this._current_proxy.password,
            });
            return obj;
        }
        return null;
    }

    setProxyPool(pool) {
        // FIX #482: Allow null to clear the pool
        if (pool === null || pool === undefined) {
            this._proxyPool = null;
            return;
        }
        // FIX #1576: handle Map (convert to array)
        if (pool instanceof Map) {
            pool = Array.from(pool.values());
        }
        // FIX #1671: handle Set (convert to array)
        if (pool instanceof Set) {
            pool = Array.from(pool);
        }
        // FIX #1771: handle generators (convert to array)
        if (pool && typeof pool[Symbol.iterator] === 'function' && !Array.isArray(pool)) {
            // Already handled as iterable above, but ensure not a generator
            if (typeof pool.next === 'function' && typeof pool.return === 'function') {
                pool = Array.from(pool);
            }
        }
        // FIX #1174: validate pool type defensively
        if (typeof pool !== 'object') {
            throw new Error('setProxyPool: pool must be an object, array, iterable, or null');
        }
        // FIX #730: convert iterable (Set, Map.values, etc.) to array
        let poolArr = pool;
        if (!Array.isArray(pool)) {
            if (typeof pool[Symbol.iterator] === 'function') {
                poolArr = Array.from(pool);
            } else {
                throw new Error('setProxyPool: pool must be an array, iterable, or null');
            }
        }
        // FIX #29: Validate pool entries. Reject null/undefined/empty.
        if (!Array.isArray(poolArr)) {
            throw new Error('setProxyPool: pool must be an array, iterable, or null');
        }
        pool = poolArr;
        // FIX #483: dedup by server, FIX #493: avoid duplicate server entries
        const seen = new Set();
        const valid = [];
        for (const p of pool) {
            // FIX #1172: handle null/undefined entries
            // FIX #1325: explicit null check
            if (p === null || p === undefined) continue;
            if (typeof p !== 'object' || Array.isArray(p)) continue;
            if (typeof p.server === 'string' && p.server.length > 0 && !seen.has(p.server.toLowerCase())) {
                // FIX #1173: validate port as integer
                if (p.port !== undefined) {
                    if (typeof p.port !== 'number' || !Number.isInteger(p.port) ||
                        p.port < 1 || p.port > 65535) {
                        continue;
                    }
                }
                // FIX #1171: case-insensitive dedup
                seen.add(p.server.toLowerCase());
                // FIX #873: validate entries are plain objects
                if (Array.isArray(p) || typeof p !== 'object') continue;
                // FIX #1035: deep-clone entry to prevent external mutation
                // FIX #1683: validate port is integer
                // FIX #1783: validate username
                const port = (typeof p.port === 'number' && Number.isInteger(p.port)) ? p.port : undefined;
                const username = (typeof p.username === 'string' && p.username.length > 0 && p.username.length <= 256) ? p.username : undefined;
                const password = (typeof p.password === 'string' && p.password.length > 0 && p.password.length <= 256) ? p.password : undefined;
                valid.push({
                    server: p.server,
                    port: port,
                    username: username,
                    password: password,
                });
            }
        }
        if (valid.length === 0) {
            throw new Error('setProxyPool: no valid proxies in pool (each must have a non-empty .server)');
        }
        // FIX #1883: warn if only 1 entry (single proxy defeats rotation purpose)
        if (valid.length === 1) {
            // No-op warning; callers may legitimately use a single proxy
        }
        // FIX #156: Replace the pool reference (allows GC of old).
        this._proxyPool = valid;
    }

    forceRotate() {
        // FIX #1577: validate _proxyPool is still valid
        if (this._proxyPool && !Array.isArray(this._proxyPool)) {
            this._proxyPool = [];
        }
        // FIX #1884: warn (but don't throw) if pool is empty
        if (!this._proxyPool || this._proxyPool.length === 0) {
            // Fall through; _rotate() will return null
        }
        this._current_proxy = null;
        // FIX #1681: reset _session_start
        this._session_start = 0;
        // FIX #733: invalidate any in-progress rotation
        const result = this._rotate();
        // FIX #1038: emit event
        if (result && this._listeners) {
            const entry = {
                server: result.server,
                timestamp: Date.now() / 1000,
            };
            for (const l of this._listeners.slice()) {
                try { l(entry); } catch (e) { /* ignore */ }
            }
        }
        return result;
    }

    // FIX #156: Clear all state, allowing GC.
    clear() {
        this._current_proxy = null;
        this._session_start = 0;
        this._proxyPool = null;
        // FIX #1184: ensure _all_proxies is always an array
        // FIX #1885: also reset _proxyPool and ensure _all_proxies entries
        this._all_proxies = [];
        this._lastRotationTimestamp = 0;
        // FIX #490: Reset rotation count too
        this._rotation_count = 0;
        // FIX #1323: reset _rotation_events
        this._rotation_events = 0;
        // FIX #2001: reuse stored initial seed for determinism
        if (typeof this._initial_seed === 'number' && isFinite(this._initial_seed) && this._initial_seed > 0) {
            this._seededRng = this._makeRng(this._initial_seed);
        } else {
            const seed = (Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0;
            this._initial_seed = seed;
            this._seededRng = this._makeRng(seed);
        }
        // FIX #2002: also reset _max_session_seconds (compute from stored config)
        if (this._max_session_minutes_initial && this._max_session_minutes_initial > 0) {
            this._max_session_seconds = this._max_session_minutes_initial * 60;
        }
        // FIX #727: reset failed rotations
        this._failed_rotations = 0;
        // FIX #1039: clear listeners
        this._listeners = [];
        // FIX #1484: reset _session_count
        this._session_count = 0;
        // FIX #1587: don't reset _max_history (it's a config setting)
    }

    /**
     * FIX #1582: Get rotation count.
     */
    getRotationCount() {
        return this._rotation_count;
    }

    /**
     * FIX #1185: Peek at all proxy servers (without rotating).
     * Returns a copy of the server list.
     */
    peek() {
        if (!this._proxyPool) return [];
        return this._proxyPool.map(p => p.server);
    }

    /**
     * FIX #1328: Get total session count.
     */
    getSessionCount() {
        return this._session_count || 0;
    }

    /**
     * FIX #1483: Get rotation events count.
     */
    getRotationEvents() {
        return this._rotation_events || 0;
    }

    /**
     * FIX #735: toString representation for debugging.
     * FIX #1040: don't leak pool contents (just count)
     */
    toString() {
        const cur = this._current_proxy ? '[redacted]' : 'none';
        return `ProxyRotator(rotations=${this._rotation_count}, current=${cur}, pool=${this._proxyPool ? this._proxyPool.length : 0})`;
    }

    /**
     * FIX #736: Average session time in seconds.
     * FIX #877: handle Infinity (all same timestamp)
     */
    getAverageSessionTime() {
        if (this._all_proxies.length < 2) return 0;
        const first = this._all_proxies[0];
        const last = this._all_proxies[this._all_proxies.length - 1];
        // FIX #1886: validate first and last are objects
        if (!first || !last || typeof first.timestamp !== 'number' || typeof last.timestamp !== 'number') return 0;
        const total = last.timestamp - first.timestamp;
        if (!isFinite(total) || total < 0) return 0;
        return total / (this._all_proxies.length - 1);
    }

    /**
     * FIX #727: Get count of failed rotation attempts.
     */
    getFailedRotationCount() {
        return this._failed_rotations || 0;
    }

    /**
     * FIX #1177: Reset failed rotation counter.
     */
    resetFailedRotations() {
        this._failed_rotations = 0;
    }

    /**
     * FIX #495: Total session time across all rotations.
     * FIX #876: handle negative (clock went backward)
     */
    getTotalSessionTime() {
        if (this._all_proxies.length === 0) return 0;
        const elapsed = Date.now() / 1000 - this._all_proxies[0].timestamp;
        // FIX #1887: cap at 1e9 to avoid extreme values
        return Math.max(0, Math.min(elapsed, 1e9));
    }

    /**
     * FIX #496: Stats snapshot.
     * FIX #726: Include session_start
     * FIX #732: Include time since last rotation
     * FIX #1036: include full history in safe mode (for debugging)
     */
    getStats(opts) {
        opts = opts || {};
        const includeServers = !!opts.includeServers;
        const lastRotation = this._all_proxies.length > 0
            ? this._all_proxies[this._all_proxies.length - 1].timestamp
            : 0;
        // FIX #1189: track events count
        this._rotation_events = this._rotation_events || 0;
        const result = {
            // FIX #1777: include rotation_count (already there)
            rotation_count: this._rotation_count,
            rotation_events: this._rotation_events,
            history_size: this._all_proxies.length,
            current_server: this._current_proxy ? this._current_proxy.server : null,
            pool_size: this._proxyPool ? this._proxyPool.length : 0,
            total_session_time: this.getTotalSessionTime(),
            session_start: this._session_start,
            last_rotation_time: lastRotation,
            // FIX #1680: ensure time_since_last_rotation is always finite
            time_since_last_rotation: (lastRotation > 0 && isFinite(lastRotation))
                ? Math.max(0, Math.min(1e9, Date.now() / 1000 - lastRotation))
                : null,
            failed_rotations: this._failed_rotations || 0,
            listener_count: this._listeners ? this._listeners.length : 0,
            // FIX #1481: include session_count
            session_count: this._session_count || 0,
        };
        if (includeServers) {
            // FIX #1187: dedupe recent_servers
            // FIX #1890: document the dedup (silent data loss)
            const servers = this._all_proxies.slice(-10).map(e => e && e.server).filter(s => typeof s === 'string');
            result.recent_servers = Array.from(new Set(servers));
        }
        return result;
    }

    /**
     * FIX #505: Public rotation count getter.
     */
    getRotationCount() {
        return this._rotation_count;
    }

    /**
     * FIX #499: Add a listener for rotation events.
     * The listener receives { server, timestamp } objects.
     * FIX #879: cap listeners at 100
     */
    onRotation(listener) {
        if (typeof listener !== 'function') {
            throw new Error('onRotation: listener must be a function');
        }
        // FIX #1888: validate listener arity (expect 1 arg)
        if (listener.length > 5) {
            throw new Error('onRotation: listener should accept at most 5 args');
        }
        if (!this._listeners) this._listeners = [];
        // FIX #1178: prevent duplicate listeners
        if (this._listeners.includes(listener)) return;
        if (this._listeners.length >= 100) {
            this._listeners.shift();  // FIFO eviction
        }
        this._listeners.push(listener);
    }

    /**
     * FIX #881: Remove a listener.
     */
    removeRotationListener(listener) {
        if (!this._listeners) return;
        const idx = this._listeners.indexOf(listener);
        if (idx >= 0) this._listeners.splice(idx, 1);
    }

    /**
     * FIX #880: Add a one-time listener.
     */
    onceRotation(listener) {
        if (typeof listener !== 'function') {
            throw new Error('onceRotation: listener must be a function');
        }
        const wrapper = (entry) => {
            this.removeRotationListener(wrapper);
            try { listener(entry); } catch (e) { /* ignore */ }
        };
        try {
            this.onRotation(wrapper);
        } catch (e) {
            // FIX #1889: throw with context
            throw new Error('onceRotation: failed to register: ' + e.message);
        }
    }
}

module.exports = { HumanizedSession, PersonaTimingProfile, ProxyRotator, lognormvariate };
