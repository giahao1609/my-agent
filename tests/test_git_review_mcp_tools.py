import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from my_agent_mcp.server import (
    _resolve_repo,
    draft_review_reply,
    fetch_pr_review_threads,
    post_batch_review_replies,
    post_pr_review_summary,
    post_review_reply,
    submit_pr_review,
)
from core.git_review_coordinator import ReviewThread, ReviewComment


@pytest.mark.asyncio
async def test_resolve_repo_explicit():
    owner, repo = await _resolve_repo("org/my-service")
    assert owner == "org"
    assert repo == "my-service"


@pytest.mark.asyncio
async def test_resolve_repo_from_git_detection():
    with patch("my_agent_mcp.server._git_review_coordinator.detect_repo_from_git") as mock_detect:
        mock_detect.return_value = ("detected-owner", "detected-repo")
        owner, repo = await _resolve_repo()
        assert owner == "detected-owner"
        assert repo == "detected-repo"


@pytest.mark.asyncio
async def test_draft_review_reply():
    res = await draft_review_reply(
        solution_summary="Added nil check before accessing field.",
        code_changes="+ if obj == nil { return nil }",
        commit_hash="deadbeef",
    )
    assert res["status"] == "ok"
    assert "Added nil check" in res["draft_reply"]
    assert "deadbeef" in res["draft_reply"]


@pytest.mark.asyncio
async def test_fetch_pr_review_threads_mcp():
    mock_thread = ReviewThread(
        thread_id=555,
        path="src/main.py",
        line=10,
        diff_hunk="@@ -8,3 +8,4 @@",
        comments=[
            ReviewComment(
                comment_id=555,
                author="lead_dev",
                body="Please fix this.",
                created_at="2026-09-07T00:00:00Z",
                html_url="https://github.com/org/repo/pull/1#discussion_r555",
            )
        ],
    )

    with patch("my_agent_mcp.server._resolve_repo", new_callable=AsyncMock) as mock_res_repo, \
         patch("my_agent_mcp.server._git_review_coordinator.get_pull_request", new_callable=AsyncMock) as mock_pr, \
         patch("my_agent_mcp.server._git_review_coordinator.fetch_pr_threads", new_callable=AsyncMock) as mock_threads, \
         patch("my_agent_mcp.server._git_review_coordinator.fetch_issue_comments", new_callable=AsyncMock) as mock_issues:

        mock_res_repo.return_value = ("org", "repo")
        mock_pr.return_value = {"title": "Feature PR", "state": "open", "user": {"login": "author"}}
        mock_threads.return_value = [mock_thread]
        mock_issues.return_value = []

        result = await fetch_pr_review_threads(pull_number=123)

        assert result["status"] == "ok"
        assert result["repo"] == "org/repo"
        assert result["pull_number"] == 123
        assert result["threads_count"] == 1
        assert result["review_threads"][0]["thread_id"] == 555


@pytest.mark.asyncio
async def test_post_review_reply_mcp():
    with patch("my_agent_mcp.server._resolve_repo", new_callable=AsyncMock) as mock_res_repo, \
         patch("my_agent_mcp.server._git_review_coordinator.post_thread_reply", new_callable=AsyncMock) as mock_post:

        mock_res_repo.return_value = ("org", "repo")
        mock_post.return_value = {"id": 999, "html_url": "https://github.com/org/repo/pull/1#discussion_r999"}

        res = await post_review_reply(
            pull_number=123,
            comment_id=555,
            reply_text="Refactored as requested.",
        )

        assert res["status"] == "ok"
        assert res["reply_id"] == 999
        mock_post.assert_called_once_with(
            owner="org",
            repo="repo",
            pull_number=123,
            comment_id=555,
            body="Refactored as requested.",
        )


@pytest.mark.asyncio
async def test_post_batch_review_replies_mcp():
    with patch("my_agent_mcp.server._resolve_repo", new_callable=AsyncMock) as mock_res_repo, \
         patch("my_agent_mcp.server._git_review_coordinator.post_batch_thread_replies", new_callable=AsyncMock) as mock_batch:

        mock_res_repo.return_value = ("org", "repo")
        mock_batch.return_value = {
            "status": "ok",
            "total_replies": 2,
            "success_count": 2,
            "failed_count": 0,
            "successful_replies": [
                {"status": "ok", "comment_id": 101, "reply_id": 1001},
                {"status": "ok", "comment_id": 102, "reply_id": 1002},
            ],
            "failed_replies": [],
            "summary_comment": {"status": "ok", "comment_id": 2001},
        }

        res = await post_batch_review_replies(
            pull_number=123,
            replies=[
                {"comment_id": 101, "body": "Fixed 1"},
                {"comment_id": 102, "body": "Fixed 2"},
            ],
            summary_text="Resolved all items.",
        )

        assert res["status"] == "ok"
        assert res["total_replies"] == 2
        assert res["success_count"] == 2
        assert len(res["successful_replies"]) == 2
        mock_batch.assert_called_once_with(
            owner="org",
            repo="repo",
            pull_number=123,
            replies=[
                {"comment_id": 101, "body": "Fixed 1"},
                {"comment_id": 102, "body": "Fixed 2"},
            ],
            summary_text="Resolved all items.",
        )


@pytest.mark.asyncio
async def test_submit_pr_review_mcp():
    with patch("my_agent_mcp.server._resolve_repo", new_callable=AsyncMock) as mock_res_repo, \
         patch("my_agent_mcp.server._git_review_coordinator.submit_pull_request_review", new_callable=AsyncMock) as mock_submit:

        mock_res_repo.return_value = ("org", "repo")
        mock_submit.return_value = {
            "id": 8888,
            "state": "APPROVED",
            "html_url": "https://github.com/org/repo/pull/123#pullrequestreview-8888",
        }

        res = await submit_pr_review(
            pull_number=123,
            body="Looks great, approving!",
            event="APPROVE",
            comments=[{"path": "foo.py", "line": 5, "body": "Nice optimization"}],
        )

        assert res["status"] == "ok"
        assert res["review_id"] == 8888
        assert res["state"] == "APPROVED"
        mock_submit.assert_called_once_with(
            owner="org",
            repo="repo",
            pull_number=123,
            body="Looks great, approving!",
            event="APPROVE",
            comments=[{"path": "foo.py", "line": 5, "body": "Nice optimization"}],
            commit_id=None,
        )

