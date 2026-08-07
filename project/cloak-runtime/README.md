# cloak-runtime

Manifest-driven anti-fingerprinting browser runtime. Loads hardened personas with a one-line API call.

## Quick Start

```bash
npm install playwright
# Use cloak-runtime in your project
node -e "const {cloak} = require('cloak-runtime'); console.log(cloak.listPersonas());"
```

```javascript
const { cloak } = require('cloak-runtime');

// Launch a hardened browser with a persona applied
const browser = await cloak.launch({
    persona: 'chrome-128-win',           // built-in persona
    proxy: 'socks5://user:pass@host:port',
    headless: true,
    signatureRotation: true,            // monthly salt rotation
});

const page = await browser.newPage();
await page.goto('https://bot.sannysoft.com');

// Check what was applied
const info = await page.evaluate(() => ({
    salt: window.__cloak_signature_salt__,
    session: window.__cloak_session_id__,
    persona: window.__cloak_runtime__?.name,
    patchCount: window.__cloak_runtime__?.patchCount,
}));
console.log(info);

await browser.close();
```

## What It Does

Takes a persona manifest (JSON) describing 200-500 fingerprinting targets and their values, and produces a runtime JS that:

1. **Overrides** `navigator`, `screen`, `Intl`, `Date` properties with persona values
2. **Wraps** canvas/audio methods to return persona-consistent outputs
3. **Registers** every patched function in a `modifiedFunctions` Map so `Function.prototype.toString` returns native-looking strings
4. **Installs** the `anti_detect_signature.js` module for nondeterministic toString
5. **Rotates** patch order monthly via a deterministic PRNG so signature detection fails

## Personas

| Name | Description |
|---|---|
| `chrome-128-win` | Chrome 128 on Windows 10, 1920x1080, 8 cores, en-US (default) |

Create your own by copying `manifests/chrome-128-win.json` and editing.

## API

### `cloak.launch(options)`

| Option | Type | Default | Description |
|---|---|---|---|
| `persona` | string | `'chrome-128-win'` | Built-in name or path to manifest.json |
| `proxy` | string | null | `socks5://` or `http://` URL |
| `headless` | boolean | true | Headless mode |
| `signatureRotation` | boolean | true | Enable monthly salt rotation |
| `playwrightOptions` | object | {} | Pass-through to `playwright.chromium.launch()` |

Returns a Playwright `Browser` with `_cloak` metadata attached.

### `cloak.listPersonas()`

Returns array of built-in persona names.

### `cloak.loadManifest(path)`

Build a runtime script from a custom manifest file. Returns the JS string.

## Architecture

```
cloak-runtime/
├── package.json
├── src/
│   ├── index.js              # Main API (cloak.launch, etc.)
│   ├── manifest_patcher.js   # JSON manifest -> JS runtime
│   ├── timing.js             # HumanizedSession, ProxyRotator
│   ├── signature_rotator.js  # Monthly salt, session IDs
│   └── playwright_integration.js  # Patches Playwright to auto-install
├── manifests/
│   └── chrome-128-win.json   # Default persona (465 patches)
└── test/
    └── selftest.js           # npm test
```

The runtime is generated from a JSON manifest, not hand-written. This:
- Makes patches reviewable in a structured format
- Lets you swap personas without touching code
- Reduces 27,156 lines of hand-written JS to ~4,000 lines of generated runtime
- Allows audit/dedup/conflict detection on the manifest itself

## Manifest Format

```json
{
    "name": "my-persona",
    "version": "1.0.0",
    "description": "...",
    "persona": {
        "userAgent": "...",
        "platform": "...",
        "timezone": "..."
    },
    "patches": [
        { "target": "navigator.webdriver", "kind": "value", "value": false },
        { "target": "screen.width", "kind": "value", "value": 1920 },
        { "target": "Canvas.prototype.toDataURL", "kind": "function",
          "implementation": "return _orig_0.apply(this, arguments);" },
        { "target": "WebGLRenderingContext.prototype.getParameter",
          "kind": "native_register" }
    ]
}
```

### Patch Kinds

- `value` — Simple value override
- `function` — Replace method with custom implementation
- `getter` — Define a getter
- `native_register` — Register in modifiedFunctions (so toString returns native string)
- `farble` — Wrap function and apply random noise to output

## Testing

```bash
npm test
```

Runs 13 self-tests covering manifest loading, runtime generation, timing
distributions, signature rotation, and proxy rotation. Does NOT require a
browser.

## Limitations

- Does not patch C++ engine internals (WebGL, Audio, etc. need CloakBrowser
  binary for full stealth)
- 465 patches cover most common fingerprinting vectors, not all 3,400 in
  the original `prototype_hardener.js`. Run the dedup audit on that file
  to see what's missing.
- Designed to work WITH Playwright + a hardened Chromium binary, not as
  a standalone browser.

## License

Apache-2.0
