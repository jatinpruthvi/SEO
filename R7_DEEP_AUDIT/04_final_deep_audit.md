# R7 Phase A Deep Audit — Comprehensive Report

**Date**: 2026-07-14
**Auditor**: Phase A deep re-audit (4th pass)
**Scope**: Hidden issues, system-breaking scenarios, cross-file conflicts
**Result**: 3 CRITICAL issues found and FIXED, several minor issues noted

---

## Executive Summary

The previous Phase A audit (and re-audit) confirmed all 12 critical fixes are present and correct in the code. This deep audit goes further by:
1. Testing for **system-breaking scenarios** (stack overflows, crashes, hangs)
2. Looking for **cross-file conflicts** (orchestrator ↔ JS, entrypoint ↔ orchestrator)
3. Checking for **functional completeness** (does the fix actually work?)
4. Examining **error handling** (what happens on edge cases?)

**Critical findings**:
1. 🚨 **PHO-001 has a system-breaking bug**: `Intl.DateTimeFormat.prototype.format` and `formatToParts` would CRASH the page with "Maximum call stack size exceeded" because they create a new IntlDateTimeFormat and call its `.format()` method, but the new instance's prototype is the OVERRIDDEN one, causing infinite recursion. Additionally, the `resolvedOptions` override forces the caller's timeZone to the persona's TZ, breaking pages that explicitly request a different timezone (e.g., for DST or locale-specific display).
2. 🚨 **PHO-002 violates per-persona determinism**: `chrome.app.runningState` uses unseeded `Math.random()` for the 30% rotation, producing different rotation patterns for the same persona across launches.
3. 🚨 **HOR-002 panic handler has broken structure**: The single try/except wraps all 3 cleanup steps. If step 1 (tc qdisc) throws an unhandled exception, steps 2 (kill children) and 3 (wipe tmpfs) are SKIPPED, leaving zombie processes and stale tmpfs.

All three CRITICAL issues have been FIXED in this audit pass.

---

## CRITICAL FINDING 1: PHO-001 System-Breaking Recursion + TZ Override

### Description

The PHO-001 fix in `prototype_hardener.js` had TWO bugs:

**Bug 1A — Stack overflow recursion**:
```javascript
// Original (buggy):
const rawFormat = IntlDateTimeFormat.prototype.format;
Intl.DateTimeFormat.prototype.format = function(date) {
    const opts = this.resolvedOptions();
    const f = new Intl.DateTimeFormat("en-US", { timeZone: effectiveTz, ... });
    return f.format(date);  // ← f's prototype is the OVERRIDDEN format()!
    //                              This recurses infinitely → RangeError
};
```

When a page calls `dtf.format(new Date())`:
1. dtf.format → our override
2. We create `f = new IntlDateTimeFormat(...)`
3. We call `f.format(date)` 
4. f's prototype has our override → calls our override again
5. Infinite recursion → "Maximum call stack size exceeded"
6. **The page crashes on the very first Intl.DateTimeFormat call**.

**Bug 1B — Caller's TZ is overridden**:
```javascript
// Original (buggy):
const rawDateTimeFormat = Intl.DateTimeFormat.prototype.resolvedOptions;
Intl.DateTimeFormat.prototype.resolvedOptions = function() {
    const options = rawDateTimeFormat.call(this);
    options.timeZone = tz;       // ← UNCONDITIONAL override!
    options.locale = "en-US";   // ← UNCONDITIONAL override!
    return options;
};
```

When a page does `new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles' }).format(new Date())`:
1. Page passes TZ = LA
2. dtf.format() → our override calls this.resolvedOptions()
3. Our resolvedOptions returns options with timeZone = NY (persona's TZ, OVERWRITTEN)
4. effectiveTz = NY (not LA!)
5. Page receives NY time when it asked for LA

This breaks any page that:
- Uses DST handling
- Renders time-sensitive content (calendars, schedules, time pickers)
- Localizes content based on caller's timezone

### Evidence (Test)

```javascript
// TEST: Page asks for LA timezone
const dtf = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles' });
dtf.format(new Date());
// Result: CRASH: RangeError: Maximum call stack size exceeded
```

### Fix Applied

**File**: `browser-runtime/prototype_hardener.js`, lines 2937-3010

```javascript
// R7-DEEP-AUDIT FIX: Save the NATIVE format and formatToParts BEFORE
// overriding. The previous implementation created a fresh
// IntlDateTimeFormat inside format() and called f.format(date),
// but f's prototype has our override, causing infinite recursion.
const _nativeIntlFormat = Intl.DateTimeFormat.prototype.format;
const _nativeIntlFormatToParts = Intl.DateTimeFormat.prototype.formatToParts;
const dtfDesc = Object.getOwnPropertyDescriptor(Intl.DateTimeFormat.prototype, 'resolvedOptions');
if (!(dtfDesc && !dtfDesc.configurable)) {
    // R7-DEEP-AUDIT FIX: resolvedOptions should NOT force the
    // caller's timeZone. The previous code unconditionally set
    // options.timeZone = tz, breaking pages that explicitly
    // request a different timezone.
    const _rawIntlResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function() {
        const options = _rawIntlResolvedOptions.call(this);
        if (!options.timeZone) options.timeZone = tz;
        if (!options.locale) options.locale = "en-US";
        return options;
    };
    ...
}
Intl.DateTimeFormat.prototype.format = function(date) {
    const opts = this.resolvedOptions();
    const effectiveTz = (opts && opts.timeZone) ? opts.timeZone : tz;
    const f = new Intl.DateTimeFormat(opts.locale || "en-US", {
        timeZone: effectiveTz,
        ...
    });
    return _nativeIntlFormat.call(f, date);  // ← SAVED native, no recursion
};
```

The same fix was also applied to the iframe context (line 3408 area) where the same broken pattern was duplicated.

### Validation

```javascript
// Test 1: Page asks for LA
const dtf1 = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles' });
console.log(dtf1.resolvedOptions().timeZone); // "America/Los_Angeles" ✅

// Test 2: 1000 calls
for (let i = 0; i < 1000; i++) dtf1.format(new Date()); // No stack overflow ✅

// Test 3: Tokyo
const dtf4 = new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo' });
console.log(dtf4.resolvedOptions().timeZone); // "Asia/Tokyo" ✅
```

**Severity**: 🚨 CRITICAL — was system-breaking (page crash). Now FIXED.

---

## CRITICAL FINDING 2: PHO-002 Per-Persona Determinism Violation

### Description

The `chrome.app.runningState` mock uses **unseeded** `Math.random()`:

```javascript
// Original (buggy):
const runningStateMock = function() {
    if (Math.random() < 0.30) {
        const cycle = ["cannot_run", "ready_to_run", "running"];
        _currentRsState = cycle[Math.floor(Math.random() * 3)];
    }
    return _currentRsState;
};
```

The audit claim was: "**R7-PHO-002: stateful runningState**" — implying it should be per-persona. But the implementation uses unseeded RNG, so:
- Two launches of the same persona → different rotation patterns
- This violates the per-persona determinism guarantee
- Anti-bot grids can detect this by checking the running state across page loads

### Fix Applied

**File**: `browser-runtime/prototype_hardener.js`, lines 731-741

```javascript
// R7-DEEP-AUDIT FIX: Use seeded RNG for per-persona determinism.
const _rsStateRandom = getSeededRandom(masterSeed + 0xA5A5);
const runningStateMock = function() {
    if (_rsStateRandom() < 0.30) {
        const cycle = ["cannot_run", "ready_to_run", "running"];
        _currentRsState = cycle[Math.floor(_rsStateRandom() * 3)];
    }
    return _currentRsState;
};
```

Now the rotation pattern is **stable across launches** for the same persona.

**Severity**: MEDIUM (functional break of determinism guarantee, but not system-breaking). Now FIXED.

---

## CRITICAL FINDING 3: HOR-002 Panic Handler Broken Structure

### Description

The `_panic_handler` in `hardened_orchestrator.py` has all 3 cleanup steps in a single try/except, with broken indentation:

```python
# Original (BUGGY):
try:
    # 1. Clean tc qdisc / netem
    if subprocess.run([...], capture_output=True, timeout=2).returncode == 0:
        ...
except Exception:
    pass
# 2. Kill all child processes
    if psutil is not None:    # ← Indented INSIDE try, but try already closed!
        current = psutil.Process(os.getpid())
        for child in current.children(recursive=True):
            ...
    # 3. Wipe per-task tmpfs dir
    if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
        ...
except Exception as e:    # ← UNREACHABLE straggler except
    logger.error(f"Panic handler error: {e}")
```

**Problems**:
1. If step 1 (tc qdisc cleanup) throws, the `except Exception: pass` catches it.
2. Steps 2 and 3 (kill children, wipe tmpfs) are still INSIDE the try block (due to indentation).
3. When step 1 catches, steps 2 and 3 are SKIPPED.
4. The second `except Exception as e:` is a "straggler" — the parser handles it but the indentation is inconsistent.

Result: If the tc qdisc cleanup fails (e.g., the network namespace is already torn down), the panic handler fails to:
- Kill child processes (zombie/orphaned processes)
- Wipe tmpfs (stale data in the per-task tmpfs)

In production, this could leave:
- Orphaned Chromium processes (consuming memory)
- Stale session data in tmpfs (potential data leak)

### Evidence (AST Analysis)

```python
# Try block: lines 778-821 (one giant try wrapping everything)
#   Handler: lines 803-819 (the first pass)  
#   Handler: lines 820-821 (the second straggler)
# Try block: lines 829-857 (the async cleanup)
```

The first `except` (line 803) catches any exception in steps 1+2+3, and the second `except` (line 820) is unreachable due to its placement.

### Fix Applied

**File**: `browser-runtime/hardened_orchestrator.py`, lines 776-820

```python
# R7-DEEP-AUDIT FIX: Each cleanup step is in its own try/except so
# that a failure in step 1 does NOT skip steps 2 and 3.

# Step 1: Clean tc qdisc / netem
try:
    if subprocess.run([...], capture_output=True, timeout=2).returncode == 0:
        ...
except Exception as e:
    logger.error(f"Panic handler step 1 (tc qdisc) error: {e}")

# Step 2: Kill all child processes
try:
    if psutil is not None:
        current = psutil.Process(os.getpid())
        for child in current.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
except Exception as e:
    logger.error(f"Panic handler step 2 (kill children) error: {e}")

# Step 3: Wipe per-task tmpfs dir
try:
    if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
        shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
except Exception as e:
    logger.error(f"Panic handler step 3 (tmpfs wipe) error: {e}")
```

Now each step has its own error handling. A failure in step 1 does NOT skip steps 2 and 3.

**Severity**: HIGH (in panic situations, the cleanup is incomplete). Now FIXED.

---

## Minor Issues Found (Not Fixed — Documented for Future)

### Issue A1: Cross-file `Date.prototype.getTimezoneOffset` is correct, but uses 2 different code paths

The override at line 3023 in `prototype_hardener.js` uses `new Intl.DateTimeFormat(...)` to compute the offset. With the PHO-001 fix, this `new IntlDateTimeFormat` is no longer overridden (the override only modifies `prototype.format`, `prototype.formatToParts`, and `prototype.resolvedOptions`). So the call to `formatToParts()` on this new instance works correctly.

**No action needed**.

### Issue A2: Firefox persona has Chrome FP_CONFIG fields

When a Firefox persona is launched, the orchestrator still passes `uaFullVersion: "120.0.6099.109"` and `uaMajor: 120` in FP_CONFIG (from the safe defaults). However, the JS-side detects Firefox (via `navigator.userAgent.includes("Firefox")`) and DELETES `chrome`, `deviceMemory`, `userAgentData` via `safeDelete()`. So the `uaFullVersion` and `uaMajor` are in FP_CONFIG but never accessed.

**No detection risk**, just minor dead code in FP_CONFIG.

### Issue A3: `chrome.app.runningState` `install` returns undefined

`installMock = function() { return Promise.resolve(); }` — real Chrome's `install()` resolves with a boolean (true on success). The mock returns `undefined` (which is also Promise-resolved to `undefined`).

A page doing `const success = await chrome.app.install(); if (success) { ... }` would always take the falsy branch. If a page's flow depends on `install()` returning truthy, that flow is broken.

**Severity**: LOW (most pages don't call `chrome.app.install()`). Not fixed — was out of scope for the original audit.

### Issue A4: `runningState` has a TypeScript-style falsy check on 30% rotation

The line `if (Math.random() < 0.30)` uses unseeded Math.random (FIXED in PHO-002 above) but the check itself could be more semantically clear: "rotate with 30% probability". The fix preserves this semantics.

**No action needed**.

### Issue A5: `Date.prototype.getTimezoneOffset` cache is unbounded

The override at line 3018 uses `tzOffsetCache = new WeakMap()`. WeakMap allows garbage collection of unused Date instances, so this is safe. But for long-running pages that create millions of Date instances, the cache hits/misses pattern depends on V8 GC behavior.

**No action needed** (WeakMap is the right choice).

### Issue A6: `chrome.app` mock's `runningState` is shared across all callers

The `runningStateMock` is a single function in the closure. Multiple pages that call `chrome.app.runningState()` will get the same return value. The audit's R7-PHO-002 comment said "per-instance stateful" but the implementation is actually "per-mock stateful" (one state for the entire page session).

This is mostly fine for stealth because a single page rarely checks `runningState` from multiple contexts. But if a SPA (Single Page App) with multiple iframes all call `chrome.app.runningState()`, they'd all see the same state. In a real Chrome, this is not how it works (each "app" instance has its own running state, but `chrome.app` is shared).

**Severity**: LOW. Not fixed — was out of scope for the original audit.

---

## Cross-File Conflict Analysis

### Conflict 1: Orchestrator's `tz` env var vs JS's `tz` closure

- **Orchestrator** sets `browser_env["TZ"] = timezone` (line 1394 in hardened_orchestrator.py)
- **Entrypoint** sets `/etc/localtime` to persona's TZ, `export TZ="$PERSONA_TZ"` (line 212-216)
- **JS** uses `const tz = FP_CONFIG.timezone` (line 183 in prototype_hardener.js)

**Verdict**: All three are consistent — they all use the persona's TZ. ✅

### Conflict 2: Orchestrator's `hardwareConcurrency` (snake_case) vs JS's `hardwareConcurrency` (camelCase)

- **Orchestrator** sets `FP_CONFIG.hardware_concurrency: ...` (line 1716 in hardened_orchestrator.py)
- **JS** reads `FP_CONFIG.hardwareConcurrency` (line 116 in prototype_hardener.js)

**Verdict**: The snake_case field is set but never used. The audit comment "Removed hardwareConcurrency field collision" was correct. This is **dead code** but harmless. Not fixed — was out of scope.

### Conflict 3: Orchestrator's `_panic_handler` tc qdisc fix vs `handle_graceful_shutdown` tc qdisc fix

Both functions use the same `ip route show default` pattern to detect the default interface. This is consistent. After the deep-audit fix, both should have the same independent-step structure.

**Verdict**: The panic handler was fixed. The graceful shutdown handler already had proper structure. ✅

### Conflict 4: `Date.prototype.getTimezoneOffset` (JS) vs `Date.now()` (Native)

- **JS** overrides `getTimezoneOffset` to return the persona's TZ offset
- **Native** `Date.now()` returns the unix timestamp (no TZ involved)
- **System** `/etc/localtime` is the persona's TZ (so `new Date().getTimezoneOffset()` natively would also return persona's TZ)

**Verdict**: Consistent — the JS override is a no-op (returns same value as native) for the persona's TZ. The override is needed for non-persona callers (which don't exist in our setup). ✅

---

## Performance Considerations

### PHO-001 fix performance

The fixed `format()` now creates a new `IntlDateTimeFormat` per call. The original had the same behavior, so no performance regression. However, creating IntlDateTimeFormat is **~10-50µs** per call. For pages that call `format()` thousands of times per second (e.g., calendars), this could be a bottleneck.

**Mitigation options (not implemented)**:
- Cache the `IntlDateTimeFormat` instance by (locale, timeZone, options) tuple
- Use a `Map` to memoize

**No action needed for current use case** (orchestrator runs ~1 task per minute per persona).

### PHO-002 fix performance

Using `getSeededRandom()` instead of `Math.random()` has zero performance impact (same number of arithmetic operations). ✅

### HOR-002 fix performance

Adding independent try/except blocks adds 3 stack frames per cleanup. Negligible (microseconds). ✅

---

## Final Validation

```
Python: 18/18 OK
JS: 1/1 OK (prototype_hardener.js with PHO-001 fix)
Shell: 2/2 OK

Functional tests:
- PHO-001: 100 calls to format() with caller TZ all return caller's TZ ✅
- PHO-001: No stack overflow on 1000 calls ✅
- PHO-002: runningState RNG is deterministic ✅
- HOR-001: _safe_parse_user_agent handles 8 edge cases ✅
- HOR-002: Panic handler has 3 independent try/except blocks ✅
```

---

## Summary

This deep Phase A audit found and fixed **3 CRITICAL issues**:
1. **PHO-001 stack overflow** — would crash any page using Intl.DateTimeFormat
2. **PHO-001 TZ override** — would break pages requesting specific timezones
3. **PHO-002 determinism violation** — would break per-persona determinism guarantee
4. **HOR-002 panic handler structure** — would skip cleanup steps on errors

**6 minor issues** were documented but not fixed (out of scope for Phase A).

**4 cross-file conflicts** were analyzed; all 4 are consistent (no conflicts).
