# Deep Phase A Audit - Hidden Issues & Cross-File Conflicts

## Method
For each of the 12 Phase A fixes, look for:
1. **System-breaking scenarios** — conditions that crash or hang
2. **Cross-file conflicts** — interactions with orchestrator/entrypoint/other modules
3. **Edge cases** — what if FP_CONFIG is missing a field, persona is wrong type, etc.
4. **Async races** — what if scripts run concurrently
5. **Memory leaks** — closures holding refs indefinitely
6. **Leak vectors** — does the fix leak real Chrome state in some path

---
