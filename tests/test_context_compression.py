"""Unit test suite for Phase 12 — Context Compression & Dynamic Token Pruning."""

from __future__ import annotations

from core.agent_role import AgentRole
from core.context_compression import (
    DynamicHandoffPruner,
    HandoffTokenBudget,
    ModelContextTier,
    ProgressiveConversationSummarizer,
    SemanticDiffCompressor,
    TestOutputCompactor,
    estimate_tokens,
)


def test_token_budget_tiers() -> None:
    compact = HandoffTokenBudget.for_tier(ModelContextTier.COMPACT)
    standard = HandoffTokenBudget.for_tier(ModelContextTier.STANDARD)
    frontier = HandoffTokenBudget.for_tier(ModelContextTier.FRONTIER)

    assert compact.max_total_tokens == 4000
    assert standard.max_total_tokens == 8000
    assert frontier.max_total_tokens == 16000

    role_budget = HandoffTokenBudget.for_role(AgentRole.ARCHITECT)
    assert role_budget.max_total_tokens == 16000
    coder_budget = HandoffTokenBudget.for_role(AgentRole.BACKEND_CODER)
    assert coder_budget.max_total_tokens == 8000


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("a" * 400) == 100


def test_semantic_diff_compressor_small_diff_unchanged() -> None:
    diff = (
        "diff --git a/core/foo.py b/core/foo.py\n"
        "--- a/core/foo.py\n"
        "+++ b/core/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def foo():\n"
        "-    return 1\n"
        "+    return 2\n"
        "     pass\n"
    )
    compressed, did_comp = SemanticDiffCompressor.compress_diff(diff, max_tokens=1000)
    assert did_comp is False
    assert compressed == diff


def test_semantic_diff_compressor_large_diff_prunes_with_summary() -> None:
    # Build a large diff with 2 files
    file1 = (
        "diff --git a/core/primary.py b/core/primary.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/core/primary.py\n"
        "+++ b/core/primary.py\n"
        "@@ -1,10 +1,10 @@\n"
        + "".join(f" context line {i}\n" for i in range(10))
        + "-old_logic()\n+new_logic()\n"
        + "".join(f" context line {i}\n" for i in range(10))
    )
    file2 = (
        "diff --git a/core/secondary.py b/core/secondary.py\n"
        "index 3333333..4444444 100644\n"
        "--- a/core/secondary.py\n"
        "+++ b/core/secondary.py\n"
        "@@ -1,50 +1,50 @@\n"
        + "".join(f"+added_line_{i}\n" for i in range(100))
    )
    full_diff = file1 + "\n" + file2

    # Set strict budget prioritizing primary.py
    compressed, did_comp = SemanticDiffCompressor.compress_diff(
        full_diff,
        max_tokens=60,
        priority_files=["primary.py"],
    )
    assert did_comp is True
    # primary.py diff must be retained or summarized cleanly
    assert "primary.py" in compressed
    # secondary.py should be summarized
    assert "secondary.py" in compressed
    assert "[Diff omitted for" in compressed or "unchanged lines omitted" in compressed


def test_test_output_compactor_passed_suite() -> None:
    raw_pass = (
        "============================= test session starts ==============================\n"
        "platform darwin -- Python 3.13.11, pytest-9.1.1\n"
        "collected 50 items\n"
        "tests/test_one.py ............\n"
        "tests/test_two.py ..................................\n"
        "============================== 50 passed in 2.15s ==============================\n"
    )
    compacted, did_comp = TestOutputCompactor.compact_test_output(raw_pass, max_tokens=20)
    assert did_comp is True
    assert compacted == "[TEST SUITE PASSED: 50 passed in 2.15s]"


def test_test_output_compactor_failed_suite() -> None:
    raw_fail = (
        "============================= test session starts ==============================\n"
        "collected 10 items\n"
        "tests/test_error.py ..F.......\n"
        "=================================== FAILURES ===================================\n"
        "_________________________________ test_crash ___________________________________\n"
        "    def test_crash():\n"
        ">       assert 1 == 2\n"
        "E       AssertionError: assert 1 == 2\n"
        "/Users/user/.venv/lib/python3.13/site-packages/_pytest/runner.py:100: in run\n"
        "    some_noisy_internal_pytest_frame()\n"
        "tests/test_error.py:15: AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_error.py::test_crash - AssertionError: assert 1 == 2\n"
        "========================= 1 failed, 9 passed in 0.45s ==========================\n"
    )
    compacted, did_comp = TestOutputCompactor.compact_test_output(raw_fail, max_tokens=60)
    assert did_comp is True
    assert "FAILURES" in compacted
    assert "AssertionError: assert 1 == 2" in compacted
    # Must filter out site-packages frame
    assert "some_noisy_internal_pytest_frame" not in compacted


def test_progressive_conversation_summarizer() -> None:
    history = [
        {"role": "user", "content": "Fix the bug in memory consolidation"},
        {"role": "assistant", "content": "I will examine the AST cycles", "tool_calls": [{"name": "find_symbol"}]},
        {"role": "tool", "content": "Found symbol at line 42"},
        {"role": "assistant", "content": "Identified circular import between memory and consolidation"},
        {"role": "user", "content": "Please implement the fix immediately"},
        {"role": "assistant", "content": "Edited core/memory.py", "tool_calls": [{"name": "edit_file"}]},
        {"role": "tool", "content": "File edited successfully"},
    ]

    compacted, did_comp = ProgressiveConversationSummarizer.summarize_history(history, max_tokens=100)
    assert did_comp is True
    # First item must be progressive recap
    assert compacted[0]["role"] == "system"
    assert "[CONVERSATION PROGRESSIVE RECAP" in compacted[0]["content"]
    assert "User Goals:" in compacted[0]["content"]
    assert "Actions Executed:" in compacted[0]["content"]

    # Active turn (last 2 items) must be preserved verbatim
    assert compacted[-2] == history[-2]
    assert compacted[-1] == history[-1]


def test_dynamic_handoff_pruner_end_to_end() -> None:
    pruner = DynamicHandoffPruner()
    payload = {
        "task_id": "task-test-123",
        "diff": (
            "diff --git a/core/a.py b/core/a.py\n"
            + "".join(f"+line_number_{i:04d} = 'some_verbose_long_code_statement_{i}'\n" for i in range(500))
        ),
        "test_output": (
            "============================== 100 passed in 1.2s ==============================\n"
            "platform darwin -- Python 3.13.11\n"
            "rootdir: /Project/my-agent\n"
            "collected 100 items\n"
        ),
        "history": [
            {"role": "user", "content": f"msg {i}"} for i in range(20)
        ],
        "verbose_note": "A " * 4000,
    }

    # Use compact role to enforce compression across all fields
    pruned, report = pruner.prune_payload(payload, target_role=AgentRole.DOCUMENTATION)

    assert report.tokens_saved > 0
    assert report.diff_compressed is True
    assert report.tests_compacted is True
    assert report.history_summarized is True
    assert report.compression_ratio < 1.0
    assert "verbose_note" in report.truncated_keys
    assert "[... remaining" in pruned["verbose_note"]
