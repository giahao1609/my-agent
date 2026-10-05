"""Git Review Coordinator for MyAgent.

Handles GitHub Pull Request review thread inspection, comment grouping,
context linking with local workspace files, and approval-gated reply posting.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReviewComment:
    comment_id: int
    author: str
    body: str
    created_at: str
    html_url: str
    in_reply_to_id: int | None = None


@dataclass
class ReviewThread:
    thread_id: int
    path: str
    line: int | None
    diff_hunk: str
    comments: list[ReviewComment] = field(default_factory=list)
    resolved: bool = False

    @property
    def latest_comment(self) -> ReviewComment | None:
        return self.comments[-1] if self.comments else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "path": self.path,
            "line": self.line,
            "diff_hunk": self.diff_hunk,
            "comment_count": len(self.comments),
            "comments": [
                {
                    "id": c.comment_id,
                    "author": c.author,
                    "body": c.body,
                    "created_at": c.created_at,
                    "html_url": c.html_url,
                    "in_reply_to_id": c.in_reply_to_id,
                }
                for c in self.comments
            ],
            "resolved": self.resolved,
        }


def parse_github_remote(remote_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from git remote URL (SSH or HTTPS)."""
    clean_url = remote_url.strip()
    # SSH pattern: git@github.com:owner/repo(.git)
    ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", clean_url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    # HTTPS pattern: https://(?:token@)?github.com/owner/repo(.git)
    https_match = re.match(r"^https?://(?:[^@]+@)?github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$", clean_url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    return None


class GitReviewCoordinator:
    """Coordinates PR review thread retrieval, contextualization, and reply posting."""

    def __init__(
        self,
        token: str | None = None,
        workspace_path: str | Path | None = None,
    ) -> None:
        self.token = token or self._resolve_token()
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()

    def _resolve_token(self) -> str | None:
        """Resolve token from environment variables."""
        return (
            os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
            or os.environ.get("GH_TOKEN")
        )

    def detect_repo_from_git(self, cwd: Path | None = None) -> tuple[str, str] | None:
        """Detect (owner, repo) by running git remote get-url origin in the given or workspace directory."""
        target_dir = cwd or self.workspace_path
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                check=True,
            )
            return parse_github_remote(result.stdout.strip())
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def _request_sync(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a synchronous GitHub API HTTP request."""
        if not self.token:
            raise ValueError(
                "GitHub token not found. Please set GITHUB_TOKEN or pass a valid token."
            )

        url = f"https://api.github.com{endpoint}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MyAgent-GitReviewCoordinator",
        }

        data_bytes = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                if resp.status == 204 or not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                err_msg = err_json.get("message", err_body)
            except Exception:
                err_msg = err_body
            raise RuntimeError(
                f"GitHub API error {exc.code} on {method} {endpoint}: {err_msg}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error accessing GitHub API: {exc.reason}") from exc

    async def _request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Asynchronously call GitHub API in a worker thread."""
        return await asyncio.to_thread(self._request_sync, endpoint, method, payload)

    async def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        """Fetch general pull request details."""
        return await self._request(f"/repos/{owner}/{repo}/pulls/{pull_number}")

    async def fetch_pr_threads(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> list[ReviewThread]:
        """Fetch all inline review comments and organize them into grouped threads."""
        raw_comments: list[dict[str, Any]] = await self._request(
            f"/repos/{owner}/{repo}/pulls/{pull_number}/comments?per_page=100"
        )

        threads_map: dict[int, ReviewThread] = {}
        child_comments: list[ReviewComment] = []

        for item in raw_comments:
            c_id = item["id"]
            author = item.get("user", {}).get("login", "unknown")
            body = item.get("body", "")
            created_at = item.get("created_at", "")
            html_url = item.get("html_url", "")
            in_reply_to = item.get("in_reply_to_id")
            path = item.get("path", "")
            line = item.get("line") or item.get("original_line")
            diff_hunk = item.get("diff_hunk", "")

            comment_obj = ReviewComment(
                comment_id=c_id,
                author=author,
                body=body,
                created_at=created_at,
                html_url=html_url,
                in_reply_to_id=in_reply_to,
            )

            if in_reply_to is None:
                threads_map[c_id] = ReviewThread(
                    thread_id=c_id,
                    path=path,
                    line=line,
                    diff_hunk=diff_hunk,
                    comments=[comment_obj],
                )
            else:
                child_comments.append(comment_obj)

        # Attach children to root thread
        for child in child_comments:
            root_id = child.in_reply_to_id
            if root_id in threads_map:
                threads_map[root_id].comments.append(child)
            else:
                # Fallback if root wasn't captured in first page
                found = False
                for thread in threads_map.values():
                    if any(c.comment_id == root_id for c in thread.comments):
                        thread.comments.append(child)
                        found = True
                        break
                if not found and root_id is not None:
                    # Create isolated thread
                    threads_map[root_id] = ReviewThread(
                        thread_id=root_id,
                        path="",
                        line=None,
                        diff_hunk="",
                        comments=[child],
                    )

        # Sort threads by file path and line
        result = list(threads_map.values())
        result.sort(key=lambda t: (t.path, t.line or 0, t.thread_id))
        return result

    async def fetch_issue_comments(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        """Fetch general conversation comments from the PR's issue timeline."""
        raw = await self._request(f"/repos/{owner}/{repo}/issues/{pull_number}/comments?per_page=100")
        return [
            {
                "id": item["id"],
                "author": item.get("user", {}).get("login", "unknown"),
                "body": item.get("body", ""),
                "created_at": item.get("created_at", ""),
                "html_url": item.get("html_url", ""),
            }
            for item in raw
        ]

    def format_thread_markdown(self, thread: ReviewThread, index: int | None = None) -> str:
        """Format a single review thread into a scannable, standardized markdown card."""
        latest = thread.latest_comment
        first = thread.comments[0] if thread.comments else latest
        header_index = f"Thread #{thread.thread_id}" if index is None else f"Thread #{thread.thread_id} ({index})"
        status_text = "RESOLVED" if thread.resolved else "UNRESOLVED"

        lines = [
            f"#### [{header_index}] `{thread.path}`:{thread.line or 'N/A'}",
            f"- **Author:** @{first.author if first else 'unknown'} | **Status:** {status_text} | **Comments:** {len(thread.comments)}",
        ]
        if thread.diff_hunk:
            lines.append("- **Diff Context:**")
            lines.append("```diff\n" + thread.diff_hunk.strip() + "\n```")

        lines.append("- **Review Comments:**")
        for c in thread.comments:
            lines.append(f"  - **@{c.author}** ({c.created_at[:10] if len(c.created_at) >= 10 else c.created_at}): {c.body.strip()}")

        return "\n".join(lines)

    def format_pr_overview_markdown(
        self,
        pr_info: dict[str, Any],
        threads: list[ReviewThread],
        owner: str,
        repo: str,
    ) -> str:
        """Format PR summary and all review threads into a standardized presentation."""
        unresolved = [t for t in threads if not t.resolved]
        title = pr_info.get("title", "Pull Request")
        pull_num = pr_info.get("number", "N/A")
        author = pr_info.get("user", {}).get("login", "unknown")
        state = pr_info.get("state", "open").upper()

        lines = [
            f"### PR #{pull_num}: {title}",
            f"- **Repository:** `{owner}/{repo}` | **Author:** @{author} | **State:** {state}",
            f"- **Total Threads:** {len(threads)} | **Unresolved:** {len(unresolved)}",
            "",
            "---",
        ]

        if not threads:
            lines.append("No review comments found on this Pull Request.")
            return "\n".join(lines)

        for i, thread in enumerate(threads, 1):
            lines.append(self.format_thread_markdown(thread, index=i))
            lines.append("\n---")

        return "\n".join(lines)

    def draft_reply(
        self,
        solution_summary: str,
        code_changes: str | None = None,
        commit_hash: str | None = None,
        next_steps: str | None = None,
    ) -> str:
        """Compose a structured, technical response following engineering review etiquette."""
        lines = [f"**Resolution:** {solution_summary.strip()}"]
        if code_changes:
            lines.append("\n**Code Changes:**")
            lines.append("```diff\n" + code_changes.strip() + "\n```")
        if commit_hash:
            lines.append(f"\nFixed in commit `{commit_hash}`.")
        if next_steps:
            lines.append(f"\n{next_steps.strip()}")
        return "\n".join(lines)

    async def post_thread_reply(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        comment_id: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a reply to an existing PR review comment thread."""
        if not body.strip():
            raise ValueError("Reply body cannot be empty.")
        endpoint = f"/repos/{owner}/{repo}/pulls/{pull_number}/comments/{comment_id}/replies"
        return await self._request(endpoint, method="POST", payload={"body": body.strip()})

    async def post_general_comment(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a general comment on the PR conversation."""
        if not body.strip():
            raise ValueError("Comment body cannot be empty.")
        endpoint = f"/repos/{owner}/{repo}/issues/{pull_number}/comments"
        return await self._request(endpoint, method="POST", payload={"body": body.strip()})

    async def post_batch_thread_replies(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        replies: list[dict[str, Any]],
        summary_text: str | None = None,
    ) -> dict[str, Any]:
        """Post multiple replies to PR review comment threads concurrently and optional summary."""
        if not replies and not summary_text:
            raise ValueError("At least one reply or a summary_text must be provided.")

        async def _send_single_reply(item: dict[str, Any]) -> dict[str, Any]:
            comment_id = item.get("comment_id")
            body = item.get("body") or item.get("reply_text") or ""
            if not comment_id:
                return {
                    "status": "error",
                    "comment_id": comment_id,
                    "error": "Missing 'comment_id' in reply item.",
                }
            if not isinstance(body, str) or not body.strip():
                return {
                    "status": "error",
                    "comment_id": comment_id,
                    "error": "Reply body cannot be empty.",
                }
            try:
                res = await self.post_thread_reply(
                    owner=owner,
                    repo=repo,
                    pull_number=pull_number,
                    comment_id=int(comment_id),
                    body=body.strip(),
                )
                return {
                    "status": "ok",
                    "comment_id": int(comment_id),
                    "reply_id": res.get("id"),
                    "html_url": res.get("html_url"),
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "comment_id": int(comment_id),
                    "error": str(exc),
                }

        results = await asyncio.gather(*[_send_single_reply(item) for item in replies]) if replies else []

        successful = [r for r in results if r["status"] == "ok"]
        failed = [r for r in results if r["status"] == "error"]

        summary_result = None
        if summary_text and summary_text.strip():
            try:
                summary_res = await self.post_general_comment(
                    owner=owner,
                    repo=repo,
                    pull_number=pull_number,
                    body=summary_text.strip(),
                )
                summary_result = {
                    "status": "ok",
                    "comment_id": summary_res.get("id"),
                    "html_url": summary_res.get("html_url"),
                }
            except Exception as exc:
                summary_result = {
                    "status": "error",
                    "error": str(exc),
                }

        status = "ok"
        if failed or (summary_result and summary_result.get("status") == "error"):
            status = "partial_success" if successful else "error"

        return {
            "status": status,
            "total_replies": len(replies),
            "success_count": len(successful),
            "failed_count": len(failed),
            "successful_replies": successful,
            "failed_replies": failed,
            "summary_comment": summary_result,
        }

    async def submit_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
        commit_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit a full pull request review with optional inline comments in a single atomic call."""
        valid_events = {"COMMENT", "APPROVE", "REQUEST_CHANGES"}
        upper_event = event.upper()
        if upper_event not in valid_events:
            raise ValueError(f"Invalid review event: {event}. Must be one of {valid_events}.")

        payload: dict[str, Any] = {
            "body": body.strip(),
            "event": upper_event,
        }
        if commit_id:
            payload["commit_id"] = commit_id.strip()
        if comments:
            payload["comments"] = comments

        endpoint = f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        return await self._request(endpoint, method="POST", payload=payload)

