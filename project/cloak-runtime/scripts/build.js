#!/usr/bin/env node
// scripts/build.js - Pre-generate runtime JS for all built-in manifests
//
// USAGE: node scripts/build.js
// Output: dist/<persona-name>.js for each manifest in manifests/

'use strict';

const fs = require('fs');
const path = require('path');
const { buildFromFile } = require('../src/manifest_patcher');

const ROOT = path.join(__dirname, '..');
const MANIFESTS_DIR = path.join(ROOT, 'manifests');
const DIST_DIR = path.join(ROOT, 'dist');

function main() {
    if (!fs.existsSync(MANIFESTS_DIR)) {
        console.error(`No manifests directory at ${MANIFESTS_DIR}`);
        process.exit(1);
    }

    fs.mkdirSync(DIST_DIR, { recursive: true });

    const manifests = fs.readdirSync(MANIFESTS_DIR)
        .filter(f => f.endsWith('.json'));

    console.log(`Building ${manifests.length} manifest(s)...`);

    for (const file of manifests) {
        const manifestPath = path.join(MANIFESTS_DIR, file);
        const name = file.replace('.json', '');
        const outPath = path.join(DIST_DIR, `${name}.js`);

        try {
            const runtime = buildFromFile(manifestPath);
            fs.writeFileSync(outPath, runtime);
            const lines = runtime.split('\n').length;
            const bytes = Buffer.byteLength(runtime);
            console.log(`  [OK] ${name}.js (${lines} lines, ${bytes} bytes)`);
        } catch (e) {
            console.error(`  [FAIL] ${name}: ${e.message}`);
        }
    }

    console.log(`\nDone. Output in ${DIST_DIR}`);
}

main();
