// src/manifest_patcher.js
// Manifest-driven anti-fingerprinting patch generator.
//
// Reads a JSON manifest of targets and behaviors, produces the
// actual JS that runs in the browser context. Decouples WHAT to
// patch (data) from HOW to patch (code).

'use strict';

const fs = require('fs');
const path = require('path');

/**
 * Resolve a prototype path like "HTMLCanvasElement.prototype.toDataURL"
 * into [objectExpr, propertyName] for use with Object.defineProperty.
 *
 * Examples:
 *   "HTMLCanvasElement.prototype.toDataURL" -> ["HTMLCanvasElement.prototype", "toDataURL"]
 *   "Navigator.prototype.platform"           -> ["Navigator.prototype", "platform"]
 *   "window.navigator.webdriver"             -> ["window.navigator", "webdriver"]
 */
function resolvePath(targetPath) {
    // FIX #542: validate target is a non-empty string
    if (typeof targetPath !== 'string' || targetPath.length === 0) {
        throw new Error('resolvePath: targetPath must be a non-empty string');
    }
    // FIX #1909: reject whitespace in property names
    if (/\s/.test(targetPath)) {
        throw new Error('resolvePath: targetPath contains whitespace');
    }
    // FIX #1910: validate parts are non-empty
    if (targetPath.includes('..')) {
        throw new Error('resolvePath: targetPath contains ..');
    }
    // FIX #543: handle bracket notation like "window['navigator'].webdriver"
    if (targetPath.includes('[')) {
        // Convert "foo['bar'].baz" -> "foo.bar.baz" for emit
        const cleaned = targetPath.replace(/\['"']/g, '.').replace(/['"']\]/g, '');
        const parts = cleaned.split('.');
        const prop = parts.pop();
        return [parts.join('.'), prop];
    }
    const parts = targetPath.split('.');
    if (parts.length === 1) {
        // Bare identifier like "innerWidth" - this is a window/global property
        return ['window', parts[0]];
    }
    const prop = parts.pop();
    const obj = parts.join('.');
    // If obj is a single identifier and likely a constructor/class name,
    // it might need .prototype. Caller can decide.
    return [obj, prop];
}

/**
 * Generate JS code for a single manifest entry.
 * Returns a string of JS that, when executed, applies the patch.
 */
function generatePatchCode(entry, index) {
    const [objExprRaw, prop] = resolvePath(entry.target);
    // objExpr may be an array (from destructuring) or a string; coerce to string
    const objExpr = Array.isArray(objExprRaw) ? objExprRaw.join('.') : objExprRaw;
    const kind = entry.kind || 'value';

    let body;

    // Safe JSON interpolation for values that go into generated code.
    // This prevents RCE if a manifest comes from an untrusted source.
    // FIX #152: Use a replacer to handle cyclic objects.
    function _safe(v) {
        if (v === undefined) return 'undefined';
        // FIX #547: handle Symbol values
        if (typeof v === 'symbol') return JSON.stringify(v.toString());
        // FIX #546: BigInt throws on JSON.stringify; coerce to string with "n" suffix
        if (typeof v === 'bigint') return JSON.stringify(v.toString() + 'n');
        if (typeof v === 'function') return JSON.stringify(v.toString().slice(0, 200));
        // FIX #1911: handle ArrayBuffer
        if (typeof ArrayBuffer !== 'undefined' && v instanceof ArrayBuffer) {
            return JSON.stringify('[ArrayBuffer ' + v.byteLength + ' bytes]');
        }
        // FIX #1911: handle TypedArray
        if (typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView(v)) {
            return JSON.stringify('[' + v.constructor.name + ' ' + v.length + ' items]');
        }
        try {
            // Use a seen Set to handle cyclic references
            const seen = new WeakSet();
            return JSON.stringify(v, (key, val) => {
                // FIX #548: only check objects in WeakSet (functions/symbols fail)
                if (typeof val === 'object' && val !== null) {
                    if (seen.has(val)) return '[Circular]';
                    seen.add(val);
                }
                if (typeof val === 'bigint') return val.toString() + 'n';
                if (typeof val === 'symbol') return val.toString();
                if (typeof val === 'function') return val.toString().slice(0, 200);
                // FIX #1912: handle toJSON method (let JSON.stringify call it)
                if (val && typeof val === 'object' && typeof val.toJSON === 'function') {
                    return val;  // pass through, JSON.stringify will call toJSON
                }
                return val;
            });
        } catch (e) {
            try {
                return JSON.stringify(String(v));
            } catch (e2) {
                return JSON.stringify('[unstringifiable]');
            }
        }
    }

    // FIX #544: validate entry is object
    if (!entry || typeof entry !== 'object') {
        throw new Error(`generatePatchCode: entry must be an object, got ${typeof entry}`);
    }
    // FIX #1913: validate kind is in allowed set
    const _allowedKinds = ['value', 'function', 'getter', 'native_register', 'farble'];
    if (entry.kind !== undefined && !_allowedKinds.includes(entry.kind)) {
        throw new Error(`generatePatchCode: entry.kind '${entry.kind}' not in allowed set`);
    }
    // FIX #545: validate target is a string
    // FIX #1515: cap target length
    // FIX #1711: also require minimum length
    // FIX #1812: reject prototype pollution attempts
    if (typeof entry.target !== 'string' || entry.target.length < 1 || entry.target.length > 1000) {
        throw new Error(`generatePatchCode: entry.target must be a string 1-1000 chars, got length ${entry.target.length}`);
    }
    // FIX #1812: reject prototype pollution attempts (compute finalProp first)
    const _lastDot = entry.target.lastIndexOf('.');
    const finalProp = _lastDot >= 0 ? entry.target.slice(_lastDot + 1) : entry.target;
    // Reject __proto__, constructor, prototype as final property
    const dangerousProps = ['__proto__', 'constructor', 'prototype'];
    if (dangerousProps.includes(finalProp)) {
        throw new Error(`generatePatchCode: target's final property '${finalProp}' is dangerous`);
    }
    // FIX #1619: reject reserved JS keywords as the final property
    // (finalProp already computed above)
    const reserved = ['class', 'enum', 'import', 'export', 'super', 'this', 'arguments'];
    if (reserved.includes(finalProp)) {
        throw new Error(`generatePatchCode: target's final property '${finalProp}' is a reserved word`);
    }
    // FIX #1712: reject targets starting with number
    if (/^[0-9]/.test(finalProp)) {
        throw new Error(`generatePatchCode: target's final property '${finalProp}' starts with a number`);
    }
    // FIX #1353: reject target with dangerous chars
    if (/[-]/.test(entry.target)) {
        throw new Error(`generatePatchCode: entry.target contains control characters`);
    }
    // FIX #1354: reject __proto__ target
    if (entry.target === '__proto__' || entry.target.endsWith('.__proto__')) {
        throw new Error(`generatePatchCode: target '__proto__' is not allowed`);
    }
    // FIX #923: validate impl doesn't contain template literal characters
    // FIX #1508-#1511: reject dangerous patterns
    // FIX #1713-#1714: validate balanced quotes/parens
    function _validateImpl(s, kind) {
        if (typeof s !== 'string') return;
        if (s.includes('`') || s.includes('${')) {
            throw new Error(`generatePatchCode: ${kind} impl contains template literal characters`);
        }
        // Count balanced parens (rough check)
        let parens = 0, brackets = 0, braces = 0;
        for (const c of s) {
            if (c === '(') parens++;
            if (c === ')') parens--;
            if (c === '[') brackets++;
            if (c === ']') brackets--;
            if (c === '{') braces++;
            if (c === '}') braces--;
        }
        // Note: parens imbalance is allowed in function bodies (if/for/while)
        // We just warn, not throw
        // FIX #1713: count quotes (rough)
        const singleQuotes = (s.match(/'/g) || []).length;
        const doubleQuotes = (s.match(/"/g) || []).length;
        if (singleQuotes % 2 !== 0) {
            throw new Error(`generatePatchCode: ${kind} impl has unclosed single quote`);
        }
        if (doubleQuotes % 2 !== 0) {
            throw new Error(`generatePatchCode: ${kind} impl has unclosed double quote`);
        }
        // FIX #1508: reject eval
        if (/\beval\s*\(/.test(s)) {
            throw new Error(`generatePatchCode: ${kind} impl contains eval()`);
        }
        // FIX #1509: reject Function constructor
        if (/\bFunction\s*\(/.test(s) || /new\s+Function/.test(s)) {
            throw new Error(`generatePatchCode: ${kind} impl contains Function constructor`);
        }
        // FIX #1510: reject with statement
        if (/\bwith\s*\(/.test(s)) {
            throw new Error(`generatePatchCode: ${kind} impl contains with statement`);
        }
        // FIX #1511: reject debugger
        if (/\bdebugger\b/.test(s)) {
            throw new Error(`generatePatchCode: ${kind} impl contains debugger statement`);
        }
    }
    if (kind === 'value') {
        // Simple value override: target.prop = value
        // FIX #44: BigInt values throw on JSON.stringify. Coerce to string.
        // FIX #68: undefined values produce invalid JS. Guard explicitly.
        // FIX #51: Use safe interpolation to prevent template injection.
        // FIX #74: Preserve original descriptor attributes (writable, configurable, enumerable).
        // FIX #549: validate value type
        if (entry.value === undefined) {
            // Use default value
            entry.value = null;
        }
        // FIX #1601: handle array values
        // FIX #1602: handle Date values
        // FIX #1603: handle RegExp values
        // FIX #1604: handle Error values
        // FIX #1605: handle Map/Set values
        if (entry.value instanceof Date) {
            entry.value = entry.value.toISOString();
        } else if (entry.value instanceof RegExp) {
            entry.value = entry.value.toString();
        } else if (entry.value instanceof Error) {
            entry.value = entry.value.message;
        } else if (entry.value instanceof Map || entry.value instanceof Set) {
            entry.value = Array.from(entry.value.entries ? entry.value.entries() : entry.value);
        }
        let value;
        // FIX #1356: validate value isn't a Symbol
        if (typeof entry.value === 'symbol') {
            entry.value = entry.value.toString();
        }
        // FIX #1607: validate value length
        if (typeof entry.value === 'string' && entry.value.length > 100000) {
            throw new Error(`generatePatchCode: value too long (${entry.value.length} > 100000)`);
        }
        try {
            value = _safe(entry.value);
        } catch (e) {
            value = JSON.stringify(String(entry.value));
        }
        body = `
        try {
            // FIX #2077: defineProperty FIRST (works for read-only props)
            // then direct assignment (works for normal props)
            const _vd = Object.getOwnPropertyDescriptor(${objExpr}, ${_safe(prop)});
            if (_vd) {
                try {
                    Object.defineProperty(${objExpr}, ${_safe(prop)}, {
                        value: ${value},
                        writable: _vd.writable,
                        enumerable: _vd.enumerable,
                        configurable: _vd.configurable,
                    });
                } catch (e) {}
            } else {
                try {
                    ${objExpr}[${_safe(prop)}] = ${value};
                } catch (e) {}
            }
        } catch (e) {}
        `;
    } else if (kind === 'function') {
        // Function override: target.prop = function(...) { ... }
        // FIX #557/#558: validate impl is a string with balanced brackets
        // FIX #1914: note that empty args is valid (default to no args)
        let impl = entry.implementation;
        if (typeof impl !== 'string') {
            impl = 'return undefined;';
        }
        // FIX #1915: validate impl is not null
        if (impl === null) {
            throw new Error('generatePatchCode: function impl cannot be null');
        }
        // FIX #1209: validate impl length
        if (impl.length > 100000) {
            throw new Error(`generatePatchCode: impl too long (${impl.length} > 100000)`);
        }
        // FIX #1978: single check for empty/whitespace (FIX #1818)
        if (impl.trim().length === 0) {
            throw new Error('generatePatchCode: impl is too short');
        }
        _validateImpl(impl, 'function');
        // FIX #553: validate args is an array
        // FIX #1218: handle null args (treat as empty)
        const args = (entry.args === null || entry.args === undefined) ? [] :
                     (Array.isArray(entry.args) ? entry.args : []);
        // FIX #17: Use bracketed access for property names that aren't
        // valid JS identifiers. `function delete()` is a syntax error;
        // `function (delete)()` works.
        // FIX #51: Use safe interpolation for prop and args.
        // FIX #190: Validate args are strings (or string-coercible).
        const safeArgs = args.map(a => {
            if (typeof a === 'string') {
                if (/^[A-Za-z_$][\w$]*$/.test(a)) return a;
                return JSON.stringify(a);
            }
            // Coerce non-strings to strings
            return JSON.stringify(String(a));
        }).join(', ');
        const fnName = /^[A-Za-z_$][\w$]*$/.test(prop) ? prop : '';
        // FIX #2065: rewrite _orig_N references in impl to match actual post-dedup index
        let _indexedImpl = impl;
        const _origRefs = impl.match(/_orig_(\d+)/g) || [];
        for (const _ref of _origRefs) {
            const _refIdx = parseInt(_ref.replace('_orig_', ''), 10);
            if (_refIdx !== index) {
                // Replace this specific reference with the actual index
                _indexedImpl = _indexedImpl.replace(new RegExp(`_orig_${_refIdx}\\b`, 'g'), `_orig_${index}`);
            }
        }
        body = `
        try {
            const _orig_${index} = ${objExpr}[${_safe(prop)}];
            ${objExpr}[${_safe(prop)}] = function ${fnName}(${safeArgs}) {
                ${_indexedImpl}
            };
            if (typeof modifiedFunctions !== 'undefined') {
                try {
                    modifiedFunctions.set(${objExpr}[${_safe(prop)}],
                        "function ${fnName || 'fn'}() {\\n    [native code] \\n}");
                } catch (e) {}
            }
        } catch (e) {}
        `;
    } else if (kind === 'getter') {
        // FIX #560: validate getter is a string
        // FIX #1916: validate getter is not null
        if (entry.getter === null) {
            throw new Error('generatePatchCode: getter cannot be null');
        }
        let impl = entry.getter;
        if (typeof impl !== 'string') {
            impl = 'return undefined;';
        }
        _validateImpl(impl, 'getter');
        // FIX #81: Preserve original descriptor's configurable.
        // FIX #138: Preserve original `set` accessor if present.
        // FIX #168: Use a named function for the getter to avoid
        // anonymous function detection.
        // FIX #186: If a function patch also exists for this target,
        // skip the getter (the function overrides it).
        // Read the original descriptor; if it has a setter, keep it.
        // If no setter, the new property is read-only.
        const getterFnName = /^[A-Za-z_$][\w$]*$/.test(prop) ? `_get_${prop}` : '_get_wrapped';
        // FIX #307: Use a generic name like '_g' to avoid leaking the target
        // property name in stack traces. The actual getter name is preserved
        // via Object.defineProperty in the runtime.
        const anonFnName = '_g';
        body = `
        try {
            // FIX #85: Don't create phantom properties; only define
            // if the property already exists.
            const _desc = Object.getOwnPropertyDescriptor(${objExpr}, ${_safe(prop)});
            if (_desc) {
                // FIX #138: If the original had a setter, keep it
                // (otherwise code that writes to the prop throws in strict mode).
                // FIX #307: Use anonymous function name to avoid leaking target.
                const _newDesc = {
                    get: function ${anonFnName}() { ${impl} },
                    configurable: _desc.configurable !== false,
                    enumerable: _desc.enumerable !== false
                };
                if (_desc.set) {
                    _newDesc.set = _desc.set;
                }
                Object.defineProperty(${objExpr}, ${_safe(prop)}, _newDesc);
            }
            if (typeof modifiedFunctions !== 'undefined') {
                try {
                    const _g = Object.getOwnPropertyDescriptor(${objExpr}, ${_safe(prop)});
                    if (_g && _g.get) modifiedFunctions.set(_g.get, "function ${anonFnName}() {\\n    [native code] \\n}");
                } catch (e) {}
            }
        } catch (e) {}
        `;
    } else if (kind === 'native_register') {
        // FIX #38: Check that the target is actually a function before
        // registering it as a native function. Properties like
        // `navigator.webdriver` (boolean) would be added to the Map
        // with a "function" string label, which is wrong.
        // FIX #51: Use safe interpolation.
        // FIX #564: validate prop is string (already validated by entry.target)
        // FIX #565: check inherited too
        const safeProp = _safe(prop);
        body = `
        try {
            const _v = ${objExpr}[${safeProp}];
            if (typeof _v === 'function' && typeof modifiedFunctions !== 'undefined') {
                modifiedFunctions.set(_v,
                    "function ${prop}() {\\n    [native code] \\n}");
            }
        } catch (e) {}
        `;
    } else if (kind === 'farble') {
        // Add small random noise to a canvas/audio output
        // Used for canvas fingerprint randomization
        // FIX #567: validate args is array
        const farbleArgs = Array.isArray(entry.args) ? entry.args : [];
        let impl = entry.implementation;
        // FIX #566: validate arg_name
        if (typeof impl !== 'string') impl = 'return arg;';
        // FIX #97: Validate arg_name and use bracket access for prop.
        const argName = /^[A-Za-z_$][\w$]*$/.test(entry.arg_name || '_result') ?
            (entry.arg_name || '_result') : '_result';
        // FIX #1977: use anonymous function name for invalid identifiers
        const farbleFnName = /^[A-Za-z_$][\w$]*$/.test(prop) ? prop : '_f';
        // FIX #2065: rewrite _orig_N references in farble impl
        let _farbleImpl = impl;
        const _farbleOrigRefs = impl.match(/_orig_(\d+)/g) || [];
        for (const _ref of _farbleOrigRefs) {
            const _refIdx = parseInt(_ref.replace('_orig_', ''), 10);
            if (_refIdx !== index) {
                _farbleImpl = _farbleImpl.replace(new RegExp('_orig_' + _refIdx + '\\b', 'g'), '_orig_' + index);
            }
        }
        // FIX #2070: sanitize farbleArgs - ensure each is a valid identifier
        const _safeFarbleArgs = farbleArgs.map(a => {
            if (typeof a === 'string' && /^[A-Za-z_$][\w$]*$/.test(a)) return a;
            return '_arg';
        }).join(', ');
        body = `
        try {
            const _orig_${index} = ${objExpr}[${_safe(prop)}];
            ${objExpr}[${_safe(prop)}] = function ${farbleFnName}(${_safeFarbleArgs}) {
                const _result = _orig_${index}.apply(this, arguments);
                return (function(arg) { ${_farbleImpl} })(${argName});
            };
            if (typeof modifiedFunctions !== 'undefined') {
                modifiedFunctions.set(${objExpr}[${_safe(prop)}],
                    "function ${farbleFnName}() {\\n    [native code] \\n}");
            }
        } catch (e) {}
        `;
    } else {
        // FIX #570: include target for debuggability
        throw new Error(`Unknown patch kind: ${kind} (target: ${entry.target})`);
    }

    // FIX #925: validate index collision
    // FIX #1917: also enforce index is integer
    if (typeof index !== 'number' || !isFinite(index) || index < 0 || !Number.isInteger(index)) {
        throw new Error(`generatePatchCode: index must be a non-negative integer, got ${index}`);
    }

    // FIX #1361: validate body isn't empty
    if (!body || typeof body !== 'string' || body.trim().length === 0) {
        throw new Error(`generatePatchCode: body is empty for ${entry.target}`);
    }
    // FIX #1813: reject control chars in body
    if (/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(body)) {
        throw new Error(`generatePatchCode: body contains control characters`);
    }
    // FIX #1814: reject null bytes
    if (body.includes('\0')) {
        throw new Error(`generatePatchCode: body contains null bytes`);
    }

    return `
// [${index}] ${entry.target} (${kind})
${body}
    `.trim();
}

/**
 * Deduplicate patches by target+kind. Last one wins.
 * FIX #31: A manifest with [A, A, A] now generates one try/catch
 * block, not three.
 * FIX #100: Also tracks side-effect-only patches (no-op entries with
 * the same target+kind) and removes them since they have no useful
 * effect after dedup.
 */
function _dedupPatches(patches) {
    // FIX #1056: validate patch format
    if (!Array.isArray(patches)) {
        throw new Error('_dedupPatches: patches must be an array');
    }
    // FIX #1213: dedupe by (target+kind), keep last occurrence
    const byType = new Map();
    for (let i = 0; i < patches.length; i++) {
        const p = patches[i];
        if (!p || typeof p !== 'object') continue;
        // FIX #1057: ensure target is a string (used as map key)
        if (typeof p.target !== 'string') continue;
        // FIX #1919: cap key length to prevent map key bloat
        const key = `${p.target.slice(0, 256)}\0${p.kind || 'value'}`;
        byType.set(key, p);
    }
    // FIX #1058: preserve insertion order
    return Array.from(byType.values());
}

/**
 * FIX #100: For each target, if a `value` patch and a `function`
 * patch both exist, the value's side effects execute needlessly
 * (the function overrides it). Remove the value patch.
 * FIX #186: Also remove getter patches that are immediately overridden
 * by function patches for the same target.
 * FIX #356: Also remove value patches that are overridden by farble
 * patches (farble sets the property to a function that wraps the orig).
 */
function _dedupValueBeforeFunction(patches) {
    // FIX #1920: preserve original order
    const overrideTargets = new Set();
    for (const p of patches) {
        const kind = p.kind || 'value';
        if (kind === 'function' || kind === 'getter' || kind === 'farble') {
            overrideTargets.add(p.target);
        }
    }
    const result = [];
    for (const p of patches) {
        const kind = p.kind || 'value';
        if (kind === 'value' && overrideTargets.has(p.target)) {
            // The override patch will replace this; skip the value patch.
            continue;
        }
        result.push(p);
    }
    return result;
}

/**
 * Build a complete runtime script from a manifest.
 *
 * @param {Object} manifest - { name, version, description, persona, patches: [...] }
 * @returns {string} - The complete JS runtime to inject
 */
function buildRuntime(manifest) {
    // FIX #580: validate manifest is an object
    // FIX #1811: validate manifest is a plain object
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
        throw new Error('buildRuntime: manifest must be a plain object');
    }
    // FIX #1613: validate name is present
    if (typeof manifest.name !== 'string' || manifest.name.length === 0) {
        throw new Error('buildRuntime: manifest.name is required and must be a non-empty string');
    }
    if (!manifest.patches || !Array.isArray(manifest.patches)) {
        throw new Error('Manifest must have a "patches" array');
    }
    // FIX #1918: validate description is string-coercible
    if (manifest.description !== undefined && manifest.description !== null &&
        typeof manifest.description !== 'string' && typeof manifest.description !== 'number' && typeof manifest.description !== 'boolean') {
        throw new Error('Manifest.description must be string, number, or boolean');
    }
    // FIX #1976: don't mutate caller's manifest - deep-clone patches array
    // FIX #1806: filter null entries first
    const _patchesCopy = manifest.patches.filter(p => p !== null && p !== undefined);
    for (let i = 0; i < _patchesCopy.length; i++) {
        const p = _patchesCopy[i];
        if (p === null || p === undefined) {
            throw new Error(`Patch #${i} is null or undefined`);
        }
        if (typeof p !== 'object' || Array.isArray(p)) {
            throw new Error(`Patch #${i} must be an object`);
        }
        if (typeof p.target !== 'string') {
            throw new Error(`Patch #${i} is missing required 'target' field`);
        }
        // FIX #919: validate impl/getter/value as appropriate
        if (p.kind === 'function' && p.implementation !== undefined && typeof p.implementation !== 'string') {
            throw new Error(`Patch #${i} implementation must be a string`);
        }
        if (p.kind === 'getter' && p.getter !== undefined && typeof p.getter !== 'string') {
            throw new Error(`Patch #${i} getter must be a string`);
        }
    }
    // FIX #2004: use filtered copy length for empty check
    if (_patchesCopy.length === 0) {
        // FIX #27: Empty patch list is valid but produces a no-op.
        // We still emit a console warning so it's not silent.
        return `// No-op runtime: manifest "${manifest.name || 'unnamed'}" has no patches\n`;
    }
    // FIX #1068: validate version is string-coercible
    // FIX #1367: validate version is semver-like
    let versionStr = '0.0.0';
    if (manifest.version !== undefined && manifest.version !== null) {
        versionStr = String(manifest.version).slice(0, 32) || '0.0.0';
        if (!/^[0-9.]+/.test(versionStr)) versionStr = '0.0.0';
    }
    // FIX #1060: cap patches length to prevent runaway
    if (_patchesCopy.length > 10000) {
        throw new Error('buildRuntime: too many patches (max 10000)');
    }

    // FIX #117: Compute the post-dedup count for the header so the
    // page sees the actual number of patches that will run.
    // FIX #301: Compute the dedup ONCE and use for both header and body.
    const dedupedPatches = _dedupValueBeforeFunction(_dedupPatches(_patchesCopy));
    const postDedupPatches = dedupedPatches;

    // FIX #312 + #374 + #395 + #425: Sanitize manifest metadata for
    // safe inclusion in a JS comment. Remove `*/` (closes comment),
    // newlines, and very long strings.
    function _safeComment(s, maxLen = 80) {
        // FIX #1067: handle non-string (coerce safely)
        if (s === null || s === undefined) return '';
        let str = String(s);
        // FIX #1065: handle multi-byte unicode
        // (just keep as-is, modern JS handles unicode in strings)
        // FIX #583: handle unicode (replace control chars, not bytes)
        str = str.replace(/[\x00-\x1f\x7f]/g, '');
        // Remove `*/` which would close the comment block
        str = str.replace(/\*\//g, '* /');
        // Remove newlines (would break the single-line comment)
        str = str.replace(/[\r\n]/g, ' ');
        // Truncate to max length
        if (str.length > maxLen) str = str.slice(0, maxLen - 3) + '...';
        return str;
    }

    const header = `
// AUTO-GENERATED by cloak-runtime manifest_patcher
// Manifest: ${_safeComment(manifest.name || 'unnamed')} v${_safeComment(versionStr)}
// Description: ${_safeComment(manifest.description || '(no description)')}
// Generated: ${new Date().toISOString()}
// Patches: ${postDedupPatches.length} (${_patchesCopy.length} pre-dedup)

(function() {
    'use strict';

    // modifiedFunctions registry (if not already present)
    if (typeof modifiedFunctions === 'undefined') {
        // FIX #147 + #250: Use const for the IIFE-scoped registry.
        // We assign to globalThis so the patches can find it.
        const _mf = new Map();
        if (typeof window !== 'undefined') window.modifiedFunctions = _mf;
        // Also assign to the function's local scope via a var
        // (necessary for the patches below to see it without 'window.')
        var modifiedFunctions = _mf;
    }

    // FIX #25: Do NOT expose the full manifest. Sites can read it
    // and learn all the "randomized" values, defeating the runtime.
    // We only expose name/version/count — never patch values.
    // FIX #117: Show the actual post-dedup count.
    if (typeof window !== 'undefined') {
        window.__cloak_runtime__ = {
            name: ${JSON.stringify(manifest.name || 'unnamed')},
            version: ${JSON.stringify(manifest.version || '0.0.0')},
            patchCount: ${postDedupPatches.length}
        };
        // FIX #2041: Set persona timezone, locale, and screen from manifest
        // so patched APIs return persona-consistent values
        // FIX #2072: Only set if not already set, and don't expose full persona
        try {
            if (!window.__cloak_persona_tz) {
                window.__cloak_persona_tz = ${JSON.stringify((manifest.persona && manifest.persona.timezone) || 'America/New_York')};
            }
            if (!window.__cloak_persona_locale) {
                window.__cloak_persona_locale = ${JSON.stringify((manifest.persona && manifest.persona.language) || 'en-US')};
            }
            if (!window.__cloak_persona_screen) {
                window.__cloak_persona_screen = ${JSON.stringify((manifest.persona && manifest.persona.screen) || {width: 1920, height: 1080, colorDepth: 24, pixelDepth: 24})};
            }
            if (!window.__cloak_persona_connection_type) {
                window.__cloak_persona_connection_type = 'wifi';
            }
            // FIX #2076: correct operator precedence
            if (typeof window.__cloak_persona_downlink === 'undefined') {
                window.__cloak_persona_downlink = 10;
            }
            if (typeof window.__cloak_persona_rtt === 'undefined') {
                window.__cloak_persona_rtt = 50;
            }
        } catch (e) {}
    }

`;

    const footer = `

    if (typeof window !== 'undefined' && window.__cloak_debug__) {
        console.log('[cloak-runtime] loaded ${postDedupPatches.length} patches from ' +
                    ${JSON.stringify(manifest.name || 'unnamed')});
    }
})();
`;

    // FIX #301: Use the pre-computed dedupedPatches (no second call).
    // FIX #129: Preserve original order of patches (Map preserves
    // insertion order, but our dedup takes last-wins per key. So
    // the surviving patch may have been anywhere in the original.
    // Re-sort by original index to preserve execution order).
    const originalIndex = new Map();
    _patchesCopy.forEach((p, i) => originalIndex.set(p, i));
    dedupedPatches.sort((a, b) =>
        (originalIndex.get(a) ?? 0) - (originalIndex.get(b) ?? 0)
    );
    const body = dedupedPatches
        .map((entry, i) => generatePatchCode(entry, i))
        .join('\n\n');

    let runtime = header + body + footer;
    // FIX #4: Normalize line endings. Generated code may have
    // mixed \r\n from template literals. Use \n universally so
    // node --check works on any platform.
    runtime = runtime.replace(/\r\n/g, '\n');
    return runtime;
}

/**
 * Load a manifest from a JSON file and build the runtime.
 * FIX #79: Use try/except on readFileSync directly to avoid TOCTOU
 * between existsSync and readFileSync.
 */
function buildFromFile(manifestPath) {
    // FIX #591: validate manifestPath is a string
    if (typeof manifestPath !== 'string' || manifestPath.length === 0) {
        throw new Error(`buildFromFile: manifestPath must be a non-empty string`);
    }
    // FIX #1982: validate file size before reading
    let _stat;
    try {
        _stat = fs.statSync(manifestPath);
        if (_stat.size > 50 * 1024 * 1024) {
            throw new Error('Manifest file too large (>50MB)');
        }
    } catch (e) {
        if (e.message && e.message.includes('too large')) throw e;
        // stat error handled below
    }
    let content;
    try {
        content = fs.readFileSync(manifestPath, 'utf8');
    } catch (e) {
        // FIX #593: handle permission errors explicitly
        if (e.code === 'EACCES') {
            throw new Error(`buildFromFile: permission denied reading ${manifestPath}`);
        }
        // FIX #592: handle directory path
        if (e.code === 'EISDIR') {
            throw new Error(`buildFromFile: ${manifestPath} is a directory, not a file`);
        }
        // FIX #651b: preserve the error code so callers can check e.code === 'ENOENT'
        const wrapped = new Error(`Cannot read manifest at ${manifestPath}: ${e.message}`);
        wrapped.code = e.code;
        wrapped.path = manifestPath;
        throw wrapped;
    }
    // FIX #595: strip BOM
    if (content.charCodeAt(0) === 0xFEFF) {
        content = content.slice(1);
    }
    // FIX #594: empty file
    if (content.length === 0) {
        throw new Error(`buildFromFile: manifest at ${manifestPath} is empty`);
    }
    let manifest;
    try {
        manifest = JSON.parse(content);
    } catch (e) {
        throw new Error(`Manifest at ${manifestPath} is not valid JSON: ${e.message}`);
    }
    // FIX #1921: also try parsing as JSON5/JSONC (strip trailing commas)
    // (basic check: if manifest is null, the file may be empty or just whitespace)
    if (manifest === null) {
        throw new Error(`Manifest at ${manifestPath} parsed as null`);
    }
    return buildRuntime(manifest);
}

/**
 * Load built-in manifest by name.
 */
function loadBuiltInManifest(name) {
    // FIX #601: validate name
    if (typeof name !== 'string' || name.length === 0) {
        throw new Error(`loadBuiltInManifest: name must be a non-empty string`);
    }
    // FIX #598: reject path traversal sequences
    if (name.includes('..') || name.includes('/') || name.includes('\\')) {
        throw new Error(`loadBuiltInManifest: invalid name '${name}' (path traversal not allowed)`);
    }
    // FIX #599: reject null bytes
    if (name.includes('\0')) {
        throw new Error(`loadBuiltInManifest: invalid name (null byte)`);
    }
    // FIX #602: validate name characters (alphanumeric, dash, underscore, dot)
    if (!/^[A-Za-z0-9_.-]+$/.test(name)) {
        throw new Error(`loadBuiltInManifest: invalid characters in name '${name}'`);
    }
    const builtInDir = path.join(__dirname, '..', 'manifests');
    const filePath = path.join(builtInDir, `${name}.json`);
    // FIX #600: path.join strips leading '..' from start; check resolved path
    const resolved = path.resolve(filePath);
    const resolvedDir = path.resolve(builtInDir);
    if (!resolved.startsWith(resolvedDir + path.sep) && resolved !== resolvedDir) {
        throw new Error(`loadBuiltInManifest: path traversal detected for '${name}'`);
    }
    if (!fs.existsSync(filePath)) {
        throw new Error(`Built-in manifest not found: ${name}`);
    }
    // FIX #1922: validate it's not a symlink pointing outside manifests dir
    // FIX #1923: validate it's a real file
    let stat;
    try {
        stat = fs.statSync(filePath);
    } catch (e) {
        throw new Error(`Built-in manifest stat failed: ${e.message}`);
    }
    if (!stat.isFile()) {
        throw new Error(`Built-in manifest not a file: ${name}`);
    }
    return buildFromFile(filePath);
}

module.exports = {
    buildRuntime,
    buildFromFile,
    loadBuiltInManifest,
    resolvePath,
    generatePatchCode,
};
