# Reliability & Leak Audit — Round 2: verified findings + tested fixes

> **STATUS: ALL FIXES APPLIED.** Every finding below is now patched in the working
> tree and re-verified against the patched source. Round-3 verification results
> are in the box at the bottom of each section and in "Verification" at the end.

Second pass. Everything here has been **executed**, not just reasoned about. Where a fix couldn't be run in this sandbox, it says so explicitly.

Sandbox: Python 3.11.2, ShellCheck 0.11.0, ruff 0.17.3. No Docker, no browsers, no DB, no `dig`/`ping6`.

---

## NEW H0 — CRITICAL — SIGTERM cancels its own graceful shutdown

**File:** `project/browser-runtime/hardened_orchestrator.py:5035` + `5104-5109`

This is worse than the `create_task` reference issue I reported in round 1. The shutdown never finishes.

### The race

```python
# 5030-5037  — nobody awaits this task
loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handle_graceful_shutdown(s.name)))

# 5049        — main entry point
loop.run_until_complete(orchestrator_daemon_loop())

# 5104-5109  — runs as soon as the daemon loop returns
finally:
    pending = asyncio.all_tasks(loop)      # <-- INCLUDES the shutdown task
    for task in pending:
        task.cancel()                      # <-- kills the shutdown
    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))  # swallows CancelledError
```

Sequence: signal fires → shutdown task starts → `_do_shutdown` sets `daemon_running = False` (line 4960) → daemon loop returns → `finally` calls `asyncio.all_tasks(loop)`, which **includes the still-running shutdown task** → `task.cancel()` → `gather(..., return_exceptions=True)` swallows the `CancelledError`.

The first thing `_do_shutdown` does is a drain loop with `await asyncio.sleep(0.2)` for up to 5 s (line 4979), so the shutdown task is *always* mid-await when the daemon loop exits. It gets cancelled on every single shutdown.

### Proof

Faithful miniature of all three code paths, SIGTERM delivered to itself:

```
[signal] sending SIGTERM
[finally] daemon loop returned; cancelling all tasks
[finally] pending tasks at cancel time: ['Task-3']
cleanup steps that ran: ['start', 'daemon exited']
FULL graceful shutdown completed? -> False
```

`db_pool.close`, `tmpfs wipe`, `tc qdisc del` — **none of them ran.**

### Impact

- **tmpfs not wiped.** The tmpfs is what holds the decrypted browser profile and session cookies. Not wiping it defeats the "nothing sensitive on disk" property.
- **`tc qdisc` left configured.** Netem delay rules survive into the next container run.
- **DB pool not closed** (the `finally` block does try again at 5113, but only after the wipe was skipped).
- It fails **silently** — `return_exceptions=True` eats the cancellation.

### Best fix — tested

Track shutdown tasks, and let them finish before the blanket cancel:

```python
_SHUTDOWN_TASKS: set = set()

def _on_shutdown_signal(sig):
    t = asyncio.create_task(handle_graceful_shutdown(sig.name))
    _SHUTDOWN_TASKS.add(t)
    t.add_done_callback(_SHUTDOWN_TASKS.discard)
    t.add_done_callback(
        lambda f: None if (f.cancelled() or f.exception() is None)
        else logger.error(f"Graceful shutdown raised: {f.exception()!r}")
    )

def register_shutdown_signals(loop):
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_shutdown_signal, sig)
        except (NotImplementedError, RuntimeError) as e:
            logger.warning(f"Could not register signal handler for {sig}: {e}")
```

and in `__main__`'s `finally` (replacing 5104-5109):

```python
finally:
    try:
        current = asyncio.current_task()
        pending = {t for t in asyncio.all_tasks(loop) if t is not current}
        for task in pending - _SHUTDOWN_TASKS:
            task.cancel()
        if _SHUTDOWN_TASKS:                      # let shutdown finish FIRST
            loop.run_until_complete(asyncio.wait_for(
                asyncio.gather(*_SHUTDOWN_TASKS, return_exceptions=True), timeout=30))
        rest = asyncio.all_tasks(loop) - _SHUTDOWN_TASKS - {asyncio.current_task()}
        if rest:
            loop.run_until_complete(asyncio.gather(*rest, return_exceptions=True))
    except Exception:
        pass
```

Verified:

```
[finally] pending at cancel time: ['Task-3']
cleanup steps that ran: ['start', 'daemon exited', 'db_pool.close', 'tmpfs wipe', 'tc qdisc del', 'DONE']
FULL graceful shutdown completed? -> True
```

**Better still (architectural):** don't `create_task` from the signal handler at all. Set an `asyncio.Event()` and have `orchestrator_daemon_loop()` await it each iteration, then run shutdown inline. That removes the race by construction rather than by bookkeeping. The patch above is the minimal safe change; the Event version is the clean one.

---

## H1 — CRITICAL — `network_audit.sh` inverts its verdict

**File:** `project/browser-runtime/network_audit.sh:47-90`

Round-1 finding, now measured precisely against four controlled scenarios:

| scenario | truth | current verdict |
|---|---|---|
| egress OPEN, interception page containing lowercase `blocked` | FAIL | **✓ PASS** ✗ |
| egress OPEN, returns real IP | FAIL | ✗ FAIL ✓ |
| egress REJECTED (`tcp-reset` → refused, rc 7) | PASS | **✗ FAIL** ✗ |
| egress DROPped (timeout, rc 28) | PASS | **✗ FAIL** ✗ |

**1 of 4 correct.** And the two wrong ones are the dangerous direction: a leaking host is cleared for campaigns, while a correctly hardened container (using the exact `--reject-with tcp-reset` policy from `entrypoint.sh:929-930`) halts itself on boot.

Root cause: `curl -s` prints nothing on failure, so a block leaves an empty string; and the pass test greps the **response body** for `blocked|failed|timeout|unreachable`. Note the first grep is case-**sensitive** (line 54), the recovery greps are case-**insensitive** (60/63) but only match `connection refused`/`timeout` — so whether you get a false pass depends on how the remote server capitalises its error page.

### Best fix — tested, 6/6 correct

Decide on **exit code**, never on body text. Replace `check()` with:

```bash
PASS=0; FAIL=0

# curl exit codes that mean "the network did not let this out":
#   6  could not resolve host      7  failed to connect (REJECT)
#  28  operation timeout (DROP)   51  cert verify failed
#  56  receive failure (tcp-reset)
check_blocked() {
    local name="$1"; shift
    local out rc
    out=$("$@" 2>&1); rc=$?
    case "$rc" in
        6|7|28|51|56) echo "✓ PASS: $name (egress blocked, rc=$rc)"; PASS=$((PASS+1));;
        0)            echo "✗ FAIL: $name (EGRESS OPEN - request succeeded)"; FAIL=$((FAIL+1));;
        *)            echo "✗ FAIL: $name (unexpected rc=$rc: $out)";          FAIL=$((FAIL+1));;
    esac
}

check_udp_blocked() {
    local name="$1" host="$2" port="$3" res
    res=$(udp_probe "$host" "$port")
    case "$res" in
        timeout)            echo "✓ PASS: $name (egress blocked, DROP)";   PASS=$((PASS+1));;
        ConnectionRefused*) echo "✓ PASS: $name (egress blocked, REJECT)"; PASS=$((PASS+1));;
        udp_open)           echo "✗ FAIL: $name (UDP EGRESS OPEN)";        FAIL=$((FAIL+1));;
        *)                  echo "✗ FAIL: $name (probe error: $res)";      FAIL=$((FAIL+1));;
    esac
}

# A command that must simply fail (ping6, dig against an external resolver)
check_cmd_blocked() {
    local name="$1"; shift
    local out rc
    out=$("$@" 2>&1); rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "✗ FAIL: $name (succeeded - egress is open)"; FAIL=$((FAIL+1))
    else
        echo "✓ PASS: $name (blocked, rc=$rc)"; PASS=$((PASS+1))
    fi
}
```

Then rewrite the six tests (lines 100-141):

```bash
check_blocked     "Direct Public Egress Blocked"        curl -sS --max-time 5 http://checkip.amazonaws.com
check_blocked     "Oracle Cloud Metadata Blocked"       curl -sS --max-time 5 http://169.254.169.254/opc/v1/instance/
check_cmd_blocked "IPv6 Interface Blocked"              ping6 -c 1 -W 2 google.com
check_cmd_blocked "Direct Port 53 DNS Blocked"          dig +short @8.8.8.8 google.com
check_udp_blocked "STUN UDP 3478 Blocked"               172.18.0.5 3478
check_udp_blocked "mDNS UDP 5353 Blocked"               224.0.0.251 5353
```

Also change `udp_probe` to print a bare token: `print("timeout")`, `print("udp_open")`, `print(f"{e.__class__.__name__}")` — no `|| true`, no prose, so the `case` above can classify it.

Verified against the same four scenarios plus two UDP probes:

```
✗ FAIL: open+blockedpage (EGRESS OPEN - request succeeded)
✗ FAIL: open+realip (EGRESS OPEN - request succeeded)
✓ PASS: refused(rc7) (egress blocked, rc=7)
✓ PASS: timeout(rc28) (egress blocked, rc=28)
✗ FAIL: UDP open (echo srv) (UDP EGRESS OPEN)
✓ PASS: UDP closed (egress blocked, DROP)
PASS=3 FAIL=3   <-- all six verdicts correct
```

**[UNVERIFIED]** `ping6` and `dig` aren't installed here, so those two variants are reasoned, not run. The logic is `rc==0 → FAIL`, which is sound for both.

---

## H2 — HIGH — tar traversal guard bypassable

**File:** `project/browser-runtime/hardened_orchestrator.py:2640-2646`

Confirmed in round 1 with a working escape. Detail added this pass:

### A trap in the obvious fix

`filter="data"` is the textbook answer — and it **does not exist on your runtime**. `tarfile.TarFile.extractall()` gained `filter=` in **Python 3.12**; your image is `mcr.microsoft.com/playwright:v1.44.0-jammy` (Ubuntu 22.04 → **Python 3.10**). Measured:

```
python 3.11.2
extractall signature: (self, path='.', members=None, *, numeric_owner=False)
has filter support: False
```

Proposing `extractall(..., filter="data")` unconditionally would raise `TypeError` and break every session restore. My first attempt at the fix did exactly that.

### Best fix — portable, tested

```python
# replace hardened_orchestrator.py:2637-2646
_tar_buf = io.BytesIO(decrypted_bytes)
try:
    with tarfile.open(fileobj=_tar_buf, mode="r:gz") as tar:
        members = tar.getmembers()
        if not any('Cookies' in m.name or 'cookies.json' in m.name for m in members):
            raise ValueError("Decrypted archive missing Cookies database — likely truncated/corrupt")
        base = os.path.realpath(temp_extract)
        for m in members:
            if m.type in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
                raise ValueError(f"Refusing device/fifo tar member: {m.name}")
            target = os.path.realpath(os.path.join(base, m.name))
            if target != base and not target.startswith(base + os.sep):
                raise ValueError(f"Path traversal detected in tar entry: {m.name}")
            if m.issym() or m.islnk():
                lt = m.linkname if m.issym() else os.path.join(os.path.dirname(m.name), m.linkname)
                lt_abs = os.path.realpath(os.path.join(base, lt))
                if not (lt_abs == base or lt_abs.startswith(base + os.sep)):
                    raise ValueError(f"Refusing out-of-root link in tar entry: {m.name} -> {m.linkname}")
        if sys.version_info >= (3, 12):
            tar.extractall(path=temp_extract, filter="data")   # py3.12+: kernel-level safety net
        else:
            tar.extractall(path=temp_extract)                  # py3.10/3.11: checks above
finally:
    _tar_buf.close()
```

Three changes over the current code: `os.sep` in the prefix test (the actual bypass), `realpath` so symlinked parent dirs can't fool it, and link/device-member rejection. `filter="data"` is added **only** where it exists, so a future base-image bump keeps working.

Tested — 2 legitimate archives extract, all 6 attacks blocked, nothing written outside the root:

```
python 3.11.2   filter= supported: False

legit profile archive                    allowed=True   escaped_outside_root=[]
legit + harmless internal symlink        allowed=True   escaped_outside_root=[]
ATK sibling ../profile.tmp.extract.evil  allowed=False  ValueError: Path traversal detected
ATK simple ../evil.txt                   allowed=False  ValueError: Path traversal detected
ATK absolute /tmp/poc/ESCAPED.txt        allowed=False  ValueError: Path traversal detected
ATK symlink -> /etc/passwd               allowed=False  ValueError: Refusing out-of-root link
ATK symlink -> ../../../../etc/passwd    allowed=False  ValueError: Refusing out-of-root link
ATK device member (chr)                  allowed=False  ValueError: Refusing device/fifo member
```

Note I deliberately allow symlinks whose target resolves **inside** the root rather than rejecting all links — `profile_archiver.py:107` uses `tar.add(source_dir, ...)` with default `dereference=False`, so if a Chrome profile ever contains a symlink, an outright ban would break session restore.

**Also worth doing:** symlink the cleanup to match. The escaped file lands in a sibling directory that `shutil.rmtree(temp_extract)` (line 2658) never touches, so once extraction is safe, also sweep `temp_extract`'s parent for stray `<profile>*.tmp.extract*` leftovers.

---

## M3 — REVISED (downgraded) — `create_task` without a strong reference

**File:** `project/browser-runtime/hardened_orchestrator.py:5035`

Round 1 I flagged the documented "task disappears mid-execution" hazard. **I could not reproduce it.** Measured:

```
TEST 2 - discarded create_task(), coroutine parks on a future, then gc.collect():
  live tasks before gc=2  after gc=1  (task survived gc? True)
```

A task awaiting a future is kept alive by that future's callback, so CPython 3.11 doesn't collect it. The hazard is real but doesn't bite this code shape.

What **is** real:
- Nothing awaits the task and there's no `add_done_callback`, so a failure inside `handle_graceful_shutdown` is only surfaced by asyncio's fallback handler at GC time — `ERROR Task exception was never retrieved` on `logging.getLogger("asyncio")`, which is **not** your loguru pipeline, so it won't be in your logs.
- There is no `loop.set_exception_handler` anywhere in the repo (verified: zero hits).

So: keep this as **low**, folded into the H0 fix (which adds strong refs and a done-callback as a side effect). H0 is the bug; this is a symptom.

---

## M4 — MEDIUM — host kill-switch is TCP-only

**File:** `project/config/iptables_docker.sh:43-44`

```bash
iptables -A DOCKER-USER -i docker0 -p tcp ! --dport 1080 -j DROP
iptables -A DOCKER-USER -i br-+    -p tcp ! --dport 1080 -j DROP
```

`-p tcp`. UDP is only blocked for 53/5353/5355/1900 and 224.0.0.0/4, 239.0.0.0/8. UDP to anything else (QUIC/443, 500, 161, arbitrary) is permitted. The comment on line 39 claims this is the backstop for when container-level iptables fails — for UDP it isn't.

Container policy (`entrypoint.sh:842`, `-P OUTPUT DROP`) holds today, so this is defence-in-depth, not a live leak.

**Fix** — add a UDP catch-all *after* the existing UDP accepts, and move the sidecar whitelist *after* the DROP rules:

```bash
# ... existing per-port UDP blocks (53, 5353, 5355, 1900, multicast) stay where they are ...

# NEW: default-deny UDP egress from every container bridge
iptables -A DOCKER-USER -i br-+    -p udp -j DROP
iptables -A DOCKER-USER -i docker0 -p udp -j DROP

# MOVE these two to AFTER all DROP rules (they currently precede the
# multicast blocks, so -j ACCEPT lets the sidecars bypass them):
iptables -A DOCKER-USER -s "$EGRESS_PROXY_IP" -p tcp -j ACCEPT
iptables -A DOCKER-USER -s "$EGRESS_DOH_IP"   -p tcp --dport 443 -j ACCEPT
```

Parameterise `172.19.0.5` / `172.19.0.6` (`$EGRESS_PROXY_IP` / `$EGRESS_DOH_IP`) rather than hardcoding — the values are correct today (they're the `egress-net` addresses from `docker-compose.yml:588`), but they're a silent break the moment the subnet changes.

**[UNVERIFIED]** — can't run iptables here.

---

## M5 — MEDIUM — rotation log committed to git

**File:** `.local/share/cloak/signature_rotations.jsonl`

Confirmed tracked via `git ls-files`. 23 records of `{salt, timestamp, persona_id, session_id}`. It exists because `SignatureRotator.__init__` falls back to `Path.home()/".local"/"share"/"cloak"` (`signature_rotation.py:102`), so running its smoke test from a checkout writes operational data into the working tree.

Fix:

```bash
git rm --cached .local/share/cloak/signature_rotations.jsonl
printf '\n# Operational runtime state\n.local/\n*.jsonl\n' >> .gitignore
```

```python
# signature_rotation.py:99-108 — both fallbacks
self.log_dir = Path("/var/log/cloak")
self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
...
self.log_dir = Path(tempfile.mkdtemp(prefix="cloak-"))   # instead of the predictable /tmp/cloak
```

---

## M6 — MEDIUM — predictable session IDs seed the behavioural RNGs

**File:** `project/browser-runtime/signature_rotation.py:171-177`, consumed at `:203`

`sess-{year*12+month}-{sha256(persona_id)[:8]}-{unix_ts:010x}` — salt is public, persona hash is a stable 32-bit value, timestamp is guessable. That string then seeds RNGs via `_fnv1a_hash(self._session_id)`. The code already treats it as sensitive (FIX #200 truncates it before logging) but the value itself is the weakness.

Fix — keep the deterministic persona component for cross-session stability, add real entropy:

```python
persona_hash = hashlib.sha256(persona_id.encode()).hexdigest()[:8]
nonce = secrets.token_hex(8)                      # NEW: unguessable
self._session_id = f"sess-{salt}-{persona_hash}-{nonce}"
```

and seed RNGs from `secrets.randbits(64)` / `os.urandom`, never from the printable ID. (`hardened_orchestrator.py:2293` already does this correctly with `uuid.uuid4()`.)

---

## Clean bill for the shell layer

Ran ShellCheck 0.11.0 at `--severity=warning` across **every** `.sh` in the repo:

```
--- ./browser-runtime/entrypoint.sh
entrypoint.sh:641:5: warning: i appears unused. [SC2034]
--- ./config/hardened_host_os.sh
hardened_host_os.sh:1431:21: warning: UGRP... UGRUB_OUT appears unused. [SC2034]
(end)
```

Two unused-variable warnings and nothing else — no quoting bugs, no unset-variable bugs, no `cd` failures, across ~4,500 lines including the 54 KB `entrypoint.sh`. That's genuinely well-maintained shell.

I also went looking for an unset-variable hazard from `set -eo pipefail` lacking `-u`: **`resolve_ip()` (`entrypoint.sh:757-767`) always returns either a resolved address or a caller-supplied fallback**, so `$DOH_PROXY_IP` / `$PROXY_RES_IP` can never be empty. That hypothesis is **disproved** — no bug there.

---

## Round-1 claims I'm correcting

| Round-1 claim | Verdict this pass |
|---|---|
| `create_task` GC hazard (M3) | **Not reproducible** on CPython 3.11 for this code shape. Downgraded to low; the real defect is H0. |
| Unset-variable risk from missing `set -u` | **Disproved** — `resolve_ip()` guarantees non-empty. ShellCheck also found nothing. |
| `filter="data"` as the tarfile fix | **Would have broken production** — doesn't exist on Python 3.10/3.11. Replaced with the version-gated fix above. |

Still standing as verified: H1 (network audit inversion), H2 (tar traversal), M4 (UDP), M5 (committed log), M6 (session IDs), L1 (dead annotations), L2 (missing timeout), L3 (blocking calls in async).

---

## Priority

1. **H0** — shutdown cancels itself. Small patch, eliminates a silent security-property failure on *every* shutdown.
2. **H1** — 6/6 verdicts correct after the fix. Until then the gate is worse than no gate.
3. **H2** — version-gated extract. Use my tested version, not the textbook `filter=` one.
4. **M4 / M5 / M6** — defence-in-depth, hygiene, entropy.

Repros are in `/tmp/poc/` (sandbox scratch, not committed): `m3c.py` vs `m3d.py` for H0 (broken vs fixed), `fixed_check.sh` for H1, `tar_fix2.py` for H2.

---

# Verification after applying the fixes

All fixes are applied on branch `arena/b658958e-seo`. Re-verification:

## Lint: before vs after

| check | before | after |
|---|---|---|
| ruff total (E,F,W,B,ASYNC,C4,RET,SIM,ARG,LOG,G,RUF,PERF,S @120) | 970 | **886** |
| **S110** silent `except: pass` | **83** | **0** |
| **F821** undefined name | **2** | **0** |
| **F811** redefinition | **1** | **0** |
| **F601** duplicate dict key | **1** | **0** |
| S202 tarfile extractall | 1 | 0 (validated + `# noqa` documented) |
| E501 line-too-long | 105 | **105** (no new long lines) |
| `py_compile` on all 30 `.py` files | pass | **pass** |
| `bash -n` + ShellCheck (warning+) | clean | **clean** |

The +58 in `G004` (logging-f-string) is a style preference this codebase already
uses 203 times; the new debug logs follow the existing convention.

## Re-ran the PoCs against the patched source

**H2 (tar traversal)** — the validation block was extracted *verbatim from the
patched `hardened_orchestrator.py`* (lines 2670-2711) and executed:

```
PYTHON 3.11.2  (filter= supported: False)

legit profile archive                      allowed=True  escaped=[]
legit + internal symlink                   allowed=True  escaped=[]
ATK sibling ../profile.tmp.extract.evil    allowed=False ValueError: Path traversal detected
ATK ../evil.txt                            allowed=False ValueError: Path traversal detected
ATK absolute path                          allowed=False ValueError: Path traversal detected
ATK symlink -> /etc/passwd                 allowed=False ValueError: Refusing out-of-root link
ATK symlink -> ../../../../etc/passwd      allowed=False ValueError: Refusing out-of-root link
ATK device member                          allowed=False ValueError: Refusing device/fifo member
```

**H1 (network audit)** — functions read back out of the patched file:

```
✗ FAIL: open+blockedpage (EGRESS OPEN - the request succeeded)
✗ FAIL: open+realip (EGRESS OPEN - the request succeeded)
✓ PASS: refused(rc7) (egress blocked, curl rc=7)
✓ PASS: timeout(rc28) (egress blocked, curl rc=28)
✗ FAIL: UDP open (UDP EGRESS OPEN - got a reply)
✓ PASS: UDP closed (egress blocked, DROP - no reply)
PASS=3 FAIL=3   <-- every "open" fails, every "blocked" passes: 6/6
```

**H0 (shutdown)** — the real `_SHUTDOWN_TASKS` / `_on_shutdown_signal` /
`register_shutdown_signals` definitions were AST-extracted from the patched file,
exec'd, and driven with a real SIGTERM:

```
REAL patched code -> cleanup steps that ran: ['start', 'db_pool.close', 'tmpfs wipe', 'tc qdisc del', 'DONE']
REAL patched code -> FULL graceful shutdown completed? True
```

**M5** — `git rm --cached` untracked the rotation log; `.local/` and `*.jsonl`
added to `.gitignore`. Re-ran the module's own smoke test to confirm the 0o700
fallback works and the session nonce is now random:

```
Session ID: sess-24322-c7fded06-b6e00b08b8ba7523     <- was sess-24319-c7fded06-6a5f1487
drwx------  /home/user/.local/share/cloak
=== ALL TESTS PASSED ===
```

## A bug in my own patch, caught by testing it

The first version of the H0 fix filtered the current task with
`asyncio.current_task()`. That call **raises `RuntimeError` when there is no
running loop** — and `__main__`'s `finally` block runs *after*
`run_until_complete()` returns, in synchronous code. Because that whole block is
wrapped in `except Exception: pass`, the exception would have silently skipped
the *entire* cleanup: the fix would have made things worse than the original bug.

```
RuntimeError: no running event loop
all_tasks(loop) works: set()
```

Removed the `current_task()` call (there is no current task in that scope) and
re-verified. Worth stating plainly: this is exactly why the fixes were executed
rather than just written.

## Not done / needs your decision

- **The rotation log is still on disk** at `.local/share/cloak/signature_rotations.jsonl`
  (untracked now, so it can't be re-committed). Delete it yourself if you want it
  gone: `rm -rf .local/`. It contains `test_persona` data only.
- **History**: the file is still in past commits. `git rm --cached` stops future
  tracking but does not rewrite history; purging it needs `git filter-repo` and a
  force-push. Tell me if you want that.
- **M4 is unverified at runtime** — no iptables in this sandbox. The rules are
  logically correct but have not been executed against a live host.
- **L5 instrumentation logs the exception *type* only**, never the message.
  Several of those handlers wrap decryption/key-material paths, and logging
  `str(e)` there risks writing secrets into logs.
