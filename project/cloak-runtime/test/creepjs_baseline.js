// test/creepjs_baseline.js
// Week 1-2 deliverable: Visit CreepJS, capture the trust score and
// "lies" panel. Run before/after any persona change to measure
// improvement.
//
// USAGE:
//   node test/creepjs_baseline.js                    # default persona
//   node test/creepjs_baseline.js chrome-128-win     # specific persona
//   node test/creepjs_baseline.js --no-cloak         # vanilla Chromium baseline
//   node test/creepjs_baseline.js --save baseline.json

'use strict';

const fs = require('fs');
const path = require('path');

async function main() {
    const args = process.argv.slice(2);
    let persona = args.find(a => !a.startsWith('--')) || 'chrome-128-win';
    const noCloak = args.includes('--no-cloak');
    const saveFile = (args.find(a => a.startsWith('--save=')) || '').replace('--save=', '') ||
                     (args.includes('--save') ? `baseline-${persona}-${Date.now()}.json` : null);

    let browser;
    if (noCloak) {
        // Vanilla Chromium - no persona
        const { chromium } = require('playwright');
        browser = await chromium.launch({ headless: true });
    } else {
        const { cloak } = require('../src/index');
        browser = await cloak.launch({ persona, headless: true });
    }

    console.log(`[baseline] persona=${persona}, cloak=${!noCloak}`);

    const context = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    });

    if (!noCloak && browser._installCloak) {
        await browser._installCloak(context);
    }

    const page = await context.newPage();

    let result = {
        persona,
        cloak_enabled: !noCloak,
        timestamp: new Date().toISOString(),
        errors: [],
    };

    try {
        console.log('[baseline] Navigating to CreepJS...');
        // FIX #22: CreepJS loads 50+ WASM modules and runs hundreds of
        // async checks. On residential proxies with 200ms+ latency,
        // 60s is not enough. Increased to 120s.
        await page.goto('https://abrahamjuliot.github.io/creepjs/', {
            waitUntil: 'networkidle',
            timeout: 120000,
        });
        // CreepJS takes 15-30s to complete all checks
        await page.waitForTimeout(30000);

        // Try to extract the trust score and lies
        // CreepJS displays results in a "headless" section with class names
        const fp = await page.evaluate(() => {
            const out = {
                userAgent: navigator.userAgent,
                webdriver: navigator.webdriver,
                platform: navigator.platform,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory,
                languages: navigator.languages,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                screenWidth: screen.width,
                screenHeight: screen.height,
                colorDepth: screen.colorDepth,
                cloak_salt: window.__cloak_signature_salt__,
                cloak_session: window.__cloak_session_id__,
            };
            // Try to find CreepJS lie count
            try {
                const liesEl = document.querySelector('.lies-count, [data-lies]');
                if (liesEl) out.creepjs_lies = liesEl.textContent;
                const trustEl = document.querySelector('.trust-score, [data-trust]');
                if (trustEl) out.creepjs_trust = trustEl.textContent;
            } catch (e) {}
            return out;
        });

        result.fingerprint = fp;
        console.log('[baseline] Fingerprint captured:');
        console.log(JSON.stringify(fp, null, 2));

        // FIX #36: Save a screenshot alongside the JSON. The JSON says
        // what was returned; the screenshot shows what the page looked
        // like. Both are needed for debugging.
        if (saveFile) {
            const savePath = path.isAbsolute(saveFile) ? saveFile :
                path.join(__dirname, '..', 'baselines', saveFile);
            const shotPath = savePath.replace(/\.json$/, '.png');
            await page.screenshot({ path: shotPath, fullPage: true });
            console.log(`[baseline] Screenshot saved to ${shotPath}`);
        }
    } catch (e) {
        result.errors.push(e.message);
        console.error('[baseline] Error:', e.message);
    } finally {
        await browser.close();
    }

    if (saveFile) {
        const savePath = path.isAbsolute(saveFile) ? saveFile :
            path.join(__dirname, '..', 'baselines', saveFile);
        fs.mkdirSync(path.dirname(savePath), { recursive: true });
        fs.writeFileSync(savePath, JSON.stringify(result, null, 2));
        console.log(`[baseline] Saved to ${savePath}`);
    } else {
        console.log('\n[baseline] Result:');
        console.log(JSON.stringify(result, null, 2));
    }
}

main().catch(e => {
    console.error('Fatal:', e);
    process.exit(1);
});
