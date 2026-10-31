# Bug report — SEO

* **Repo:** https://github.com/jatinpruthvi/SEO  
* **Branch:** `dolphin-audit`  
* **Base commit:** `b5b48c5`  
* **Model:** Dolphin3.0-Llama3.1-8B (Q4_K_M, local Colab CPU)  
* **Scanned:** 2026-10-31 05:17  
* **Files scanned:** 5  
* **Findings:** 2 (critical 0 · high 1 · medium 1 · low 0)

> LLM-generated audit — verify each finding before changing code.

## HIGH

### [HIGH] Incorrect early exit if manifests directory does not exist.

`project/cloak-runtime/scripts/build.js:18` · _logic error_

The script exits immediately when the manifests directory is missing, preventing any further checks or processing. This can lead to incomplete or no output being generated.

**Fix:** Move the exit call to after all necessary checks and processing.

## MEDIUM

### [MEDIUM] Incorrect line count calculation.

`project/cloak-runtime/scripts/build.js:38` · _off-by-one_

The script counts lines in the generated runtime, but `split(' ').length` includes an extra line for the last newline character. This can cause incorrect output in logs and potentially mislead users.

**Fix:** Use `split(' ').length - 1` to correctly count the number of lines.
