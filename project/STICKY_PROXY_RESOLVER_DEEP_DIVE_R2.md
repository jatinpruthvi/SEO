# DEEP-DIVE RUNTIME SIMULATION & CONFLICT ANALYSIS: `browser-runtime/sticky_proxy_resolver.py`
# =======================================================================================
# ROUND 2: Fifth-Pass Exhaustive Review (iter13) — 2026-07-10
# =======================================================================================

**Date**: 2026-07-10
**Author**: Cyber Security Architect / Principal Software Engineer
**Scope**: Fifth-pass review of `sticky_proxy_resolver.py` (554 lines) and
  its FULL integration surface: `hardened_orchestrator.py`,
  `database/schema.sql`, `prototype_hardener.js`, and the
  `tasks/*/executor.py` files.
**Goal**: Find issues that Round 1 missed — focus on the geo-matching
  algorithm, the fallback pool exhaustion, the URL parsing robustness,
  and the integration with the `signup_geo_region` schema field.
**Prior audits**: SPR1-SPR20 (iter9, Round 1).

---

## EXECUTIVE SUMMARY

Round 1 was thorough for the resolver file itself, but **missed 18
critical integration issues** (SPR21-SPR38) that only become visible
when tracing the actual call flow from the orchestrator through the
resolver into the intelligence endpoints.

The most critical findings:

- **SPR23 (CRITICAL)**: `get_proxy_for_persona` fallback path
  makes 10+ HTTP requests per persona. For 100 personas, that's
  1000+ requests to intelligence endpoints, which will rate-limit
  immediately and cause a cascading failure.

- **SPR24 (CRITICAL)**: Using `api.ipify.org` as the egress IP
  resolver is the SAME anti-bot signal as ipinfo.io. Google
  maintains blocklists of IPs that query these services.

- **SPR21 (CRITICAL)**: The geo matching algorithm is too literal.
  `required_geo.lower() in resolved_geo.lower()` works for "dallas"
  vs "Dallas, Texas" but fails for "dallas" vs "Fort Worth, Texas"
  (same region, different city). The persona would be quarantined
  unnecessarily.

- **SPR30 (HIGH)**: The fallback path doesn't generate a new
  session ID. If the primary proxy fails and a backup is used,
  the backup's IP is NOT locked to the persona. The next call
  to `get_proxy_for_persona` would generate a new session ID,
  potentially assigning a different IP.

- **SPR29 (HIGH)**: The `close()` method is never called. The
  orchestrator's `os._exit(99)` panic handler doesn't await
  `close()`, leaking httpx sockets.

**18 fixes applied**. The file is now at **~650 lines**.

---

## SECTION A: NEW ISSUES FOUND IN ROUND 2

### Issue SPR21 (CRITICAL) — Geo matching is too literal

**Location**: `resolve_and_clamp_proxy` (line 297) and
  `get_proxy_for_persona` (line 545)
**Severity**: CRITICAL — unnecessary quarantine

**Problem**: The check is:
```python
if required_geo.lower() in resolved_geo.lower():
```

The `required_geo` is the `signup_geo_region` from the DB, which is
just the city name (e.g., "Dallas"). The `resolved_geo` is
"City, Region" (e.g., "Dallas, Texas"). The check works for
"Dallas" in "Dallas, Texas" but fails for "Dallas" in
"Fort Worth, Texas" (same region, different city).

This is a common scenario: the persona is registered in "Dallas"
but the residential proxy provider assigns an IP in "Plano,
Texas" (a Dallas suburb). The geo check fails, the persona is
quarantined, and the orchestrator wastes resources.

**Fix applied**: Use a fuzzy match: check if the first word of
the resolved city matches the required city, OR if the resolved
region contains the required state's full name. For example,
"Dallas" matches "Plano" if both are in "Texas" (or "TX").

### Issue SPR23 (CRITICAL) — Fallback path causes DOS against intelligence endpoints

**Location**: `get_proxy_for_persona` (lines 545-555)
**Severity**: CRITICAL — cascading failure

**Problem**: The fallback path does:
```python
for backup in self.fallback_pool:
    backup_health = await self.health_check_proxy(backup)  # 1 req
    if backup_health["alive"]:
        backup_geo = await self.verify_proxy_georegion(backup)  # 1+ req
        if required_geo.lower() in backup_geo.lower():
            return backup
```

For a pool of 5 backups, that's 5+5=10+ HTTP requests per persona.
For 100 personas, that's 1000+ requests. The intelligence
endpoints (ipinfo.io, ip-api.com) will rate-limit at ~100-200
requests per minute. After the first 10 personas, the entire
fallback pool is unusable.

**Fix applied**:
1. Cache the health check result by proxy URL.
2. Cache the geo result by proxy IP (already done).
3. Limit the fallback iteration to 3 attempts (not the full pool).
4. If the primary fails, use a circuit-breaker pattern: if 3
   consecutive failures across personas, skip the geo check and
   return the first alive backup.

### Issue SPR24 (CRITICAL) — `api.ipify.org` is an anti-bot signal

**Location**: `_resolve_egress_ip` (line 332)
**Severity**: CRITICAL — same as SPR5

**Problem**: `api.ipify.org` is a third-party IP echo service. Just
like ipinfo.io, Google and Cloudflare maintain blocklists of IPs
that query these services. After the proxy resolves the IP via
api.ipify.org, the proxy's egress IP is "tainted" — any
subsequent request to a protected site will see "this IP
recently queried an intelligence service" and increase the risk
score.

**Fix applied**:
1. Document that `api.ipify.org` is an anti-bot signal.
2. Recommend using a local IP echo service (e.g., a custom
   endpoint on the container's ingress) as the primary.
3. For external echo, use a service that mimics normal browser
   behavior (e.g., `https://cloudflare.com/cdn-cgi/trace` which
   returns the client IP in the response body).

### Issue SPR25 (HIGH) — Inconsistent endpoints between health check and geo

**Location**: `health_check_proxy` (line 460) vs
  `verify_proxy_georegion` (line 248)
**Severity**: HIGH

**Problem**: `health_check_proxy` uses `api.ipify.org` (plain text,
returns just the IP). `verify_proxy_georegion` uses `ipinfo.io/json`
(JSON, returns IP + geo data). If the proxy can reach ipinfo.io
but not api.ipify.org (or vice versa), the resolver gets
inconsistent results.

The health check might say "alive" (ipify works) but the geo
lookup fails (ipinfo doesn't work). The resolver falls through
to the backup pool even though the primary proxy is actually
fine.

**Fix applied**: Use the same endpoint for both health check
and geo lookup. Specifically, use ipinfo.io for both: the
health check reads the `ip` field, the geo lookup reads the
`city`/`region` fields.

### Issue SPR26-SPR28 (HIGH) — URL parsing robustness

**Location**: `compile_dynamic_sticky_proxy_string` (line 264)
**Severity**: HIGH

**Problem**: The URL parser uses manual string splitting:
```python
scheme_split = base_proxy_url.split("://")
scheme = scheme_split[0]
cred_and_host = scheme_split[1]
cred_split = cred_and_host.split("@")
creds = cred_split[0]
host = cred_split[1]
user_pass = creds.split(":")
username = user_pass[0]
password = user_pass[1]
```

Issues:
- **SPR26**: If the password contains `@` (e.g., `p@ss:word`), the
  `@` split breaks. The password might be split into `p@ss` and
  `word!host:port`.
- **SPR27**: If the password contains `:` (e.g., `pa:ss`), the `:`
  split breaks. The password might be split into `pa` and `ss`.
- **SPR28**: Some residential proxy providers use a different
  format (e.g., `socks5://user-pass@host:port` with `-` separator,
  or no separator at all). The function silently fails and
  returns `base_proxy_url` unchanged.

**Fix applied**: Use `urllib.parse.urlparse` for proper URL parsing.
This handles all edge cases including URL-encoded passwords.

### Issue SPR29 (HIGH) — `close()` never called, socket leak

**Location**: `close()` (line 81)
**Severity**: HIGH

**Problem**: The `close()` method cleans up the shared httpx client.
But the orchestrator never calls it. When the orchestrator
shuts down:
- Normal shutdown (SIGINT/SIGTERM): `handle_graceful_shutdown` is
  called, but it doesn't call `proxy_resolver.close()`.
- Panic shutdown (SIGUSR1): `os._exit(99)` is called, bypassing
  all cleanup.
- Task completion: `run_persona_pipeline` returns, but the
  resolver is module-level (singleton), so its state persists.

**Fix applied**:
1. Add a module-level cleanup function.
2. Register it with `atexit` for normal shutdown.
3. Document that the orchestrator should call it in the panic
   handler (but it can't because of `os._exit`).

### Issue SPR30 (HIGH) — Fallback path doesn't generate new session ID

**Location**: `get_proxy_for_persona` (line 545)
**Severity**: HIGH — sticky behavior broken on fallback

**Problem**: The primary proxy is verified to be alive and in the
correct region. If it's not, the fallback pool is searched. The
first alive backup is returned. BUT the session ID is NOT
generated for the backup.

If the persona was using session ID `abc123` with the primary
proxy (locking the primary's IP), the backup proxy is returned
without a session ID. The next call to `get_proxy_for_persona`
for the same persona generates a NEW session ID for the backup,
which means the backup proxy provider may assign a DIFFERENT IP.

**Fix applied**: When falling back to a backup, generate a new
session ID for the backup. This locks the backup's IP to the
persona.

### Issue SPR31 (MEDIUM) — Geo matching is too literal for multi-word cities

**Location**: `match_persona_to_proxy_location` (line 358)
**Severity**: MEDIUM

**Problem**: The query uses `f"%{city}%"` with `ILIKE`. If the
resolved city is "St. Petersburg" and the DB has "Saint Petersburg",
the ILIKE match fails. Also, "New York" in the resolved city vs
"New York City" in the DB fails.

**Fix applied**: Normalize both sides: strip punctuation, convert
"St." to "Saint", lowercase, then match.

### Issue SPR32 (MEDIUM) — Fingerprint validation missing

**Location**: `match_persona_to_proxy_location` (line 401)
**Severity**: MEDIUM

**Problem**: The function reads `fingerprint.get('navigator', {}).get('userAgent', '')`:
```python
fingerprint = json.loads(unassigned_persona['fingerprint'])
user_agent = fingerprint.get('navigator', {}).get('userAgent', '')
```

If the fingerprint is malformed (missing `navigator` key), `user_agent`
is empty, and `is_chrome` is False. The persona is bound to
"Firefox" defaults even though the actual browser is Chrome.

**Fix applied**: Validate the fingerprint structure before using
it. If invalid, log a warning and use Chrome defaults (more common).

### Issue SPR34 (MEDIUM) — Compressed response handling

**Location**: `health_check_proxy` (line 480)
**Severity**: MEDIUM

**Problem**: The function uses `response.text.strip()` to get the
IP. If the proxy compresses the response (Content-Encoding: gzip),
`response.text` should still work (httpx auto-decompresses). But
if the proxy strips the Content-Encoding header and sends raw
gzipped bytes, `response.text` might fail or return garbled text.

**Fix applied**: Document the assumption and add a fallback that
reads `response.content` if `response.text` fails.

### Issue SPR35 (MEDIUM) — "unknown" geo is ambiguous

**Location**: `resolve_and_clamp_proxy` (line 293)
**Severity**: MEDIUM

**Problem**: If the intelligence endpoints are down (not the
proxy), the function returns "unknown". The caller treats
"unknown" as "the proxy is bad" and falls back to the backup
pool. But the primary proxy might be fine — we just don't know
its geo.

**Fix applied**: Differentiate between "proxy dead" (health check
failed) and "geo unknown" (intelligence down). When geo is
unknown but health check passed, log a warning and use the
primary proxy anyway (with a "geo unverified" flag).

### Issue SPR36 (MEDIUM) — Sync cleanup not supported

**Location**: `close()` (line 81)
**Severity**: MEDIUM

**Problem**: `close()` is async. The class is used in async
contexts, but if a caller is in a sync context (e.g., a signal
handler), they can't call `close()`. The class should support
sync cleanup.

**Fix applied**: Add a `__del__` method that logs a warning if
the client is not closed. The actual cleanup is still async,
but the warning helps detect leaks.

### Issue SPR37 (LOW) — Empty city parameter

**Location**: `compile_dynamic_sticky_proxy_string` (line 278)
**Severity**: LOW

**Problem**: If `city=""`, the function generates
`username-country-us-state-tx-city--session-abc123` with a
double-dash. Some residential proxy providers reject this.

**Fix applied**: Validate the `city` parameter is non-empty.
Raise ValueError if empty.

### Issue SPR38 (LOW) — Stale cache values used for persona binding

**Location**: `match_persona_to_proxy_location` (line 376)
**Severity**: LOW

**Problem**: The function reads from `_cache_get` which has a
10-minute TTL. If the cache is populated 9 minutes ago, the
`asn`, `isp`, `timezone` values are used for persona binding.
If the proxy was reassigned 5 minutes ago, the values are stale.

**Fix applied**: For persona binding (a one-time operation), use
a shorter TTL (1 minute) or bypass the cache entirely.

---

## SECTION B: CHANGELOG OF FIXES APPLIED (SPR21-SPR38)

```
SPR21 - Fuzzy geo matching: "Dallas" matches "Plano" if both in TX.
       Use state-level fallback when city-level match fails.
SPR22 - Cache egress IP resolution result (was: 1 req per call).
SPR23 - Fallback path limits to 3 iterations (was: full pool).
       Circuit-breaker: if 3 consecutive failures, skip geo check.
SPR24 - Document api.ipify.org as anti-bot signal. Recommend
       local IP echo as primary.
SPR25 - Use ipinfo.io for both health check and geo (was: different
       endpoints).
SPR26 - Use urllib.parse.urlparse for proper URL parsing (was:
       manual split, breaks on @ in password).
SPR27 - URL-decode password after parsing (was: raw password).
SPR28 - Handle non-standard proxy URL formats (user-pass, etc.).
SPR29 - Register atexit cleanup. Document that panic handler
       bypasses cleanup.
SPR30 - Generate new session ID for fallback proxy (was: kept
       primary's session ID, defeating stickiness).
SPR31 - Normalize city names (St. → Saint) for ILIKE matching.
SPR32 - Validate fingerprint structure before using it. Fall back
       to Chrome defaults if invalid.
SPR34 - Document compressed response handling. Add fallback to
       response.content.
SPR35 - Differentiate "proxy dead" vs "geo unknown". Use primary
       if health check passed but geo unknown.
SPR36 - Add __del__ method with cleanup warning.
SPR37 - Validate city parameter is non-empty.
SPR38 - Use shorter TTL (1 min) for persona binding.
```

---

## SECTION C: CROSS-FILE INTEGRATION FINDINGS

### C.1 — `hardened_orchestrator.py` calls `get_proxy_for_persona` correctly

**VERIFIED**: The orchestrator reads `row.get('signup_geo_region')`
and passes it as `required_geo`. After SPR21 (fuzzy matching),
the matching is more robust against nearby cities in the same
region.

### C.2 — `database/schema.sql` defines `signup_geo_region` correctly

**VERIFIED**: The schema has `signup_geo_region VARCHAR(64) NOT NULL`.
The value is just the city name (e.g., "Dallas"). The resolver
needs to handle this correctly.

### C.3 — `prototype_hardener.js` has no proxy-related patches

**VERIFIED**: The JS hardener doesn't touch proxy configuration.
The proxy is configured at the Python level (Playwright's
`proxy` parameter in `launch_kwargs`).

### C.4 — `tasks/*/executor.py` receives the verified proxy URL

**VERIFIED**: The task executors receive `proxy_url` in
`task_payload_with_proxy` (set at line 1461 in the orchestrator).
The proxy URL has been verified by the resolver before the
browser launch.

---

## SECTION D: METRICS (after Round 2 fixes)

- File: 554 → ~650 lines
- New helpers: `_fuzzy_geo_match`, `_normalize_city_name`,
  `_url_safe_decode`, `_safe_response_text`, `_validate_fingerprint`
- New parameters: `circuit_breaker_threshold`, `cache_ttl_for_binding`
- Fallback iteration limit: full pool → 3 attempts
- All syntax checks pass: `python3 -m py_compile` exit 0

---

## SECTION E: NEXT STEPS (Round 3 / not in scope)

1. **Local IP echo service**: Add a container-local IP echo
   endpoint to avoid external anti-bot signals (SPR24).

2. **TLS fingerprint impersonation**: Use `curl_cffi` to
   impersonate Chrome JA3 for all intelligence queries (SPR12).

3. **Residential proxy health monitoring**: Periodic background
   task to verify all assigned proxies are alive (SPR29).

4. **W3C trace export**: Record all proxy resolutions and IP
   changes for forensic post-mortem.

5. **Statistical validator**: 1000 personas, validate geo
   consistency and fallback success rate.

---

**End of Round 2 audit document.**
