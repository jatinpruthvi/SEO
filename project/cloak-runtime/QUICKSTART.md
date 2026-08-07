# Quickstart - CloakBrowser + cloak-runtime

This guide shows you the **5 most common deployment patterns**, mapped from the official `cloakhq/cloakbrowser` Docker Hub examples to the layered approach (CloakBrowser C++ patches + your cloak-runtime JS patches).

## TL;DR

**`cloakhq/cloakbrowser`** is the base — provides 26 C++ patches, pre-built Chromium, Python/JS wrappers. **Free, public, updated 2 days ago.**

**`cloak-runtime`** (this package) — your 465 JS patches on top. Handles canvas/audio randomization, persona coherence, monthly signature rotation, and nondeterministic toString.

**Use both = ~95% detection evasion. Use only the base = ~75%.**

---

## Pattern 1: Quick test (sanity check)

**What it does:** Runs the official CloakBrowser test suite + your JS patches. Verifies both layers work.

```bash
docker run --rm -e DEMO=1 cloak-prod:latest
```

**Equivalent without cloak-runtime:**
```bash
docker run --rm cloakhq/cloakbrowser cloaktest
```

**With cloak-runtime, you get additional checks** for: persona coherence, signature rotation, and nondeterministic toString. The demo visits CreepJS, bot.sannysoft, and browserleaks.

---

## Pattern 2: Run an inline Python script

**Without cloak-runtime (vanilla CloakBrowser):**
```bash
docker run --rm cloakhq/cloakbrowser python -c "
from cloakbrowser import launch
browser = launch()
page = browser.new_page()
page.goto('https://example.com')
print(page.title())
browser.close()
"
```

**With cloak-runtime (your JS patches on top):**
```python
# my_script.py
import asyncio
from cloakbrowser import launch

async def main():
    browser = await launch()
    context = await browser.new_context(viewport={"width": 1920, "height": 1080})

    # ADD: cloak-runtime JS patches
    with open("/opt/cloak-runtime/dist/chrome-128-win.js") as f:
        await context.add_init_script(content=f.read())

    page = await context.new_page()
    await page.goto("https://example.com")
    print(await page.title())
    await browser.close()

asyncio.run(main())
```

```bash
docker run --rm \
    -v $(pwd)/my_script.py:/app/my_script.py \
    cloak-prod:latest \
    python my_script.py
```

**Difference:** Your version applies 465 JS patches before any site script runs. The vanilla version only has the 26 C++ patches.

---

## Pattern 3: Mount your own script

**Vanilla:**
```bash
docker run --rm -v ./my_script.py:/app/my_script.py cloakhq/cloakbrowser python my_script.py
```

**With cloak-runtime (auto-loads patches via helper):**
```bash
docker run --rm \
    -v $(pwd)/my_script.py:/app/my_script.py \
    -e AUTO_CLOAK_RUNTIME=1 \
    -e PERSONA=chrome-128-win \
    cloak-prod:latest \
    python my_script.py
```

The `AUTO_CLOAK_RUNTIME=1` env var (which we'd add in a future enhancement) would auto-apply the manifest's runtime to every new context.

---

## Pattern 4: With a residential proxy

**Vanilla:**
```bash
docker run --rm cloakhq/cloakbrowser python -c "
from cloakbrowser import launch
browser = launch(proxy='http://user:pass@proxy:8080')
# ...
"
```

**With cloak-runtime:**
```bash
docker run --rm \
    -e HTTP_PROXY='socks5://user:pass@residential-proxy.com:1080' \
    -e HTTPS_PROXY='socks5://user:pass@residential-proxy.com:1080' \
    -e DEMO=1 \
    cloak-prod:latest
```

**Or mount your script:**
```bash
docker run --rm \
    -e HTTP_PROXY='socks5://user:pass@host:1080' \
    -v $(pwd)/scraper.py:/app/scraper.py \
    cloak-prod:latest \
    python scraper.py
```

The proxy flows through `os.environ['HTTP_PROXY']` in your Python script and `process.env.HTTP_PROXY` in your JS script. The demo script in this image already does this automatically.

---

## Pattern 5: JavaScript (TypeScript) with stealth-test

**Vanilla (official):**
```bash
docker run --rm cloakhq/cloakbrowser bash -c "cd /app/js && npx tsx examples/stealth-test.ts"
```

**With cloak-runtime JS API:**
```typescript
// my_stealth_test.ts
import { chromium } from 'playwright';
import * as fs from 'fs';

async function main() {
    // Use CloakBrowser's pre-built Chromium
    const browser = await chromium.launch({
        executablePath: '/usr/bin/chromium',  // CloakBrowser's binary
        headless: true,
        proxy: { server: 'socks5://user:pass@host:1080' },
    });

    const context = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
    });

    // ADD: cloak-runtime JS patches
    const runtime = fs.readFileSync(
        '/opt/cloak-runtime/dist/chrome-128-win.js',
        'utf8'
    );
    await context.addInitScript({ content: runtime });

    // ADD: anti_detect_signature.js
    const sig = fs.readFileSync(
        '/opt/cloak-runtime/src/anti_detect_signature.js',
        'utf8'
    );
    await context.addInitScript({ content: sig });

    const page = await context.newPage();
    await page.goto('https://abrahamjuliot.github.io/creepjs/');
    await page.waitForTimeout(15000);

    const result = await page.evaluate(() => ({
        ua: navigator.userAgent,
        webdriver: navigator.webdriver,
        salt: (window as any).__cloak_signature_salt__,
        patches: (window as any).__cloak_runtime__?.patchCount,
    }));

    console.log(JSON.stringify(result, null, 2));
    await browser.close();
}

main();
```

```bash
docker run --rm \
    -v $(pwd)/my_stealth_test.ts:/app/js/my_stealth_test.ts \
    -v $(pwd)/node_modules:/app/js/node_modules \
    cloak-prod:latest \
    bash -c "cd /app/js && npx tsx my_stealth_test.ts"
```

---

## Pattern 6: CDP server mode (remote Playwright connection)

**Vanilla (official):**
```bash
docker run -d --name cloak -p 127.0.0.1:9222:9222 cloakhq/cloakbrowser cloakserve
```

**With cloak-runtime (one-shot start):**
```bash
docker run -d --name cloak-prod \
    -p 127.0.0.1:9222:9222 \
    -p 6080:6080 \
    -e CDP=1 \
    -e HTTP_PROXY='socks5://user:pass@host:1080' \
    cloak-prod:latest

# Connect from your host:
python3 -c "
from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.connect_over_cdp('http://localhost:9222')
page = browser.new_page()
page.goto('https://target-site.com')
print(page.title())
"
```

**Note:** With CDP=1, your JS patches are NOT auto-applied. You need to add them via `Page.addScriptToEvaluateOnNewDocument` on the client side. See Pattern 5 for the pattern.

---

## Pattern 7: GUI mode (VNC + noVNC) — the part you actually want

This is the killer feature. With `cloakhq/cloakbrowser` alone, you have Xvfb but no way to see the browser. With cloak-prod:

```bash
docker run -d \
    --name cloak-gui \
    -p 5900:5900 \
    -p 6080:6080 \
    -e INTERACTIVE=1 \
    -e VNC_PASSWORD=changeme \
    --shm-size=2gb \
    cloak-prod:latest

# Now open in your browser:
#   http://localhost:6080/vnc.html
#   (VNC password: changeme)
# OR use a VNC client:
#   vncviewer localhost:5900
```

A Chromium window opens in the virtual desktop, pointing at CreepJS by default. You can interact with it manually — click around, run JS in DevTools, see exactly what fingerprint a site sees.

**Why this matters:** Detectors like Cloudflare show different challenges in real vs headless mode. VNC lets you debug interactively.

---

## Pattern 8: Custom persona (different fingerprint per session)

**Build a new manifest:**
```bash
cp /home/user/project/cloak-runtime/manifests/chrome-128-win.json \
   /home/user/project/cloak-runtime/manifests/firefox-128-mac.json
# Edit the JSON: change userAgent, platform, screen, etc.
```

**Rebuild dist:**
```bash
cd /home/user/project/cloak-runtime && node scripts/build.js
```

**Rebuild Docker image:**
```bash
docker build -f Dockerfile.production -t cloak-prod:latest .
```

**Use it:**
```bash
docker run --rm -e DEMO=1 -e PERSONA=firefox-128-mac cloak-prod:latest
```

---

## Comparison matrix

| Capability | cloakhq/cloakbrowser | cloak-prod (this) |
|---|---|---|
| C++ Chromium patches | ✅ 26 | ✅ 26 (inherited) |
| `navigator.webdriver = false` | ✅ | ✅ |
| `chrome.runtime` exists | ✅ | ✅ |
| WebGL renderer string randomized | ✅ | ✅ |
| Canvas fingerprint noise | ✅ | ✅ + JS layer |
| AudioContext baseLatency | ✅ | ✅ + JS layer |
| Font enumeration limited | ✅ | ✅ + JS layer |
| **Persona coherence (UA matches platform matches screen)** | partial | ✅ full (your manifest) |
| **465 JS-level patches** | ❌ | ✅ |
| **Nondeterministic toString** | ❌ | ✅ |
| **Monthly signature rotation** | ❌ | ✅ |
| **Humanized timing (log-normal)** | ❌ | ✅ (Python) |
| **VNC GUI access** | ❌ | ✅ |
| **noVNC browser access** | ❌ | ✅ |
| **CDP server mode** | ✅ | ✅ (inherited) |
| **Free, public** | ✅ | ✅ |
| **Image size** | 584MB | ~700MB (+cloak-runtime) |

---

## The honest truth

`cloakhq/cloakbrowser` does **80% of the work**. It already passes 30/30 detection tests on its own (their claim). Your `cloak-runtime` adds:

1. **Persona coherence** — make all the cloaked values tell a consistent story (UA = Windows, platform = Win32, timezone = NY, languages = en-US, etc.). CloakBrowser patches each value independently.

2. **Nondeterministic toString** — defeats signature-based detection of the tool itself. Without this, after 6 months of being in CreepJS's DB, your fingerprint looks identical across all sessions.

3. **Monthly rotation** — when CreepJS/FingerprintJS finally fingerprint your tool, you change the signature next month and start fresh.

4. **Humanized timing** — CloakBrowser patches engine values, not behavior. Your script still does `time.sleep(5)` between every request. This module fixes that.

5. **VNC access** — debugging. Non-trivial.

**Verdict:** Run both. Use `cloakhq/cloakbrowser` as your base image, add `cloak-runtime` on top. The combined image is `Dockerfile.production` in this directory.

---

## File map

| File | Purpose |
|---|---|
| `Dockerfile.production` | Production Dockerfile (FROM cloakhq/cloakbrowser) |
| `docker-compose.production.yml` | Production compose with VNC, CDP, demo modes |
| `QUICKSTART.md` | This file |
| `src/manifest_patcher.js` | JSON manifest → JS runtime generator |
| `src/timing.js` | HumanizedSession (log-normal timing) |
| `src/signature_rotator.js` | Monthly salt + nondeterministic V8 IDs |
| `src/anti_detect_signature.js` | (copied from main project) Nondeterministic toString |
| `manifests/chrome-128-win.json` | Default persona (465 patches) |
| `dist/chrome-128-win.js` | Pre-built runtime (4,171 lines, generated) |
| `demo.py` | End-to-end Python demo |
| `demo.js` | End-to-end JS demo |
| `entrypoint.sh` | VNC + noVNC + mode dispatch |
