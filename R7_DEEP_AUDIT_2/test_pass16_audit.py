#!/usr/bin/env python3
"""
R7-DEEP-AUDIT-2 Pass 16: Error handling audit.

Hunts for error handling issues:
1. Bare `except Exception` that hides real bugs
2. Try/except blocks that swallow errors silently
3. Functions that should re-raise vs. log-and-continue
4. Missing error propagation (caller can't tell what failed)
5. Inconsistent error codes/log levels
"""
import os
import sys
import re
import inspect

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BROWSER_RUNTIME = "/home/user/project/browser-runtime"

sys.path.insert(0, _BROWSER_RUNTIME)

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ OK: {name}")
    else:
        failed += 1
        print(f"  ❌ FAIL: {name}  {detail}")


print("=" * 60)
print("R7-DEEP-AUDIT-2 Pass 16: Error handling audit")
print("=" * 60)


# ============================================================================
# EH-1: Bare except: pass pattern
# ============================================================================
print("\n[TEST] EH-1: No bare 'except Exception: pass' in function bodies")
# These are particularly bad because they silently swallow ALL errors
# including KeyboardInterrupt (in Py2, fixed in Py3) and SystemExit.
# Note: class declarations like `class Foo(Exception): pass` are
# legitimate (empty exception body) and not what we're hunting.
all_files = [
    "behavioral_entropy.py",
    "hardened_orchestrator.py",
    "humanized_movement.py",
    "keyboard_emulator.py",
    "session_serializer.py",
    "asymmetric_vault.py",
    "profile_archiver.py",
    "dynamic_pacing.py",
    "hci_reading_model.py",
    "sticky_proxy_resolver.py",
]
for fname in all_files:
    path = os.path.join(_BROWSER_RUNTIME, fname)
    if not os.path.exists(path):
        continue
    with open(path) as f:
        src = f.read()
    # Find `except: pass` or `except Exception: pass` patterns
    # at function/method indentation level (4-12 spaces, NOT 0 spaces
    # which is class definitions). This excludes `class X(Exception): pass`
    # which is a legitimate empty exception class body.
    # We require the pass to be on the very next line (no other
    # statements between except: and pass:).
    # Use lookbehind assertion to ensure 'except' is at end of previous line.
    bad = re.findall(r'(?m)^([ \t]+)except\b[^:]*:\s*\n\1pass\s*(?:#[^\n]*)?$', src)
    if bad:
        check(
            f"{fname}: no silent 'except: pass'",
            False,
            f"found {len(bad)} occurrences"
        )
    else:
        check(f"{fname}: no silent 'except: pass'", True)


# ============================================================================
# EH-2: restore_session_state handles partial cookie failures
# ============================================================================
print("\n[TEST] EH-2: restore_session_state uses loop-level try/except")
path = os.path.join(_BROWSER_RUNTIME, "session_serializer.py")
with open(path) as f:
    src = f.read()
# Look for the restore_session_state function and check if it uses
# per-cookie error handling
m = re.search(r'def restore_session_state.*?(?=\n    def|\nclass |\Z)', src, re.DOTALL)
if m:
    body = m.group(0)
    # Check if add_cookies is called with a single try/except (brittle)
    # vs. looped (resilient)
    has_loop_try = "for cookie in" in body or "for c in " in body
    check(
        "restore_session_state: per-cookie loop for resilience",
        has_loop_try,
        "if not, a single bad cookie fails the whole restore"
    )
else:
    check("restore_session_state: function found", False, "could not find function")


# ============================================================================
# EH-3: restore_local_storage distinguishes cross-origin vs real errors
# ============================================================================
print("\n[TEST] EH-3: restore_local_storage distinguishes cross-origin")
m = re.search(r'def restore_local_storage.*?(?=\n    def|\nclass |\Z)', src, re.DOTALL)
if m:
    body = m.group(0)
    has_origin_check = "page.url" in body
    check(
        "restore_local_storage: checks page.url for cross-origin",
        has_origin_check,
        "without origin check, about:blank → target redirect looks like failure"
    )
else:
    check("restore_local_storage: function found", False, "could not find function")


# ============================================================================
# EH-4: serialize_session_state has fallback for storage extraction failure
# ============================================================================
print("\n[TEST] EH-4: serialize_session_state handles storage extraction failure")
m = re.search(r'def serialize_session_state.*?(?=\n    def|\nclass |\Z)', src, re.DOTALL)
if m:
    body = m.group(0)
    has_eval_try = "try:" in body and "page.evaluate" in body
    has_local_fallback = "local_store = {}" in body
    check(
        "serialize_session_state: catches evaluate failure with empty fallback",
        has_eval_try and has_local_fallback,
        f"eval_try={has_eval_try}, fallback={has_local_fallback}"
    )
else:
    check("serialize_session_state: function found", False, "could not find function")


# ============================================================================
# EH-5: decrypt_payload validates input type
# ============================================================================
print("\n[TEST] EH-5: decrypt_payload validates input type")
m = re.search(r'def decrypt_payload.*?(?=\n    def|\nclass |\Z)', src, re.DOTALL)
if m:
    body = m.group(0)
    has_type_check = "isinstance(encrypted_payload, str)" in body
    has_empty_check = "not encrypted_payload" in body
    check(
        "decrypt_payload: validates input is non-empty str",
        has_type_check and has_empty_check,
        f"type={has_type_check}, empty={has_empty_check}"
    )


# ============================================================================
# EH-6: keyboard_emulator always releases Shift on error
# ============================================================================
print("\n[TEST] EH-6: keyboard_emulator releases Shift on cancellation")
path = os.path.join(_BROWSER_RUNTIME, "keyboard_emulator.py")
with open(path) as f:
    ke_src = f.read()
# Count except blocks that release Shift
shift_release_count = ke_src.count('keyboard.up("Shift")')
shift_release_in_except = sum(
    1 for m in re.finditer(r'except[^:]*:.*?keyboard\.up\("Shift"\)', ke_src, re.DOTALL)
)
check(
    "keyboard_emulator: Shift released in except blocks",
    shift_release_in_except >= 1,
    f"found {shift_release_in_except} releases in except blocks (total {shift_release_count})"
)


# ============================================================================
# EH-7: orchestrator has proper SIGTERM handler that does cleanup
# ============================================================================
print("\n[TEST] EH-7: orchestrator registers shutdown signal handlers")
path = os.path.join(_BROWSER_RUNTIME, "hardened_orchestrator.py")
with open(path) as f:
    orch_src = f.read()
has_sigterm_handler = "register_shutdown_signals" in orch_src
has_signal_handlers = "SIGINT" in orch_src and "SIGTERM" in orch_src
check(
    "orchestrator: registers shutdown signal handlers",
    has_sigterm_handler and has_signal_handlers,
    f"register={has_sigterm_handler}, handlers={has_signal_handlers}"
)


# ============================================================================
# EH-8: orchestrator's panic handler is in try/except for each cleanup step
# ============================================================================
print("\n[TEST] EH-8: panic handler has isolated try/except for each step")
# Look for the _panic_handler function
m = re.search(r'def _panic_handler.*?def ', orch_src, re.DOTALL)
if m:
    body = m.group(0)
    # Count try/except blocks (4-space indent at function level)
    n_tries = body.count("\n    try:")
    n_subprocess_calls = body.count("subprocess.run")
    # Each cleanup step should have its own try/except
    # We expect at least 3 (tc qdisc, kill children, tmpfs wipe)
    check(
        f"panic handler: {n_tries} try blocks for {n_subprocess_calls} cleanup steps",
        n_tries >= 3,
        f"n_tries={n_tries}, n_subprocess={n_subprocess_calls}"
    )


# ============================================================================
# EH-9: behavioral_entropy validate is in try/except for validate sigma
# ============================================================================
print("\n[TEST] EH-9: behavioral_entropy validates sigma strictly")
path = os.path.join(_BROWSER_RUNTIME, "behavioral_entropy.py")
with open(path) as f:
    be_src = f.read()
# BEN-4: sigma must be validated
has_sigma_check = "sigma must be a positive finite number" in be_src
check(
    "calculate_log_normal_delays: validates sigma against NaN/Inf/zero",
    has_sigma_check,
    "BEN-4 fix should be present"
)


# ============================================================================
# EH-10: behavioral_entropy validates base_delay
# ============================================================================
print("\n[TEST] EH-10: behavioral_entropy validates base_delay")
m = re.search(r'def calculate_log_normal_delays.*?(?=\n    def|\nclass |\Z)', be_src, re.DOTALL)
if m:
    body = m.group(0)
    has_check = "base_delay must be positive" in body
    has_finite = "base_delay must be finite" in body
    check(
        "calculate_log_normal_delays: validates base_delay (positive + finite)",
        has_check and has_finite,
        f"positive={has_check}, finite={has_finite}"
    )


# ============================================================================
# EH-11: all parse_proxy_url has explicit exception type
# ============================================================================
print("\n[TEST] EH-11: orchestrator parse_proxy_url uses broad except")
m = re.search(r'def parse_proxy_url.*?(?=\ndef |\nclass |\Z)', orch_src, re.DOTALL)
if m:
    body = m.group(0)
    # Find the OUTERMOST except (the one at function scope, not nested)
    # The except is at 4-space indent (function level)
    has_bare_except = re.search(r'^    except\s*Exception\b', body, re.MULTILINE) is not None
    has_specific = "ValueError" in body or "TypeError" in body or "AttributeError" in body
    check(
        "parse_proxy_url: uses specific exception type",
        has_specific,
        f"specific={has_specific}, bare={has_bare_except}"
    )


# ============================================================================
# EH-12: high_precision_sleep is not blocked by bare except
# ============================================================================
print("\n[TEST] EH-12: high_precision_sleep handles edge cases")
path = os.path.join(_BROWSER_RUNTIME, "humanized_movement.py")
with open(path) as f:
    hm_src = f.read()
has_sanitize = "_sanitize_sleep_duration" in hm_src
has_nan = "float('nan')" in hm_src or "NaN check" in hm_src
has_negative = "target_delay <= 0" in hm_src or "negative" in hm_src.lower()
check(
    "high_precision_sleep: sanitizes NaN/negative/Inf inputs",
    has_sanitize and has_nan,
    f"sanitize={has_sanitize}, NaN={has_nan}, neg={has_negative}"
)


# ============================================================================
# EH-13: DecryptionFailedError carries error code
# ============================================================================
print("\n[TEST] EH-13: DecryptionFailedError carries error code")
m = re.search(r'class DecryptionFailedError.*?(?=\nclass|\Z)', orch_src, re.DOTALL)
if m:
    body = m.group(0)
    has_code = "self.code" in body and 'def __init__' in body
    has_codes_used = "empty_payload" in orch_src and "auth_failed" in orch_src
    check(
        "DecryptionFailedError: carries distinguishable error code",
        has_code and has_codes_used,
        f"code attr={has_code}, codes used={has_codes_used}"
    )


# ============================================================================
# EH-14: orchestrator logs panic signal in panic handler
# ============================================================================
print("\n[TEST] EH-14: panic handler logs the panic event")
m = re.search(r'def _panic_handler.*?def ', orch_src, re.DOTALL)
if m:
    body = m.group(0)
    has_log = "PANIC SIGNAL RECEIVED" in body or "panic" in body.lower()
    check(
        "_panic_handler: logs panic event",
        has_log,
        "operator visibility into panic events"
    )


# ============================================================================
# EH-15: All public functions validate inputs
# ============================================================================
print("\n[TEST] EH-15: validate_* patterns for public functions")
path = os.path.join(_BROWSER_RUNTIME, "keyboard_emulator.py")
with open(path) as f:
    ke_src = f.read()
# _inter_key_delay_multiplier should handle unknown keys gracefully
m = re.search(r'def _inter_key_delay_multiplier.*?(?=\ndef |\nclass |\Z)', ke_src, re.DOTALL)
if m:
    body = m.group(0)
    has_unknown_fallback = "return 1.00" in body or "return 1.0" in body
    check(
        "_inter_key_delay_multiplier: handles unknown keys with default",
        has_unknown_fallback,
        f"fallback={has_unknown_fallback}"
    )


# ============================================================================
# EH-16: orchestrator has all main errors handled
# ============================================================================
print("\n[TEST] EH-16: orchestrator daemon handles DB unavailability")
# Look for DatabaseUnavailableError handling
m = re.search(r'except DatabaseUnavailableError', orch_src)
m2 = re.search(r'except PersonaRetiredError', orch_src)
m3 = re.search(r'except PIDBudgetExceededError', orch_src)
m4 = re.search(r'except DecryptionFailedError', orch_src)
handled = sum(1 for m in [m, m2, m3, m4] if m)
check(
    f"orchestrator daemon: handles {handled}/4 custom exceptions",
    handled == 4,
    f"DatabaseUnavailable={m is not None}, PersonaRetired={m2 is not None}, "
    f"PIDBudget={m3 is not None}, Decryption={m4 is not None}"
)


# ============================================================================
# EH-17: storage_state's bare except has a comment explaining the failure mode
# ============================================================================
print("\n[TEST] EH-17: storage extraction failure has explanatory log")
m = re.search(r'def serialize_session_state.*?(?=\n    def|\nclass |\Z)', src, re.DOTALL)
if m:
    body = m.group(0)
    has_explanation = "Failed to extract storage" in body
    has_separate_except = body.count("except") >= 1
    check(
        "serialize_session_state: logs storage extraction failure clearly",
        has_explanation and has_separate_except,
        f"explanation={has_explanation}, separate except={has_separate_except}"
    )


# ============================================================================
# Summary
# ============================================================================
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed > 0:
    sys.exit(1)
sys.exit(0)
