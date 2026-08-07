"""
audit_round_1_5_completeness.py - Comprehensive verification audit
================================================================

Verifies that ALL accepted items from Rounds 1-5 are actually
implemented in the code.
"""

import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path("/home/user/project")
BROWSER_RUNTIME = PROJECT_ROOT / "browser-runtime"
CONFIG = PROJECT_ROOT / "config"
DATABASE = PROJECT_ROOT / "database"

HARDENER = BROWSER_RUNTIME / "prototype_hardener.js"
ORCHESTRATOR = BROWSER_RUNTIME / "hardened_orchestrator.py"
IPTABLES = CONFIG / "iptables_docker.sh"
SCHEMA = DATABASE / "schema.sql"
ENTRYPOINT = BROWSER_RUNTIME / "entrypoint.sh"


def _read(path):
    with open(path, "r") as f:
        return f.read()


class AuditResult:
    def __init__(self, name, round_num, expected_in, status, evidence=""):
        self.name = name
        self.round = round_num
        self.expected_in = expected_in
        self.status = status
        self.evidence = evidence

    def __str__(self):
        icon = "[OK]" if self.status == "COMPLETE" else ("[~]" if self.status == "PARTIAL" else "[X]")
        return f"  {icon} [R{self.round}] {self.name}: {self.status}" + (f" -- {self.evidence}" if self.evidence else "")


def check_pattern(content, patterns):
    """Check if patterns exist in content."""
    found = []
    missing = []
    for pat in patterns:
        if pat in content or re.search(pat, content):
            found.append(pat)
        else:
            missing.append(pat)
    return (len(found), len(patterns), missing)


# Pre-load all relevant files
hardener = _read(HARDENER)
orchestrator = _read(ORCHESTRATOR)
iptables = _read(IPTABLES) if IPTABLES.exists() else ""
schema = _read(SCHEMA) if SCHEMA.exists() else ""
entrypoint = _read(ENTRYPOINT) if ENTRYPOINT.exists() else ""

results = []


def add_result(name, round_num, expected_in, status, evidence=""):
    results.append(AuditResult(name, round_num, expected_in, status, evidence))


# ROUND 1
print("=" * 70)
print("ROUND 1 AUDIT (Original 5 Items)")
print("=" * 70)

sharedworker_markers = [
    "Failed to construct 'SharedWorker'",
    "window.SharedWorker",
    "R7-DEEP-AUDIT-2 ROUND 1 FIX: SharedWorker scope",
]
found, total, missing = check_pattern(hardener, sharedworker_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("SharedWorker throws TypeError on construction", 1, "hardener", status, f"{found}/{total}")

sw_markers = [
    "navigator.serviceWorker.getRegistrations",
    "caches.delete",
    "R7-DEEP-AUDIT-2 ROUND 1 FIX: ServiceWorker Cache Persistence",
]
found, total, missing = check_pattern(hardener, sw_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("ServiceWorker cache teardown on init", 1, "hardener", status, f"{found}/{total}")

canvas_markers = [
    "R7-DEEP-AUDIT-2 ROUND 1 FIX: Canvas Font Measurement",
    "_osFontCalibration",
    "DirectWrite",
    "CoreText",
    "Fontconfig",
]
found, total, missing = check_pattern(hardener, canvas_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Canvas font OS-specific calibration", 1, "hardener", status, f"{found}/{total}")

mcast_markers = [
    "224.0.0.0/4",
    "239.0.0.0/8",
    "SSDP",
    "Multicast",
    "1900",
]
found, total, missing = check_pattern(iptables, mcast_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Multicast/SSDP iptables blocking", 1, "iptables", status, f"{found}/{total}")

network_markers = [
    "navigator.connection",
    "disable-webrtc",
    "--disable-ipv6",
]
found, total, missing = check_pattern(hardener + orchestrator, network_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Network MAC/Interface (JS-level only)", 1, "hardener+orchestrator", status, f"{found}/{total}")


# ROUND 3
print("\n" + "=" * 70)
print("ROUND 3 AUDIT (9 Items - 7 Accepted, 2 Rejected)")
print("=" * 70)

module_worker_markers = [
    "R7-DEEP-AUDIT-2 ROUND 3 FIX: Module Worker bypass",
    "import(",
    "blobUrl = URL.createObjectURL(blob)",
]
found, total, missing = check_pattern(hardener, module_worker_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Module Worker bypass fix", 3, "hardener", status, f"{found}/{total}")

worker_nav_markers = [
    "R7-DEEP-AUDIT-2 ROUND 3 FIX: WorkerNavigator missing",
    "'geolocation'",
    "'serviceWorker'",
    "'permissions'",
    "'storage'",
    "'clipboard'",
    "'userAgentData'",
    "'locks'",
    "'gpu'",
]
found, total, missing = check_pattern(hardener, worker_nav_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("WorkerNavigator 17+ property coverage", 3, "hardener", status, f"{found}/{total}")

houdini_markers = [
    "CSS.registerProperty",
    "NotSupportedError",
    "CSSStyleValue",
    "paintWorklet",
    "layoutWorklet",
]
found, total, missing = check_pattern(hardener, houdini_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("CSS Houdini / Typed OM blocking", 3, "hardener", status, f"{found}/{total}")

attach_internals_markers = [
    "Element.prototype.attachInternals",
    "attachInternals is not supported",
]
found, total, missing = check_pattern(hardener, attach_internals_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Element.attachInternals blocking", 3, "hardener", status, f"{found}/{total}")

shape_markers = [
    "'BarcodeDetector'",
    "'TextDetector'",
    "'FaceDetector'",
]
found, total, missing = check_pattern(hardener, shape_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Shape Detection APIs (Barcode/Text/Face)", 3, "hardener", status, f"{found}/{total}")

pointer_markers = [
    "'tiltX'", "'tiltY'",
    "'altitudeAngle'", "'azimuthAngle'",
    "'twist'",
    "this.pointerType === 'pen'",
]
found, total, missing = check_pattern(hardener, pointer_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("PointerEvent tilt/altitude patches", 3, "hardener", status, f"{found}/{total}")

webgl2_markers = [
    "WebGL2RenderingContext.prototype.getParameter",
    "WebGL2RenderingContext.prototype.getExtension",
]
found, total, missing = check_pattern(hardener, webgl2_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("OffscreenCanvas WebGL2 context inheritance", 3, "hardener", status, f"{found}/{total}")


# ROUND 4
print("\n" + "=" * 70)
print("ROUND 4 AUDIT (16 Items - All Accepted)")
print("=" * 70)

r4_items = [
    ("ResizeObserver wrapping", ["ResizeObserver", "rawRO", "wrappedCallback"]),
    ("PressureObserver removal", ["'PressureObserver'"]),
    ("EyeDropper blocking", ["'EyeDropper'", "EyeDropper is not supported"]),
    ("EditContext removal", ["'EditContext'"]),
    ("IdleDetector removal", ["'IdleDetector'"]),
    ("WindowControlsOverlay removal", ["'WindowControlsOverlay'"]),
    ("Document Picture-in-Picture removal", ["'documentPictureInPicture'"]),
    ("WebNN removal", ["'NN'"]),
    ("Storage Buckets removal", ["'StorageBucket'", "'storageBuckets'"]),
    ("Attribution Reporting removal", ["'AttributionReporting'"]),
    ("Topics API removal", ["'BrowsingTopics'", "'browsingTopics'"]),
    ("Fenced Frame removal", ["'FencedFrameConfig'"]),
    ("PerformanceServerTiming removal", ["'PerformanceServerTiming'"]),
    ("ReportingObserver removal", ["'ReportingObserver'"]),
    ("WakeLockSensor removal", ["'WakeLockSensor'"]),
]

for name, markers in r4_items:
    found, total, missing = check_pattern(hardener, markers)
    status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
    add_result(name, 4, "hardener", status, f"{found}/{total}")

# Worklets (combined)
worklet_markers = ["audioWorklet", "paintWorklet", "layoutWorklet"]
found, total, missing = check_pattern(hardener, worklet_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("AudioWorklet/PaintWorklet/LayoutWorklet", 4, "hardener", status, f"{found}/{total}")


# ROUND 5
print("\n" + "=" * 70)
print("ROUND 5 AUDIT (11 Items - 10 Accepted, 1 Verification)")
print("=" * 70)

webtransport_markers = [
    "window.WebTransport",
    "WebTransport is not supported in this context",
    "R7-DEEP-AUDIT-2 ROUND 5",
]
found, total, missing = check_pattern(hardener, webtransport_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("WebTransport API (CRITICAL - SOCKS5 bypass)", 5, "hardener", status, f"{found}/{total}")

hw_api_markers = [
    "'USB'", "'Serial'", "'HID'", "'Bluetooth'", "'NFC'",
    "WebUSB is not supported",
    "Web Serial is not supported",
    "Web HID is not supported",
    "Web Bluetooth is not supported",
    "Web NFC is not supported",
]
found, total, missing = check_pattern(hardener, hw_api_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("USB/Serial/HID/Bluetooth/NFC hardware APIs (5 APIs)", 5, "hardener", status, f"{found}/{total}")

fs_api_markers = [
    "'showDirectoryPicker'",
    "'showOpenFilePicker'",
    "'showSaveFilePicker'",
    "'FileSystemFileHandle'",
    "'FileSystemDirectoryHandle'",
]
found, total, missing = check_pattern(hardener, fs_api_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("File System Access API (5 APIs)", 5, "hardener", status, f"{found}/{total}")

webxr_markers = [
    "'navigator.xr'",
    "'XRSystem'",
    "safeDelete(Navigator.prototype, 'xr')",
]
found, total, missing = check_pattern(hardener, webxr_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("WebXR / VR / AR APIs", 5, "hardener", status, f"{found}/{total}")

coi_markers = [
    "'crossOriginIsolated'",
    "globalThis",
]
found, total, missing = check_pattern(hardener, coi_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("crossOriginIsolated locked to false", 5, "hardener", status, f"{found}/{total}")

cookie_store_markers = [
    "'cookieStore'",
    "'CookieStore'",
    "'CookieChangeEvent'",
    "safeDelete(window, 'cookieStore')",
]
found, total, missing = check_pattern(hardener, cookie_store_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Async Cookies API (CookieStore)", 5, "hardener", status, f"{found}/{total}")

share_markers = [
    "'navigator.share'",
    "'navigator.canShare'",
    "Web Share is not supported",
]
found, total, missing = check_pattern(hardener, share_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Web Share API", 5, "hardener", status, f"{found}/{total}")

winname_markers = [
    "window.name",
    "_windowName",
    "INVIS-12",
]
found, total, missing = check_pattern(hardener, winname_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("window.name persistence (verification)", 5, "hardener", status, f"{found}/{total}")


# ORCHESTRATOR-SPECIFIC ITEMS
print("\n" + "=" * 70)
print("ORCHESTRATOR-SPECIFIC ITEMS")
print("=" * 70)

singleton_markers = [
    "R7-DEEP-AUDIT-2 ROUND 1 FIX: Chrome SingletonLock cleanup",
    "SingletonLock", "SingletonLockSymlink", "SingletonCookie",
    "R7-RO1: Removed stale",
]
found, total, missing = check_pattern(orchestrator, singleton_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Chrome SingletonLock cleanup", 1, "orchestrator", status, f"{found}/{total}")

font_markers = [
    "document.fonts.check",
    "PLAUSIBLE_FONTS",
    "ALL_FONTS",
]
found, total, missing = check_pattern(hardener, font_markers)
status = "COMPLETE" if found == total else ("PARTIAL" if found > 0 else "MISSING")
add_result("Font coherence (document.fonts.check)", 2, "hardener", status, f"{found}/{total}")


# ADDITIONAL API COVERAGE CHECK
print("\n" + "=" * 70)
print("ADDITIONAL API COVERAGE CHECK (out-of-scope items)")
print("=" * 70)

# These are commonly seen fingerprinting APIs that should NOT be present
# (they're out of scope for our patches but should be correctly absent or patched)
out_of_scope_checks = {
    "WebGL2 precision format (should be present, no R2 #4 fix)": "R7-PHO-025",
    "Worker bypass (R3 fix)": "R7-DEEP-AUDIT-2 ROUND 3 FIX: Module Worker",
}

for name, marker in out_of_scope_checks.items():
    if marker in hardener:
        # These ARE expected to be present (they're correct implementations
        # that should exist). Mark as VERIFIED.
        add_result(name, "VER", "hardener", "COMPLETE")
    else:
        add_result(name, "VER", "hardener", "MISSING", f"Expected: {marker}")


# REPORT
print("\n" + "=" * 70)
print("AUDIT REPORT")
print("=" * 70)

complete = sum(1 for r in results if r.status == "COMPLETE")
partial = sum(1 for r in results if r.status == "PARTIAL")
missing = sum(1 for r in results if r.status == "MISSING")

print(f"\nTotal items audited: {len(results)}")
print(f"  [OK]  COMPLETE: {complete}")
print(f"  [~]  PARTIAL:  {partial}")
print(f"  [X]  MISSING:  {missing}")

print("\n--- DETAILED RESULTS ---")
for r in results:
    print(r)

# Print items that need attention
if partial > 0 or missing > 0:
    print("\n--- ITEMS REQUIRING ATTENTION ---")
    for r in results:
        if r.status in ("MISSING", "PARTIAL"):
            print(f"  >>> {r}")

# Final summary
print("\n" + "=" * 70)
if complete == len(results) and partial == 0 and missing == 0:
    print("[OK] AUDIT PASSED: All accepted items are fully implemented")
    sys.exit(0)
else:
    print(f"[!] AUDIT INCOMPLETE: {partial} partial + {missing} missing items")
    sys.exit(1)
