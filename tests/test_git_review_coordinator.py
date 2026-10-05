import pytest
from unittest.mock import AsyncMock, patch

from core.git_review_coordinator import (
    GitReviewCoordinator,
    ReviewComment,
    ReviewThread,
    parse_github_remote,
)


def test_parse_github_remote():
    assert parse_github_remote("git@github.com:haohg/platform.git") == ("haohg", "platform")
    assert parse_github_remote("git@github.com:haohg/platform") == ("haohg", "platform")
    assert parse_github_remote("https://github.com/haohg/my-agent.git") == ("haohg", "my-agent")
    assert parse_github_remote("https://github.com/haohg/my-agent") == ("haohg", "my-agent")
    assert parse_github_remote("https://x-token@github.com/org/repo.git") == ("org", "repo")
    assert parse_github_remote("https://gitlab.com/foo/bar.git") is None
    assert parse_github_remote("git@gitlab.com:foo/bar.git") is None


def test_draft_reply():
    coordinator = GitReviewCoordinator(token="test_token")

    reply1 = coordinator.draft_reply("Updated validation logic.")
    assert "Updated validation logic." in reply1

    reply2 = coordinator.draft_reply(
        "Refactored function to handle nil check.",
        code_changes="+ if err != nil { return err }",
        commit_hash="abc1234",
        next_steps="Please review again.",
    )
    assert "Refactored function" in reply2
    assert "```diff" in reply2
    assert "+ if err != nil { return err }" in reply2
    assert "Fixed in commit `abc1234`." in reply2
    assert "Please review again." in reply2


def test_format_thread_markdown():
    coordinator = GitReviewCoordinator(token="test_token")
    thread = ReviewThread(
        thread_id=101,
        path="services/auth.go",
        line=42,
        diff_hunk="@@ -40,5 +40,5 @@",
        comments=[
            ReviewComment(
                comment_id=101,
                author="reviewer1",
                body="Extract helper function.",
                created_at="2026-09-07T10:00:00Z",
                html_url="https://example.com",
            )
        ],
    )
    md = coordinator.format_thread_markdown(thread, index=1)
    assert "#### [Thread #101 (1)] `services/auth.go`:42" in md
    assert "@reviewer1" in md
    assert "UNRESOLVED" in md
    assert "Extract helper function." in md


def test_format_pr_overview_markdown():
    coordinator = GitReviewCoordinator(token="test_token")
    pr_info = {"title": "Fix Auth", "number": 42, "state": "open", "user": {"login": "dev1"}}
    thread = ReviewThread(
        thread_id=101,
        path="services/auth.go",
        line=42,
        diff_hunk="",
        comments=[
            ReviewComment(
                comment_id=101,
                author="reviewer1",
                body="Looks good but add test.",
                created_at="2026-09-07T10:00:00Z",
                html_url="https://example.com",
            )
        ],
    )
    overview = coordinator.format_pr_overview_markdown(pr_info, [thread], "org", "repo")
    assert "### PR #42: Fix Auth" in overview
    assert "`org/repo`" in overview
    assert "Total Threads:** 1" in overview
    assert "Unresolved:** 1" in overview


@pytest.mark.asyncio
async def test_fetch_pr_threads_grouping():
    coordinator = GitReviewCoordinator(token="test_token")

    mock_comments = [
        {
            "id": 101,
            "user": {"login": "reviewer1"},
            "body": "Can we extract this into a helper function?",
            "created_at": "2026-09-07T10:00:00Z",
            "html_url": "https://github.com/org/repo/pull/1#discussion_r101",
            "in_reply_to_id": None,
            "path": "services/auth.go",
            "line": 42,
            "diff_hunk": "@@ -40,5 +40,5 @@",
        },
        {
            "id": 102,
            "user": {"login": "author1"},
            "body": "Good point, I will refactor it.",
            "created_at": "2026-09-07T10:05:00Z",
            "html_url": "https://github.com/org/repo/pull/1#discussion_r102",
            "in_reply_to_id": 101,
            "path": "services/auth.go",
            "line": 42,
            "diff_hunk": "@@ -40,5 +40,5 @@",
        },
        {
            "id": 201,
            "user": {"login": "reviewer2"},
            "body": "Typo in variable name.",
            "created_at": "2026-09-07T10:02:00Z",
            "html_url": "https://github.com/org/repo/pull/1#discussion_r201",
            "in_reply_to_id": None,
            "path": "apps/web/main.ts",
            "line": 15,
            "diff_hunk": "@@ -10,6 +10,6 @@",
        },
    ]

    with patch.object(coordinator, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_comments

        threads = await coordinator.fetch_pr_threads("org", "repo", 1)

        assert len(threads) == 2
        # Verify sorting by path (apps/web/main.ts should come before services/auth.go)
        assert threads[0].path == "apps/web/main.ts"
        assert threads[0].thread_id == 201
        assert len(threads[0].comments) == 1

        assert threads[1].path == "services/auth.go"
        assert threads[1].thread_id == 101
        assert len(threads[1].comments) == 2
        assert threads[1].comments[0].author == "reviewer1"
        assert threads[1].comments[1].author == "author1"


@pytest.mark.asyncio
async def test_post_thread_reply():
    coordinator = GitReviewCoordinator(token="test_token")

    with patch.object(coordinator, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"id": 103, "body": "Fixed in commit 123"}

        res = await coordinator.post_thread_reply(
            owner="org",
            repo="repo",
            pull_number=1,
            comment_id=101,
            body="Fixed in commit 123",
        )

        mock_req.assert_called_once_with(
            "/repos/org/repo/pulls/1/comments/101/replies",
            method="POST",
            payload={"body": "Fixed in commit 123"},
        )
        assert res["id"] == 103


@pytest.mark.asyncio
async def test_empty_reply_raises_error():
    coordinator = GitReviewCoordinator(token="test_token")
    with pytest.raises(ValueError, match="cannot be empty"):
        await coordinator.post_thread_reply("org", "repo", 1, 101, "   ")


@pytest.mark.asyncio
async def test_post_batch_thread_replies_success():
    coordinator = GitReviewCoordinator(token="test_token")

    with patch.object(coordinator, "post_thread_reply", new_callable=AsyncMock) as mock_post_thread, \
         patch.object(coordinator, "post_general_comment", new_callable=AsyncMock) as mock_summary:
        mock_post_thread.side_effect = [
            {"id": 1001, "html_url": "https://github.com/org/repo/pull/1#discussion_r1001"},
            {"id": 1002, "html_url": "https://github.com/org/repo/pull/1#discussion_r1002"},
        ]
        mock_summary.return_value = {"id": 2001, "html_url": "https://github.com/org/repo/pull/1#issuecomment-2001"}

        res = await coordinator.post_batch_thread_replies(
            owner="org",
            repo="repo",
            pull_number=1,
            replies=[
                {"comment_id": 101, "body": "Fixed in commit A"},
                {"comment_id": 102, "body": "Fixed in commit B"},
            ],
            summary_text="All 2 comments resolved in commit abc1234.",
        )

        assert res["status"] == "ok"
        assert res["total_replies"] == 2
        assert res["success_count"] == 2
        assert res["failed_count"] == 0
        assert len(res["successful_replies"]) == 2
        assert res["successful_replies"][0]["reply_id"] == 1001
        assert res["summary_comment"]["comment_id"] == 2001
        assert mock_post_thread.call_count == 2
        mock_summary.assert_called_once_with(
            owner="org",
            repo="repo",
            pull_number=1,
            body="All 2 comments resolved in commit abc1234.",
        )


@pytest.mark.asyncio
async def test_post_batch_thread_replies_partial_failure():
    coordinator = GitReviewCoordinator(token="test_token")

    with patch.object(coordinator, "post_thread_reply", new_callable=AsyncMock) as mock_post_thread:
        mock_post_thread.side_effect = [
            {"id": 1001, "html_url": "https://github.com/org/repo/pull/1#discussion_r1001"},
            RuntimeError("GitHub API 404: Comment not found"),
        ]

        res = await coordinator.post_batch_thread_replies(
            owner="org",
            repo="repo",
            pull_number=1,
            replies=[
                {"comment_id": 101, "body": "Fixed in commit A"},
                {"comment_id": 999, "body": "Fixed in commit B"},
            ],
        )

        assert res["status"] == "partial_success"
        assert res["total_replies"] == 2
        assert res["success_count"] == 1
        assert res["failed_count"] == 1
        assert res["successful_replies"][0]["comment_id"] == 101
        assert res["failed_replies"][0]["comment_id"] == 999
        assert "Comment not found" in res["failed_replies"][0]["error"]


@pytest.mark.asyncio
async def test_post_batch_thread_replies_empty_raises():
    coordinator = GitReviewCoordinator(token="test_token")
    with pytest.raises(ValueError, match="At least one reply or a summary_text must be provided"):
        await coordinator.post_batch_thread_replies(
            owner="org",
            repo="repo",
            pull_number=1,
            replies=[],
            summary_text=None,
        )


@pytest.mark.asyncio
async def test_submit_pull_request_review():
    coordinator = GitReviewCoordinator(token="test_token")

    with patch.object(coordinator, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {
            "id": 5001,
            "state": "COMMENTED",
            "html_url": "https://github.com/org/repo/pull/1#pullrequestreview-5001",
        }

        res = await coordinator.submit_pull_request_review(
            owner="org",
            repo="repo",
            pull_number=1,
            body="Overall code looks good with 1 suggestion.",
            event="COMMENT",
            comments=[
                {
                    "path": "src/main.py",
                    "line": 42,
                    "body": "Consider using enum here.",
                }
            ],
            commit_id="deadbeef",
        )

        assert res["id"] == 5001
        mock_req.assert_called_once_with(
            "/repos/org/repo/pulls/1/reviews",
            method="POST",
            payload={
                "body": "Overall code looks good with 1 suggestion.",
                "event": "COMMENT",
                "commit_id": "deadbeef",
                "comments": [
                    {
                        "path": "src/main.py",
                        "line": 42,
                        "body": "Consider using enum here.",
                    }
                ],
            },
        )


@pytest.mark.asyncio
async def test_submit_pull_request_review_invalid_event():
    coordinator = GitReviewCoordinator(token="test_token")
    with pytest.raises(ValueError, match="Invalid review event: INVALID"):
        await coordinator.submit_pull_request_review(
            owner="org",
            repo="repo",
            pull_number=1,
            body="Review body",
            event="INVALID",
        )

