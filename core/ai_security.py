from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# OWASP LLM Top 10 category labels (mirrors red_team_engine without circular import)
# ---------------------------------------------------------------------------
_OWASP_LLM01 = "LLM01"  # Prompt Injection
_OWASP_LLM02 = "LLM02"  # Insecure Output Handling
_OWASP_LLM06 = "LLM06"  # Sensitive Information Disclosure


@dataclass(frozen=True, slots=True)
class SecurityInspectionResult:
    is_safe: bool
    risk_level: str                               # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    detected_patterns: tuple[str, ...]
    sanitized_text: str
    pii_detected: bool = False
    owasp_categories: tuple[str, ...] = field(default_factory=tuple)  # e.g. ("LLM01", "LLM06")
    attack_confidence: float = 0.0                # 0.0-1.0 confidence that this is a real attack


class PromptInjectionDefense:
    """Detects and sanitizes prompt injection, jailbreak, indirect injection, and credential exfiltration attempts."""

    # Zero-width spaces, joiners, and bidirectional override characters often used for prompt smuggling
    UNICODE_SMUGGLING_PATTERN = re.compile(r"[\u200B-\u200D\uFEFF\u202A-\u202E\u2066-\u2069]")

    # Each tuple: (pattern, pattern_name, owasp_category)
    INJECTION_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
        (re.compile(r"(?i)ignore\s+(all\s+)?(?:previous\s+|prior\s+|above\s+)?instructions"), "system_prompt_override", _OWASP_LLM01),
        (re.compile(r"(?i)you\s+are\s+now\s+in\s+dan\s+mode"), "jailbreak_dan", _OWASP_LLM01),
        (re.compile(r"(?i)(print|output|reveal|show|dump)\s+(your\s+)?system\s+prompt"), "system_prompt_exfiltration", _OWASP_LLM06),
        (re.compile(r"(?i)bypass\s+(all\s+)?security(\s+checks)?"), "security_bypass_attempt", _OWASP_LLM01),
        # Indirect Prompt Injection markers
        (re.compile(r"(?i)(?:<<SYS>>|\[SYSTEM\]|\[INST\]|<\|system\|>|<\|im_start\|>)"), "indirect_system_delimiter_injection", _OWASP_LLM01),
        (re.compile(r"(?i)(?:developer\s+mode\s+output|sudo\s+su\s+mode|unrestricted\s+mode)"), "jailbreak_developer_mode", _OWASP_LLM01),
        # Markdown / Image Data Exfiltration attempts
        (re.compile(r"!\[[^\]]*\]\((?:https?:\/\/[^\)]*(?:leak|exfil|token|key|secret|steal)[^\)]*)\)"), "markdown_data_exfiltration", _OWASP_LLM02),
        (re.compile(r"\[[^\]]*\]\((?:https?:\/\/[^\)]*(?:exfil|leak|token|password|auth)[^\)]*)\)"), "markdown_link_exfiltration", _OWASP_LLM02),
        (re.compile(r"(?i)(?:IMPORTANT\s*:\s*)?(?:disregard|forget)\s+(?:all\s+)?(?:prior|previous)\s+context"), "context_reset_injection", _OWASP_LLM01),
        (re.compile(r"(?i)base64\s+decode\s+the\s+following\s+and\s+execute"), "encoded_payload_execution", _OWASP_LLM01),
        # Credential / sensitive disclosure probes — match natural language phrasing
        (re.compile(r"(?i)(api[\s_-]?keys?|credentials?|passwords?|tokens?).*?(you\s+have\s+access|available\s+to\s+you|have\s+access\s+to|do\s+you\s+have|list\s+(them|all)|list\s+all)"), "credential_probe", _OWASP_LLM06),
        (re.compile(r"(?i)list\s+(all\s+)?tools?\s+(available|you\s+have)"), "tool_enumeration_probe", _OWASP_LLM06),
    )

    def inspect(self, text: str, sanitize_pii: bool = True) -> SecurityInspectionResult:
        from .pii_sanitizer import PiiSanitizer

        detected: list[str] = []
        owasp_cats: set[str] = set()

        # 1. Check for Unicode smuggling
        if self.UNICODE_SMUGGLING_PATTERN.search(text):
            detected.append("unicode_smuggling_detected")
            owasp_cats.add(_OWASP_LLM01)

        # 2. Check for injection and exfiltration patterns
        for pattern, name, owasp_cat in self.INJECTION_PATTERNS:
            if pattern.search(text):
                detected.append(name)
                owasp_cats.add(owasp_cat)

        # 3. Sanitize text by stripping unicode smuggling chars and neutralizing injection triggers
        sanitized = self.UNICODE_SMUGGLING_PATTERN.sub("", text)
        for pattern, _, _owasp in self.INJECTION_PATTERNS:
            sanitized = pattern.sub("[REDACTED_PROMPT_INJECTION]", sanitized)

        # 4. Check and sanitize PII
        pii_detected = False
        if sanitize_pii:
            pii_res = PiiSanitizer.sanitize(sanitized)
            if pii_res.pii_detected:
                pii_detected = True
                sanitized = pii_res.sanitized_text
                for e in pii_res.entities_found:
                    detected.append(f"pii_{e.entity_type}")

        # 5. Compute risk level and attack confidence
        n = len(detected)
        is_safe = n == 0
        has_critical_patterns = any(
            p in detected for p in (
                "system_prompt_override", "jailbreak_dan", "system_prompt_exfiltration",
                "credential_probe", "jailbreak_developer_mode",
            )
        )
        if n == 0:
            risk_level = "LOW"
            attack_confidence = 0.0
        elif has_critical_patterns and n >= 2:
            risk_level = "CRITICAL"
            attack_confidence = min(1.0, 0.7 + n * 0.05)
        elif has_critical_patterns or n >= 3:
            risk_level = "HIGH"
            attack_confidence = min(0.9, 0.5 + n * 0.08)
        else:
            risk_level = "MEDIUM"
            attack_confidence = min(0.7, 0.2 + n * 0.1)

        return SecurityInspectionResult(
            is_safe=is_safe,
            risk_level=risk_level,
            detected_patterns=tuple(detected),
            sanitized_text=sanitized,
            pii_detected=pii_detected,
            owasp_categories=tuple(sorted(owasp_cats)),
            attack_confidence=attack_confidence,
        )
