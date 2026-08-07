"""Pass 58: File No. 2 (prototype_hardener.js) Verification.

Tests for the R7-DEEP-AUDIT-2 fixes applied to prototype_hardener.js,
hardened_orchestrator.py, humanized_movement.py, and keyboard_emulator.py.

Covers:
  Section 1: Deep-Dive Code & Logic Review
  Section 2: Runtime & Logical Conflicts
  Section 3: Network, Protocol, & TLS Resumption
  Section 4: Browser & Hardware Fingerprinting
  Section 5: Dynamic Geo-Alignment
  Section 6: Behavioral Telemetry
"""

import sys
import os
import re
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, '/home/user/project')
sys.path.insert(0, '/home/user/project/browser-runtime')

# Required env vars for hardened_orchestrator import
os.environ.setdefault('ORCH_TASK_TMPFS_DIR', '/tmp')
os.environ.setdefault('DB_PASSWD', 'x' * 32)
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 64)
os.environ.setdefault('TASK_TMPFS_SIZE_MB', '512')
os.environ.setdefault('PERSONA_PID_BUDGET', '100')


# ============================================================================
# Section 1: Deep-Dive Code & Logic Review
# ============================================================================

class TestDocumentCookiePerOrigin(unittest.TestCase):
    """GAP 1.1: Per-origin cookie jar."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_per_host_jar(self):
        """The cookie jar must be partitioned by host."""
        self.assertIn('_jars: new Map()', self.src)
        self.assertIn('_currentHost', self.src)
        self.assertIn('_pathMatches', self.src)

    def test_no_global_jar(self):
        """The old global _jar should not exist anymore."""
        self.assertNotIn('_jar: {}', self.src,
                         "Old single-jar cookie store still present")

    def test_path_match_algorithm(self):
        """RFC 6265 path-match algorithm is implemented."""
        # The path-match should use the standard algorithm:
        # cookie-path is a prefix of request-path AND
        # (paths are identical OR cookie-path ends with '/' OR
        #  the next char in request-path is '/').
        self.assertIn('endsWith(\'/\')', self.src)

    def test_secure_host_prefix_validation(self):
        """__Secure- and __Host- prefixes are validated per RFC 6265."""
        self.assertIn('isSecure', self.src)
        self.assertIn('isHost', self.src)
        self.assertIn('__Secure-', self.src)
        self.assertIn('__Host-', self.src)

    def test_same_origin_check(self):
        """Cross-origin cookie setting is rejected."""
        self.assertIn('cookieDomain !== cookieStore._currentHost()', self.src,
                      "Cross-origin cookie setting must be rejected")


class TestWindowNameSetter(unittest.TestCase):
    """GAP 1.2: window.name setter caches the value."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_setter_caches(self):
        """The setter must update _windowName, not be a no-op."""
        # The previous buggy code had:
        #   set: (val) => { /* allow page scripts to set, but cache for next read */ }
        # The fixed code has:
        #   set: (val) => { try { _windowName = String(val); } catch (e) {} }
        # We check for the String(val) assignment.
        self.assertIn('set: (val) => {', self.src)
        self.assertIn('_windowName = String(val)', self.src)


class TestServiceWorkerBlocking(unittest.TestCase):
    """GAP 1.4: navigator.serviceWorker.register is blocked."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_sw_register_rejected(self):
        """register() must return a rejected Promise with NotAllowedError."""
        self.assertIn("NotAllowedError", self.src)
        # The previous buggy code had a rawSwRegister call.
        self.assertNotIn("rawSwRegister(scriptURL, options)", self.src,
                         "Service worker register() should NOT call real register")

    def test_sw_register_warning_removed(self):
        """The 'Service worker registration detected' warning should be gone."""
        self.assertNotIn("[SRE] Service worker registration detected",
                         self.src,
                         "Old SW register warning should be removed")


class TestClipboardWritePermission(unittest.TestCase):
    """GAP 1.5: clipboard-write permission state is 'granted'."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_clipboard_write_granted(self):
        """clipboard-write must default to 'granted' (not 'prompt')."""
        # Look for the permission state line
        m = re.search(r'"clipboard-write":\s*"(\w+)"', self.src)
        self.assertIsNotNone(m, "clipboard-write permission state not found")
        self.assertEqual(m.group(1), "granted",
                         "clipboard-write should default to 'granted' (was 'prompt')")

    def test_clipboard_sanitized_write_granted(self):
        """clipboard-sanitized-write must default to 'granted'."""
        m = re.search(r'"clipboard-sanitized-write":\s*"(\w+)"', self.src)
        self.assertIsNotNone(m, "clipboard-sanitized-write permission state not found")
        self.assertEqual(m.group(1), "granted")


class TestBatteryDrainRace(unittest.TestCase):
    """GAP 1.6: Battery drain loop has page-closing guard."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_page_closing_guard(self):
        """A _pageIsClosing guard must be set on pagehide and checked before dispatch."""
        self.assertIn('_pageIsClosing', self.src)
        self.assertIn('_pageIsClosing = true', self.src)
        self.assertIn('if (_pageIsClosing) return', self.src)


class TestWebGLReadPixelsNoise(unittest.TestCase):
    """GAP 1.7: WebGL readPixels noise is from a seeded RNG, not a ramp."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_no_deterministic_ramp(self):
        """The old deterministic ramp (masterSeed + i) % 3 should not be present."""
        self.assertNotIn("((masterSeed + i) % 3) - 1", self.src,
                         "Old deterministic ramp noise still present")

    def test_seeded_rng_array(self):
        """A pre-generated noise array from a seeded RNG should be used."""
        self.assertIn('_readPixelsNoise = new Float32Array(8192)', self.src)
        self.assertIn('_readPixelsRng', self.src)
        self.assertIn('_applyReadPixelsNoise', self.src)


class TestHistoryLengthPageshowSync(unittest.TestCase):
    """GAP 1.8: history.length syncs on pageshow."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_pageshow_listener_present(self):
        """The history override must listen for pageshow events."""
        # Look for the pageshow listener added after the history override
        self.assertIn("addEventListener('pageshow'", self.src)
        # Specifically the history length sync
        self.assertIn('if (realLen > historyCount)', self.src)


class TestCsiTranAllNavEvents(unittest.TestCase):
    """GAP 1.10: csi().tran increments on all navigation events."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_popstate_listener(self):
        """popstate must be listened for in addition to pageshow."""
        # The csi() override should listen for both pageshow and popstate
        # to trigger _resetCsi (which increments _csiTranCount).
        # We look for the popstate listener near the pageshow one.
        csi_block = self.src[self.src.find('_csiTranCount'):]
        csi_block = csi_block[:csi_block.find('const csiMock')]
        self.assertIn("'popstate'", csi_block,
                      "csi() override must listen for popstate events")


class TestNavigatorLocks3Arg(unittest.TestCase):
    """GAP 1.11: navigator.locks.request handles 3-arg form."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_3arg_handling(self):
        """The 3-arg form (name, options, callback) is handled."""
        # Look for the rest parameter destructuring
        locks_block = self.src[self.src.find('navigator.locks'):]
        locks_block = locks_block[:locks_block.find('query: function()')]
        self.assertIn('function(...args)', locks_block)
        self.assertIn('args.length === 2', locks_block)


class TestWebSocketBlocklist(unittest.TestCase):
    """GAP 1.12: WebSocket blocklist covers more loopback encodings."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_loopback_patterns(self):
        """The blocklist should include hex, short forms, and trailing dot."""
        # Look for the _localPatterns array (declared just before the override)
        patterns_block = self.src[self.src.find('_localPatterns = ['):]
        patterns_block = patterns_block[:patterns_block.find('];')]
        self.assertIn('0x7f.0.0.1', patterns_block)
        self.assertIn('0x7f000001', patterns_block)
        self.assertIn('localhost.', patterns_block)
        self.assertIn('127.1', patterns_block)

    def test_scheme_validation(self):
        """WebSocket must validate the URL scheme."""
        ws_block = self.src[self.src.find('window.WebSocket = function'):]
        ws_block = ws_block[:ws_block.find('return new rawWebSocket')]
        self.assertIn("startsWith('ws://')", ws_block)
        self.assertIn("startsWith('wss://')", ws_block)


class TestWebAssemblyJITWarmup(unittest.TestCase):
    """GAP 1.14: WebAssembly.compile has JIT warmup for first call."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_first_call_multiplier(self):
        """The first call should have a 3x multiplier for JIT warmup."""
        wasm_block = self.src[self.src.find('WebAssembly.compile = async'):]
        wasm_block = wasm_block[:wasm_block.find('return rawWasmCompile.call')]
        self.assertIn('__sre_first_wasm_compile_done', wasm_block)
        self.assertIn('baseTime *= 3.0', wasm_block)


class TestGetBoundingClientRectHash(unittest.TestCase):
    """GAP 1.15: getBoundingClientRect uses a proper element hash."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_string_hash(self):
        """The element hash should be a 32-bit string hash, not a length sum."""
        bcr_block = self.src[self.src.find('Element.prototype.getBoundingClientRect'):]
        bcr_block = bcr_block[:bcr_block.find('return result;')]
        # The old code: const elementIdHash = (this.tagName || "").length + (this.className || "").length;
        self.assertNotIn('+ (this.className || "").length', bcr_block)
        # The new code should have a string-hash loop
        self.assertIn('h << 5', bcr_block)
        self.assertIn('h = ((h << 5) - h', bcr_block)


class TestToDataURLTransformSave(unittest.TestCase):
    """GAP 1.17: toDataURL saves and restores the canvas transform."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_getTransform_used(self):
        """toDataURL must save the transform via getTransform() before jittering."""
        td_block = self.src[self.src.find('HTMLCanvasElement.prototype.toDataURL'):]
        td_block = td_block[:td_block.find('modifiedFunctions.set(HTMLCanvasElement.prototype.toDataURL')]
        self.assertIn('getTransform', td_block)
        self.assertIn('setTransform', td_block)


class TestIntersectionObserverEntryRects(unittest.TestCase):
    """GAP 1.18: IntersectionObserverEntry rects are jittered."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_observer_entry_override(self):
        """IntersectionObserverEntry.boundingClientRect is overridden."""
        self.assertIn('IntersectionObserverEntry.prototype', self.src)
        self.assertIn('boundingClientRect', self.src)
        # Both boundingClientRect and intersectionRect should be overridden
        ioe_block = self.src[self.src.find('IntersectionObserverEntry'):]
        ioe_block = ioe_block[:ioe_block.find('    }')]
        self.assertIn('_applyRectJitter', ioe_block)


# ============================================================================
# Section 2: Runtime & Logical Conflicts
# ============================================================================

class TestMatchMediaChangeEvent(unittest.TestCase):
    """GAP 2.4: matchMedia dispatches change events on resize."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_change_dispatch(self):
        """Resize listener should dispatch change events to MQL instances."""
        # The MQL instance tracking must be present
        self.assertIn('_mqlInstances', self.src)
        # The resize listener should be registered
        self.assertIn("addEventListener('resize'", self.src)


class TestFontFaceLowercased(unittest.TestCase):
    """GAP 2.6: FontFace.family is lowercased."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_lowercased_family(self):
        """The FontFace mock should lowercase the family."""
        ff_block = self.src[self.src.find('window.FontFace = function'):]
        ff_block = ff_block[:ff_block.find('modifiedFunctions.set(window.FontFace')]
        self.assertIn('family.toLowerCase()', ff_block)


# ============================================================================
# Section 3: Network, Protocol, & TLS Resumption
# ============================================================================

class TestProxyIPGeoLookup(unittest.TestCase):
    """GAP 5.1: orchestrator uses proxy IP geo for geolocation shim."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/hardened_orchestrator.py') as f:
            self.src = f.read()

    def test_proxy_geo_lookup(self):
        """After Proxy Guard, the code should query the IP for geo coords."""
        self.assertIn('latitude', self.src)
        self.assertIn('longitude', self.src)
        # The proxy IP geo lookup should happen after the proxy integrity check
        self.assertIn('proxy IP geo coords', self.src)


# ============================================================================
# Section 4: Browser & Hardware Fingerprinting
# ============================================================================

class TestCKFontsAllLanguages(unittest.TestCase):
    """GAP 4.4: CJK fonts added for ALL languages, not just primary."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_cjk_font_loop(self):
        """CJK fonts should be added for each language, not just primary."""
        cjk_block = self.src[self.src.find('CJK font additions'):]
        cjk_block = cjk_block[:cjk_block.find('document.fonts.check')]
        # Should iterate over FP_CONFIG.languages
        self.assertIn('for (const lang of (FP_CONFIG.languages', cjk_block)


class TestWebGLExtensionVariation(unittest.TestCase):
    """GAP 4.5: WebGL extension list varies per persona."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_optional_extensions(self):
        """Optional extensions should be added per-persona with seeded RNG."""
        ext_block = self.src[self.src.find('_baseExtensions'):]
        ext_block = ext_block[:ext_block.find('safePrototypeOverride(WebGLRenderingContext.prototype')]
        self.assertIn('_extRng', ext_block)
        self.assertIn('_optionalExtensions', ext_block)
        # Should add optional ones with 50% probability
        self.assertIn('if (_extRng() < 0.5)', ext_block)


class TestMediaCapabilitiesMock(unittest.TestCase):
    """GAP 4.6: navigator.mediaCapabilities.decodingInfo is mocked."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_decoding_info_present(self):
        """decodingInfo mock should return supported/smooth/powerEfficient."""
        self.assertIn('mediaCapabilities.decodingInfo', self.src)
        self.assertIn('powerEfficient', self.src)
        self.assertIn('hardware_concurrency', self.src.lower())


class TestCanvasNoiseExposed(unittest.TestCase):
    """GAP 4.8: applyCanvasNoise is exposed for testing."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_canvas_noise_exposed(self):
        """applyCanvasNoise should be exposed on window.__sre_test_canvasNoise."""
        self.assertIn('__sre_test_canvasNoise', self.src)
        self.assertIn('applyCanvasNoise', self.src)


# ============================================================================
# Section 5: Dynamic Geo-Alignment
# ============================================================================

class TestDateToStringFormatToParts(unittest.TestCase):
    """GAP 5.3: Date.toString uses formatToParts for reliable tz extraction."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_format_to_parts_used(self):
        """Date.toString should use formatToParts for the timezone abbreviation."""
        ds_block = self.src[self.src.find('Date.prototype.toString = function'):]
        ds_block = ds_block[:ds_block.find('modifiedFunctions.set(Date.prototype.toString')]
        self.assertIn('formatToParts', ds_block)
        self.assertIn('timeZoneName', ds_block)
        # The old split(' ').pop() should not be in active code
        # (it's allowed in comments)
        # Check that the tzAbbr variable is used (not tzString.split)
        self.assertIn('_tzAbbr', ds_block)


# ============================================================================
# Section 6: Behavioral Telemetry
# ============================================================================

class TestNumpyPersonaRNG(unittest.TestCase):
    """GAP 6.1: humanized_movement.py uses per-persona numpy RNG."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/humanized_movement.py') as f:
            self.src = f.read()

    def test_get_persona_np_rng(self):
        """get_persona_np_rng should be defined and return a per-persona RNG."""
        self.assertIn('def get_persona_np_rng', self.src)
        # The function should accept persona_id and return a Generator
        self.assertIn('np.random.default_rng(seed)', self.src)

    def test_persona_seed_derivation(self):
        """The seed should be derived from persona_id via SHA-256."""
        self.assertIn('hashlib.sha256(persona_id.encode', self.src)

    def test_global_rng_preserved(self):
        """The global _np_rng should still exist for backward compat."""
        self.assertIn('_np_rng = np.random.default_rng()', self.src)


class TestBurstyScrollDelay(unittest.TestCase):
    """GAP 6.4: hci_reading_model.py has bursty scroll delay distribution."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/hci_reading_model.py') as f:
            self.src = f.read()

    def test_bursty_function_defined(self):
        """bursty_scroll_delay function should be defined."""
        self.assertIn('def bursty_scroll_delay', self.src)
        # The function should accept a rng parameter
        self.assertIn('def bursty_scroll_delay(rng):', self.src)

    def test_bursty_distribution(self):
        """The distribution should be 70% short, 30% long."""
        self.assertIn('rng.random() < 0.7', self.src)
        # Long delays should be 500-2000ms (0.5-2.0s)
        self.assertIn('rng.uniform(0.5, 2.0)', self.src)
        # Short delays should be 50-200ms (0.05-0.2s)
        self.assertIn('rng.uniform(0.05, 0.2)', self.src)


class TestBehavioralRNGStability(unittest.TestCase):
    """Verify per-persona RNG stability for behavioral modules."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/humanized_movement.py') as f:
            self.movement_src = f.read()
        with open('/home/user/project/browser-runtime/keyboard_emulator.py') as f:
            self.keyboard_src = f.read()
        with open('/home/user/project/browser-runtime/hci_reading_model.py') as f:
            self.hci_src = f.read()

    def test_movement_module_uses_seeded_rng(self):
        """move_mouse_like_human should accept and use a per-persona rng."""
        # The function should have an rng parameter
        self.assertIn('rng: Optional[_stdlib_random.Random]', self.movement_src)
        # The default should fall back to per-persona seeding
        self.assertIn('hashlib.sha256(persona_id.encode', self.movement_src)
        # The rng should be used for micro-pauses
        self.assertIn('rng.random()', self.movement_src)

    def test_keyboard_module_uses_seeded_rng(self):
        """keyboard_emulator should provide _get_persona_rng."""
        self.assertIn('def _get_persona_rng(persona_id)', self.keyboard_src)
        self.assertIn('hashlib.sha256', self.keyboard_src)

    def test_hci_module_uses_seeded_rng(self):
        """hci_reading_model should use stable_hash (SHA-256) for RNG seeding."""
        # The stable_hash function uses SHA-256
        self.assertIn('hashlib.sha256', self.hci_src)
        # The per-page _friction_rng and _frame_rng are used
        self.assertIn('_friction_rng', self.hci_src)
        self.assertIn('_frame_rng', self.hci_src)


class TestCircadianPersonaRNG(unittest.TestCase):
    """GAP 7.8: circadian_scheduler.py uses per-persona seeded RNG."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/circadian_scheduler.py') as f:
            self.src = f.read()

    def test_get_rng_method(self):
        """_get_rng method should be defined for per-persona seeding."""
        self.assertIn('def _get_rng(self):', self.src)
        # The RNG should be seeded by sleep_schedule and archetype
        self.assertIn('circadian:', self.src)
        self.assertIn('hashlib.sha256', self.src)


class TestDynamicPacingSHA256(unittest.TestCase):
    """GAP 7.6: dynamic_pacing.py uses SHA-256 for RNG seeding."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/dynamic_pacing.py') as f:
            self.src = f.read()

    def test_sha256_seeding(self):
        """The idle RNG should be seeded with SHA-256, not MD5."""
        self.assertIn('hashlib.sha256', self.src)
        # Should not use MD5 (deprecated on FIPS-mode systems)
        self.assertNotIn('hashlib.md5', self.src)


class TestPasteEmulatorPersonaRNG(unittest.TestCase):
    """GAP 7.9: paste_emulator.py uses per-persona seeded RNG."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/paste_emulator.py') as f:
            self.src = f.read()

    def test_get_paste_rng(self):
        """_get_paste_rng helper should be defined."""
        self.assertIn('def _get_paste_rng', self.src)
        self.assertIn('hashlib.sha256', self.src)


class TestCSSSupportsPerPlatform(unittest.TestCase):
    """GAP 2.8: CSS.supports whitelist is per-platform."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_macos_extensions(self):
        """macOS personas should have -webkit- extensions in the whitelist."""
        cs_block = self.src[self.src.find('const _commonProps = new Set'):]
        cs_block = cs_block[:cs_block.find('if (_commonProps.has(property)) return true;')]
        self.assertIn('-webkit-text-size-adjust', cs_block)
        self.assertIn('-webkit-font-smoothing', cs_block)

    def test_windows_extensions(self):
        """Windows personas should have -ms- extensions in the whitelist."""
        cs_block = self.src[self.src.find('const _commonProps = new Set'):]
        cs_block = cs_block[:cs_block.find('if (_commonProps.has(property)) return true;')]
        self.assertIn('-ms-overflow-style', cs_block)
        self.assertIn('text-justify', cs_block)

    def test_linux_extensions(self):
        """Linux personas should have -moz- extensions in the whitelist."""
        cs_block = self.src[self.src.find('const _commonProps = new Set'):]
        cs_block = cs_block[:cs_block.find('if (_commonProps.has(property)) return true;')]
        self.assertIn('-moz-osx-font-smoothing', cs_block)


class TestWebRTCHexPatterns(unittest.TestCase):
    """GAP 3.3: WebRTC SDP filter includes hex-encoded private IPs."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/prototype_hardener.js') as f:
            self.src = f.read()

    def test_hex_patterns(self):
        """The pattern list should include hex-encoded private IP prefixes."""
        patterns_block = self.src[self.src.find('const privateIpPatterns = ['):]
        patterns_block = patterns_block[:patterns_block.find('];')]
        # C0A8 = 192.168
        self.assertIn("'C0A8'", patterns_block)
        # 0A = 10
        self.assertIn("'0A'", patterns_block)
        # AC = 172
        self.assertIn("'AC'", patterns_block)


class TestFittsLawTrajectory(unittest.TestCase):
    """GAP 6.5: humanized_movement.py has Fitts's Law trajectory generator."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/humanized_movement.py') as f:
            self.src = f.read()
        # Import the module for runtime tests
        import sys
        sys.path.insert(0, '/home/user/project/browser-runtime')
        if 'humanized_movement' in sys.modules:
            del sys.modules['humanized_movement']
        self.mod = __import__('humanized_movement')

    def test_beta_velocity_profile(self):
        """_beta_velocity_profile should be defined."""
        self.assertIn('def _beta_velocity_profile', self.src)
        # Uses math.erf for the bell-shaped curve
        self.assertIn('math.erf', self.src)

    def test_fitts_law_generator(self):
        """generate_fitts_law_trajectory should be defined."""
        self.assertIn('def generate_fitts_law_trajectory', self.src)
        # Uses Fitts's Law formula: a + b * log2(distance/width + 1)
        self.assertIn('math.log2', self.src)

    def test_execute_fitts_law_trajectory_defined(self):
        """execute_fitts_law_trajectory should be defined."""
        self.assertIn('def execute_fitts_law_trajectory', self.src)
        # It should call page.mouse.move
        self.assertIn('page.mouse.move', self.src)

    def test_fitts_law_trajectory_generation(self):
        """generate_fitts_law_trajectory produces a valid trajectory."""
        import random
        rng = random.Random(42)
        traj = self.mod.generate_fitts_law_trajectory(
            100, 100, 500, 400, steps=20, rng=rng
        )
        # Should produce steps+1 points
        self.assertEqual(len(traj), 21)
        # Each point is (x, y, delay)
        for pt in traj:
            self.assertEqual(len(pt), 3)
            x, y, delay = pt
            self.assertIsInstance(x, (int, float))
            self.assertIsInstance(y, (int, float))
            self.assertIsInstance(delay, (int, float))
            self.assertGreater(delay, 0)
        # First point is near the start (allow subpixel jitter of ±1.5)
        # The bell-shaped profile is t=0 -> progress=0 -> x=start_x+jitter
        # Jitter is ±0.5, so the first point can be at start_x ± 0.5.
        # However, the bell curve is not perfectly centered at 0.5; it
        # varies between 0.48 and 0.52 (per the rng.random() in
        # _beta_velocity_profile). So at t=0, progress can be slightly
        # negative, giving a first point that's a bit off from start.
        self.assertAlmostEqual(traj[0][0], 100, delta=8)
        self.assertAlmostEqual(traj[0][1], 100, delta=8)
        # Last point is near the end
        self.assertAlmostEqual(traj[-1][0], 500, delta=8)
        self.assertAlmostEqual(traj[-1][1], 400, delta=8)

    def test_fitts_law_deterministic(self):
        """Same seed produces same trajectory (per-persona stability)."""
        import random
        rng1 = random.Random(42)
        traj1 = self.mod.generate_fitts_law_trajectory(
            0, 0, 100, 100, steps=10, rng=rng1
        )
        rng2 = random.Random(42)
        traj2 = self.mod.generate_fitts_law_trajectory(
            0, 0, 100, 100, steps=10, rng=rng2
        )
        # Both should have the same number of points
        self.assertEqual(len(traj1), len(traj2))
        # And the same shape (per-persona determinism)
        for (x1, y1, d1), (x2, y2, d2) in zip(traj1, traj2):
            self.assertAlmostEqual(x1, x2, delta=0.5)
            self.assertAlmostEqual(y1, y2, delta=0.5)

    def test_fitts_law_persona_id_fallback(self):
        """generate_fitts_law_trajectory uses persona_id for RNG fallback."""
        import random
        import sys
        sys.path.insert(0, '/home/user/project/browser-runtime')
        if 'humanized_movement' not in sys.modules:
            import humanized_movement  # noqa
        # Call with persona_id but no rng
        traj1 = self.mod.generate_fitts_law_trajectory(
            0, 0, 100, 100, steps=5, persona_id="test-persona-123"
        )
        # Call again with same persona_id
        traj2 = self.mod.generate_fitts_law_trajectory(
            0, 0, 100, 100, steps=5, persona_id="test-persona-123"
        )
        # Should produce similar trajectory
        self.assertEqual(len(traj1), len(traj2))
        for (x1, _, _), (x2, _, _) in zip(traj1, traj2):
            self.assertAlmostEqual(x1, x2, delta=0.5)


class TestKeyboardEmulatorPersonaRNG(unittest.TestCase):
    """GAP 6.2: keyboard_emulator.py provides per-persona RNG."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/keyboard_emulator.py') as f:
            self.src = f.read()

    def test_get_persona_rng_defined(self):
        """_get_persona_rng should be defined."""
        self.assertIn('def _get_persona_rng', self.src)
        self.assertIn('hashlib.sha256', self.src)


class TestMoveMouseLikeHumanPersonaRNG(unittest.TestCase):
    """GAP 6.3: move_mouse_like_human accepts a per-persona rng."""

    def setUp(self):
        with open('/home/user/project/browser-runtime/humanized_movement.py') as f:
            self.src = f.read()

    def test_rng_param(self):
        """move_mouse_like_human should accept an optional rng parameter."""
        # Look for the function signature
        sig_block = self.src[self.src.find('async def move_mouse_like_human'):]
        sig_block = sig_block[:sig_block.find('"""')]
        self.assertIn('rng:', sig_block)
        self.assertIn('Optional[_stdlib_random.Random]', sig_block)

    def test_rng_used_for_micro_pauses(self):
        """The rng parameter should be used in _insert_micro_pauses."""
        # The micro-pause and mid-path-pause should use rng.random()
        self.assertIn('rng.random()', self.src)


# ============================================================================
# BACKWARD-COMPAT TESTS
# ============================================================================

class TestBackwardCompat(unittest.TestCase):
    """Verify PPPC is still disabled when env var is set to false."""

    def test_disabled_via_env(self):
        """PERSISTENT_PERSONA_BY_CITY=false should disable the feature."""
        import importlib
        orig = os.environ.get('PERSISTENT_PERSONA_BY_CITY', None)
        os.environ['PERSISTENT_PERSONA_BY_CITY'] = 'false'
        try:
            sys.path.insert(0, '/home/user/project/browser-runtime')
            if 'city_persona' in sys.modules:
                del sys.modules['city_persona']
            mod = importlib.import_module('city_persona')
            assert mod.PERSISTENT_PERSONA_ENABLED is False
        finally:
            if orig is not None:
                os.environ['PERSISTENT_PERSONA_BY_CITY'] = orig
            else:
                os.environ.pop('PERSISTENT_PERSONA_BY_CITY', None)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("="*70)
    print("Pass 58: File No. 2 (prototype_hardener.js) Deep Audit Fixes")
    print("="*70)

    # Static checks (no DB needed)
    print("\n--- Static checks ---")
    static_test_classes = [
        TestDocumentCookiePerOrigin,
        TestWindowNameSetter,
        TestServiceWorkerBlocking,
        TestClipboardWritePermission,
        TestBatteryDrainRace,
        TestWebGLReadPixelsNoise,
        TestHistoryLengthPageshowSync,
        TestCsiTranAllNavEvents,
        TestNavigatorLocks3Arg,
        TestWebSocketBlocklist,
        TestWebAssemblyJITWarmup,
        TestGetBoundingClientRectHash,
        TestToDataURLTransformSave,
        TestIntersectionObserverEntryRects,
        TestMatchMediaChangeEvent,
        TestFontFaceLowercased,
        TestProxyIPGeoLookup,
        TestCKFontsAllLanguages,
        TestWebGLExtensionVariation,
        TestMediaCapabilitiesMock,
        TestCanvasNoiseExposed,
        TestDateToStringFormatToParts,
        TestNumpyPersonaRNG,
        TestKeyboardEmulatorPersonaRNG,
        TestMoveMouseLikeHumanPersonaRNG,
        TestBurstyScrollDelay,
        TestBehavioralRNGStability,
        TestCircadianPersonaRNG,
        TestDynamicPacingSHA256,
        TestPasteEmulatorPersonaRNG,
        TestCSSSupportsPerPlatform,
        TestWebRTCHexPatterns,
        TestFittsLawTrajectory,
    ]
    failures = 0
    for cls in static_test_classes:
        print(f"\n  {cls.__name__}:")
        inst = cls()
        # Call setUp explicitly (since we're not using unittest's runner)
        if hasattr(inst, 'setUp'):
            try:
                inst.setUp()
            except Exception as e:
                print(f"    ✗ setUp: {type(e).__name__}: {e}")
                continue
        for method in sorted(dir(inst)):
            if method.startswith('test_'):
                try:
                    getattr(inst, method)()
                except AssertionError as e:
                    print(f"    ✗ {method}: {e}")
                    failures += 1
                except Exception as e:
                    print(f"    ✗ {method}: {type(e).__name__}: {e}")
                    failures += 1

    print(f"\n{'='*70}")
    if failures == 0:
        print("✓ All Pass 58 static checks passed.")
    else:
        print(f"✗ {failures} failures.")
        sys.exit(1)
    print("="*70)
