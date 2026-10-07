"""Deterministic, local-only disclosure contracts and content gate."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from enum import IntEnum, StrEnum
from pathlib import PurePosixPath

from core.pii_sanitizer import PiiSanitizer
from core.security_scanner import SecretRedactor, calculate_shannon_entropy


class BridgeErrorCode(StrEnum):
    PROJECT_CONTEXT_REQUIRED = "PROJECT_CONTEXT_REQUIRED"
    PROJECT_SCOPE_DENIED = "PROJECT_SCOPE_DENIED"
    DISCLOSURE_DENIED = "DISCLOSURE_DENIED"
    CONTEXT_BUDGET_EXCEEDED = "CONTEXT_BUDGET_EXCEEDED"
    SENSITIVE_CONTEXT_DENIED = "SENSITIVE_CONTEXT_DENIED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXTERNAL_REASONING_INVALID = "EXTERNAL_REASONING_INVALID"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"


class BridgeError(ValueError):
    """Only a fixed code crosses the boundary, never a source/backend exception."""

    def __init__(self, code: BridgeErrorCode) -> None:
        self.code = BridgeErrorCode(code)
        super().__init__(self.code.value)

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code.value}


class DisclosureLevel(IntEnum):
    SAFE_METADATA = 0
    SUMMARY = 1
    SELECTED_EVIDENCE = 2
    EXPANDED_PROJECT_CONTEXT = 3
    PROJECT_WIDE_OR_SENSITIVE = 4


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_code_snippets: int = 6
    max_memory_matches: int = 5
    max_lines_per_snippet: int = 120
    max_graph_neighbors: int = 12
    max_graph_depth: int = 1
    max_git_diff_chars: int = 8000
    max_test_output_chars: int = 6000
    max_total_context_chars: int = 24000
    max_input_chars: int = 48000
    max_selection_items: int = 64

    def __post_init__(self) -> None:
        if any(type(getattr(self, f.name)) is not int or getattr(self, f.name) < 0
               for f in fields(self)):
            raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
        if self.max_total_context_chars < 1 or self.max_input_chars < 1:
            raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)


@dataclass(frozen=True, slots=True)
class DisclosurePolicy:
    budget: ContextBudget = ContextBudget()
    maximum_level: DisclosureLevel = DisclosureLevel.SELECTED_EVIDENCE
    expanded_context_justification: str = ""

    def validate_level(self, level: DisclosureLevel) -> None:
        if not isinstance(level, DisclosureLevel) or level > self.maximum_level:
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        if level == DisclosureLevel.PROJECT_WIDE_OR_SENSITIVE:
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        if level == DisclosureLevel.EXPANDED_PROJECT_CONTEXT and not (
            self.expanded_context_justification.strip()
        ):
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)


def serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class SensitiveDataGate:
    """Scan before truncating. Unknown high-risk material fails closed.

    This is a deterministic filter, not a claim that arbitrary encoded secrets
    can be recognized. Untrusted text never configures this gate or any tools.
    """

    # Safe HTTPS URL policy: Standard public HTTPS/HTTP URLs (e.g. package registries,
    # documentation, git remotes) are legitimate software metadata and must remain visible,
    # not misidentified as local filesystem paths. URLs containing user/password credentials
    # or signed/secret query parameters (e.g. token, sig, signature, X-Amz-Signature, X-Goog-Signature)
    # have sensitive credentials and signature values redacted.
    _url_credentials = re.compile(r"(?i)\b(https?://)(?:[^/\s@]+)@")
    _url_secret_params = re.compile(
        r"(?i)(?<=[?&])(token|sig|signature|x-amz-signature|x-amz-security-token|x-amz-credential|x-goog-signature|x-goog-credential|api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|secret|auth)\b=([^&\s'\"<>]+)"
    )
    _safe_url = re.compile(r"(?i)\bhttps?://[^\s'\"<>()]+(?<![.,:;?!])")
    _credentials = re.compile(
        r"(?im)\b(?:authorization|proxy-authorization|api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|auth[_-]?token|token|password|passwd|client[_-]?secret|"
        r"secret(?:[_-]?(?:key|access[_-]?key))?|aws[_-]?(?:access[_-]?key[_-]?id|"
        r"secret[_-]?access[_-]?key|session[_-]?token))\b[\"']?\s*[:=][^\r\n]*"
    )
    _bearer = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=:-]+")
    _paths = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|~/|/)[\w.~/\\:@%+-]+")
    _opaque = re.compile(r"(?<![\w])[A-Za-z0-9_+/=-]{32,}(?![\w])")

    @staticmethod
    def validate_path(value: str) -> str:
        if not isinstance(value, str) or not value or "\\" in value:
            raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
        path = PurePosixPath(value)
        if path.is_absolute() or any(p in {"..", ".", "~"} for p in value.split("/")):
            raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
            raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
        parts = [part.lower() for part in path.parts]
        if any(part.startswith(".env") or part in {
            ".git", ".ssh", ".aws", ".gnupg", ".config", ".venv",
            "credentials", "credential", "secrets", "id_rsa", "id_ed25519",
            "id_dsa", "id_ecdsa",
        } or re.search(r"(?:^|[_.-])(?:credentials?|secrets?|tokens?)(?:[_.-]|$)", part)
               for part in parts):
            raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}:
            raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
        return path.as_posix()

    def sanitize(self, value: str, *, max_input_chars: int) -> tuple[str, int]:
        if not isinstance(value, str):
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        if len(value) > max_input_chars:
            raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
        if re.search(r"-----BEGIN\b|-----END\b|\x00", value):
            raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
        result = value
        count = 0
        result, url_cred_matches = self._url_credentials.subn(r"\1[REDACTED_CREDENTIAL]@", result)
        count += url_cred_matches
        result, url_param_matches = self._url_secret_params.subn(r"\1=[REDACTED_SECRET]", result)
        count += url_param_matches

        # Stash safe URLs so normal https:// URLs are not mistaken for local filesystem paths
        # or mangled by line-level credential patterns.
        urls: list[str] = []

        def _stash_url(m: re.Match[str]) -> str:
            urls.append(m.group(0))
            return f"__URL_SAFE_TOKEN_{len(urls) - 1}__"

        temp = self._safe_url.sub(_stash_url, result)
        for pattern, replacement in SecretRedactor.PATTERNS:
            temp, matches = pattern.subn(replacement, temp)
            count += matches
        for pattern, replacement in (
            (self._credentials, "[REDACTED_CREDENTIAL]"),
            (self._bearer, "[REDACTED_AUTHORIZATION]"),
        ):
            temp, matches = pattern.subn(replacement, temp)
            count += matches
        temp, path_matches = self._paths.subn("[REDACTED_PATH]", temp)
        count += path_matches
        for idx, original_url in enumerate(urls):
            temp = temp.replace(f"__URL_SAFE_TOKEN_{idx}__", original_url)
        result = temp
        pii = PiiSanitizer.sanitize(result)
        result = pii.sanitized_text
        count += len(pii.entities_found)
        for match in self._opaque.finditer(result):
            candidate = match.group()
            if re.fullmatch(r"(?:[a-zA-Z0-9]+_)?[0-9a-fA-F]{32,}", candidate):
                continue
            if calculate_shannon_entropy(candidate) >= 4.5:
                raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
        return result, count


@dataclass(frozen=True, slots=True)
class ContextItem:
    category: str
    source_id: str
    content: str
    start_line: int | None = None
    end_line: int | None = None
    trust: str = "UNTRUSTED_DATA"


@dataclass(frozen=True, slots=True)
class DisclosedSource:
    category: str
    source_id: str
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class DisclosureManifest:
    request_id: str
    task_id: str
    project_id: str
    disclosure_level: DisclosureLevel
    source_categories: tuple[str, ...]
    number_of_items: int
    sources: tuple[DisclosedSource, ...]
    memory_match_count: int
    redaction_count: int
    omission_count: int
    budget_usage_chars: int


@dataclass(frozen=True, slots=True)
class DisclosureEnvelope:
    selected_context: tuple[ContextItem, ...]
    manifest: DisclosureManifest

    def to_dict(self) -> dict:
        return asdict(self)
