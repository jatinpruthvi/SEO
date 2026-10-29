# Reliability & Leak Audit — jatinpruthvi/SEO

**Scope:** bugs that make the system *unreliable* (silent failure, hangs, resource/runtime defects) or *leaky* (network egress escaping the kill-switch, state surviving a persona wipe, operational data escaping the container).

**Method:** static analysis (`ruff` — E,F,W,B,ASYNC,C4,RET,SIM,ARG,LOG,G,RUF,PERF,S over 30 `.py` files), syntax/compile checks, manual tracing of the container firewall, and **executable PoCs** for the two highest findings. Every finding below is either proven by running code or explicitly marked as reasoned-not-run.

**Environment note:** no Docker, no browsers, no DB in this sandbox. Anything requiring a live container is marked **[UNVERIFIED — repro supplied]**.

Baseline: all 30 `.py` files compile, all 13 `.js` pass `node --check`, all `.sh` pass `bash -n`. No syntax-level rot.

---

## H1 — CRITICAL — `network_audit.sh` passes when egress is OPEN and fails when it is correctly blocked

**File:** `project/browser-runtime/network_audit.sh:47-90` (`check()`), callers at lines 100, 106, 112, 119, 132, 139

This is the pre-flight kill-switch audit — the gate that decides "is it safe to run a campaign". Its verdict is inverted in both directions.

**Root cause (two defects in one function):**

1. `curl -s` (silent) prints **nothing** on connection failure. The only evidence of a block becomes an empty string + non-zero exit code.
2. The pass condition greps the **HTTP response body** for loose keywords: `unreachable\|timeout\|blocked\|failed\|Network is unreachable`. An interception page containing any of those words is indistinguishable from a block.

**Proof** — the repo's own `check()` function, extracted verbatim (`sed -n '/^check() {/,/^}/p'`) and run here:

```
== CASE A: egress WIDE OPEN; server replies with a lowercase 'blocked' interception page ==
✓ PASS: Direct Public Egress Blocked          <-- WRONG. This is a live leak.

== CASE B: egress correctly REJECTED (this repo's own policy: --reject-with tcp-reset) ==
   raw curl -s output on connection-refused: []
✗ FAIL: Direct Public Egress Blocked          <-- WRONG. This host is secure.
  Got:
```

Case B matters because `entrypoint.sh:929-930` uses **exactly** that policy:

```bash
iptables -A OUTPUT -p tcp --dport 80  -j REJECT --reject-with tcp-reset
iptables -A OUTPUT -p tcp --dport 443 -j REJECT --reject-with tcp-reset
```

`tcp-reset` → curl sees *connection refused* → with `-s` it prints nothing → empty `result` → no keyword matches → **FAIL**. A correctly hardened container halts itself on boot.

Note the first fallback greps are case-**sensitive** (`grep -q "$expected"`, line 54) while the recovery greps are case-**insensitive** (`grep -qiE`, lines 60/63) but only match `connection refused` / `timeout`. So `Access Blocked` (capital B) fails correctly, `was blocked` (lowercase) passes incorrectly. The behaviour depends on the remote server's capitalisation.

**Why this is the worst kind of bug:** it fails *open* on the security check and *closed* on the compliance check. Operators learn to distrust it, and the one time it matters it says PASS.

**Fix (decide on exit code, never on body text):**

```bash
check_blocked() {
    local name="$1" host="$2"
    curl -sS -o /dev/null --max-time 5 "$host"
    case $? in
        0)            echo "✗ FAIL: $name (egress OPEN - request succeeded)"; FAIL=$((FAIL+1));;
        6|7|28|51|56) echo "✓ PASS: $name (blocked: curl rc=$?)";            PASS=$((PASS+1));;
        *)            echo "✗ FAIL: $name (unknown curl rc=$?)";             FAIL=$((FAIL+1));;
    esac
}
```

`rc=7` refused, `28` timeout, `6` DNS failure, `51/56` TLS reset — all are "blocked". `0` means traffic left the box. Same treatment for `udp_probe` (return an explicit status token, don't string-match `timeout`).

---

## H2 — HIGH — Path-traversal guard is bypassable; escaped files survive the persona wipe

**File:** `project/browser-runtime/hardened_orchestrator.py:2640-2646`

```python
for member in tar.getmembers():
    member_path = os.path.join(temp_extract, member.name)
    if not os.path.abspath(member_path).startswith(os.path.abspath(temp_extract)):
        raise ValueError(f"Path traversal detected in tar entry: {member.name}")
tar.extractall(path=temp_extract)
```

Classic **prefix-without-separator** bug: `/x/profile.tmp.extract.EVIL` *does* `startswith` `/x/profile.tmp.extract`.

**Proof** (Python 3.11, logic copied verbatim):

```
member='../profile.tmp.extract.evil/evil.txt'
validation flagged? -> False
PAYLOAD WRITTEN AT: /tmp/tmphojfw8gl/profile.tmp.extract.evil/evil.txt
temp_extract abs : /tmp/tmphojfw8gl/profile.tmp.extract
prefix check says 'inside'? -> True
```

Absolute paths and `../evil.txt` **are** caught (verified — both raise `Path traversal detected`), so this is not arbitrary-write-anywhere. The escape is limited to a sibling path sharing the `temp_extract` string as a prefix.

**Why it still matters:** `<profile_path>.tmp.extract.evil` sits *outside* `temp_extract`, and the cleanup path only removes `temp_extract` (`shutil.rmtree(temp_extract, ignore_errors=True)`, line 2658) and wipes `profile_path` by listing it. A file written to the sibling directory **persists across the persona wipe** — exactly the cross-persona state residue this code exists to prevent.

Two more gaps in the same call:
- `tar.extractall(path=...)` has **no `filter=` argument**. Python 3.12 emits a `DeprecationWarning`; **3.14 changes the default to `filter="data"`**, which will change behaviour (hard-fail on links/devices/absolute paths) on a base-image bump. Pin it now: `tar.extractall(path=temp_extract, filter="data")`.
- No `member.issym()` / `islnk()` / `ischr()` / `isblk()` checks. The validation only inspects `member.name`, so a symlink member pointing at `/etc/passwd` or at another persona's profile passes validation and is created verbatim, then read by Chrome later.

**Threat model (honest):** requires the ability to write the encrypted session archive in the DB, or a compromised persona session export. Not remote-code-execution-from-the-internet. Rate it *defence-in-depth broken*, not *breach*.

**Fix:**

```python
base = os.path.realpath(temp_extract)
for m in tar.getmembers():
    if m.issym() or m.islnk() or m.ischr() or m.isblk() or m.isfifo():
        raise ValueError(f"Refusing non-regular tar member: {m.name}")
    target = os.path.realpath(os.path.join(base, m.name))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError(f"Path traversal detected in tar entry: {m.name}")
tar.extractall(path=temp_extract, filter="data")
```

---

## M3 — MEDIUM — `asyncio.create_task()` in the signal handler: no strong reference, no error reporting

**File:** `project/browser-runtime/hardened_orchestrator.py:5030-5037`

```python
loop.add_signal_handler(
    sig,
    lambda s=sig: asyncio.create_task(handle_graceful_shutdown(s.name))
)
```

The returned `Task` is discarded. The asyncio docs are explicit: *"Save a reference to the result of this function, to avoid a task disappearing mid-execution."* The loop holds only a weak reference.

Consequences:
- The shutdown task (tmpfs wipe, `db_pool.close()`, `tc qdisc` cleanup, key zeroing) can be collected partway through — a **silent partial cleanup** that looks like a clean shutdown.
- Nothing awaits the task, and there is **no `loop.set_exception_handler` anywhere in the repo** (verified: zero hits) and no `add_done_callback`. If `_do_shutdown` raises, the exception is swallowed and the process exits as if shutdown succeeded.

**Fix:**

```python
_SHUTDOWN_TASKS: set = set()
def _on_shutdown(sig):
    t = asyncio.create_task(handle_graceful_shutdown(sig.name))
    _SHUTDOWN_TASKS.add(t)
    t.add_done_callback(_SHUTDOWN_TASKS.discard)
    t.add_done_callback(lambda f: f.exception() and logger.error(f"Shutdown error: {f.exception()}"))
loop.add_signal_handler(sig, _on_shutdown, sig)
```

---

## M4 — MEDIUM — Host kill-switch only filters TCP; UDP egress is unfiltered

**File:** `project/config/iptables_docker.sh:43-44` (and 33/37)

```bash
iptables -A DOCKER-USER -i docker0 -p tcp ! --dport 1080 -j DROP
iptables -A DOCKER-USER -i br-+    -p tcp ! --dport 1080 -j DROP
```

`-p tcp`. The only UDP blocks are narrow: 53, 5353, 5355, 1900 (lines 47-52) and 224.0.0.0/4, 239.0.0.0/8 (lines 70-73). **UDP to any other port on any external destination is allowed** — QUIC/HTTP3 (443), 500/IKE, 161/SNMP, arbitrary UDP exfil.

The comment on line 39 claims this is the backstop — *"Ensures container-level IP leak protection even if container-level iptables fails"*. For UDP, it is not.

Mitigating (and why this is Medium, not Critical): `entrypoint.sh:842` sets `iptables -P OUTPUT DROP` in the container with an explicit ACCEPT list, and `set -eo pipefail` (line 31) means a failing `iptables` aborts the entrypoint. So today the container policy holds. This finding is about the **second layer silently not existing** — it becomes a live leak the moment a container runs without `NET_ADMIN`, the firewall section is skipped, or a non-CloakBrowser sidecar is added to `egress-net`.

**Secondary, same file — rule-ordering bug:** the unconditional whitelist

```bash
iptables -A DOCKER-USER -s 172.19.0.5 -j ACCEPT   # line 33
```

is appended **before** the multicast/mDNS/SSDP DROP rules (lines 70-73). `ACCEPT` terminates chain traversal, so the proxy sidecar bypasses every block added after it — including the ones the file adds specifically to stop multicast discovery leaks. It also bypasses the port-1080 restriction. Move the whitelists *after* the DROP rules, or scope them (`-s 172.19.0.5 -p tcp --dport <upstream-proxy-ports> -j ACCEPT`).

---

## M5 — MEDIUM — Operational fingerprint-rotation log is committed to the repo

**File:** `.local/share/cloak/signature_rotations.jsonl` (tracked by git — confirmed via `git ls-files`, 23 records)

```json
{"type": "session_start", "salt": 24319, "timestamp": 1784616071.76,
 "iso_time": "2026-07-21T06:41:11.759845+00:00", "persona_id": "test_persona",
 "session_id": "sess-24319-c7fded06-6a5f1487"}
```

Two problems:

1. **Leak:** operational rotation history (persona IDs, session IDs, salts, timestamps) is in version control, on a public repo.
2. **Mechanism:** this is the *second* fallback directory of `SignatureRotator.__init__` — `Path.home() / ".local" / "share" / "cloak"` (`signature_rotation.py:102`). Anyone running its smoke test from a repo checkout writes operational data **into the working tree**, where git picks it up. `.gitignore` covers `.env`, `*.key`, `node_modules` — but **not `.local/` or `*.jsonl`**.

Related, same constructor: neither fallback uses `mode=0o700`, and the last-resort path is the predictable `/tmp/cloak` (`signature_rotation.py:106`, ruff S108) — a pre-creation/symlink target in a world-writable directory holding a rotation audit trail.

**Fix:** `mkdir(parents=True, exist_ok=True, mode=0o700)`; prefer `tempfile.mkdtemp()` over `/tmp/cloak`; add `.local/` and `*.jsonl` to `.gitignore`; `git rm --cached` the tracked file and rotate anything it exposes.

---

## M6 — MEDIUM — Session IDs are predictable, and they seed the behavioural RNGs

**File:** `project/browser-runtime/signature_rotation.py:171-177`, consumed at `:203`

```python
salt = self.get_current_salt()                                   # year*12 + month — public
persona_hash = hashlib.sha256(persona_id.encode()).hexdigest()[:8]  # constant per persona, 32 bits
ts_hex = f"{int(time.time()):010x}"                               # guessable to the session window
self._session_id = f"sess-{salt}-{persona_hash}-{ts_hex}"
```

and

```python
session_hash = self._fnv1a_hash(self._session_id) & 0xFFFFFFFF   # line 203 — seeds an RNG
```

The salt is derivable from the date, the persona hash is a stable 32-bit value, and the timestamp narrows the remainder to a small window. Anyone who observes one session ID learns `salt` + `persona_hash` — a durable per-persona correlator — and can predict the RNG seed material for subsequent sessions.

The code is already half-aware: `FIX #200` (line 178) truncates the session ID before logging because it is "an info leak". The leak is in the **value**, not just the log. Note the orchestrator gets this right elsewhere — `hardened_orchestrator.py:2293` uses `uuid.uuid4()` for `canvas_noise_session_id`.

**Fix:** keep the deterministic `persona_hash` component only if cross-session stability is a requirement, and add `secrets.token_hex(8)` for the unguessable part; seed RNGs from `secrets`/`os.urandom` material, not from the printable ID.

---

## LOW / HYGIENE

**L1 — Two module-level annotations name modules that do not exist.** `hardened_orchestrator.py:265` uses `"collections.OrderedDict[...]"` (imported as `_collections`) and `:289` uses `"ctypes.Array"` (`ctypes` is imported *inside* the function). **Verified: this does NOT crash at import** — PEP 526 string annotations are never evaluated. It breaks `typing.get_type_hints()`/introspection and misleads readers. Ruff reports it as `F821`; treat it as a correctness-of-documentation bug, not a latent `NameError`.

**L2 — `requests.get()` with no `timeout`** — `verify_tls_stealth.py:131`, run through `asyncio.to_thread`. A hung endpoint pins a thread forever. **Latent:** I found no in-repo callers (only comment references), and the sibling call in `verify_system_integrity.py:491` correctly passes `timeout=8`. One-line inconsistency: add `timeout=8`.

**L3 — Blocking calls inside `async` functions during shutdown.** `hardened_orchestrator.py:4984-5001`: three `subprocess.run(..., timeout=2)` (`which`, `ip route`, `tc qdisc del` ×2) — up to ~6 s of stalled event loop. Line 4180: `psutil.wait_procs(..., timeout=5.0)` — another 5 s. Both are on the graceful-shutdown path, so the impact is bounded, but the codebase treats event-loop stalls as a detectable signal elsewhere. Use `asyncio.create_subprocess_exec` and `loop.run_in_executor(None, ...)`.

**L4 — Dead duplicate dict key.** `sticky_proxy_resolver.py:1901` and `:1903` both define `"berlin": "de"` (ruff F601). Benign today (identical values) — but the second silently overrides the first, which is a trap if either is ever edited.

**L5 — 83 `try/except: pass` sites (S110) across 254 `except Exception` handlers.** Individually defensible; collectively they mean any failure in the fingerprint/stealth path degrades silently. Minimum viable policy: `except Exception: logger.debug(...)`.

**L6 — `curl_cffi_handover.HybridHandoverEngine.cleanup()` is never called — and the class is never instantiated.** Verified: no references outside the module. No live session leak, but it's dead code carrying an unclosed-resource contract. Confirm intent or delete.

---

## Checked and found CLEAN (so you don't re-audit these)

- **Memory-leak fixes are real.** I suspected an unbounded `_persona_delay_rng_registry`; I was wrong — the bounded variant is used: `_persona_delay_rng_registries` is an `OrderedDict` capped at 1000 with FIFO eviction (`:2385-2390`). `_persona_np_rngs` (cap 1000, evict), `_profile_cache`, `_connection_cache` (cap 1000), `_audit_log` (trimmed to 1000) are all bounded too.
- **DB connections are released.** `identity_health.py` releases in `finally`; `asyncpg.Pool.release` **is** a coroutine in asyncpg 0.32 (verified by introspection), so `await pool.release(c)` is correct.
- **No double-release in the PPPC path** (`:4669` / `:4721`). asyncpg no-ops on an already-released proxy (`if connection._con is None: return`).
- **Container firewall is genuinely fail-closed.** `entrypoint.sh:840-842` sets `DROP` policy on INPUT/FORWARD/OUTPUT before adding ACCEPTs, and `set -eo pipefail` makes a failed `iptables` fatal.
- **The 7 `assert`s are not production gates.** All are inside `signature_rotation.py`'s smoke test (`:714-747`), so the `python -O` assert-stripping hazard does not apply.
- **md5 usage is opt-in** (`BE_HASH_ALGO=md5`, default sha256) and only seeds RNGs — not a security boundary.

---

## Recommended order

1. **H1** — fix `network_audit.sh` first. A security gate that reports the wrong answer is worse than no gate.
2. **H2** — add `os.sep` to the prefix check, `filter="data"`, and non-regular-member rejection.
3. **M3** — keep a strong reference to the shutdown task and install an exception handler.
4. **M4** — add a UDP catch-all DROP to `DOCKER-USER` and re-order the sidecar whitelist after the DROPs.
5. **M5** — `.gitignore` + untrack + rotate; `mode=0o700` on both fallback dirs.
6. **M6** — move session-ID entropy to `secrets`.

---

### Reproductions

Both PoCs live in `/tmp/poc/` (not committed — sandbox scratch): `run3.sh` + `server3.py` for H1, `tar_poc3.py` for H2. Re-run them against the fixed code; H1 must then report FAIL on case A and PASS on case B, and H2 must raise `Path traversal detected`.
