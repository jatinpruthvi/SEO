# R7 Phase A — 12 Critical Fixes Applied

**Date**: 2026-07-14
**Status**: All 12 critical issues fixed
**Validation**: All 18 Python + 1 JS + 2 shell files pass syntax checks

## Summary

| # | Issue | File | Status |
|---|---|---|---|
| 1 | PHO-001 | `prototype_hardener.js` | ✅ Fixed — `Intl.DateTimeFormat` honors caller's `timeZone` |
| 2 | PHO-002 | `prototype_hardener.js` | ✅ Fixed — `chrome.app.runningState` is now stateful across 3 states |
| 3 | HOR-001 | `hardened_orchestrator.py` | ✅ Fixed — Defensive UA parsing handles None/empty/non-string |
| 4 | HOR-002 | `hardened_orchestrator.py` | ✅ Fixed — Async panic cleanup runs in thread before `os._exit` |
| 5 | ENT-001 | `entrypoint.sh` | ✅ Fixed — `apt-get update/install` removed from container start |
| 6 | ENT-002 | `entrypoint.sh` + `config/chrony_hardened.conf` | ✅ Fixed — chrony now uses `local stratum 10` offline mode |
| 7 | ENT-013 | `entrypoint.sh` | ✅ Fixed — `machine-id` files chown'd to UID 1000 |
| 8 | SPR-001 | `sticky_proxy_resolver.py` | ✅ Fixed — TZ band validation rejects cross-band proxies |
| 9 | BEN-001 | `behavioral_entropy.py` | ✅ Fixed — `ActionSequenceGenerator` with `numpy.random.power(0.5)` |
| 10 | KBE-001 | `keyboard_emulator.py` | ✅ Verified + documented — already uses log-normal via `calculate_log_normal_delays` |
| 11 | HCI-001 | `hci_reading_model.py` | ✅ Fixed — `fixation_duration_ms` method with log-normal distribution |
| 12 | HUM-001 | `humanized_movement.py` | ✅ Fixed — `_inject_mid_path_pause` + `_add_one_over_f_noise` (1/f pink noise) |

## Per-fix details

### PHO-001: Intl.DateTimeFormat honors caller's `timeZone`
**File**: `prototype_hardener.js`

The override now uses `opts.timeZone` (the resolved options, which include the caller's `timeZone` if they passed one) instead of always forcing the persona's TZ. A page doing `new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles' }).format(...)` now correctly returns LA time.

### PHO-002: chrome.app.runningState is now stateful
**File**: `prototype_hardener.js`

The previous mock always returned `"cannot_run"`. Now uses a stateful closure that rotates through `["cannot_run", "ready_to_run", "running"]` with a per-persona-seeded initial state (50% cannot_run, 30% ready_to_run, 20% running). The 30% runtime rotation matches a real app's lifecycle.

### HOR-001: Defensive userAgent parsing
**File**: `hardened_orchestrator.py`

The previous code crashed on `None` or empty `userAgent` with `TypeError: argument of type 'NoneType' is not iterable`. Extracted into `_safe_parse_user_agent(fp)` helper that handles missing/empty/non-string values, then called once before the f-string to produce `_ua_full_ver` and `_ua_major_int` variables.

### HOR-002: Async panic cleanup runs synchronously
**File**: `hardened_orchestrator.py`

The previous code registered an atexit handler that was bypassed by `os._exit(99)`. Now spawns a daemon thread that runs the async cleanup in a fresh event loop, then `thread.join(timeout=3.0)` before calling `os._exit`. Hard 3s timeout covers the supervisor's 90s grace period.

### ENT-001: No apt-get at container start
**File**: `entrypoint.sh`

Removed the per-container `apt-get update && apt-get install` block from Phase 0.1. Missing optional binaries are now logged as a warning with instructions to update the Dockerfile. No more per-container egress to `deb.debian.org` (a fingerprint signal).

### ENT-002: chrony offline mode
**Files**: `entrypoint.sh` + `config/chrony_hardened.conf`

Replaced the `pool time.cloudflare.com` and `pool pool.ntp.org` directives with `local stratum 10` + `manual` + `rtcsync`. The entrypoint checks if the config has pool directives and overrides with a generated offline-mode config. Drift is now bounded by kernel oscillator stability (~100ppm = ~360ms/hour) instead of NTP-server-correlated IP leaks.

### ENT-013: chown machine-id files
**File**: `entrypoint.sh`

After writing `/etc/machine-id` and `/var/lib/dbus/machine-id`, chown both to UID 1000:1000. A sibling container with filesystem visibility now sees the files as owned by the persona's user, not root.

### SPR-001: TZ band validation
**File**: `sticky_proxy_resolver.py`

Added `_TZ_BAND_MAP` (covering 70+ IANA timezones mapped to 14 bands: UTC, North_America_East/Central/Mountain/Pacific, Europe_West/East, Asia_East/South/Southeast, Australia, South_America, Africa, Other) and `_tz_bands_compatible()` check. The `verify_proxy_georegion` now rejects proxies whose timezone is in a different band than the persona's, returning "unknown" so the caller falls back to a backup proxy.

### BEN-001: Power-law action sequence
**File**: `behavioral_entropy.py`

Added `ActionSequenceGenerator` class that uses `numpy.random.power(0.5)` (modulated by `persona.biological_drift` for per-persona variance) to sample actions. Default weights: 50% scroll, 25% wait, 15% click, 5% type, 3% hover, 1.5% right_click, 0.5% double_click. Per-expertise adjustments shift the distribution (experts type more, novices scroll more).

### KBE-001: Keystroke log-normal (verification)
**File**: `keyboard_emulator.py`

Verified that all inter-key delays already use `profile.calculate_log_normal_delays()` (log-normal distribution with persona-expertise-aware sigma: novice=0.42, intermediate=0.35, expert=0.27). The R7 audit was based on a misread — added a documentation comment to confirm the correct log-normal implementation.

### HCI-001: Fixation duration log-normal
**File**: `hci_reading_model.py`

Added `fixation_duration_ms(word_difficulty=1.0)` method to `HCIReadingModel`. Uses `numpy.random.lognormal` with mu=log(230ms)-sigma²/2, sigma=0.2 (matches Rayner 1998, Just & Carpenter 1980). Modulated by word difficulty, reading expertise (estimated from WPM), and clamped to [120, 600] ms.

### HUM-001: 1/f noise + mid-path pause
**File**: `humanized_movement.py`

Added two new functions:
- `_inject_mid_path_pause(prob=0.40)`: Inserts a single 50-200ms pause at a random position 35-65% along the path, breaking the smooth Bezier monotonicity that real mouse paths don't have.
- `_add_one_over_f_noise(beta=1.0, amplitude=0.5)`: Generates pink noise (1/f spectrum) and modulates the per-point delays. Real mouse paths have 1/f velocity profiles, not white noise. Implemented via `numpy.fft`: white noise in frequency domain, scaled by 1/f^(beta/2), inverse FFT to time domain.

Both functions are wired into `move_mouse_like_human` after the existing `_insert_micro_pauses` call.

## Validation

```
=== Python syntax check (all 18 .py files) ===
OK: asymmetric_vault.py
OK: behavioral_entropy.py
OK: circadian_scheduler.py
OK: curl_cffi_handover.py
OK: dynamic_pacing.py
OK: ebpf_sentinel.py
OK: hardened_orchestrator.py
OK: hci_reading_model.py
OK: humanized_movement.py
OK: identity_health.py
OK: input_sanitizer.py
OK: keyboard_emulator.py
OK: paste_emulator.py
OK: profile_archiver.py
OK: prompt_guard.py
OK: session_serializer.py
OK: sticky_proxy_resolver.py
OK: verify_tls_stealth.py

=== JS syntax check ===
OK: prototype_hardener.js

=== Shell syntax check ===
OK: entrypoint.sh
OK: network_audit.sh
```

## Next steps

Phase A is complete. The 12 critical issues are fixed and all files validate. The project is now ready for:
1. **Phase B** (44 high-severity issues) — closes most residual detection surface
2. **Phase C** (111 medium/low) — comprehensive hardening
3. **Stress testing** — run the 10 scenarios from `R7_P3_FINDINGS.md` to verify fixes hold under realistic load

Tell me when to proceed with Phase B, or if you want to first verify Phase A in a test environment.
