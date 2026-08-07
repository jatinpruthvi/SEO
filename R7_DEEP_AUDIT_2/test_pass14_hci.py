"""R7-DEEP-AUDIT-2 Pass 14: HCI reading model fixes.

Tests:
- BEN-10 FIX: HCIReadingModel now imports NUMPY_AVAILABLE
  (was used but never imported, would cause NameError on call)
- BEN-10 FIX: HCIReadingModel now stores biological_drift from the
  persona's profile (was always 1.0 due to getattr default)
- BEN-10 FIX: simulate_idle_period_lognormal uses per-persona drift
"""
import sys
import os
import asyncio

# We need to mock the modules that hci_reading_model depends on
sys.path.insert(0, '/home/user/project/browser-runtime')

# Mock the modules that aren't available
class MockLoguru:
    @staticmethod
    def info(msg, *args): pass
    @staticmethod
    def warning(msg, *args): pass
    @staticmethod
    def debug(msg, *args): pass
    @staticmethod
    def error(msg, *args): pass

class MockBehavioralEntropy:
    BehavioralEntropyGenerator = object
    FocusSwitchModel = object
    NUMPY_AVAILABLE = True

# Try to actually import first
try:
    from behavioral_entropy import BehavioralEntropyGenerator, FocusSwitchModel, NUMPY_AVAILABLE
    print("✅ behavioral_entropy imports OK")
except ImportError as e:
    print(f"❌ behavioral_entropy import failed: {e}")
    sys.exit(1)

def test_ben10_hci_numpy_available_import():
    """The hci_reading_model.py file uses NUMPY_AVAILABLE but
    previously didn't import it. This would cause a NameError at
    runtime when simulate_idle_period_lognormal was called.
    """
    # Re-import hci_reading_model to verify it works
    import importlib
    if 'hci_reading_model' in sys.modules:
        importlib.reload(sys.modules['hci_reading_model'])
    try:
        import hci_reading_model
        # Check NUMPY_AVAILABLE is in hci_reading_model's namespace
        # (it's imported into the module)
        if hasattr(hci_reading_model, 'NUMPY_AVAILABLE'):
            print("  ✅ OK: hci_reading_model has NUMPY_AVAILABLE")
        else:
            print("  ❌ BUG: hci_reading_model does NOT have NUMPY_AVAILABLE")
            return False
        return True
    except Exception as e:
        print(f"  ❌ BUG: import failed: {e}")
        return False

def test_ben10_hci_biological_drift_per_persona():
    """HCIReadingModel should have a biological_drift attribute
    set from the persona's profile. Previously it was always 1.0
    because the attribute was never set (the getattr default in
    simulate_idle_period_lognormal always returned 1.0).
    """
    try:
        import hci_reading_model
        # Need a loguru mock since we don't have it installed
        import unittest.mock
        with unittest.mock.patch('hci_reading_model.logger', MockLoguru()):
            m1 = hci_reading_model.HCIReadingModel("alice_001")
            m2 = hci_reading_model.HCIReadingModel("bob_002")
            m3 = hci_reading_model.HCIReadingModel("charlie_003")
            # Different personas should have different drifts
            drifts = {m1.biological_drift, m2.biological_drift, m3.biological_drift}
            print(f"  drifts: {drifts}")
            if len(drifts) < 2:
                print(f"  ❌ BUG: only {len(drifts)} unique drift values for 3 personas")
                return False
            # All drifts should be in valid range (0.92-1.08)
            for d in drifts:
                if not (0.90 <= d <= 1.10):
                    print(f"  ❌ BUG: drift {d} out of range")
                    return False
            print(f"  ✅ OK: {len(drifts)} unique drifts for 3 personas, all in valid range")
            return True
    except Exception as e:
        print(f"  ❌ EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("R7-DEEP-AUDIT-2 Pass 14: HCI reading model tests")
    print("=" * 60)
    results = []
    tests = [
        ("BEN-10: HCI NUMPY_AVAILABLE import", test_ben10_hci_numpy_available_import),
        ("BEN-10: HCI biological_drift per-persona", test_ben10_hci_biological_drift_per_persona),
    ]
    for name, test_fn in tests:
        print(f"\n[TEST] {name}")
        try:
            ok = test_fn()
            results.append((name, ok))
        except Exception as e:
            import traceback
            print(f"  ❌ EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} tests passed")
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    sys.exit(0 if passed == total else 1)
