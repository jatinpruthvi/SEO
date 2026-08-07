# R7-DEEP-AUDIT-2: chrome.loadTimes mock returns different values per call

## Issue

`loadTimesMock` at line 777-810 in `prototype_hardener.js`:

```javascript
const loadTimesMock = function() {
    const t0 = performance.now() + seededRandom() * 50;  // ← 1st call to seededRandom
    const reqTime = t0 / 1000;
    const startL = reqTime + 0.120 + seededRandom() * 0.080;  // ← 2nd
    const commitL = startL + 0.350 + seededRandom() * 0.150;  // ← 3rd
    const docL = commitL + 0.280 + seededRandom() * 0.120;  // ← 4th
    const finL = docL + 0.220 + seededRandom() * 0.100;  // ← 5th
    return {
        requestTime: reqTime,
        startLoadTime: startL,
        commitLoadTime: commitL,
        finishDocumentLoadTime: docL,
        finishLoadTime: finL,
        firstPaintTime: commitL + 0.080 + seededRandom() * 0.040,  // ← 6th
        firstPaintAfterLoadTime: finL + 0.080 + seededRandom() * 0.250,  // ← 7th
        ...
    };
};
```

## Runtime scenario

A page calls `chrome.loadTimes()` to measure page performance. Real Chrome returns consistent values. Our mock returns different values per call:

```js
// Real Chrome:
const t1 = chrome.loadTimes();
const t2 = chrome.loadTimes();
t1.requestTime === t2.requestTime;  // true

// Our mock:
const t1 = chrome.loadTimes();
const t2 = chrome.loadTimes();
t1.requestTime === t2.requestTime;  // FALSE (different per call)
```

## Why this is detectable

Anti-bot grids can detect this with a simple test:
```js
const before = window.chrome.loadTimes();
await new Promise(r => setTimeout(r, 100));
const after = window.chrome.loadTimes();
const changed = before.requestTime !== after.requestTime;
// Real Chrome: changed = false (loadTimes values are stable)
// Our mock:   changed = true  (PRNG advances per call)
```

## Fix

Cache the values on first call, similar to the `csi` mock:
