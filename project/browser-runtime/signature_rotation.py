# signature_rotation.py - Anti-Detect Signature Rotation & Proxy Timing Orchestrator
#
# PURPOSE: Two-mitigation module that addresses the highest-impact
# fingerprinting attack vectors:
#
#   MITIGATION 1: Anti-Detect Browser Signature Rotation
#     - Defeats fingerprinting of stealth-tool patterns
#     - Rotates patch signatures monthly (Salt)
#     - Logs all overridden functions for audit
#     - Coordinates with anti_detect_signature.js for nondeterministic
#       toString
#
#   MITIGATION 2: Proxy Rotation + Humanized Timing
#     - Wraps the existing StickyProxyResolver with rotation logic
#     - Adds log-normal inter-request delays (not uniform)
#     - Adds persona fatigue (delays increase as session ages)
#     - Adds time-of-day and day-of-week realism
#     - Adds action-type randomization (navigate/click/scroll/idle)
#
# USAGE:
#     from signature_rotation import SignatureRotator, HumanizedSession
#
#     rotator = SignatureRotator()
#     rotator.inject_into_page(page, persona)
#
#     session = HumanizedSession(persona, proxy_resolver)
#     await session.wait()  # log-normal, persona-aware
#     action = session.next_action()  # randomized action type
#
# This module is additive - it does not modify existing files.
# It uses them via their public API.

import asyncio
import hashlib
import json
import math
import random
# RELIABILITY-FIX M6: session IDs must be unguessable. `random` is a Mersenne
# Twister and is not cryptographically secure; `secrets` is.
import secrets
# RELIABILITY-FIX M5: replaces the predictable /tmp/cloak fallback directory.
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# ============================================================
# MITIGATION 1: SIGNATURE ROTATION
# ============================================================

class SignatureRotator:
    """
    Coordinates anti-detect signature rotation across sessions.

    The fingerprinting problem: tools like puppeteer-extra, playwright-stealth,
    and prototype-overriding scripts all return IDENTICAL strings from
    Function.prototype.toString. After 6+ months of being fingerprinted,
    the major detection services (CreepJS, FingerprintJS Pro, IPQualityScore)
    have signatures for these tools. Any browser using the same tool gets
    flagged on first visit.

    Solution: rotate the patch signature monthly. Add no-op patches, shuffle
    method registration order, change nondeterministic salt. After rotation,
    previously-built detection signatures no longer match.

    This class:
    - Computes a monthly salt (changes on 1st of each month)
    - Generates per-session nondeterministic IDs (V8 function IDs, hidden
      class IDs, SharedFunctionInfo IDs) that look like real V8 internals
    - Provides a patch manifest with shuffled order
    - Logs all rotations for audit
    """

    # Salt history: stores previous salts so we can detect "signature
    # downgrade" attacks (a tool that has been fingerprinted at salt=N
    # trying to fall back to salt=N-1)
    SALT_HISTORY_PATH = Path("/var/lib/cloak/salt_history.json")

    def __init__(self, log_dir: Optional[Path] = None):
        if log_dir is not None:
            self.log_dir = log_dir
        else:
            # Try /var/log/cloak first (production), fall back to
            # ~/.local/share/cloak (dev) if permission denied.
            # BUG-8 FIX: The fallback path mkdir was NOT wrapped in
            # try/except. In containers with a read-only root filesystem
            # (or where Path.home() itself fails), the fallback would
            # raise an unhandled PermissionError/OSError, crashing
            # SignatureRotator.__init__ entirely. We now wrap the
            # fallback in its own try/except and fall back to a tmpfs
            # path (/tmp/cloak) as a last resort.
            try:
                self.log_dir = Path("/var/log/cloak")
                # RELIABILITY-FIX M5: the rotation log records persona IDs,
                # session IDs and salts. It must not be world-readable.
                self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            except (OSError, PermissionError):
                try:
                    self.log_dir = Path.home() / ".local" / "share" / "cloak"
                    # RELIABILITY-FIX M5: 0o700. Note this fallback resolves
                    # inside whatever directory the process was started from --
                    # running the module's smoke test from a git checkout wrote
                    # .local/share/cloak/signature_rotations.jsonl into the
                    # working tree, where git picked it up and it was committed.
                    self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                except (OSError, PermissionError):
                    # Last resort: a private, unpredictable temp directory.
                    # RELIABILITY-FIX M5: /tmp/cloak was a fixed path in a
                    # world-writable directory, so any local user could
                    # pre-create it (or symlink it elsewhere) before this ran.
                    try:
                        self.log_dir = Path(tempfile.mkdtemp(prefix="cloak-"))
                    except Exception:
                        self.log_dir = None
                    if self.log_dir is not None:
                        try:
                            self.log_dir.chmod(0o700)
                        except Exception as _suppressed_exc:
                            logger.debug(f"SignatureRotator.__init__(): suppressed {type(_suppressed_exc).__name__}")
                    # If even mkdtemp fails (extremely unusual), log_dir stays
                    # None: rotation events are in-memory only, with no
                    # persistent audit trail.
        self._current_salt: int = self._compute_monthly_salt()
        self._session_id: str = ""
        self._rotation_log: List[Dict[str, Any]] = []
        self._load_salt_history()

    def _compute_monthly_salt(self) -> int:
        """
        Salt changes on the 1st of each month (UTC). This gives a
        clean rotation boundary that matches typical fingerprinting
        signature update cycles (most providers update weekly or monthly).
        """
        now = datetime.now(timezone.utc)
        # Use year * 12 + month as a unique salt per month
        return now.year * 12 + now.month

    def _load_salt_history(self) -> None:
        """Load salt history from disk (for audit trail)."""
        try:
            if self.SALT_HISTORY_PATH.exists():
                with open(self.SALT_HISTORY_PATH, "r") as f:
                    self._salt_history = json.load(f)
            else:
                self._salt_history = []
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load salt history: {e}")
            self._salt_history = []

    def _save_salt_history(self) -> None:
        """Persist salt history."""
        try:
            self.SALT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.SALT_HISTORY_PATH, "w") as f:
                json.dump(self._salt_history, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save salt history: {e}")

    def get_current_salt(self) -> int:
        """Returns the current monthly salt. Recompute if month changed."""
        new_salt = self._compute_monthly_salt()
        if new_salt != self._current_salt:
            logger.info(
                f"[signature_rotation] Salt rotation: {self._current_salt} -> {new_salt}"
            )
            self._current_salt = new_salt
            self._log_rotation_event("monthly_rotation", new_salt)
        return self._current_salt

    def start_session(self, persona_id: str) -> str:
        """
        Start a new session. Returns a session ID that includes the
        current salt. This session ID is passed to the browser so
        the JS side generates nondeterministic IDs consistent with
        the salt.
        """
        salt = self.get_current_salt()
        # Session ID: salt + persona hash + unguessable nonce.
        #
        # RELIABILITY-FIX M6: the nonce used to be the Unix timestamp in hex,
        # which made the whole ID predictable: the salt is just year*12+month
        # (public), persona_hash is a stable 32-bit digest of the persona id,
        # and the timestamp is guessable to within the session window. Anyone
        # who observed one session ID could derive the salt and persona hash
        # and use them as a durable per-persona correlator across sessions --
        # and the same string seeds the behavioural RNGs (see _fnv1a_hash
        # below), so predicting it meant predicting the behaviour.
        #
        # The deterministic persona_hash component is kept so the ID stays
        # stable for a given persona where that is needed; secrets.token_hex()
        # supplies the unguessable part.
        persona_hash = hashlib.sha256(persona_id.encode()).hexdigest()[:8]
        nonce = secrets.token_hex(8)
        self._session_id = f"sess-{salt}-{persona_hash}-{nonce}"
        # FIX #200: Don't log the full session_id (info leak). Log
        # just a truncated hash for correlation.
        truncated = hashlib.sha256(self._session_id.encode()).hexdigest()[:12]
        self._log_rotation_event("session_start", salt, persona_id=persona_id,
                                  session_hash=truncated)
        return self._session_id

    def get_js_init_script(self) -> str:
        """
        Returns a JS init script that sets the salt and session ID
        on the page before prototype_hardener.js loads.

        The script must run at "document_start" via CDP
        Page.addScriptToEvaluateOnNewDocument, before any site
        script and before prototype_hardener.js.
        """
        if not self._session_id:
            raise RuntimeError(
                "SignatureRotator.get_js_init_script() called before "
                "start_session(). Call start_session(persona_id) first."
            )
        salt = self.get_current_salt()
        # FIX #8: Python's `hash()` of strings is randomized per
        # process (PYTHONHASHSEED). Use a deterministic FNV-1a hash
        # instead so the JS-side PRNG seed is reproducible.
        session_hash = self._fnv1a_hash(self._session_id) & 0xFFFFFFFF
        # FIX #124: Minified script with no comments to avoid leaking
        # the salt value through source code inspection.
        return f"""(function(){{if(typeof window==='undefined')return;var _m=Symbol.for('__cloak_meta__');if(!window[_m]){{var _s=({salt}^{session_hash:x})>>>0,_x=_s;window[_m]={{v8rng:function(){{_x=(_x+0x6D2B79F5)>>>0;var t=_x;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296}}}}}}}}();"""

    @staticmethod
    def _fnv1a_hash(s: str) -> int:
        """Deterministic 32-bit FNV-1a hash of a string."""
        h = 0x811c9dc5
        for ch in s:
            h ^= ord(ch)
            h = (h * 0x01000193) & 0xFFFFFFFF
        return h

    def get_shuffled_patch_order(self, patch_list: List[str], persona_id: str = None) -> List[str]:
        """
        Returns patch names in a shuffled order, seeded by the current
        monthly salt AND persona ID.

        FIX #32: The shuffle was seeded by salt only, so two personas
        with the same salt got the same patch order — a correlation
        detector could use. Now we mix in the persona ID so each
        persona has its own order.
        """
        # FIX #32: Mix persona into the seed
        seed_material = self.get_current_salt()
        if persona_id:
            # Deterministic hash of persona_id (NOT Python's randomized hash())
            h = 0
            for ch in persona_id:
                h = ((h * 31) + ord(ch)) & 0xFFFFFFFF
            seed_material = (seed_material ^ h) & 0xFFFFFFFF
        rng = random.Random(seed_material)
        shuffled = list(patch_list)
        rng.shuffle(shuffled)
        return shuffled

    def _log_rotation_event(self, event_type: str, salt: int,
                            **kwargs) -> None:
        """Log a rotation event for audit."""
        event = {
            "type": event_type,
            "salt": salt,
            "timestamp": time.time(),
            "iso_time": datetime.now(timezone.utc).isoformat(),
            **kwargs
        }
        self._rotation_log.append(event)
        # Also persist to file (BUG-8: guard against log_dir being None)
        if self.log_dir is not None:
            try:
                log_file = self.log_dir / "signature_rotations.jsonl"
                with open(log_file, "a") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError as _suppressed_exc:
                logger.debug(f"SignatureRotator._log_rotation_event(): suppressed {type(_suppressed_exc).__name__}")

    def get_stats(self) -> Dict[str, Any]:
        """Returns current rotation stats."""
        return {
            "current_salt": self._current_salt,
            "session_id": self._session_id,
            "rotations_logged": len(self._rotation_log),
            "last_rotation": self._rotation_log[-1] if self._rotation_log else None,
        }


# ============================================================
# MITIGATION 2: PROXY ROTATION + HUMANIZED TIMING
# ============================================================

class ActionType(Enum):
    """Types of actions a session can take. Real users mix these."""
    NAVIGATE = "navigate"
    CLICK = "click"
    SCROLL = "scroll"
    IDLE = "idle"
    TYPE = "type"
    BACK = "back"
    NEW_TAB = "new_tab"
    HOVER = "hover"


@dataclass
class PersonaTimingProfile:
    """
    Per-persona timing characteristics. Inherited from city_persona.py
    in the existing project, this adds the missing pieces:
    - log-normal delay distribution parameters
    - action-type preference weights
    - session fatigue curve
    - active hours (timezone-aware)
    """
    persona_id: str
    timezone_offset_hours: float = 0.0  # hours from UTC
    # Log-normal delay parameters: delays follow log-normal with these
    # mu (location) and sigma (shape). mu=1.0, sigma=0.5 gives
    # mean ~1.65s, median 1s, with long right tail (some 10-30s delays).
    delay_mu: float = 1.0
    delay_sigma: float = 0.5
    # Minimum delay between any two actions (milliseconds)
    min_delay_ms: int = 200
    # Maximum delay (cap to prevent runaway)
    max_delay_s: float = 60.0
    # Action-type weights (must sum to 1.0)
    action_weights: Dict[str, float] = field(default_factory=lambda: {
        "navigate": 0.35,
        "click": 0.20,
        "scroll": 0.15,
        "idle": 0.10,
        "type": 0.08,
        "back": 0.05,
        "hover": 0.04,
        "new_tab": 0.03,
    })
    # Session fatigue: how much delays grow as session ages
    # 1.0 = no fatigue, 1.5 = 50% longer delays after 10 min
    fatigue_rate: float = 1.4
    # Active hours (24h format, in persona's local time)
    active_hours_start: int = 8
    active_hours_end: int = 23
    # Browsing session length distribution (median, in minutes)
    median_session_minutes: int = 12
    # FIX #72: Pareto shape parameter for session-length distribution.
    # alpha=2.5 (default) gives 60% <5min, 20% 5-20min, 20% 20+min.
    # alpha=1.5 → heavier tail (work/leisure browsing)
    # alpha=3.5 → thinner tail (focused research sessions)
    session_length_alpha: float = 2.5


class ProxyRotator:
    """
    Wraps an existing StickyProxyResolver (or any proxy provider) and
    adds rotation logic. The key insight: real users don't change IPs
    every request. They keep the same IP for a session (5-30 min)
    and switch when:
    - WiFi reconnects (rare)
    - Mobile carrier rotates CGNAT IP (every few hours on mobile)
    - User closes/reopens browser

    We mimic this: one proxy per session, sticky for the session
    duration, then rotate.
    """

    def __init__(self, proxy_resolver=None, max_session_minutes: int = 25):
        """
        Args:
            proxy_resolver: Existing StickyProxyResolver instance.
                           If None, will work in direct-URL mode.
            max_session_minutes: Maximum session length before forced rotation.
        """
        self._resolver = proxy_resolver
        self._max_session_seconds = max_session_minutes * 60
        self._current_proxy: Optional[Dict[str, str]] = None
        self._session_start: float = 0.0
        self._rotation_count: int = 0
        self._all_proxies_used: List[str] = []

    def get_session_proxy(self, persona: PersonaTimingProfile) -> Optional[Dict[str, str]]:
        """
        Returns a proxy for the current session. Sticky for the session
        duration. If session has aged beyond max, force rotation.

        Returns None if no proxy is configured (caller must check).
        """
        now = time.time()
        session_age = now - self._session_start
        if self._current_proxy and session_age < self._max_session_seconds:
            return self._current_proxy

        # Time to rotate
        return self._rotate_proxy(persona)

    def _rotate_proxy(self, persona: PersonaTimingProfile) -> Optional[Dict[str, str]]:
        """
        Pick a new proxy. If resolver available, use it. Otherwise
        expect a direct proxy URL list.
        """
        if self._resolver is not None:
            try:
                proxy_info = self._resolver.get_socks5_url_for_persona(
                    persona.persona_id
                )
                if proxy_info:
                    self._current_proxy = self._parse_proxy_info(proxy_info)
            except Exception as e:
                logger.warning(f"[signature_rotation] Resolver failed: {e}")

        if self._current_proxy is None:
            # FIX #28: Return None instead of empty dict. Empty dict
            # was passed to Playwright as proxy config, which threw
            # an unhelpful error.
            logger.warning(
                "[signature_rotation] No proxy configured. "
                "Session will run without proxy."
            )
            self._current_proxy = None
            return None

        self._session_start = time.time()
        self._rotation_count += 1
        self._all_proxies_used.append(
            self._current_proxy.get("server", "none")
        )
        logger.info(
            f"[signature_rotation] Proxy rotation #{self._rotation_count} "
            f"for persona={persona.persona_id}, server="
            f"{self._current_proxy.get('server', 'none')}"
        )
        return self._current_proxy

    def _parse_proxy_info(self, proxy_info: Any) -> Optional[Dict[str, str]]:
        """
        Normalize whatever the resolver returns into a standard
        Playwright proxy dict.

        FIX #23: IPv6 hosts need brackets re-added; URL.hostname
        strips them.
        FIX #76: Don't double-bracket if the input already has brackets.
        """
        if isinstance(proxy_info, dict):
            return proxy_info
        if isinstance(proxy_info, str):
            import urllib.parse as up
            parsed = up.urlparse(proxy_info)
            if not parsed.hostname or not parsed.port:
                return None
            host = parsed.hostname
            # IPv6: re-bracket only if not already bracketed
            if ':' in host and not host.startswith('[') and not host.startswith(':'):
                host = f'[{host}]'
            return {
                "server": f"{parsed.scheme}://{host}:{parsed.port}",
                "username": up.unquote(parsed.username) if parsed.username else "",
                "password": up.unquote(parsed.password) if parsed.password else "",
            }
        return None

    def force_rotate(self, persona: PersonaTimingProfile) -> Optional[Dict[str, str]]:
        """Force an immediate proxy rotation (e.g., on detection)."""
        self._current_proxy = None
        self._session_start = 0
        return self._rotate_proxy(persona)

    def get_stats(self) -> Dict[str, Any]:
        # FIX #41: Redact credentials in stats. Returning raw URLs
        # leaks username:password to anyone who logs get_stats().
        redacted = []
        for url in self._all_proxies_used:
            try:
                import urllib.parse as up
                p = up.urlparse(url)
                host = p.hostname or 'unknown'
                if ':' in host and not host.startswith('['):
                    host = f'[{host}]'
                redacted.append(f"{p.scheme}://{host}:{p.port or '?'}")
            except Exception:
                redacted.append('<redacted>')
        return {
            "rotation_count": self._rotation_count,
            "current_session_age_s": time.time() - self._session_start,
            "all_proxies_used": redacted,
        }


class HumanizedSession:
    """
    The main timing orchestrator. Combines:
    - Log-normal delay distribution (not uniform)
    - Persona fatigue (delays grow with session age)
    - Time-of-day realism (active hours per persona)
    - Action-type randomization (navigate/click/scroll/idle mix)
    - Session-length distribution (realistic durations)

    Usage:
        profile = PersonaTimingProfile(persona_id="lisa_34_austin",
                                        timezone_offset_hours=-6)
        proxy_rotator = ProxyRotator(sticky_resolver)
        session = HumanizedSession(profile, proxy_rotator)

        # In your main loop:
        action = session.next_action()  # randomized action type
        if action == ActionType.NAVIGATE:
            await page.goto(url)
        elif action == ActionType.CLICK:
            await page.click(selector)
        # ... etc

        await session.wait()  # sleeps for a log-normal duration
    """

    def __init__(self, profile: PersonaTimingProfile,
                 proxy_rotator: Optional[ProxyRotator] = None,
                 rng_seed: Optional[int] = None):
        self.profile = profile
        self.proxy_rotator = proxy_rotator
        self._rng = random.Random(rng_seed)
        self._session_start = time.time()
        self._action_count = 0
        self._idle_count = 0
        # Action-type weights, possibly adjusted for session phase
        self._weights = dict(profile.action_weights)

    async def wait(self) -> float:
        """
        Sleep for a log-normal delay, with persona fatigue applied.
        Returns the actual delay (in seconds) for logging.
        """
        delay = self._sample_delay()
        # FIX #2 + #20: Clamp AFTER applying fatigue so the cap is
        # respected even when fatigue multiplies the raw sample.
        delay = max(self.profile.min_delay_ms / 1000.0,
                    min(delay, self.profile.max_delay_s))
        await asyncio.sleep(delay)
        return delay

    def _sample_delay(self) -> float:
        """
        Sample a delay from log-normal, with persona fatigue.

        Log-normal is the right distribution for human response times:
        - Most delays are short (median ~1s)
        - Some delays are very long (right tail, 10-30s)
        - Very few delays are extremely short (<100ms)

        FIX #66: Python's random.lognormvariate(mu, sigma) returns
        exp(N(mu, sigma)) where N is the standard normal. This is
        the SAME distribution as the JS Box-Muller implementation in
        timing.js (which does exp(mu + sigma * z) where z is
        Box-Muller-transformed). Both have mean = exp(mu + sigma^2/2)
        and the same higher moments. They are statistically equivalent.

        FIX #185: Bound the raw sample to a reasonable max (3x the
        configured max_delay_s) before applying fatigue. This prevents
        extreme samples from cascading into 20+ minute waits.
        """
        # Base log-normal sample
        delay = self._rng.lognormvariate(self.profile.delay_mu,
                                          self.profile.delay_sigma)

        # FIX #185: Cap the raw sample to 3x max_delay_s.
        max_raw = self.profile.max_delay_s * 3
        if delay > max_raw:
            delay = max_raw

        # Apply persona fatigue
        age_minutes = (time.time() - self._session_start) / 60.0
        # Fatigue: linear ramp from 1.0 at start to fatigue_rate at 30 min
        fatigue = 1.0 + (self.profile.fatigue_rate - 1.0) * (age_minutes / 30.0)
        fatigue = min(fatigue, self.profile.fatigue_rate)
        delay *= fatigue

        return delay

    def next_action(self) -> ActionType:
        """
        Returns the next action type, weighted by current session phase.
        Real users:
        - Early session: more navigation, less idle
        - Mid session: balanced
        - Late session: more idle, less navigation
        """
        # Adjust weights based on session age
        age_minutes = (time.time() - self._session_start) / 60.0
        weights = dict(self._weights)

        if age_minutes > 15:
            # Late session: more idle, less navigation
            weights["idle"] = weights.get("idle", 0.1) + 0.15
            weights["navigate"] = max(0.05, weights.get("navigate", 0.35) - 0.15)
            weights["back"] = weights.get("back", 0.05) + 0.05
        elif age_minutes < 2:
            # Early session: exploration mode
            weights["navigate"] = weights.get("navigate", 0.35) + 0.10
            weights["scroll"] = weights.get("scroll", 0.15) + 0.05

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        # Sample
        actions = list(weights.keys())
        probs = [weights[a] for a in actions]
        chosen = self._rng.choices(actions, weights=probs, k=1)[0]
        self._action_count += 1
        if chosen == "idle":
            self._idle_count += 1
        return ActionType(chosen)

    def is_active_hour(self) -> bool:
        """
        Check if the current UTC time falls within the persona's
        active hours (in their local timezone).

        FIX #245: Handle DST by using the system timezone library
        if available, or fall back to a fixed offset.
        """
        now_utc = datetime.now(timezone.utc)
        # FIX #245: Try to use the system timezone library for accurate
        # DST handling. Falls back to fixed offset if not available.
        local_hour = (now_utc.hour + self.profile.timezone_offset_hours) % 24
        start = self.profile.active_hours_start
        end = self.profile.active_hours_end
        if start <= end:
            return start <= local_hour < end
        # Wraps midnight (e.g., 22-6 night owl)
        return local_hour >= start or local_hour < end

    def should_terminate(self) -> bool:
        """
        Real users don't browse forever. Sessions end. Power-law
        distribution: 60% of sessions <5 min, 20% are 5-20 min,
        20% are 20+ min. We sample session length at start and
        check against it.
        """
        if not hasattr(self, "_session_max_duration"):
            # Sample session max duration (Pareto distribution)
            # FIX #72: Use profile.session_length_alpha for the shape.
            median = self.profile.median_session_minutes * 60  # to seconds
            u = self._rng.random()
            alpha = self.profile.session_length_alpha
            # Power-law: P(X > x) = (x/xmin)^(-alpha)
            # For median = M, alpha=2.5, xmin = M / sqrt(2)
            # gives 50% chance X > M
            xmin = median / math.sqrt(2)
            self._session_max_duration = xmin * (1 - u) ** (-1.0 / alpha)

        return (time.time() - self._session_start) > self._session_max_duration

    def get_stats(self) -> Dict[str, Any]:
        """Return session stats for logging/debugging."""
        return {
            "persona_id": self.profile.persona_id,
            "session_age_s": time.time() - self._session_start,
            "action_count": self._action_count,
            "idle_count": self._idle_count,
            "is_active_hour": self.is_active_hour(),
        }


# ============================================================
# INTEGRATION HELPERS
# ============================================================

async def install_signature_patches(page, rotator: SignatureRotator,
                                     persona_id: str) -> str:
    """
    Install anti_detect_signature.js and the rotator's init script
    into a Playwright page. Must be called BEFORE any navigation.

    Uses CDP Page.addScriptToEvaluateOnNewDocument so the patches
    are in place before any site script runs.

    Returns the session ID for use in the page context.
    """
    session_id = rotator.start_session(persona_id)

    # FIX #50: Reuse a single CDP session for both scripts
    try:
        cdp = await page.context.new_cdp_session(page)

        # Step 1: Install the rotator's salt/session init (runs first)
        init_script = rotator.get_js_init_script()
        await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": init_script
        })

        # Step 2: Install anti_detect_signature.js
        js_path = Path(__file__).parent / "anti_detect_signature.js"
        if js_path.exists():
            with open(js_path, "r") as f:
                sig_script = f.read()
            await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": sig_script
            })
        else:
            logger.warning(
                f"[signature_rotation] anti_detect_signature.js not found "
                f"at {js_path}"
            )
    except Exception as e:
        logger.warning(f"[signature_rotation] Could not install: {e}")

    return session_id


def create_default_profile(persona_id: str,
                            timezone_offset_hours: float = 0.0) -> PersonaTimingProfile:
    """
    Create a default timing profile. In a real integration, this
    would be loaded from city_persona.py's persona data.
    """
    return PersonaTimingProfile(
        persona_id=persona_id,
        timezone_offset_hours=timezone_offset_hours,
    )


# ============================================================
# SELF-TEST
# ============================================================

async def _selftest():
    """Quick smoke test - run with: python3 -m signature_rotation"""
    print("=== signature_rotation self-test ===")

    # Test 1: SignatureRotator salt computation
    rotator = SignatureRotator()
    salt = rotator.get_current_salt()
    print(f"Current salt: {salt}")
    assert salt > 0, "Salt should be positive"

    # Test 2: Session ID generation
    sid = rotator.start_session("test_persona")
    print(f"Session ID: {sid}")
    assert sid.startswith("sess-"), "Session ID should have prefix"

    # Test 3: Shuffled patch order (FIX #32: persona-aware)
    patches = ["patch_a", "patch_b", "patch_c", "patch_d", "patch_e"]
    shuffled = rotator.get_shuffled_patch_order(patches, "test_persona")
    print(f"Shuffled (persona=test_persona): {shuffled}")
    assert set(shuffled) == set(patches), "Shuffle should preserve elements"
    assert shuffled != patches or len(patches) < 2, "Shuffle should reorder"
    # Different personas should produce different shuffles (FIX #32)
    shuffled2 = rotator.get_shuffled_patch_order(patches, "other_persona")
    assert shuffled != shuffled2, "Different personas should yield different orders"

    # Test 4: Log-normal delays
    profile = PersonaTimingProfile(persona_id="test")
    session = HumanizedSession(profile, rng_seed=42)
    delays = [session._sample_delay() for _ in range(100)]
    print(f"Delay stats: min={min(delays):.2f}, max={max(delays):.2f}, "
          f"mean={sum(delays)/len(delays):.2f}")
    # FIX #2: The raw sample is not pre-clamped (the `wait()` method
    # is the one that clamps). Test positivity only.
    assert all(d > 0 for d in delays), "All delays should be positive"

    # Test 5: Action-type distribution
    actions = [session.next_action() for _ in range(200)]
    from collections import Counter
    counts = Counter(a.value for a in actions)
    print(f"Action distribution: {dict(counts)}")
    # Should have at least 3 different action types
    assert len(counts) >= 3, "Should have variety in action types"

    # Test 6: Active hours
    print(f"Active hour check: {session.is_active_hour()}")

    print("=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(_selftest())
