# R7-DEEP-AUDIT-2 (Pass 14) — Final Sweep Findings

**Date:** 2026-07-16
**Scope:** Final targeted sweep after completing all 7 Pass 13 bugs + 3 Pass 14 fixes (H1, M4, M5)
**Method:** Targeted grep on the highest-leverage risk patterns (global `random.X` in persona-specific code), deferred files (hci_reading_model.py was header-only before), and singleton/state-caches

---

## Summary

| # | Severity | File | Status |
|---|----------|------|--------|
| **N1** | **HIGH** | `hci_reading_model.py` lines 124, 492, 499, 501-504, 506, 510, 514, 520, 527-528 | **Real bug, not yet fixed** |
| N2 | MEDIUM | `keyboard_emulator.py` lines 264, 730, 738 | Real bug (typo + polyphonic rollover) |
| N3 | MEDIUM | `keyboard_emulator.py` line 598 | Lower-priority (probabilistic event) |
| N4 | LOW | `sticky_proxy_resolver.py` line 1336 | Real but minor (one-time seed) |
| N5 | LOW | `curl_cffi_handover.py` line 84 | Real but minor (generic delay helper) |

**1 HIGH-priority bug found that was missed by all prior audits (Pass 1-14).**

---

## N1 (HIGH) — `hci_reading_model.py` uses global `random` for persona-specific actions

**File:** `browser-runtime/hci_reading_model.py` lines 124, 492, 499, 501-504, 506, 510, 514, 520, 527-528
**Severity:** HIGH
**Detected by:** Targeted grep on `random.X` patterns in `hci_reading_model.py` (which was deferred in Pass 14 file-wise audit)

### Evidence

`hci_reading_model.py` is the persona's reading + interaction model. The class has `self.rng` (per-persona seeded `np.random.Generator`, set at line 171):

```python
class HCIReadingModel:
    def __init__(self, persona_id: str, total_words: int = 1500):
        ...
        self.rng = np.random.default_rng(state_seed)
```

But many persona-specific actions use the **global `random` module** instead of `self.rng`:

| Line | Code | Persona-specific action |
|------|------|------------------------|
| 124 | `tremor_x = random.choice([-1, 0, 1])` | Scroll-mouse correlation tremor |
| 492 | `target_p = paragraphs.nth(random.randint(0, min(count - 1, 5)))` | Paragraph selection |
| 499 | `drag_start_x = box["x"] + random.randint(10, 30)` | Drag start X |
| 501 | `drag_end_x = drag_start_x + random.randint(50, 120)` | Drag end X |
| 502 | `drag_end_y = drag_start_y + random.uniform(-2, 2)` | Drag end Y |
| 506 | `await asyncio.sleep(random.uniform(0.1, 0.3))` | Sleep after move |
| 510 | `await asyncio.sleep(random.uniform(0.05, 0.15))` | Sleep after mouse down |
| 514 | `await asyncio.sleep(random.uniform(0.1, 0.2))` | Sleep after drag |
| 520 | `await asyncio.sleep(random.uniform(1.0, 2.5))` | Cognitive realization pause |
| 527 | `dismiss_x = drag_end_x + random.randint(20, 50)` | Dismiss click X |
| 528 | `dismiss_y = drag_end_y + random.randint(20, 50)` | Dismiss click Y |

### Real-world impact

1. **Non-per-persona-deterministic**: Two personas launched in the same process would get the **same** `tremor_x` series, the **same** drag start offsets, the **same** sleep durations. The behavioral model claims to be per-persona but is partially global.

2. **Cross-persona contamination**: The global `random` state is consumed by:
   - The orchestrator's `random.seed()` for persona_id (M1, already documented as a HACK)
   - `loguru` internal sampling
   - `asyncio` jitter
   - Other files that use `random.X`
   So the actual values depend on **what order the orchestrator consumes random state**, not just on persona_id.

3. **Detection vectors** (top-3 fingerprint signals):
   - **Tremor during scroll**: creepjs and FingerprintJS measure micro-tremor on cursor movement. Two personas with the same `tremor_x` series = same fingerprint.
   - **Polyphonic key rollover** (in keyboard_emulator.py — see N2): even worse, the rollover timing is a STRONG human-vs-bot signal.
   - **Drag jitter offsets**: drag-to-highlight behavior in articles (e.g., Medium). The drag start offset of `+10-30` random pixels is the same for all personas, producing a "perfectly straight line" of drag positions.

### Fix

Replace all `random.X` calls with `self.rng.X` (or `self.rng.integers` for `randint`). The class has `self.rng` initialized at line 171. Changes:

| Was | Becomes |
|-----|---------|
| `random.choice([-1, 0, 1])` | `self.rng.choice([-1, 0, 1])` (np.random.Generator supports `choice`) |
| `random.randint(a, b)` | `self.rng.integers(a, b + 1)` (np.random uses `integers` and is exclusive) |
| `random.uniform(a, b)` | `self.rng.uniform(a, b)` (same signature) |

Note: `np.random.Generator.integers(low, high)` is **exclusive** on `high`, while `random.randint(a, b)` is **inclusive**. So `random.randint(0, 5)` → `self.rng.integers(0, 6)`.

Lines changed: ~10. Test: 1 new test verifying the values are now per-persona-deterministic.

### Why this wasn't caught in earlier audits

The Pass 14 file-wise audit (11_filewise_priority_list.md) explicitly deferred `hci_reading_model.py` to "full deep read deferred". The earlier Pass 9/10 behavioral audits looked at `humanized_movement.py` and `behavioral_entropy.py` but didn't reach `hci_reading_model.py`. The file has **no comment markers** (no `R7-DEEP-AUDIT-2` audit notes in the source), so grep-based audits missed it.

---

## N2 (MEDIUM) — `keyboard_emulator.py` uses global `random` for typo + polyphonic rollover

**File:** `browser-runtime/keyboard_emulator.py` lines 264, 730, 738
**Severity:** MEDIUM
**Detected by:** Same targeted sweep

### Evidence

`_get_adjacent_key` (line 264):
```python
def _get_adjacent_key(char: str) -> str:
    if char in _QWERTY_ADJACENT:
        return random.choice(_QWERTY_ADJACENT[char])  # <-- GLOBAL random
    return char
```

`_human_type_words_impl` (line 730):
```python
if is_digraph and (random.random() < rollover_chance):  # <-- GLOBAL random
    ...
    overlap_offset = press_duration_1 * random.uniform(0.30, 0.70)  # <-- GLOBAL random
```

### Real-world impact

1. **Typo pattern is the same for all personas**: `_get_adjacent_key` is called when the persona makes a typo. Every persona will have the same sequence of "physically adjacent" typos. This is a **strong detection signal** (creepjs measures typo patterns).

2. **Polyphonic key rollover timing is a top-3 keyboard fingerprint signal**: experts overlap keys much more than novices. The `rollover_chance` is persona-specific (0.65 for expert, 0.25 for novice), but the random check uses global `random` — so all experts in the same process have the same rollover sequence, and the **overlap_offset** (the percent of key 1's hold during which key 2 is pressed) is also global.

3. **The `profile.rng.normal` and `profile.calculate_log_normal_delays` calls nearby are correct** (lines 729, 732, 733) — the bug is only in `random.random()` and `random.uniform()`.

### Fix

Use `_get_persona_rng(persona_id)` (already defined at line 87 of keyboard_emulator.py) instead of global `random`. The persona_id is in scope at all call sites (it's a function parameter or `page.context.persona_id`).

Lines: ~3 changed. Test: 1 new test verifying typo/rollover values are per-persona-deterministic.

---

## N3 (MEDIUM-LOW) — `keyboard_emulator.py` autocomplete trigger

**File:** `browser-runtime/keyboard_emulator.py` line 598
**Severity:** MEDIUM-LOW (probabilistic event, lower impact)
**Detected by:** Same sweep

### Evidence

```python
is_credential_field = _is_credential_field(selector)
if is_credential_field and len(text) > 5 and random.random() < 0.15:  # <-- GLOBAL random
    # Type first 3 characters naturally
    ...
```

### Real-world impact

Lower than N2 because:
1. This is a probabilistic gate (15% of credential fields) — the global random only affects whether the gate fires, not the actual content.
2. The downstream action uses `profile.rng.normal` correctly.

But still: a returning persona with `persona_id=X` will trigger autocomplete in the **same** credential field as a different persona with `persona_id=Y` (if the global random state is the same). Inconsistent.

### Fix

Replace with `profile.rng.random()` (since the loop already has `profile` in scope) or use `_get_persona_rng(persona_id)`.

Lines: 1 changed. Test: optional.

---

## N4 (LOW) — `sticky_proxy_resolver.py` uses global `random.randint` for deterministic seed

**File:** `browser-runtime/sticky_proxy_resolver.py` line 1336
**Severity:** LOW
**Detected by:** Same sweep

### Evidence

```python
deterministic_seed = random.randint(10000, 99999)
```

This is a one-time seed for persona binding. The function has access to `persona_id` (it's the surrounding function's parameter).

### Real-world impact

Low. The seed is used once per persona binding, and `random.Random(deterministic_seed)` creates a per-persona RNG. The global `random.randint` only determines the SEED (which is then used to create a per-persona RNG). Different persona_ids with the same global state would get the same seed if they happen to be processed in the same order.

The fix is trivial: use `int(hashlib.md5(persona_id.encode()).hexdigest()[:8], 16) % 90000 + 10000` to derive the seed from persona_id directly (no global state dependency).

### Fix

Lines: 1-2 changed. Test: optional.

---

## N5 (LOW) — `curl_cffi_handover.py` uses global `random.gauss` in helper

**File:** `browser-runtime/curl_cffi_handover.py` line 84
**Severity:** LOW
**Detected by:** Same sweep

### Evidence

```python
def _human_like_delay(min_s: float, max_s: float) -> float:
    ...
    raw_gauss = random.gauss(mu, sigma)
```

This is a generic log-normal delay helper, not persona-specific. Used by the curl_cffi handover engine for TLS fingerprint timing.

### Real-world impact

Low. The delay is for TLS fingerprint timing, not browser behavior. Different from the behavioral delays (which ARE persona-specific).

### Fix

Defer. Not in the high-leverage path. Could be a future improvement to make the helper accept an optional RNG parameter.

---

## RECOMMENDED ORDER

1. **N1 (HIGH)** — `hci_reading_model.py` global random in scroll + drag. ~10 line fixes. 1 new test.
2. **N2 (MEDIUM)** — `keyboard_emulator.py` global random in typo + polyphonic rollover. ~3 line fixes. 1 new test.
3. **N3 (MEDIUM-LOW)** — `keyboard_emulator.py` autocomplete trigger. 1 line fix. Optional test.
4. **N4 (LOW)** — `sticky_proxy_resolver.py` deterministic seed. 1-2 line fix. Optional.
5. **N5 (LOW)** — `curl_cffi_handover.py` generic delay. Defer.

**N1 + N2 = ~13 lines, 2 new tests, fixes 2 real fingerprint leaks.**

---

## Why This Wasn't Caught Earlier

**N1** was missed because:
- Pass 9/10/11 behavioral audits focused on `humanized_movement.py` and `behavioral_entropy.py` (the "main" behavioral files).
- `hci_reading_model.py` was deferred in Pass 14 as "header review only".
- The file has no R7-DEEP-AUDIT-2 audit-fix markers in the source code, so grep-based audits (`grep -rn "R7-DEEP-AUDIT-2"`) skip it.

**N2** was missed because:
- `keyboard_emulator.py` was deferred in Pass 14 as "header review only" (M5 was confirmed as a real gap, but only for page validation, not RNG hygiene).
- The `_get_persona_rng` helper exists but is not consistently used.

Both bugs share the same root cause: **prior audits prioritized reading the file header and structure over targeted deep reads of every file's call sites**. The Pass 14 file-wise priority list explicitly noted that `hci_reading_model.py` and `keyboard_emulator.py` were "header-only reviewed" and that a "full deep read" was deferred. This finding is the deferred deep read's first concrete output.

---

## IMPLICATIONS

After fixing N1, N2, N3, N4: the **behavioral layer is fully per-persona-deterministic**. Today, there are still 4 places where global `random` leaks persona-specific data into the global state.

The N1 fix is the highest-impact: it affects scroll, drag, highlight, and dismiss-click — all of which are medium-to-high-frequency browser behaviors that fingerprint vendors collect.

---

## Inline Markers

After the fix, I recommend adding `R7-DEEP-AUDIT-2 (Pass 14)` markers to:
- `hci_reading_model.py` next to each `self.rng.X` replacement
- `keyboard_emulator.py` next to each `_get_persona_rng(persona_id).X` replacement

This documents the audit trail and prevents future regressions (e.g., a maintainer "simplifying" by reverting to `random.X`).
