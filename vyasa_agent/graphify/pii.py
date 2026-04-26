# SPDX-License-Identifier: Apache-2.0
"""PII redaction helpers for Graphify ingest.

This module detects the PII classes Graymatter cares about in India-
centric chat traffic (phone, email, PAN, Aadhaar, OTP codes) and replaces
hits with stable opaque tokens. The token map is returned to the caller
so the private L1 store can retain a reversible record while the shared
L2 graph sees only scrubbed text.

The design intent (see design-03-memory.md §8) is:

* ``scrub`` — best-effort, never raises; used inside the write pipeline.
* ``check_before_write`` — strict gate; raises :class:`PIILeakError` when
  any residue remains, so unscrubbed payloads cannot hit L2.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from re import Pattern

from .types import PIILeakError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# regex patterns
# --------------------------------------------------------------------------- #

# Indian mobile: optional +91/0091/91 prefix then a leading digit in 6-9
# plus 9 more digits. Internal spaces and hyphens are tolerated (chat
# traffic routinely splits the number) but the total digit count is
# pinned to 10. The leading-class restriction keeps us from vacuuming
# every 10-digit number on the planet.
_PHONE_RE: Pattern[str] = re.compile(
    r"(?<!\d)(?:\+?91[\s\-]?|0091[\s\-]?)?[6-9]\d(?:[\s\-]?\d){8}(?!\d)",
)

# RFC-ish — not the full 5322, but good enough for chat ingest.
_EMAIL_RE: Pattern[str] = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)

# Indian PAN: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).
_PAN_RE: Pattern[str] = re.compile(r"(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])")

# Aadhaar: 12 digits, optionally split into 4-4-4 groups.
_AADHAAR_RE: Pattern[str] = re.compile(r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)")

# OTP: 4-6 digit numeric code appearing next to an OTP keyword in a
# window of ~40 chars. Avoid matching arbitrary integers.
_OTP_RE: Pattern[str] = re.compile(
    r"(?i)\b(?:otp|one[\s\-]?time(?:\s+pin|\s+password|\s+code)?|"
    r"verification\s+code|passcode|pin\s+code|confirmation\s+code)"
    r"[^\d]{0,40}?(?<!\d)(\d{4,6})(?!\d)",
)

# Scan order matters: phone before Aadhaar because 12-digit phone strings
# don't exist in this region but Aadhaar can otherwise eat phone variants.
_SCAN_ORDER: tuple[tuple[str, Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("pan", _PAN_RE),
    ("aadhaar", _AADHAAR_RE),
    ("phone", _PHONE_RE),
    ("otp", _OTP_RE),
)


# --------------------------------------------------------------------------- #
# redaction record
# --------------------------------------------------------------------------- #


@dataclass
class _Redaction:
    kind: str
    at: int
    hashed: str


@dataclass
class PIIScrubber:
    """Stateless scrubber. Instances are cheap to create."""

    # Keep a running counter of token ids per scrub() call so the map
    # can be rebuilt in caller code if they want reversibility.
    _counters: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # primary API
    # ------------------------------------------------------------------ #

    def scrub(self, text: str) -> tuple[str, dict[str, str]]:
        """Strip PII from ``text`` and return ``(scrubbed, token_map)``.

        ``token_map`` keys are the tokens we inserted
        (``<PHONE_1>`` / ``<EMAIL_1>`` / …); values are the original
        strings. The caller may persist this map locally (L1) and discard
        it before promoting the scrubbed text to L2.
        """

        token_map: dict[str, str] = {}
        counters: dict[str, int] = {}
        redactions: list[_Redaction] = []

        # Collect all matches in one pass so positional tokens stay
        # stable regardless of pattern order.
        spans: list[tuple[int, int, str, str]] = []
        for kind, pattern in _SCAN_ORDER:
            for match in pattern.finditer(text):
                # For OTP, the capture group is the digits only.
                if kind == "otp":
                    start, end = match.span(1)
                    original = match.group(1)
                else:
                    start, end = match.span()
                    original = match.group(0)
                spans.append((start, end, kind, original))

        # Sort by start, drop overlaps (first writer wins).
        spans.sort(key=lambda row: (row[0], -row[1]))
        pruned: list[tuple[int, int, str, str]] = []
        last_end = -1
        for span in spans:
            start, end, _kind, _original = span
            if start < last_end:
                continue
            pruned.append(span)
            last_end = end

        # Rebuild the string from left to right.
        out_parts: list[str] = []
        cursor = 0
        for start, end, kind, original in pruned:
            out_parts.append(text[cursor:start])
            counters[kind] = counters.get(kind, 0) + 1
            token = f"<{kind.upper()}_{counters[kind]}>"
            token_map[token] = original
            out_parts.append(token)
            cursor = end
            hashed = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
            redactions.append(_Redaction(kind=kind, at=start, hashed=hashed))

        out_parts.append(text[cursor:])
        scrubbed = "".join(out_parts)

        for record in redactions:
            logger.info(
                "pii.redaction",
                extra={
                    "kind": record.kind,
                    "at": record.at,
                    "hashed": record.hashed,
                },
            )

        return scrubbed, token_map

    # ------------------------------------------------------------------ #
    # strict gate
    # ------------------------------------------------------------------ #

    def check_before_write(
        self,
        summary: str,
        key_claims: list[str],
        *,
        entities: list[str] | None = None,
        symbols: list[str] | None = None,
    ) -> bool:
        """Raise :class:`PIILeakError` if any PII pattern still matches.

        Returns ``True`` when the payload is clean so call sites can use
        the result as an assertion-style boolean too. ``entities`` and
        ``symbols`` are optional extra fields that, when present, are
        scanned too — a node with an email address dropped into
        ``entities`` was previously able to slip past the gate.
        """

        fragments = [summary, *key_claims, *(entities or []), *(symbols or [])]
        offenders: list[str] = []
        for fragment in fragments:
            for kind, pattern in _SCAN_ORDER:
                if pattern.search(fragment):
                    offenders.append(kind)
                    break

        if offenders:
            # De-dup while preserving detection order for the message.
            seen: set[str] = set()
            ordered: list[str] = []
            for kind in offenders:
                if kind in seen:
                    continue
                seen.add(kind)
                ordered.append(kind)
            raise PIILeakError(
                f"refusing to write: unredacted PII detected ({', '.join(ordered)})"
            )

        return True


__all__ = ["PIIScrubber"]
