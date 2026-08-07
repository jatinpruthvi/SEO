# Pass 44: Cross-Module Interaction & Runtime Behavior

## Audit Focus
Verify how modules interact at runtime. Find issues that only manifest when
components are wired together. Phase C deep audit.

## Tests (12 total)

| ID | Test | Status |
|----|------|--------|
| CM-1 | BE + HM integration | ✓ Pass |
| CM-2 | BE + KE PES integration | ✓ Pass |
| CM-3 | Identity health DB contract | ✓ Pass |
| CM-4 | Session serializer entropy | ✓ Pass |
| CM-5 | Proxy + eBPF coordination | ✓ Pass |
| CM-6 | Circadian + movement timing | ✓ Pass |
| CM-7 | Paste + keyboard coordination | ✓ Pass |
| CM-8 | Profile archiver roundtrip | ✓ Pass |
| CM-9 | Singleton sharing | ✓ Pass |
| CM-10 | Env var consistency | ✓ Pass |
| CM-11 | State propagation | ✓ Pass |
| CM-12 | Entropy determinism | ✓ Pass |

## Issues Found & Fixed

### CM-44-1: NameError in sticky_proxy_resolver._safe_getenv_int (CRITICAL)
- **File**: `browser-runtime/sticky_proxy_resolver.py:271`
- **Bug**: `raw = os.getenv(name)` - `os` is imported as `_os` (line 201),
  not `os`. Function reference fails at module load time.
- **Symptom**: `sticky_proxy_resolver` fails to import with `NameError: name
  'os' is not defined`. The orchestrator's `_try_import` catches this and
  silently sets `_StickyProxyResolver = None`. At first proxy request,
  `RuntimeError("StickyProxyResolver not importable")` is raised - **the
  system would have no working proxy resolution in production**.
- **Fix**: Changed `os.getenv` to `_os.getenv` on line 271.
- **Verified**: `import sticky_proxy_resolver` now succeeds;
  `sticky_proxy_resolver._CACHE_TTL_S` returns 600 as expected.

### CM-44-2: Missing subprocess timeout in panic handler
- **File**: `browser-runtime/hardened_orchestrator.py:2986`
- **Bug**: `subprocess.run(["which", "tc"], capture_output=True)` had no
  timeout. If `which` hangs (e.g., PATH corruption during panic), the panic
  handler blocks indefinitely.
- **Fix**: Added `timeout=2` to match the pattern at line 1050.

## Cross-Module Findings

1. **Singleton sharing works correctly**: `get_muscle_profile("X")` returns
   the same instance on repeated calls; state (e.g., `_pes_keystrokes_remaining`)
   persists across calls.

2. **Entropy is deterministic per persona**: 6/6 biometric attributes
   (age, biological_drift, muscle_speed_factor, tremor_frequency,
   typing_delay_base, hand_dominance) match between two `get_muscle_profile`
   calls for the same persona_id.

3. **HCI model is non-deterministic by design**: `fixation_duration_ms()`
   advances the RNG state, so consecutive calls produce different values
   (226ms vs 178ms). This is intentional - each fixation gets a new sample.

4. **No env var naming conflicts**: Only `BE_COMPOSITOR_JITTER_MS` is shared
   between `behavioral_entropy` and `hardened_orchestrator`, and both
   interpret it identically.

5. **DB schema contract**: `identity_health.py` queries `personas` table
   for `state`, `failure_count`, `last_active`, `cooldown_until` - all 4
   columns match expected schema.

6. **PES counter propagation**: When `record_typing_error()` is called on
   a profile, the `_pes_keystrokes_remaining` counter increments from 0
   to ~15 (random within 5-15 range, per BEN/PES spec).
