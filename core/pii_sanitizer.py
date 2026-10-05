from __future__ import annotations

import re
from .handoff_contracts import PiiEntity, PiiSanitizationResult


class PiiSanitizer:
    """Detects and redacts Personally Identifiable Information (PII) including credit cards,

    emails, phone numbers, and national IDs according to data privacy standards.
    """

    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    # US SSN format: 123-45-6789
    SSN_PATTERN = re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
    # VN Phone numbers (+84 or 0 followed by 9 digits) and common international phones
    VN_PHONE_PATTERN = re.compile(r"\b(?:\+?84|0)(?:3[2-9]|5[25689]|7[06-9]|8[1-9]|9[0-9])\d{7}\b")
    INTL_PHONE_PATTERN = re.compile(r"\b\+(?:[1-9]\d{0,2})[-.\s]?(?:\(\d{1,4}\)|\d{1,4})[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b")
    # Vietnam CCCD (12 digits, starting with 0)
    VN_CCCD_PATTERN = re.compile(r"\b0\d{11}\b")
    # Credit Card candidate (13-19 digits, optionally spaced or hyphenated)
    CREDIT_CARD_CANDIDATE = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b|\b\d{13,19}\b")

    @classmethod
    def _is_valid_luhn(cls, number_str: str) -> bool:
        """Validates credit card number candidates via Luhn Algorithm (Mod 10)."""
        digits = [int(d) for d in number_str if d.isdigit()]
        if len(digits) < 13 or len(digits) > 19:
            return False

        checksum = 0
        reverse_digits = digits[::-1]
        for i, d in enumerate(reverse_digits):
            if i % 2 == 1:
                doubled = d * 2
                checksum += doubled - 9 if doubled > 9 else doubled
            else:
                checksum += d
        return checksum % 10 == 0

    @classmethod
    def sanitize(cls, text: str, mask_only: bool = False) -> PiiSanitizationResult:
        if not text:
            return PiiSanitizationResult(original_length=0, sanitized_text="", pii_detected=False, summary="Empty text.")

        entities: list[PiiEntity] = []

        # 1. Credit Cards (with Luhn validation)
        for m in cls.CREDIT_CARD_CANDIDATE.finditer(text):
            matched = m.group()
            digits_only = re.sub(r"\D", "", matched)
            if cls._is_valid_luhn(digits_only):
                rep = f"XXXX-XXXX-XXXX-{digits_only[-4:]}" if mask_only else "[CREDIT_CARD_REDACTED]"
                entities.append(
                    PiiEntity(
                        entity_type="credit_card",
                        matched_text=matched,
                        start_index=m.start(),
                        end_index=m.end(),
                        replacement=rep,
                    )
                )

        # 2. Email Addresses
        for m in cls.EMAIL_PATTERN.finditer(text):
            matched = m.group()
            if mask_only:
                parts = matched.split("@")
                masked_user = parts[0][0] + "***" if len(parts[0]) > 1 else "***"
                rep = f"{masked_user}@{parts[1]}"
            else:
                rep = "[EMAIL_REDACTED]"
            entities.append(
                PiiEntity(
                    entity_type="email",
                    matched_text=matched,
                    start_index=m.start(),
                    end_index=m.end(),
                    replacement=rep,
                )
            )

        # 3. Phone Numbers
        for pat in (cls.VN_PHONE_PATTERN, cls.INTL_PHONE_PATTERN):
            for m in pat.finditer(text):
                matched = m.group()
                rep = f"***-***-{matched[-4:]}" if mask_only else "[PHONE_REDACTED]"
                entities.append(
                    PiiEntity(
                        entity_type="phone",
                        matched_text=matched,
                        start_index=m.start(),
                        end_index=m.end(),
                        replacement=rep,
                    )
                )

        # 4. SSN
        for m in cls.SSN_PATTERN.finditer(text):
            matched = m.group()
            rep = f"***-**-{matched[-4:]}" if mask_only else "[SSN_REDACTED]"
            entities.append(
                PiiEntity(
                    entity_type="ssn",
                    matched_text=matched,
                    start_index=m.start(),
                    end_index=m.end(),
                    replacement=rep,
                )
            )

        # 5. Vietnam CCCD (12 digits)
        for m in cls.VN_CCCD_PATTERN.finditer(text):
            matched = m.group()
            # Avoid re-matching if already matched as credit card
            if any(e.start_index <= m.start() and e.end_index >= m.end() for e in entities):
                continue
            rep = f"0********{matched[-3:]}" if mask_only else "[NATIONAL_ID_REDACTED]"
            entities.append(
                PiiEntity(
                    entity_type="national_id",
                    matched_text=matched,
                    start_index=m.start(),
                    end_index=m.end(),
                    replacement=rep,
                )
            )

        # De-duplicate overlapping entities (keep longer/earlier match)
        entities.sort(key=lambda e: (e.start_index, -(e.end_index - e.start_index)))
        clean_entities: list[PiiEntity] = []
        last_end = -1
        for e in entities:
            if e.start_index >= last_end:
                clean_entities.append(e)
                last_end = e.end_index

        # Apply replacements from right to left
        sanitized_chars = list(text)
        for e in sorted(clean_entities, key=lambda x: x.start_index, reverse=True):
            sanitized_chars[e.start_index : e.end_index] = list(e.replacement)

        sanitized_str = "".join(sanitized_chars)
        pii_detected = len(clean_entities) > 0

        summary = (
            f"PII detected and sanitized: {len(clean_entities)} entity(ies) redacted ({', '.join(set(e.entity_type for e in clean_entities))})."
            if pii_detected
            else "PII scan complete: No sensitive PII detected."
        )

        return PiiSanitizationResult(
            original_length=len(text),
            sanitized_text=sanitized_str,
            entities_found=tuple(clean_entities),
            pii_detected=pii_detected,
            summary=summary,
        )
