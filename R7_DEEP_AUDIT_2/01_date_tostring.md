# R7-DEEP-AUDIT-2: Date.prototype.toString timezone inconsistency

## Issue

`Date.prototype.toString` override at line 3075-3082 in `prototype_hardener.js`:

```javascript
Date.prototype.toString = function() {
    try {
        const tzString = this.toLocaleString('en-US', {
            timeZone: tz,  // ← persona's TZ
            timeZoneName: 'short'
        });
        return `${this.toLocaleDateString('en-US', {weekday: 'short', year: 'numeric', month: 'short', day: '2-digit'})} ${this.toLocaleTimeString('en-US', {timeZone: tz, hour12: false})} ${tzString.split(' ').pop()}`;
        //   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ no timeZone option — uses SYSTEM timezone!
    } catch (e) {
        return rawToStringDate.call(this);
    }
};
```

## Runtime scenario

**Persona TZ**: America/Los_Angeles (UTC-8)  
**System TZ**: UTC (set by entrypoint's `export TZ`)

Wait — the entrypoint sets `TZ="$PERSONA_TZ"`, so the system TZ should be the persona's TZ. Let me verify.

## Entrypoint TZ chain

entrypoint.sh:
- Line 200-205: Sets `/etc/localtime` to persona's TZ
- Line 217: `export TZ="$PERSONA_TZ"`

So the system TZ env var is the persona's TZ. Native `Date.prototype.toString()` would already return the persona's TZ.

But the orchestrator at line 1394 also sets `browser_env["TZ"] = timezone`. This is the browser's env, which V8 reads.

So `toLocaleString` (no override) and `toLocaleDateString` (no override) should both use the persona's TZ. **There is NO bug** in this case.

## But what if `tz` is overridden?

Wait — let me check what happens if the persona's TZ string is "wrong" or "unknown". E.g., if `persona_digest["timezone"]` is set to something invalid.

`validate_timezone` in the orchestrator at line 1011:
```python
def validate_timezone(tz: str) -> str:
    if not tz or not isinstance(tz, str):
        return "America/New_York"
    if tz in _get_known_timezones():
        return tz
    logger.warning(f"Unknown timezone '{tz}'; falling back to America/New_York")
    return "America/New_York"
```

So the orchestrator ensures the persona's TZ is valid. The `tz` variable in JS is set to `personaDigest.timezone || "America/New_York"`.

## Conclusion

The `toLocaleDateString` without `timeZone` option will use the SYSTEM timezone, which is set to the persona's TZ. So the date and time parts of toString will be consistent (both in persona's TZ).

The override actually works correctly when entrypoint properly sets TZ. ✅

## But: still a potential issue

If the orchestrator's `browser_env["TZ"]` is NOT properly set or if the browser doesn't respect it (some Chrome versions override TZ), then:
- `toLocaleDateString` uses system TZ
- `toLocaleTimeString` uses the persona's TZ (forced via `timeZone: tz` option)
- The result is inconsistent (date from system, time from persona)

This is a latent risk. The fix would be to also pass `timeZone: tz` to `toLocaleDateString`:
