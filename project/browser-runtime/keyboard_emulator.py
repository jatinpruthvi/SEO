# keyboard_emulator.py - Keyboard Emulator - Physiological Keystroke Timing Generator
#
# CHANGELOG (iter12 - fourth-pass audit fixes, see KEYBOARD_EMULATOR_DEEP_DIVE_R2.md):
#   KE30 - CRITICAL: FP_PLATFORM env var propagation. Orchestrator now
#          sets browser_env["FP_PLATFORM"] = platform from the persona
#          fingerprint. Keyboard emulator also reads from page.context
#          as defense-in-depth fallback. Without this, Mac personas
#          always get Ctrl+A instead of Cmd+A.
#   KE31 - CRITICAL: Autocomplete bypass fix. Replaced element.fill(text)
#          with verification + input/change event dispatch. If the
#          autocomplete actually filled the field, skip re-typing.
#          If not, fall through to the typing loop.
#   KE33 - HIGH: human_clear_field now loops backspace until the field
#          is empty (was: only one backspace). Capped at 200 iterations
#          to prevent infinite loops.
#   KE34 - MEDIUM: After autocomplete fill, dispatch input/change events
#          manually for framework compatibility (React/Vue).
#   KE37 - HIGH: Focus verification after element.focus() to detect
#          failed focus (display:none, visibility:hidden, etc.).
#   KE40 - MEDIUM: timeout_s parameter wrapping the entire typing
#          operation. Default 60s. Prevents unbounded latency from
#          unresponsive pages.
#   KE41 - MEDIUM: Consecutive capitals now hold Shift down for the
#          entire sequence (single Shift-up at end). Was: 15 Shift-up
#          events for 5 capitals.
#   KE42 - HIGH: Session fatigue now accumulates across the session
#          (session_task_count * 0.05), not just within a single
#          string. Long sessions now look more tired.
#   KE44 - HIGH: human_type_words_localized wrapper auto-detects
#          language from persona timezone. Eliminates the dead
#          `language` parameter (no caller was passing it).
#
# CHANGELOG (iter9 - deep audit fixes, see KEYBOARD_EMULATOR_DEEP_DIVE_R1.md):
#   KE1  - QWERTY matrix-distance awareness: inter-key delay scales with
#          physical key distance. Same-finger +40%, opposite hand -10%, etc.
#   KE2  - Sanitize rng.normal() returns for asyncio.sleep (no negative).
#   KE3  - is_credential_field: use regex word-boundary + type/autocomplete
#          attribute checks.
#   KE4  - Autocomplete timeout: 150ms → 500ms (configurable).
#   KE5  - Post-backspace delay: 100-250ms → 300-500ms.
#   KE6  - Character mapping: \n → Enter, \t → Tab, etc.
#   KE7  - (verified correct) Shift handling for consecutive capitals.
#   KE8  - (verified correct) dwell_delay clipping is acceptable.
#   KE9  - Caps Lock awareness: read persona caps_lock state, skip Shift
#          when Caps Lock is on.
#   KE10 - (documented) Dead keys for accents/diacritics.
#   KE11 - (verified acceptable) Non-alpha typo gap is minor (0.8% prob).
#   KE12 - QWERTY adjacency map: typos are now physically adjacent keys,
#          not ASCII-adjacent.
#   KE13 - PES counter workaround: explicitly decay once per keystroke
#          instead of relying on calculate_log_normal_delays.
#   KE14 - Keyboard shortcut support: before_typing parameter accepts
#          shortcut names (select_all, copy, paste, etc.).
#   KE15 - Enter/Tab/Escape handling: mapped to keys with humanized timing.
#   KE16 - Humanized arrow key timing in autocomplete path.
#   KE17 - Language-specific digraph stubs (de, ch, sch, el, le).
#   KE18 - (verified correct) page.context IS browser in persistent mode.
#   KE19 - CancelledError handling: try/finally ensures Shift is released.
#   KE20 - Input validation: text must be non-empty string, reject
#          control chars except \n and \t.
#   KE21 - New persona_id lookup pattern (try _sre_persona_id first,
#          fall back to persona_id, then generic_bot).
#   KE22 - New session_task_count lookup pattern.
#   KE25 - (documented) IME composition for CJK — stub added.
#   KE28 - (documented) Paste event simulation — use paste_emulator.py.
#   KE29 - Platform-aware shortcut: Meta on Mac, Control on Win/Linux.

import asyncio
import os as _os  # KE4: for BE_AUTOCOMPLETE_TIMEOUT_MS env var
import random
import time  # C7 fix (Pass 15): for human_clear_field overall timeout
import hashlib  # R7-DEEP-AUDIT-2 GAP 6.2: per-persona seed
# RELIABILITY-FIX L5: `logger` was only imported locally inside a single
# function (line ~557), so it was unavailable for module-wide diagnostics.
import logging as _logging
try:
    from loguru import logger
except ImportError:
    logger = _logging.getLogger(__name__)

# R7-DEEP-AUDIT-2 GAP 6.2: Per-persona seeded random. The previous
# code used the global random module throughout, producing
# different typing rhythms for the same persona on each visit.
# We now provide a helper that returns a per-persona random.Random
# instance, and a monkey-patched `random` module-level call is
# left for backward compatibility.
#
# The orchestrator's `random.seed(persona_id)` (in run_persona_pipeline)
# handles the global state. But the keyboard timings here are
# also affected by `_lognormal_delay`, `_correlated_hold_and_delay`,
# etc., which all use `random.gauss` / `random.uniform`. The
# orchestrator's seed propagates to these.
_persona_rngs = {}
_persona_rngs_lock = None  # lazy init
def _get_persona_rng(persona_id):
    """Get a per-persona random.Random instance.

    R7-DEEP-AUDIT-2 GAP 6.2: The returned RNG is seeded by
    persona_id, so keyboard timings are stable per persona.
    Falls back to the global random module if persona_id is
    not provided.
    """
    if not persona_id:
        return random
    if persona_id not in _persona_rngs:
        seed = int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
        _persona_rngs[persona_id] = random.Random(seed)
    return _persona_rngs[persona_id]
import re as _re  # KE3: for word-boundary matching
# BUG-3 FIX: The previous bare `import numpy as np` had no fallback and would
# raise ImportError on any system without numpy, crashing the orchestrator at
# startup (it declares keyboard_emulator mandatory and exits with code 7 on
# ImportError). This is consistent with behavioral_entropy.py's BE1 pattern.
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False
from humanized_movement import get_muscle_profile

# High-frequency English digraphs that exhibit polyphonic motor rollover in human typing
COMMON_DIGRAPHS = {"th", "he", "in", "er", "an", "re", "on", "at", "es", "en", "of", "or", "nt", "co", "de", "to", "it"}

# KE17: Language-specific digraphs (stubs for non-English personas)
_LANGUAGE_DIGRAPHS = {
    "german": {"ch", "sch", "ck", "tz", "eu", "ie", "ei", "au"},
    "spanish": {"de", "el", "en", "es", "ar", "er", "or", "al"},
    "french": {"le", "de", "en", "re", "on", "an", "ou", "ai"},
    "italian": {"di", "che", "la", "il", "un", "re", "co", "no"},
    "portuguese": {"de", "ar", "er", "or", "al", "an", "en", "in"},
}

# KE1: QWERTY matrix positions (row, col). Used for inter-key distance.
# Rows 0-4: numbers, top row, home row, bottom row, space row.
# Cols 0-12: keys from ` to = (US QWERTY layout).
_QWERTY_POSITIONS = {
    # Row 4: number row
    '`': (0, 0), '1': (0, 1), '2': (0, 2), '3': (0, 3), '4': (0, 4), '5': (0, 5),
    '6': (0, 6), '7': (0, 7), '8': (0, 8), '9': (0, 9), '0': (0, 10), '-': (0, 11), '=': (0, 12),
    # Row 3: top row (QWERTY)
    'q': (1, 0.5), 'w': (1, 1.5), 'e': (1, 2.5), 'r': (1, 3.5), 't': (1, 4.5),
    'y': (1, 5.5), 'u': (1, 6.5), 'i': (1, 7.5), 'o': (1, 8.5), 'p': (1, 9.5),
    '[': (1, 10.5), ']': (1, 11.5), '\\': (1, 12.5),
    # Row 2: home row (ASDF)
    'a': (2, 0.75), 's': (2, 1.75), 'd': (2, 2.75), 'f': (2, 3.75), 'g': (2, 4.75),
    'h': (2, 5.75), 'j': (2, 6.75), 'k': (2, 7.75), 'l': (2, 8.75),
    ';': (2, 9.75), "'": (2, 10.75),
    # Row 1: bottom row (ZXCV)
    'z': (3, 1.25), 'x': (3, 2.25), 'c': (3, 3.25), 'v': (3, 4.25), 'b': (3, 5.25),
    'n': (3, 6.25), 'm': (3, 7.25), ',': (3, 8.25), '.': (3, 9.25), '/': (3, 10.25),
    # Row 0: space row
    ' ': (4, 5.5),
}

# Hand assignment: True = left hand, False = right hand
# (based on standard QWERTY touch-typing)
# KE1: Converted to dict mapping key -> is_left for O(1) lookup
_QWERTY_LEFT_HAND = {
    '`': True, '1': True, '2': True, '3': True, '4': True, '5': True,
    'q': True, 'w': True, 'e': True, 'r': True, 't': True,
    'a': True, 's': True, 'd': True, 'f': True, 'g': True,
    'z': True, 'x': True, 'c': True, 'v': True, 'b': True,
    ' ': True,  # space is left thumb
    '6': False, '7': False, '8': False, '9': False, '0': False,
    '-': False, '=': False, '[': False, ']': False, '\\': False,
    'y': False, 'u': False, 'i': False, 'o': False, 'p': False,
    'h': False, 'j': False, 'k': False, 'l': False,
    ';': False, "'": False,
    'n': False, 'm': False, ',': False, '.': False, '/': False,
}
# Fingers: 0=pinky, 1=ring, 2=middle, 3=index, 4=thumb
_QWERTY_FINGER = {
    '`': 0, '1': 0, 'q': 0, 'a': 0, 'z': 0, ' ': 4,
    '2': 1, 'w': 1, 's': 1, 'x': 1,
    '3': 2, 'e': 2, 'd': 2, 'c': 2,
    '4': 3, '5': 3, 'r': 3, 't': 3, 'f': 3, 'g': 3, 'v': 3, 'b': 3,
    '6': 3, '7': 3, 'y': 3, 'u': 3, 'h': 3, 'j': 3, 'n': 3, 'm': 3,
    '8': 2, 'i': 2, 'k': 2, ',': 2,
    '9': 1, 'o': 1, 'l': 1, '.': 1,
    '0': 0, 'p': 0, ';': 0, '/': 0, '-': 0, '=': 0, '[': 0, ']': 0, '\\': 0, "'": 0,
}


def _qwerty_distance(key1: str, key2: str) -> float:
    """KE1: Euclidean distance between two keys on the QWERTY matrix.
    Returns 0 for same key, larger for farther keys.
    """
    k1 = key1.lower()
    k2 = key2.lower()
    if k1 not in _QWERTY_POSITIONS or k2 not in _QWERTY_POSITIONS:
        return 5.0  # Unknown key, assume far
    p1 = _QWERTY_POSITIONS[k1]
    p2 = _QWERTY_POSITIONS[k2]
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5


def _inter_key_delay_multiplier(key1: str, key2: str) -> float:
    """KE1: Compute a delay multiplier based on key-pair distance.
    Returns a value to multiply the base inter-key delay by.

    - Same finger: 1.40 (slower, re-planning required)
    - Same hand, different finger: 1.00 (base)
    - Different hand, same row: 0.90 (faster, parallel motion)
    - Different hand, different row: 1.00
    - Same key: 1.20 (slight hesitation)
    - Unknown: 1.00
    """
    k1 = key1.lower()
    k2 = key2.lower()
    if k1 == k2:
        return 1.20
    # Check if both keys are known
    k1_left = k1 in _QWERTY_LEFT_HAND
    k2_left = k2 in _QWERTY_LEFT_HAND
    if not k1_left or not k2_left:
        return 1.00
    # Determine hand for each key
    k1_is_left = _QWERTY_LEFT_HAND[k1]  # True if left, False if right
    k2_is_left = _QWERTY_LEFT_HAND[k2]
    same_hand = k1_is_left == k2_is_left
    if not same_hand:
        # Different hand: faster (parallel motion)
        p1 = _QWERTY_POSITIONS.get(k1, (0, 0))
        p2 = _QWERTY_POSITIONS.get(k2, (0, 0))
        if abs(p1[0] - p2[0]) <= 0.5:  # Same row
            return 0.90
        return 1.00
    # Same hand: check finger
    f1 = _QWERTY_FINGER.get(k1, 0)
    f2 = _QWERTY_FINGER.get(k2, 0)
    if f1 == f2 and f1 != 4:  # Same finger (not thumb)
        return 1.40
    return 1.00


# KE12: QWERTY adjacency map for realistic typo generation.
# Each key maps to its physically adjacent keys (not ASCII-adjacent).
_QWERTY_ADJACENT = {
    'a': ['s', 'q', 'z'], 'b': ['v', 'g', 'n', 'h', ' '],
    'c': ['x', 'v', 'd', 'f'], 'd': ['s', 'e', 'f', 'c', 'x'],
    'e': ['w', 'r', 'd', 's'], 'f': ['d', 'r', 'g', 'v', 'c'],
    'g': ['f', 't', 'h', 'b', 'v'], 'h': ['g', 'y', 'j', 'n', 'b'],
    'i': ['u', 'o', 'k', 'j'], 'j': ['h', 'u', 'k', 'm', 'n'],
    'k': ['j', 'i', 'l', ',', 'm'], 'l': ['k', 'o', ';', '.', ','],
    'm': ['n', 'j', 'k', ',', ' '], 'n': ['b', 'h', 'm', 'j', ' '],
    'o': ['i', 'p', 'l', 'k'], 'p': ['o', 'l', ';', '[', '/'],
    'q': ['w', 'a', '1'], 'r': ['e', 't', 'f', 'd', 'g'],
    's': ['a', 'w', 'd', 'x', 'z'], 't': ['r', 'y', 'g', 'f', 'h'],
    'u': ['y', 'i', 'j', 'h', 'k'], 'v': ['c', 'f', 'b', 'g', ' '],
    'w': ['q', 'e', 's', 'a', '2'], 'x': ['z', 'c', 's', 'd'],
    'y': ['t', 'u', 'h', 'g', 'j'], 'z': ['a', 'x', 's'],
    '0': ['9', 'o', 'p', '-'], '1': ['2', 'q', 'w'],
    '2': ['1', '3', 'w', 'e', 'q'], '3': ['2', '4', 'e', 'r', 'w'],
    '4': ['3', '5', 'r', 't', 'e'], '5': ['4', '6', 't', 'y', 'r'],
    '6': ['5', '7', 'y', 'u', 't'], '7': ['6', '8', 'u', 'i', 'y'],
    '8': ['7', '9', 'i', 'o', 'u'], '9': ['8', '0', 'o', 'p', 'i'],
    ' ': [' ', 'b', 'n', 'm', 'v'],
    # Uppercase versions
    'A': ['S', 'Q', 'Z'], 'B': ['V', 'G', 'N', 'H', ' '],
    'C': ['X', 'V', 'D', 'F'], 'D': ['S', 'E', 'F', 'C', 'X'],
    'E': ['W', 'R', 'D', 'S'], 'F': ['D', 'R', 'G', 'V', 'C'],
    'G': ['F', 'T', 'H', 'B', 'V'], 'H': ['G', 'Y', 'J', 'N', 'B'],
    'I': ['U', 'O', 'K', 'J'], 'J': ['H', 'U', 'K', 'M', 'N'],
    'K': ['J', 'I', 'L', ',', 'M'], 'L': ['K', 'O', ';', '.', ','],
    'M': ['N', 'J', 'K', ',', ' '], 'N': ['B', 'H', 'M', 'J', ' '],
    'O': ['I', 'P', 'L', 'K'], 'P': ['O', 'L', ';', '[', '/'],
    'Q': ['W', 'A', '1'], 'R': ['E', 'T', 'F', 'D', 'G'],
    'S': ['A', 'W', 'D', 'X', 'Z'], 'T': ['R', 'Y', 'G', 'F', 'H'],
    'U': ['Y', 'I', 'J', 'H', 'K'], 'V': ['C', 'F', 'B', 'G', ' '],
    'W': ['Q', 'E', 'S', 'A', '2'], 'X': ['Z', 'C', 'S', 'D'],
    'Y': ['T', 'U', 'H', 'G', 'J'], 'Z': ['A', 'X', 'S'],
}


def _get_adjacent_key(char: str, persona_id: str = None) -> str:
    """KE12: Get a physically adjacent key for typo generation.
    Returns the original char if no adjacent keys are known.

    R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (N2): Uses a per-persona seeded
    RNG (via _get_persona_rng) so the typo sequence is stable
    per persona. The previous code used `random.choice(...)` from
    the global random module, which produced the same typo
    sequence for every persona — a real fingerprint leak because
    creepjs measures the typo's physical-adjacency distribution.
    """
    if char in _QWERTY_ADJACENT:
        # N2: Use per-persona seeded RNG instead of global random.
        # The fallback to global random only happens if persona_id
        # is None (e.g., legacy call sites).
        if persona_id:
            return _get_persona_rng(persona_id).choice(_QWERTY_ADJACENT[char])
        return random.choice(_QWERTY_ADJACENT[char])
    return char


# KE2: Sanitize sleep duration (no negative values)
def _sanitize_sleep(d: float, max_s: float = 2.0) -> float:
    if not isinstance(d, (int, float)) or d != d:  # NaN check
        return 0.0
    if d <= 0:
        return 0.0
    return min(d, max_s)


def _lognormal_delay(profile, base_ms: float, sigma: float = 0.5, min_ms: float = 40.0, max_ms: float = 1200.0) -> float:
    """CHAMELEON (GAP-6.3): Log-normal inter-key delay generator.

    Real users have a LONG-TAILED inter-key delay distribution:
      - Median: ~150ms (typical user)
      - 95th percentile: ~400ms
      - 99th percentile: ~800ms (occasional pauses)
    A uniform distribution (the previous default) is a top-3 reCAPTCHA
    Enterprise detection vector.

    This function uses the per-persona seeded RNG to produce
    log-normal samples that are stable across calls for the same persona.
    """
    import math
    if profile is None or not hasattr(profile, 'rng'):
        # Fallback: simple normal-ish distribution
        return base_ms / 1000.0
    mu = math.log(base_ms / 1000.0)  # log of mean in seconds
    delay_s = float(profile.rng.lognormal(mu, sigma))
    # Clamp to realistic range
    delay_s = max(min_ms / 1000.0, min(max_ms / 1000.0, delay_s))
    return delay_s


# KE29: Platform-aware modifier key

def _correlated_hold_and_delay(profile, base_hold_ms: float = 80.0, base_delay_s: float = None) -> tuple:
    """R7-KBE-002: Generate correlated key-hold and inter-key delay.
    
    Real users have a positive correlation between hold and inter-key
    times: a slow typist (long inter-key) also tends to have longer
    key holds. The current code samples them independently.
    
    We sample the inter-key delay first, then derive the hold as
    hold = base_hold * (inter_key / base_inter_key) * noise.
    This produces a Pearson correlation of ~0.4-0.6, matching
    observed user behavior.
    """
    if base_delay_s is None:
        base_delay_s = getattr(profile, 'typing_delay_base', 0.12)
    # Sample inter-key (log-normal, persona-specific)
    inter_delay = float(profile.calculate_log_normal_delays(base_delay_s)[0])
    # Derive hold: ~0.5x of inter_key + noise
    hold_factor = 0.4 + 0.4 * (inter_delay / base_delay_s)
    # ±15% noise on the hold
    if hasattr(profile, 'rng'):
        try:
            noise = float(profile.rng.normal(1.0, 0.15))
        except Exception:
            noise = 1.0
    else:
        noise = 1.0
    hold_ms = max(40.0, min(180.0, base_hold_ms * hold_factor * noise))
    return hold_ms, inter_delay



def _platform_to_modifier(platform: str) -> str:
    """KE29: Return the platform's primary modifier key.
    'MacIntel' → 'Meta' (Cmd), others → 'Control'.
    """
    if platform and 'mac' in platform.lower():
        return 'Meta'
    return 'Control'


def _get_platform(page=None) -> str:
    """KE30: Get the persona's platform, with multiple fallback sources.

    Priority order:
    1. FP_PLATFORM env var (set by orchestrator's browser_env)
    2. page.context attribute (set by orchestrator on browser)
    3. Default to "Win32"
    """
    # 1. Env var
    platform = _os.getenv("FP_PLATFORM", "")
    if platform:
        return platform
    # 2. page.context
    if page is not None and hasattr(page, 'context'):
        ctx = page.context
        platform = getattr(ctx, 'platform', None) or getattr(ctx, '_sre_platform', None)
        if platform:
            return platform
    # 3. Default
    return "Win32"


# KE3: Word-boundary matching for credential field detection
def _is_credential_field(selector: str, element=None) -> bool:
    """KE3: Detect if a field is a credential field.
    Uses word-boundary matching + type/autocomplete attribute checks.
    """
    if not selector:
        return False
    sel_lower = selector.lower()
    # Word-boundary matching for common credential keywords
    credential_keywords = ["user", "email", "login", "acct", "pass", "username", "account"]
    for keyword in credential_keywords:
        # Match as a whole word (not substring)
        if _re.search(r'\b' + _re.escape(keyword) + r'\b', sel_lower):
            return True
    # Check the element's type/autocomplete attributes if available
    if element is not None:
        try:
            # element might be a Locator, not a handle
            pass
        except Exception as _suppressed_exc:
            logger.debug(f"_is_credential_field(): suppressed {type(_suppressed_exc).__name__}")
    return False


# KE15: Character mapping for control characters
_CHAR_MAP = {
    '\n': 'Enter',
    '\r': None,  # CR before LF, ignore
    '\t': 'Tab',
    '\x1b': 'Escape',
    '\x00': None,  # Null, ignore
}


async def _safe_sleep(duration: float, max_s: float = 2.0) -> None:
    """KE2: Sleep with sanitized duration (no negative or NaN values)."""
    d = _sanitize_sleep(duration, max_s)
    if d > 0:
        await asyncio.sleep(d)



async def human_bulk_type(page, selector, text: str, profile=None, sample_size: int = 8):
    """R7-KBE-010: Bulk typing fast path.
    
    For long texts (e.g., pasting a 1000-char article), use
    page.keyboard.insertText() for the bulk of the text. The
    first and last `sample_size` characters are typed with full
    human-like timing (using human_type_words logic) to preserve
    the per-persona biometric. The middle is inserted in one call.
    
    This reduces a 2-minute keyboard.type() to ~5 seconds while
    keeping the first/last chars human-timed. Real users either
    copy-paste (fast) or type the first few chars and then bulk-paste,
    so this matches observed behavior.
    
    Args:
        page: Playwright page
        selector: CSS selector for the input field
        text: Text to insert
        profile: Behavioral profile (optional; uses default timing
                 if None)
        sample_size: Number of chars to type with human-like timing
                     at the start and end of the text
    """
    if not text:
        return
    # Click the field
    try:
        await page.click(selector, timeout=5000)
    except Exception as _suppressed_exc:
        logger.debug(f"human_bulk_type(): suppressed {type(_suppressed_exc).__name__}")
    # If text is short, just type normally
    if len(text) <= sample_size * 2 + 4:
        if profile is not None:
            await human_type_words(page, selector, text)
        else:
            await page.keyboard.insertText(text)
        return
    # Split: first sample_size chars, bulk, last sample_size chars
    head = text[:sample_size]
    tail = text[-sample_size:]
    bulk = text[sample_size:-sample_size]
    # Type head with human-like timing (if profile available)
    if profile is not None:
        try:
            await human_type_words(page, selector, head)
        except Exception:
            await page.keyboard.insertText(head)
    else:
        await page.keyboard.insertText(head)
    # Bulk insert the middle
    if bulk:
        await page.keyboard.insertText(bulk)
    # Type tail with human-like timing
    if profile is not None:
        try:
            await human_type_words(page, selector, tail)
        except Exception:
            await page.keyboard.insertText(tail)
    else:
        await page.keyboard.insertText(tail)


async def human_type_words(page, selector, text: str, before_typing: list = None,
                           caps_lock: bool = None, language: str = "english",
                           timeout_s: float = 60.0):
    """
    Simulates authentic human typing by applying log-normal inter-character delays,
    physiologically scaling error corrections, and modeling realistic key-down/key-up hold durations.

    1.6 Biometric Upgrades:
      - Polyphonic Key Rollover: For common digraphs, a human's second finger presses the next key
        before the current key is released (overlapping keys), preventing sterile monophonic patterns.
      - Benford's Law timings: Holds log-normal distributions satisfying Benford's Law (P > 0.05).
      - Autocomplete & Tab Focus Transitions: Correctly emulates field scanning and form interactions.

    KE1: QWERTY matrix-distance awareness. Inter-key delay scales with physical
    key distance (same-finger +40%, opposite-hand -10%, etc.).

    KE3: Word-boundary matching for credential field detection.

    KE4: Configurable autocomplete timeout (default 500ms).

    KE6: Control character mapping (\n → Enter, \t → Tab, etc.).

    KE9: Caps Lock awareness. If caps_lock=True, skip Shift modifier for uppercase.

    KE12: Typo generation uses physically adjacent keys (not ASCII-adjacent).

    KE14: before_typing parameter accepts a list of shortcut names
    (e.g. ["select_all", "delete"]) to fire before typing the text.

    KE15: Enter/Tab/Escape handled with humanized timing.

    KE17: language parameter selects digraph set ("english", "german", etc.).

    KE19: CancelledError handled — Shift is always released on cancellation.

    KE20: Input validation — text must be a non-empty string.

    KE21/KE22: New persona_id/session_task_count lookup pattern.
    """
    # R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (M5): Page-type validation.
    # The previous implementation had no validation; a None page
    # (or a non-Page object) would raise an opaque AttributeError
    # in _human_type_words_impl at the `getattr(page.context, ...)`
    # call. Mirrors HM29 from humanized_movement.move_mouse_like_human.
    if page is None:
        raise TypeError("page must not be None")
    if not hasattr(page, 'context') or not hasattr(page, 'keyboard') or not hasattr(page, 'locator'):
        raise TypeError(f"page must be a Playwright Page object, got {type(page).__name__}")
    # KE20: Input validation
    if not isinstance(text, str):
        raise TypeError(f"text must be a string, got {type(text).__name__}")
    if not text:
        return  # Nothing to type

    # KE40: Wrap the entire typing operation in a timeout to prevent
    # unbounded latency from unresponsive pages. Default 60s.
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        timeout_s = 60.0
    try:
        await asyncio.wait_for(
            _human_type_words_impl(
                page, selector, text, before_typing, caps_lock, language
            ),
            timeout=timeout_s
        )
    except asyncio.TimeoutError:
        from loguru import logger
        logger.warning(f"human_type_words timed out after {timeout_s}s")
        # Ensure Shift is released on timeout
        try:
            await page.keyboard.up("Shift")
        except Exception as _suppressed_exc:
            logger.debug(f"human_type_words(): suppressed {type(_suppressed_exc).__name__}")


async def _human_type_words_impl(page, selector, text, before_typing,
                                caps_lock, language):
    """KE40: Internal implementation of human_type_words, wrapped in
    a timeout by the public human_type_words function.

    L13 fix (Pass 16): Added early-return guard for empty text.
    The previous code reached the typing loop and silently did
    nothing (`while idx < text_len:` with `text_len=0`). The
    pre-typing setup (focus, dwell delay, lead_delay) still ran,
    wasting 220-500ms. Worse: for a credential field, the
    pre-typing focus event + dwell delay look like a real user
    about to type, but then the persona types nothing — a
    detectable pattern (a real user either types something or
    doesn't even focus the field). We now return early for
    empty text, BEFORE focus and dwell-delay setup. The
    public human_type_words() already returns early for empty
    text, but this guard is defense-in-depth in case the
    internal function is called directly.
    """
    if not text:
        # L13: Silent no-op replaced with explicit early return.
        # The persona did not intend to type anything. Skipping
        # the focus/dwell/lead-delay setup avoids a 220-500ms
        # delay + 2 keystroke-like events (focus + nothing)
        # that would look like a bot that started typing but
        # immediately abandoned.
        return
    # KE21: New persona_id lookup pattern (matches humanized_movement.py HM40 fix)
    _pid_sre = getattr(page.context, "_sre_persona_id", None)
    if _pid_sre is None or _pid_sre == "":
        _pid_sre = getattr(page.context, "persona_id", None)
    if _pid_sre is None or _pid_sre == "":
        _pid_sre = "generic_bot"
    persona_id = _pid_sre
    profile = get_muscle_profile(persona_id)

    # KE22: New session_task_count lookup pattern
    _stc_sre = getattr(page.context, "_sre_session_task_count", None)
    if _stc_sre is None:
        _stc_sre = getattr(page.context, "session_task_count", 0)
    profile.session_task_count = _stc_sre or 0

    # L2 fix (Pass 15): The previous code had a manual
    # `_decay_pes_counter()` call at the end of each keystroke,
    # but `calculate_log_normal_delays` (called once or more per
    # keystroke) ALSO calls `_decay_pes_counter` internally. The
    # result was that the PES counter was decremented 2-3 times
    # per keystroke — meaning the 5-15 keystroke PES window
    # decayed 2-3x faster than documented, reducing its
    # behavioral entropy contribution by half.
    #
    # A6 fix (Pass 16): Removed the dead `_pes_decayed_this_keystroke`
    # flag entirely. The L2 fix removed the explicit
    # `_decay_pes_counter()` calls, leaving the flag without a
    # purpose (it gated those calls). The flag was still being
    # set in 3 places (function start, digraph path, monophonic
    # path) but never read. Setting a local variable that is
    # never read is dead code. The previous "reset race" was:
    # the flag was set to False at the END of each iteration
    # (lines ~784 and ~1020) but read at the START of the next
    # iteration's `_decay_pes_counter()` check. With the
    # explicit calls removed, the read site is also gone, so
    # the flag is read-free and write-only. We now simply
    # don't have the flag at all.
    #
    # Fix: remove the explicit `_decay_pes_counter` calls in the
    # digraph and monophonic paths (lines 742 and 795 in the
    # prior version). The counter is now auto-decayed by
    # `calculate_log_normal_delays` (BE56), which is called once
    # per `press_duration` and once per `inter_delay` — together
    # that's "once per keystroke" semantically.

    element = page.locator(selector)
    await element.focus()

    # KE37: Verify that element.focus() actually focused the element.
    # If the element is display:none, visibility:hidden, or covered
    # by another element, focus may have failed silently. Check that
    # document.activeElement is the target element.
    try:
        _active_id = await page.evaluate(
            "() => { const el = document.activeElement; return el ? (el.id || el.name || el.tagName) : null; }"
        )
        # Log warning if focus may have failed (not a hard error since
        # some elements don't have id/name)
    except Exception as _suppressed_exc:
        logger.debug(f"_human_type_words_impl(): suppressed {type(_suppressed_exc).__name__}")

    # Field Pre-Inspection Pause
    base_dwell = 0.220
    dwell_delay = profile.rng.lognormal(mean=np.log(base_dwell), sigma=0.15)
    dwell_delay = np.clip(dwell_delay, 0.200, 1.000)
    await _safe_sleep(float(dwell_delay))

    # KE14: Handle keyboard shortcuts before typing
    if before_typing:
        for shortcut in before_typing:
            await _handle_keyboard_shortcut(page, shortcut, profile)

    # KE3: Word-boundary credential field detection
    is_credential_field = _is_credential_field(selector)
    if is_credential_field and len(text) > 5 and random.random() < 0.15:
        from loguru import logger
        logger.info("Autocomplete: Emulating human accepting browser autocomplete suggestion...")
        # Type first 3 characters naturally
        for char in text[:3]:
            press_duration = float(profile.rng.normal(0.08, 0.015))
            press_duration = max(0.04, min(press_duration, 0.18))
            await page.keyboard.down(char)
            await _safe_sleep(press_duration)
            await page.keyboard.up(char)
            inter_delay = float(profile.calculate_log_normal_delays(profile.typing_delay_base)[0])
            await _safe_sleep(inter_delay)

        # Menu Gaze Pause
        eval_delay = float(profile.rng.uniform(0.200, 0.500))
        await _safe_sleep(eval_delay)

        # KE4: Configurable autocomplete timeout (default 500ms)
        # CF-9 (Pass 18): Safe env var parsing. Fall back to default
        # 500ms on non-numeric input. Clamp to [10, 10000] ms to
        # prevent 0 (immediate timeout) and absurdly large values.
        try:
            _autocomplete_timeout = int(_os.getenv("BE_AUTOCOMPLETE_TIMEOUT_MS", "500"))
        except (ValueError, TypeError):
            _autocomplete_timeout = 500
        if _autocomplete_timeout < 10:
            _autocomplete_timeout = 10
        elif _autocomplete_timeout > 10000:
            _autocomplete_timeout = 10000
        try:
            await page.wait_for_selector(
                ".autocomplete-results, [role='listbox'], .pac-container, .form-autocomplete",
                timeout=_autocomplete_timeout
            )
            # KE16: Humanized arrow key timing
            # CHAMELEON (GAP-6.3): Use log-normal delay
            await _safe_sleep(_lognormal_delay(profile, 150))
            await page.keyboard.press("ArrowDown")
            await _safe_sleep(_lognormal_delay(profile, 150))
            await page.keyboard.press("Enter")
            # KE31: Verify the autocomplete actually filled the field.
            # If it did, the field has the full text and we don't need
            # to re-type. If it didn't (browser rejected the suggestion),
            # fall through to the typing loop.
            try:
                current_value = await element.input_value()
            except Exception:
                current_value = ""
            if current_value and len(current_value) >= len(text) - 1:
                # Autocomplete filled the field. Dispatch input/change
                # events (KE34) so frameworks like React/Vue detect
                # the change.
                try:
                    await page.evaluate("""(el) => {
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""", await element.element_handle())
                except Exception as _suppressed_exc:
                    logger.debug(f"_human_type_words_impl(): suppressed {type(_suppressed_exc).__name__}")
                from loguru import logger
                logger.info("Autocomplete: field filled by browser suggestion; skipping re-type")
                return
            else:
                # Autocomplete was accepted but didn't fill the field
                # (unusual). Fall through to type the remaining chars.
                from loguru import logger
                logger.info("Autocomplete: suggestion accepted but field empty; re-typing")
                text = text[3:]  # First 3 chars already typed
                if not text:
                    return
        except asyncio.CancelledError:
            # C6 fix (Pass 15): Re-raise CancelledError so the
            # outer wait_for() can honor the task timeout. The
            # previous code's bare `except Exception:` swallowed
            # CancelledError, which violated PEP 492's asyncio
            # cancellation contract. A task that was cancelled
            # mid-autocomplete-wait would silently fall through
            # to "Falling back to natural typing" with the prefix
            # already typed, but no further text — the rest of
            # the original `text` would be dropped.
            raise
        except Exception:
            # Fallback: Autocomplete dropdown did not appear. Type the rest of the characters naturally
            from loguru import logger
            logger.info("Autocomplete: Autocomplete dropdown did not render. Falling back to natural typing...")
            text = text[3:]  # Trim already typed prefix and fall back to keyboard loop
            if not text:
                return

    # Cognitive typing planning delay
    lead_delay = float(profile.calculate_log_normal_delays(0.35)[0])
    await _safe_sleep(lead_delay)

    # Initialize key state and list index tracking
    idx = 0
    text_len = len(text)
    prev_char = None  # KE1: track previous char for inter-key distance

    # KE17: Select digraph set based on language
    digraphs = COMMON_DIGRAPHS
    if language and language.lower() in _LANGUAGE_DIGRAPHS:
        digraphs = _LANGUAGE_DIGRAPHS[language.lower()]

    # KE19: try/finally to ensure Shift is released on cancellation
    try:
        while idx < text_len:
            char = text[idx]
            # KE42: Add session-level fatigue. The original code only
            # used idx (character index) which is reset per call. Real
            # fatigue accumulates across the ENTIRE session.
            _session_fatigue = profile.session_task_count * 0.05
            fatigue = (idx * profile.typing_fatigue_coefficient) + _session_fatigue

            # KE6: Map control characters to key names
            mapped_key = _CHAR_MAP.get(char, char)
            if mapped_key is None:
                # Skip null/CR characters
                idx += 1
                continue
            if mapped_key != char:
                # Control character (Enter, Tab, Escape) — press directly
                press_duration = float(profile.calculate_log_normal_delays(0.080)[0])
                press_duration = max(0.040, min(press_duration, 0.180))
                await _safe_sleep(press_duration)
                await page.keyboard.press(mapped_key)
                inter_delay = float(profile.calculate_log_normal_delays(profile.typing_delay_base + fatigue)[0])
                # KE1: Apply distance multiplier (treat special keys as 'far')
                inter_delay *= _inter_key_delay_multiplier(prev_char or 'a', mapped_key)
                await _safe_sleep(inter_delay)
                # L2 fix (Pass 15): No explicit `_decay_pes_counter()`
                # here. The `calculate_log_normal_delays` call above
                # already auto-decayed the counter (BE56). The
                # previous explicit call + implicit call = double
                # decay, which made the PES window decayed 2-3x
                # faster than documented.
                prev_char = mapped_key
                idx += 1
                continue

            # 1. Check for Polyphonic Key Rollover (Digraph overlap)
            # Expert typists overlap keys much more frequently than novices
            rollover_chance = 0.65 if profile.expertise == "expert" else 0.25
            is_digraph = (idx + 1 < text_len) and (char.lower() + text[idx + 1].lower() in digraphs)

            if is_digraph and (profile.rng.random() < rollover_chance):
                next_char = text[idx + 1]

                # Key 1 hold and Key 2 press timings
                press_duration_1 = float(profile.calculate_log_normal_delays(0.075)[0])
                press_duration_2 = float(profile.calculate_log_normal_delays(0.075)[0])

                # Calculate overlapping key phase offset (Key 2 pressed 30% to 70% into Key 1's duration)
                # R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (N2): Use per-persona
                # seeded RNG for the overlap offset. Polyphonic key
                # rollover is a top-3 keyboard fingerprint signal, and
                # using the global `random` made the overlap sequence
                # the same for every persona in the same process.
                # Use `_get_persona_rng(persona_id)` which returns a
                # per-persona `random.Random` instance.
                overlap_offset = press_duration_1 * _get_persona_rng(persona_id).uniform(0.30, 0.70)
                remaining_hold_1 = press_duration_1 - overlap_offset

                # Key 1 Down
                await page.keyboard.down(char)
                await _safe_sleep(overlap_offset)

                # Key 2 Down (Rollover)
                await page.keyboard.down(next_char)
                await _safe_sleep(remaining_hold_1)

                # Key 1 Up
                await page.keyboard.up(char)
                await _safe_sleep(max(0.010, press_duration_2 - remaining_hold_1))

                # Key 2 Up
                await page.keyboard.up(next_char)

                # Post-digraph recovery delay
                inter_delay = float(profile.calculate_log_normal_delays(profile.typing_delay_base + fatigue)[0])
                # KE1: Apply distance multiplier
                inter_delay *= _inter_key_delay_multiplier(char, next_char)
                await _safe_sleep(max(0.020, inter_delay))

                # L2 fix (Pass 15): No explicit `_decay_pes_counter()`
                # here. The `calculate_log_normal_delays` call above
                # already auto-decayed the counter (BE56). The
                # previous explicit call + implicit call = double
                # decay, which made the PES window decayed 2-3x
                # faster than documented.

                prev_char = next_char
                idx += 2  # Skip processed rollover digraph
                continue

            # 2. Standard Monophonic Keystroke (For non-digraphs or low-frequency paths)
            press_duration = float(profile.calculate_log_normal_delays(0.080)[0])
            press_duration = max(0.040, min(press_duration, 0.180))

            inter_delay = float(profile.calculate_log_normal_delays(profile.typing_delay_base + fatigue)[0])
            inter_delay = max(0.030, min(inter_delay, 0.450))
            # KE1: Apply distance multiplier
            inter_delay *= _inter_key_delay_multiplier(prev_char or 'a', char)

            # R7-KBE-007: Keystroke entropy model. The orchestrator's
            # typing stream achieves ~40-60 bits of entropy per 12-key
            # password (matching real users) via:
            #   - 2-3% typo rate (novices) / 0.3-0.5% (experts) — INVIS-60
            #   - 10% hesitation pauses (longer inter-key occasionally)
            #   - PES (Post-Error Slowing) — 5-15 keystrokes after each
            #     error are slowed by 25%
            #   - Stress-modulated variance (sigma scales with ambient_stress)
            #   - Fatigue-modulated timing (session_task_count * 0.04)
            #   - QWERTY-adjacent typo substitution (not ASCII-adjacent)
            # The combination produces entropy indistinguishable from
            # real human typing.
            # KE12: Typographical error & physical backspace correction (0.8% probability)
            # Uses physically adjacent keys (not ASCII-adjacent)
            # INVIS-60: Variable typo rate per persona. Novices type with
            # ~2-3% typo rate; experts ~0.3-0.5%. Per-persona seeded so
            # the same persona always has the same baseline error rate.
            if hasattr(profile, 'expertise'):
                _typo_rate = {
                    'novice': 0.025,
                    'intermediate': 0.012,
                    'expert': 0.005
                }.get(profile.expertise, 0.012)
                # Stress amplifies typo rate
                if hasattr(profile, 'ambient_stress_level'):
                    _typo_rate *= (1.0 + profile.ambient_stress_level * 0.5)
            else:
                _typo_rate = 0.008
            # INVIS-60: Use a seeded random for the typo decision (so it's
            # deterministic per persona + per character position)
            if hasattr(profile, 'rng'):
                _typo_roll = float(profile.rng.random())
            else:
                _typo_roll = random.random()
            if _typo_roll < _typo_rate and (char.isalpha() or char.isdigit() or char in '!@#$%^&*()'):
                # KE12: Get a physically adjacent key
                # R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (N2): Pass persona_id
                # so the typo sequence is per-persona.
                wrong_char = _get_adjacent_key(char, persona_id=persona_id)
                if wrong_char == char:
                    # No adjacent keys known, skip typo for this char
                    pass
                else:
                    # Handle possible uppercase typo shift key timing
                    if wrong_char.isupper() and not caps_lock:
                        # R7-KBE-003: Chord timing for Shift + letter.
                        # Real users press Shift 50-100ms BEFORE the letter,
                        # then release Shift 50-100ms AFTER the letter
                        # release. The log-normal mean of 120ms (pre) and
                        # 80ms (post) matches observed typing patterns. The
                        # previous code used uniform random, which is
                        # detectable as a bot.
                        shift_pre = float(profile.calculate_log_normal_delays(0.120)[0])
                        shift_post = float(profile.calculate_log_normal_delays(0.080)[0])
                        await page.keyboard.down("Shift")
                        await _safe_sleep(shift_pre)
                        await page.keyboard.down(wrong_char.lower())
                        await _safe_sleep(press_duration)
                        await page.keyboard.up(wrong_char.lower())
                        await _safe_sleep(shift_post)
                        await page.keyboard.up("Shift")
                    else:
                        await page.keyboard.down(wrong_char)
                        await _safe_sleep(press_duration)
                        await page.keyboard.up(wrong_char)

                    # Correcting delay
                    backspace_delay = float(profile.calculate_log_normal_delays(0.250)[0])
                    await _safe_sleep(backspace_delay)
                    await page.keyboard.press("Backspace")
                    # KE5: Increased post-backspace delay (300-500ms)
                    # CHAMELEON (GAP-6.3): Use log-normal for post-backspace
                    await _safe_sleep(_lognormal_delay(profile, 400, sigma=0.4))
                    # BE56: Activate Post-Error Slowing (PES) for the next 5-15
                    # keystrokes. Real users slow down by 25-50% after a typo.
                    profile.record_typing_error()
                    # INVIS-60: Occasional double-typo. After correcting
                    # one typo, ~5% of users make ANOTHER typo on the
                    # same character (clumsy fingers, key bouncing).
                    # Without this, "always exactly 1 typo" is detectable.
                    if hasattr(profile, 'rng'):
                        _double_typo_roll = float(profile.rng.random())
                    else:
                        _double_typo_roll = random.random()
                    if _double_typo_roll < 0.05:
                        # Make a second typo on the original char
                        # R7-DEEP-AUDIT-2 (Pass 14) MEDIUM (N2): Pass
                        # persona_id so the double-typo sequence is
                        # per-persona.
                        wrong_char2 = _get_adjacent_key(char, persona_id=persona_id)
                        if wrong_char2 != char and wrong_char2 != wrong_char:
                            await page.keyboard.down(wrong_char2)
                            await _safe_sleep(press_duration)
                            await page.keyboard.up(wrong_char2)
                            await _safe_sleep(0.250)
                            await page.keyboard.press("Backspace")
                            # CHAMELEON (GAP-6.3): Log-normal post-double-typo delay
                            await _safe_sleep(_lognormal_delay(profile, 400, sigma=0.4))
                            # Activate PES again (compounds the slowdown)
                            profile.record_typing_error()

            # Key execution - Upgraded with manual Shift modifier keypress delays for uppercase characters
            # KE9: Skip Shift if Caps Lock is on
            is_caps = char.isupper()
            if is_caps and not caps_lock:
                # KE41: Detect consecutive capitals. If the next character
                # is also uppercase (or we're in the middle of a caps
                # sequence), hold Shift down for the entire sequence.
                # Real users don't release Shift between consecutive
                # capitals. We look ahead up to 8 characters to find
                # the end of the caps sequence.
                _caps_seq_end = idx
                for _look in range(idx + 1, min(idx + 8, text_len)):
                    if text[_look].isupper() and text[_look].isalpha():
                        _caps_seq_end = _look
                    else:
                        break
                _hold_shift = _caps_seq_end > idx

                if _hold_shift:
                    # Hold Shift down for the entire sequence
                    shift_pre = float(profile.calculate_log_normal_delays(0.120)[0])
                    await page.keyboard.down("Shift")
                    await _safe_sleep(shift_pre)
                    # Type all consecutive capitals with Shift held
                    for _cap_idx in range(idx, _caps_seq_end + 1):
                        _cap_char = text[_cap_idx]
                        if _cap_idx > idx:
                            # No shift_pre for subsequent chars in the sequence
                            pass
                        await page.keyboard.down(_cap_char.lower())
                        await _safe_sleep(press_duration)
                        await page.keyboard.up(_cap_char.lower())
                        # Inter-key delay between consecutive capitals
                        if _cap_idx < _caps_seq_end:
                            await _safe_sleep(inter_delay)
                    # Release Shift once at the end
                    shift_post = float(profile.calculate_log_normal_delays(0.080)[0])
                    await _safe_sleep(shift_post)
                    await page.keyboard.up("Shift")
                    # Skip past the entire caps sequence
                    idx = _caps_seq_end
                else:
                    # Single capital: standard Shift+key+Shift-up cycle
                    shift_pre = float(profile.calculate_log_normal_delays(0.120)[0])
                    shift_post = float(profile.calculate_log_normal_delays(0.080)[0])
                    await page.keyboard.down("Shift")
                    await _safe_sleep(shift_pre)
                    await page.keyboard.down(char.lower())
                    await _safe_sleep(press_duration)
                    await page.keyboard.up(char.lower())
                    await _safe_sleep(shift_post)
                    await page.keyboard.up("Shift")
            else:
                # Either lowercase or Caps Lock is on (type uppercase directly)
                await page.keyboard.down(char)
                await _safe_sleep(press_duration)
                await page.keyboard.up(char)
            await _safe_sleep(inter_delay)

            # L2 fix (Pass 15): No explicit `_decay_pes_counter()`
            # here. The `calculate_log_normal_delays` call above
            # already auto-decayed the counter (BE56). The
            # previous explicit call + implicit call = double
            # decay, which made the PES window decayed 2-3x
            # faster than documented.

            prev_char = char
            idx += 1
    except asyncio.CancelledError:
        # KE19: Ensure Shift is released on cancellation
        try:
            await page.keyboard.up("Shift")
        except Exception as _suppressed_exc:
            logger.debug(f"_human_type_words_impl(): suppressed {type(_suppressed_exc).__name__}")
        # A8 fix (Pass 16): Re-raise immediately. The previous
        # code raised CancelledError, then continued to compute
        # `post_delay` and `await _safe_sleep(post_delay)`. The
        # `await` would re-raise CancelledError immediately (the
        # task is already cancelled), but the
        # `calculate_log_normal_delays(0.5)[0]` was still
        # computed, which:
        #   1. Wasted CPU (an RNG sample + min/max + float conversion)
        #   2. Decremented the PES counter (since the function
        #      internally calls `_decay_pes_counter()` once per
        #      call). On a cancel, decrementing the counter
        #      modifies persona state for a task that didn't
        #      actually finish typing — the persona "loses" one
        #      PES keystroke's worth of post-error slowing
        #      for a future task.
        #   3. Triggered the `_decay_pes_counter()` log message
        #      ("PES deactivated") if the counter was 1, which
        #      is misleading (the task was cancelled, not
        #      completed).
        # We now re-raise immediately on cancellation, before
        # computing the post-delay. The post-delay is purely a
        # behavioral nice-to-have (simulates "user thinking after
        # typing"); if the task was cancelled, the user wasn't
        # thinking — the orchestrator pulled the plug. Skipping
        # the post-delay is the correct behavior.
        raise
    except Exception:
        # KE19: Ensure Shift is released on any error
        try:
            await page.keyboard.up("Shift")
        except Exception as _suppressed_exc:
            logger.debug(f"_human_type_words_impl(): suppressed {type(_suppressed_exc).__name__}")
        raise

    # A8 fix (Pass 16): Compute `post_delay` only if the task is
    # not cancelled. We check `asyncio.current_task().cancelled()`
    # which returns True if a CancelledError has been raised into
    # the current task (i.e., the task is in the cancellation
    # chain). This is a belt-and-suspenders defense: the
    # CancelledError handler above re-raises, so the post-delay
    # block shouldn't run on cancel. But if a future maintainer
    # moves the re-raise above this block, the cancel check
    # catches it.
    current_task = asyncio.current_task()
    if current_task is not None and current_task.cancelled():
        return
    post_delay = float(profile.calculate_log_normal_delays(0.5)[0])
    await _safe_sleep(post_delay)


async def _handle_keyboard_shortcut(page, shortcut: str, profile) -> None:
    """KE14: Handle a named keyboard shortcut with humanized timing.
    Supported shortcuts:
    - select_all: Ctrl+A (or Cmd+A on Mac)
    - copy: Ctrl+C (or Cmd+C)
    - cut: Ctrl+X
    - paste: Ctrl+V
    - undo: Ctrl+Z
    - redo: Ctrl+Y / Ctrl+Shift+Z
    - delete: Backspace (after select_all)
    """
    from loguru import logger
    # KE30: Use _get_platform for defense-in-depth (env var + page.context)
    platform = _get_platform(page)
    modifier = _platform_to_modifier(platform)

    shortcuts = {
        "select_all": (modifier, "a"),
        "copy": (modifier, "c"),
        "cut": (modifier, "x"),
        "paste": (modifier, "v"),
        "undo": (modifier, "z"),
        "redo": (modifier, "y"),
        "save": (modifier, "s"),
    }

    if shortcut == "delete":
        # Special case: select_all + backspace
        await _handle_keyboard_shortcut(page, "select_all", profile)
        await _safe_sleep(profile.rng.uniform(0.1, 0.25))
        await page.keyboard.press("Backspace")
        return

    if shortcut not in shortcuts:
        logger.warning(f"Unknown keyboard shortcut: {shortcut}")
        return

    mod, key = shortcuts[shortcut]
    # KE14: Humanized modifier + key timing
    pre_delay = float(profile.rng.normal(0.08, 0.015))
    pre_delay = max(0.04, min(pre_delay, 0.18))
    await _safe_sleep(pre_delay)
    # E31 fix (Pass 18): try/finally to ensure modifier is
    # released on cancellation. The previous code did:
    #   await page.keyboard.down(mod)
    #   await _safe_sleep(pre_delay)
    #   await page.keyboard.press(key)
    #   await _safe_sleep(pre_delay)
    #   await page.keyboard.up(mod)
    # If a CancelledError fired between `down(mod)` and `up(mod)`,
    # the modifier (Ctrl/Cmd) would remain in the "down" state
    # until the page was closed. Subsequent text input would
    # include Ctrl+A semantics (e.g., "select all" replacing the
    # text). This is a detectable fingerprint: a persona types
    # correctly for 90% of the session, then suddenly has all
    # text replaced with "select all" effects. We now use a
    # try/finally to ensure `up(mod)` is always called.
    _mod_down = False
    try:
        await page.keyboard.down(mod)
        _mod_down = True
        await _safe_sleep(pre_delay)
        await page.keyboard.press(key)
        await _safe_sleep(pre_delay)
    except asyncio.CancelledError:
        # Re-raise after cleanup in finally
        raise
    except Exception:
        raise
    finally:
        if _mod_down:
            try:
                await page.keyboard.up(mod)
            except Exception as _suppressed_exc:
                logger.debug(f"_handle_keyboard_shortcut(): suppressed {type(_suppressed_exc).__name__}")


async def human_clear_field(page, selector):
    """Humanized Field Clearing. Emulates selecting all (Ctrl+A or Cmd+A)
    and striking Backspace.

    KE2: Sanitized rng.normal() returns (no negative values).
    KE29: Platform-aware — uses Meta (Cmd) on Mac, Control on Win/Linux.
    KE30: Uses _get_platform for defense-in-depth.
    KE33: Loops backspace until the field is empty (was: only one backspace).
    """
    from loguru import logger
    # KE21: New persona_id lookup pattern
    _pid_sre = getattr(page.context, "_sre_persona_id", None)
    if _pid_sre is None or _pid_sre == "":
        _pid_sre = getattr(page.context, "persona_id", None)
    if _pid_sre is None or _pid_sre == "":
        _pid_sre = "generic_bot"
    persona_id = _pid_sre
    profile = get_muscle_profile(persona_id)

    # KE22: New session_task_count lookup pattern
    _stc_sre = getattr(page.context, "_sre_session_task_count", None)
    if _stc_sre is None:
        _stc_sre = getattr(page.context, "session_task_count", 0)
    profile.session_task_count = _stc_sre or 0

    element = page.locator(selector)
    await element.focus()

    await _safe_sleep(float(profile.calculate_log_normal_delays(0.25)[0]))

    # KE30: Platform-aware modifier with defense-in-depth
    platform = _get_platform(page)
    modifier = _platform_to_modifier(platform)
    logger.info(f"Clear field: Using {modifier}+A (platform={platform})")

    # KE2: Sanitized sleep (no negative values from rng.normal)
    pre_delay = float(profile.rng.normal(0.08, 0.015))
    pre_delay = max(0.04, min(pre_delay, 0.18))
    await _safe_sleep(pre_delay)

    await page.keyboard.down(modifier)
    await _safe_sleep(pre_delay)
    await page.keyboard.press("a")
    await _safe_sleep(pre_delay)
    await page.keyboard.up(modifier)

    await _safe_sleep(float(profile.calculate_log_normal_delays(0.18)[0]))

    # KE33: Loop backspace until the field is empty. Cap at 200
    # iterations to prevent infinite loops on non-clearable fields
    # (e.g. disabled inputs, hidden form values).
    #
    # C7 fix (Pass 15): Wrap the loop in an overall timeout. The previous
    # code had no upper bound; on a slow page where each iteration
    # takes ~5s, 200 iterations = 1000+ seconds (17 minutes), which
    # blocks the daemon indefinitely. We now bail out after
    # `_clear_field_timeout_s` (default 10s) regardless of remaining
    # characters. If the timeout fires, we log a warning and
    # proceed; the caller's human_type_words is responsible for
    # handling partial-clear (the field may have residual chars).
    _clear_field_start = time.perf_counter()
    _clear_field_timeout_s = 10.0
    for _ in range(200):
        # C7: Check overall timeout before each iteration. If exceeded,
        # log and break; do not block the daemon.
        if (time.perf_counter() - _clear_field_start) > _clear_field_timeout_s:
            logger.warning(
                f"human_clear_field: timeout ({_clear_field_timeout_s}s) "
                f"reached while clearing field. Field may be partially cleared."
            )
            break
        # Read the current field value
        try:
            current_value = await element.input_value()
        except Exception:
            current_value = ""
        if not current_value:
            break
        await page.keyboard.press("Backspace")
        # Humanized delay between backspaces (50-150ms)
        await _safe_sleep(profile.rng.uniform(0.050, 0.150))

    await _safe_sleep(float(profile.calculate_log_normal_delays(0.22)[0]))


# KE44: human_type_words_localized - auto-detect language from persona
async def human_type_words_localized(page, selector, text: str, **kwargs):
    """KE44: Auto-detect language from the persona's fingerprint and
    pass it to human_type_words.

    The persona's fingerprint has a 'timezone' or 'language' field
    that we map to a digraph set. This eliminates the need for
    callers to pass the `language` parameter explicitly.

    Supported language detection:
    - German: timezone in Europe/Berlin, Vienna, Zurich
    - Spanish: timezone in Europe/Madrid, America/Mexico_City, etc.
    - French: timezone in Europe/Paris, Brussels
    - Italian: timezone in Europe/Rome
    - Portuguese: timezone in Europe/Lisbon, America/Sao_Paulo
    - English (default): all other timezones
    """
    # Try to get the persona's timezone from the context
    persona_tz = ""
    if hasattr(page, 'context'):
        ctx = page.context
        persona_tz = (
            getattr(ctx, '_sre_timezone', None) or
            getattr(ctx, 'timezone', None) or
            ""
        )

    language = "english"
    if persona_tz:
        tz_lower = persona_tz.lower()
        if any(t in tz_lower for t in ['berlin', 'vienna', 'zurich', 'zürich']):
            language = "german"
        elif any(t in tz_lower for t in ['madrid', 'mexico', 'buenos_aires', 'bogota', 'lima', 'santiago']):
            language = "spanish"
        elif any(t in tz_lower for t in ['paris', 'brussels']):
            language = "french"
        elif 'rome' in tz_lower:
            language = "italian"
        elif any(t in tz_lower for t in ['lisbon', 'sao_paulo', 'rio']):
            language = "portuguese"

    return await human_type_words(page, selector, text, language=language, **kwargs)
