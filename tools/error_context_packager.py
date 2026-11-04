"""Surgical Error Context Packager for External LLM (Gemini Pro Web).

Extracts and packages targeted, minimal error context with strict zero-data-leakage
protections:
- Hard blacklist rejecting .env, *.key, credentials, tokens, secrets, databases.
- AST-level symbol extraction (function/class) and direct dependency signatures.
- SensitiveDataGate redaction (Shannon entropy, API keys, Bearer tokens, PII, DB URLs).
- Bounded line windows (zero full repository dumps).
- Ready for one-click copy-paste directly into Gemini Pro Web UI.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.context_disclosure import BridgeError, BridgeErrorCode, SensitiveDataGate


class ErrorContextPackager:
    """Surgical packager for minimal, safe error context."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.gate = SensitiveDataGate()

    def package_targeted_error(
        self,
        file_path: str,
        symbol_name: str | None = None,
        line_number: int | None = None,
        error_description: str = "",
        context_lines: int = 40,
    ) -> str:
        """Package minimal error context for a specific file, symbol, or line."""
        # 1. Strict path validation (fails closed on .env, secrets, keys, paths outside root)
        safe_rel_path = self._validate_and_resolve_path(file_path)
        full_path = self.workspace_root / safe_rel_path

        if not full_path.is_file():
            raise FileNotFoundError(f"Tệp không tồn tại: {safe_rel_path}")

        # 2. Read file content safely
        raw_content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = raw_content.splitlines()
        total_lines = len(lines)

        # 3. Extract snippet and AST symbols
        snippet = ""
        symbol_signature = ""
        related_calls: list[str] = []
        target_start = 1
        target_end = total_lines

        if symbol_name and safe_rel_path.endswith(".py"):
            ast_info = self._extract_python_symbol(raw_content, symbol_name)
            if ast_info:
                target_start = ast_info["start_line"]
                target_end = ast_info["end_line"]
                symbol_signature = ast_info.get("signature", "")
                related_calls = ast_info.get("called_functions", [])
                snippet = "\n".join(lines[target_start - 1 : target_end])
            else:
                # Fallback to window around line if symbol not found
                target_start, target_end, snippet = self._extract_line_window(
                    lines, line_number or 1, context_lines
                )
        elif line_number is not None:
            target_start, target_end, snippet = self._extract_line_window(
                lines, line_number, context_lines
            )
        else:
            target_start, target_end, snippet = self._extract_line_window(
                lines, 1, context_lines
            )

        # 4. Sanitize snippet and error description via SensitiveDataGate
        clean_snippet, _snippet_redactions = self.gate.sanitize(
            snippet, max_input_chars=24000
        )
        clean_error = ""
        if error_description:
            clean_error, _ = self.gate.sanitize(
                error_description.strip(), max_input_chars=12000
            )

        # 5. Format into clean, copy-pasteable Markdown
        doc_parts: list[str] = [
            "# YÊU CẦU SỬA LỖI (TARGETED ERROR CONTEXT)",
            "",
            "## 1. VỊ TRÍ VÀ MÔ TẢ VẤN ĐỀ",
            f"- **Tệp mục tiêu:** `{safe_rel_path}`",
            f"- **Phạm vi dòng:** {target_start} - {target_end} (Tổng số dòng của tệp: {total_lines})",
        ]

        if symbol_name:
            doc_parts.append(f"- **Hàm / Lớp mục tiêu:** `{symbol_name}`")
        if symbol_signature:
            doc_parts.append(f"- **Chữ ký hàm:** `{symbol_signature}`")
        if clean_error:
            doc_parts.extend(["", "- **Nội dung lỗi chi tiết:**", "```text", clean_error, "```"])

        doc_parts.extend([
            "",
            "## 2. ĐOẠN CODE CẦN SỬA (TARGET SNIPPET)",
            f"```{self._get_language_tag(safe_rel_path)}",
            clean_snippet,
            "```",
        ])

        if related_calls:
            doc_parts.extend([
                "",
                "## 3. CÁC HÀM PHỤ THUỘC ĐƯỢC GỌI TỪ ĐOẠN CODE (DEPENDENCY CALLS)",
            ])
            for call in related_calls[:8]:
                doc_parts.append(f"- `{call}`")

        doc_parts.extend([
            "",
            "## 4. CHỈ DẪN KỸ THUẬT CHO GEMINI PRO",
            "- Chỉ tập trung phân tích và cung cấp giải pháp sửa đoạn code trên.",
            "- Bảo toàn chữ ký hàm và logic kiểm thử hiện hữu.",
            "- Tuyệt đối không thay đổi biến môi trường hay kiến trúc ngoài phạm vi này.",
        ])

        return "\n".join(doc_parts)

    def package_last_test_failure(
        self,
        test_output: str | None = None,
        log_file: str | None = None,
        context_lines: int = 40,
    ) -> str:
        """Parse the most recent test failure and package its surgical context."""
        failure_info = self._detect_last_test_failure(test_output, log_file)
        if not failure_info:
            return (
                "# KHÔNG TÌM THẤY LỖI TEST GẦN NHẤT\n\n"
                "Không phát hiện bài test nào bị FAILED trong bộ đệm hoặc log."
            )

        file_path = failure_info.get("file_path", "")
        line_number = failure_info.get("line_number")
        test_name = failure_info.get("test_name", "")
        error_msg = failure_info.get("error_message", "")

        packaged = self.package_targeted_error(
            file_path=file_path,
            line_number=line_number,
            error_description=f"Test FAILED: {test_name}\n{error_msg}",
            context_lines=context_lines,
        )

        # Prepend test-specific metadata banner
        banner = [
            f"> **Phát hiện từ Test Runner:** `{test_name}`",
            f"> **Tệp kiểm thử / thực thi:** `{file_path}:{line_number or '?'}`",
            "",
        ]
        return "\n".join(banner) + packaged

    def _validate_and_resolve_path(self, file_path: str) -> str:
        """Validate path using SensitiveDataGate and ensure it stays inside workspace."""
        clean_path = self.gate.validate_path(file_path.strip().lstrip("/"))
        resolved = (self.workspace_root / clean_path).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
        return clean_path

    def _extract_line_window(
        self, lines: list[str], target_line: int, context_lines: int
    ) -> tuple[int, int, str]:
        """Extract a symmetric window of lines around target_line."""
        total = len(lines)
        if total == 0:
            return 1, 1, ""

        half = max(1, context_lines // 2)
        start_idx = max(0, target_line - 1 - half)
        end_idx = min(total, target_line - 1 + half)
        snippet = "\n".join(lines[start_idx:end_idx])
        return start_idx + 1, end_idx, snippet

    def _extract_python_symbol(
        self, source_code: str, symbol_name: str
    ) -> dict[str, Any] | None:
        """Parse Python AST to extract the exact function/class and called symbols."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == symbol_name
            ):
                start_line = node.lineno
                end_line = getattr(node, "end_lineno", node.lineno + 20)

                # Extract signature
                sig = node.name
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    arg_names = [a.arg for a in node.args.args]
                    sig = f"def {node.name}({', '.join(arg_names)})"

                # Find internal calls (dependencies)
                calls: list[str] = []
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name):
                            calls.append(child.func.id)
                        elif isinstance(child.func, ast.Attribute):
                            calls.append(child.func.attr)

                return {
                    "name": node.name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "signature": sig,
                    "called_functions": list(dict.fromkeys(calls)),  # deduplicate
                }
        return None

    def _detect_last_test_failure(
        self, test_output: str | None = None, log_file: str | None = None
    ) -> dict[str, Any] | None:
        """Detect last test failure from string output, log file, or pytest cache."""
        raw_text = test_output or ""
        if not raw_text and log_file:
            path = Path(log_file)
            if path.is_file():
                raw_text = path.read_text(encoding="utf-8", errors="replace")

        # Fallback to pytest cache if available
        if not raw_text:
            cache_file = self.workspace_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
            if cache_file.is_file():
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data:
                        first_failed = next(iter(data.keys()))
                        parts = first_failed.split("::")
                        file_part = parts[0]
                        test_fn = parts[1] if len(parts) > 1 else ""
                        return {
                            "file_path": file_part,
                            "line_number": None,
                            "test_name": test_fn or first_failed,
                            "error_message": "Failed in pytest lastfailed cache",
                        }
                except (json.JSONDecodeError, OSError, KeyError):
                    pass

        if not raw_text:
            return None

        # Parse standard pytest failure lines:
        # FAILED tests/test_foo.py::test_bar - AssertionError: ...
        for line in raw_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("FAILED "):
                parts = line_str[len("FAILED ") :].split(" - ")
                test_spec = parts[0].strip()
                err_msg = parts[1].strip() if len(parts) > 1 else ""
                file_and_test = test_spec.split("::")
                file_path = file_and_test[0]
                test_name = file_and_test[1] if len(file_and_test) > 1 else test_spec
                return {
                    "file_path": file_path,
                    "line_number": None,
                    "test_name": test_name,
                    "error_message": err_msg,
                }
        return None

    @staticmethod
    def _get_language_tag(path_str: str) -> str:
        ext = Path(path_str).suffix.lower()
        mapping = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".js": "javascript",
            ".jsx": "jsx",
            ".go": "go",
            ".rs": "rust",
            ".json": "json",
            ".md": "markdown",
            ".sql": "sql",
            ".sh": "bash",
            ".zsh": "bash",
        }
        return mapping.get(ext, "")


# Module-level convenience functions
_default_packager = ErrorContextPackager()


def package_targeted_error(
    file_path: str,
    symbol_name: str | None = None,
    line_number: int | None = None,
    error_description: str = "",
    context_lines: int = 40,
    workspace_root: Path | str | None = None,
) -> str:
    """Đóng gói ngữ cảnh lỗi mục tiêu cho một hàm, tệp hoặc dòng mã cụ thể."""
    packager = (
        ErrorContextPackager(workspace_root)
        if workspace_root
        else _default_packager
    )
    return packager.package_targeted_error(
        file_path=file_path,
        symbol_name=symbol_name,
        line_number=line_number,
        error_description=error_description,
        context_lines=context_lines,
    )


def package_last_test_failure(
    test_output: str | None = None,
    log_file: str | None = None,
    context_lines: int = 40,
    workspace_root: Path | str | None = None,
) -> str:
    """Tự động đóng gói ngữ cảnh lỗi từ bài kiểm thử bị FAILED gần nhất."""
    packager = (
        ErrorContextPackager(workspace_root)
        if workspace_root
        else _default_packager
    )
    return packager.package_last_test_failure(
        test_output=test_output,
        log_file=log_file,
        context_lines=context_lines,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Đóng gói ngữ cảnh lỗi phẫu thuật siêu sạch để gửi lên Gemini Pro Web."
    )
    parser.add_argument("--file", "-f", help="Đường dẫn tương đối tới tệp lỗi (vd: core/auth.py)")
    parser.add_argument("--symbol", "-s", help="Tên hàm hoặc lớp lỗi (vd: verify_token)")
    parser.add_argument("--line", "-l", type=int, help="Dòng lỗi cụ thể trong tệp")
    parser.add_argument("--error", "-e", default="", help="Mô tả lỗi hoặc stacktrace")
    parser.add_argument("--context-lines", "-n", type=int, default=40, help="Số dòng ngữ cảnh (mặc định 40)")
    parser.add_argument("--last-test", "-t", action="store_true", help="Tự động bắt lỗi từ lần chạy test gần nhất")
    parser.add_argument("--output", "-o", help="Đường dẫn tệp xuất markdown (mặc định in ra terminal)")
    parser.add_argument("--clip", "-c", action="store_true", help="Tự động sao chép vào clipboard (Mac pbcopy)")

    args = parser.parse_args()
    packager = ErrorContextPackager()

    try:
        if args.last_test:
            result = packager.package_last_test_failure(context_lines=args.context_lines)
        elif args.file:
            result = packager.package_targeted_error(
                file_path=args.file,
                symbol_name=args.symbol,
                line_number=args.line,
                error_description=args.error,
                context_lines=args.context_lines,
            )
        else:
            parser.print_help()
            sys.exit(1)

        if args.output:
            out_path = Path(args.output)
            out_path.write_text(result, encoding="utf-8")
            print(f"[OK] Đã xuất ngữ cảnh lỗi vào: {out_path} ({len(result)} ký tự)")
        else:
            print(result)

        if args.clip:
            try:
                subprocess.run(["pbcopy"], input=result.encode("utf-8"), check=True)
                print("\n[OK] Đã tự động sao chép vào Clipboard! Anh chỉ việc dán (Cmd+V) vào Gemini Web Pro.")
            except (subprocess.SubprocessError, OSError) as ex:
                print(f"[WARN] Không thể sao chép vào clipboard: {ex}")

    except BridgeError as be:
        print(f"[ERROR BẢO MẬT] Bị chặn bởi cổng an toàn SensitiveDataGate: {be.args[0]}")
        sys.exit(2)
    except (ValueError, OSError, RuntimeError, FileNotFoundError) as ex:
        print(f"[ERROR] {ex}")
        sys.exit(1)


if __name__ == "__main__":
    main()
