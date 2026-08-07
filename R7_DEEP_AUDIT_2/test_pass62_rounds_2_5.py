"""
test_pass62_rounds_2_5.py - Tests for Round 2-5 suggestion implementations
==========================================================================

R7-DEEP-AUDIT-2 implementation tests for the pending items identified
in the Round 2-5 deep audits. Each test verifies a specific patch or
fix in the orchestrator, hardener, or iptables config.

Test categories:
- Round 2-4: API surface gaps (WorkerNavigator, CSS Houdini, etc.)
- Round 5: New API surface patches (WebTransport, USB/Serial/HID/etc.)
- Orchestrator: SingletonLock cleanup, Font coherence, etc.
- Validation: all original code still passes

Total: 80+ tests
"""

import os
import re
import sys
import json
import time
import unittest
import subprocess
from pathlib import Path

# Project paths
PROJECT_ROOT = Path("/home/user/project")
BROWSER_RUNTIME = PROJECT_ROOT / "browser-runtime"
DATABASE = PROJECT_ROOT / "database"
CONFIG = PROJECT_ROOT / "config"
AUDIT_DIR = Path("/home/user/R7_DEEP_AUDIT_2")

HARDENER = BROWSER_RUNTIME / "prototype_hardener.js"
ORCHESTRATOR = BROWSER_RUNTIME / "hardened_orchestrator.py"
IPTABLES_SCRIPT = CONFIG / "iptables_docker.sh"
SCHEMA = DATABASE / "schema.sql"


def _read(path):
    with open(path, "r") as f:
        return f.read()


class TestRound2WorkerNavigatorExpansion(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 3 FIX: WorkerNavigator missing properties"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_worker_navigator_geolocation_patched(self):
        """WorkerNavigator.geolocation should be patched in Worker scope."""
        self.assertIn("'geolocation'", self.hardener)

    def test_worker_navigator_service_worker_patched(self):
        """WorkerNavigator.serviceWorker should be set to undefined."""
        self.assertIn("'serviceWorker'", self.hardener)

    def test_worker_navigator_permissions_patched(self):
        """WorkerNavigator.permissions should be patched with a no-op query."""
        self.assertIn("'permissions'", self.hardener)

    def test_worker_navigator_storage_patched(self):
        """WorkerNavigator.storage should be patched with estimate/persist."""
        self.assertIn("'storage'", self.hardener)
        self.assertIn("estimate", self.hardener)

    def test_worker_navigator_clipboard_patched(self):
        """WorkerNavigator.clipboard should be patched."""
        self.assertIn("'clipboard'", self.hardener)

    def test_worker_navigator_userAgentData_patched(self):
        """WorkerNavigator.userAgentData should be patched."""
        self.assertIn("'userAgentData'", self.hardener)

    def test_worker_navigator_locks_patched(self):
        """WorkerNavigator.locks should be patched."""
        self.assertIn("'locks'", self.hardener)

    def test_worker_navigator_gpu_patched(self):
        """WorkerNavigator.gpu should be set to undefined."""
        self.assertIn("'gpu'", self.hardener)

    def test_worker_navigator_restricted_apis(self):
        """WorkerNavigator restricted APIs (usb, serial, hid, bluetooth, ink, nfc) should be set to undefined."""
        for api in ['usb', 'serial', 'hid', 'bluetooth', 'ink', 'nfc']:
            # Each is set to undefined via Object.defineProperty
            # We just check the API names appear in the worker override context
            self.assertIn(f"'{api}'", self.hardener)


class TestRound3ModuleWorkerBypass(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 3 FIX: Module Worker bypass"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_module_worker_no_longer_bypasses(self):
        """Module workers should no longer use RawWorker directly."""
        # The fix removed the early `return new RawWorker(stringUrl, options);` for module type.
        # The new code creates a blob-based wrapper for module workers.
        self.assertIn("R7-DEEP-AUDIT-2 ROUND 3 FIX: Module Worker bypass", self.hardener)

    def test_module_worker_uses_blob_wrapper(self):
        """Module worker should use a Blob URL wrapper that imports the original module."""
        # Check for the dynamic import() pattern inside the module worker
        self.assertIn("import(${JSON.stringify(absoluteUrl)})", self.hardener)

    def test_module_worker_preserves_fp_config(self):
        """Module worker should set FP_CONFIG with all persona fields."""
        # The new module worker code includes a full FP_CONFIG block
        self.assertIn("hardwareConcurrency: FP_CONFIG.hardwareConcurrency", self.hardener)


class TestRound4PointerEventTilt(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 3 FIX: PointerEvent tilt/altitude patches"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_pointer_event_tiltx_patched(self):
        """PointerEvent.tiltX should be patched."""
        self.assertIn("'tiltX'", self.hardener)

    def test_pointer_event_tilty_patched(self):
        """PointerEvent.tiltY should be patched."""
        self.assertIn("'tiltY'", self.hardener)

    def test_pointer_event_altitude_angle_patched(self):
        """PointerEvent.altitudeAngle should be patched."""
        self.assertIn("'altitudeAngle'", self.hardener)

    def test_pointer_event_azimuth_angle_patched(self):
        """PointerEvent.azimuthAngle should be patched."""
        self.assertIn("'azimuthAngle'", self.hardener)

    def test_pointer_event_twist_patched(self):
        """PointerEvent.twist should be patched."""
        self.assertIn("'twist'", self.hardener)

    def test_pointer_event_pen_only(self):
        """Tilt/altitude properties should only return non-zero for pen pointerType."""
        self.assertIn("this.pointerType === 'pen'", self.hardener)


class TestRound4ShapeDetection(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 4: Shape Detection APIs"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_barcode_detector_blocked(self):
        """BarcodeDetector should throw on construction."""
        self.assertIn("'BarcodeDetector'", self.hardener)

    def test_text_detector_blocked(self):
        """TextDetector should throw on construction."""
        self.assertIn("'TextDetector'", self.hardener)

    def test_face_detector_blocked(self):
        """FaceDetector should throw on construction."""
        self.assertIn("'FaceDetector'", self.hardener)


class TestRound4NewAPIs(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 4: New Chrome 120+ APIs"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_pressure_observer_removed(self):
        """PressureObserver should be removed."""
        self.assertIn("'PressureObserver'", self.hardener)

    def test_eye_dropper_blocked(self):
        """EyeDropper should throw on construction."""
        self.assertIn("'EyeDropper'", self.hardener)

    def test_edit_context_removed(self):
        """EditContext should be removed."""
        self.assertIn("'EditContext'", self.hardener)

    def test_idle_detector_removed(self):
        """IdleDetector should be removed."""
        self.assertIn("'IdleDetector'", self.hardener)

    def test_window_controls_overlay_removed(self):
        """WindowControlsOverlay should be removed."""
        self.assertIn("'WindowControlsOverlay'", self.hardener)

    def test_document_picture_in_picture_removed(self):
        """documentPictureInPicture should be removed."""
        self.assertIn("'documentPictureInPicture'", self.hardener)

    def test_webnn_removed(self):
        """WebNN (NN) should be removed."""
        self.assertIn("'NN'", self.hardener)

    def test_storage_bucket_removed(self):
        """StorageBucket should be removed."""
        self.assertIn("'StorageBucket'", self.hardener)

    def test_storage_buckets_navigator_removed(self):
        """navigator.storageBuckets should be removed."""
        self.assertIn("'storageBuckets'", self.hardener)

    def test_attribution_reporting_removed(self):
        """AttributionReporting should be removed."""
        self.assertIn("'AttributionReporting'", self.hardener)

    def test_topics_browsing_removed(self):
        """BrowsingTopics and document.browsingTopics should be removed."""
        self.assertIn("'BrowsingTopics'", self.hardener)
        self.assertIn("'browsingTopics'", self.hardener)

    def test_fenced_frame_config_removed(self):
        """FencedFrameConfig should be removed."""
        self.assertIn("'FencedFrameConfig'", self.hardener)

    def test_performance_server_timing_removed(self):
        """PerformanceServerTiming should be removed."""
        self.assertIn("'PerformanceServerTiming'", self.hardener)

    def test_reporting_observer_removed(self):
        """ReportingObserver should be removed."""
        self.assertIn("'ReportingObserver'", self.hardener)

    def test_wakelock_sensor_removed(self):
        """WakeLockSensor should be removed."""
        self.assertIn("'WakeLockSensor'", self.hardener)

    def test_resize_observer_patched(self):
        """ResizeObserver should be wrapped (not raw)."""
        self.assertIn("'ResizeObserver'", self.hardener)
        # Should be wrapped, not removed
        self.assertIn("rawRO", self.hardener)

    def test_css_houdini_register_property_blocked(self):
        """CSS.registerProperty should throw NotSupportedError."""
        self.assertIn("CSS.registerProperty", self.hardener)
        self.assertIn("NotSupportedError", self.hardener)

    def test_audio_worklet_disabled(self):
        """AudioContext.audioWorklet should be disabled."""
        self.assertIn("audioWorklet", self.hardener)

    def test_paint_worklet_disabled(self):
        """CSS.paintWorklet should be disabled."""
        self.assertIn("paintWorklet", self.hardener)

    def test_layout_worklet_disabled(self):
        """CSS.layoutWorklet should be disabled."""
        self.assertIn("layoutWorklet", self.hardener)

    def test_attach_internals_blocked(self):
        """Element.attachInternals should be blocked."""
        self.assertIn("attachInternals", self.hardener)

    def test_css_unit_value_removed(self):
        """CSSUnitValue constructor should be blocked."""
        self.assertIn("'CSSUnitValue'", self.hardener)

    def test_css_keyword_value_removed(self):
        """CSSKeywordValue constructor should be blocked."""
        self.assertIn("'CSSKeywordValue'", self.hardener)

    def test_css_image_value_removed(self):
        """CSSImageValue constructor should be blocked."""
        self.assertIn("'CSSImageValue'", self.hardener)

    def test_css_math_value_removed(self):
        """CSSMathValue constructor should be blocked."""
        self.assertIn("'CSSMathValue'", self.hardener)

    def test_css_transform_value_removed(self):
        """CSSTransformValue constructor should be blocked."""
        self.assertIn("'CSSTransformValue'", self.hardener)

    def test_css_skew_removed(self):
        """CSSSkew constructor should be blocked."""
        self.assertIn("'CSSSkew'", self.hardener)

    def test_css_scale_removed(self):
        """CSSScale constructor should be blocked."""
        self.assertIn("'CSSScale'", self.hardener)

    def test_css_rotation_removed(self):
        """CSSRotation constructor should be blocked."""
        self.assertIn("'CSSRotation'", self.hardener)

    def test_attribute_style_map_removed(self):
        """Element.attributeStyleMap should be set to undefined."""
        self.assertIn("'attributeStyleMap'", self.hardener)

    def test_style_map_removed(self):
        """CSSStyleDeclaration.styleMap should be set to undefined."""
        self.assertIn("'styleMap'", self.hardener)

    def test_css_typed_om_houdini_section(self):
        """CSS Typed OM section should be present in Round 3-4 patches."""
        self.assertIn("_cssTypedOmClasses", self.hardener)
        self.assertIn("CSSStyleValue", self.hardener)


class TestRound5WebTransport(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5 CRITICAL: WebTransport API bypass"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_webtransport_throws_typeerror(self):
        """WebTransport should throw TypeError to prevent QUIC IP leak."""
        self.assertIn("window.WebTransport", self.hardener)
        self.assertIn("'WebTransport'", self.hardener)
        # The error message should be present
        self.assertIn("not supported in this context", self.hardener)

    def test_webtransport_critical_priority(self):
        """WebTransport patch should be in the critical ROUND 5 section."""
        # Check for ROUND 5 comment
        self.assertIn("R7-DEEP-AUDIT-2 ROUND 5", self.hardener)
        self.assertIn("WebTransport", self.hardener)


class TestRound5HardwareAPIs(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5: USB/Serial/HID/Bluetooth/NFC hardware APIs"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_usb_blocked(self):
        """WebUSB should throw on construction."""
        self.assertIn("'USB'", self.hardener)
        self.assertIn("'WebUSB is not supported'", self.hardener)

    def test_serial_blocked(self):
        """Web Serial should throw on construction."""
        self.assertIn("'Serial'", self.hardener)
        self.assertIn("'Web Serial is not supported'", self.hardener)

    def test_hid_blocked(self):
        """Web HID should throw on construction."""
        self.assertIn("'HID'", self.hardener)
        self.assertIn("'Web HID is not supported'", self.hardener)

    def test_bluetooth_blocked(self):
        """Web Bluetooth should throw on construction."""
        self.assertIn("'Bluetooth'", self.hardener)
        self.assertIn("'Web Bluetooth is not supported'", self.hardener)

    def test_nfc_blocked(self):
        """Web NFC should throw on construction."""
        self.assertIn("'NFC'", self.hardener)
        self.assertIn("'Web NFC is not supported'", self.hardener)


class TestRound5FileSystemAccess(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5: File System Access API"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_show_directory_picker_blocked(self):
        """showDirectoryPicker should reject with NotAllowedError."""
        self.assertIn("'showDirectoryPicker'", self.hardener)

    def test_show_open_file_picker_blocked(self):
        """showOpenFilePicker should reject with NotAllowedError."""
        self.assertIn("'showOpenFilePicker'", self.hardener)

    def test_show_save_file_picker_blocked(self):
        """showSaveFilePicker should reject with NotAllowedError."""
        self.assertIn("'showSaveFilePicker'", self.hardener)

    def test_filesystem_file_handle_removed(self):
        """FileSystemFileHandle should be removed."""
        self.assertIn("'FileSystemFileHandle'", self.hardener)

    def test_filesystem_directory_handle_removed(self):
        """FileSystemDirectoryHandle should be removed."""
        self.assertIn("'FileSystemDirectoryHandle'", self.hardener)


class TestRound5WebXR(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5: WebXR / VR / AR APIs"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_navigator_xr_removed(self):
        """navigator.xr should be removed."""
        self.assertIn("'xr'", self.hardener)
        self.assertIn("'navigator.xr'", self.hardener)

    def test_xr_system_global_removed(self):
        """window.XRSystem should be removed."""
        self.assertIn("'XRSystem'", self.hardener)


class TestRound5CrossOriginIsolated(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5: crossOriginIsolated property"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_cross_origin_isolated_patched(self):
        """crossOriginIsolated should be locked to false (matches non-COOP+COEP pages)."""
        self.assertIn("'crossOriginIsolated'", self.hardener)
        self.assertIn("get: () => false", self.hardener)


class TestRound5CookieStore(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5: Async Cookies API (CookieStore)"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_cookie_store_removed(self):
        """window.cookieStore should be removed."""
        self.assertIn("'cookieStore'", self.hardener)

    def test_cookie_store_class_removed(self):
        """window.CookieStore should be removed."""
        self.assertIn("'CookieStore'", self.hardener)

    def test_cookie_change_event_removed(self):
        """window.CookieChangeEvent should be removed."""
        self.assertIn("'CookieChangeEvent'", self.hardener)


class TestRound5WebShare(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 5: Web Share API"""

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_navigator_share_blocked(self):
        """navigator.share should reject."""
        self.assertIn("'navigator.share'", self.hardener)

    def test_navigator_can_share_patched(self):
        """navigator.canShare should return false."""
        self.assertIn("'navigator.canShare'", self.hardener)


class TestSingletonLockCleanup(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 1 FIX: Chrome SingletonLock cleanup"""

    def setUp(self):
        self.orchestrator = _read(ORCHESTRATOR)

    def test_singleton_lock_cleanup_present(self):
        """SingletonLock cleanup should be in the orchestrator."""
        # The format string uses f-string with variable, so check both parts
        self.assertIn("R7-RO1: Removed stale", self.orchestrator)
        self.assertIn("SingletonLock", self.orchestrator)

    def test_singleton_lock_uses_correct_paths(self):
        """SingletonLock cleanup should handle all 3 lock names."""
        self.assertIn("SingletonLock", self.orchestrator)
        self.assertIn("SingletonLockSymlink", self.orchestrator)
        self.assertIn("SingletonCookie", self.orchestrator)

    def test_singleton_lock_is_robust(self):
        """SingletonLock cleanup should handle errors gracefully (try/except)."""
        # Find the cleanup block (use the prefix that doesn't have a variable)
        idx = self.orchestrator.find("R7-RO1: Removed stale")
        self.assertGreater(idx, 0)
        # Look backwards for the try: block (within 300 chars)
        prev_block = self.orchestrator[max(0, idx-300):idx]
        self.assertIn("try:", prev_block, "SingletonLock cleanup should be wrapped in try/except")
        # Look forward for except block
        next_block = self.orchestrator[idx:idx+500]
        self.assertIn("except", next_block)


class TestValidationIntegrity(unittest.TestCase):
    """Verify the code still parses and basic structure is intact"""

    def test_hardener_compiles(self):
        """The JS hardener should pass node --check."""
        result = subprocess.run(
            ["node", "--check", str(HARDENER)],
            capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, f"node --check failed: {result.stderr}")

    def test_orchestrator_compiles(self):
        """The Python orchestrator should pass py_compile."""
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(ORCHESTRATOR)],
            capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, f"py_compile failed: {result.stderr}")

    def test_iptables_docker_compiles(self):
        """The iptables_docker.sh should pass bash -n syntax check."""
        if IPTABLES_SCRIPT.exists():
            result = subprocess.run(
                ["bash", "-n", str(IPTABLES_SCRIPT)],
                capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0, f"bash -n failed: {result.stderr}")

    def test_hardener_has_round5_section(self):
        """The hardener should have a ROUND 5 section."""
        hardener = _read(HARDENER)
        self.assertIn("ROUNDS 2-5: NEW API SURFACE PATCHES", hardener)

    def test_hardener_has_orchestrator_singleton_lock(self):
        """The orchestrator should have the SingletonLock cleanup."""
        orch = _read(ORCHESTRATOR)
        self.assertIn("R7-RO1: Removed stale", orch)
        self.assertIn("SingletonLock", orch)


class TestRound5SummaryMarker(unittest.TestCase):
    """Summary test for Round 2-5 implementation completeness"""

    def test_summary_marker(self):
        """A single marker test to confirm Round 2-5 patches are present."""
        hardener = _read(HARDENER)
        markers = {
            "Module Worker bypass (R3)": "R7-DEEP-AUDIT-2 ROUND 3 FIX: Module Worker bypass",
            "WorkerNavigator expansion (R3)": "R7-DEEP-AUDIT-2 ROUND 3 FIX: WorkerNavigator missing",
            "PointerEvent tilt (R3)": "'tiltX'",
            "Shape Detection (R4)": "'BarcodeDetector'",
            "EyeDropper (R4)": "'EyeDropper'",
            "IdleDetector (R4)": "'IdleDetector'",
            "WebNN (R4)": "'NN'",
            "StorageBuckets (R4)": "'StorageBucket'",
            "Topics (R4)": "'BrowsingTopics'",
            "FencedFrame (R4)": "'FencedFrameConfig'",
            "PerformanceServerTiming (R4)": "'PerformanceServerTiming'",
            "ReportingObserver (R4)": "'ReportingObserver'",
            "WakeLockSensor (R4)": "'WakeLockSensor'",
            "ResizeObserver (R4)": "'ResizeObserver'",
            "CSS Houdini (R4)": "CSS.registerProperty",
            "AudioWorklet (R4)": "audioWorklet",
            "PaintWorklet (R4)": "paintWorklet",
            "Element.attachInternals (R4)": "attachInternals",
            "WebTransport (R5)": "WebTransport is not supported",
            "USB (R5)": "'WebUSB is not supported'",
            "Serial (R5)": "'Web Serial is not supported'",
            "HID (R5)": "'Web HID is not supported'",
            "Bluetooth (R5)": "'Web Bluetooth is not supported'",
            "NFC (R5)": "'Web NFC is not supported'",
            "showDirectoryPicker (R5)": "'showDirectoryPicker'",
            "showOpenFilePicker (R5)": "'showOpenFilePicker'",
            "showSaveFilePicker (R5)": "'showSaveFilePicker'",
            "WebXR (R5)": "'navigator.xr'",
            "XRSystem (R5)": "'XRSystem'",
            "crossOriginIsolated (R5)": "'crossOriginIsolated'",
            "cookieStore (R5)": "'cookieStore'",
            "CookieStore class (R5)": "'CookieStore'",
            "Web Share (R5)": "'navigator.share'",
            "SingletonLock cleanup (R1 fix in orchestrator)": "R7-RO1: Removed stale",
        }
        # The orchestrator SingletonLock is in a separate file
        orch = _read(ORCHESTRATOR)

        for name, marker in markers.items():
            # Last item is in orchestrator, not hardener
            if "orchestrator" in name.lower():
                self.assertIn(marker, orch, f"Missing {name}: '{marker}'")
            else:
                self.assertIn(marker, hardener, f"Missing {name}: '{marker}'")


class TestRound6MediaQueryGap(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 6: 5 missing CSS media query types

    These are newer CSS Media Queries Level 5 features (Chrome 120+)
    that were not in the original mediaSpoofs table. They can be
    probed to detect HDR display, PWA installation, accessibility
    settings, and other persona characteristics.
    """

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_dynamic_range_standard_present(self):
        """(dynamic-range: standard) should be in mediaSpoofs, returning true."""
        self.assertIn("'(dynamic-range: standard)': true", self.hardener)

    def test_dynamic_range_high_present(self):
        """(dynamic-range: high) should be in mediaSpoofs, returning false (no HDR)."""
        self.assertIn("'(dynamic-range: high)': false", self.hardener)

    def test_display_mode_browser_present(self):
        """(display-mode: browser) should return true (non-PWA)."""
        self.assertIn("'(display-mode: browser)': true", self.hardener)

    def test_display_mode_standalone_absent(self):
        """(display-mode: standalone) should return false (not installed as PWA)."""
        self.assertIn("'(display-mode: standalone)': false", self.hardener)

    def test_inverted_colors_none_present(self):
        """(inverted-colors: none) should return true (no accessibility setting)."""
        self.assertIn("'(inverted-colors: none)': true", self.hardener)

    def test_inverted_colors_inverted_absent(self):
        """(inverted-colors: inverted) should return false."""
        self.assertIn("'(inverted-colors: inverted)': false", self.hardener)

    def test_forced_colors_none_present(self):
        """(forced-colors: none) should return true (no high-contrast mode)."""
        self.assertIn("'(forced-colors: none)': true", self.hardener)

    def test_forced_colors_active_absent(self):
        """(forced-colors: active) should return false."""
        self.assertIn("'(forced-colors: active)': false", self.hardener)

    def test_prefers_reduced_transparency_present(self):
        """(prefers-reduced-transparency: no-preference) should be in mediaSpoofs."""
        self.assertIn("'(prefers-reduced-transparency: no-preference)': true", self.hardener)

    def test_prefers_reduced_data_present(self):
        """(prefers-reduced-data: no-preference) should be in mediaSpoofs."""
        self.assertIn("'(prefers-reduced-data: no-preference)': true", self.hardener)


class TestRound7ComprehensiveMQL5(unittest.TestCase):
    """R7-DEEP-AUDIT-2 ROUND 7: Comprehensive CSS Media Queries Level 5 coverage

    After Round 6 added 5 missing media query types, this round audits
    ALL remaining MQL5 discrete features supported in Chrome 120+ to
    ensure no persona-claim contradictions can be detected by anti-bot
    scripts that probe the full media query surface.

    Coverage:
    - prefers-contrast (all 4 values per W3C)
    - pointer / any-pointer (all 3 values)
    - display-mode (5 values including picture-in-picture)
    - video-color-gamut / video-dynamic-range
    - scripting / update (Chrome 120+)
    - overflow-block / overflow-inline
    - grid / scan
    - environment-blending / nav-controls (XR; forward-compat)
    """

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_prefers_contrast_all_4_values(self):
        """prefers-contrast should have all 4 W3C values."""
        for value in ['no-preference', 'less', 'more', 'custom']:
            self.assertIn(
                f"'(prefers-contrast: {value})':",
                self.hardener,
                f"Missing prefers-contrast: {value}"
            )

    def test_pointer_all_3_values(self):
        """pointer should have all 3 W3C values (fine, coarse, none)."""
        for value in ['fine', 'coarse', 'none']:
            self.assertIn(
                f"'(pointer: {value})':",
                self.hardener,
                f"Missing pointer: {value}"
            )

    def test_any_pointer_all_3_values(self):
        """any-pointer should have all 3 W3C values."""
        for value in ['fine', 'coarse', 'none']:
            self.assertIn(
                f"'(any-pointer: {value})':",
                self.hardener,
                f"Missing any-pointer: {value}"
            )

    def test_any_hover_none_present(self):
        """any-hover: none should be present (was missing in R5)."""
        self.assertIn("'(any-hover: none)':", self.hardener)

    def test_display_mode_picture_in_picture(self):
        """display-mode: picture-in-picture should be present (Chrome 120+)."""
        self.assertIn("'(display-mode: picture-in-picture)':", self.hardener)

    def test_video_color_gamut_all_3_values(self):
        """video-color-gamut should have all 3 W3C values."""
        for value in ['srgb', 'p3', 'rec2020']:
            self.assertIn(
                f"'(video-color-gamut: {value})':",
                self.hardener,
                f"Missing video-color-gamut: {value}"
            )

    def test_video_dynamic_range(self):
        """video-dynamic-range should have standard and high."""
        self.assertIn("'(video-dynamic-range: standard)':", self.hardener)
        self.assertIn("'(video-dynamic-range: high)':", self.hardener)

    def test_scripting_media_query(self):
        """scripting media query (Chrome 120+) should be present."""
        for value in ['none', 'initial-only', 'enabled']:
            self.assertIn(
                f"'(scripting: {value})':",
                self.hardener,
                f"Missing scripting: {value}"
            )

    def test_update_media_query(self):
        """update media query (Chrome 113+) should be present."""
        for value in ['none', 'slow', 'fast']:
            self.assertIn(
                f"'(update: {value})':",
                self.hardener,
                f"Missing update: {value}"
            )

    def test_overflow_block(self):
        """overflow-block should have all 3 values (none, scroll, paged)."""
        for value in ['none', 'scroll', 'paged']:
            self.assertIn(
                f"'(overflow-block: {value})':",
                self.hardener,
                f"Missing overflow-block: {value}"
            )

    def test_overflow_inline(self):
        """overflow-inline should have both values (none, scroll)."""
        for value in ['none', 'scroll']:
            self.assertIn(
                f"'(overflow-inline: {value})':",
                self.hardener,
                f"Missing overflow-inline: {value}"
            )

    def test_grid_media_query(self):
        """grid media query (bitmap=0, grid=1) should be present."""
        self.assertIn("'(grid: 0)':", self.hardener)
        self.assertIn("'(grid: 1)':", self.hardener)

    def test_scan_media_query(self):
        """scan media query (interlace, progressive) should be present."""
        self.assertIn("'(scan: interlace)':", self.hardener)
        self.assertIn("'(scan: progressive)':", self.hardener)

    def test_environment_blending_xr(self):
        """environment-blending (XR-specific) should be present for forward-compat."""
        for value in ['opaque', 'additive', 'subtractive']:
            self.assertIn(
                f"'(environment-blending: {value})':",
                self.hardener,
                f"Missing environment-blending: {value}"
            )

    def test_nav_controls_xr(self):
        """nav-controls (XR-specific) should be present for forward-compat."""
        self.assertIn("'(nav-controls: none)':", self.hardener)
        self.assertIn("'(nav-controls: back)':", self.hardener)

    def test_total_media_query_count(self):
        """The mediaSpoofs table should have at least 60 entries (was 31 before R7)."""
        # Count all entries in mediaSpoofs (lines like '(...): true/false')
        # This is a smoke test that the table is comprehensive.
        import re
        count = 0
        in_media_spoofs = False
        for line in self.hardener.split('\n'):
            if 'const mediaSpoofs = {' in line:
                in_media_spoofs = True
                continue
            if in_media_spoofs and line.strip().startswith('};'):
                break
            if in_media_spoofs and re.search(r"'\([^']+':", line):
                count += 1
        self.assertGreaterEqual(
            count, 60,
            f"Expected at least 60 media query entries, found {count}"
        )


class TestPass13CrossAPIConsistency(unittest.TestCase):
    """R7-DEEP-AUDIT-2 (Pass 13): 7 bugs found in the cross-API consistency,
    timer cleanup, and Worker/iFrame scope coverage audit.

    Each test verifies that one of the 7 bugs is fixed in the hardener.

    Coverage:
    - Bug #1: WebGL archString mapping covers Gen 7.5 through Gen 11
    - Bug #2: navigator.connection type+effectiveType consistent
    - Bug #3: battery start time documented as IIFE-anchored
    - Bug #4: connection jitter has pagehide cleanup
    - Bug #5: RTCPeerConnection setTimeout uses WeakRef guard
    - Bug #6: rAF _rafHandles Map has max-size cap
    - Bug #7: iFrame path covers 25+ Navigator props (parity with Worker)
    """

    def setUp(self):
        self.hardener = _read(HARDENER)

    def test_bug1_arch_string_covers_default_renderer(self):
        """Bug #1: archString mapping must not default to 'Gen11' for the
        default persona's 'Intel(R) Iris(TM) Plus Graphics' renderer.
        The new mapping explicitly handles 'iris plus' / 'iris' cases.
        """
        # Old buggy line: 'let archString = "Gen11";' as initial value
        # New fix: 'let archString = "Gen 9.5";' as safe fallback, with
        # explicit Iris Plus mapping
        self.assertIn('"iris plus"', self.hardener,
                      "Missing 'iris plus' archString mapping (Bug #1)")
        # Verify the fallback is 'Gen 9.5' (a modern desktop default) and
        # not the buggy 'Gen11'
        # Match: let archString = "Gen 9.5";  // safe fallback
        import re
        m = re.search(r'let\s+archString\s*=\s*"([^"]+)"\s*;\s*//\s*safe fallback', self.hardener)
        self.assertIsNotNone(m, "archString safe-fallback comment not found")
        self.assertEqual(m.group(1), "Gen 9.5",
                         f"Expected 'Gen 9.5' fallback, got '{m.group(1)}'")

    def test_bug2_connection_type_consistency(self):
        """Bug #2: effectiveType must be undefined when type is non-cellular.
        The FP_CONFIG default block now conditions effectiveType on type.
        """
        # New fix: effectiveType is undefined when connectionType != 'cellular'
        self.assertIn("'cellular'", self.hardener,
                      "Missing 'cellular' check (Bug #2)")
        # Verify the liveJitterState initial value also conditions on type
        self.assertIn(
            "liveJitterState = {",
            self.hardener,
            "liveJitterState block must be present"
        )
        # Check the getter returns undefined for non-cellular
        self.assertIn("return undefined", self.hardener,
                      "effectiveType getter should return undefined for non-cellular")

    def test_bug3_battery_start_time_documented(self):
        """Bug #3: battery start time anchor is IIFE init, not page-load.
        Documented for clarity; not exploitable.
        """
        # The new comment block must be present
        self.assertIn("R7-DEEP-AUDIT-2 (Pass 13) BUG #3", self.hardener,
                      "Bug #3 documentation comment missing")

    def test_bug4_connection_jitter_has_pagehide_cleanup(self):
        """Bug #4: connection jitter setTimeout must have a pagehide
        handler and a _connJitterClosing guard.
        """
        self.assertIn("_connJitterTimer", self.hardener,
                      "Missing _connJitterTimer handle (Bug #4)")
        self.assertIn("_connJitterClosing", self.hardener,
                      "Missing _connJitterClosing flag (Bug #4)")
        # Verify the pagehide handler exists in the connection block
        import re
        # The pagehide block should clear _connJitterTimer
        m = re.search(
            r"_connJitterClosing\s*=\s*true.*?_connJitterTimer.*?clearTimeout",
            self.hardener,
            re.DOTALL
        )
        self.assertIsNotNone(m, "pagehide handler for connection jitter missing (Bug #4)")

    def test_bug5_rtc_setTimeout_uses_weakref(self):
        """Bug #5: RTCPeerConnection setTimeout must use a WeakRef to detect
        if the RTCPeerConnection was GC'd during the delay.
        """
        self.assertIn("selfRef", self.hardener,
                      "Missing selfRef (Bug #5)")
        self.assertIn("new WeakRef(this)", self.hardener,
                      "Missing WeakRef creation (Bug #5)")
        # Verify the .deref() check
        self.assertIn("selfRef.deref()", self.hardener,
                      "Missing .deref() check (Bug #5)")

    def test_bug6_raf_handles_map_has_max_size(self):
        """Bug #6: rAF _rafHandles Map must have a max-size cap to
        prevent unbounded growth in long-running sessions.
        """
        self.assertIn("_rafHandles.size > 1000", self.hardener,
                      "Missing _rafHandles max-size cap (Bug #6)")
        # Verify the cap drops entries
        self.assertIn("_rafHandles.delete(k)", self.hardener,
                      "Missing Map entry deletion (Bug #6)")

    def test_bug7_iframe_navigator_property_coverage(self):
        """Bug #7: iFrame path must cover 25+ Navigator properties,
        matching Worker path coverage. The previous iFrame path only
        covered 5 properties; cross-iframe fingerprints could read
        real navigator values.
        """
        # All 25 properties must be patched in the iFrame path. Most
        # use _iframeNavOverride; serviceWorker and the 6 restricted APIs
        # (usb/serial/hid/bluetooth/ink/nfc) use direct Object.defineProperty
        # because they have conditional logic or fixed values.
        primary_props = [
            'platform', 'hardwareConcurrency', 'deviceMemory', 'language',
            'languages', 'userAgent', 'appVersion', 'vendor', 'vendorSub',
            'product', 'productSub', 'cookieEnabled', 'pdfViewerEnabled',
            'onLine', 'maxTouchPoints', 'doNotTrack'
        ]
        for prop in primary_props:
            self.assertIn(
                f"_iframeNavOverride('{prop}'",
                self.hardener,
                f"iFrame path missing Navigator property: {prop} (Bug #7)"
            )
        # serviceWorker / gpu: each has its own Object.defineProperty
        # (different pattern from _iframeNavOverride)
        for prop in ['serviceWorker', 'gpu']:
            self.assertIn(
                f"targetWindow.Navigator.prototype, '{prop}'",
                self.hardener,
                f"iFrame path missing Navigator property: {prop} (Bug #7)"
            )
        # 6 restricted APIs are in a for loop. Verify the loop exists and
        # contains all 6 names in the array literal.
        self.assertIn(
            "_iframeRestrictedApis",
            self.hardener,
            "iFrame restricted APIs for loop missing (Bug #7)"
        )
        for api in ['usb', 'serial', 'hid', 'bluetooth', 'ink', 'nfc']:
            self.assertIn(
                f"'{api}'",
                self.hardener.split('_iframeRestrictedApis')[1].split('];')[0],
                f"iFrame restricted API '{api}' not in the for loop (Bug #7)"
            )
        # Verify the complex mocks are also present in iFrame path
        for mock in ['geolocation', 'permissions', 'storage', 'clipboard',
                     'userAgentData', 'locks']:
            self.assertIn(
                f"targetWindow.Navigator.prototype, '{mock}'",
                self.hardener,
                f"iFrame path missing complex mock: {mock} (Bug #7)"
            )

    def test_bug7_iframe_date_tostring_not_raw(self):
        """Bug #7 part 2: iFrame Date.toString must NOT call the raw
        toString (which leaks the system TZ into the iframe context).
        """
        # The old buggy code was:
        #   targetWindow.Date.prototype.toString = function() {
        #       try { return rawToStringDate.call(this); } ...
        # The new code constructs a persona-TZ string directly.
        # Verify the rawToStringDate variable is no longer referenced
        # in the iFrame Date.toString block.
        import re
        # Find the iFrame Date.toString block
        m = re.search(
            r"//\s*Date\.toString\s*-.*?\}\s*;\s*\}(\s*\} catch \(e\) \{\}\s*\};)",
            self.hardener,
            re.DOTALL
        )
        if m:
            block = m.group(0)
            # The new block should NOT contain 'rawToStringDate'
            self.assertNotIn(
                "rawToStringDate",
                block,
                "iFrame Date.toString still uses rawToStringDate (Bug #7 part 2)"
            )
            # The new block should use formatToParts to build the persona-TZ string
            self.assertIn(
                "formatToParts",
                block,
                "iFrame Date.toString should use formatToParts to build persona-TZ string"
            )

    def test_bug7_iframe_risk_marker_present(self):
        """Bug #7: the iFrame path should have a marker comment
        explaining why 25+ properties are now patched.
        """
        self.assertIn(
            "R7-DEEP-AUDIT-2 (Pass 13) BUG #7",
            self.hardener,
            "Missing Bug #7 marker comment in iFrame path"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
