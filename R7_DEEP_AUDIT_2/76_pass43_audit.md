# Pass 43: Memory Profiling & Allocation Patterns

## Audit Focus
Use `tracemalloc` and stress tests to verify memory stays bounded during
sustained operation. Find unbounded growth, leaks, and inefficient allocation
patterns.

## Tests (10 total)

| ID | Test | Status |
|----|------|--------|
| MEM-1 | Behavioral entropy allocation profile | ✓ Pass |
| MEM-2 | Orchestrator init allocation | ✓ Pass |
| MEM-3 | Proxy resolver allocation | ✓ Pass |
| MEM-4 | Unbounded growth pattern detection | ✓ Pass |
| MEM-5 | __slots__ usage analysis | ✓ Pass |
| MEM-6 | String concatenation in loops | ⚠ False positives (numeric accumulators) |
| MEM-7 | Closure retention analysis | ✓ Pass |
| MEM-8 | Import-time memory cost | ✓ Pass |
| MEM-9 | Garbage collection health | ✓ Pass |
| MEM-10 | Async task retention | ✓ Pass |

## Issues Found & Fixed

### MEM-43-1: Off-by-one in profile cache eviction
- **File**: `browser-runtime/humanized_movement.py:135`
- **Bug**: `if len(_profile_cache) > 1000` allowed cache to grow to 1001
- **Fix**: Changed to `>= 1000` to enforce hard cap at 1000
- **Impact**: Without fix, `_profile_cache` could grow to 1001 entries instead
  of 1000, causing ~10-50KB extra memory per cache slot in worst case
- **Verified**: Stress test with 1500 personas shows cache bounded at 1000

## Findings (No Fix Needed)

1. **BehavioralEntropyGenerator** does not use `__slots__`. The class is
   per-persona singleton (cached), so memory cost is one-time. Adding
   `__slots__` would save ~5-10KB per cached profile but adds maintenance
   burden. Current cost is acceptable.

2. **Orchestrator init allocates 13.4 MB** - dominated by third-party
   imports (asyncpg: 405KB, numpy: 113KB, rich: 128KB, functools: 77KB).
   This is unavoidable.

3. **`+=` in loops** - All 11 detected are numerical accumulators
   (`cum += w`, `total_ms += fd`, `running += w`), not string concat.
   Python float `+=` is idiomatic and not a memory issue.

4. **Audit log eviction** (`ebpf_sentinel.py`) - Already bounded at 1000
   events via `_audit_log[:] = _audit_log[-1000:]`.

## Cache Bounded (Confirmed)

- `_profile_cache` in `humanized_movement.py`: 1000 cap (FIFO eviction)
- `_audit_log` in `ebpf_sentinel.py`: 1000 cap (sliding window)
- `db_pool` in `hardened_orchestrator.py`: max 4 connections (asyncpg pool)

## Memory Hot Spots (Top 10)

After 50 personas × 10 mouse trajectories = 228.6 KB total allocation.
Top sources (per profile):
- `behavioral_entropy.py:501` - 41.7 KB (numpy RNG init)
- `behavioral_entropy.py:490` - 41.6 KB (sub-RNG init)
- `behavioral_entropy.py:436` - 39.8 KB (sub-RNG init)
- `behavioral_entropy.py:419` - 39.8 KB (sub-RNG init)

These are per-persona one-time costs. The `_profile_cache` singleton pattern
prevents re-allocation on subsequent access.
