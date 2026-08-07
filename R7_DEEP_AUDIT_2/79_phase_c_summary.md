# Phase C: Deep Audit Summary (Passes 43-45)

## Goal
Continue R7-DEEP-AUDIT-2 with deeper passes focused on:
- Memory profiling and leak detection
- Cross-module interaction at runtime
- Error handling completeness
- Hidden bugs that only manifest when components are wired

## Issues Found in Phase C

### CRITICAL (would break production)
1. **CM-44-1**: `sticky_proxy_resolver.py:271` - `os.getenv()` called but `os`
   is imported as `_os`. Module fails to import → `_StickyProxyResolver = None`
   → `RuntimeError("StickyProxyResolver not importable")` at first proxy
   request. **Fixed**: Changed to `_os.getenv()`.

### BUG (subtle, off-by-one)
2. **MEM-43-1**: `humanized_movement.py:135` - `if len > 1000` allowed
   cache to grow to 1001. **Fixed**: Changed to `>= 1000`.

### POTENTIAL (panic-time hang)
3. **CM-44-2**: `hardened_orchestrator.py:2986` - `subprocess.run(["which",
   "tc"])` had no timeout in panic handler. **Fixed**: Added `timeout=2`.

## Tests Passing (Passes 43-45)

| Suite | Tests | Pass | Notes |
|-------|-------|------|-------|
| Pass 43 (Memory) | 10/10 | ✓ | Including stress tests |
| Pass 44 (Cross-Module) | 12/12 | ✓ | All 12 interactions verified |
| Pass 45 (Error Handling) | 8/8 | ✓ | All static patterns correct |

## Key Findings (No Fix Needed)

1. **Memory bounded**: 2 module-level caches (`_profile_cache`, `_audit_log`)
   both hard-capped at 1000 entries with FIFO/sliding-window eviction.

2. **Singleton sharing**: `get_muscle_profile` returns same instance per
   persona_id. State (PES counter, etc.) persists across calls.

3. **Entropy deterministic per persona**: 6/6 biometric attributes match
   between repeated `get_muscle_profile("X")` calls.

4. **No env var conflicts**: Only 1 shared env var
   (`BE_COMPOSITOR_JITTER_MS`) and both modules interpret identically.

5. **DB schema consistent**: `identity_health.py` queries match expected
   `personas` table columns (state, failure_count, last_active, cooldown_until).

6. **Error handling well-structured**: 203 try blocks, 40 context managers,
   50% of except blocks include logging. Silent passes are all in
   cleanup paths (intentional).

7. **Subprocess calls**: 10/11 have explicit timeouts (1 was fixed in
   CM-44-2).

8. **Async error coverage**: 36/56 async functions (64%) have try blocks.
   Remaining 20 are simple sleep/getter helpers that don't need them.

## Static Validation

- Python: 18/18 files compile clean
- JavaScript: prototype_hardener.js syntax OK
- Shell: entrypoint.sh and network_audit.sh syntax OK

## Files Modified

1. `browser-runtime/sticky_proxy_resolver.py` - Fixed `_safe_getenv_int`
   to use `_os.getenv` instead of `os.getenv`.
2. `browser-runtime/humanized_movement.py` - Fixed cache eviction off-by-one
   (changed `>` to `>=`).
3. `browser-runtime/hardened_orchestrator.py` - Added `timeout=2` to
   `subprocess.run(["which", "tc"])` in panic handler.

## Test Files Created

- `R7_DEEP_AUDIT_2/test_pass43_audit.py` - 10 memory profiling tests
- `R7_DEEP_AUDIT_2/test_pass43_stress.py` - Cache eviction stress tests
- `R7_DEEP_AUDIT_2/test_pass44_audit.py` - 12 cross-module interaction tests
- `R7_DEEP_AUDIT_2/test_pass45_audit.py` - 8 error handling pattern tests

## Documentation Created

- `R7_DEEP_AUDIT_2/76_pass43_audit.md`
- `R7_DEEP_AUDIT_2/77_pass44_audit.md`
- `R7_DEEP_AUDIT_2/78_pass45_audit.md`
- `R7_DEEP_AUDIT_2/79_phase_c_summary.md` (this file)
