#!/usr/bin/env python3
"""
dedup_conflict_audit.py - Detect redundant and conflicting patches in prototype_hardener.js

PURPOSE (Week 3 of the productionization plan):
    Audit the 3,400+ patches in prototype_hardener.js to find:
    1. REDUNDANT patches: same target + similar implementation, applied multiple times
    2. CONFLICTING patches: same target, different implementations (last wins, but it
       means earlier ones are wasted code)
    3. DEAD patches: targets that get overridden anyway (e.g., Object.defineProperty
       followed by direct assignment that wipes it)
    4. ORDER-DEPENDENT patches: patch B breaks patch A, requires execution order

    This script produces a report you can use to:
    - Delete redundant patches (reduce 27K -> 8K lines)
    - Resolve conflicts (decide which implementation is "right")
    - Fix ordering issues
    - Generate the canonical manifest for cloak-runtime

USAGE:
    python3 dedup_conflict_audit.py /home/user/project/browser-runtime/prototype_hardener.js
    python3 dedup_conflict_audit.py /home/user/project/browser-runtime/prototype_hardener.js --json report.json
    python3 dedup_conflict_audit.py /home/user/project/browser-runtime/prototype_hardener.js --suggest-deletes

OUTPUT:
    Human-readable report + optional JSON. The --suggest-deletes mode prints
    line ranges that can be safely removed without changing behavior.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class PatchInfo:
    """Information about a single patch application."""
    target: str  # e.g. "HTMLCanvasElement.prototype.toDataURL"
    kind: str  # 'native_register', 'override', 'safe_override', 'defineProperty', 'getter_setter'
    line: int
    end_line: int
    snippet: str  # first 200 chars
    is_redundant_with: List[int] = field(default_factory=list)  # line numbers
    conflicts_with: List[int] = field(default_factory=list)
    overrides_target_lines: List[int] = field(default_factory=list)


@dataclass
class AuditReport:
    total_patches: int = 0
    unique_targets: int = 0
    redundant_patches: int = 0
    conflicting_patches: int = 0
    dead_patches: int = 0
    order_dependent: int = 0
    redundancy_pairs: List[Tuple[int, int, str]] = field(default_factory=list)
    conflicts: List[Tuple[int, int, str, str]] = field(default_factory=list)
    dead_patches_list: List[int] = field(default_factory=list)
    suggested_deletions: List[Tuple[int, int, str]] = field(default_factory=list)
    target_frequency: Dict[str, int] = field(default_factory=dict)
    by_kind: Dict[str, int] = field(default_factory=dict)


class PrototypeHardenerAuditor:
    """Analyzes prototype_hardener.js for redundancy and conflicts."""

    # Patterns that indicate a patch application
    PATCH_PATTERNS = [
        # Function call patterns: registerXxx(proto, 'method')
        (r'_regNative\s*\(\s*([^,]+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*\)', 'native_register'),
        (r'_regGetter\s*\(\s*([^,]+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*\)', 'getter_register'),
        (r'_regCtor\s*\(\s*([^,]+)\s*\)', 'ctor_register'),

        # Override patterns
        (r'safePrototypeOverride\s*\(\s*([^,]+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*,', 'safe_override'),
        (r'safePrototypePropertyOverride\s*\(\s*([^,]+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*,', 'safe_prop_override'),
        (r'safePropertyOverride\s*\(\s*([^,]+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*,', 'safe_prop_override'),

        # Direct prototype assignment: Proto.prototype.method = function ...
        (r'([A-Za-z_][\w.]*?)\.prototype\.([A-Za-z_]\w*)\s*=\s*function', 'override'),

        # Object.defineProperty
        (r'Object\.defineProperty\s*\(\s*([^,]+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*,', 'defineProperty'),

        # Try-catch with assignment
        (r'try\s*\{\s*([A-Za-z_][\w.]*?)\.prototype\.([A-Za-z_]\w*)', 'override'),
    ]

    # Targets that look like "register the same native multiple times" (always redundant)
    NATIVE_REGISTER_REDUNDANT_THRESHOLD = 3

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.content = self.file_path.read_text()
        self.lines = self.content.splitlines()
        self.patches: List[PatchInfo] = []
        self.report = AuditReport()

    def run(self) -> AuditReport:
        """Run the full audit pipeline."""
        print(f"Auditing {self.file_path} ({len(self.lines):,} lines)...", file=sys.stderr)

        # Phase 1: Extract all patches
        self._extract_patches()
        print(f"  Phase 1: Extracted {len(self.patches):,} patch applications", file=sys.stderr)

        # Phase 2: Count by target
        self._compute_target_frequency()
        print(f"  Phase 2: {self.report.unique_targets:,} unique targets, "
              f"{self.report.total_patches:,} total patches", file=sys.stderr)

        # Phase 3: Find redundant patches
        self._find_redundant_patches()
        print(f"  Phase 3: {self.report.redundant_patches:,} redundant patches found",
              file=sys.stderr)

        # Phase 4: Find conflicting patches
        self._find_conflicting_patches()
        print(f"  Phase 4: {self.report.conflicting_patches:,} conflicting patches found",
              file=sys.stderr)

        # Phase 5: Find dead patches
        self._find_dead_patches()
        print(f"  Phase 5: {self.report.dead_patches:,} dead patches found", file=sys.stderr)

        # Phase 6: Compute suggested deletions
        self._compute_suggested_deletions()
        print(f"  Phase 6: {len(self.report.suggested_deletions):,} deletion suggestions",
              file=sys.stderr)

        return self.report

    def _extract_patches(self) -> None:
        """Find all patch application points in the file.
        FIX #47: Now handles multi-line function calls.
        """
        # Concatenate lines for multi-line matching
        full_text = '\n'.join(self.lines)
        for line_num, line in enumerate(self.lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith('//'):
                continue

            for pattern, kind in self.PATCH_PATTERNS:
                # FIX #47: Allow patterns to span up to 3 lines
                # Look at this line + next 2 for multi-line matches
                search_text = '\n'.join(self.lines[line_num - 1:line_num + 2])
                match = re.search(pattern, search_text)
                if match:
                    # Construct target string
                    groups = match.groups()
                    if kind in ('native_register', 'getter_register',
                                'safe_override', 'safe_prop_override'):
                        target = f"{groups[0]}.{groups[1]}"
                    elif kind == 'ctor_register':
                        target = groups[0]
                    elif kind == 'override':
                        target = f"{groups[0]}.prototype.{groups[1]}"
                    elif kind == 'defineProperty':
                        target = f"{groups[0]}.{groups[1]}"
                    else:
                        target = str(groups)

                    end_line = self._find_block_end(line_num, stripped)

                    # Avoid double-counting: only add if no patch at
                    # this exact line already
                    if not any(p.line == line_num for p in self.patches):
                        self.patches.append(PatchInfo(
                            target=target,
                            kind=kind,
                            line=line_num,
                            end_line=end_line,
                            snippet=line[:200],
                        ))
                    break  # one match per line is enough

        self.report.total_patches = len(self.patches)
        by_kind = defaultdict(int)
        for p in self.patches:
            by_kind[p.kind] += 1
        self.report.by_kind = dict(by_kind)

    def _find_block_end(self, start_line: int, first_line: str) -> int:
        """Find approximate end of a patch block."""
        # Heuristic: blocks end at next blank line followed by closing brace
        # at the same indent level as the first line. For simplicity,
        # assume single-line patches are most common.
        if first_line.endswith(';') and '{' not in first_line:
            return start_line
        if '{' in first_line and '}' in first_line:
            return start_line
        # Multi-line: look forward for closing
        depth = first_line.count('{') - first_line.count('}')
        for i in range(start_line, min(start_line + 100, len(self.lines))):
            depth += self.lines[i].count('{') - self.lines[i].count('}')
            if depth <= 0 and i > start_line:
                return i
        return start_line

    def _compute_target_frequency(self) -> None:
        """Count how many times each target is patched."""
        freq = defaultdict(int)
        for p in self.patches:
            freq[p.target] += 1
        self.report.target_frequency = dict(freq)
        self.report.unique_targets = len(freq)

    def _find_redundant_patches(self) -> None:
        """Find patches that re-register the same target with the same operation."""
        # Group by target + kind
        groups = defaultdict(list)
        for p in self.patches:
            groups[(p.target, p.kind)].append(p)

        redundant_count = 0
        for (target, kind), patches in groups.items():
            if len(patches) < 2:
                continue
            # Multiple patches on same target with same kind = redundant
            # (last one wins; earlier ones are wasted code)
            for i, p in enumerate(patches[:-1]):
                redundant_count += 1
                self.report.redundancy_pairs.append(
                    (p.line, patches[-1].line, f"{target} ({kind})")
                )
                p.is_redundant_with = [other.line for other in patches[i+1:]]

        self.report.redundant_patches = redundant_count

    def _find_conflicting_patches(self) -> None:
        """Find patches on same target with different implementations."""
        # Group by target
        groups = defaultdict(list)
        for p in self.patches:
            groups[p.target].append(p)

        conflict_count = 0
        for target, patches in groups.items():
            if len(patches) < 2:
                continue
            # Different kinds on same target = potential conflict
            kinds = set(p.kind for p in patches)
            if len(kinds) > 1:
                # Pick the last two for the conflict report
                last_two = patches[-2:]
                if last_two[0].kind != last_two[1].kind:
                    conflict_count += 1
                    self.report.conflicts.append(
                        (last_two[0].line, last_two[1].line,
                         f"{last_two[0].kind}", f"{last_two[1].kind}")
                    )
                    last_two[0].conflicts_with.append(last_two[1].line)
                    last_two[1].conflicts_with.append(last_two[0].line)

        self.report.conflicting_patches = conflict_count

    def _find_dead_patches(self) -> None:
        """Find patches that are immediately overridden by another patch."""
        # FIX #13: A naive "target = X" check matches function declarations
        # like `function HTMLCanvasElement.prototype.toDataURL() {}` (which
        # wouldn't actually parse, but the regex matches the prefix).
        # Use a stricter pattern: only match assignments that start at
        # the beginning of a line with optional whitespace, are not part
        # of a function declaration, and end with semicolon or operator.
        dead_count = 0
        for i, p in enumerate(self.patches):
            look_end = min(p.line + 5, len(self.lines))
            for j in range(p.line, look_end):
                line = self.lines[j]
                # Strict: line starts with optional whitespace + target +
                # whitespace + '=' + non-= char (so we don't match ==).
                # Exclude lines that look like function declarations.
                if (re.match(rf'^\s*{re.escape(p.target)}\s*=\s*[^=]', line) and
                        not re.match(rf'^\s*function\s+{re.escape(p.target)}', line)):
                    dead_count += 1
                    self.report.dead_patches_list.append(p.line)
                    p.overrides_target_lines.append(j + 1)
                    break

        self.report.dead_patches = dead_count

    def _compute_suggested_deletions(self) -> None:
        """Compute safe deletion candidates."""
        # Strategy: if patch A is redundant with patch B (B is later),
        # and B has the same kind, then A can be deleted.
        for p in self.patches:
            if p.is_redundant_with:
                for later_line in p.is_redundant_with:
                    # Only suggest deletion if the later patch has the same
                    # operation type (last-wins is safe).
                    later_patch = next(
                        (x for x in self.patches if x.line == later_line), None
                    )
                    if later_patch and later_patch.kind == p.kind:
                        self.report.suggested_deletions.append(
                            (p.line, p.end_line, f"Redundant: {p.target} "
                             f"re-registered at line {later_line}")
                        )

    def print_report(self, suggest_deletes: bool = False) -> None:
        """Print a human-readable audit report."""
        print()
        print("=" * 78)
        print(f"PROTOTYPE_HARDENER.JS AUDIT REPORT")
        print("=" * 78)
        print(f"File: {self.file_path}")
        print(f"Total lines: {len(self.lines):,}")
        print()
        print("PATCH SUMMARY")
        print("-" * 78)
        print(f"Total patches found:        {self.report.total_patches:>8,}")
        print(f"Unique targets:             {self.report.unique_targets:>8,}")
        print(f"Redundant patches:          {self.report.redundant_patches:>8,}")
        print(f"Conflicting patches:        {self.report.conflicting_patches:>8,}")
        print(f"Dead patches:               {self.report.dead_patches:>8,}")
        print()
        print("BY KIND")
        print("-" * 78)
        for kind, count in sorted(self.report.by_kind.items(),
                                   key=lambda x: -x[1]):
            print(f"  {kind:<30} {count:>6,}")
        print()
        print("MOST-PATCHED TARGETS (top 15)")
        print("-" * 78)
        top_targets = sorted(self.report.target_frequency.items(),
                              key=lambda x: -x[1])[:15]
        for target, count in top_targets:
            if count > 1:
                print(f"  {count:>4}x  {target}")
        print()

        if self.report.redundancy_pairs:
            print(f"REDUNDANT PATCHES (showing first 20 of {len(self.report.redundancy_pairs):,})")
            print("-" * 78)
            for line_a, line_b, target in self.report.redundancy_pairs[:20]:
                print(f"  Line {line_a:>6} redundant with line {line_b:>6}: {target}")
            if len(self.report.redundancy_pairs) > 20:
                print(f"  ... and {len(self.report.redundancy_pairs) - 20:,} more")
            print()

        if self.report.conflicts:
            print(f"CONFLICTING PATCHES (showing first 20 of {len(self.report.conflicts):,})")
            print("-" * 78)
            for line_a, line_b, kind_a, kind_b in self.report.conflicts[:20]:
                print(f"  Line {line_a} ({kind_a}) vs line {line_b} ({kind_b})")
            if len(self.report.conflicts) > 20:
                print(f"  ... and {len(self.report.conflicts) - 20:,} more")
            print()

        if self.report.dead_patches_list:
            print(f"DEAD PATCHES (showing first 20 of {len(self.report.dead_patches_list):,})")
            print("-" * 78)
            for line in self.report.dead_patches_list[:20]:
                print(f"  Line {line:>6} (immediately overridden)")
            if len(self.report.dead_patches_list) > 20:
                print(f"  ... and {len(self.report.dead_patches_list) - 20:,} more")
            print()

        if suggest_deletes and self.report.suggested_deletions:
            print("SUGGESTED DELETIONS (safe to remove)")
            print("-" * 78)
            print("# Add these sed commands to a script to remove redundant code:")
            print()
            for line_a, line_b, reason in self.report.suggested_deletions[:30]:
                print(f"# Lines {line_a}-{line_b}: {reason}")
                print(f"sed -i '{line_a},{line_b}d' {self.file_path}")
            if len(self.report.suggested_deletions) > 30:
                print(f"# ... and {len(self.report.suggested_deletions) - 30:,} more")
            print()

        # Reduction estimate
        if self.report.suggested_deletions:
            total_lines_to_delete = sum(
                end - start + 1 for start, end, _ in self.report.suggested_deletions
            )
            new_size = len(self.lines) - total_lines_to_delete
            pct = 100.0 * total_lines_to_delete / len(self.lines)
            print("REDUCTION ESTIMATE")
            print("-" * 78)
            print(f"  Current:                  {len(self.lines):>8,} lines")
            print(f"  After deletions:          {new_size:>8,} lines")
            print(f"  Reduction:                {pct:>7.1f}%")
            print()

        print("=" * 78)

    def to_json(self) -> str:
        """Serialize report to JSON."""
        return json.dumps(asdict(self.report), indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description="Audit prototype_hardener.js for redundant/conflicting patches"
    )
    parser.add_argument("file", help="Path to prototype_hardener.js")
    parser.add_argument("--json", help="Write JSON report to this file")
    parser.add_argument("--suggest-deletes", action="store_true",
                        help="Print sed commands for safe deletions")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"Error: {args.file} not found", file=sys.stderr)
        sys.exit(1)

    auditor = PrototypeHardenerAuditor(args.file)
    report = auditor.run()
    auditor.print_report(suggest_deletes=args.suggest_deletes)

    if args.json:
        Path(args.json).write_text(auditor.to_json())
        print(f"\nJSON report written to {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
