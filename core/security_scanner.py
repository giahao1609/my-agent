from __future__ import annotations

import math
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Sequence

from .handoff_contracts import SecurityFinding, SecurityReviewResult, SecuritySeverity


def calculate_shannon_entropy(data: str) -> float:
    """Calculates the Shannon entropy of a string."""
    if not data:
        return 0.0
    entropy = 0.0
    counter = Counter(data)
    length = len(data)
    for count in counter.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


class SecretRedactor:
    """Scrubs credentials, API tokens, private keys, and secrets from text."""

    PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*['\"]([^'\"]+)['\"]"), r"\1='[REDACTED_SECRET]'"),
        (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),
        (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "[REDACTED_OPENAI_KEY]"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
        (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "[REDACTED_GITHUB_TOKEN]"),
        (re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"), "[REDACTED_JWT_TOKEN]"),
        (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
        (re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}"), "[REDACTED_SLACK_TOKEN]"),
        (re.compile(r"(?:sk|rk)_(?:test|live)_[0-9a-zA-Z]{24,}"), "[REDACTED_STRIPE_KEY]"),
        (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "[REDACTED_GOOGLE_API_KEY]"),
        (re.compile(r"(?i)discord(?:app)?\.com\/api\/webhooks\/[0-9]+\/[a-zA-Z0-9_-]+"), "[REDACTED_DISCORD_WEBHOOK]"),
        (re.compile(r"sq0csp-[0-9A-Za-z_-]{43}"), "[REDACTED_SQUARE_SECRET]"),
        (re.compile(r"redis:\/\/(?:[^:]*:)?([^@]+)@"), "redis://[REDACTED_REDIS_PASSWORD]@"),
        (re.compile(r"(?:postgres|postgresql|mysql|mongodb(?:\+srv)?):\/\/[a-zA-Z0-9_.-]+:[^@\s]+@[a-zA-Z0-9_.-]+(?::[0-9]+)?\/[a-zA-Z0-9_.-]*"), "[REDACTED_DATABASE_URL]"),
        (re.compile(r"glpat-[0-9a-zA-Z_-]{20,}"), "[REDACTED_GITLAB_TOKEN]"),
        (re.compile(r"hf_[a-zA-Z0-9]{34,}"), "[REDACTED_HUGGINGFACE_TOKEN]"),
        (re.compile(r"sbp_[a-zA-Z0-9]{40,}"), "[REDACTED_SUPABASE_KEY]"),
    )

    @classmethod
    def redact(cls, text: str) -> str:
        result = text
        for pattern, replacement in cls.PATTERNS:
            result = pattern.sub(replacement, result)
        return result


class SecretDetectorAdapter:
    SECRET_PATTERNS = (
        (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "Anthropic API Key"),
        (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "OpenAI API Key"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
        (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "GitHub Personal Access Token"),
        (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "Private Key Block"),
        (re.compile(r'"type":\s*"service_account"'), "GCP Service Account Key"),
        (re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}"), "Slack API Token"),
        (re.compile(r"(?:sk|rk)_(?:test|live)_[0-9a-zA-Z]{24,}"), "Stripe API Key"),
        (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "Google API Key"),
        (re.compile(r"(?i)discord(?:app)?\.com\/api\/webhooks\/[0-9]+\/[a-zA-Z0-9_-]+"), "Discord Webhook URL"),
        (re.compile(r"sq0csp-[0-9A-Za-z_-]{43}"), "Square OAuth Secret"),
        (re.compile(r"redis:\/\/(?:[^:]*:)?([^@]+)@"), "Redis Connection Password"),
        (re.compile(r"(?:postgres|postgresql|mysql|mongodb(?:\+srv)?):\/\/[a-zA-Z0-9_.-]+:[^@\s]+@[a-zA-Z0-9_.-]+"), "Database Credentials URL"),
        (re.compile(r"(?i)(password|secret_key|private_key)\s*=\s*['\"][^'\"]{8,}['\"]"), "Hardcoded Secret/Password"),
        (re.compile(r"glpat-[0-9a-zA-Z_-]{20,}"), "GitLab Personal Access Token"),
        (re.compile(r"hf_[a-zA-Z0-9]{34,}"), "Hugging Face API Token"),
        (re.compile(r"sbp_[a-zA-Z0-9]{40,}"), "Supabase API Key / Secret"),
    )

    def scan_file(self, file_path: Path, relative_path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                # Check regex patterns
                for pattern, name in self.SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            SecurityFinding(
                                rule_id="SECRET-001",
                                severity=SecuritySeverity.CRITICAL,
                                file_path=relative_path,
                                line_number=line_idx,
                                description=f"Potential hardcoded secret detected ({name})",
                                remediation="Move secret into environment variables or secret manager",
                            )
                        )
                        break

                # Shannon entropy check for high-entropy tokens
                quote_matches = re.findall(r'["\']([a-zA-Z0-9_\-\.\/\+=]{30,})["\']', line)
                for candidate in quote_matches:
                    if not candidate.startswith("http") and calculate_shannon_entropy(candidate) > 4.5:
                        if not any(f.line_number == line_idx for f in findings):
                            findings.append(
                                SecurityFinding(
                                    rule_id="SECRET-002",
                                    severity=SecuritySeverity.HIGH,
                                    file_path=relative_path,
                                    line_number=line_idx,
                                    description="Potential high-entropy secret detected (Shannon entropy > 4.5)",
                                    remediation="Verify whether this token is an unencrypted credential and externalize it",
                                )
                            )
        except Exception:
            pass
        return findings


class SastScannerAdapter:
    SAST_PATTERNS = (
        (re.compile(r"\b(eval|exec)\s*\("), "SAST-001", SecuritySeverity.HIGH, "Use of unsafe eval/exec function (CWE-95)"),
        (re.compile(r"SELECT\s+.*\s+FROM\s+.*\s*\+\s*"), "SAST-002", SecuritySeverity.HIGH, "Potential SQL injection string concatenation (CWE-89)"),
        (re.compile(r"open\s*\(\s*.*(?:request|input|param).*"), "SAST-003", SecuritySeverity.MEDIUM, "Potential unvalidated path traversal in file open (CWE-22)"),
        (re.compile(r"\b(os\.system|subprocess\.(?:Popen|call|run|check_output|check_call))\s*\([^)]*shell\s*=\s*True"), "SAST-004", SecuritySeverity.HIGH, "Potential OS command injection with shell=True (CWE-78)"),
        (re.compile(r"\b(pickle\.(?:loads|load))\s*\("), "SAST-005", SecuritySeverity.HIGH, "Potential insecure deserialization via pickle (CWE-502)"),
        (re.compile(r"dangerouslySetInnerHTML\s*=\s*\{\s*\{\s*__html\s*:"), "SAST-006", SecuritySeverity.HIGH, "Direct HTML rendering without sanitization (CWE-79 XSS)"),
        (re.compile(r"yaml\.load\s*\([^)]*(?!Loader=yaml\.SafeLoader)[^)]*\)"), "SAST-007", SecuritySeverity.HIGH, "Unsafe YAML deserialization without SafeLoader (CWE-502)"),
        (re.compile(r"\bhashlib\.(?:md5|sha1)\s*\("), "SAST-008", SecuritySeverity.MEDIUM, "Use of cryptographically weak hash algorithm MD5/SHA1 (CWE-328)"),
        (re.compile(r"verify\s*=\s*False"), "SAST-009", SecuritySeverity.HIGH, "TLS certificate verification disabled (CWE-295)"),
        (re.compile(r"\b(?:requests\.(?:get|post|put|delete|patch)|urllib\.request\.urlopen|httpx\.(?:get|post)|aiohttp\.ClientSession\(\)\.(?:get|post))\s*\([^)]*(?:request\.(?:args|form|values|json)|user_input|param|query)"), "SAST-010", SecuritySeverity.HIGH, "Potential Server-Side Request Forgery (SSRF) via unvalidated user URL (CWE-918)"),
        (re.compile(r"(?i)(?:Access-Control-Allow-Origin['\"]\s*(?::|\]\s*=)\s*['\"]\s*\*|allow_origins\s*=\s*\[\s*['\"]\*\s*['\"])"), "SAST-011", SecuritySeverity.MEDIUM, "Overly permissive CORS wildcard configuration (CWE-942)"),
        (re.compile(r"\([^)]+[+*]\)[+*]|\([a-zA-Z0-9_.+*-]+\)\{\d+,\}[+*]"), "SAST-012", SecuritySeverity.MEDIUM, "Potential Regular Expression Denial of Service (ReDoS) with nested quantifiers (CWE-1333)"),
        (re.compile(r"\b(?:AES\.MODE_ECB|modes\.ECB\(\)|DES\.new\b)"), "SAST-013", SecuritySeverity.HIGH, "Insecure cipher mode or weak cipher algorithm (ECB mode / DES) (CWE-327)"),
        (re.compile(r"\bjwt\.encode\s*\([^)]*,\s*['\"][^'\"]{1,64}['\"]"), "SAST-014", SecuritySeverity.HIGH, "Hardcoded secret or key used in JWT signing (CWE-798)"),
        (re.compile(r"(?:__proto__|Object\.prototype|constructor\.prototype)\s*\["), "SAST-015", SecuritySeverity.HIGH, "Potential Prototype Pollution via dynamic property assignment (CWE-1321)"),
        (re.compile(r"\b(?:xml\.etree\.ElementTree|xml\.dom\.minidom|xml\.sax)\.(?:parse|fromstring)\s*\("), "SAST-016", SecuritySeverity.MEDIUM, "Insecure XML parsing vulnerable to XML External Entity (XXE) attacks (CWE-611)"),
        (re.compile(r"(?i)(?:iv|salt)\s*=\s*b?['\"][0-9a-zA-Z]{4,32}['\"]"), "SAST-017", SecuritySeverity.MEDIUM, "Hardcoded or static initialization vector (IV) / salt detected (CWE-329)"),
    )

    def scan_file(self, file_path: Path, relative_path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                for pattern, rule_id, severity, desc in self.SAST_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            SecurityFinding(
                                rule_id=rule_id,
                                severity=severity,
                                file_path=relative_path,
                                line_number=line_idx,
                                description=desc,
                                remediation="Refactor to use parameterized queries, safe parsers, or strict input validation",
                            )
                        )
        except Exception:
            pass
        return findings


def _parse_version(v_str: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in re.split(r"[.\-+]", v_str):
        if part.isdigit():
            parts.append(int(part))
        else:
            m = re.match(r"\d+", part)
            if m:
                parts.append(int(m.group(0)))
            break
    return tuple(parts) if parts else (0,)


def _is_vulnerable(detected_v: str, fixed_v: str) -> bool:
    d = _parse_version(detected_v)
    f = _parse_version(fixed_v)
    max_len = max(len(d), len(f))
    d_pad = d + (0,) * (max_len - len(d))
    f_pad = f + (0,) * (max_len - len(f))
    return d_pad < f_pad


class DependencyScaScannerAdapter:
    """Scans dependency manifests (requirements.txt, package.json, pyproject.toml) for known vulnerable packages."""

    # (package_name, fixed_version, cve_id, description)
    KNOWN_VULNERABILITIES = (
        ("requests", "2.31.0", "CVE-2023-32681", "Leaked Proxy-Authorization header on redirect"),
        ("urllib3", "2.0.7", "CVE-2023-45803", "Cookie leak on cross-site redirect in urllib3"),
        ("flask", "2.2.5", "CVE-2023-30861", "Cookie security issue in Flask"),
        ("django", "4.2.8", "CVE-2023-46695", "Potential DoS vulnerability in Django"),
        ("lodash", "4.17.21", "CVE-2021-23337", "Command injection in lodash template"),
        ("axios", "1.6.0", "CVE-2023-45857", "XSRF-TOKEN leakage in Axios cross-origin requests"),
        ("jsonwebtoken", "9.0.0", "CVE-2022-23529", "Insecure key verification in jsonwebtoken"),
    )

    def scan_file(self, file_path: Path, relative_path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        name_lower = file_path.name.lower()
        if not (
            name_lower.endswith(".txt")
            or name_lower.endswith(".json")
            or name_lower.endswith(".toml")
        ):
            return findings

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                # 1. requirements.txt style: package==1.2.3 or package<=1.2.3
                m_req = re.match(r"^\s*([a-zA-Z0-9_\-]+)\s*(?:==|<=|~=)\s*([0-9a-zA-Z_.\-]+)", line)
                if m_req:
                    pkg, ver = m_req.group(1).lower(), m_req.group(2)
                    for v_pkg, fixed_v, cve, desc in self.KNOWN_VULNERABILITIES:
                        if pkg == v_pkg and _is_vulnerable(ver, fixed_v):
                            findings.append(
                                SecurityFinding(
                                    rule_id="SCA-001",
                                    severity=SecuritySeverity.HIGH,
                                    file_path=relative_path,
                                    line_number=line_idx,
                                    description=f"Vulnerable dependency: {pkg}=={ver} is affected by {cve} ({desc})",
                                    remediation=f"Upgrade {pkg} to >={fixed_v}",
                                )
                            )

                # 2. package.json or pyproject.toml style: "package": "^1.2.3" or package = "^1.2.3"
                m_json = re.search(r'["\']?([a-zA-Z0-9_\-@\/]+)["\']?\s*(?::|=)\s*["\'][\^~><=]*([0-9a-zA-Z_.\-]+)["\']', line)
                if m_json:
                    pkg_raw, ver = m_json.group(1).lower().split("/")[-1], m_json.group(2)
                    for v_pkg, fixed_v, cve, desc in self.KNOWN_VULNERABILITIES:
                        if pkg_raw == v_pkg and _is_vulnerable(ver, fixed_v):
                            findings.append(
                                SecurityFinding(
                                    rule_id="SCA-001",
                                    severity=SecuritySeverity.HIGH,
                                    file_path=relative_path,
                                    line_number=line_idx,
                                    description=f"Vulnerable dependency: {pkg_raw}@{ver} is affected by {cve} ({desc})",
                                    remediation=f"Upgrade {pkg_raw} to >={fixed_v}",
                                )
                            )
        except Exception:
            pass
        return findings


class SecurityScannerRegistry:
    def __init__(self) -> None:
        self.secret_detector = SecretDetectorAdapter()
        self.sast_scanner = SastScannerAdapter()
        self.sca_scanner = DependencyScaScannerAdapter()
        # Check availability of optional external scanning tools
        self._semgrep_available: bool = shutil.which("semgrep") is not None
        self._gitleaks_available: bool = shutil.which("gitleaks") is not None

    def scan_workspace(
        self,
        workspace_path: Path | str,
        step_id: str,
        target_files: Sequence[str] | None = None,
        include_tool_warnings: bool = False,
    ) -> SecurityReviewResult:
        root = Path(workspace_path)
        findings: list[SecurityFinding] = []

        # Emit WARNING findings when external scanners are unavailable so the
        # control plane knows that security coverage is degraded (if requested).
        if include_tool_warnings:
            if not self._semgrep_available:
                findings.append(SecurityFinding(
                    rule_id="SCANNER-WARN-001",
                    severity=SecuritySeverity.MEDIUM,
                    file_path="<scanner>",
                    line_number=0,
                    description="semgrep is not installed — SAST coverage degraded to regex-only patterns",
                    remediation="Install semgrep: pip install semgrep",
                ))
            if not self._gitleaks_available:
                findings.append(SecurityFinding(
                    rule_id="SCANNER-WARN-002",
                    severity=SecuritySeverity.MEDIUM,
                    file_path="<scanner>",
                    line_number=0,
                    description="gitleaks is not installed — secret scanning degraded to regex-only patterns",
                    remediation="Install gitleaks: https://github.com/gitleaks/gitleaks",
                ))

        files_to_scan: list[Path] = []
        if target_files:
            for rel in target_files:
                full_p = root / rel
                if full_p.is_file():
                    files_to_scan.append(full_p)
        else:
            # Default scan python/js/go files, manifests, and configs, ignoring .venv and node_modules
            for p in root.rglob("*"):
                if p.is_file() and not any(part in p.parts for part in (".venv", "node_modules", ".git", "__pycache__")):
                    if p.suffix in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".json", ".env", ".txt", ".toml"):
                        files_to_scan.append(p)

        for file_p in files_to_scan:
            try:
                rel_path = str(file_p.relative_to(root))
            except ValueError:
                rel_path = str(file_p)

            sec_findings = self.secret_detector.scan_file(file_p, rel_path)
            sast_findings = self.sast_scanner.scan_file(file_p, rel_path)
            sca_findings = self.sca_scanner.scan_file(file_p, rel_path)

            findings.extend(sec_findings)
            findings.extend(sast_findings)
            findings.extend(sca_findings)

        # Redact any findings text
        clean_findings = [
            SecurityFinding(
                rule_id=f.rule_id,
                severity=f.severity,
                file_path=f.file_path,
                line_number=f.line_number,
                description=SecretRedactor.redact(f.description),
                remediation=SecretRedactor.redact(f.remediation),
            )
            for f in findings
        ]

        has_critical_or_high = any(f.severity in (SecuritySeverity.CRITICAL, SecuritySeverity.HIGH) for f in clean_findings)
        passed_gate = not has_critical_or_high

        summary = (
            f"Security scan completed: {len(clean_findings)} findings."
            if passed_gate
            else f"Security gate FAILED: {len(clean_findings)} findings (critical/high severity issues present)."
        )

        return SecurityReviewResult(
            step_id=step_id,
            passed_gate=passed_gate,
            summary=summary,
            findings=tuple(clean_findings),
        )


default_security_scanner = SecurityScannerRegistry()
