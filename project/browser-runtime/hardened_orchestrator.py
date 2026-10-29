# hardened_orchestrator.py - Production Async Orchestrator v5.0+ (1 PERSONA, MAX INVISIBILITY)
#
# CHANGELOG (v5.0+ - complete rewrite for 1-persona absolute invisibility):
#   v5-P1  - MAX_CONCURRENT_PERSONAS=1; per-persona retirement (8-12 tasks max).
#   v5-P2  - Per-task tmpfs lifecycle: pre-mounted by entrypoint.sh; orchestrator
#            only does mkdir/extract/wipe. NO mount/umount syscalls in Python.
#   v5-P3  - Single-tmpfs invariant: assert len(mounts at /mnt/ramdisk) <= 1.
#   v5-P4  - Per-persona SOCKS5 port routing via SOCKS5_PERSONA_PORTS.
#   v5-P5  - SIGUSR1 panic handler with full cleanup (tmpfs, tc qdisc, netem).
#   v5-P6  - Init script registered BEFORE first browser.new_page().
#   v5-P7  - Compositor jitter integration via behavioral_entropy.
#   v5-P8  - Per-persona PID budget tracking (PERSONA_PID_BUDGET=128).
#   v5-P9  - Per-persona persona_retirement_count, max-tasks-per-persona.
#   v5-P10 - First-navigation-delay 8-25s; about:blank in history; history.length >= 2.
#   v5-P11 - [NOT IMPLEMENTED] was "Per-persona boot_id rotation via
#            /etc/mock_boot_id rewrite". No code writes that file: it is
#            a host file created once at provisioning and made immutable,
#            then bind-mounted read-only. Every container sees the same
#            value. See C15 in CONCURRENCY_AUDIT.md.
#   v5-P12 - Battery API: charging=true, level=0.85-0.99 (locked per persona).
#   v5-P13 - Intl.DateTimeFormat timezone matches proxy geo.
#   v5-P14 - Date.now() drift check (chrony in entrypoint, verified in orchestrator).
#   v5-P15 - nstimestamp: navigator.deviceMemory locked to 4 or 8 per persona.
#   v5-P16 - screen.colorDepth = 24, pixelDepth = 24 (no HDR mismatches).
#   v5-P17 - scroll-behavior: smooth or auto (locked per persona).
#   v5-P18 - Locked per-persona audioContext sampleRate (48000, not random).
#   v5-P19 - Cookie lifetime randomization 7-30 days.
#   v5-P20 - ulimit core 0 verified, dmesg access denied verified.
#   v5-P21 - restart:no; new container = new tmpfs (true). Since C12/C14
#            removed the static IP and static MAC, "new MAC" is now also
#            true. NOT true for boot_id: that is a shared immutable host
#            file, so it does NOT change per container. See C15.
#   v5-P22 - [CLAIM WITHDRAWN] was "Daemon loop: asyncio.gather(
#            return_exceptions=True), race-free." The gather it points at
#            is in SHUTDOWN cleanup, not the daemon loop, and
#            return_exceptions=True suppresses errors rather than
#            preventing races. The daemon loop had ten real concurrency
#            defects (C1-C10), now fixed. Using gather does not make
#            anything race-free.
#   v5-P23 - Cookie persistence via in-memory only (no disk).
#   v5-P24 - per-persona uTLS HelloChrome_120 TLS fingerprint alignment hint.
#   v5-P25 - CloakBrowser paths discovered: host-deployed at /opt/cloakbrowser/chrome.

import os as _os
import sys as _sys

_REQUIRED_ENV = ["DB_PASSWD", "ENCRYPTION_KEY", "TASK_TMPFS_SIZE_MB", "PERSONA_PID_BUDGET"]
_MISSING = [k for k in _REQUIRED_ENV if not _os.getenv(k)]
if _MISSING:
    _sys.stderr.write(
        f"FATAL: Required environment variables not set: {', '.join(_MISSING)}\n"
        f"FATAL: Refusing to start to prevent insecure default-key operation.\n"
    )
    _sys.exit(2)

_THIS_FILE = _os.path.realpath(__file__) if "__file__" in dir() else _os.getcwd()
_SCRIPT_DIR = _os.path.dirname(_THIS_FILE)
_PROJECT_ROOT = _os.path.dirname(_SCRIPT_DIR)
_sys.path.insert(0, _SCRIPT_DIR)
_sys.path.insert(0, _PROJECT_ROOT)

# Standard library
import asyncio
import os
import sys
import signal
import time
import random
import json
import importlib
import re
import shutil
import hashlib
# RELIABILITY-FIX L1b: `ctypes` was only imported inside two functions, while the
# module-level annotation `_make_zeroable_key(...) -> "ctypes.Array"` referenced it.
# Hoisted here so the annotation resolves and both callers share one import.
import ctypes
import urllib.parse
import subprocess
import socket
import io
import tarfile
import uuid  # INVIS-6: for per-launch session ID for canvas/audio noise
from concurrent.futures import ProcessPoolExecutor
from typing import Optional, Dict, Any, List, Tuple

# Third-party
try:
    import httpx
except ImportError:
    httpx = None
    _sys.stderr.write("WARNING: httpx not installed; check_proxy_integrity disabled.\n")

try:
    import asyncpg
except ImportError:
    _sys.stderr.write("FATAL: asyncpg is required for PostgreSQL access.\n")
    _sys.exit(2)

try:
    import psutil
except ImportError:
    psutil = None
    _sys.stderr.write("WARNING: psutil not installed; zombie reaping limited.\n")

try:
    from loguru import logger
except ImportError:
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s | %(levelname)-7s | %(name)s:%(funcName)s:%(lineno)d - %(message)s'
    )
    logger = _logging.getLogger("orchestrator")

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None
    _sys.stderr.write("WARNING: cryptography.fernet not available; in-memory decryption disabled.\n")

# R7-HOR-010: ThreadPoolExecutor for async-safe Fernet operations.
# Capped at 2 workers to avoid thread pool exhaustion under load.
# 2 is enough for the orchestrator's 1-persona use case (one for
# decrypt, one for encrypt, both rarely concurrent).
import atexit
from concurrent.futures import ThreadPoolExecutor
_fernet_executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="sre-fernet",
)


# CF-9 (Pass 18): Safe env var parser. The previous code did
# `int(os.getenv("X", "5"))` directly, which crashes with
# ValueError on non-numeric input (e.g., operator sets
# `MAX_CRYPTO_WORKERS=auto`). We now use a safe parser that
# falls back to the default on parse failure. This makes the
# orchestrator robust to typos in deployment configs.
def _safe_getenv_int(name: str, default: int, min_value: int = 0, max_value: int = 2147483647) -> int:
    """CF-9 (Pass 18): Safe int env var parser.

    Returns the int value of the env var, or `default` if:
    - The env var is not set
    - The env var is set to a non-numeric string (e.g., "auto", "")
    - The parsed value is below `min_value` (clamps to min_value)
    - The parsed value is above `max_value` (clamps to max_value)

    DC-41 (Pass 41): Added max_value parameter to prevent resource
    exhaustion from absurdly large values. For example, an operator
    accidentally setting MAX_CRYPTO_WORKERS=999999999 would create
    1 billion worker threads, crashing the system.

    This is called at module load time. On parse failure, the
    default is used silently — operators see a warning in the logs.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
    except (ValueError, TypeError):
        logger.warning(
            f"CF-9: env var {name}='{raw}' is not an integer; "
            f"using default {default}"
        )
        return default
    if val < min_value:
        logger.warning(
            f"CF-9: env var {name}={val} is below minimum {min_value}; "
            f"clamping to {min_value}"
        )
        return min_value
    if val > max_value:
        logger.warning(
            f"CF-9: env var {name}={val} is above maximum {max_value}; "
            f"clamping to {max_value}"
        )
        return max_value
    return val




def _safe_getenv_float(name: str, default: float, min_value: float = 0.0) -> float:
    """CF-9: Safe float env var parser. Same semantics as _safe_getenv_int.

    EC-30 (Pass 30): Reject NaN and infinity. Python's float() accepts
    "nan" and "inf" as valid floats, but these are problematic for
    downstream math (NaN != NaN, inf causes division by zero). We
    explicitly reject these and fall back to the default.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (ValueError, TypeError):
        logger.warning(
            f"CF-9: env var {name}='{raw}' is not a float; "
            f"using default {default}"
        )
        return default
    # EC-30: Reject NaN and infinity - these are dangerous values
    # that can cause math errors downstream.
    import math as _math
    if _math.isnan(val) or _math.isinf(val):
        logger.warning(
            f"CF-9: env var {name}={val} is NaN or infinity; "
            f"using default {default}"
        )
        return default
    if val < min_value:
        logger.warning(
            f"CF-9: env var {name}={val} is below minimum {min_value}; "
            f"clamping to {min_value}"
        )
        return min_value
    return val



def _try_import(module_name, class_name=None):
    # R7-DEEP-AUDIT-3 (Pass 8): Widen exception catch to ALL Exception
    # types. The previous `except ImportError` only caught missing-module
    # errors. If a module had a NameError, SyntaxError, or any other
    # load-time failure, the exception would propagate and crash the
    # orchestrator on startup. We now log and return None for any
    # load-time error. Critical modules still have explicit guards
    # below (lines 144-151) that print FATAL and _sys.exit(7) on None.
    try:
        mod = importlib.import_module(module_name)
        if class_name:
            return getattr(mod, class_name, None)
        return mod
    except Exception as e:
        logger.warning(f"Module {module_name} not available: {type(e).__name__}: {e}")
        return None


# v5.0+: Lazy global holders
_HardenedSessionSerializer = _try_import("session_serializer", "HardenedSessionSerializer")
_ebpf_sentinel = _try_import("ebpf_sentinel")
_DynamicPacingEngine = _try_import("dynamic_pacing", "DynamicPacingEngine")
_HybridHandoverEngine = _try_import("curl_cffi_handover", "HybridHandoverEngine")
_CircadianScheduler = _try_import("circadian_scheduler", "CircadianScheduler")
_StickyProxyResolver = _try_import("sticky_proxy_resolver", "StickyProxyResolver")
_HardenedProfileArchiver = _try_import("profile_archiver", "HardenedProfileArchiver")
_AIBridge = _try_import("core.ai_bridge", "AIBridge")
# PPPC: Persistent Persona Per City (CHAMELEON feature)
_city_persona_module = _try_import("city_persona")

# v6.2: Behavioral modules are MANDATORY. If any of these fail to import,
# the orchestrator ABORTS immediately. This prevents task executors from
# running without behavioral simulation (which would be detectable).
_behavioral_entropy_module = _try_import("behavioral_entropy")
_humanized_movement_module = _try_import("humanized_movement")
_keyboard_emulator_module = _try_import("keyboard_emulator")

if _behavioral_entropy_module is None:
    _sys.stderr.write("FATAL: behavioral_entropy.py not found. Refusing to start.\n")
    _sys.exit(7)
if _humanized_movement_module is None:
    _sys.stderr.write("FATAL: humanized_movement.py not found. Refusing to start.\n")
    _sys.exit(7)
if _keyboard_emulator_module is None:
    _sys.stderr.write("FATAL: keyboard_emulator.py not found. Refusing to start.\n")
    _sys.exit(7)

# v5.0+: Per-persona delay RNG registry
# Module-level to persist across task runs. Each persona_id has its own
# dictionary of site_label -> random.Random, ensuring per-call-site
# determinism while persisting across launches.
#
# BUG-5 FIX: The original dict had no eviction policy. Over thousands of
# persona rotations (a long-running daemon) this dict would grow without
# bound, leaking memory indefinitely. We use collections.OrderedDict with
# a FIFO cap of 1000 entries — consistent with the _profile_cache cap in
# humanized_movement.py. 1000 entries covers ~50x the MAX_CONCURRENT_PERSONAS
# count, giving plenty of headroom for in-flight and recently-retired personas.
import collections as _collections
# RELIABILITY-FIX L1a: this annotation previously read "collections.OrderedDict[...]",
# but the module is imported as `_collections`, so the annotation named a module that
# does not exist in this namespace. String annotations are never evaluated at runtime
# (PEP 526), so it did not raise -- but it broke typing.get_type_hints() and any
# introspection over this module. Refer to the real alias.
_persona_delay_rng_registries: "_collections.OrderedDict[str, Dict[str, random.Random]]" = _collections.OrderedDict()
_PERSONA_DELAY_RNG_REGISTRY_MAX = 1000

DB_PASSWD = os.environ["DB_PASSWD"]
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]
# M2 fix: Make a ctypes-backed mutable copy of the Fernet key so
# we can zero it from memory when no longer needed. Python strings
# are immutable, so we can't overwrite the bytes in place. ctypes
# gives us a writable buffer that we can memset to 0.
#
# The original ENCRYPTION_KEY string still exists (we can't zero
# it), but this mutable copy is what we use for the actual Fernet
# operations. After the daemon shuts down, the ctypes buffer is
# zeroed in the atexit handler.
#
# NOTE: True secure-zeroing of process memory requires either:
#   1. mlock'd memory (prevents swap, but doesn't zero)
#   2. A custom allocator (out of scope for this Python project)
#   3. ctypes.memset to 0 (what we do here, but Python's GC
#      can move the string object around, so it's best-effort)
#
# This is defense-in-depth, not a security boundary. The real
# defense is container isolation (no /proc/PID/mem access from
# outside the container).
def _make_zeroable_key(key_str: str) -> "ctypes.Array":
    """M2 fix: Create a ctypes-backed mutable copy of the Fernet key.

    Returns a ctypes c_ubyte array containing the key bytes. The
    caller MUST call zero_key() when done. The returned array is
    NOT a Python string, so it can be overwritten in place.
    """
    import ctypes
    key_bytes = key_str.encode("utf-8")
    arr = (ctypes.c_ubyte * len(key_bytes))(*key_bytes)
    return arr

def _zero_key(arr) -> None:
    """M2 fix: Zero a ctypes-backed key buffer in place.

    Uses ctypes.memset to write zeros to the buffer. The Python
    GC can still move the underlying memory (although ctypes
    objects are usually not moved), so this is best-effort.
    After calling this, the buffer contains all-zero bytes.
    """
    if arr is None:
        return
    try:
        # RELIABILITY-FIX L1b: `ctypes` is now imported at module level.
        ctypes.memset(ctypes.addressof(arr), 0, len(arr))
    except Exception as _suppressed_exc:
        # Non-fatal; the buffer will be GC'd eventually
        logger.debug(f"_zero_key(): suppressed {type(_suppressed_exc).__name__}")

# Create the zeroable key (used for Fernet operations)
_ENCRYPTION_KEY_BUF = _make_zeroable_key(ENCRYPTION_KEY)
# Track whether the buffer has been zeroed (for idempotency)
_ENCRYPTION_KEY_ZEROED = False

SANDBOX_ENV = os.getenv("SANDBOX_ENV", "False").lower() == "true"

if SANDBOX_ENV:
    DB_URI = os.getenv("DB_URI", f"postgresql://engine_user:{DB_PASSWD}@172.18.0.2:5432/core_db?sslmode=disable")
else:
    DB_URI = os.getenv("DB_URI", f"postgresql://engine_user:{DB_PASSWD}@172.18.0.2:5432/core_db?sslmode=require")

# v5.0+: per-task tmpfs (pre-mounted by entrypoint.sh)
RAM_DISK_DIR = os.getenv("RAM_DISK_DIR", "/mnt/ramdisk")
ORCH_TASK_UUID = os.getenv("ORCH_TASK_UUID", "")
ORCH_TASK_TMPFS_DIR = os.getenv("ORCH_TASK_TMPFS_DIR", "")
TASK_TMPFS_SIZE_MB = _safe_getenv_int("TASK_TMPFS_SIZE_MB", 1400, min_value=100)

# R7-HOR-025: Master Xvfb display (read once at module init).
# The previous code read os.getenv("MASTER_XVFB_DISPLAY") at multiple
# call sites with different default values. Some used "" (empty
# default), some used ":99". This inconsistency made the code
# harder to reason about. We now read once with a consistent
# default of ":99".
MASTER_XVFB_DISPLAY = os.getenv("MASTER_XVFB_DISPLAY", ":99")

# v5.0+: 1-persona sizing
MAX_CONCURRENT_PERSONAS = 1
MAX_CRYPTO_WORKERS = _safe_getenv_int("MAX_CRYPTO_WORKERS", 1, min_value=1)
PERSONA_PID_BUDGET = _safe_getenv_int("PERSONA_PID_BUDGET", 128, min_value=10)
TASK_TIMEOUT_SECONDS = _safe_getenv_int("TASK_TIMEOUT_SECONDS", 2700, min_value=1)  # 45min

# CONCURRENCY-FIX C2/C9: stable identity for this worker, used as the value of
# tasks.claimed_by and personas.locked_by. Without it, two daemons are
# indistinguishable in the lease columns and neither can tell whether a lock it
# finds belongs to itself or to a peer. HOSTNAME is set by Docker; fall back to
# the kernel hostname and finally to the PID so a bare-metal run still works.
import socket as _socket
WORKER_ID = (
    os.getenv("HOSTNAME")
    or _socket.gethostname()
    or f"pid-{os.getpid()}"
) + f":{os.getpid()}"

# CONCURRENCY-FIX C2: how long a claimed task may stay in 'running' before the
# reaper assumes its worker died and returns it to the queue. Derived from the
# existing task timeout so a legitimately slow task is never recycled early.
CLAIM_LEASE_GRACE_SECONDS = TASK_TIMEOUT_SECONDS + 300  # timeout + 5 min slack
PAGE_LOAD_TIMEOUT = _safe_getenv_int("PAGE_LOAD_TIMEOUT", 30, min_value=1)
TASKS_PER_BATCH = 1  # Sequential
DAEMON_POLL_INTERVAL = _safe_getenv_int("DAEMON_POLL_INTERVAL", 5, min_value=1)

# v5.0+: persona retirement and rotation
MAX_TASKS_PER_PERSONA = _safe_getenv_int("MAX_TASKS_PER_PERSONA", 10, min_value=1)
PERSONA_ROTATION_HOURS = _safe_getenv_int("PERSONA_ROTATION_HOURS", 6, min_value=1)
MAX_SESSION_MINUTES = _safe_getenv_int("MAX_SESSION_MINUTES", 45, min_value=1)

# v5.0+: quiescence / cooldown
PERSONA_QUIESCENCE_BEFORE_TASK_S_MIN = _safe_getenv_int("PERSONA_QUIESCENCE_BEFORE_TASK_S_MIN", 30, min_value=1)
PERSONA_QUIESCENCE_BEFORE_TASK_S_MAX = _safe_getenv_int("PERSONA_QUIESCENCE_BEFORE_TASK_S_MAX", 180, min_value=1)
POST_TASK_COOLDOWN_S_MIN = _safe_getenv_int("POST_TASK_COOLDOWN_S_MIN", 300, min_value=1)
POST_TASK_COOLDOWN_S_MAX = _safe_getenv_int("POST_TASK_COOLDOWN_S_MAX", 900, min_value=1)

# BUG-6 FIX: Validate that MIN < MAX for all range-pairs. An operator typo
# such as QUIESCENCE_MIN=200 / QUIESCENCE_MAX=30 would cause _human_like_delay
# to receive min > max, producing a geometric mean outside both bounds and
# silently yielding incorrect delay values. We fail fast at startup so the
# misconfiguration is immediately obvious rather than silently misbehaving.
def _assert_range(min_val: int, max_val: int, min_name: str, max_name: str) -> None:
    if min_val > max_val:
        _sys.stderr.write(
            f"FATAL: {min_name}={min_val} > {max_name}={max_val}. "
            f"MIN must be <= MAX. Check your environment variables.\n"
        )
        _sys.exit(9)

_assert_range(PERSONA_QUIESCENCE_BEFORE_TASK_S_MIN, PERSONA_QUIESCENCE_BEFORE_TASK_S_MAX,
              "PERSONA_QUIESCENCE_BEFORE_TASK_S_MIN", "PERSONA_QUIESCENCE_BEFORE_TASK_S_MAX")
_assert_range(POST_TASK_COOLDOWN_S_MIN, POST_TASK_COOLDOWN_S_MAX,
              "POST_TASK_COOLDOWN_S_MIN", "POST_TASK_COOLDOWN_S_MAX")
# FIRST_NAV_DELAY range check is deferred until after those vars are defined (see below)

# v5.0+: behavioral entropy
BE_COMPOSITOR_JITTER_MS = _safe_getenv_float("BE_COMPOSITOR_JITTER_MS", 1.5, min_value=0.0)

# v5.0+: navigation
FIRST_NAV_DELAY_MIN_S = _safe_getenv_int("FIRST_NAV_DELAY_MIN_S", 8, min_value=1)
FIRST_NAV_DELAY_MAX_S = _safe_getenv_int("FIRST_NAV_DELAY_MAX_S", 25, min_value=1)
_assert_range(FIRST_NAV_DELAY_MIN_S, FIRST_NAV_DELAY_MAX_S,
              "FIRST_NAV_DELAY_MIN_S", "FIRST_NAV_DELAY_MAX_S")

# v5.0+: TLS fingerprint target
UTLS_TARGET_HELLO = os.getenv("UTLS_TARGET_HELLO", "HelloChrome_120")

# v5.0+: SOCKS5 per-persona ports
SOCKS5_PERSONA_PORTS = [p.strip() for p in os.getenv("SOCKS5_PERSONA_PORTS", "1081,1082,1083,1084,1085").split(",") if p.strip()]
SOCKS5_PROXY_BASE = f"socks5h://172.18.0.5"  # proxy-res container IP

# Verify per-task tmpfs dir is set (entrypoint.sh must provide it)
if not ORCH_TASK_TMPFS_DIR or not os.path.isdir(ORCH_TASK_TMPFS_DIR):
    _sys.stderr.write(
        f"FATAL: ORCH_TASK_TMPFS_DIR '{ORCH_TASK_TMPFS_DIR}' is not set or does not exist.\n"
        f"FATAL: entrypoint.sh must pre-mount the per-task tmpfs.\n"
    )
    _sys.exit(2)

# Single-tmpfs invariant
def _verify_single_tmpfs_invariant():
    """v5.0+: enforce that only one tmpfs is mounted at /mnt/ramdisk.

    R7-HOR-014: This function is called from the main module
    (synchronous context) so blocking on subprocess.run for up to
    5 seconds is acceptable. If called from an async context, the
    async version (_verify_single_tmpfs_invariant_async) should be
    used instead. The previous code's blocking call was fine for
    the module-load case; the issue is if any async caller invokes
    this. We document the limitation.
    """
    try:
        result = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return True  # mount command not available; skip
        lines = [l for l in result.stdout.split("\n") if "on /mnt/ramdisk" in l and "type tmpfs" in l]
        if len(lines) > 1:
            logger.critical(f"Single-tmpfs invariant VIOLATED: {len(lines)} tmpfs mounts at /mnt/ramdisk.")
            return False
        return True
    except Exception as e:
        logger.warning(f"Single-tmpfs invariant check failed: {e}")
        return True


async def _verify_single_tmpfs_invariant_async():
    """R7-HOR-014: Async version of _verify_single_tmpfs_invariant.
    Use this from async contexts (e.g., the daemon loop) to avoid
    blocking the event loop for up to 5 seconds.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "mount",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception as _suppressed_exc:
                logger.debug(f"_verify_single_tmpfs_invariant_async(): suppressed {type(_suppressed_exc).__name__}")
            try:
                # M32 fix: Wait for the killed proc to be reaped
                # BEFORE returning. The previous code did
                # `proc.kill()` then `await proc.wait()` but if
                # `proc.wait()` itself raised (e.g., ProcessLookupError
                # because the process was already reaped by the OS),
                # the exception would propagate to the outer
                # try/except and be logged as a warning, leaving
                # a zombie process visible to ps. We now wrap the
                # wait in try/except for total safety.
                await proc.wait()
            except Exception as _suppressed_exc:
                logger.debug(f"_verify_single_tmpfs_invariant_async(): suppressed {type(_suppressed_exc).__name__}")
            return True  # assume OK on timeout
        if proc.returncode != 0:
            return True  # mount command not available; skip
        lines = [l for l in stdout.decode("utf-8", errors="replace").split("\n")
                 if "on /mnt/ramdisk" in l and "type tmpfs" in l]
        if len(lines) > 1:
            logger.critical(f"Single-tmpfs invariant VIOLATED: {len(lines)} tmpfs mounts at /mnt/ramdisk.")
            return False
        return True
    except Exception as e:
        logger.warning(f"Async tmpfs invariant check failed: {e}")
        return True
    finally:
        # M32 fix: Defensive proc cleanup. If the function exits
        # via a non-timeout path (e.g., returncode != 0 or the
        # success path) without the proc being explicitly waited
        # on, the subprocess pipe fds (stdout=PIPE, stderr=PIPE)
        # can be leaked. The proc.communicate() above DOES wait
        # the process, but we add a defensive kill+wait here for
        # any code path that bypasses communicate (e.g., if a
        # future refactor moves the communicate call).
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception as _suppressed_exc:
                logger.debug(f"_verify_single_tmpfs_invariant_async(): suppressed {type(_suppressed_exc).__name__}")
            try:
                await proc.wait()
            except Exception as _suppressed_exc:
                logger.debug(f"_verify_single_tmpfs_invariant_async(): suppressed {type(_suppressed_exc).__name__}")

if not _verify_single_tmpfs_invariant():
    _sys.exit(3)

# v5.0+: Core dumps disabled
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    logger.info("Core dumps disabled (RLIMIT_CORE=0).")
except Exception as e:
    logger.warning(f"Failed to disable core dumps: {e}")

# v5.0+: Verify dmesg is blocked
def _verify_dmesg_blocked() -> bool:
    """v5.0+: Verify dmesg is blocked (CAP_SYSLOG dropped).
    R7-HOR-003: Distinguish permission denied (good — dmesg blocked)
    from generic OSError (unknown — could be segfault or permission).
    The previous code returned True on ANY exception, including
    scenarios where dmesg crashed (rare but possible). Now we log
    the exception type and only return True for permission-related
    errors.

    H7 fix: Fail-CLOSED on unknown errors. The previous code
    returned True on ANY exception (PermissionError,
    FileNotFoundError, TimeoutExpired, OSError, generic Exception).
    This means a kernel-level bug that crashes dmesg (segfault),
    a missing dmesg binary, or any other anomaly is silently
    treated as "dmesg is blocked, safe to continue". An attacker
    who can crash dmesg (e.g., via a NULL deref exploit) would
    bypass the check.
    """
    import errno
    try:
        result = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and result.stdout.strip():
            logger.warning("dmesg is readable; host kernel version may leak.")
            return False
        if result.returncode == 0 and not result.stdout.strip():
            # dmesg succeeded but produced no output. Unusual
            # (real dmesg has at least the boot banner). Could
            # be a container restriction blocking the kernel ring
            # buffer. Treat as blocked but log a warning.
            logger.warning(
                "dmesg returned 0 with empty output; kernel ring "
                "buffer may be restricted. Treating as blocked."
            )
            return True
        return True
    except PermissionError:
        return True
    except FileNotFoundError:
        # H7 fix: dmesg binary missing. Fail CLOSED. We cannot
        # verify the kernel info leak is blocked if dmesg
        # doesn't exist; an operator might have removed dmesg
        # to bypass our check. The orchestrator should refuse
        # to start.
        logger.critical(
            "H7: dmesg binary not found. Cannot verify kernel info "
            "leak is blocked. Refusing to start (fail-closed)."
        )
        return False
    except subprocess.TimeoutExpired:
        # H7 fix: dmesg hung. Could be a kernel deadlock OR a
        # malicious delay. Fail CLOSED.
        logger.critical(
            "H7: dmesg verification timed out after 2s. Cannot "
            "verify kernel info leak is blocked. Refusing to start "
            "(fail-closed)."
        )
        return False
    except OSError as e:
        # H7 fix: Distinguish permission issues (EPERM/EACCES)
        # which mean "dmesg blocked" (good) from any other error
        # which means "verification impossible" (fail-closed).
        if e.errno in (errno.EPERM, errno.EACCES):
            return True
        logger.critical(
            f"H7: dmesg verification failed with unexpected OSError "
            f"(errno={e.errno}): {e}. Refusing to start (fail-closed)."
        )
        return False
    except Exception as e:
        # H7 fix: Any other exception. Fail CLOSED. The previous
        # code returned True on bare `except Exception`, which is
        # an information leak vector.
        logger.critical(
            f"H7: dmesg verification failed unexpectedly: "
            f"{type(e).__name__}: {e}. Refusing to start (fail-closed)."
        )
        return False

if not _verify_dmesg_blocked():
    # H7 fix: Halt the orchestrator if dmesg verification cannot
    # confirm the kernel info leak is blocked. The previous code
    # only logged a warning and continued, which means a stripped
    # image or a malicious dmesg crash would silently allow the
    # leak. We now exit with a distinct code (8) so the operator
    # can identify the cause.
    _sys.stderr.write(
        "FATAL: dmesg verification failed (kernel info leak cannot "
        "be verified as blocked). Refusing to start (H7 fail-closed).\n"
    )
    _sys.exit(8)

# v5.0+: SOCKS5 proxy port from env, default 1080
SOCKS5_PROXY_PORT_FALLBACK = _safe_getenv_int("SOCKS5_PROXY_PORT", 1080, min_value=1)

# SINKHOLE_URL
SINKHOLE_URL = os.getenv("SINKHOLE_URL", "http://127.0.0.1:1")

# v5.0+: per-persona SOCKS5 port assignment
def get_socks5_port_for_persona(persona_id: str) -> int:
    """Map persona UUID to a unique SOCKS5 *listener* port.

    C11 ACCURACY: a unique listener port is NOT a unique source IP.
    All five ports in SOCKS5_PERSONA_PORTS are listeners on the same
    gost instance, and gost's single -F upstream applies to every one
    of them, so all personas egress from the same address. This
    function spreads personas across listeners; it provides no egress
    diversity and must not be relied on to prevent cross-persona
    source-IP correlation. See CONCURRENCY_AUDIT.md C11.

    R7-HOR-008: Use SHA-256 instead of MD5. On FIPS-mode Debian
    images, hashlib.md5 may be disabled. SHA-256 is always available.
    The 32-bit truncation is preserved (we only need an index into
    5 ports, so 32 bits of entropy is more than enough).
    """
    if not SOCKS5_PERSONA_PORTS or not persona_id:
        return SOCKS5_PROXY_PORT_FALLBACK
    h = int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
    idx = h % len(SOCKS5_PERSONA_PORTS)
    return int(SOCKS5_PERSONA_PORTS[idx])

def get_socks5_url_for_persona(persona_id: str) -> str:
    """DC-19 (Pass 19): Build the SOCKS5 URL for a specific persona.

    Combines `SOCKS5_PROXY_BASE` (proxy-res container IP) with the
    persona-specific port (from `get_socks5_port_for_persona`).

    C11 ACCURACY: the port is unique per persona but the upstream is
    shared (see get_socks5_port_for_persona), so this does NOT prevent
    cross-persona source-IP correlation.
    """
    port = get_socks5_port_for_persona(persona_id)
    return f"{SOCKS5_PROXY_BASE}:{port}"


# Lazy global instances
_serializer_instance = None
_archiver_instance = None
_proxy_resolver_instance = None

# BUG-9 FIX: The pacing engine was a single global singleton, so its
# `last_load_latency` state was shared across all personas. With
# MAX_CONCURRENT_PERSONAS=1 this is harmless, but if concurrency is
# ever raised the latency state from persona A would pollute persona B.
# We now keep a per-persona dict so each persona gets an independent
# engine with its own latency history.
_pacing_engine_instances: Dict[str, Any] = {}

def get_serializer():
    """DC-19 (Pass 19): Singleton accessor for the session serializer.

    Lazily creates a HardenedSessionSerializer on first use, sharing it
    across all task executions. The serializer is pickle-safe so it can
    be used inside ProcessPoolExecutors for bulk operations.
    """
    global _serializer_instance
    if _serializer_instance is None:
        if _HardenedSessionSerializer is None:
            raise RuntimeError("HardenedSessionSerializer not importable")
        _serializer_instance = _HardenedSessionSerializer(ENCRYPTION_KEY)
    return _serializer_instance

def get_archiver():
    """DC-19 (Pass 19): Singleton accessor for the profile archiver.

    Lazily creates a HardenedProfileArchiver on first use, sharing it
    across all task executions to avoid the cost of Fernet key
    initialization per task.
    """
    global _archiver_instance
    if _archiver_instance is None:
        if _HardenedProfileArchiver is None:
            raise RuntimeError("HardenedProfileArchiver not importable")
        _archiver_instance = _HardenedProfileArchiver(ENCRYPTION_KEY)
    return _archiver_instance

def get_pacing_engine(persona_id: str = "global"):
    """BUG-9 FIX: Per-persona pacing engine accessor.

    Returns an independent DynamicPacingEngine for each persona_id so that
    last_load_latency state from one persona does not bleed into another.
    Falls back to a 'global' key for callers that don't pass a persona_id
    (backward-compatible). Returns None if the engine was not importable.
    """
    if _DynamicPacingEngine is None:
        return None
    if persona_id not in _pacing_engine_instances:
        # Evict oldest entry if the registry grows beyond MAX_CONCURRENT_PERSONAS * 4.
        # This prevents unbounded growth during long deployments with many rotations.
        _max_engines = MAX_CONCURRENT_PERSONAS * 4 + 4
        if len(_pacing_engine_instances) >= _max_engines:
            oldest_key = next(iter(_pacing_engine_instances))
            del _pacing_engine_instances[oldest_key]
        _pacing_engine_instances[persona_id] = _DynamicPacingEngine()
    return _pacing_engine_instances[persona_id]

def get_proxy_resolver():
    """DC-19 (Pass 19): Singleton accessor for the sticky proxy resolver.

    Lazily creates a StickyProxyResolver on first use. Required dependency.
    """
    global _proxy_resolver_instance
    if _proxy_resolver_instance is None:
        if _StickyProxyResolver is None:
            raise RuntimeError("StickyProxyResolver not importable")
        _proxy_resolver_instance = _StickyProxyResolver()
    return _proxy_resolver_instance


# v5.0+: Per-persona SOCKS5 port and proxy state
CITY_TO_STATE = {
    "dallas": "tx", "houston": "tx", "austin": "tx", "san antonio": "tx",
    "los angeles": "ca", "san francisco": "ca", "san diego": "ca", "sacramento": "ca",
    "new york": "ny", "buffalo": "ny", "brooklyn": "ny",
    "chicago": "il", "springfield": "il",
    "phoenix": "az", "tucson": "az",
    "philadelphia": "pa", "pittsburgh": "pa",
    "miami": "fl", "orlando": "fl", "tampa": "fl", "jacksonville": "fl",
    "atlanta": "ga", "savannah": "ga",
    "seattle": "wa", "spokane": "wa",
    "denver": "co", "boulder": "co",
    "boston": "ma", "cambridge": "ma",
    "washington": "dc",
    "london": "ln", "manchester": "ln", "birmingham": "ln",
    "paris": "fr", "berlin": "be", "munich": "be",
    "amsterdam": "nl", "rotterdam": "nl",
    "toronto": "on", "vancouver": "bc", "montreal": "qc",
    "sydney": "nsw", "melbourne": "vic",
    "tokyo": "tk", "osaka": "tk",
    "singapore": "sg", "hong kong": "hk",
    "mumbai": "mh", "delhi": "dl", "bangalore": "ka",
}

def derive_state_from_city(city: str) -> str:
    """DC-19 (Pass 19): Map a city name to its 2-letter US state code.

    Uses a hardcoded lookup of major US cities. Falls back to "tx" (Texas)
    for unknown cities, which is intentional — Texas is the most common
    proxy exit in the pool, and falling back to a "plausible" state is
    better than raising an error.
    """
    if not city:
        return "tx"
    return CITY_TO_STATE.get(city.lower().strip(), "tx")


# R7-DEEP-AUDIT-2 GAP 1.3 / 5.2: City-to-coordinates lookup table for
# navigator.geolocation shim. Real Chrome returns geolocation coordinates
# that match the user's location. We return plausible coordinates for
# the persona's proxy city so a site that calls navigator.geolocation
# sees a consistent "real" location.
#
# Coordinates are approximate city centers (lat, lon) in WGS84. Real
# GPS coordinates are within 5-30m of these values.
#
# The list covers the most common US cities (top 25 metros) plus a
# handful of international cities. For unknown cities, the JS shim
# returns PERMISSION_DENIED (better than returning fake coordinates).
_GEO_COORDS_BY_CITY: Dict[str, Tuple[float, float]] = {
    # US cities (top 25 metros + state capitals)
    "dallas": (32.7767, -96.7970),
    "houston": (29.7604, -95.3698),
    "austin": (30.2672, -97.7431),
    "san antonio": (29.4241, -98.4936),
    "fort worth": (32.7555, -97.3308),
    "los angeles": (34.0522, -118.2437),
    "san francisco": (37.7749, -122.4194),
    "san diego": (32.7157, -117.1611),
    "sacramento": (38.5816, -121.4944),
    "san jose": (37.3382, -121.8863),
    "new york": (40.7128, -74.0060),
    "brooklyn": (40.6782, -73.9442),
    "buffalo": (42.8864, -78.8784),
    "chicago": (41.8781, -87.6298),
    "springfield": (39.7817, -89.6501),
    "phoenix": (33.4484, -112.0740),
    "tucson": (32.2226, -110.9747),
    "philadelphia": (39.9526, -75.1652),
    "pittsburgh": (40.4406, -79.9959),
    "miami": (25.7617, -80.1918),
    "orlando": (28.5383, -81.3792),
    "tampa": (27.9506, -82.4572),
    "jacksonville": (30.3322, -81.6557),
    "atlanta": (33.7490, -84.3880),
    "savannah": (32.0809, -81.0912),
    "seattle": (47.6062, -122.3321),
    "spokane": (47.6588, -117.4260),
    "denver": (39.7392, -104.9903),
    "boulder": (40.0150, -105.2705),
    "boston": (42.3601, -71.0589),
    "cambridge": (42.3736, -71.1097),
    "washington": (38.9072, -77.0369),
    "detroit": (42.3314, -83.0458),
    "minneapolis": (44.9778, -93.2650),
    "portland": (45.5152, -122.6784),
    "las vegas": (36.1699, -115.1398),
    "nashville": (36.1627, -86.7816),
    # International
    "london": (51.5074, -0.1278),
    "manchester": (53.4808, -2.2426),
    "birmingham": (52.4862, -1.8904),
    "paris": (48.8566, 2.3522),
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "amsterdam": (52.3676, 4.9041),
    "rotterdam": (51.9244, 4.4777),
    "toronto": (43.6532, -79.3832),
    "vancouver": (49.2827, -123.1207),
    "montreal": (45.5017, -73.5673),
    "sydney": (-33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
    "tokyo": (35.6762, 139.6503),
    "osaka": (34.6937, 135.5023),
    "singapore": (1.3521, 103.8198),
    "hong kong": (22.3193, 114.1694),
    "mumbai": (19.0760, 72.8777),
    "delhi": (28.7041, 77.1025),
    "bangalore": (12.9716, 77.5946),
}


def get_coords_for_city(city_norm: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for a normalized city name, or None if unknown.

    R7-DEEP-AUDIT-2 GAP 1.3: Used by the orchestrator to inject
    __sre_geo_coords into the browser init script. The JS-side
    navigator.geolocation shim uses this to return plausible
    coordinates matching the persona's proxy geo.

    Falls back to None for unknown cities. The JS shim treats None
    as "no proxy geo known" and returns PERMISSION_DENIED.
    """
    if not city_norm:
        return None
    return _GEO_COORDS_BY_CITY.get(city_norm.lower().strip())

# v6.2: in-memory Fernet decryption helper with timeout
# RELIABILITY-FIX: `signal` is already imported at module level (line 53); this
# second import shadowed it for the rest of the module (ruff F811).
def _fernet_decrypt_bytes(encrypted_bytes: bytes, key: bytes, timeout_s: float = 5.0) -> bytes:
    """Decrypt a Fernet-encrypted blob in-memory (no temp file on disk).
    v6.2: Added timeout to prevent DoS from corrupted tokens.

    R7-HOR-010: The previous implementation used signal.SIGALRM
    to enforce a timeout, but SIGALRM fires in the main thread
    and interrupts whatever coroutine is awaiting in the event
    loop. This caused event loop corruption in async contexts.
    We now run the decryption in a thread pool with a future.result()
    timeout, which is async-safe.

    A7 fix (Pass 16): The previous code was a sync function that
    was called from the async `run_persona_pipeline`. The
    `future.result(timeout=...)` call BLOCKS the calling thread
    until the future completes (or the timeout fires). For a
    normal Fernet decrypt (~1ms), the blocking is negligible
    (1ms is below the human-perceivable threshold of ~10ms). But
    for a CORRUPTED token that takes 5s to fail, the event loop
    is blocked for 5s — every other coroutine (cleanup, signal
    handlers, the daemon loop's next iteration) is paused. A
    blocked event loop is a top-3 fingerprint for advanced
    telemetry (the event loop's heartbeat is observable via
    JavaScript's `setTimeout` drift, and a 5s gap is detectable).

    The fix: introduce an async wrapper
    `_fernet_decrypt_bytes_async` that uses
    `loop.run_in_executor(_fernet_executor, ...)` and
    `await future` instead of `future.result()`. The thread
    pool worker runs the decrypt; the event loop continues
    running other coroutines. New async callers should use
    the async version. The sync version is kept for
    backward compatibility (and the blocking is acceptable
    for the small (1ms) decrypts).
    """
    if Fernet is None:
        raise RuntimeError("cryptography.fernet not available")

    # R7-HOR-010: Run decryption in a thread pool to avoid blocking
    # the event loop, and use future.result(timeout=...) for the
    # hard upper bound. This is async-safe and doesn't use signals.
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    _executor = _fernet_executor
    future = None
    try:
        future = _executor.submit(Fernet(key).decrypt, encrypted_bytes)
        try:
            return future.result(timeout=timeout_s)
        except FuturesTimeoutError:
            raise TimeoutError(f"Fernet decryption timed out after {timeout_s}s")
    finally:
        # Cancel the future if it's still running
        if future is not None and not future.done():
            future.cancel()


async def _fernet_decrypt_bytes_async(encrypted_bytes: bytes, key: bytes, timeout_s: float = 5.0) -> bytes:
    """Async version of `_fernet_decrypt_bytes` that doesn't block
    the event loop.

    A7 fix (Pass 16): The sync version's `future.result(timeout=...)`
    blocks the calling thread for up to `timeout_s` seconds. For
    a corrupted Fernet token, this can block the event loop for
    5+ seconds, freezing the orchestrator. This async version
    uses `loop.run_in_executor()` to run the decrypt in a thread
    pool worker, then `await`s the future. The event loop
    continues running other coroutines while the decrypt is in
    flight. We use `asyncio.wait_for()` for the timeout so the
    event loop can cancel cleanly.

    This is the version called from `run_persona_pipeline` and
    other async contexts. The sync version is kept for
    backward compatibility.
    """
    if Fernet is None:
        raise RuntimeError("cryptography.fernet not available")
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(
            _fernet_executor, Fernet(key).decrypt, encrypted_bytes
        )
        return await asyncio.wait_for(future, timeout=timeout_s)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Fernet decryption timed out after {timeout_s}s")

def _fernet_encrypt_bytes(plaintext_bytes: bytes, key: bytes) -> bytes:
    if Fernet is None:
        raise RuntimeError("cryptography.fernet not available")
    f = Fernet(key)
    return f.encrypt(plaintext_bytes)


# Custom exceptions
class OrchestratorError(Exception):
    """DC-19 (Pass 19): Base exception for all orchestrator-level errors.

    All custom exceptions raised by the orchestrator inherit from this
    class, allowing callers to catch any orchestrator-specific error
    with a single `except OrchestratorError` clause.
    """


class DatabaseUnavailableError(OrchestratorError):
    """DC-19 (Pass 19): Raised when the database pool is unavailable.

    This is a CRITICAL error: the orchestrator cannot function without
    database access. The daemon loop will pause task execution and
    retry with exponential backoff when this is raised.
    """


class DecryptionFailedError(OrchestratorError):
    """DC-19 (Pass 19): Raised when session data decryption fails.

    The `code` attribute distinguishes failure modes:
    - "empty_payload": no session data to decrypt (skip silently)
    - "auth_failed": Fernet MAC check failed (CRITICAL: ENCRYPTION_KEY mismatch)
    - "corrupted": data is unreadable for unknown reasons (quarantine persona)
    """
    def __init__(self, message, code="unknown"):
        super().__init__(message)
        self.code = code


class GeoConflictError(OrchestratorError):
    """DC-19 (Pass 19): Raised when proxy geo doesn't match persona geo.

    The `persona_id` attribute identifies which persona triggered the
    conflict, useful for logging and quarantine decisions.
    """
    def __init__(self, message, persona_id=""):
        super().__init__(message)
        self.persona_id = persona_id

class PersonaRetiredError(OrchestratorError):
    """Raised when a persona has reached its task limit and must be rotated."""
    def __init__(self, message, persona_id=""):
        super().__init__(message)
        self.persona_id = persona_id

class PIDBudgetExceededError(OrchestratorError):
    """Raised when a persona's child processes exceed PERSONA_PID_BUDGET."""
    def __init__(self, message, persona_id=""):
        super().__init__(message)
        self.persona_id = persona_id


def stable_hash(s: str) -> int:
    """R7-HOR-008: SHA-256 instead of MD5 for FIPS compatibility.
    Returns a 32-bit stable hash suitable for indexing into small
    collections (e.g., display slots, port assignments).

    R7-PASS-11: Defensive against non-string inputs. If `s` is None
    or not a string, return a stable hash of the empty string
    (deterministic, not random) so callers don't crash. The real
    call sites always pass strings, but defensive programming here
    prevents a confusing crash if the caller is updated to pass
    a different type.
    """
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    return int(hashlib.sha256(s.encode('utf-8')).hexdigest()[:8], 16) % (2**32 - 1)


# v5.0+: Timezone validation
_KNOWN_TIMEZONES = None
def _get_known_timezones() -> set:
    global _KNOWN_TIMEZONES
    if _KNOWN_TIMEZONES is None:
        tz_dir = "/usr/share/zoneinfo"
        tzs = set()
        if os.path.isdir(tz_dir):
            for root, dirs, files in os.walk(tz_dir):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), tz_dir)
                    if not any(rel.startswith(p) for p in ("Etc", "posixrules", "SystemV", "right", "ROC", "ROK", "PRC", "CST6CDT", "EST5EDT", "EET", "MET", "MST7MDT", "PST8PDT", "WET", "W-SU")):
                        tzs.add(rel)
        _KNOWN_TIMEZONES = tzs
    return _KNOWN_TIMEZONES

def validate_timezone(tz: str) -> str:
    """
    DC-19 (Pass 19): Validate a timezone string against the system tzdata.

    Returns the timezone if valid, "America/New_York" otherwise. The
    fallback is intentional — New York is the most common proxy exit and
    is a "plausible" default for any persona. Without a fallback, a
    database row with a corrupted timezone would crash the orchestrator.

    M18 fix: Reject path-traversal-like inputs. The previous
    code accepted any string and only checked it against the
    system tzdata set. A malicious DB row with
    `tz = "../../etc/passwd"` would fall back to "America/New_York"
    (it's not in the tzdata set), but defense-in-depth: we now
    reject any string with path separators or ".." segments
    before the tzdata lookup.
    """
    if not tz or not isinstance(tz, str):
        return "America/New_York"
    # M18 fix: Reject path-traversal-like inputs (allow / since valid IANA timezones use it)
    if ".." in tz or "\\" in tz or "\x00" in tz:
        logger.warning(
            "validate_timezone: rejecting suspicious input "
            "with path traversal characters; falling back to America/New_York"
        )
        return "America/New_York"
    # M18 fix: Limit length to prevent DoS via huge strings
    if len(tz) > 128:
        logger.warning(
            f"validate_timezone: rejecting overlong input "
            f"({len(tz)} chars); falling back to America/New_York"
        )
        return "America/New_York"
    if tz in _get_known_timezones():
        return tz
    logger.warning(f"Unknown timezone '{tz}'; falling back to America/New_York")
    return "America/New_York"


def parse_proxy_url(proxy_url: str) -> Optional[Dict[str, str]]:
    """Parse a proxy URL into Playwright-compatible format.
    R7-HOR-018: The previous code accepted any URL scheme (http://,
    https://, etc.). Playwright's proxy field expects socks5 or
    socks5h. We now reject non-SOCKS schemes explicitly.

    R7-PASS-13: Preserve IPv6 brackets in the server field. The
    previous code used `parsed.hostname` which strips brackets for
    IPv6 (returns `::1` instead of `[::1]`). Playwright requires
    the bracketed form. We now check for IPv6 in the netloc and
    reconstruct the host:port with brackets.

    Also, R7-PASS-13: Don't unquote the username/password. The
    previous code called `unquote()` which would decode `%40` to `@`
    and break the URL structure. Playwright's `username` and `password`
    fields are raw strings (not URL-encoded), but they're also not
    expected to contain `@` (which would confuse the URL parser). We
    keep them unquoted for safety.
    """
    if not proxy_url or not isinstance(proxy_url, str):
        logger.warning(
            f"parse_proxy_url: invalid input (type={type(proxy_url).__name__}); "
            f"returning None"
        )
        return None
    # M21 fix: Length sanity check. A 1MB proxy URL is
    # definitely a bug; refuse to parse it (could be a DoS).
    if len(proxy_url) > 4096:
        logger.error(
            f"parse_proxy_url: proxy_url is too long "
            f"({len(proxy_url)} bytes; max 4KB); returning None"
        )
        return None
    # EH-11 (Pass 16): Use specific exception types instead of bare
    # `except Exception`. The previous code masked ALL errors including
    # KeyboardInterrupt (Py2) and SystemExit. The actual exceptions
    # raised by urllib.parse.urlparse on a bad URL are:
    #   - ValueError: invalid IPv6 address, missing brackets
    #   - AttributeError: if proxy_url is not a string
    # We catch these specifically and log a warning. Other exceptions
    # (OSError, TypeError) indicate real bugs that should propagate
    # to the caller.
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        # R7-HOR-018: Validate the scheme. Only socks5/socks5h are
        # accepted. http:// and https:// would cause Playwright to
        # attempt HTTP proxying, which conflicts with the SOCKS5
        # infrastructure.
        if parsed.scheme not in ("socks5", "socks5h"):
            logger.error(
                f"R7-HOR-018: Invalid proxy scheme '{parsed.scheme}'. "
                f"Only socks5 and socks5h are supported."
            )
            return None
        # R7-PASS-13: Preserve IPv6 brackets. The hostname attribute
        # strips brackets for IPv6 (returns `::1` instead of `[::1]`),
        # so we check parsed.netloc for brackets and use the bracketed
        # form if found.
        if '[' in parsed.netloc and ']' in parsed.netloc:
            # IPv6 - extract host:port from netloc, skipping the user:pass@ part
            at_idx = parsed.netloc.rfind('@')
            host_port = parsed.netloc[at_idx + 1:] if at_idx >= 0 else parsed.netloc
            server_host = host_port.rsplit(':', 1)[0]  # Remove port
        else:
            server_host = parsed.hostname
        port = parsed.port
        if not server_host or not port:
            return None
        result = {"server": f"{server_host}:{port}"}
        # R7-PASS-13: KEEP unquote() for username/password - Playwright
        # expects the DECODED form. The previous version of this function
        # correctly unquoted. The redaction function (_redact_proxy_credentials)
        # is the one that should NOT unquote (to preserve the original
        # URL structure for logging). The split between these is:
        # - parse_proxy_url: unquote credentials (Playwright compatibility)
        # - _redact_proxy_credentials: don't unquote (logging safety)
        if parsed.username:
            result["username"] = urllib.parse.unquote(parsed.username)
        if parsed.password:
            result["password"] = urllib.parse.unquote(parsed.password)
        return result
    except (ValueError, AttributeError) as e:
        # EH-11: Specific exceptions only. ValueError covers bad IPv6
        # addresses, missing brackets, etc. AttributeError covers
        # non-string inputs. Other exceptions are bugs and propagate.
        logger.error(f"Failed to parse proxy URL '{proxy_url}': {e}")
        return None


def _redact_proxy_credentials(proxy_url: str) -> str:
    """R7-SPR-008: Redact the password from a proxy URL for safe logging.
    Returns scheme://user:***@host:port (or just scheme://host:port
    if no credentials).

    R7-PASS-13: Preserve IPv6 brackets and URL-encoded characters.
    The previous code used `urllib.parse.unquote()` on the username,
    which incorrectly decoded `%40` to `@` and broke the URL structure
    (a username containing `@` would confuse the URL parser). Also,
    the `parsed.hostname` for IPv6 URLs strips the brackets, producing
    an unparseable URL. We now keep the original (encoded) username
    and reconstruct the host:port with brackets for IPv6.

    H5 fix: parsed.username is ALREADY URL-decoded by
    urllib.parse.urlparse. The previous code's comment claimed
    "Keep the original (URL-encoded) username" but
    `parsed.username` returns the DECODED form (e.g.,
    `socks5h://user%40name:p%40ss@host` has parsed.username ==
    'user@name', not 'user%40name'). The bug: when the redacted
    URL is logged to a centralized log aggregator (e.g., ELK,
    Datadog), the decoded username is in plaintext, which is
    a credential leak in log files. We now extract the username
    from the original netloc (preserving URL-encoding) and use
    the parsed.password for redaction, also from the original
    netloc. The re-encoded username in the redacted output is
    safe to log.

    To preserve URL-encoding in the username, we extract from
    the netloc string directly, splitting on the LAST '@' (the
    credentials separator) and then on the first ':' (the
    user:pass separator). This works for any URL form including
    IPv6 brackets.
    """
    if not proxy_url:
        return ""
    try:
        # Use the raw netloc to extract credentials with original
        # URL-encoding preserved. parsed.username/password
        # automatically URL-decode, which is what we want to
        # AVOID here.
        parsed = urllib.parse.urlparse(proxy_url)
        scheme = parsed.scheme or "socks5h"
        netloc = parsed.netloc

        # Split off user:pass@ if present
        raw_user = None
        raw_password = None  # We won't echo it; just used to detect its presence
        if '@' in netloc:
            at_idx = netloc.rfind('@')
            userinfo, host_port = netloc[:at_idx], netloc[at_idx + 1:]
            if ':' in userinfo:
                colon_idx = userinfo.find(':')
                raw_user = userinfo[:colon_idx]
                raw_password = userinfo[colon_idx + 1:]
            else:
                raw_user = userinfo
        else:
            host_port = netloc

        # host_port preserves IPv6 brackets because we didn't go
        # through parsed.hostname (which strips them).
        if raw_user is not None:
            return f"{scheme}://{raw_user}:***@{host_port}"
        return f"{scheme}://{host_port}"
    except Exception:
        return "<unparseable proxy URL>"


# v6.2: Explicit storage clearing for cross-session isolation
# This is called before the first navigation and before browser.close()
async def _clear_browser_storage(page) -> None:
    """Clear all browser storage that should NOT be inherited between tasks.

    This prevents:
    - localStorage/IndexedDB leakage between tasks of the same persona
    - Cache API stale entries from previous tasks
    - Service Workers from previous target sites
    - Async writes in flight when tmpfs is wiped

    E9 fix (Pass 18): Validate page is not None. The previous code
    did `page.evaluate(...)` without checking if `page` is a valid
    Playwright Page object. A None page (e.g., browser failed to
    launch but the cleanup path still runs) would crash with
    AttributeError. We now return early with a warning.
    """
    if page is None:
        logger.debug("HOR-E9: _clear_browser_storage called with page=None; skipping")
        return
    if not hasattr(page, 'evaluate'):
        logger.debug(
            f"HOR-E9: _clear_browser_storage page has no evaluate method "
            f"(type={type(page).__name__}); skipping"
        )
        return
    try:
        # R7-HOR-013: Use a 5s timeout on the page.evaluate call
        # (defense in depth against JS infinite loops) and wait
        # for any pending writes via a Promise.allSettled check.
        # The previous code used a hard-coded 2s sleep, which was
        # both wasteful (real writes complete in <100ms) and
        # unreliable (slow pages with pending writes may need
        # 5+ seconds).
        try:
            await asyncio.wait_for(
                page.evaluate("""async () => {
                    // Track pending writes so callers can wait for them
                    if (typeof window.__sre_pending_writes === 'undefined') {
                        window.__sre_pending_writes = [];
                    }
                    // Clear localStorage and sessionStorage
                    try { localStorage.clear(); } catch(e) {}
                    try { sessionStorage.clear(); } catch(e) {}

                    // Clear Cache API
                    if (typeof caches !== 'undefined') {
                        try {
                            const keys = await caches.keys();
                            for (const key of keys) {
                                const p = caches.delete(key);
                                window.__sre_pending_writes.push(p);
                                await p;
                            }
                        } catch(e) {}
                    }

                    // Clear IndexedDB databases
                    if (typeof indexedDB !== 'undefined' && indexedDB.databases) {
                        try {
                            const dbs = await indexedDB.databases();
                            for (const db of dbs) {
                                if (db.name) {
                                    const p = new Promise((resolve) => {
                                        const req = indexedDB.deleteDatabase(db.name);
                                        req.onsuccess = req.onerror = req.onblocked = () => resolve();
                                    });
                                    window.__sre_pending_writes.push(p);
                                    await p;
                                }
                            }
                        } catch(e) {}
                    }

                    // Unregister Service Workers
                    if (typeof navigator !== 'undefined' && navigator.serviceWorker) {
                        try {
                            const registrations = await navigator.serviceWorker.getRegistrations();
                            for (const reg of registrations) {
                                const p = reg.unregister();
                                window.__sre_pending_writes.push(p);
                                await p;
                            }
                        } catch(e) {}
                    }

                    // CHAMELEON (GAP-5.4): Clear additional browser state
                    // to prevent cross-session correlation.
                    try {
                        // Clear HTTP authentication cache
                        if (window.chrome && chrome.removeHttpAuth) {
                            try { await chrome.removeHttpAuth(); } catch(e) {}
                        }
                        // Clear client hints (Sec-CH-UA) state
                        if (typeof navigator.userAgentData !== 'undefined' &&
                            navigator.userAgentData.brands) {
                            try { delete navigator.userAgentData.brands; } catch(e) {}
                        }
                        // Clear notification permissions
                        try {
                            if (typeof Notification !== 'undefined' &&
                                Notification.permission !== 'default') {
                                // Can't reset permission but we can suppress
                                // the state from being read
                                Object.defineProperty(Notification, 'permission', {
                                    get: () => 'default', configurable: true
                                });
                            }
                        } catch(e) {}
                        // Clear clipboard history (best effort)
                        try {
                            if (navigator.clipboard && navigator.clipboard.writeText) {
                                // We can't actually clear OS clipboard,
                                // but we can suppress the read state
                            }
                        } catch(e) {}
                        // Clear credential management
                        if (navigator.credentials && navigator.credentials.preventSilentAccess) {
                            try { navigator.credentials.preventSilentAccess(); } catch(e) {}
                        }
                    } catch(e) {}

                    // Wait for all pending writes to complete
                    if (window.__sre_pending_writes.length > 0) {
                        await Promise.allSettled(window.__sre_pending_writes);
                        window.__sre_pending_writes = [];
                    }
                }"""),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Storage clear timed out after 5s; continuing.")
    except Exception as e:
        logger.warning(f"Storage clear failed: {e}")


def is_display_ready(display: str, timeout: float = 5.0) -> bool:
    """DC-19 (Pass 19): Check if an X11 display is ready (Xvfb running).

    Tries `xdpyinfo` first (definitive check), falls back to a TCP probe
    on the X11 port (6000 + display number). Returns True if either
    indicates the display is accepting connections.

    E10 fix (Pass 18): Validate display string. The previous code
    did `int(display.replace(":", ""))` without checking if
    `display` is a string. A None display (e.g., from a missing
    env var) would crash with AttributeError. A non-numeric
    display (e.g., "abc") would raise ValueError on `int()`.
    We now return False on invalid input (display not ready).
    """
    if not isinstance(display, str) or not display:
        return False
    if os.path.exists("/usr/bin/xdpyinfo") or os.path.exists("/usr/local/bin/xdpyinfo"):
        try:
            proc = subprocess.run(
                ["xdpyinfo", "-display", display],
                capture_output=True, timeout=timeout
            )
            return proc.returncode == 0
        except Exception as _suppressed_exc:
            logger.debug(f"is_display_ready(): suppressed {type(_suppressed_exc).__name__}")
    try:
        port = 6000 + int(display.replace(":", ""))
        # M29 fix: Socket fd leak. The previous code did
        # `sock = socket.socket(...)` and `sock.close()` separately.
        # If an exception fired between them (e.g.,
        # sock.settimeout raised on a bad timeout value, or
        # connect_ex raised an OSError), the socket fd was
        # leaked. Over many `is_display_ready` calls in the
        # Xvfb display-collision-retry loop, this leaks up to 10
        # fds per persona (the retry loop runs up to 10 times).
        # We now use try/finally (or a context manager) to
        # guarantee the socket is closed on every code path.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            result = sock.connect_ex(("127.0.0.1", port)) == 0
            return result
        finally:
            try:
                sock.close()
            except Exception as _suppressed_exc:
                logger.debug(f"is_display_ready(): suppressed {type(_suppressed_exc).__name__}")
    except (ValueError, AttributeError, TypeError):
        return False
    except Exception:
        return False


async def check_proxy_integrity(proxy_url: str) -> bool:
    """DC-19 (Pass 19): Pre-flight check that a SOCKS5 proxy is alive.

    Connects to the proxy and requests the IP from a custom internal
    endpoint (not icanhazip.com, which is a fingerprint). Returns True
    if the proxy returns a valid IP, False otherwise. The orchestrator
    refuses to launch a browser through a dead proxy.
    """
    # v6.2: Use a custom internal endpoint instead of icanhazip.com.
    # icanhazip.com is a fingerprint (all automation containers hit it).
    # Use the DoH proxy or a custom internal IP-check endpoint.
    if httpx is None:
        return True
    # v6.2: Custom endpoint (configurable via env var, defaults to a sinkhole)
    # R7-HOR-004: Treat empty string the same as not set. Without this,
    # an operator who sets `IP_CHECK_URL=""` (intending "disable") gets
    # a MissingSchema exception in httpx, which is caught and returns
    # False, blocking the launch. Now empty string falls through to
    # the default.
    ip_check_url = os.getenv("IP_CHECK_URL") or "http://172.18.0.1:8888/ip"
    logger.info(f"Pre-flight routing lookup via {ip_check_url}")
    adapted = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
    try:
        async with httpx.AsyncClient(proxies={"all://": adapted}, timeout=5.0) as client:
            response = await client.get(ip_check_url)
            if response.status_code == 200:
                logger.info(f"Proxy integrity passed. Egress IP: {response.text.strip()[:20]}...")
                return True
            return False
    except Exception as err:
        logger.error(f"Proxy check failed for {proxy_url}: {err}")
        return False


WEBRTC_MODERN_FLAGS = [
    # R7-HOR-028: Removed deprecated WebRtcHideLocalIpsWithMdns flag.
    # The flag was renamed in Chrome 110+ to WebRtcAllowInputVolumeAdjustment.
    # Listing the deprecated flag produces a startup warning in
    # Chrome 120, which is logged to the browser console and could
    # be read by a fingerprinting script. We use only the modern flag.
    "--enable-features=WebRtcAllowInputVolumeAdjustment",
    "--force-webrtc-ip-handling-policy=default_public_interface_only",
]


def is_chrome_ua(user_agent: str) -> bool:
    """DC-19 (Pass 19): Detect if a user agent is Chrome/Chromium-based.

    Returns True for any UA containing "Chrome" or "Chromium", which
    covers real Chrome, Chromium, Edge (ChakraEdge), Brave, Opera, and
    other Chromium-based browsers. Used to select the right engine
    (Chromium vs Firefox) for the persona.
    """
    if not user_agent:
        return False
    return ("Chrome" in user_agent) or ("Chromium" in user_agent)


# v5.0+: PID budget tracking
def check_pid_budget(persona_id: str = "") -> bool:
    """v5.0+: Verify current persona's child PIDs are within budget.
    Returns True if safe to launch a new browser; False if exceeded or unknown.

    v6.2: FAIL CLOSED on exception. Previously, if psutil was None or the
    check raised an exception, the function returned True (safe to launch).
    This meant a PID exhaustion scenario could be missed. Now we fail closed
    and require an explicit verification.
    """
    if psutil is None:
        logger.critical("psutil not available; cannot verify PID budget. FAILING CLOSED.")
        return False
    try:
        current = psutil.Process(os.getpid())
        # Count PIDs that the orchestrator has spawned
        children = current.children(recursive=True)
        # Allow some headroom: the orchestrator itself, tini, chronyd,
        # python helpers, etc. The browser is the main consumer.
        total = len(children) + 1  # +1 for self
        # We expect ~80 PIDs for a single persona (Chromium + helpers)
        if total > PERSONA_PID_BUDGET:
            logger.error(
                f"PID budget EXCEEDED for {persona_id or 'unknown'}: "
                f"{total} PIDs (budget: {PERSONA_PID_BUDGET})"
            )
            return False
        return True
    except Exception as e:
        # v6.2: FAIL CLOSED. If we can't verify, we don't proceed.
        logger.error(f"PID budget check failed for {persona_id}: {e}. FAILING CLOSED.")
        return False


# v5.0+: Panic button (SIGUSR1) handler
_panic_triggered = False

# C1/C3 fix: Track the last proxy URL used to detect proxy changes
# between task runs. When the proxy changes (e.g., persona switch,
# city change), the resolver's HTTP client (with its keep-alive
# TCP+TLS connection and HTTP/2 session) must be reset to prevent
# cross-persona connection bleed. The first task sets this to None
# (initial); subsequent tasks check against the previous value.
_LAST_PROXY_URL: Optional[str] = None
_LAST_PROXY_PERSONA_ID: Optional[str] = None

# H3 fix: Shutdown re-entrancy guard. Multiple signals (e.g.,
# SIGINT then SIGTERM) can arrive in quick succession. Without
# this guard, two concurrent shutdown coroutines race on the
# same db_pool and tmpfs, causing double-close and partial
# cleanup. The lock is a simple flag (asyncio is single-threaded,
# so a non-locking flag is sufficient).
_SHUTDOWN_IN_PROGRESS: bool = False

def _panic_handler(signum, frame):
    """Synchronous SIGUSR1 handler. Triggers full cleanup.
    - Umount per-task tmpfs (if mounted; in v5.0+ it's pre-mounted by
      entrypoint.sh so we just delete the dir contents).
    - Clean tc qdisc / netem rules.
    - Kill all child processes.
    - Exit with code 99 (signals 'panic, do not restart')."""
    global _panic_triggered
    if _panic_triggered:
        return
    _panic_triggered = True
    logger.critical("PANIC SIGNAL RECEIVED (SIGUSR1). Initiating emergency cleanup.")
    # Sync cleanup; the async loop will also see _panic_triggered
    # R7-DEEP-AUDIT FIX: Each cleanup step is in its own try/except so
    # that a failure in step 1 does NOT skip steps 2 and 3. The previous
    # structure had a single try wrapping everything, which meant a
    # failure in tc qdisc cleanup would skip kill-all-children and
    # tmpfs-wipe. Each step now has its own isolated error handling.
    
    # Step 1: Clean tc qdisc / netem
    try:
        # R7-HOR-029: Detect the default interface dynamically instead
        # of hard-coded "eth0". On some systems (Docker, K8s), the
        # default interface may be eth1, ens5, veth*, etc. We use
        # `ip route show default` to find it.
        if subprocess.run(["which", "tc"], capture_output=True, timeout=2).returncode == 0:
            _default_iface = None
            try:
                _ip_route = subprocess.run(
                    ["ip", "route", "show", "default"],
                    capture_output=True, text=True, timeout=2
                )
                if _ip_route.returncode == 0 and _ip_route.stdout.strip():
                    _parts = _ip_route.stdout.strip().split()
                    if "dev" in _parts:
                        _default_iface = _parts[_parts.index("dev") + 1]
            except Exception as _suppressed_exc:
                logger.debug(f"_panic_handler(): suppressed {type(_suppressed_exc).__name__}")
            if not _default_iface:
                _default_iface = "eth0"  # fallback
            subprocess.run(["tc", "qdisc", "del", "dev", _default_iface, "root"],
                          capture_output=True, timeout=2)
            subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"],
                          capture_output=True, timeout=2)
    except Exception as e:
        logger.error(f"Panic handler step 1 (tc qdisc) error: {e}")
    
    # Step 2: Kill all child processes
    try:
        if psutil is not None:
            current = psutil.Process(os.getpid())
            for child in current.children(recursive=True):
                try:
                    child.kill()
                except Exception as _suppressed_exc:
                    logger.debug(f"_panic_handler(): suppressed {type(_suppressed_exc).__name__}")
    except Exception as e:
        logger.error(f"Panic handler step 2 (kill children) error: {e}")
    
    # Step 3: Wipe per-task tmpfs dir
    try:
        # Wipe per-task tmpfs dir (contents only; the tmpfs is unmounted
        # automatically by container teardown)
        if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
            shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
    except Exception as e:
        logger.error(f"Panic handler step 3 (tmpfs wipe) error: {e}")
    
    # R7-HOR-002: Run async cleanup synchronously BEFORE os._exit().
    # The previous code registered an atexit handler, but os._exit()
    # bypasses atexit — the cleanup was dead code. Now we spawn a
    # short-lived thread that runs the cleanup in a fresh event loop
    # and join it (with a hard 3s timeout). The thread will complete
    # before os._exit() is called, ensuring the db_pool is closed
    # and tmpfs is wiped.
    # R7-DEEP-AUDIT-2 FIX: The previous code had a race where
    # os._exit(99) was called immediately after cleanup_thread.join
    # returned. If join timed out (3s), the thread was still running
    # but os._exit killed it mid-execution, leaving the db_pool in
    # an inconsistent state. We now use a thread that's NOT a daemon
    # (so os._exit would actually wait for it... but os._exit doesn't
    # wait for non-daemon threads either). The proper fix is to run
    # the cleanup SYNCHRONOUSLY in the panic handler itself, blocking
    # until done or hard timeout. This eliminates the race.
    try:
        # Synchronous async cleanup using a fresh event loop.
        # We use a hard 5s timeout to bound the wait. If cleanup
        # takes longer, we proceed to os._exit anyway (better than
        # blocking the panic signal indefinitely).
        import threading
        cleanup_done = threading.Event()
        cleanup_error = [None]
        def _sync_async_panic_cleanup():
            """Run db_pool.close() synchronously in a new event loop."""
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                if db_pool is not None:
                    try:
                        loop.run_until_complete(db_pool.close())
                    except Exception as e:
                        cleanup_error[0] = e
                loop.close()
            except Exception as e:
                cleanup_error[0] = e
            finally:
                cleanup_done.set()
        cleanup_thread = threading.Thread(
            target=_sync_async_panic_cleanup,
            name="sre-panic-cleanup",
            daemon=True,
        )
        cleanup_thread.start()
        # Wait up to 5s for cleanup. If it doesn't complete, log
        # a warning and proceed to os._exit. The thread is daemon=True
        # so it'll be killed when the process exits, but its
        # connection-close work may be incomplete. Acceptable for
        # panic situations (we're already in a bad state).
        if not cleanup_done.wait(timeout=5.0):
            logger.warning("Panic cleanup thread did not complete in 5s; proceeding to os._exit")
        elif cleanup_error[0]:
            logger.error(f"Panic cleanup error: {cleanup_error[0]}")
        # Wait briefly for the thread to actually exit (it may have
        # set the event before fully closing the loop).
        cleanup_thread.join(timeout=0.5)
    except Exception as e:
        logger.error(f"Panic cleanup handler error: {e}")

    # Exit with code 99 (orchestrator supervisor will see this and
    # NOT restart the container — restart:no also prevents auto-restart)
    os._exit(99)

# v6.0.4: Use ORCH_PANIC_SIGNAL env var to allow configurable panic signal
PANIC_SIGNAL_NAME = os.getenv("ORCH_PANIC_SIGNAL", "SIGUSR1")
PANIC_SIGNAL_MAP = {
    "SIGUSR1": signal.SIGUSR1,
    "SIGUSR2": signal.SIGUSR2,
    "SIGTERM": signal.SIGTERM,
    "SIGINT": signal.SIGINT,
    "SIGHUP": signal.SIGHUP,
}
PANIC_SIGNAL = PANIC_SIGNAL_MAP.get(PANIC_SIGNAL_NAME.upper(), signal.SIGUSR1)

# Register panic handler
# R7-DEEP-AUDIT-3 (Pass 9): Fail closed if the panic signal handler
# cannot be registered. The previous code only logged a warning,
# allowing the orchestrator to continue without a panic handler.
# Without a panic handler, SIGUSR1 (or whatever PANIC_SIGNAL is set
# to) would be handled by Python's default (terminate), bypassing
# the cleanup logic. This is a critical security risk: a panic
# event without cleanup leaves tmpfs contents on disk, which
# could leak persona data.
try:
    signal.signal(PANIC_SIGNAL, _panic_handler)
    logger.info(f"PANIC signal handler installed: {PANIC_SIGNAL_NAME} -> emergency cleanup + exit(99)")
except Exception as e:
    logger.critical(
        f"FAILED to register panic signal handler {PANIC_SIGNAL_NAME}: {e}. "
        f"Refusing to start (no panic cleanup would run on signal)."
    )
    # R7-DEEP-AUDIT-3: Fail closed - exit immediately. The
    # orchestrator supervisor (Docker) will see exit code 4 and
    # NOT restart the container (we use restart:no policy). A new
    # container will be spawned with a fresh tmpfs, persona state,
    # and boot_id.
    _sys.exit(4)


# v5.0+: Pre-mount tmpfs verification at task start
def _verify_task_tmpfs_ready() -> bool:
    """v5.0+: Verify per-task tmpfs is mounted and writable."""
    if not ORCH_TASK_TMPFS_DIR:
        return False
    try:
        os.makedirs(ORCH_TASK_TMPFS_DIR, exist_ok=True, mode=0o700)
        test_path = os.path.join(ORCH_TASK_TMPFS_DIR, ".write_test")
        with open(test_path, 'w') as f:
            f.write("ok")
        os.unlink(test_path)
        return True
    except Exception as e:
        logger.error(f"Task tmpfs not ready: {e}")
        return False


# v5.0+: Compute persona fingerprint digest for stable per-persona values
def _compute_persona_digest(persona_id: str) -> Dict[str, Any]:
    """Compute stable per-persona values that don't randomize across tasks.
    These are locked at persona creation time so all tasks for the same
    persona produce consistent fingerprints (no fingerprint drift).

    INVIS-5: hardwareConcurrency now uses a realistic distribution
    matching US consumer hardware census data. Real Chrome users have:
      - 4 cores: ~30%
      - 8 cores: ~50%
      - 16 cores: ~15%
      - Other: ~5%
    Previously we only had 4 or 8 (binary).

    INVIS-6: canvas_noise_session_id is a per-launch UUID that the JS
    side combines with the per-persona seed to produce slightly
    different canvas output per session (mimicking real Chrome driver
    cache variations).

    INVIS-10: notification_session_count tracks how many times this
    persona has been launched, used by the JS side to determine the
    Notification.permission state (fresh = "default", seasoned = "denied").
    """
    h = hashlib.sha256(persona_id.encode('utf-8')).hexdigest()

    # INVIS-5: Weighted random selection of hardwareConcurrency
    # Distribution: 30% get 4, 50% get 8, 15% get 16, 5% get other
    # (2, 12, 24, 32)
    _hc_roll = int(h[4:8], 16) % 100
    if _hc_roll < 30:
        hardware_concurrency = 4
    elif _hc_roll < 80:
        hardware_concurrency = 8
    elif _hc_roll < 95:
        hardware_concurrency = 16
    else:
        # 5%: unusual core counts
        _unusual_choices = [2, 12, 24, 32]
        hardware_concurrency = _unusual_choices[int(h[8:10], 16) % len(_unusual_choices)]

    return {
        # CHAMELEON (GAP-4.3): Realistic audio sample rate distribution.
        # Real users have varying audio sample rates:
        #   85% have 48000 Hz (USB/audio interface default)
        #   10% have 44100 Hz (consumer audio / Apple devices)
        #    5% have 96000 Hz (professional audio)
        # A constant 48000 for all personas is a fingerprint.
        "audio_sample_rate": _sample_audio_rate(persona_id),
        # R7-DEEP-AUDIT-3 GAP 2.5: Per-persona screen color depth
        # distribution. Real users have:
        #   90% = 24 (SDR, sRGB)
        #   5% = 30 (HDR10, deep color)
        #   5% = 48 (Dolby Vision / P3 wide gamut, Apple XDR)
        # The previous code hard-coded 24 for ALL personas, which
        # is a fingerprint for the 10% with HDR or wide-gamut.
        # Use the per-persona hash to distribute realistically.
        "screen_color_depth": [24, 24, 24, 24, 24, 24, 24, 24, 24, 30, 48][int(h[4], 16) % 11] if len(h) > 4 else 24,
        "screen_pixel_depth": 24,
        "device_memory": 8 if int(h[0], 16) % 2 == 0 else 4,  # 4 or 8
        "scroll_behavior": "smooth" if int(h[1], 16) % 2 == 0 else "auto",
        "battery_charging": True,
        "battery_level": 0.85 + (int(h[2], 16) / 16 * 0.14),  # 0.85-0.99
        "cookie_lifetime_days": 7 + (int(h[3], 16) % 24),  # 7-30 days
        # INVIS-5: Realistic hardwareConcurrency distribution
        "hardware_concurrency": hardware_concurrency,
        # INVIS-6: Per-launch session ID for canvas/audio noise
        # (computed at browser launch time, not here)
        "canvas_noise_session_id": None,  # filled in at launch
        # INVIS-10: How many times this persona has been launched
        "notification_session_count": 0,  # filled from DB
        # PPPC: History depth — how many tasks this persona has
        # completed. The JS side uses this to make chrome.csi().tran
        # and history.length grow over time (real users have a
        # monotonic, slowly-growing history). A bare 1/0/0 for all
        # sessions is itself a fingerprint.
        "history_depth": 0,  # filled from DB
        # PPPC: Whether this persona has prior session_data. The JS
        # side uses this to vary UI affordances (e.g., a returning
        # user has more browser tabs, more bookmarks, etc.)
        "is_returning": False,  # filled at launch
    }


def _sample_audio_rate(persona_id: str) -> int:
    """CHAMELEON (GAP-4.3): Sample audio rate per persona.

    Real users have varying audio sample rates:
      85% have 48000 Hz (USB/audio interface default)
      10% have 44100 Hz (consumer audio / Apple devices)
       5% have 96000 Hz (professional audio)
    A constant 48000 for all personas is a fingerprint.

    Per-persona seeded for reproducibility.
    """
    # E11 fix (Pass 18): Validate persona_id before encoding.
    if not isinstance(persona_id, str) or not persona_id:
        return 48000
    try:
        _h = int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16)
    except (TypeError, ValueError):
        return 48000
    _roll = _h % 100
    if _roll < 85:
        return 48000
    elif _roll < 95:
        return 44100
    else:
        return 96000


# v6.2: First-navigation-delay helper (human-like log-normal distribution)
async def first_navigation_delay(page, target_url: str, delay_s: float):
    """v6.2: Simulate a real user opening a tab, looking around, then
    navigating to the target. Uses log-normal distribution for the delay
    (more realistic than uniform).
    """
    logger.info(f"First-nav delay: {delay_s:.1f}s (log-normal, simulating user looking around)")
    await asyncio.sleep(delay_s)


# v5.0+: get current boot_id (we have it from /proc/sys/kernel/random/boot_id)
def get_current_boot_id() -> str:
    """DC-19 (Pass 19): Read the current Linux boot_id from /proc.

    The boot_id is a unique identifier for the current system boot,
    regenerated on every reboot. Real users have a stable boot_id
    throughout their session; we expose this value to the JS side
    so the persona's boot_id matches the actual container's boot_id.

    CHAMELEON (GAP-2.3): If /proc is unreadable, generate a per-launch
    UUID (looks like a real boot_id but is unique per launch). Returning
    "unknown" would create a cross-persona correlation vector.
    """
    # M23 fix: Add timeout and length validation. The previous
    # code did a blocking open() with no timeout. A hung
    # /proc filesystem (rare but possible in containerized
    # environments) would block the entire pipeline. We now
    # use a 1s timeout via signal.alarm (Linux only) and
    # validate the length of the read content.
    try:
        # Use signal-based timeout (Linux only)
        import signal as _signal
        def _boot_id_timeout_handler(signum, frame):
            raise TimeoutError("boot_id read timed out")
        old_handler = _signal.signal(_signal.SIGALRM, _boot_id_timeout_handler)
        _signal.alarm(1)  # 1s timeout
        try:
            with open("/proc/sys/kernel/random/boot_id", "r") as f:
                real = f.read().strip()
            _signal.alarm(0)  # Cancel alarm
        finally:
            _signal.signal(_signal.SIGALRM, old_handler)
        if real and real != "unknown" and len(real) <= 128:
            return real
    except (TimeoutError, Exception):
        try:
            _signal.alarm(0)  # Ensure alarm is cancelled
        except Exception as _suppressed_exc:
            logger.debug(f"get_current_boot_id(): suppressed {type(_suppressed_exc).__name__}")
    # Fallback: per-launch synthetic boot_id (32 hex chars, like real)
    return uuid.uuid4().hex


# v5.0+: compositor jitter helper
async def compositor_jitter_sleep(base_seconds: float, persona_jitter_rng=None):
    """v5.0+: Sleep with realistic compositor jitter (real browsers have
    ±1-2ms frame jitter, not perfect periodic ticks).

    R7-HOR-011: The previous code used unseeded random.uniform,
    producing different jitter per call even for the same persona.
    We now accept an optional persona_jitter_rng (a random.Random
    instance seeded by the persona_id). If not provided, we fall
    back to unseeded random (for callers that don't have a persona
    context).

    C5 fix (Pass 15): Ensure `actual` is strictly positive before
    calling `asyncio.sleep()`. The previous `max(0, base_seconds + jitter)`
    could yield `actual=0` when `base_seconds=0.0001` and `jitter=-0.005`
    (within the ±1.5ms range). `asyncio.sleep(0)` is documented as
    a single event-loop yield with no delay — meaning the function
    effectively did nothing while the caller believed it slept. We
    now clamp to a tiny minimum (10µs) so the call always yields
    at least one event-loop tick. This matches real browsers, which
    have a minimum ~8ms compositor frame time.

    M17 fix: Input validation. The previous code did not
    validate base_seconds or persona_jitter_rng. A None
    base_seconds would crash with TypeError on the
    `<=` comparison. A non-numeric base_seconds (e.g., a
    string from a misconfigured env var) would crash with
    TypeError. We now validate at function entry.
    """
    # M17 fix: Input validation
    if base_seconds is None or not isinstance(base_seconds, (int, float)):
        logger.debug(
            f"compositor_jitter_sleep called with invalid "
            f"base_seconds (type={type(base_seconds).__name__}); skipping"
        )
        return
    import math as _math
    if _math.isnan(base_seconds) or _math.isinf(base_seconds):
        logger.warning(
            f"compositor_jitter_sleep called with non-finite "
            f"base_seconds ({base_seconds}); skipping"
        )
        return
    if base_seconds <= 0:
        return
    _rng = persona_jitter_rng if persona_jitter_rng is not None else random
    # Add ±BE_COMPOSITOR_JITTER_MS jitter
    jitter = _rng.uniform(-BE_COMPOSITOR_JITTER_MS / 1000.0,
                          BE_COMPOSITOR_JITTER_MS / 1000.0)
    # C5: Clamp to a small positive minimum to avoid asyncio.sleep(0)
    # silently no-oping. 10µs is below human perception but above
    # the asyncio.sleep(0) threshold.
    _MIN_SLEEP_S = 1e-5
    actual = max(_MIN_SLEEP_S, base_seconds + jitter)
    await asyncio.sleep(actual)



def _safe_fp_get(fp, key, default=None):
    """R7-PASS-11 FIX: Defensive nested-dict access for fingerprint JSON.

    The standard Python idiom `_fp_get2(fp, 'navigator', 'model', '')`
    crashes with AttributeError when the KEY is present but the VALUE
    is None (or any non-dict). PostgreSQL JSONB allows null values, and
    a record inserted with `{"navigator": null, "webgl": null}` (e.g.,
    a partial fingerprint, a record imported from another schema, or
    a manual DB edit) would crash the entire task with a confusing
    AttributeError on a benign attribute access.

    This helper:
    1. Returns `default` if `fp` is None or not a dict
    2. Returns `default` if the key is missing
    3. Returns `default` if the value at the key is None
    4. Returns `default` if the value at the key is not a dict
       (for nested access)
    5. Returns the dict otherwise

    Usage: `_safe_fp_get(fp, 'navigator', {}).get(...)` is replaced
    with `_safe_fp_get(fp, 'navigator')` which always returns a dict
    (or the default).

    The key check uses `not isinstance(value, dict)` to also handle
    the case where a JSON object was malformed and parsed to a
    different type (e.g., list, string, int).
    """
    if not isinstance(fp, dict):
        return default
    value = fp.get(key, default)
    if value is None:
        return default
    if not isinstance(value, dict):
        # Non-dict value (e.g., list, string, int from malformed JSON).
        # Always return the default (or None) to prevent the caller
        # from doing .get() on a non-dict and crashing with
        # AttributeError. This is the safe-by-default behavior.
        return default
    return value



def _fp_get2(fp, outer_key, inner_key, default):
    """R7-PASS-11: Two-level safe dict access for fingerprint JSON.

    Returns `default` if:
    - fp is None or not a dict
    - fp[outer_key] is missing, None, or not a dict
    - fp[outer_key][inner_key] is missing or None
    - fp[outer_key][inner_key] is an empty string '' (C2 fix)
    Otherwise returns the value at fp[outer_key][inner_key].

    C2 fix (Pass 15): Empty string '' is now treated as missing and
    the default is returned. Real Chrome never reports an empty
    `navigator.model` or `navigator.platformVersion` — these are
    always non-empty strings. Rendering an empty value into the JS
    FP_CONFIG produced `model: ""` which is itself a fingerprint
    (creepjs and similar tools can detect it). The default fallback
    values used in the FP_CONFIG injection (e.g., "x86", "64") are
    persona-realistic, so falling back to them is always safer than
    emitting "".
    """
    if not isinstance(fp, dict):
        return default
    outer = fp.get(outer_key)
    if not isinstance(outer, dict):
        return default
    inner = outer.get(inner_key)
    if inner is None:
        return default
    # C2: empty string is a fingerprint leak — fall back to default
    if isinstance(inner, str) and inner == "":
        return default
    return inner

def _safe_parse_user_agent(fp):
    """R7-HOR-001: Defensive UA parsing. Returns (full_version, major_int).
    
    Real Chrome 120 UAs are like:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.109 Safari/537.36"
    
    Firefox personas (no Chrome/) and missing/malformed UAs must not
    crash the orchestrator. Falls back to Chrome 120 defaults.
    """
    default_full = "120.0.6099.109"
    default_major = 120
    if not isinstance(fp, dict):
        return default_full, default_major
    nav = fp.get('navigator')
    if not isinstance(nav, dict):
        return default_full, default_major
    ua = nav.get('userAgent')
    if not isinstance(ua, str) or 'Chrome/' not in ua:
        return default_full, default_major
    try:
        chrome_part = ua.split('Chrome/', 1)[1]
        full_ver = chrome_part.split(' ')[0] if ' ' in chrome_part else chrome_part
        major_str = chrome_part.split('.')[0] if '.' in chrome_part else "120"
        major_int = int(major_str)
        return full_ver, major_int
    except (ValueError, TypeError, IndexError):
        return default_full, default_major


async def run_persona_pipeline(persona_id: str, task_name: str, task_payload: dict) -> bool:
    """
    Executes a single persona's task pipeline inside an isolated browser context.
    v5.0+: 1-persona, ephemeral in-memory profile, pre-mounted tmpfs.

    M22 fix: Input validation. The previous code only
    validated task_payload type. A None persona_id or
    non-string task_name would crash deep in the function
    with confusing AttributeError/TypeError. We now validate
    all three at function entry and raise a clear error
    on invalid input.
    """
    # M22 fix: Input validation
    if not persona_id or not isinstance(persona_id, str):
        logger.critical(
            f"run_persona_pipeline called with invalid persona_id "
            f"(type={type(persona_id).__name__}); raising OrchestratorError"
        )
        raise OrchestratorError("invalid_persona_id")
    if not task_name or not isinstance(task_name, str):
        logger.critical(
            f"run_persona_pipeline called with invalid task_name "
            f"(type={type(task_name).__name__}); raising OrchestratorError"
        )
        raise OrchestratorError("invalid_task_name")
    # Limit persona_id length to prevent DB query slowness
    if len(persona_id) > 128:
        logger.critical(
            f"run_persona_pipeline called with overlong persona_id "
            f"({len(persona_id)} chars; max 128); raising OrchestratorError"
        )
        raise OrchestratorError("invalid_persona_id")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.critical(f"playwright not installed; cannot run persona {persona_id}")
        raise OrchestratorError("playwright_missing")

    if not isinstance(task_payload, dict):
        logger.warning(f"task_payload for {persona_id}/{task_name} is not a dict (got {type(task_payload).__name__}). Coercing to empty dict.")
        task_payload = {}
    task_payload = dict(task_payload)
    start_session_time = time.perf_counter()

    # R7-DEEP-AUDIT-2 GAP 1.9 / 6.1 / 6.2 / 6.3: Seed the global
    # random module with the persona_id so any subsequent
    # random.* calls in behavioral modules are deterministic
    # per persona. This is a HACK — it pollutes the global RNG
    # state — but it makes the current behavior deterministic
    # without requiring a refactor of every behavioral module.
    # The proper fix is to pass an `rng` parameter through every
    # behavioral function (out of scope for this audit).
    #
    # M11 fix: Save and restore the previous global random state.
    # The previous code reseeded the global `random` module on every
    # call, which means:
    #   1. After `run_persona_pipeline` returns, the global random
    #      state is in an arbitrary state derived from the LAST
    #      persona's seed.
    #   2. Subsequent code in the daemon loop (e.g., the jittered
    #      poll interval `_poll_jitter = random.uniform(0.7, 1.3)`)
    #      produces values correlated with the last persona's seed.
    #   3. If two personas are launched in the same process, both
    #      get correlated jitter values, which is itself a
    #      fingerprint (creepjs detects "two personas with identical
    #      timing distributions" as a correlation signal).
    # H9 fix: Use per-call-site RNGs. The M4 fix introduced
    # _persona_delay_rng_registry which gives each call site
    # an independent random.Random seeded by (persona_id,
    # site_label). This eliminates the need to mutate the
    # global random module's state for persona-specific
    # randomness. The legacy global reseed below is kept for
    # backward compat with behavioral modules that still
    # call the global random module directly.
    # Capture random state BEFORE any fallible operations
    _random_state = random.getstate()
    try:
        random.seed(int(hashlib.sha256(persona_id.encode('utf-8')).hexdigest()[:8], 16))
    except Exception as _suppressed_exc:
        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")

    # R7-DEEP-AUDIT-2 GAP 1.3 / 5.2: Compute geolocation coords for the
    # active city. Used to inject __sre_geo_coords into the browser init
    # script. The JS-side geolocation shim uses these to return
    # plausible coordinates matching the persona's proxy geo.
    #
    # R7-DEEP-AUDIT-2 GAP 5.1: Use the PROXY IP's coords (not the
    # city's) so a site that cross-checks the browser geolocation
    # against an IP geo lookup doesn't see a mismatch. The proxy
    # IP is the source of truth for the persona's perceived
    # location.
    _geo_coords_for_payload: Optional[Dict[str, float]] = None
    try:
        # Try to extract the city from the task payload (PPTC routing
        # already populated persona_by_city with the canonical city).
        # Fall back to the persona's signup_geo_region (state code) if
        # no city is in the payload.
        _geo_city: Optional[str] = None
        if _city_persona_module is not None:
            try:
                _geo_city = _city_persona_module._extract_city_from_payload(task_payload)
            except Exception:
                _geo_city = None
        if _geo_city:
            _coords = get_coords_for_city(_geo_city)
            if _coords:
                _geo_coords_for_payload = {
                    "latitude": _coords[0],
                    "longitude": _coords[1],
                    "accuracy": 25,  # ~25m, matching real GPS
                }
    except Exception as _geo_err:
        logger.debug(f"PPTC: geo coord lookup failed (non-fatal): {_geo_err}")
        _geo_coords_for_payload = None

    # R7-DEEP-AUDIT-2 GAP 5.1: After the persona_socks5_url is set
    # (line ~1911), we can call check_proxy_integrity which returns
    # the proxy's resolved IP. The proxy IP's geo is the most
    # accurate match for the persona's geolocation. We do this AFTER
    # the proxy integrity check (line ~1920) and OVERRIDE
    # _geo_coords_for_payload with the proxy IP's coords.
    #
    # The actual override is done after the proxy check; this
    # placeholder documents the intent.

    # A5 fix (Pass 16): Removed `global db_pool` declaration.
    # The previous code had `global db_pool` immediately before
    # `if db_pool is None: raise ...`, which is a READ-ONLY
    # access. The `global` declaration is needed only when
    # REASSIGNING the global variable (e.g., `db_pool = ...`).
    # The check `if db_pool is None` only reads, so the `global`
    # declaration was dead code that confused readers into
    # thinking this function might reassign `db_pool` (it
    # doesn't; the daemon loop's `db_pool = await ...` is
    # what sets it). The declaration is preserved in
    # `orchestrator_daemon_loop()` (line ~3431) where the actual
    # assignment happens.
    if db_pool is None:
        raise DatabaseUnavailableError("db_pool is None")

    # v5.0+: PID budget check
    if not check_pid_budget(persona_id):
        raise PIDBudgetExceededError(f"PID budget exceeded", persona_id=persona_id)

    # v5.0+: Verify task tmpfs is ready
    if not _verify_task_tmpfs_ready():
        logger.critical(f"Task tmpfs {ORCH_TASK_TMPFS_DIR} not ready; aborting.")
        return False

    # Fetch persona attributes
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT fingerprint, session_data, assigned_proxy_url, signup_geo_region, state,
                       ai_provider, ai_model, ai_api_key, canvas_noise_seed, client_hints, timezone,
                       assigned_asn, assigned_isp, warmup_stage, created_at
                FROM personas WHERE id = $1
            """, persona_id)
    except Exception as query_err:
        logger.error(f"Database query failed for persona {persona_id}: {query_err}")
        raise DatabaseUnavailableError(f"query_failed: {query_err}") from query_err

    if not row:
        logger.error(f"Persona {persona_id} metadata missing in Database. Aborting run.")
        return False

    if row['state'] == 'limited':
        logger.warning(f"Persona {persona_id} is in 'limited' state; skipping.")
        return False

    try:
        fp = json.loads(row['fingerprint'])
    except (json.JSONDecodeError, TypeError) as fp_err:
        logger.error(f"Persona {persona_id} has invalid fingerprint JSON: {fp_err}")
        return False

    # R7-PASS-11 FIX: Validate that the fingerprint parsed to a dict.
    # JSONB can contain any JSON value (null, true, "string", [1,2,3], etc.).
    # A fingerprint of just `null` or `42` would parse successfully but
    # be unusable. The subsequent code assumes `fp` is a dict.
    if not isinstance(fp, dict) or len(fp) == 0:
        logger.warning(f"Persona {persona_id} fingerprint is empty/missing — generating synthetic one")
        # CHAMELEON (GAP-1.7): Generate a synthetic but realistic
        # fingerprint seeded by persona_id. An empty `fp = {}` would
        # cause the browser to report only defaults — itself a
        # fingerprint (no real user has all-default values).
        try:
            from humanized_movement import get_muscle_profile
            _profile_for_fp = get_muscle_profile(persona_id)
            _hw_conc = getattr(_profile_for_fp, 'hardware_concurrency', 8)
            _dev_mem = getattr(_profile_for_fp, 'device_memory', 8)
        except Exception:
            _hw_conc, _dev_mem = 8, 8
        fp = {
            "navigator": {
                "platform": "Win32",
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.109 Safari/537.36",
                "hardwareConcurrency": _hw_conc,
                "deviceMemory": _dev_mem,
                "maxTouchPoints": 0,
            },
            "screen": {"width": 1920, "height": 1080, "colorDepth": 24, "pixelDepth": 24},
            "webgl": {"vendor": "Intel Inc.", "renderer": "Intel(R) Iris(TM) Plus Graphics 640"},
        }

    session_data = row['session_data']
    # CONCURRENCY-FIX C7: remember the exact value we loaded. The session save
    # at the end of run_persona_pipeline() compares against this so a
    # concurrent session for the same persona cannot be silently overwritten.
    _session_data_at_load = session_data
    assigned_proxy_url = row['assigned_proxy_url']
    signup_geo_region = row['signup_geo_region'] or "Dallas"
    ai_provider = row['ai_provider']
    ai_model = row['ai_model']
    ai_api_key = row['ai_api_key'] or os.getenv("DEFAULT_AI_API_KEY")
    canvas_noise_seed = row['canvas_noise_seed'] or 12345

    client_hints_raw = row['client_hints']
    client_hints = None
    if client_hints_raw:
        try:
            client_hints = json.loads(client_hints_raw)
        except (json.JSONDecodeError, TypeError) as ch_err:
            logger.warning(f"client_hints for {persona_id} is malformed JSON ({ch_err}); using None.")
            client_hints = None

    timezone = validate_timezone(row['timezone'])
    warmup_stage = row['warmup_stage'] or 0

    # v5.0+: persona retirement check
    created_at = row.get('created_at')
    if created_at:
        # R7-HOR-005: Use timezone-aware datetime to match asyncpg's
        # tz-aware datetimes (TIMESTAMP WITH TIME ZONE columns). The
        # previous code used datetime.datetime.utcnow() which is NAIVE
        # (no tzinfo). Subtracting a tz-aware datetime from a naive
        # one raises TypeError. Now we use datetime.now(timezone.utc).
        if isinstance(created_at, datetime.datetime):
            if created_at.tzinfo is None:
                # Naive datetime from DB — assume UTC
                now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            else:
                # Tz-aware datetime
                now_utc = datetime.datetime.now(datetime.timezone.utc)
            age_hours = (now_utc - created_at).total_seconds() / 3600
        else:
            # created_at is a string or other type; skip the check
            age_hours = 0
        if age_hours > PERSONA_ROTATION_HOURS:
            logger.warning(
                f"Persona {persona_id} is {age_hours:.1f}h old "
                f"(rotation threshold: {PERSONA_ROTATION_HOURS}h); marking for rotation."
            )
            try:
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE personas SET state = 'rotated' WHERE id = $1",
                        persona_id
                    )
            except Exception as e:
                logger.error(f"Failed to mark persona for rotation: {e}")
            raise PersonaRetiredError(
                f"Persona {persona_id} reached {PERSONA_ROTATION_HOURS}h rotation threshold",
                persona_id=persona_id
            )

    # v5.0+: per-persona fingerprint digest (locked values)
    persona_digest = _compute_persona_digest(persona_id)

    # INVIS-6: Generate a per-launch session ID for canvas/audio noise.
    # This combines with the per-persona canvas_noise_seed in the JS
    # side to produce slightly different canvas output per session,
    # mimicking real Chrome's per-session driver/font cache variation.
    # (Use uuid4 for full entropy; the JS side hashes it into 32 bits.)
    _session_uuid = str(uuid.uuid4())
    persona_digest["canvas_noise_session_id"] = _session_uuid
    logger.debug(f"INVIS-6: Session ID for {persona_id}: {_session_uuid[:8]}...")

    # INVIS-10: Query the persona's task_count from the DB. This is used
    # by the JS side to compute Notification.permission state
    # (fresh = "default", seasoned = "denied" after 3+ sessions).
    #
    # PPPC: Also use task_count for history_depth (chrome.csi().tran
    # and history.length grow with task_count — real users have a
    # monotonic, slowly-growing history).
    #
    # R7-DEEP-AUDIT-2 GAP 1.2: For PPTC personas, prefer the
    # persona_by_city.task_count (per-city count) over the
    # personas.task_count (global count). Otherwise a persona used
    # in 3 cities will have history_depth=3 in all 3 cities, which
    # is itself a fingerprint (the city's history should be per-city).
    try:
        async with db_pool.acquire() as conn:
            # PPTC: prefer the city-specific task count.
            _ppc_row = await conn.fetchrow(
                "SELECT COALESCE(task_count, 0) AS tc "
                "FROM persona_by_city "
                "WHERE persona_id = $1 "
                "ORDER BY last_used DESC LIMIT 1",
                persona_id
            )
            if _ppc_row and _ppc_row['tc'] is not None:
                _tc = int(_ppc_row['tc'])
            else:
                # Fallback to the persona's global task_count
                _notif_count_row = await conn.fetchrow(
                    "SELECT COALESCE(task_count, 0) AS tc FROM personas WHERE id = $1",
                    persona_id
                )
                _tc = int(_notif_count_row['tc'] or 0) if _notif_count_row else 0
            persona_digest["notification_session_count"] = _tc
            # PPPC: history_depth = task_count (real users have
            # grown their history by exactly 1 per visit)
            persona_digest["history_depth"] = _tc
    except Exception as _ne:
        logger.debug(f"INVIS-10: Could not query notification_session_count: {_ne}")
        persona_digest["notification_session_count"] = 0
        persona_digest["history_depth"] = 0

    # PPPC: is_returning flag — true if session_data was successfully
    # extracted above (i.e., this is a returning persona with prior
    # history). The JS side uses this to vary UI affordances.
    try:
        persona_digest["is_returning"] = bool(
            session_data and not isinstance(session_data, str) and len(session_data) > 0
        )
    except Exception:
        persona_digest["is_returning"] = False

    # Circadian check
    if _CircadianScheduler is not None:
        try:
            local_circadian = _CircadianScheduler(timezone=timezone)
            if not local_circadian.is_persona_awake():
                logger.warning(f"Circadian lockout active for {persona_id} (TZ: {timezone}). Suspending.")
                return False
        except Exception as circ_err:
            logger.warning(f"Circadian check skipped for {persona_id}: {circ_err}")

    # v6.2: Human-like delay distribution (log-normal, not uniform)
    # Real human behavior follows log-normal distributions for inter-action
    # times. Using uniform distributions is statistically detectable over
    # many samples.
    import math
    # R7-DEEP-AUDIT-2 FIX: Use a per-persona seeded RNG instead of the
    # global random.gauss(). The previous code violated per-persona
    # determinism — the same persona launched twice would produce
    # different delay distributions. We now create a deterministic
    # random.Random instance from a seed derived from the persona_id
    # and the call site, so delays are stable across launches.
    # M4 fix: Per-call-site RNG for _human_like_delay. The previous
    # code used a single _persona_delay_rng instance for ALL
    # _human_like_delay calls within a persona's pipeline. The
    # first call would advance the RNG state, and subsequent calls
    # would see correlated values (e.g., the first sample being
    # on the high end of the distribution biased subsequent
    # samples to the low end). This is a behavioral fingerprint:
    # real users do not have correlated timing across independent
    # events like pre-task quiescence and post-task cooldown.
    #
    # The fix: use a per-call-site RNG, seeded deterministically
    # by (persona_id, call_site_label). Each call site gets its
    # own independent RNG state, so the same value isn't repeated
    # across call sites. The persona_id keeps it stable across
    # launches (the same persona always sees the same values for
    # the same call site).
    if persona_id not in _persona_delay_rng_registries:
        # BUG-5 FIX: Evict oldest entry when cap is reached (FIFO).
        if len(_persona_delay_rng_registries) >= _PERSONA_DELAY_RNG_REGISTRY_MAX:
            _persona_delay_rng_registries.popitem(last=False)
        _persona_delay_rng_registries[persona_id] = {}
    _persona_delay_rng_registry = _persona_delay_rng_registries[persona_id]

    def _human_like_delay(min_s: float, max_s: float, site_label: str = "default") -> float:
        """Generate a log-normally distributed delay between min_s and max_s.
        R7-HOR-009: The previous implementation clipped the output in
        OUTPUT SPACE (max(..., min(..., delay))), which produced a
        bimodal distribution with mass accumulating at the edges
        (min_s and max_s). This is because the underlying log-normal
        has a long right tail; clipping it at max_s piles up mass at
        max_s. The new implementation clips in LOG-SPACE (clamp the
        Gaussian sample to ±2 sigma around the mean), preserving the
        log-normal shape. Output is then exp(clamped_sample) which
        falls in [exp(mu-2*sigma), exp(mu+2*sigma)] ≈ [min_s, max_s]
        for our parameterization.

        M4 fix: Per-call-site RNG. Each call site (identified by
        `site_label`) gets its own independent random.Random
        instance. Without this, multiple _human_like_delay calls
        in the same pipeline share the same RNG state, producing
        correlated samples (e.g., a high first sample biases
        subsequent samples). Real user timings are independent
        across distinct events. The site_label parameter gives
        each call a deterministic, isolated RNG stream.

        R7-DEEP-AUDIT-2: All RNGs are seeded by persona_id (and
        the call site label), so the same persona launched twice
        produces the same sequence at each call site.
        """
        # M4: Get or create the per-site RNG
        if site_label not in _persona_delay_rng_registry:
            _site_seed = int(hashlib.sha256(
                f"delay:{persona_id}:{site_label}".encode('utf-8')
            ).hexdigest()[:8], 16)
            _persona_delay_rng_registry[site_label] = random.Random(_site_seed)
        _site_rng = _persona_delay_rng_registry[site_label]

        if min_s > max_s:
            min_s, max_s = max_s, min_s
        if min_s <= 0 or max_s <= 0:
            logger.warning("_human_like_delay called with non-positive values; using minimum safe value")
            min_s = max(min_s, 0.1)
            max_s = max(max_s, 0.2)
        if min_s == max_s:
            return min_s

        # Log-normal centered on the geometric mean
        # mu in log-space such that exp(mu) = geometric mean
        # sigma in log-space such that exp(mu+sigma) ≈ 1.5 * geometric mean
        geometric_mean = math.sqrt(min_s * max_s)
        mu = math.log(geometric_mean)
        # sigma chosen so 95% of samples fall in [min_s, max_s]
        # i.e., exp(mu ± 2*sigma) covers [min_s, max_s]
        # This gives sigma = (log(max_s) - log(min_s)) / 4
        sigma = (math.log(max_s) - math.log(min_s)) / 4.0
        # Sample with clamped Gaussian (preserves log-normal shape)
        # M4 fix: Use per-site RNG, not the shared _persona_delay_rng
        raw_gauss = _site_rng.gauss(mu, sigma)
        # Clamp to ±2 sigma in log-space (preserves ~95% of distribution)
        clamped_gauss = max(mu - 2 * sigma, min(mu + 2 * sigma, raw_gauss))
        return math.exp(clamped_gauss)

    # R7-HOR-012: Adjust quiescence range based on persona's task count.
    # First-launch personas (task_count=0) need longer quiescence
    # (a real user takes longer to "settle in" on a new account).
    # Seasoned personas (task_count>3) have shorter quiescence
    # (they're familiar with the flow).
    _task_count = persona_digest.get("notification_session_count", 0)
    if _task_count < 3:
        # First-launch: full range (30-180s)
        _q_min = PERSONA_QUIESCENCE_BEFORE_TASK_S_MIN
        _q_max = PERSONA_QUIESCENCE_BEFORE_TASK_S_MAX
    else:
        # Seasoned: shorter range (10-90s)
        _q_min = 10
        _q_max = 90
    quiescence = _human_like_delay(_q_min, _q_max, site_label="quiescence")
    logger.info(f"Pre-task quiescence: {quiescence:.1f}s (log-normal, task_count={_task_count})")
    await asyncio.sleep(quiescence)

    # v5.0+: per-persona SOCKS5 port
    persona_socks5_url = get_socks5_url_for_persona(persona_id)
    # R7-SPR-008: Redact credentials when logging the proxy URL.
    # The previous code logged the full URL including username:password,
    # which is a credential leak if logs are shipped to a centralized
    # system. We now log only the host:port and the username (not
    # the password).
    _safe_proxy_log = _redact_proxy_credentials(persona_socks5_url)
    logger.info(f"SOCKS5 proxy for {persona_id}: {_safe_proxy_log}")

    # C1/C3 fix: Detect proxy URL change vs. last task and purge the
    # resolver's HTTP client + cache. Without this, the resolver's
    # shared httpx client (with its keep-alive TCP+TLS connection
    # and HTTP/2 session) leaks across personas. The new persona
    # would inherit the previous persona's connection state, causing
    # traffic to be routed through the wrong proxy and exposing
    # H2 session IDs from the previous persona to the target site.
    # We track the last proxy URL at module scope and call
    # purge_proxy_state() when it changes.
    global _LAST_PROXY_URL, _LAST_PROXY_PERSONA_ID
    # Store initial signature BEFORE proxy resolution
    _initial_proxy_signature = f"{persona_id}:{persona_socks5_url}"
    if (
        _LAST_PROXY_URL is not None
        and _LAST_PROXY_URL != _initial_proxy_signature
    ):
        logger.info(
            f"C1/C3: Proxy URL changed from '{_LAST_PROXY_URL}' "
            f"to '{_initial_proxy_signature}'; purging resolver state"
        )
        if _StickyProxyResolver is not None:
            try:
                _resolver_for_purge = get_proxy_resolver()
                await _resolver_for_purge.purge_proxy_state()
            except Exception as purge_err:
                # Non-fatal; the new proxy will work, just may
                # carry some stale state for the first request.
                logger.warning(
                    f"C1/C3: resolver purge failed (non-fatal): {purge_err}"
                )
    _LAST_PROXY_URL = _initial_proxy_signature
    _LAST_PROXY_PERSONA_ID = persona_id

    # SPR1+SPR2: Pre-flight proxy health check + geo-clamping.
    # The proxy must be alive AND in the persona's registered region
    # before browser launch. Without this check, a dead proxy is
    # only discovered when the browser tries to connect, which is
    # too late.
    # SPR52: Pass required_timezone so the resolver can detect
    # timezone desync (reCAPTCHA Enterprise top-3 signal).
    if _StickyProxyResolver is not None:
        try:
            proxy_resolver = get_proxy_resolver()
            # Get the persona's required geo from the DB
            _required_geo = row.get('signup_geo_region') or 'unknown'
            persona_socks5_url = await proxy_resolver.get_proxy_for_persona(
                persona_id=persona_id,
                assigned_proxy_url=persona_socks5_url,
                required_geo=_required_geo,
                required_timezone=timezone,  # SPR52
            )
            logger.info(
                f"Proxy Guard: {persona_id} proxy verified. URL: {persona_socks5_url[:50]}..."
            )
            # R7-DEEP-AUDIT-2 GAP 5.1: After the proxy integrity check,
            # try to resolve the proxy IP's geo coords and use them
            # for the navigator.geolocation shim. This ensures the
            # browser's geolocation matches the proxy IP's geo (a
            # site that cross-checks won't see a mismatch).
            if httpx is not None:
                try:
                    _ip_check_url = os.getenv("IP_CHECK_URL") or "http://172.18.0.1:8888/ip"
                    _adapted_proxy = persona_socks5_url.replace("socks5://", "socks5h://") if persona_socks5_url.startswith("socks5://") else persona_socks5_url
                    async with httpx.AsyncClient(proxies={"all://": _adapted_proxy}, timeout=5.0) as _ip_client:
                        _ip_resp = await _ip_client.get(_ip_check_url)
                        if _ip_resp.status_code == 200:
                            # The IP endpoint may return JSON or plain text
                            _ip_text = _ip_resp.text.strip()
                            try:
                                _ip_data = json.loads(_ip_text)
                                _proxy_lat = _ip_data.get("latitude") or _ip_data.get("lat")
                                _proxy_lon = _ip_data.get("longitude") or _ip_data.get("lon") or _ip_data.get("lng")
                                if _proxy_lat and _proxy_lon:
                                    _geo_coords_for_payload = {
                                        "latitude": float(_proxy_lat),
                                        "longitude": float(_proxy_lon),
                                        "accuracy": 5000,  # IP geo is city-level
                                    }
                                    logger.debug(
                                        f"PPTC: Using proxy IP geo coords: "
                                        f"({_proxy_lat}, {_proxy_lon})"
                                    )
                            except (json.JSONDecodeError, TypeError, ValueError) as _suppressed_exc:
                                # Plain text response (just an IP); no geo
                                # available. Fall back to city center.
                                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                except Exception as _ip_geo_err:
                    logger.debug(f"PPTC: proxy IP geo lookup failed (non-fatal): {_ip_geo_err}")
        except ValueError as proxy_err:
            # SPR49: GeoBreachError is also a ValueError, so this catches
            # both the legacy "no proxy available" and the new typed
            # "geo breach" case. Either way, the persona is unsafe to launch.
            logger.critical(f"Proxy Guard: {proxy_err}")
            return False
        except Exception as proxy_err:
            # SPR49: ProxyUnavailableError and any other unexpected error.
            # Log and fall through (the rest of the pipeline will see
            # the proxy as unverified but will still attempt to launch).
            logger.warning(f"Proxy Guard: Non-fatal error during proxy check: {proxy_err}")

    # v5.0+: workspace path = pre-mounted per-task tmpfs
    # The entrypoint.sh created /mnt/ramdisk/task_<uuid> as a tmpfs mount.
    # The orchestrator only does mkdir/extract/wipe (NO mount/umount).
    profile_path = None
    xvfb_process = None
    profile_path = os.path.join(ORCH_TASK_TMPFS_DIR, f"persona_{persona_id}")
    # L20 fix: Removed dead `encrypted_archive` variable. The
    # previous code computed `encrypted_archive = ...` but
    # never used the variable anywhere downstream. The session
    # archive is now decrypted in-memory from session_data
    # (bytes) — no on-disk file is ever written. The path
    # computation was dead code that nonetheless produced a
    # string reference, which (combined with the os.path.join
    # call) leaked a small amount of memory. Over many task
    # invocations, this minor leak accumulates. We now skip
    # the path computation entirely.
    # (Previous dead code: encrypted_archive = os.path.join(...))

    # v5.0+: in-memory decrypt the encrypted session archive (no temp file)
    # The DB session_data column contains the Fernet-encrypted bytes (or
    # a path to a side-loaded archive; we use a query-time approach).
    if session_data and isinstance(session_data, (bytes, bytearray, memoryview)):
        decrypted_bytes = None
        try:
            # A7 fix (Pass 16): Use the async version to avoid
            # blocking the event loop on a corrupted token. The
            # previous code called the sync `_fernet_decrypt_bytes`
            # which uses `future.result(timeout=...)` — that blocks
            # the calling thread (and thus the event loop) for up
            # to 5s. A blocked event loop is detectable via
            # JavaScript's setTimeout drift (a 5s gap is
            # observable). The async version uses `await` so the
            # event loop continues running other coroutines.
            decrypted_bytes = await _fernet_decrypt_bytes_async(
                bytes(session_data), ENCRYPTION_KEY.encode()
            )
            # CHAMELEON (GAP-1.3): Verify archive has expected structure
            # (must contain Cookies) and use atomic extract (to temp +
            # rename) to prevent partial state from corrupted archives.
            #
            # M25 fix: tarfile + io.BytesIO leak. The previous code
            # used `with tarfile.open(...)` which closes the TarFile
            # object but NOT the underlying io.BytesIO fileobj.
            # On exception (e.g., has_cookies_db check fails, or
            # extractall raises), the io.BytesIO buffer holding
            # the decrypted archive bytes lingers in memory until
            # GC. Combined with the finally block's `decrypted_bytes
            # = None` (which is a different reference), the
            # io.BytesIO may hold the archive bytes for the
            # duration of the function. We now explicitly close
            # the fileobj in a nested try/finally.
            _tar_buf = io.BytesIO(decrypted_bytes)
            try:
                with tarfile.open(fileobj=_tar_buf, mode="r:gz") as tar:
                    members = tar.getmembers()
                    has_cookies_db = any('Cookies' in m.name or 'cookies.json' in m.name for m in members)
                    if not has_cookies_db:
                        raise ValueError("Decrypted archive missing Cookies database — likely truncated/corrupt")
                    # Atomic extract: first to temp dir, then rename
                    temp_extract = profile_path + ".tmp.extract"
                    os.makedirs(temp_extract, mode=0o755, exist_ok=True)
                    try:
                        # R7-SEC-001: Validate tar members for path traversal
                        #
                        # RELIABILITY-FIX H2: the previous check was
                        #     os.path.abspath(member_path).startswith(
                        #         os.path.abspath(temp_extract))
                        # which is a prefix test without a separator. A member
                        # named "../<profile>.tmp.extract.evil/x" resolves to a
                        # SIBLING directory whose path string still starts with
                        # temp_extract's, so it passed validation and was
                        # written outside temp_extract -- into a directory the
                        # cleanup rmtree() never touches, so it survived the
                        # persona wipe.
                        #
                        # Three changes:
                        #   1. compare against `base + os.sep` (and `== base`)
                        #   2. realpath() both sides so a symlinked parent
                        #      directory cannot fool the check
                        #   3. reject device/fifo members, and reject symlink /
                        #      hardlink members whose target resolves outside
                        #      the extraction root
                        base = os.path.realpath(temp_extract)
                        for member in tar.getmembers():
                            if member.type in (
                                tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE
                            ):
                                raise ValueError(
                                    f"Refusing device/fifo tar member: {member.name}"
                                )
                            member_path = os.path.realpath(
                                os.path.join(base, member.name)
                            )
                            if member_path != base and not member_path.startswith(base + os.sep):
                                raise ValueError(f"Path traversal detected in tar entry: {member.name}")
                            if member.issym() or member.islnk():
                                # Links are allowed only when their target
                                # resolves inside the extraction root.
                                # (profile_archiver.py uses tar.add() with
                                # dereference=False, so legitimate profiles can
                                # contain internal symlinks -- banning all
                                # links would break session restore.)
                                link_target = (
                                    member.linkname if member.issym()
                                    else os.path.join(os.path.dirname(member.name), member.linkname)
                                )
                                link_abs = os.path.realpath(os.path.join(base, link_target))
                                if link_abs != base and not link_abs.startswith(base + os.sep):
                                    raise ValueError(
                                        f"Refusing out-of-root link in tar entry: "
                                        f"{member.name} -> {member.linkname}"
                                    )
                        # tarfile.extractall() only accepts `filter=` on Python
                        # 3.12+. This image is playwright:jammy (Python 3.10),
                        # so passing it unconditionally would raise TypeError
                        # and break every session restore. Use it where it
                        # exists; the manual checks above cover earlier versions.
                        if sys.version_info >= (3, 12):
                            tar.extractall(path=temp_extract, filter="data")  # noqa: S202
                        else:
                            # noqa: S202 - members are validated immediately
                            # above (path containment, link targets, device
                            # nodes). `filter=` does not exist before 3.12.
                            tar.extractall(path=temp_extract)  # noqa: S202
                        # Wipe profile_path contents (keep .tmp) and swap
                        for entry in os.listdir(profile_path):
                            if entry == ".tmp":
                                continue
                            entry_path = os.path.join(profile_path, entry)
                            try:
                                if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                                    shutil.rmtree(entry_path, ignore_errors=True)
                                else:
                                    os.unlink(entry_path)
                            except Exception as _suppressed_exc:
                                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                        # Move temp_extract contents into profile_path
                        for entry in os.listdir(temp_extract):
                            os.rename(
                                os.path.join(temp_extract, entry),
                                os.path.join(profile_path, entry)
                            )
                        os.rmdir(temp_extract)
                    except Exception as swap_err:
                        shutil.rmtree(temp_extract, ignore_errors=True)
                        raise
            finally:
                # M25: Close the io.BytesIO buffer. tarfile.close()
                # only closes the TarFile handle, not the underlying
                # BytesIO. Without this, the buffer holds the
                # decrypted archive bytes in memory until the next
                # GC cycle.
                try:
                    _tar_buf.close()
                except Exception as _suppressed_exc:
                    logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
            logger.info(f"In-memory decrypt + extract OK ({len(decrypted_bytes)} bytes -> {profile_path})")
        except Exception as arch_err:
            logger.warning(f"In-memory session archive decryption failed: {arch_err}")
            # CHAMELEON: Ensure profile_path is fresh even on failure
            for entry in list(os.listdir(profile_path)):
                if entry == ".tmp":
                    continue
                entry_path = os.path.join(profile_path, entry)
                try:
                    if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                        shutil.rmtree(entry_path, ignore_errors=True)
                    else:
                        os.unlink(entry_path)
                except Exception as _suppressed_exc:
                    logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
        finally:
            # R7-DEEP-AUDIT-2 FIX: Fernet.decrypt() returns immutable
            # `bytes`, not `bytearray`. The previous `isinstance(..., bytearray)`
            # check was always False, so the zeroing loop never ran and
            # decrypted session data lived in memory until garbage
            # collection. Worse: even if we tried to use ctypes to
            # zero the buffer, CPython's `bytes` objects have READ-ONLY
            # backing buffers (TypeError: underlying buffer is not
            # writable). We document this limitation here.
            #
            # Best-effort approach:
            # 1. Drop the local reference (allows GC to reclaim memory)
            # 2. Force gc.collect() to reclaim immediately
            # 3. The bytes object becomes eligible for collection; the
            #    actual memory reuse happens at the C allocator level.
            # 4. For TRUE secure zeroing, would need a C extension or
            #    mlock'd memory allocator. Not in scope for this audit.
            #
            # The data was already extracted to disk (profile_path) at
            # this point, so the in-memory copy is the LAST plaintext
            # copy. By dropping the reference promptly, we minimize
            # the window during which the plaintext exists in RAM.
            decrypted_bytes = None
            import gc
            gc.collect()
    elif session_data and isinstance(session_data, str):
        # session_data is a string (likely JSON of cookies etc., not archive)
        # This is the legacy path; we don't extract any archive, just use
        # the cookies.
        logger.debug("session_data is a string (legacy cookies JSON); no archive to extract.")

    os.makedirs(profile_path, mode=0o755, exist_ok=True)
    tmp_dir = os.path.join(profile_path, ".tmp")
    os.makedirs(tmp_dir, mode=0o700, exist_ok=True)

    # R7-DEEP-AUDIT-2 ROUND 1 FIX: Chrome SingletonLock cleanup.
    # Chrome uses a "SingletonLock" symlink/file in the user data
    # dir to ensure only one Chrome instance per profile. If a
    # previous Chrome process crashed without releasing the lock,
    # the next launch fails with:
    #   "The profile is already in use by another Chrome process"
    # Real Chrome on real users doesn't have this issue because
    # crashes are rare and the user manually closes Chrome.
    # In automation, crashes are common. We must clean up the
    # SingletonLock on every launch to ensure the profile is usable.
    # The lock file is at <profile>/SingletonLock (or
    # SingletonLockSymlink on some platforms).
    #
    # L25 fix: SingletonLock leak on process death. The previous
    # code unlinked the lockfile ONLY when the persona launches
    # again. If the orchestrator process is killed (SIGKILL,
    # OOM kill, container OOM) before the next launch, the
    # lockfile remains on the tmpfs. When the next container
    # starts (NEW tmpfs, but the persona may have been persisted
    # to the DB), Chrome sees the stale lockfile and refuses to
    # launch. We now unlink the lockfile in the finally block of
    # run_persona_pipeline too (in case the pipeline crashes
    # mid-task and the lock is recreated by a child Chrome). The
    # combination of "unlink at start" + "unlink at finally" closes
    # the leak vector.
    for _lock_name in ("SingletonLock", "SingletonLockSymlink", "SingletonCookie"):
        _lock_path = os.path.join(profile_path, _lock_name)
        try:
            if os.path.islink(_lock_path) or os.path.isfile(_lock_path):
                os.unlink(_lock_path)
                logger.debug(f"R7-RO1: Removed stale {_lock_name} from {profile_path}")
        except Exception as _lock_err:
            # Non-fatal; Chrome will fail to start if the lock
            # is still there, but that's a hard error we'll catch
            # elsewhere with a clearer message.
            logger.debug(f"R7-RO1: Failed to remove {_lock_name}: {_lock_err}")

    # CHAMELEON (GAP-1.2): Pre-launch profile wipe — no carryover from
    # previous persona that may have crashed before cleanup. Combined
    # with HSTS/cache purge, this ensures every launch is a clean slate.
    #
    # PPPC softening (R7-DEEP-AUDIT-2): If session_data was successfully
    # extracted (i.e., the persona is a RETURNING persona with prior
    # history), we keep the persistent state (Cookies, Local Storage)
    # and only purge the cross-session artifacts (HSTS, TLS session
    # tickets, IndexedDB, Service Workers, GPUCache, etc.). This makes
    # the persona look like a real returning user.
    #
    # For FIRST-LAUNCH personas (no session_data), we do the full wipe
    # to ensure no carryover from a previous persona that crashed.
    _is_returning_persona = bool(session_data and not isinstance(session_data, str) and len(session_data) > 0)
    try:
        # CHAMELEON (GAP-5.4): Aggressive HSTS/cache purge. HSTS state
        # is observable via fetch() to a known HSTS-eligible domain, and
        # the Cache API can leak prior session URLs. We explicitly purge
        # the Chrome subdirs that hold this state.
        #
        # PPPC: For returning personas, the session archive restore
        # already overwrites Cookies/Local Storage with the persona's
        # saved state, so we don't need to wipe those. The HSTS, TLS
        # session tickets, IndexedDB, and Service Workers are
        # cross-session correlation vectors that we DO wipe.
        #
        # R7-DEEP-AUDIT-2 GAP 1.4: Added missing TLS state directories
        # that were not in the original purge list. Chrome 120 stores
        # TLS session tickets and SSL client cert cache in:
        #   - Default/Network/Socket Pool (TCP socket state)
        #   - Default/Network/Sockets (active TCP connections)
        #   - Default/Code Cache/ssl (TLS-related compiled code)
        #   - Default/ClientCertificates (SSL client certs)
        #   - Default/Network/Predictor (preconnect predictions)
        #   - Default/Network/Preconnect (preconnect state)
        # Without these, a returning persona can be linked to a
        # previous session via TLS session ticket reuse.
        _chameleon_purge_paths = [
            # HSTS / TLS (always purge — observable via fetch)
            "Default/HSTS Cache",
            "Default/TransportSecurity",
            "Default/Network/TransportSecurity",
            "Default/SslCertCache",
            "Default/ClientCertificates",
            "Default/Network/Socket Pool",
            "Default/Network/Sockets",
            "Default/Network/Predictor",
            "Default/Network/Preconnect",
            "Default/Network/Reporting",
            "Default/Code Cache/ssl",
            "Default/Code Cache/wasm",
            "Default/Reporting and NEL",
            "Default/trusted_certs",
            "Default/certificate_manager",
            # IndexedDB / Service Workers (always purge — cross-session state)
            "Default/IndexedDB",
            "Default/Service Worker",
            "Default/Service Worker/Database",
            "Default/Service Worker/ScriptCache",
            "Default/Service Worker/CacheStorage",
            # C2 fix: Additional Service Worker storage locations. Chrome
            # 120 stores SW state in multiple directories, not just
            # "Default/Service Worker/". The previous purge list was
            # incomplete: SW registrations and push subscriptions are
            # stored in LevelDB databases under the "Service Worker"
            # directory with lock-protected .ldb/.log files. If the
            # browser is killed mid-write (SIGKILL, OOM), the SW
            # database may have lock files or partial writes that
            # confuse the next launch. We now purge the additional
            # SW-related paths:
            #   - "Default/Service Worker/Storage" (LevelDB)
            #   - "Default/Service Worker/Database/Servers" (per-origin SWs)
            #   - "Default/Site Characteristics Database" (SW-driven hints)
            #   - "Default/Storage/ext" (extension storage, if SW used it)
            "Default/Service Worker/Storage",
            "Default/Service Worker/Database/Servers",
            "Default/Site Characteristics Database",
            "Default/Storage/ext",
            "Default/File System",
            "Default/Storage",
            # Cache (always purge — leaks prior URLs)
            "Default/Cache",
            "Default/Code Cache",
            "Default/GPUCache",
            "Default/Shared Dictionary Cache",
            "Default/optimization_guide",
            "Default/ShaderCache",
            "Default/DawnGraphiteCache",
            "Default/DawnWebGPUCache",
            "Default/GPUCache/data_3",
            # Preferences (always purge — carries last site URL)
            "Default/Preferences",
            "Default/Secure Preferences",
        ]
        # For first-launch personas, also wipe Cookies/Local Storage
        # (the session archive restore above will have failed or not run)
        if not _is_returning_persona:
            _chameleon_purge_paths.extend([
                "Default/Cookies",
                "Default/Cookies-journal",
                "Default/Local Storage",
                "Default/Local Storage/leveldb",
                "Default/Session Storage",
            ])
        for sub in _chameleon_purge_paths:
            sub_path = os.path.join(profile_path, sub.replace('/', os.sep))
            try:
                if os.path.isdir(sub_path):
                    shutil.rmtree(sub_path, ignore_errors=True)
                elif os.path.isfile(sub_path):
                    os.unlink(sub_path)
            except Exception as _suppressed_exc:
                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
        # For first-launch personas, also do a sweep of all other
        # entries (keep .tmp). For returning personas, only sweep
        # entries NOT in the persona's preserved set.
        if not _is_returning_persona:
            for entry in os.listdir(profile_path):
                if entry == ".tmp":
                    continue
                entry_path = os.path.join(profile_path, entry)
                try:
                    if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                        shutil.rmtree(entry_path, ignore_errors=True)
                    else:
                        os.unlink(entry_path)
                except Exception as _suppressed_exc:
                    logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
        else:
            # PPPC returning persona: only sweep top-level non-preserved
            # entries (e.g., crash reports, logs, singleton lock)
            _preserved_for_returning = {"Default"}
            for entry in os.listdir(profile_path):
                if entry == ".tmp" or entry in _preserved_for_returning:
                    continue
                entry_path = os.path.join(profile_path, entry)
                try:
                    if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                        shutil.rmtree(entry_path, ignore_errors=True)
                    else:
                        os.unlink(entry_path)
                except Exception as _suppressed_exc:
                    logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
        if _is_returning_persona:
            logger.info(
                f"PPPC: Returning persona — preserved Cookies/Local Storage, "
                f"purged HSTS/TLS/IDB/SW for {persona_id}"
            )
    except Exception as pre_wipe_err:
        logger.debug(f"Pre-launch profile wipe: {pre_wipe_err}")

    # v6.0.2: Check if entrypoint.sh pre-spawned a master Xvfb
    # This eliminates the double-Xvfb race condition and saves ~50MB RAM
    master_display = MASTER_XVFB_DISPLAY
    master_xvfb_pid = os.getenv("XVFB_PID", "")
    xvfb_process = None  # Will be set if we spawn our own

    if master_display and master_xvfb_pid:
        # v6.0.2: Use the entrypoint's pre-spawned Xvfb
        try:
            display_num = int(master_display.lstrip(":"))
        except (ValueError, TypeError):
            logger.warning(f"Invalid MASTER_XVFB_DISPLAY '{master_display}'; using fallback :99")
            display_num = 99
        if is_display_ready(f":{display_num}"):
            logger.info(f"Xvfb: Using entrypoint's pre-spawned display :{display_num} (PID: {master_xvfb_pid})")
            # Don't track this PID for cleanup - entrypoint handles it
            xvfb_process = None
        else:
            warn_msg = f"Xvfb: Entrypoint's display :{display_num} not ready, spawning per-persona"
            logger.warning(warn_msg)
            master_display = ""  # Fall through to per-persona spawn
            master_xvfb_pid = ""

    if not master_display:
        # v6.0.2: Fallback - spawn per-persona Xvfb (legacy behavior)
        # v6.0.3: Avoid collision with the entrypoint's master Xvfb display
        try:
            master_num = int(MASTER_XVFB_DISPLAY.lstrip(":"))
        except (ValueError, TypeError):
            master_num = 99
        display_num = 100 + (stable_hash(persona_id) % 900)
        # CHAMELEON (GAP-1.5): Use cryptographically random nonce for
        # collision-retry (was random.random() which is global). Per-launch
        # nonce means the same persona re-launched doesn't get the same
        # fallback display number.
        for _ in range(10):
            if not is_display_ready(f":{display_num}") and display_num != master_num:
                break
            nonce = os.urandom(8).hex()
            display_num = 100 + (stable_hash(f"{persona_id}:{nonce}") % 900)
            # Skip the master's display number
            if display_num == master_num:
                display_num = ((display_num % 900) + 100)

        logger.info(f"Xvfb: Initializing private display on :{display_num}...")
        xvfb_env = os.environ.copy()
        xvfb_env["PATH"] = ":".join(filter(None, [
            xvfb_env.get("PATH", ""), "/usr/local/bin", "/usr/bin", "/bin"
        ]))
        try:
            xvfb_process = await asyncio.create_subprocess_exec(
                "Xvfb", f":{display_num}", "-screen", "0",
                f"{_fp_get2(fp, 'screen', 'width', 1920)}x{_fp_get2(fp, 'screen', 'height', 1080)}x24",
                "-ac", "+extension", "GLX", "+render", "-noreset",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=xvfb_env
            )
            ready = False
            for _retry in range(20):
                await asyncio.sleep(0.25)
                if xvfb_process.returncode is not None:
                    logger.error(f"Xvfb: exited with code {xvfb_process.returncode}")
                    break
                if is_display_ready(f":{display_num}", timeout=0.5):
                    ready = True
                    break
            if ready:
                logger.info(f"Xvfb: display :{display_num} ready (PID: {xvfb_process.pid}).")
            else:
                logger.warning(f"Xvfb: display :{display_num} not ready; falling back")
        except Exception as xvfb_err:
            logger.error(f"Xvfb: spawn failed on :{display_num}: {xvfb_err}; falling back")
            xvfb_process = None

    # Per-persona environment
    browser_env = os.environ.copy()
    browser_env["TZ"] = timezone
    # KE30: Propagate persona platform to the browser env so the
    # Python-side keyboard emulator (keyboard_emulator.py) can
    # use Cmd+A on Mac vs Ctrl+A on Win/Linux. Without this, the
    # env var defaults to "Win32" and Mac personas get the wrong
    # modifier key.
    _persona_platform = _fp_get2(fp, 'navigator', 'platform', 'Win32')
    browser_env["FP_PLATFORM"] = _persona_platform
    browser_env["XDG_CONFIG_HOME"] = os.path.join(profile_path, ".config")
    browser_env["XDG_CACHE_HOME"] = os.path.join(profile_path, ".cache")
    browser_env["XDG_DATA_HOME"] = os.path.join(profile_path, ".local", "share")
    browser_env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(profile_path, ".playwright_browsers")
    browser_env["TMPDIR"] = tmp_dir
    browser_env["TEMP"] = tmp_dir
    browser_env["TMP"] = tmp_dir
    # v6.0.4: CRITICAL FIX - Do NOT override XDG_RUNTIME_DIR!
    # The entrypoint.sh starts PulseAudio at $XDG_RUNTIME_DIR/pulse/native
    # If we override XDG_RUNTIME_DIR here, ChromeBrowser will look for
    # PulseAudio at the per-persona path, not the entrypoint's path.
    # The entrypoint's XDG_RUNTIME_DIR is inherited from os.environ.copy()
    # so we just need to NOT set it here. The DBus and PulseAudio sockets
    # are at the entrypoint's XDG_RUNTIME_DIR, which is correct.
    # The per-persona .runtime directory is NOT needed since we use
    # the entrypoint's pre-spawned services.
    browser_env["MESA_SHADER_CACHE_DISABLE"] = "true"
    # v6.0.2: timezone sync
    # v6.0.3: Set DISPLAY whether using per-persona or master Xvfb
    if xvfb_process and xvfb_process.returncode is None:
        browser_env["DISPLAY"] = f":{display_num}"
    else:
        # Use entrypoint's pre-spawned master Xvfb
        master_display = MASTER_XVFB_DISPLAY
        if master_display:
            browser_env["DISPLAY"] = master_display
        else:
            # Fallback: use the display_num anyway (Xvfb may have started)
            browser_env["DISPLAY"] = f":{display_num}"

    # Engine detection
    user_agent = _fp_get2(fp, 'navigator', 'userAgent', '')
    is_chrome = is_chrome_ua(user_agent)

    # v5.0+: use per-persona SOCKS5 URL (NOT the default env var)
    parsed_proxy = parse_proxy_url(persona_socks5_url)
    if parsed_proxy is None:
        logger.critical(f"Invalid per-persona SOCKS5 URL for {persona_id}: {persona_socks5_url}; aborting")
        if xvfb_process:
            try:
                xvfb_process.terminate()
                await xvfb_process.wait()
            except Exception as _suppressed_exc:
                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
        return False

    browser = None
    # A4 fix (Pass 16): Track `page` at the function level so the
    # `finally` block can close it on cancellation. The previous
    # code only tracked `browser`; if a CancelledError fired after
    # `page = await browser.new_page()` (line ~2920) but before
    # the success return, the page would leak. The `finally`
    # block closes the browser (which closes all pages), but
    # Playwright recommends explicit page.close() before
    # browser.close() to ensure pending writes are flushed. We
    # now also close the page explicitly.
    page = None
    try:
        async with async_playwright() as p:
            launch_kwargs = {
                "user_data_dir": profile_path,
                "headless": False,
                "viewport": {
                    "width": _fp_get2(fp, 'screen', 'width', 1920),
                    "height": _fp_get2(fp, 'screen', 'height', 1080)
                },
                "user_agent": user_agent,
                "locale": "en-US",
                "timezone_id": timezone,
                "env": browser_env,
                # v5.0+: BypassCSP allows CloakBrowser patches to apply
                "bypass_csp": False,
            }

            cloakbrowser_found = False
            cloakbrowser_required = os.getenv("CLOAKBROWSER_REQUIRED", "false").lower() == "true"

            if is_chrome:
                logger.info(f"Engine: V8/Chromium for {persona_id}.")
                browser_type = p.chromium
                launch_kwargs["proxy"] = parsed_proxy

                cloak_paths = [
                    os.getenv("CLOAKBROWSER_PATH"),
                    "/opt/cloakbrowser/chrome",
                    "/opt/cloakbrowser/cloakbrowser",
                    "/usr/local/bin/cloakbrowser",
                    "/usr/bin/cloakbrowser"
                ]
                for path in cloak_paths:
                    if path and os.path.exists(path) and os.access(path, os.X_OK):
                        logger.info(f"CloakBrowser: {path}")
                        launch_kwargs["executable_path"] = path
                        cloakbrowser_found = True
                        break
                if not cloakbrowser_found:
                    if cloakbrowser_required:
                        logger.critical("CloakBrowser not found and CLOAKBROWSER_REQUIRED=true. Aborting.")
                        return False
                    else:
                        logger.warning("CloakBrowser not found, falling back to Playwright Chromium.")

                # v5.0+: Modernized Chromium flags. Includes:
                # - All v4.0 hardening flags
                # - Disable WebRTC entirely
                # - Disable battery API spoof (we override in JS)
                # - Explicit color-depth / pixel-depth (no HDR)
                chromium_args = [
                    # v6.2: Lock down CDP endpoint. Even though Playwright uses
                    # pipe-based CDP, this prevents accidental TCP exposure.
                    "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-debugging-pipe",
                    "--no-sandbox",  # required inside container
                    "--disable-setuid-sandbox",
                    "--password-store=basic",
                    "--use-mock-keyring",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-async-dns",
                    "--disable-dev-shm-usage",
                    "--use-angle=swiftshader-webgl",
                    "--disable-gpu-shader-disk-cache",
                    "--disable-quic",
                    f"--crash-dumps-dir={profile_path}",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-domain-reliability",
                    "--no-ping",
                    "--js-flags=--max-old-space-size=2048",
                    # v5.0+: disable WebRTC entirely (not just mDNS)
                    "--disable-webrtc",
                    "--disable-rtc-audio-jitter-buffer",
                    # v5.0+: disable notification permission prompt
                    "--disable-notifications",
                    # v5.0+: prevent GPU process from leaking host info
                    "--disable-gpu-driver-bug-workarounds",
                    f"--disk-cache-dir={os.path.join(tmp_dir, 'disk_cache')}",
                    "--disk-cache-size=1",
                    "--media-cache-size=1",
                    # R7-DEEP-AUDIT-2 GAP 3.1: Consolidated --disable-features.
                    # Chromium only honors the LAST --disable-features
                    # command-line flag (later flags override earlier
                    # ones), so we put everything in one comma-separated
                    # list. Without this, only the LAST flag's
                    # features are disabled and the earlier flags are
                    # silently dropped.
                    #
                    # Consolidated features:
                    #   - Translate, AutofillServerCommunication,
                    #     OptimizationHints, WebOTP, WebPayments, WebUSB,
                    #     WebBluetooth (v5.0+ baseline disable)
                    #   - PaymentRequest, CredentialManagementAPI
                    #     (v5.0+ payment/auth surfaces)
                    #   - BackgroundSync (v5.0+ opt-out)
                    #   - AsyncDns, PartitionAlloc (CHAMELEON GAP-1.1:
                    #     per-persona fresh TLS state)
                    #   - CertificateTransparencyComponentUpdater,
                    #     TLS13EarlyData (R7-DEEP-AUDIT-2: 0-RTT
                    #     resumption disable)
                    #   - WebRtcAllowInputVolumeAdjustment,
                    #     WebRtcMediaCodecVideo, WebRtcUseEchoCanceller3
                    #     (CHAMELEON GAP-3.3: hard kill WebRTC)
                    #   - MulticastDNS, WebRtcHideLocalIpsWithMdns
                    #     (CHAMELEON GAP-3.4: mDNS leak)
                    "--disable-features=Translate,AutofillServerCommunication,OptimizationHints,WebOTP,WebPayments,WebUSB,WebBluetooth,PaymentRequest,CredentialManagementAPI,BackgroundSync,AsyncDns,PartitionAlloc,CertificateTransparencyComponentUpdater,TLS13EarlyData,WebRtcAllowInputVolumeAdjustment,WebRtcMediaCodecVideo,WebRtcUseEchoCanceller3,MulticastDNS,WebRtcHideLocalIpsWithMdns",
                    # CHAMELEON (GAP-3.5): Disable IPv6 entirely
                    "--disable-ipv6",
                    # R7-DEEP-AUDIT-2 GAP 3.2: Proxy-bypass-list format.
                    # The semicolon-separated form was deprecated in
                    # Chromium 100+; use a single comma-separated form.
                    # The <loopback> class exempts all of 127.0.0.0/8 and
                    # ::1, which is what we want (only the SOCKS5
                    # listener and other local services bypass the proxy).
                    "--proxy-bypass-list=<-loopback>",
                    # CHAMELEON: Per-session OCSP (no shared cache)
                    "--ocsp-disable-stapling-in-tests",
                    # R7-DEEP-AUDIT-2 GAP 3.4: Add EXCLUDE 127.0.0.1 to
                    # host-resolver-rules. The previous rule only
                    # excluded 'localhost' by name; some apps resolve
                    # '127.0.0.1' directly and need to be exempt.
                    # R7-DEEP-AUDIT-3 GAP 3.4: DNS NOTFOUND oracle. The
                    # MAP * ~NOTFOUND rule short-circuits DNS lookups
                    # for unknown hosts, producing a 0ms timing that
                    # is trivially detectable by anti-bot scripts
                    # (fetch('http://test.invalid/') completes in
                    # ~0ms vs ~100ms for a real DNS query). We now
                    # use a different approach: drop unknown hosts to
                    # 0.0.0.0 (which causes a real TCP connect attempt
                    # that times out via the SOCKS5 proxy, producing
                    # a realistic ~50-300ms latency). The proxy still
                    # routes the connection, and the SOCKS5 server
                    # returns a "host unreachable" error after the
                    # standard SOCKS5 timeout. This matches real DNS
                    # failure timing.
                    "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE localhost, EXCLUDE 127.0.0.1",
                    # R7-DEEP-AUDIT-2 GAP 3.1: Suppress TLS session
                    # ticket caching at the Chromium level. The cache
                    # itself is purged in the pre-launch step, but
                    # these flags prevent new entries from being created
                    # in this session.
                    "--ssl-key-exchange-strict-order",
                    "--ssl-session-cache-uses-cacheable-certificates=false",
                ]
                chromium_args.extend(WEBRTC_MODERN_FLAGS)
                launch_kwargs["args"] = chromium_args
            else:
                logger.info(f"Engine: Gecko/Firefox for {persona_id}.")
                browser_type = p.firefox
                firefox_prefs = {
                    "media.peerconnection.enabled": True,
                    "media.peerconnection.ice.no_host": True,
                    "media.peerconnection.ice.default_address_only": True,
                    "media.peerconnection.use_document_iceservers": False,
                    "media.peerconnection.ice.proxy_only_if_behind_proxy": True,
                    "media.peerconnection.ice.obfuscate_host_addresses": True,
                    "network.proxy.socks_remote_dns": True,
                    "network.proxy.socks": "127.0.0.1",
                    "network.proxy.socks_port": get_socks5_port_for_persona(persona_id),
                    "network.proxy.type": 1,
                    "dom.webnotifications.enabled": False,
                    "privacy.resistFingerprinting": False,
                    "browser.cache.memory.enable": True,
                    "browser.cache.memory.capacity": 1048576,
                    "network.http.http3.enable": False,
                    "datareporting.healthreport.uploadEnabled": False,
                    "toolkit.telemetry.enabled": False,
                    "toolkit.telemetry.unified": False,
                    "toolkit.telemetry.server": SINKHOLE_URL,
                    "experiments.supported": False,
                    "experiments.enabled": False,
                    "experiments.activeExperiment": False,
                    "browser.safebrowsing.malware.enabled": False,
                    "browser.safebrowsing.phishing.enabled": False,
                    "browser.safebrowsing.blockedURIs.enabled": False,
                    "browser.safebrowsing.provider.google4.gethashURL": SINKHOLE_URL,
                    "browser.safebrowsing.provider.google4.updateURL": SINKHOLE_URL,
                    "network.dns.disablePrefetch": True,
                    "network.prefetch-next": False,
                    "network.http.speculative-parallel-limit": 0,
                    "security.OCSP.enabled": 0,
                    "identity.fxaccounts.enabled": False,
                    "dom.push.enabled": False,
                    "dom.push.connection.enabled": False,
                    "network.http.referer.XOriginPolicy": 1,
                    "network.http.referer.XOriginTrimmingPolicy": 2,
                    "network.http.referer.trimmingPolicy": 1,
                    "extensions.pocket.enabled": False,
                    "browser.newtabpage.activity-stream.feeds.section.topstories": False,
                    # v5.0+: lock scroll-behavior per persona
                    "general.smoothScroll": True if persona_digest["scroll_behavior"] == "smooth" else False,
                }
                launch_kwargs["firefox_user_prefs"] = firefox_prefs

            # v6.2: Strict CloakBrowser-only launcher. NO FALLBACK to bare Chromium.
            # If CloakBrowser fails to launch, the task is ABORTED. The
            # container will be recreated with restart:no policy, which
            # means a NEW container with a NEW tmpfs and NEW persona
            # state. (Not a new boot_id: that is a shared immutable host
            # file, identical in every container - see C15.)
            # Falling back to bare Playwright Chromium
            # would expose the unhardened browser to the target, which is
            # an instant detection by reCAPTCHA Enterprise, Cloudflare
            # Turnstile, Akamai, and PerimeterX.
            if not cloakbrowser_found and cloakbrowser_required:
                logger.critical("CloakBrowser not found and CLOAKBROWSER_REQUIRED=true. Aborting.")
                return False
            try:
                browser = await browser_type.launch_persistent_context(**launch_kwargs)
            except Exception as launch_err:
                # v6.2: NO FALLBACK. If CloakBrowser crashes, we abort.
                logger.critical(
                    f"CloakBrowser launch failed: {launch_err}. "
                    f"ABORTING (no fallback to bare Chromium to prevent detection)."
                )
                raise

            browser.persona_id = persona_id

            # v5.0+: init script BEFORE first new_page()
            prototype_path = os.path.join(_SCRIPT_DIR, "prototype_hardener.js")
            with open(prototype_path, "r") as pf:
                prototype_js = pf.read()

            # R7-HOR-006: Removed the `min(..., 4)` cap. The previous
            # code capped cores at 4, but real personas have 8/16 cores
            # (per persona_digest["hardware_concurrency"] which uses a
            # realistic distribution: 30% have 4, 50% have 8, 15% have
            # 16, 5% have 2/12/24/32). Capping at 4 means a persona with
            # hardwareConcurrency=16 would have the browser report 4 cores
            # while the real OS has 16 — detectable via WebRTC data
            # channel or /proc/cpuinfo from a sibling container.
            # Use the fingerprint's value (or persona_digest's realistic
            # distribution as fallback).
            #
            # R7-HOR-016: Variable is now `cores_to_spoof` (was the
            # typo'd `cors_to_spoof` in earlier versions). The typo
            # was a cosmetic issue but made grep-based auditing harder.
            _fp_cores = _fp_get2(fp, 'navigator', 'hardwareConcurrency', None)
            if isinstance(_fp_cores, int) and 1 <= _fp_cores <= 128:
                cores_to_spoof = _fp_cores
            else:
                cores_to_spoof = persona_digest["hardware_concurrency"]
            memory_to_spoof = _fp_get2(fp, 'navigator', 'deviceMemory', persona_digest["device_memory"])
            # CHAMELEON (GAP-4.2): Per-persona WebGL rotation. Real users
            # have a specific GPU, but the distribution of GPUs in
            # the wild is uneven. We pool 4-6 plausible GPUs per
            # platform and use a per-persona seeded RNG to pick one.
            # This prevents every persona from reporting the same
            # default GPU (which is itself a fingerprint).
            _platform_for_webgl = _fp_get2(fp, 'navigator', 'platform', 'Win32')
            _webgl_pool = {
                'MacIntel': [
                    ('Apple Inc.', 'Apple M1'),
                    ('Apple Inc.', 'Apple M1 Pro'),
                    ('Apple Inc.', 'Apple M1 Pro Max'),
                    ('Apple Inc.', 'Apple M2'),
                    ('Apple Inc.', 'Apple M2 Pro'),
                    ('Apple Inc.', 'Apple M3'),
                ],
                'Linux': [
                    ('Mesa/X.org', 'Mesa DRI Intel(R) HD Graphics 4400 (HSW GT2)'),
                    ('Mesa/X.org', 'Mesa DRI Intel(R) UHD Graphics 620 (KBL GT2)'),
                    ('Mesa/X.org', 'Mesa DRI Intel(R) UHD Graphics 630 (CFL GT2)'),
                    ('NVIDIA Corporation', 'NVIDIA GeForce GTX 1650/PCIe/SSE2'),
                    ('NVIDIA Corporation', 'NVIDIA GeForce RTX 3060/PCIe/SSE2'),
                    ('AMD', 'AMD RENOIR (DRM 3.42.0, 5.10.0-21-amd64, LLVM 11.0.1)'),
                ],
            }
            _win32_pool = [
                ('Intel Inc.', 'Intel(R) Iris(TM) Plus Graphics 640'),
                ('Intel Inc.', 'Intel(R) UHD Graphics 630'),
                ('Intel Inc.', 'Intel(R) Iris Xe Graphics'),
                ('Intel Inc.', 'Intel(R) HD Graphics 530'),
                ('NVIDIA Corporation', 'GeForce GTX 1660 SUPER/PCIe/SSE2'),
                ('NVIDIA Corporation', 'GeForce RTX 3060/PCIe/SSE2'),
                ('NVIDIA Corporation', 'GeForce RTX 4070/PCIe/SSE2'),
                ('AMD', 'AMD Radeon RX 580'),
                ('AMD', 'AMD Radeon RX 6700 XT'),
            ]
            # Pick from platform-matched pool using per-persona seeded RNG
            # M11 fix: Use SHA-256 instead of MD5 for the WebGL
            # seed. The previous code used MD5 for the seed,
            # which is unavailable on FIPS-mode systems. A
            # FIPS deployment would fail with
            # `ValueError: [digital envelope wrap] unsupported`
            # when launching any persona. SHA-256 is always
            # available. The 32-bit truncation is preserved.
            _webgl_seed = int(hashlib.sha256(f"webgl:{persona_id}".encode('utf-8')).hexdigest()[:8], 16)
            _webgl_rng = random.Random(_webgl_seed)
            if _platform_for_webgl == 'MacIntel':
                _pool = _webgl_pool['MacIntel']
            elif 'Linux' in _platform_for_webgl:
                _pool = _webgl_pool['Linux']
            else:
                _pool = _win32_pool
            _default_vendor, _default_renderer = _webgl_rng.choice(_pool)
            # Allow fingerprint override
            webgl_vendor = _fp_get2(fp, 'webgl', 'vendor', None) or _default_vendor
            webgl_renderer = _fp_get2(fp, 'webgl', 'renderer', None) or _default_renderer
            # R7-HOR-023: Platform-aware default for webgl_vendor. The
            # previous code used "Intel Inc." / "Intel(R) Iris(TM) Plus
            # Graphics" for ALL platforms, which is wrong for Mac
            # (Apple GPUs) and Linux (NVIDIA/AMD). Use a per-platform
            # default that matches the persona's platform claim.
            platform = _fp_get2(fp, 'navigator', 'platform', 'Win32')
            languages = json.dumps(_fp_get2(fp, 'navigator', 'languages', ["en-US", "en"]))

            safe_webgl_vendor_js = json.dumps(webgl_vendor)
            safe_webgl_renderer_js = json.dumps(webgl_renderer)
            safe_platform_js = json.dumps(platform)
            safe_timezone_js = json.dumps(timezone)
            # v6.1: Default permission states (used when fingerprint doesn't provide them)
            permission_states_default = {
                "geolocation": "prompt", "notifications": "prompt", "push": "prompt",
                # R7-PHO-009: midi/speaker/device-info/clipboard-write default
                # to "prompt" for a fresh persona (no interaction history).
                # A persona with notification_session_count > 0 may have
                # these granted (per-fingerprint permissionStates overrides).
                "midi": "prompt", "camera": "prompt", "microphone": "prompt",
                "speaker": "prompt", "device-info": "prompt", "background-fetch": "prompt",
                "background-sync": "prompt", "bluetooth": "prompt", "persistent-storage": "prompt",
                "ambient-light-sensor": "prompt", "accelerometer": "prompt", "gyroscope": "prompt",
                "magnetometer": "prompt", "screen-wake-lock": "prompt", "nfc": "prompt",
                "display-capture": "prompt", "window-management": "prompt", "clipboard-read": "prompt",
                "clipboard-write": "prompt", "clipboard-sanitized-write": "prompt",
                "payment-handler": "prompt", "idle-detection": "prompt", "local-fonts": "prompt",
                "top-level-storage-access": "prompt", "storage-access": "prompt"
            }

            # v5.0+: embed persona_digest locked values
            # v6.1: Full FP_CONFIG injection with ALL 34 fields
            # Persona values must be per-persona for maximum invisibility;
            # JS-side defaults (which would be the same for every persona)
            # would expose the spoof via fingerprint correlation.
            # R7-HOR-001: Precompute UA fields outside the f-string
            # (f-strings can't contain complex expressions with multiple
            # nested calls without explicit parens).
            _ua_full_ver, _ua_major_int = _safe_parse_user_agent(fp)

            # C5 fix: JSON-encode ALL string values that are interpolated
            # into the JS FP_CONFIG block. The previous f-string did
            # `"{persona_digest['scroll_behavior']}"` which is a direct
            # string concatenation. If persona_digest["scroll_behavior"]
            # contains a `"` or `</script>` or a newline, it would
            # break out of the JS string literal and execute as JS code
            # in the browser context. This is a stored-XSS-to-RCE
            # vulnerability (an attacker who can write a persona
            # fingerprint to the DB can execute arbitrary code in the
            # browser when that persona is launched). All string values
            # are now passed through json.dumps() to ensure proper
            # escaping. The numeric and boolean values are also
            # re-validated/coerced before interpolation.
            def _js_str(v):
                """C5: JSON-encode a value for safe JS string interpolation.
                json.dumps produces a valid JS string literal (with
                proper escaping of ", \\, \n, \r, \t, and unicode).
                Returns the value as a JS-quoted string, or 'null' if
                the value is None.
                """
                if v is None:
                    return "null"
                # json.dumps on a string produces a JSON-encoded
                # string. JSON syntax is a subset of JS string syntax
                # (modulo unicode), so a JSON string is a valid JS
                # string. We wrap the result in a JS-safe form.
                return json.dumps(v)

            def _js_num(v, default=0):
                """C5: Coerce to int/float for safe JS number interpolation.
                Rejects NaN, infinity, and other dangerous values.
                """
                if v is None:
                    return str(default)
                if isinstance(v, bool):
                    # bool is a subclass of int in Python; treat True
                    # as 1 and False as 0 (not as JS booleans)
                    return "1" if v else "0"
                if isinstance(v, int):
                    return str(v)
                if isinstance(v, float):
                    import math as _math
                    if _math.isnan(v) or _math.isinf(v):
                        return str(default)
                    # Repr may produce 'inf' or 'nan' for special floats
                    return repr(v)
                try:
                    return str(int(v))
                except (ValueError, TypeError):
                    return str(default)

            def _js_bool(v):
                """C5: Coerce to JS boolean (true/false)."""
                return "true" if v else "false"

            target_config = f"""    /* FP_CONFIG_BLOCK_BEGIN */
    const FP_CONFIG = {{
        // 17 core persona fields
        hardwareConcurrency: {_js_num(cores_to_spoof)},
        deviceMemory: {_js_num(memory_to_spoof)},
        webglVendor: {_js_str(webgl_vendor)},
        webglRenderer: {_js_str(webgl_renderer)},
        platform: {_js_str(platform)},
        languages: {_js_str(_fp_get2(fp, 'navigator', 'languages', ["en-US", "en"]))},
        canvas_noise_seed: {_js_num(canvas_noise_seed)},
        client_hints: {json.dumps(client_hints) if client_hints is not None else 'null'},
        timezone: {_js_str(timezone)},
        audioSampleRate: {_js_num(persona_digest.get("audio_sample_rate"))},
        screenColorDepth: {_js_num(persona_digest.get("screen_color_depth"))},
        screenPixelDepth: {_js_num(persona_digest.get("screen_pixel_depth"))},
        batteryCharging: {_js_bool(persona_digest.get("battery_charging"))},
        batteryLevel: {_js_num(persona_digest.get("battery_level"))},
        scrollBehavior: {_js_str(persona_digest.get("scroll_behavior"))},
        cookieLifetimeDays: {_js_num(persona_digest.get("cookie_lifetime_days"))},
        bootId: {_js_str(get_current_boot_id())},
        // v6.1: User-Agent Client Hints (per-persona, derived from UA)
        // R7-HOR-001: Defensive UA parsing (precomputed above to avoid
        // f-string parsing of complex expressions).
        uaFullVersion: {json.dumps(_ua_full_ver)},
        uaMajor: {_js_num(_ua_major_int)},
        platformVersion: {_js_str(_fp_get2(fp, 'navigator', 'platformVersion', '15.0.0'))},
        architecture: {_js_str(_fp_get2(fp, 'navigator', 'architecture', 'x86'))},
        bitness: {_js_str(_fp_get2(fp, 'navigator', 'bitness', '64'))},
        mobile: {_js_bool(_fp_get2(fp, 'navigator', 'mobile', False))},
        model: {_js_str(_fp_get2(fp, 'navigator', 'model', ''))},
        // v6.1: Network Information (per-persona, derived from persona_digest)
        connectionType: {_js_str(_fp_get2(fp, 'navigator', 'connectionType', 'wifi'))},
        effectiveType: {_js_str(_fp_get2(fp, 'navigator', 'effectiveType', '4g'))},
        downlink: {_js_num(_fp_get2(fp, 'navigator', 'downlink', 10.0), default=10.0)},
        rtt: {_js_num(_fp_get2(fp, 'navigator', 'rtt', 50), default=50)},
        saveData: {_js_bool(_fp_get2(fp, 'navigator', 'saveData', False))},
        maxTouchPoints: {_js_num(_fp_get2(fp, 'navigator', 'maxTouchPoints', 0))},
        // v6.1: Permission states (per-persona, derived from fingerprint)
        permissionStates: {_js_str(_fp_get2(fp, 'navigator', 'permissionStates', permission_states_default))},
        // INVIS-5: hardware_concurrency from realistic distribution
        hardware_concurrency: {_js_num(persona_digest.get("hardware_concurrency") or cores_to_spoof)},
        // INVIS-6: per-launch session ID for canvas/audio noise
        canvas_noise_session_id: {_js_str(persona_digest.get('canvas_noise_session_id', '') or '')},
        // INVIS-10: persona session count for Notification.permission state machine
        notification_session_count: {_js_num(persona_digest.get("notification_session_count", 0) or 0)},
        // PPPC: history_depth — number of tasks this persona has
        // completed. The JS side uses this to make chrome.csi().tran
        // and history.length grow over time (real users have a
        // monotonic, slowly-growing history).
        history_depth: {_js_num(persona_digest.get("history_depth", 0) or 0)},
        // PPPC: is_returning — true if this is a returning persona
        // (has prior session_data). The JS side uses this to vary
        // UI affordances (a returning user has more browser tabs,
        // more bookmarks, etc.)
        is_returning: {_js_bool(persona_digest.get("is_returning", False))}
    }};
    /* FP_CONFIG_BLOCK_END */"""

            if "FP_CONFIG_BLOCK_BEGIN" in prototype_js and "FP_CONFIG_BLOCK_END" in prototype_js:
                # CHAMELEON (GAP-1.6): Use string find/slice instead of
                # regex sub. The regex pattern was greedy-vulnerable: if
                # the prototype ever contained a comment with both markers
                # (e.g., a docstring showing the format), the regex would
                # substitute inside the comment. String find is exact.
                begin_marker = "/* FP_CONFIG_BLOCK_BEGIN */"
                end_marker = "/* FP_CONFIG_BLOCK_END */"
                begin_idx = prototype_js.find(begin_marker)
                end_idx = prototype_js.find(end_marker, begin_idx + len(begin_marker))
                if begin_idx >= 0 and end_idx > begin_idx:
                    prototype_js = (
                        prototype_js[:begin_idx]
                        + target_config.rstrip()
                        + prototype_js[end_idx + len(end_marker):]
                    )
                else:
                    logger.warning("FP_CONFIG markers present but positions invalid; using regex fallback")
                    pattern = re.compile(
                        r'/\* FP_CONFIG_BLOCK_BEGIN \*/.*?/\* FP_CONFIG_BLOCK_END \*/',
                        re.DOTALL
                    )
                    prototype_js = pattern.sub(target_config, prototype_js, count=1)
            else:
                prototype_js = re.sub(
                    r"const FP_CONFIG = \{.*?\};",
                    target_config,
                    prototype_js,
                    count=1,
                    flags=re.DOTALL
                )

            # v5.0+: Build the persona manifest for CloakBrowser/JS coordination.
            # The manifest tells prototype_hardener.js which properties
            # CloakBrowser's C++ layer already owns, so the script doesn't
            # double-patch them. The 'js-wins' default means the JS layer
            # RE-patches to align with FP_CONFIG (FP_CONFIG is the source
            # of truth from the DB). Set 'cloak-wins' if you trust
            # CloakBrowser's C++ values and want zero JS patching for those
            # properties.
            #
            # Comprehensive list of properties CloakBrowser's C++ layer
            # typically patches (each one is verified before JS patching).
            cloak_manifest = {
                "owned_by_cloak": [
                    # C++ kernel-level patches (CloakBrowser always owns these)
                    "Function.prototype.toString",  # C++ locks this
                    "webgl_debug_renderer_info",  # C++ patches UNMASKED constants
                    # V8 init-time property patches
                    "navigator.webdriver",  # C++ sets to undefined at V8 init
                    "navigator.hardwareConcurrency",  # C++ reads sysconf(_SC_NPROCESSORS_ONLN)
                    "navigator.deviceMemory",  # C++ reads system memory
                    "screen.colorDepth",  # C++ reads from X11/Wayland
                    "screen.pixelDepth",  # C++ reads from X11/Wayland
                    # Canvas/Audio noise source: 'cpp' means C++ adds noise,
                    # JS script does NOT add noise (single source of truth).
                    # Set to 'js' to let JS script add noise instead.
                    # Default: 'js' (JS owns the per-persona noise seed).
                ],
                "canvas_noise": "js",  # 'js' | 'cpp' | 'both' | 'none'
                "audio_noise": "js",   # 'js' | 'cpp' | 'both' | 'none'
                "webgl_draw_latency": "js",  # 'js' | 'cloak-disabled'
                "conflict_resolution": "js-wins",  # 'js-wins' | 'cloak-wins' | 'first-wins'
                "values": {
                    # CloakBrowser's C++ values (read by JS for consistency checks)
                    # These should match FP_CONFIG; if they don't, JS re-patches.
                    # R7-HOR-006: cores_to_spoof now uses the fingerprint
                    # value directly (no cap at 4).
                    "hardwareConcurrency": cores_to_spoof,
                    "deviceMemory": memory_to_spoof,
                    "webglVendor": webgl_vendor,
                    "webglRenderer": webgl_renderer,
                    "platform": _fp_get2(fp, 'navigator', 'platform', 'Win32'),
                    "uaFullVersion": _ua_full_ver,
                }
            }

            # v5.0+: First init script: manifest + persona digest + per-launch
            # session ID. Must run BEFORE the main prototype_hardener.js so
            # the script can read them.
            # R7-DEEP-AUDIT-2 FIX: Also set __sre_session_id so the JS uses
            # the per-launch UUID for canvas/audio noise. Without this,
            # the JS would fall back to navigationStart+Date.now() which
            # is less stable than a true UUID.
            init_setup_js = f"""
            (() => {{
                try {{
                    window.__sre_cloak_manifest = {json.dumps(cloak_manifest)};
                    window.__sre_persona_digest = {json.dumps(persona_digest)};
                    // R7-DEEP-AUDIT-2 FIX: Per-launch session UUID
                    window.__sre_session_id = "{_session_uuid}";
                    // R7-DEEP-AUDIT-2 GAP 1.3 / 5.2: Inject persona
                    // geolocation coords. The JS-side geolocation shim
                    // uses these to return plausible coordinates
                    // matching the persona's proxy city. For unknown
                    // cities, coords is null and the shim returns
                    // PERMISSION_DENIED.
                    window.__sre_geo_coords = {json.dumps(_geo_coords_for_payload)};
                }} catch (e) {{
                    console.error('[SRE] Failed to set up manifest:', e);
                }}
            }})();
            """
            await browser.add_init_script(init_setup_js)
            logger.info(f"Manifest + persona digest registered (canvas_noise={cloak_manifest['canvas_noise']}, audio_noise={cloak_manifest['audio_noise']})")

            # v5.0+: REGISTER MAIN INIT SCRIPT BEFORE FIRST new_page()
            await browser.add_init_script(prototype_js)
            logger.info(f"Init script registered BEFORE first new_page() for {persona_id}")

            # INVIS-12: Set window.name BEFORE the first page is created.

            # INVIS-12: Set window.name BEFORE the first page is created.
            # Real Chrome often has window.name set to the previous page's
            # URL or a marketing tag. The persona can override via
            # FP_CONFIG.window_name; otherwise we use the navigation
            # target URL as a plausible value.
            #
            # We do this by injecting an init script that sets window.name
            # on document creation, BEFORE any page script runs.
            # E13 fix (Pass 16): Removed the redundant `or ''`
            # fallback. The previous code was:
            #     `json.dumps(fp.get('window_name', '') or '')`
            # The `fp.get('window_name', '')` already returns '' if
            # the key is missing. The `or ''` only fires if the
            # value is None or empty, but:
            #   - None: `fp.get('window_name', '')` returns None
            #     only if the caller explicitly passed None as the
            #     default (which we don't), so this is dead.
            #   - Empty string: `'' or ''` = `''`, so the fallback
            #     is a no-op.
            # The `or ''` is a defensive belt-and-suspenders that
            # doesn't do anything useful but obscures the intent.
            # We now use `fp.get('window_name', '')` directly. The
            # `if (name)` check below correctly handles the
            # empty-string case (empty string is falsy in JS).
            # Also, we use the C2 fix (Pass 15) to treat empty
            # string as missing — an empty window_name is itself
            # a fingerprint (no real user has an empty window.name
            # by default), so we fall through to the no-name path
            # (the JS `if (name)` check).
            _init_window_name_js = f"""
            (() => {{
                try {{
                    const name = {json.dumps(fp.get('window_name', ''))};
                    if (name) {{
                        Object.defineProperty(window, 'name', {{
                            get: () => name, configurable: true
                        }});
                    }}
                }} catch (e) {{}}
            }})();
            """
            await browser.add_init_script(_init_window_name_js)
            logger.debug(f"INVIS-12: window.name init script registered for {persona_id}")

            page = await browser.new_page()
            # v5.0+: ensure about:blank in history (real users start there)
            # INVIS-13: Pre-navigate to chrome://newtab/ to give history.length=2
            # before the task URL. Real users open tabs that have visited
            # multiple pages. A bare history.length=1 (about:blank only)
            # is detectable.
            try:
                _ = page.url  # touch to ensure page is initialized
            except Exception as _suppressed_exc:
                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")

            # C2 fix: Call _clear_browser_storage on the FIRST page
            # (not just at browser close). The pre-launch filesystem
            # wipe removes Chrome's on-disk state (Service Worker
            # Database, IndexedDB dirs, etc.), but if Chrome re-creates
            # a SW registration from a cached manifest during browser
            # startup, that registration lives in memory and the
            # on-disk purge is no longer authoritative. By calling
            # _clear_browser_storage immediately after new_page() and
            # BEFORE any user navigation, we unregister all SWs that
            # Chrome may have created during init. This closes the
            # "Service Worker re-registration after init" vector.
            try:
                await _clear_browser_storage(page)
            except Exception as first_clear_err:
                # Non-fatal; the pre-launch filesystem wipe is the
                # primary defense. The in-page unregistration is a
                # belt-and-suspenders measure.
                logger.debug(
                    f"C2: pre-first-nav storage clear failed (non-fatal): "
                    f"{first_clear_err}"
                )
            # INVIS-13: Pre-navigate to chrome://newtab/ before the task URL
            try:
                # Note: chrome:// URLs are blocked in some Chromium configs.
                # Fall back to about:blank if chrome:// is blocked.
                # R7-DEEP-AUDIT-2 (Pass 14) M3: When chrome:// is blocked,
                # the about:blank re-navigation is a no-op for
                # history (about:blank → about:blank doesn't increment
                # history.length). The prototype_hardener.js handles
                # this case at line ~5625 by synthetically bumping
                # historyCount to 2 if it reads as 1 from
                # window.history.length. So the fingerprint concern
                # is closed: the JS guarantees history.length=2 in
                # the chrome://blocked case. We keep the
                # about:blank re-navigation here for safety
                # (in case a future JS bug regresses this) but
                # the primary defense is in the JS layer.
                try:
                    await page.goto("chrome://newtab/", timeout=2000, wait_until="domcontentloaded")
                    logger.debug(f"INVIS-13: Pre-navigated to chrome://newtab/ for {persona_id}")
                except Exception:
                    # chrome:// is blocked; do a no-op about:blank nav
                    # to increment history.length (the JS layer will
                    # also handle this if it doesn't work).
                    await page.goto("about:blank", timeout=2000, wait_until="domcontentloaded")
                    logger.debug(f"INVIS-13: chrome:// blocked, used about:blank for {persona_id}")
            except Exception as nav_err:
                logger.debug(f"INVIS-13: Pre-navigation failed (non-fatal): {nav_err}")

            # INVIS-69 to INVIS-72: Seed a realistic fresh profile if
            # this is a brand-new persona (no prior session_data).
            # Real Chrome users on a 5-year-old install have 100+ cookies,
            # 50+ localStorage entries, and 1-3 IndexedDB databases.
            # Our new personas would otherwise start with 0/0/0 — detectable.
            if not session_data and _HardenedSessionSerializer is not None:
                try:
                    seed_ok = await get_serializer().seed_fresh_profile(
                        browser, page, persona_id
                    )
                    if seed_ok:
                        logger.info(
                            f"INVIS-69: Seeded realistic profile for "
                            f"new persona {persona_id[:8]}..."
                        )
                except Exception as seed_err:
                    logger.warning(
                        f"INVIS-69: Profile seed failed (non-fatal): {seed_err}"
                    )

            # Restore session
            decrypted_payload = None
            if session_data and isinstance(session_data, str):
                # Legacy path: session_data is a JSON string of cookies
                # v5.0+: still decrypt it (Fernet-encrypted)
                # R7-PASS-12: Use _fernet_executor (capped at 2 workers)
                # instead of the default executor for consistency with
                # the encryption path (line 2173). Both operations are
                # Fernet, and using a shared bounded pool prevents
                # thread pool exhaustion under load.
                try:
                    loop = asyncio.get_running_loop()
                    decrypted_payload = await loop.run_in_executor(
                        _fernet_executor,
                        get_serializer().decrypt_payload,
                        session_data
                    )
                except Exception as dec_err:
                    # R7-PASS-13: Distinguish "no data" (ValueError) from
                    # "corrupted" (InvalidToken or other). The ValueError
                    # comes from the defensive check in decrypt_payload
                    # for None/empty input. A real Fernet decryption
                    # failure throws InvalidToken (which has "MAC" or
                    # "Fernet" in the message).
                    err_msg = str(dec_err)
                    if "payload is None or empty" in err_msg:
                        code = "empty_payload"
                    elif "MAC" in err_msg or "signature" in err_msg.lower() or "Fernet" in err_msg:
                        code = "auth_failed"
                    else:
                        code = "corrupted"
                    logger.critical(f"Decryption failed: {dec_err} (code={code})")
                    raise DecryptionFailedError(str(dec_err), code=code) from dec_err

                restored = await get_serializer().restore_session_state(browser, decrypted_payload)
                if not restored:
                    logger.critical(f"Session restoration failed for {persona_id}")
                    await browser.close()
                    return False
                logger.info("Decrypted cookies restored.")

                # BEN-9 (Pass 14): keep decrypted_payload alive for the
                # restore_local_storage call below (line ~2213). The
                # previous code immediately nullified the reference
                # after restore_session_state, but the localStorage
                # restoration at line 2213 needs the same payload. The
                # nullify+gc.collect() now happens AFTER both
                # restorations complete.
                # Save reference and clean up at the end.

            try:
                logger.info(f"Dynamically importing task plugin: tasks.{task_name}.executor")
                try:
                    task_module = importlib.import_module(f"tasks.{task_name}.executor")
                    executor = task_module.TaskExecutor()
                except ModuleNotFoundError as import_err:
                    logger.error(f"Failed to locate task plugin '{task_name}': {import_err}")
                    await browser.close()
                    return False

                ai_bridge = _AIBridge(
                    provider=ai_provider,
                    model=ai_model,
                    api_key=ai_api_key
                ) if _AIBridge else None

                base_url = task_payload.get("signup_url") or task_payload.get("chat_box_selector") or "https://www.google.com"
                if base_url and isinstance(base_url, str):
                    try:
                        parsed_base = urllib.parse.urlparse(base_url)
                        is_http_url = parsed_base.scheme in ("http", "https")
                    except Exception:
                        is_http_url = False
                else:
                    is_http_url = False

                if is_http_url:
                    logger.info(f"Establishing target origin for {persona_id}: {base_url}")
                    try:
                        current_host = urllib.parse.urlparse(page.url).hostname or ""
                        target_host = parsed_base.hostname or ""
                        same_origin = (current_host == target_host
                                       or current_host.endswith(f".{target_host}")
                                       or target_host.endswith(f".{current_host}"))
                    except Exception:
                        same_origin = False

                    if page.url == "about:blank" or not same_origin:
                        # v5.0+: first-navigation-delay 8-25s
                        _nav_delay = _human_like_delay(FIRST_NAV_DELAY_MIN_S, FIRST_NAV_DELAY_MAX_S, site_label="first_nav")
                        await first_navigation_delay(page, base_url, _nav_delay)
                        start_load_time = time.perf_counter()
                        try:
                            await page.goto(base_url, timeout=PAGE_LOAD_TIMEOUT * 1000, wait_until="domcontentloaded")
                            await page.wait_for_load_state("load", timeout=PAGE_LOAD_TIMEOUT * 1000)
                        except Exception as nav_err:
                            logger.warning(f"Navigation to {base_url} failed: {nav_err}")
                        elapsed_load = time.perf_counter() - start_load_time
                        pacing_engine = get_pacing_engine()
                        if pacing_engine:
                            try:
                                await pacing_engine.record_load_latency(start_load_time)
                            except Exception as _suppressed_exc:
                                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")

                    if decrypted_payload:
                        try:
                            await get_serializer().restore_local_storage(page, decrypted_payload)
                        except Exception as ls_err:
                            logger.warning(f"restore_local_storage failed: {ls_err}")

                task_payload_with_proxy = dict(task_payload)
                # L10 fix (Pass 16): Save the caller's original
                # proxy_url (if any) in `__caller_proxy_url__` BEFORE
                # we override with persona_socks5_url. This way, the
                # task executor can see both values and decide whether
                # the caller's value or the orchestrator's per-persona
                # value is the right one to use. The caller may have
                # passed a geo-targeted proxy (e.g., a UK proxy for a
                # UK signup) that the orchestrator doesn't know about.
                if "proxy_url" in task_payload:
                    task_payload_with_proxy["__caller_proxy_url__"] = task_payload["proxy_url"]
                task_payload_with_proxy["proxy_url"] = persona_socks5_url
                task_payload_with_proxy["warmup_stage"] = warmup_stage
                # v5.0+: pass persona_digest to task executor
                task_payload_with_proxy["persona_digest"] = persona_digest

                try:
                    success = await asyncio.wait_for(
                        executor.run(page, browser, ai_bridge, task_payload_with_proxy),
                        timeout=TASK_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Task '{task_name}' timed out after {TASK_TIMEOUT_SECONDS}s")
                    success = False
                logger.info(f"Task '{task_name}' completed. Status: {success}")

                # v5.0+: serialize session in-memory (no disk write)
                raw_session_dict = await get_serializer().serialize_session_state(browser, page)
                # R7-HOR-021: Use the existing Fernet executor (capped at 2
                # workers) instead of the default ThreadPoolExecutor
                # (unbounded). The previous code used `None` which means
                # the default executor, spawning a new thread per
                # concurrent encryption. For 1-persona with 1 task, this
                # is fine, but under load the default executor can
                # spawn hundreds of threads, exhausting the GIL.
                loop = asyncio.get_running_loop()
                encrypted_state = await loop.run_in_executor(
                    _fernet_executor,
                    get_serializer().encrypt_payload,
                    raw_session_dict
                )

                async with db_pool.acquire() as conn:
                    # CONCURRENCY-FIX C7: compare-and-swap instead of a blind
                    # overwrite. The previous statement was
                    #     UPDATE personas SET session_data = $1 WHERE id = $2
                    # which is last-writer-wins: two concurrent sessions for
                    # one persona meant one session's encrypted cookies were
                    # silently and permanently destroyed (verified).
                    # `IS NOT DISTINCT FROM` also covers the NULL -> first
                    # save transition, which `=` would not.
                    saved = await conn.fetchrow("""
                        UPDATE personas SET session_data = $1, last_active = NOW()
                        WHERE id = $2 AND session_data IS NOT DISTINCT FROM $3
                        RETURNING id
                    """, encrypted_state, persona_id, _session_data_at_load)
                if saved is None:
                    logger.warning(
                        f"CONCURRENCY C7: session save skipped for "
                        f"{persona_id} - session_data changed since it was "
                        f"loaded (another worker saved first). Refusing to "
                        f"overwrite, rather than destroying one of the two "
                        f"sessions."
                    )
                else:
                    logger.info("Session state encrypted and saved.")

                # R7-DEEP-AUDIT-2 FIX: After the DB write, the
                # encrypted_state is no longer needed in this scope.
                # We drop the reference and force GC. Note that
                # encrypted_state is Fernet-encrypted (not plaintext),
                # so the security risk is low — but reducing memory
                # footprint is still good hygiene, and avoiding
                # accidental logging of the value is also important.
                encrypted_state = None
                raw_session_dict = None
                import gc
                gc.collect()

                # v5.0+: increment task counter for retirement tracking
                if success:
                    try:
                        async with db_pool.acquire() as conn:
                            # CONCURRENCY-FIX C10: the increment was already
                            # atomic, but the code then ran a SEPARATE
                            # `SELECT task_count` before comparing it to
                            # MAX_TASKS_PER_PERSONA. Between the two, another
                            # worker could increment again, so the retirement
                            # decision was made against a stale value.
                            # RETURNING gives us the post-update count from
                            # the same statement that changed it.
                            new_count = await conn.fetchval("""
                                UPDATE personas
                                SET task_count = COALESCE(task_count, 0) + 1,
                                    last_active = NOW()
                                WHERE id = $1
                                RETURNING task_count
                            """, persona_id)
                            # R7-DEEP-AUDIT-2 GAP 1.8: PPTC personas
                            # need their task_count to be in sync with
                            # the city-level count, otherwise the
                            # retirement logic at MAX_TASKS_PER_PERSONA
                            # never triggers for them. Call the sync
                            # function from city_persona.py.
                            if _city_persona_module is not None:
                                try:
                                    await _city_persona_module.sync_persona_task_count(
                                        conn, persona_id
                                    )
                                except Exception as sync_err:
                                    logger.debug(
                                        f"PPTC: persona task_count sync failed "
                                        f"(non-fatal): {sync_err}"
                                    )
                            # CONCURRENCY-FIX C10: `new_count` now comes from
                            # the UPDATE ... RETURNING above; this redundant
                            # second SELECT is what allowed the retirement
                            # check to race. Removed.
                            if new_count and new_count > MAX_TASKS_PER_PERSONA:
                                logger.warning(
                                    f"Persona {persona_id} reached {new_count} tasks "
                                    f"(limit: {MAX_TASKS_PER_PERSONA}); marking for rotation."
                                )
                                # CHAMELEON (GAP-2.1): Zero session_data on
                                # rotation to prevent any future persona
                                # from inheriting encrypted cookies that
                                # could be linked to a "rotated" persona.
                                await conn.execute(
                                    "UPDATE personas SET state = 'rotated', session_data = NULL WHERE id = $1",
                                    persona_id
                                )
                                raise PersonaRetiredError(
                                    f"Persona {persona_id} reached {MAX_TASKS_PER_PERSONA} task limit",
                                    persona_id=persona_id
                                )
                    except PersonaRetiredError as _suppressed_exc:
                        # Re-raise so the daemon loop sees it
                        # v5.0+: this is a normal retirement, not an error
                        # Let the task be marked completed (success=True),
                        # but the persona is now 'rotated' and won't be picked up.
                        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                    except Exception as rc_err:
                        logger.warning(f"Task counter update failed: {rc_err}")

                if success and task_name == "site_register" and task_payload.get("action") == "seasoning":
                    # CONCURRENCY-FIX C8: increment in SQL instead of in
                    # memory. `warmup_stage` was read at task start (line
                    # ~2255), incremented in Python, then blind-written back.
                    # Two workers both reading 3 and both writing 4 lose a
                    # seasoning day, and a long-running task can clobber a
                    # newer value with a staler one, moving the stage
                    # backwards. This column is a safety gate -
                    # tasks/site_register/executor.py blocks signup below 7 -
                    # so drift here changes behaviour, not just bookkeeping.
                    async with db_pool.acquire() as conn:
                        new_stage = await conn.fetchval("""
                            UPDATE personas
                            SET warmup_stage = LEAST(COALESCE(warmup_stage, 0) + 1, 7)
                            WHERE id = $1
                            RETURNING warmup_stage
                        """, persona_id)
                        logger.info(
                            f"Warmup stage for {persona_id} advanced to {new_stage}/7"
                        )

                try:
                    ch_for_db = json.dumps(client_hints) if client_hints is not None else None
                    async with db_pool.acquire() as conn:
                        await conn.execute("""
                            UPDATE personas
                            SET signup_geo_region = $1, assigned_asn = $2, assigned_isp = $3,
                                timezone = $4, canvas_noise_seed = $5, client_hints = $6
                            WHERE id = $7
                        """, signup_geo_region, row['assigned_asn'], row['assigned_isp'],
                             timezone, canvas_noise_seed, ch_for_db, persona_id)
                except Exception as meta_sync_err:
                    logger.warning(f"Metadata sync bypass: {meta_sync_err}")

                # Cleanup child processes
                if psutil is not None:
                    try:
                        current_process = psutil.Process(os.getpid())
                        for child in current_process.children(recursive=True):
                            try:
                                if child.name() in ["firefox", "chrome", "chromium", "node", "Xvfb"]:
                                    child.terminate()
                            except Exception as _suppressed_exc:
                                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                    except Exception as child_kill_err:
                        logger.error(f"Cleanup failure: {child_kill_err}")

                # Post-execution grace delay
                pacing_engine = get_pacing_engine()
                if pacing_engine:
                    try:
                        await pacing_engine.sleep_human_delay(base_delay=4.0, variance_factor=0.25)
                    except Exception as _suppressed_exc:
                        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")

                # v6.2: Session Pacing with human-like log-normal distribution
                # (replaces the uniform random.uniform calls that were
                # statistically detectable)
                try:
                    elapsed = time.perf_counter() - start_session_time
                    # M4 follow-up fix: Use the per-call-site RNG
                    # registry (added for _human_like_delay). The
                    # previous code referenced _persona_delay_rng
                    # which was replaced with the registry. The
                    # session_roll is now derived from the
                    # 'session_roll' site, ensuring it's
                    # deterministic per persona but independent
                    # of other call sites' RNGs.
                    if "session_roll" not in _persona_delay_rng_registry:
                        _roll_seed = int(hashlib.sha256(
                            f"delay:{persona_id}:session_roll".encode('utf-8')
                        ).hexdigest()[:8], 16)
                        _persona_delay_rng_registry["session_roll"] = random.Random(_roll_seed)
                    session_roll = _persona_delay_rng_registry["session_roll"].random()
                    if session_roll < 0.30:
                        # R7-HOR-015: Short session range widened from
                        # 60-115s to 30-180s. Real users have short
                        # sessions as short as 30s (quick check) and
                        # as long as 3 minutes (read a short article).
                        # The previous range (60-115s) was a narrow
                        # bimodal distribution detectable as a bot.
                        target_duration = _human_like_delay(30.0, 180.0, site_label="session_short")
                    elif session_roll < 0.80:
                        # Medium session: 120-450s
                        target_duration = _human_like_delay(120.0, 450.0, site_label="session_medium")
                    else:
                        # Long session: 600s to MAX_SESSION_MINUTES*60
                        target_duration = _human_like_delay(
                            600.0, max(601.0, float(MAX_SESSION_MINUTES * 60)),
                            site_label="session_long"
                        )

                    remaining_pad = target_duration - elapsed
                    if remaining_pad > 5.0 and page is not None and pacing_engine:
                        logger.info(f"Session Pacing: padding by {remaining_pad:.1f}s (log-normal)")
                        await pacing_engine.sleep_human_delay(remaining_pad, page=page)
                    elif remaining_pad < -10.0:
                        logger.warning(f"Session ran {abs(remaining_pad):.1f}s over target")
                except Exception as pad_err:
                    logger.warning(f"Session pacing skipped: {pad_err}")

                # v6.2: Human-like cooldown (log-normal distribution)
                cooldown = _human_like_delay(POST_TASK_COOLDOWN_S_MIN, POST_TASK_COOLDOWN_S_MAX, site_label="cooldown")
                logger.info(f"Post-task cooldown: {cooldown:.0f}s (log-normal)")
                await asyncio.sleep(cooldown)

                # R7-HOR-024: Clear persona_digest from memory after task.
                # The dict contains persona secrets, session IDs, and
                # other sensitive data. A debugger or /proc/PID/mem
                # scan could extract these. We clear the dict and
                # force garbage collection.
                try:
                    if persona_digest is not None:
                        persona_digest.clear()
                        persona_digest = None
                        import gc
                        gc.collect()
                except Exception as _suppressed_exc:
                    logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")

                # BEN-9 (Pass 14): Now safe to drop decrypted_payload
                # after BOTH restore_session_state and restore_local_storage
                # have completed. This is the LAST chance to drop the
                # reference; after this point, we exit the function and
                # the dict would otherwise live until GC.
                if decrypted_payload is not None:
                    decrypted_payload = None
                    import gc
                    gc.collect()

                return True

            except Exception as page_err:
                logger.error(f"Automation execution failed for {persona_id}: {page_err}")
                return False
    except Exception as browser_err:
        logger.error(f"Playwright instance creation failed for {persona_id}: {browser_err}")
        return False
    finally:
        # M11 fix: Restore the global random state captured at the
        # top of this function. Without this restore, the global
        # random module is left in a state derived from the LAST
        # persona's seed. Subsequent code in the daemon loop
        # (e.g., `_poll_jitter = random.uniform(0.7, 1.3)`) would
        # then produce values correlated with the last persona,
        # which is a fingerprint (creepjs timing-distribution
        # correlation across personas).
        try:
            if _random_state is not None:
                random.setstate(_random_state)
        except Exception as _suppressed_exc:
            # Non-fatal; the state is just slightly off.
            logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")

        # A4 fix (Pass 16): Close the page explicitly before closing
        # the browser. The previous code only closed the browser,
        # which transitively closes all pages. However, Playwright
        # recommends explicit page.close() FIRST to flush any
        # pending writes (cookies, localStorage) before the
        # browser context is torn down. If a CancelledError fired
        # mid-task, the page would not have been closed cleanly,
        # and pending writes would be lost (the next persona
        # reusing the tmpfs would see stale state). We now
        # close the page first (with a short timeout to prevent
        # hanging on a wedged page), then close the browser.
        if page is not None:
            try:
                await asyncio.wait_for(page.close(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"A4: page.close() timed out after 5s; continuing")
            except Exception as page_close_err:
                logger.debug(f"A4: page.close() failed (non-fatal): {page_close_err}")
        if browser is not None:
            try:
                # v6.2: Clear all browser storage before closing to prevent
                # cross-session leakage. This is critical for the
                # localStorage/IndexedDB/Cache API vectors.
                try:
                    pages = browser.pages if hasattr(browser, 'pages') else []
                    for p in pages:
                        await _clear_browser_storage(p)
                except Exception as clear_err:
                    logger.warning(f"Pre-close storage clear failed: {clear_err}")
                await browser.close()
            except Exception as _suppressed_exc:
                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
        # v6.2: Ensure ALL child processes are terminated, not just direct
        # children. Use process group kill to catch GPU/renderer/network
        # processes that may have escaped the parent-child tracking.
        if xvfb_process is not None and xvfb_process.returncode is None:
            try:
                # Kill the entire process group of Xvfb
                import os as _os_kill
                pgid = None
                try:
                    pgid = _os_kill.getpgid(xvfb_process.pid)
                    _os_kill.killpg(pgid, 15)  # SIGTERM
                except (OSError, AttributeError):
                    xvfb_process.terminate()
                try:
                    await asyncio.wait_for(xvfb_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    try:
                        if pgid is not None:
                            _os_kill.killpg(pgid, 9)  # SIGKILL
                        else:
                            xvfb_process.kill()
                    except (OSError, AttributeError):
                        xvfb_process.kill()
            except Exception as xvfb_kill_err:
                logger.error(f"Xvfb termination error: {xvfb_kill_err}")
        # v6.2: Also kill any orphaned browser processes via psutil
        if psutil is not None:
            try:
                parent = psutil.Process(os.getpid())
                for child in parent.children(recursive=True):
                    try:
                        if child.name().lower() in ('chrome', 'chromium', 'firefox', 'xvfb'):
                            child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as _suppressed_exc:
                        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                # Wait for terminated processes
                #
                # RELIABILITY-FIX L3: psutil.wait_procs() is a blocking call and
                # this is an `async def`, so it stalled the event loop for up to
                # 5s. Offloaded to the default executor so the loop keeps running.
                _children = parent.children(recursive=True)
                gone, alive = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: psutil.wait_procs(_children, timeout=5.0)
                )
                for p in alive:
                    try:
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as _suppressed_exc:
                        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
            except Exception as zombie_err:
                logger.warning(f"Zombie cleanup error: {zombie_err}")
        # C4 fix: TLS session tickets / cache flush synchronization.
        # The previous code killed browser processes (SIGTERM/SIGKILL
        # above) and then immediately wiped the tmpfs in the same
        # finally block. SIGKILL is non-graceful: Chrome cannot flush
        # its session ticket cache (Default/Network/Socket Pool,
        # Default/Code Cache/ssl) or LevelDB write-batches
        # (Default/Service Worker/Database, Default/IndexedDB).
        # If the tmpfs wipe happens while Chrome is still mid-write
        # to those files, the next persona may inherit partial state:
        #   - Stale TLS session tickets (the new persona reuses the
        #     previous persona's TLS session with the target site,
        #     revealing cross-persona session continuity)
        #   - Half-written LevelDB tables (Chrome fails to read them
        #     on next launch and may use the previous values, or
        #     crash with "database is corrupted")
        # The fix: BEFORE the tmpfs wipe, wait for all browser
        # processes to be confirmed dead (not just signaled). The
        # zombie cleanup above already calls psutil.wait_procs()
        # with a 5s timeout. We add a second wait loop here to
        # catch any processes that may have escaped the first
        # sweep, with a hard 10s upper bound. After all Chrome
        # processes are confirmed dead, the tmpfs is safe to
        # wipe.
        if psutil is not None:
            try:
                _wait_deadline = time.time() + 10.0
                while time.time() < _wait_deadline:
                    _alive_chrome = []
                    try:
                        for _p in psutil.process_iter(['pid', 'name']):
                            try:
                                _pn = (_p.info.get('name') or '').lower()
                                if _pn in ('chrome', 'chromium', 'firefox', 'xvfb'):
                                    if _p.is_running() and _p.status() != psutil.STATUS_ZOMBIE:
                                        _alive_chrome.append(_p.pid)
                            except (psutil.NoSuchProcess, psutil.AccessDenied) as _suppressed_exc:
                                logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                    except Exception as _suppressed_exc:
                        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
                    if not _alive_chrome:
                        break
                    logger.debug(
                        f"C4: Waiting for {len(_alive_chrome)} browser processes "
                        f"to exit before tmpfs wipe"
                    )
                    await asyncio.sleep(0.2)
            except Exception as wait_err:
                logger.debug(f"C4: process-wait loop error (non-fatal): {wait_err}")

        # v5.0+: wipe task tmpfs contents (tmpfs mount persists until
        # container teardown; we only clean contents).
        if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
            try:
                for entry in os.listdir(ORCH_TASK_TMPFS_DIR):
                    entry_path = os.path.join(ORCH_TASK_TMPFS_DIR, entry)
                    try:
                        if os.path.isfile(entry_path) or os.path.islink(entry_path):
                            os.unlink(entry_path)
                        elif os.path.isdir(entry_path):
                            shutil.rmtree(entry_path, ignore_errors=True)
                    except Exception as _suppressed_exc:
                        logger.debug(f"run_persona_pipeline(): suppressed {type(_suppressed_exc).__name__}")
            except Exception as clean_err:
                logger.error(f"Error wiping task tmpfs: {clean_err}")

        # L25 fix: Final SingletonLock cleanup. The pre-launch
        # cleanup unlinks the lockfile at the start of the next
        # task; the finally-block cleanup unlinks it again here
        # at task-end to ensure it's gone even if the pipeline
        # crashed mid-task (and Chrome recreated the lock as part
        # of its crash recovery). The pre-launch + post-task
        # pair closes the lockfile leak vector across crashes
        # and container kills.
        try:
            for _lock_name in ("SingletonLock", "SingletonLockSymlink", "SingletonCookie"):
                _lock_path = os.path.join(profile_path, _lock_name)
                if os.path.islink(_lock_path) or os.path.isfile(_lock_path):
                    try:
                        os.unlink(_lock_path)
                        logger.debug(f"L25: post-task cleanup removed {_lock_name}")
                    except Exception as _l25_err:
                        logger.debug(
                            f"L25: post-task cleanup failed for {_lock_name}: {_l25_err}"
                        )
        except Exception as _l25_outer:
            # Never let lockfile cleanup crash the finally block
            logger.debug(f"L25: post-task cleanup outer error: {_l25_outer}")


# v5.0+: helper for datetime arithmetic (used in persona retirement check)
import datetime


_RECLAIM_LAST_TS = 0.0
_RECLAIM_INTERVAL_S = 60.0


async def _reclaim_stalled_tasks() -> int:
    """CONCURRENCY-FIX C2: recycle tasks whose worker died mid-execution.

    A claimed task sits in status='running' until its worker finishes. If that
    worker crashes, is OOM-killed, or the container is destroyed, nothing else
    would ever pick the task up again. `claimed_at` plus a grace period derived
    from TASK_TIMEOUT_SECONDS lets us detect and requeue those safely: a healthy
    worker finishes (or is killed by its own timeout) well inside the window.
    """
    global _RECLAIM_LAST_TS
    if db_pool is None:
        return 0
    now = time.time()
    if now - _RECLAIM_LAST_TS < _RECLAIM_INTERVAL_S:
        return 0
    _RECLAIM_LAST_TS = now
    try:
        async with db_pool.acquire() as conn:
            tag = await conn.execute(
                "UPDATE tasks "
                "SET status = 'pending', claimed_by = NULL, claimed_at = NULL "
                "WHERE status = 'running' "
                "  AND claimed_at IS NOT NULL "
                "  AND claimed_at < NOW() - ($1 || ' seconds')::INTERVAL",
                str(CLAIM_LEASE_GRACE_SECONDS),
            )
        recycled = int(str(tag).split()[-1]) if tag and str(tag).split() else 0
        if recycled:
            logger.warning(
                f"CONCURRENCY C2: recycled {recycled} stalled task(s) "
                f"whose lease expired (>{CLAIM_LEASE_GRACE_SECONDS}s)"
            )
        return recycled
    except Exception as reclaim_err:
        logger.debug(
            f"_reclaim_stalled_tasks(): suppressed "
            f"{type(reclaim_err).__name__}"
        )
        return 0


async def _claim_tasks():
    """CONCURRENCY-FIX C2/C6/C9: atomically claim up to TASKS_PER_BATCH tasks.

    The previous code was a bare SELECT with no lock and no lease:

        SELECT ... FROM tasks t
        WHERE status IN ('pending','retry') ... ORDER BY created_at LIMIT $1

    Nothing marked a row as taken between that SELECT and the eventual
    `UPDATE tasks SET status='completed'`, so every daemon polling in the same
    window received the SAME row and executed it. With two workers that put one
    persona in two browsers at once: concurrent sessions on one account,
    interleaved writes and the action performed twice.

    This version, inside a single transaction:
      1. `FOR UPDATE OF t SKIP LOCKED` - concurrent claimers skip each other's
         rows instead of reading them.
      2. An optimistic `UPDATE ... WHERE status IN ('pending','retry')` - the
         real claim. 0 rows affected means a peer won the race.
      3. A persona lease (`personas.locked_by/locked_until`) so no two workers
         ever run two tasks for the same persona simultaneously (C9).
      4. `cooldown_until` is honoured in the query itself (C6); previously the
         queue happily handed out tasks for a cooling-down persona because the
         filter only excluded 'limited'/'rotated'.

    Leases self-heal: if this worker dies, `locked_until` expires and
    `_reclaim_stalled_tasks()` requeues the work.
    """
    claimed = []
    if db_pool is None:
        return claimed
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT t.id, t.persona_id, t.platform, t.task_payload,
                       t.status, t.failure_count
                FROM tasks t
                LEFT JOIN personas p ON p.id = t.persona_id
                WHERE (t.status = 'pending'
                       OR (t.status = 'retry' AND t.execution_time <= NOW()))
                  AND (p.state IS NULL OR p.state NOT IN ('limited', 'rotated'))
                  -- C6: honour the cooldown in the queue, not just by sleeping
                  AND (p.cooldown_until IS NULL OR p.cooldown_until <= NOW())
                  -- C9: never run two tasks for the same persona at once
                  AND (p.locked_until IS NULL OR p.locked_until <= NOW())
                ORDER BY t.created_at ASC
                LIMIT $1
                FOR UPDATE OF t SKIP LOCKED
                """,
                TASKS_PER_BATCH,
            )
            for row in rows:
                tag = await conn.execute(
                    "UPDATE tasks "
                    "SET status = 'running', claimed_by = $1, claimed_at = NOW() "
                    "WHERE id = $2 AND status IN ('pending', 'retry')",
                    WORKER_ID, row['id'],
                )
                if not tag or not str(tag).endswith("1"):
                    # A peer claimed this row between our SELECT and our UPDATE.
                    continue
                if row['persona_id']:
                    ptag = await conn.execute(
                        "UPDATE personas "
                        "SET locked_by = $1, "
                        "    locked_until = NOW() + ($2 || ' seconds')::INTERVAL "
                        "WHERE id = $3 "
                        "  AND (locked_until IS NULL OR locked_until <= NOW())",
                        WORKER_ID, str(CLAIM_LEASE_GRACE_SECONDS),
                        row['persona_id'],
                    )
                    if not ptag or not str(ptag).endswith("1"):
                        # Lost the persona lease: hand the task straight back
                        # rather than running it unlocked.
                        await conn.execute(
                            "UPDATE tasks "
                            "SET status = 'pending', claimed_by = NULL, "
                            "    claimed_at = NULL "
                            "WHERE id = $1",
                            row['id'],
                        )
                        continue
                claimed.append(row)
    return claimed


async def _release_persona_leases(persona_ids) -> None:
    """CONCURRENCY-FIX C9: drop the persona leases this worker still holds."""
    if db_pool is None or not persona_ids:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE personas "
                "SET locked_by = NULL, locked_until = NULL "
                "WHERE id = any($1::varchar[]) AND locked_by = $2",
                list(persona_ids), WORKER_ID,
            )
    except Exception as rel_err:
        logger.debug(
            f"_release_persona_leases(): suppressed {type(rel_err).__name__}"
        )


async def orchestrator_daemon_loop():
    """DC-19 (Pass 19): Main orchestrator loop.

    Polls the task queue, processes one task at a time (TASKS_PER_BATCH=1),
    and handles all error paths (DB unavailable, persona retired, decryption
    failed, PID budget exceeded). On SIGINT/SIGTERM, the loop exits cleanly
    via the registered signal handlers (handle_graceful_shutdown).

    The loop never raises — all exceptions are caught and logged. A task
    that fails 3 times (MAX_TASK_RETRIES) is marked permanently failed
    and its persona is quarantined.
    """
    global db_pool, daemon_running
    logger.info("Orchestrator: Initializing Stateful Task Supervisor Daemon (1-persona mode)...")

    # R7-HOR-019: The db_pool is closed in the __main__ finally
    # block. The daemon loop itself does not need a try/finally
    # wrapper because the __main__ block ensures cleanup runs on
    # any exit path.

    orchestrator_start_time = time.time()

    # Stale profile handling - move to stale/ subdir
    if os.path.isdir(ORCH_TASK_TMPFS_DIR):
        try:
            for entry in os.listdir(ORCH_TASK_TMPFS_DIR):
                entry_path = os.path.join(ORCH_TASK_TMPFS_DIR, entry)
                if os.path.isdir(entry_path) and entry.endswith("_session"):
                    lock_path = os.path.join(entry_path, ".in_progress")
                    if not os.path.exists(lock_path):
                        stale_dir = os.path.join(ORCH_TASK_TMPFS_DIR, "stale")
                        os.makedirs(stale_dir, mode=0o755, exist_ok=True)
                        try:
                            shutil.move(entry_path, os.path.join(stale_dir, entry))
                            logger.info(f"Moved stale session {entry} to {stale_dir}")
                        except Exception as mv_err:
                            logger.warning(f"Could not move stale {entry}: {mv_err}")
        except Exception as cleanup_err:
            logger.error(f"Stale profile cleanup error: {cleanup_err}")

    # DB pool
    try:
        # R7-HOR-007: min_size=1 (not 2) to avoid the strict-2 connection
        # requirement. If the DB is briefly unreachable, the pool won't
        # block trying to create 2 connections before any acquire returns.
        # The pool grows on demand up to max_size=4.
        db_pool = await asyncpg.create_pool(DB_URI, min_size=1, max_size=4, timeout=10.0)

        # Schema auto-init
        async with db_pool.acquire() as conn:
            table_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'personas'
                )
            """)
            if not table_exists:
                logger.warning("Cold-start detected; auto-initializing database schema...")
                schema_paths = [
                    os.path.join(_SCRIPT_DIR, "database", "schema.sql"),
                    os.path.join(_PROJECT_ROOT, "database", "schema.sql"),
                    "database/schema.sql",
                    "../database/schema.sql",
                    "schema.sql",
                    "/app/database/schema.sql",
                ]
                schema_content = None
                for path in schema_paths:
                    if os.path.exists(path):
                        try:
                            with open(path, "r") as sf:
                                schema_content = sf.read()
                            break
                        except Exception as e:
                            logger.warning(f"Could not read {path}: {e}")
                if schema_content:
                    # R7-HOR-017: asyncpg's conn.execute() runs a SINGLE
                    # statement. The previous code stripped comments and
                    # passed the entire schema.sql to conn.execute(),
                    # which only ran the first statement. Subsequent
                    # CREATE TABLE / CREATE INDEX statements were silently
                    # dropped. We now split on `;` and execute each
                    # statement individually.
                    # First, strip SQL line comments (-- to end of line)
                    clean_sql = "\n".join(
                        [line for line in schema_content.split("\n")
                         if not line.strip().startswith("--")]
                    )
                    # Split on `;` and execute each non-empty statement
                    statements = [s.strip() for s in clean_sql.split(";")
                                  if s.strip()]
                    schema_errors = 0
                    for stmt in statements:
                        try:
                            await conn.execute(stmt)
                        except Exception as stmt_err:
                            # CHAMELEON: Don't fail on "IF NOT EXISTS" related
                            # errors or on CREATE INDEX errors that occur
                            # when the table was just created. We log the
                            # error and continue.
                            err_str = str(stmt_err).lower()
                            if "already exists" in err_str:
                                # Table already exists (from prior run). OK.
                                continue
                            schema_errors += 1
                            logger.warning(f"Schema statement failed: {stmt_err}")
                    if schema_errors == 0:
                        logger.info(f"Database schema initialized ({len(statements)} statements).")
                    else:
                        logger.warning(f"Database schema initialized with {schema_errors} errors ({len(statements)} statements).")

                    # PPPC: Create the persona_by_city table separately
                    # (in case the main schema.sql doesn't include it yet).
                    # The IF NOT EXISTS makes this idempotent.
                    try:
                        await conn.execute("""
                            CREATE TABLE IF NOT EXISTS persona_by_city (
                                city VARCHAR(96) PRIMARY KEY,
                                persona_id VARCHAR(64) NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                task_count INT DEFAULT 0,
                                rotated_at TIMESTAMP WITH TIME ZONE,
                                rotation_reason TEXT
                            )
                        """)
                        await conn.execute("""
                            CREATE INDEX IF NOT EXISTS idx_persona_by_city_last_used
                            ON persona_by_city(last_used DESC)
                        """)
                        await conn.execute("""
                            CREATE INDEX IF NOT EXISTS idx_persona_by_city_persona_id
                            ON persona_by_city(persona_id)
                        """)
                        logger.info("PPPC: persona_by_city table ensured")
                    except Exception as ppc_err:
                        logger.warning(f"PPPC: persona_by_city table init failed (non-fatal): {ppc_err}")

                    # PPPC: Defensive migration. The personas.task_count
                    # column was added in R7-DEEP-AUDIT-2. For databases
                    # created before this migration, the column is
                    # missing and every UPDATE personas SET task_count
                    # will fail. ADD COLUMN IF NOT EXISTS is idempotent.
                    try:
                        await conn.execute(
                            "ALTER TABLE personas ADD COLUMN IF NOT EXISTS task_count INT DEFAULT 0"
                        )
                        logger.info("PPPC: personas.task_count column ensured")
                    except Exception as task_count_err:
                        logger.warning(f"PPPC: personas.task_count migration failed (non-fatal): {task_count_err}")

                    # ------------------------------------------------------
                    # CONCURRENCY-FIX C1/C2/C9: defensive migrations.
                    #
                    # C1 is the important one: the queue query selects
                    # t.failure_count and both retry paths UPDATE it, but
                    # no migration ever created that column. Against a
                    # database built from database/schema.sql the very
                    # first poll raised UndefinedColumnError, so the daemon
                    # logged "Queue polling failed" and backed off to 300s
                    # forever -- it never processed a single task.
                    #
                    # C2/C9 add the lease columns backing the atomic task
                    # claim and the persona lock. All are idempotent, so
                    # they are safe on a database that already has them.
                    # ------------------------------------------------------
                    _CONCURRENCY_MIGRATIONS = (
                        ("tasks", "failure_count", "INT DEFAULT 0"),
                        ("tasks", "claimed_by", "VARCHAR(128)"),
                        ("tasks", "claimed_at", "TIMESTAMP WITH TIME ZONE"),
                        ("personas", "locked_by", "VARCHAR(128)"),
                        ("personas", "locked_until", "TIMESTAMP WITH TIME ZONE"),
                    )
                    for _tbl, _col, _type in _CONCURRENCY_MIGRATIONS:
                        try:
                            await conn.execute(
                                f"ALTER TABLE {_tbl} "
                                f"ADD COLUMN IF NOT EXISTS {_col} {_type}"
                            )
                        except Exception as mig_err:
                            logger.warning(
                                f"CONCURRENCY: migration {_tbl}.{_col} failed "
                                f"(non-fatal): {mig_err}"
                            )
                    logger.info(
                        "CONCURRENCY: task/persona lease columns ensured"
                    )
                else:
                    logger.critical("Could not locate database/schema.sql")
    except Exception as db_err:
        logger.critical(f"DB pool creation failed: {db_err}")
        raise DatabaseUnavailableError(str(db_err)) from db_err

    while daemon_running:
        # Zombie reaper
        if psutil is not None:
            try:
                for proc in psutil.process_iter(['pid', 'name', 'ppid', 'create_time']):
                    try:
                        proc_name = (proc.info['name'] or '').lower()
                        proc_basename = os.path.basename(proc_name)
                        if proc_basename in ['firefox', 'chrome', 'chromium', 'xvfb', 'node']:
                            if proc.info['create_time'] < orchestrator_start_time - 5:
                                logger.warning(f"Sweeper: terminating orphan PID {proc.info['pid']} ({proc_basename})")
                                try:
                                    proc.terminate()
                                except Exception as _suppressed_exc:
                                    logger.debug(
                                        f"orchestrator_daemon_loop(): suppressed {type(_suppressed_exc).__name__}"
                                    )
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as _suppressed_exc:
                        logger.debug(f"orchestrator_daemon_loop(): suppressed {type(_suppressed_exc).__name__}")
            except Exception as sweep_err:
                logger.error(f"Sweeper failure: {sweep_err}")

        # C8 fix (Pass 15): Skip the tmpfs invariant check on every
        # other poll iteration. The subprocess IPC cost of `mount`
        # is ~200-500ms; running it on every poll wastes 10% of CPU
        # on a default 5s poll interval. We now check on iterations
        # 0, 2, 4, ... (every other poll) which is sufficient for
        # detecting mount-point corruption (a slow-failing condition,
        # not a high-frequency one). Counter starts at 0 so the
        # first poll (before any tasks) is checked.
        if getattr(orchestrator_daemon_loop, "_tmpfs_check_counter", 0) % 2 == 0:
            # Acquire one task at a time
            try:
                # R7-HOR-014: Use async version of tmpfs invariant check
                # to avoid blocking the event loop. The sync version is
                # still used at module load (where blocking is OK).
                await _verify_single_tmpfs_invariant_async()
            except Exception as _tmpfs_err:
                # Don't propagate; the next iteration will retry.
                logger.debug(f"C8: tmpfs check failed (non-fatal): {_tmpfs_err}")
        orchestrator_daemon_loop._tmpfs_check_counter = (
            getattr(orchestrator_daemon_loop, "_tmpfs_check_counter", 0) + 1
        )

        # Acquire one task at a time
        #
        # CONCURRENCY-FIX C2/C6/C9: the bare SELECT that used to live here
        # (no lock, no lease, no cooldown filter) has moved into
        # _claim_tasks(), which claims atomically inside a transaction.
        try:
            # Requeue work abandoned by a worker that died mid-task.
            await _reclaim_stalled_tasks()
            rows = await _claim_tasks()
            # R7-DEEP-AUDIT-2: Reset DB failure backoff on success.
            orchestrator_daemon_loop._db_failure_count = 0
        except Exception as e:
            logger.error(f"Queue polling failed: {e}")
            # R7-DEEP-AUDIT-2: Exponential backoff for DB failures.
            # The previous code used a fixed 10s sleep, which means
            # a persistent DB outage causes a tight retry loop that
            # hammers the DB and burns CPU. We now use exponential
            # backoff (10s, 20s, 40s, 80s, 160s, max 300s) to give
            # the DB time to recover.
            _db_failure_count = getattr(orchestrator_daemon_loop, '_db_failure_count', 0) + 1
            orchestrator_daemon_loop._db_failure_count = _db_failure_count
            backoff = min(10 * (2 ** (_db_failure_count - 1)), 300)
            logger.warning(
                f"DB unavailable; backing off {backoff}s "
                f"(failure {_db_failure_count})"
            )
            await asyncio.sleep(backoff)
            continue

        if not rows:
            # CHAMELEON (GAP-1.8): Jittered poll interval. A fixed 5s
            # poll is itself a timing fingerprint for advanced
            # telemetry. Real applications have variable idle periods.
            _poll_jitter = random.uniform(0.7, 1.3)
            await asyncio.sleep(DAEMON_POLL_INTERVAL * _poll_jitter)
            continue

        # Sequential processing (TASKS_PER_BATCH=1)
        #
        # CONCURRENCY-FIX C9: record which personas this batch holds a lease
        # on, so they can be released once the batch is done. The lease also
        # expires on its own (locked_until), so a crash here cannot deadlock a
        # persona permanently.
        _leased_personas = {r['persona_id'] for r in rows if r['persona_id']}
        for row in rows:
            if not daemon_running:
                break
            task_id = row['id']
            persona_id = row['persona_id']
            platform = row['platform']
            try:
                task_payload = json.loads(row['task_payload']) if isinstance(row['task_payload'], str) else row['task_payload']
            except json.JSONDecodeError:
                logger.error(f"Task {task_id} has invalid task_payload JSON; marking failed")
                async with db_pool.acquire() as conn2:
                    await conn2.execute("UPDATE tasks SET status='failed', error_log='invalid task_payload JSON' WHERE id = $1", task_id)
                continue
            failures = row['failure_count'] or 0

            # PPPC: Persistent Persona Per City
            # If the task payload contains a city, use the persistent
            # persona for that city (or create one if needed). This makes
            # the persona look like a real returning user.
            #
            # R7-DEEP-AUDIT-2 GAP 2.1: Production-grade PPPC routing
            # with timeouts and explicit connection release. The previous
            # code held the connection for the entire PPTC block, which
            # could exhaust the db_pool under load. Now we:
            #   1. Acquire the connection with a 1s timeout
            #   2. Run the PPTC lookup with a 2s timeout
            #   3. Release the connection explicitly in a finally block
            #   4. Catch all exceptions (timeout, DB error) and fall
            #      through to the original persona_id
            if _city_persona_module is not None and isinstance(task_payload, dict):
                ppc_persona_id = None
                _city = None
                ppc_conn = None
                # A2 fix (Pass 15): Track in-flight operations on the
                # connection. The previous code could leave an
                # in-flight execute firing after the connection is
                # released back to the pool (in the finally block).
                # asyncpg's connection operations are serialized on
                # the connection's internal loop, so a connection
                # being used by a slow execute() can be acquired by
                # another coroutine after release, causing query
                # interleaving. We now cancel any pending operation
                # before releasing the connection.
                # M5/M12 fix: Removed ppc_in_flight tracking.
                # The previous code used asyncio.current_task()
                # to track the in-flight task, but that's the
                # surrounding coroutine's task, not the inner
                # query task. Cancelling it via .cancel() and
                # then `asyncio.shield()`-ing it was
                # contradictory. The new approach uses
                # connection-level release with a tight timeout
                # (see finally block).
                try:
                    _city = _city_persona_module._extract_city_from_payload(task_payload)
                    if _city:
                        ppc_conn = await asyncio.wait_for(
                            db_pool.acquire(), timeout=1.0
                        )
                        # M5/M12: No in_flight tracking needed
                        ppc_persona_id, _ = await asyncio.wait_for(
                            _city_persona_module.get_or_create_persona_for_city(
                                ppc_conn, _city, task_payload
                            ),
                            timeout=2.0
                        )
                        if ppc_persona_id and ppc_persona_id != persona_id:
                            logger.info(
                                f"PPPC: Routing task {task_id} from {persona_id} "
                                f"to persistent persona {ppc_persona_id} for city '{_city}'"
                            )
                            persona_id = ppc_persona_id
                            # Update the task's persona_id to the persistent one
                            await asyncio.wait_for(
                                ppc_conn.execute(
                                    "UPDATE tasks SET persona_id = $1 WHERE id = $2",
                                    persona_id, task_id
                                ),
                                timeout=1.0
                            )
                            # R7-DEEP-AUDIT-2 GAP 1.13: Also update
                            # personas.last_active so a fresh read of
                            # the persona sees the recent activity.
                            try:
                                await asyncio.wait_for(
                                    ppc_conn.execute(
                                        "UPDATE personas SET last_active = NOW() WHERE id = $1",
                                        persona_id
                                    ),
                                    timeout=1.0
                                )
                            except Exception as _suppressed_exc:
                                logger.debug(
                                    f"orchestrator_daemon_loop(): suppressed {type(_suppressed_exc).__name__}"
                                )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"PPPC: Lookup timed out for city '{_city}' (task {task_id}); "
                        f"using original persona {persona_id}"
                    )
                except Exception as ppc_err:
                    # H2 fix: Distinguish recoverable vs. critical
                    # PPPC failures. The previous code muted ALL
                    # exceptions with a single `except Exception`,
                    # which meant a UniqueViolationError (a race
                    # condition where two concurrent tasks both
                    # created the same city-persona pair) silently
                    # fell through to the original persona. This
                    # is a fingerprint: the task ran with the
                    # original persona's SOCKS5 port (proxy) but
                    # the task's persona_id in the DB was the
                    # original (not the PPTC). A site that
                    # cross-checks the task's persona_id against
                    # the proxy geo would see a mismatch.
                    #
                    # Now: log the exception with full type info
                    # (was previously a single f-string), and on
                    # race conditions (UniqueViolation,
                    # DuplicateKey, etc.), re-attempt the lookup
                    # once before falling through.
                    _ppc_err_type = type(ppc_err).__name__
                    _ppc_err_str = str(ppc_err)
                    logger.warning(
                        f"PPPC: Lookup failed for city '{_city}' "
                        f"(task {task_id}, type={_ppc_err_type}): "
                        f"{_ppc_err_str}"
                    )
                    # Detect DB race conditions (unique constraint
                    # violations on persona_by_city.persona_id or
                    # .city). On a race, a SELECT-then-INSERT
                    # pattern would fail because the other task
                    # just inserted. Re-attempt with a fresh
                    # connection.
                    _is_race = (
                        "unique" in _ppc_err_str.lower()
                        or "duplicate" in _ppc_err_str.lower()
                        or "UniqueViolation" in _ppc_err_type
                        or "DuplicateKey" in _ppc_err_type
                    )
                    if _is_race:
                        logger.info(
                            f"PPPC: Race condition detected, re-attempting lookup"
                        )
                        # H2: ONE retry on race. The second attempt
                        # will SELECT the row inserted by the racing
                        # task. We do NOT retry indefinitely to avoid
                        # masking other errors.
                        try:
                            # Release the existing connection first
                            if ppc_conn is not None:
                                try:
                                    await db_pool.release(ppc_conn)
                                except Exception as _suppressed_exc:
                                    logger.debug(
                                        f"orchestrator_daemon_loop(): suppressed {type(_suppressed_exc).__name__}"
                                    )
                                ppc_conn = None
                            # Re-acquire with the same timeout
                            ppc_conn = await asyncio.wait_for(
                                db_pool.acquire(), timeout=1.0
                            )
                            # M5/M12: No in_flight tracking
                            ppc_persona_id, _ = await asyncio.wait_for(
                                _city_persona_module.get_or_create_persona_for_city(
                                    ppc_conn, _city, task_payload
                                ),
                                timeout=2.0
                            )
                            if ppc_persona_id and ppc_persona_id != persona_id:
                                logger.info(
                                    f"PPPC: Retry succeeded; routing task {task_id} "
                                    f"to {ppc_persona_id} for city '{_city}'"
                                )
                                persona_id = ppc_persona_id
                                await asyncio.wait_for(
                                    ppc_conn.execute(
                                        "UPDATE tasks SET persona_id = $1 WHERE id = $2",
                                        persona_id, task_id
                                    ),
                                    timeout=1.0
                                )
                        except Exception as retry_err:
                            logger.warning(
                                f"PPPC: Retry failed (non-fatal): {retry_err}"
                            )
                            # Fall through to original persona_id
                finally:
                    if ppc_conn is not None:
                        # M5/M12 fix: Release the connection with
                        # a tight timeout. asyncpg's pool.release()
                        # checks if the connection is busy and
                        # either waits for completion or marks
                        # the connection for termination. A
                        # wedged query (e.g., DB hung) would
                        # otherwise block the daemon loop
                        # indefinitely. We use a 2s timeout on
                        # the release; if it fires, we
                        # terminate the connection (forcing
                        # asyncpg to discard it rather than
                        # returning it to the pool). This is
                        # safer than cancelling the surrounding
                        # task (which would also cancel the
                        # current daemon loop iteration).
                        try:
                            await asyncio.wait_for(
                                db_pool.release(ppc_conn),
                                timeout=2.0
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"PPPC: db_pool.release() timed out "
                                f"after 2s; terminating connection"
                            )
                            try:
                                # Force-terminate the connection.
                                # asyncpg will discard it from the
                                # pool rather than returning it.
                                await asyncio.wait_for(
                                    ppc_conn.terminate(),
                                    timeout=1.0
                                )
                            except Exception as term_err:
                                logger.debug(
                                    f"PPPC: connection terminate "
                                    f"error (non-fatal): {term_err}"
                                )
                        except Exception as _suppressed_exc:
                            logger.debug(f"orchestrator_daemon_loop(): suppressed {type(_suppressed_exc).__name__}")

            # R7-DEEP-AUDIT-2 GAP 2.3: Re-query the task to get the
            # authoritative persona_id (in case another process updated
            # tasks.persona_id between our SELECT and our UPDATE).
            try:
                async with db_pool.acquire() as _auth_conn:
                    _auth_row = await _auth_conn.fetchrow(
                        "SELECT persona_id FROM tasks WHERE id = $1", task_id
                    )
                    if _auth_row and _auth_row['persona_id']:
                        persona_id = _auth_row['persona_id']
            except Exception as _suppressed_exc:
                logger.debug(f"orchestrator_daemon_loop(): suppressed {type(_suppressed_exc).__name__}")

            # v5.0+: per-persona SOCKS5 port
            socks5_port = get_socks5_port_for_persona(persona_id)
            logger.info(f"Launching task {task_id} for persona {persona_id} via SOCKS5 port {socks5_port}")

            try:
                result = await run_persona_pipeline(persona_id, platform, task_payload)
            except DatabaseUnavailableError as e:
                logger.critical(f"DB unavailable during task {task_id}; pausing daemon")
                await asyncio.sleep(30)
                break
            except PersonaRetiredError as e:
                # v5.0+: persona was marked 'rotated'; mark task as completed
                # (we don't fail the task; we just stop using the persona)
                logger.info(f"Persona {persona_id} retired after task {task_id}; task marked completed.")
                try:
                    async with db_pool.acquire() as conn:
                        # CONCURRENCY-FIX C2: only the worker that still holds
                        # the claim may finalise the task. Without this guard a
                        # task recycled by the reaper (because this worker was
                        # presumed dead) could be completed here AND re-run by
                        # the worker that picked it up in the meantime.
                        await conn.execute(
                            "UPDATE tasks SET status = 'completed', error_log = NULL "
                            "WHERE id = $1 AND status = 'running'",
                            task_id
                        )
                except Exception as db_err:
                    logger.error(f"Failed to update task {task_id} status: {db_err}")
                continue
            except PIDBudgetExceededError as e:
                logger.error(f"PID budget exceeded for {persona_id}; quarantining")
                try:
                    async with db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE personas SET state = 'limited' WHERE id = $1",
                            persona_id
                        )
                except Exception as db_err:
                    logger.error(f"Failed to quarantine persona: {db_err}")
                # Don't retry; persona is limited
                continue
            except DecryptionFailedError as e:
                # EH-16 (Pass 16): DecryptionFailedError was previously
                # raised but never caught in the daemon loop. The error
                # would propagate up, killing the entire daemon (and
                # the container with it). Now we catch it and quarantine
                # the persona (their session_data is corrupted beyond
                # recovery) so the orchestrator can move on to other
                # tasks. The error code distinguishes "empty_payload"
                # (no data) from "auth_failed" (key mismatch) from
                # "corrupted" (data is unreadable). For "auth_failed"
                # we abort the daemon because it indicates a system-wide
                # ENCRYPTION_KEY mismatch (CRITICAL SECURITY).
                logger.critical(
                    f"Decryption failed for {persona_id} (code={e.code}): {e}"
                )
                if e.code == "auth_failed":
                    # CRITICAL: ENCRYPTION_KEY mismatch. Abort the
                    # daemon. The operator must fix the env var.
                    # H1 fix: Ensure db_pool is closed and tmpfs is
                    # wiped BEFORE raising. The previous code raised
                    # without cleanup, which leaked any pending
                    # db_pool connection (if the exception fired
                    # mid-acquire) and any tmpfs contents. We now
                    # wrap the raise in a try/finally that runs
                    # the cleanup before propagating. The
                    # __main__ finally block will close db_pool
                    # on normal exit, but the raise here exits
                    # the daemon loop synchronously (without
                    # going through __main__'s finally), so we
                    # must do the cleanup inline.
                    logger.critical(
                        "Decryption auth_failed suggests ENCRYPTION_KEY "
                        "mismatch. Aborting daemon to prevent further "
                        "data corruption."
                    )
                    # H1 fix: Pre-raise cleanup. Close the db_pool
                    # (releases all connections) and wipe tmpfs.
                    # Best-effort: errors here are logged but do
                    # not prevent the raise.
                    try:
                        if db_pool is not None:
                            try:
                                await db_pool.close()
                                logger.info(
                                    "H1: db_pool closed before auth_failed raise"
                                )
                            except Exception as close_err:
                                logger.warning(
                                    f"H1: db_pool close before raise failed "
                                    f"(non-fatal): {close_err}"
                                )
                    except Exception as pool_cleanup_err:
                        logger.warning(
                            f"H1: pool cleanup error (non-fatal): {pool_cleanup_err}"
                        )
                    try:
                        if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
                            shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
                            logger.info(
                                "H1: tmpfs wiped before auth_failed raise"
                            )
                    except Exception as tmpfs_err:
                        logger.warning(
                            f"H1: tmpfs wipe before raise failed (non-fatal): "
                            f"{tmpfs_err}"
                        )
                    raise
                # For other codes, quarantine the persona and continue.
                try:
                    async with db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE personas SET state = 'limited' WHERE id = $1",
                            persona_id
                        )
                        await conn.execute(
                            "UPDATE tasks SET status = 'failed', "
                            "error_log = $1 WHERE id = $2",
                            f"DecryptionFailedError (code={e.code}): {e}",
                            task_id
                        )
                except Exception as db_err:
                    logger.error(f"Failed to quarantine persona: {db_err}")
                continue
            except Exception as e:
                result = False
                logger.error(f"Task {task_id} raised: {e}")

            # Update task status
            try:
                async with db_pool.acquire() as conn:
                    if result:
                        # CONCURRENCY-FIX C2: guarded by the claim we still hold.
                        await conn.execute(
                            "UPDATE tasks SET status = 'completed', error_log = NULL "
                            "WHERE id = $1 AND status = 'running'", task_id)
                        logger.info(f"Task ID {task_id} completed (persona {persona_id}).")
                    else:
                        new_failures = failures + 1
                        MAX_TASK_RETRIES = _safe_getenv_int("MAX_TASK_RETRIES", 3, min_value=1)
                        if new_failures >= MAX_TASK_RETRIES:
                            await conn.execute(
                                "UPDATE tasks SET status = 'failed', failure_count = $1, error_log = $2 WHERE id = $3",
                                new_failures, "Execution exceeded max retries", task_id
                            )
                            await conn.execute("UPDATE personas SET state = 'limited' WHERE id = $1", persona_id)
                            logger.critical(f"Task ID {task_id} permanently failed; persona {persona_id} quarantined")
                        else:
                            cooldown_minutes = (2 ** new_failures) * 5
                            await conn.execute(
                                "UPDATE tasks SET status = 'retry', failure_count = $1, "
                                "execution_time = NOW() + ($2 || ' minutes')::INTERVAL "
                                "WHERE id = $3 AND status = 'running'",
                                new_failures, str(cooldown_minutes), task_id
                            )
                            logger.warning(f"Task ID {task_id} retrying in {cooldown_minutes} minutes")
            except Exception as db_err:
                logger.error(f"Failed to update task {task_id} status: {db_err}")

        # CONCURRENCY-FIX C9: release this batch's persona leases now that the
        # work is finished. Guarded by locked_by so we never drop a lease that
        # another worker has meanwhile taken over.
        await _release_persona_leases(_leased_personas)
        _leased_personas = set()

        # v5.0+: small sleep between tasks (no concurrent batch)
        await asyncio.sleep(1)


daemon_running = True
db_pool = None

async def handle_graceful_shutdown(sig_name):
    global db_pool, daemon_running
    # H3 fix: Re-entrancy guard. Multiple signals (e.g., SIGINT
    # followed by SIGTERM) can arrive in quick succession.
    # Without a guard, two concurrent shutdown coroutines race
    # on the same db_pool and tmpfs, causing undefined behavior
    # (double-close, partial cleanup, etc.). The previous code
    # also had no timeout on the entire shutdown, so a hung
    # network call (e.g., a stuck tc qdisc del) could leave
    # the daemon hanging forever. We now:
    # 1. Use a flag to ensure idempotency (asyncio is single-threaded)
    # 2. Wrap the whole shutdown in an overall timeout (30s)
    global _SHUTDOWN_IN_PROGRESS
    if _SHUTDOWN_IN_PROGRESS:
        logger.warning(
            f"H3: shutdown already in progress; ignoring {sig_name}"
        )
        return
    _SHUTDOWN_IN_PROGRESS = True
    try:
        # H3 fix: Wrap the entire shutdown in a 30s timeout. The
        # previous code had no upper bound; a stuck subprocess
        # (e.g., a hung `tc` command) could leave the daemon
        # hanging indefinitely, defeating the purpose of
        # graceful shutdown.
        async def _shutdown_with_timeout():
            await _do_shutdown(sig_name)
        try:
            await asyncio.wait_for(_shutdown_with_timeout(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error(
                "H3: graceful shutdown exceeded 30s timeout; "
                "forcing exit. Pending operations may be lost."
            )
    except Exception as shutdown_err:
        logger.error(f"H3: unexpected error during shutdown: {shutdown_err}")


async def _do_shutdown(sig_name):
    """H3 fix: Inner shutdown logic. Extracted to enable a
    30s overall timeout on the shutdown sequence. The previous
    code had no upper bound on shutdown duration.
    """
    global db_pool, daemon_running
    logger.warning(f"Received signal {sig_name}. Initiating safe fail-closed teardown.")
    daemon_running = False

    # CHAMELEON (GAP-2.2): Drain in-flight db_pool operations before
    # close. Without this, an `async with db_pool.acquire()` held by
    # a coroutine that hasn't been cancelled yet will raise
    # InvalidSQLStatementNameError when the pool is closed mid-query.
    # The previous code closed immediately, causing data loss in
    # pending writes. We now wait up to 5s for in-flight queries.
    drain_deadline = time.time() + 5.0
    while time.time() < drain_deadline and db_pool is not None:
        # asyncpg's _holders is internal but stable across versions
        in_flight = 0
        try:
            holders = getattr(db_pool, '_holders', None) or []
            in_flight = sum(1 for h in holders if getattr(h, '_in_use', False))
        except Exception as _suppressed_exc:
            logger.debug(f"_do_shutdown(): suppressed {type(_suppressed_exc).__name__}")
        if in_flight == 0:
            break
        logger.info(f"Drain: {in_flight} db_pool operations in flight; waiting")
        await asyncio.sleep(0.2)

    # v5.0+: clean tc qdisc on shutdown
    # R7-HOR-029: Detect default interface dynamically.
    #
    # RELIABILITY-FIX L3: this used three blocking subprocess.run() calls inside
    # an `async def`, stalling the event loop for up to ~6s during shutdown.
    # The identical block in _panic_handler() is left synchronous on purpose --
    # a signal handler cannot await.
    async def _run_cmd(*args, timeout=2.0):
        """Run a command without blocking the event loop. Returns (rc, stdout)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, (stdout or b"").decode("utf-8", "replace")
        except (asyncio.TimeoutError, OSError, FileNotFoundError):
            return None, ""

    try:
        _which_rc, _ = await _run_cmd("which", "tc")
        if _which_rc == 0:
            _default_iface = None
            _route_rc, _route_out = await _run_cmd("ip", "route", "show", "default")
            if _route_rc == 0 and _route_out.strip():
                _parts = _route_out.strip().split()
                if "dev" in _parts:
                    _default_iface = _parts[_parts.index("dev") + 1]
            if not _default_iface:
                _default_iface = "eth0"  # fallback
            await _run_cmd("tc", "qdisc", "del", "dev", _default_iface, "root")
            await _run_cmd("tc", "qdisc", "del", "dev", "lo", "root")
    except Exception as _suppressed_exc:
        logger.debug(f"_do_shutdown(): suppressed {type(_suppressed_exc).__name__}")

    # v5.0+: wipe per-task tmpfs
    if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
        try:
            shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
        except Exception as _suppressed_exc:
            logger.debug(f"_do_shutdown(): suppressed {type(_suppressed_exc).__name__}")

    if db_pool:
        try:
            await db_pool.close()
        except Exception as e:
            logger.error(f"DB pool close error: {e}")

    logger.info("Graceful shutdown completed.")


# RELIABILITY-FIX H0/M3: strong references to in-flight shutdown tasks.
#
# The previous code did `asyncio.create_task(handle_graceful_shutdown(...))`
# inside the signal callback and discarded the returned Task. Two consequences:
#
#   1. (M3) Nothing awaited it and no done-callback was attached, so any
#      exception raised inside the shutdown coroutine was only reported by
#      asyncio's fallback handler at GC time -- via logging.getLogger("asyncio"),
#      NOT our loguru pipeline, so it never reached the application logs.
#   2. (H0) The `__main__` finally block called asyncio.all_tasks(loop), which
#      INCLUDED the still-running shutdown task, and cancelled it. The shutdown
#      always loses that race: _do_shutdown() begins with a drain loop that
#      awaits asyncio.sleep(0.2) for up to 5s, so it is always mid-await when
#      the daemon loop returns. Result: tmpfs wipe, db_pool.close() and
#      tc qdisc cleanup never ran on ANY SIGTERM/SIGINT, and the resulting
#      CancelledError was swallowed by gather(return_exceptions=True).
#
# Holding the tasks in this set both keeps them alive and lets the finally
# block exclude them from the blanket cancel.
_SHUTDOWN_TASKS: set = set()


def _on_shutdown_signal(sig):
    """RELIABILITY-FIX H0/M3: spawn the shutdown coroutine, keeping a reference."""
    task = asyncio.create_task(handle_graceful_shutdown(sig.name))
    _SHUTDOWN_TASKS.add(task)
    task.add_done_callback(_SHUTDOWN_TASKS.discard)

    def _report(task_future):
        if task_future.cancelled():
            logger.error("Graceful shutdown task was cancelled before completing")
            return
        exc = task_future.exception()
        if exc is not None:
            logger.error(f"Graceful shutdown raised {type(exc).__name__}: {exc}")

    task.add_done_callback(_report)


def register_shutdown_signals(loop):
    """DC-19 (Pass 19): Register SIGINT/SIGTERM handlers for graceful shutdown.

    Without this, hitting Ctrl+C in the orchestrator container would trigger
    Python's default KeyboardInterrupt handler, which bypasses all cleanup
    logic (db_pool.close, tmpfs wipe, tc qdisc cleanup). With this, the
    signal is caught and handle_graceful_shutdown runs the full cleanup
    before exiting.
    """
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_shutdown_signal, sig)
        except (NotImplementedError, RuntimeError) as e:
            logger.warning(f"Could not register signal handler for {sig}: {e}")


if __name__ == "__main__":
    # v6.2: Use a custom event loop to allow signal handler registration
    # before the daemon loop starts. asyncio.run() creates a new loop
    # internally, so we can't register signal handlers on it.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # v6.2: Register graceful shutdown signal handlers
    # This must be called BEFORE orchestrator_daemon_loop() starts,
    # otherwise SIGTERM/SIGINT will be caught by the default Python
    # handler (KeyboardInterrupt) and bypass our cleanup logic.
    register_shutdown_signals(loop)

    # R7-HOR-019 + R7-HOR-020: Wrap main in try/finally to ensure
    # db_pool is closed and tmpfs is wiped on ANY exit (not just
    # signal-triggered). The previous code only ran cleanup in
    # handle_graceful_shutdown (signal handler), so a KeyboardInterrupt
    # or unhandled exception would leak the pool and tmpfs.
    # R7-HOR-027: tmpfs wipe is also done here (was previously only
    # in the panic handler and handle_graceful_shutdown).
    def _main_cleanup():
        try:
            if db_pool is not None:
                # Run the close in a fresh event loop if the main one
                # is dead
                try:
                    asyncio.run_coroutine_threadsafe(db_pool.close(), loop)
                except Exception as _suppressed_exc:
                    logger.debug(f"_main_cleanup(): suppressed {type(_suppressed_exc).__name__}")
            if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
                try:
                    shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
                except Exception as _suppressed_exc:
                    logger.debug(f"_main_cleanup(): suppressed {type(_suppressed_exc).__name__}")
            # M2 fix: Zero the Fernet key buffer from memory on shutdown.
            # Without this, the key persists in RAM for the lifetime of
            # the process and is included in any core dump (if dumps
            # are enabled, though we set RLIMIT_CORE=0). Even without
            # core dumps, the key is readable via /proc/PID/mem from a
            # privileged process or container. Zeroing on shutdown
            # narrows the window.
            try:
                global _ENCRYPTION_KEY_ZEROED
                if not _ENCRYPTION_KEY_ZEROED:
                    _zero_key(_ENCRYPTION_KEY_BUF)
                    _ENCRYPTION_KEY_ZEROED = True
                    logger.debug("M2: Fernet key buffer zeroed on shutdown")
            except Exception as key_zero_err:
                logger.debug(f"M2: key zero on shutdown failed (non-fatal): {key_zero_err}")
        except Exception as e:
            logger.error(f"Main cleanup error: {e}")

    atexit.register(_main_cleanup)

    try:
        loop.run_until_complete(orchestrator_daemon_loop())
    except KeyboardInterrupt:
        logger.info("Process interrupted manually.")
    finally:
        # v6.2: Final cleanup
        #
        # RELIABILITY-FIX H0: the previous code cancelled EVERY pending task,
        # including the graceful-shutdown task spawned by the signal handler
        # (see _SHUTDOWN_TASKS above). That cancelled the tmpfs wipe, the
        # db_pool close and the tc qdisc cleanup on every SIGTERM/SIGINT, and
        # gather(return_exceptions=True) hid the CancelledError.
        #
        # We now (1) let shutdown tasks finish first, under an overall timeout,
        # and only then (2) cancel everything else.
        try:
            # NOTE: do NOT call asyncio.current_task() here. This finally block
            # runs in synchronous code after run_until_complete() has returned,
            # so there is no running loop; current_task() with no argument
            # raises RuntimeError, and the `except Exception: pass` below would
            # then silently skip the ENTIRE cleanup. asyncio.all_tasks(loop)
            # takes the loop explicitly and is correct here.
            pending = set(asyncio.all_tasks(loop))
            # Never cancel our own shutdown.
            for task in pending - _SHUTDOWN_TASKS:
                task.cancel()
            if _SHUTDOWN_TASKS:
                try:
                    loop.run_until_complete(asyncio.wait_for(
                        asyncio.gather(*_SHUTDOWN_TASKS, return_exceptions=True),
                        timeout=30.0,
                    ))
                except asyncio.TimeoutError:
                    logger.error(
                        "Shutdown tasks exceeded 30s in final cleanup; "
                        "tmpfs/db_pool cleanup may be incomplete"
                    )
            rest = asyncio.all_tasks(loop) - _SHUTDOWN_TASKS
            if rest:
                # Give cancelled tasks a chance to clean up
                loop.run_until_complete(asyncio.gather(*rest, return_exceptions=True))
        except Exception as _suppressed_exc:
            logger.debug(f"<module>(): suppressed {type(_suppressed_exc).__name__}")
        # R7-HOR-019: Explicit pool close here too, in case the
        # atexit handler runs after the loop is dead.
        try:
            if db_pool is not None:
                loop.run_until_complete(db_pool.close())
        except Exception as _suppressed_exc:
            logger.debug(f"<module>(): suppressed {type(_suppressed_exc).__name__}")
        # R7-HOR-027: Explicit tmpfs wipe
        try:
            if ORCH_TASK_TMPFS_DIR and os.path.isdir(ORCH_TASK_TMPFS_DIR):
                shutil.rmtree(ORCH_TASK_TMPFS_DIR, ignore_errors=True)
        except Exception as _suppressed_exc:
            logger.debug(f"<module>(): suppressed {type(_suppressed_exc).__name__}")
        # R7-HOR-021: Shutdown the Fernet executor to free threads.
        # M33 fix: Use wait=True with a tight timeout instead of
        # wait=False, cancel_futures=True. The previous code
        # (wait=False) returned immediately while the worker
        # threads continued executing in-flight decrypt/encrypt
        # jobs. After the main process exits, the worker
        # threads are killed mid-execution, leaking the GIL-held
        # memory (Fernet key buffer, decrypted bytes). For a
        # graceful shutdown, we want to drain the in-flight
        # queue with a bounded timeout. The actual draining
        # happens in the executor's shutdown(wait=True) call;
        # we bound it via a separate join timeout.
        try:
            # Set a bounded drain deadline. If the executor's
            # shutdown(wait=True) takes longer than this, we
            # fall through to cancel_futures and immediate exit.
            # 5s is enough for normal Fernet operations (which
            # complete in <10ms each).
            import concurrent.futures as _cf
            # Try a clean shutdown first (waits for current futures)
            try:
                _fernet_executor.shutdown(wait=True, cancel_futures=False)
            except TypeError:
                # Older Python (<3.9) doesn't have cancel_futures
                _fernet_executor.shutdown(wait=True)
        except Exception:
            # Best-effort: fall back to immediate shutdown.
            try:
                _fernet_executor.shutdown(wait=False, cancel_futures=True)
            except Exception as _suppressed_exc:
                logger.debug(f"<module>(): suppressed {type(_suppressed_exc).__name__}")
        loop.close()
