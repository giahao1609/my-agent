"""Phase 12 — Automated Context Pruning & Dynamic Token Compressor for Multi-Agent Handoffs.

Inspired by OpenAI Agents SDK, Claude-Mem, SWE-agent, and OpenHands.
Provides syntax-preserving, failure-focused, and token-bounded context compression
across:
1. Semantic Git Diffs (retaining hunk headers, trimming excess context lines)
2. Test Execution Output (stripping voluminous passing tests, isolating failure tracebacks)
3. Progressive Conversation History (summarizing turns 0..N-2 into executive recap, retaining turn N-1 verbatim)
4. Dynamic Token Budgeting & Pruning (allocating bounded tokens per target specialist role)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.agent_role import AgentRole


class ModelContextTier(StrEnum):
    """Model context tiers for token budget allocation."""

    COMPACT = "compact"      # Small/fast specialist model (e.g. flash-lite): ~4,000 tokens
    STANDARD = "standard"    # Standard specialist model (e.g. flash): ~8,000 tokens
    FRONTIER = "frontier"    # Frontier reasoning model (e.g. pro/sonnet): ~16,000 tokens


@dataclass(frozen=True, slots=True)
class HandoffTokenBudget:
    """Explicit token budget boundaries for specialist handoffs."""

    max_total_tokens: int = 8000
    truth_budget: int = 1500
    diff_budget: int = 2500
    test_budget: int = 1500
    history_budget: int = 1500
    notes_budget: int = 1000

    @classmethod
    def for_tier(cls, tier: ModelContextTier | str) -> HandoffTokenBudget:
        """Create token budget tailored to model context capacity."""
        raw = str(tier).lower()
        if "compact" in raw:
            return cls(
                max_total_tokens=4000,
                truth_budget=800,
                diff_budget=1200,
                test_budget=800,
                history_budget=800,
                notes_budget=400,
            )
        if "frontier" in raw or "pro" in raw:
            return cls(
                max_total_tokens=16000,
                truth_budget=2500,
                diff_budget=5500,
                test_budget=3000,
                history_budget=3000,
                notes_budget=2000,
            )
        return cls()  # Standard 8k budget

    @classmethod
    def for_role(cls, role: AgentRole | str) -> HandoffTokenBudget:
        """Resolve recommended token budget based on target agent role."""
        raw = role.value if isinstance(role, AgentRole) else str(role).lower()
        if "architect" in raw or "planner" in raw:
            return cls.for_tier(ModelContextTier.FRONTIER)
        if "coder" in raw or "review" in raw or "security" in raw:
            return cls.for_tier(ModelContextTier.STANDARD)
        return cls.for_tier(ModelContextTier.COMPACT)


def estimate_tokens(text: str) -> int:
    """Fast, deterministic token count approximation using 4 chars per token rule."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass(frozen=True, slots=True)
class HandoffCompressionReport:
    """Telemetry report documenting context compression results."""

    original_estimated_tokens: int
    compressed_estimated_tokens: int
    tokens_saved: int
    compression_ratio: float
    diff_compressed: bool
    tests_compacted: bool
    history_summarized: bool
    truncated_keys: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_estimated_tokens": self.original_estimated_tokens,
            "compressed_estimated_tokens": self.compressed_estimated_tokens,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": round(self.compression_ratio, 3),
            "diff_compressed": self.diff_compressed,
            "tests_compacted": self.tests_compacted,
            "history_summarized": self.history_summarized,
            "truncated_keys": list(self.truncated_keys),
        }


class SemanticDiffCompressor:
    """Compresses Git diffs without breaking file headers or hunk syntax."""

    @classmethod
    def compress_diff(
        cls,
        diff_text: str,
        max_tokens: int = 2500,
        priority_files: Sequence[str] | None = None,
    ) -> tuple[str, bool]:
        """Compresses unified git diff into budget while preserving syntax integrity."""
        if not diff_text or not diff_text.strip():
            return diff_text, False

        orig_tokens = estimate_tokens(diff_text)
        if orig_tokens <= max_tokens:
            return diff_text, False

        # Split into file diff blocks: diff --git a/... b/...
        file_blocks = re.split(r"(?=diff --git )", diff_text)
        if not file_blocks or len(file_blocks) == 1 and not file_blocks[0].startswith("diff --git"):
            # Fallback for raw unified diffs without git headers
            return cls._truncate_lines_cleanly(diff_text, max_tokens)

        parsed_files: list[dict[str, Any]] = []
        for block in file_blocks:
            if not block.strip():
                continue
            lines = block.splitlines()
            header_lines = [l for l in lines[:4] if l.startswith(("diff --git", "index ", "--- ", "+++ "))]
            file_name = ""
            for l in header_lines:
                if l.startswith("diff --git a/"):
                    parts = l.split(" ")
                    if len(parts) >= 3:
                        file_name = parts[2].removeprefix("a/")
                    break

            is_priority = False
            if priority_files and file_name:
                is_priority = any(p in file_name or file_name in p for p in priority_files)

            # Count additions and deletions
            adds = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
            dels = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
            hunks = [l for l in lines if l.startswith("@@ ")]

            parsed_files.append({
                "file_name": file_name,
                "block": block,
                "header": "\n".join(header_lines),
                "is_priority": is_priority,
                "adds": adds,
                "dels": dels,
                "hunk_count": len(hunks),
                "tokens": estimate_tokens(block),
            })

        # Sort: priority files first, then smaller diffs first
        parsed_files.sort(key=lambda x: (not x["is_priority"], x["tokens"]))

        retained_blocks: list[str] = []
        accumulated_tokens = 0

        for pf in parsed_files:
            file_block = pf["block"]
            # If the individual file diff is huge, compact its context lines first
            compacted_block = cls._compact_hunk_context_lines(file_block)
            block_tokens = estimate_tokens(compacted_block)

            if accumulated_tokens + block_tokens <= max_tokens:
                retained_blocks.append(compacted_block)
                accumulated_tokens += block_tokens
            else:
                # Omit full diff and add structured high-density summary
                summary = (
                    f"# [Diff omitted for {pf['file_name'] or 'file'}: "
                    f"+{pf['adds']} / -{pf['dels']} lines across {pf['hunk_count']} hunks]"
                )
                summary_tokens = estimate_tokens(summary)
                if accumulated_tokens + summary_tokens <= max_tokens:
                    retained_blocks.append(summary)
                    accumulated_tokens += summary_tokens

        result = "\n\n".join(retained_blocks)
        return result, True

    @classmethod
    def _compact_hunk_context_lines(cls, file_block: str) -> str:
        """Trims excessive unchanged context lines inside diff hunks to max 2 lines."""
        lines = file_block.splitlines()
        output_lines: list[str] = []
        in_hunk = False

        consecutive_ctx: list[str] = []

        def flush_ctx(is_trailing: bool = False) -> None:
            nonlocal consecutive_ctx
            if not consecutive_ctx:
                return
            if len(consecutive_ctx) <= 3:
                output_lines.extend(consecutive_ctx)
            else:
                if is_trailing:
                    output_lines.extend(consecutive_ctx[:2])
                    output_lines.append(f" ... [{len(consecutive_ctx) - 2} unchanged lines omitted]")
                else:
                    output_lines.append(f" ... [{len(consecutive_ctx) - 2} unchanged lines omitted]")
                    output_lines.extend(consecutive_ctx[-2:])
            consecutive_ctx = []

        for line in lines:
            if line.startswith("@@ "):
                flush_ctx(is_trailing=True)
                in_hunk = True
                output_lines.append(line)
            elif in_hunk:
                if line.startswith(" "):
                    consecutive_ctx.append(line)
                elif line.startswith(("+", "-")):
                    flush_ctx(is_trailing=False)
                    output_lines.append(line)
                else:
                    flush_ctx(is_trailing=True)
                    output_lines.append(line)
            else:
                output_lines.append(line)

        flush_ctx(is_trailing=True)
        return "\n".join(output_lines)

    @classmethod
    def _truncate_lines_cleanly(cls, text: str, max_tokens: int) -> tuple[str, bool]:
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text, False
        lines = text.splitlines()
        kept: list[str] = []
        cur_chars = 0
        for l in lines:
            if cur_chars + len(l) + 1 > max_chars:
                break
            kept.append(l)
            cur_chars += len(l) + 1
        kept.append(f"# [... remaining {len(lines) - len(kept)} diff lines omitted for token efficiency]")
        return "\n".join(kept), True


class TestOutputCompactor:
    """Compacts test suite outputs into high-density failure diagnostics."""

    FAIL_HEADER_PATTERN = re.compile(r"={3,}\s*FAILURES\s*={3,}", re.IGNORECASE)
    FAIL_ITEM_PATTERN = re.compile(r"_{3,}\s*(.+?)\s*_{3,}")
    PASS_SUMMARY_PATTERN = re.compile(r"(\d+ passed(?:,\s*\d+ [a-z]+)* in [0-9\.]+s)")

    @classmethod
    def compact_test_output(cls, output: str, max_tokens: int = 1500) -> tuple[str, bool]:
        """Strips passing test lines and isolates failure tracebacks."""
        if not output or not output.strip():
            return output, False

        orig_tokens = estimate_tokens(output)
        has_failure = any(
            marker in output
            for marker in ("FAILED ", "FAILURES", "AssertionError:", "Error:", "Traceback (most recent call last):")
        )

        if not has_failure:
            # 100% passing suite — if more than 2 lines or exceeds 50 tokens, compact to certificate summary
            if len(output.splitlines()) > 2 or orig_tokens > 50:
                match = cls.PASS_SUMMARY_PATTERN.search(output)
                if match:
                    summary = match.group(1)
                    return f"[TEST SUITE PASSED: {summary}]", True
                lines = [l.strip() for l in output.splitlines() if l.strip()]
                last_line = lines[-1] if lines else "Passed cleanly"
                return f"[TEST SUITE PASSED: {last_line}]", True
            return output, False

        if orig_tokens <= max_tokens:
            return output, False

        # Failed suite — isolate failures and strip noise
        lines = output.splitlines()
        failure_lines: list[str] = []
        collecting_failure = False
        skipping_subsequent_frame_line = False

        for line in lines:
            # Check for failure sections
            if cls.FAIL_HEADER_PATTERN.search(line):
                collecting_failure = True
                failure_lines.append(line)
                continue
            if line.startswith(("FAILED ", "ERROR ")):
                failure_lines.append(line)
                continue
            if collecting_failure:
                # Strip out noisy deep site-packages traceback lines and their code line
                if "/site-packages/" in line or "/_pytest/" in line or "/lib/python" in line:
                    skipping_subsequent_frame_line = True
                    continue
                if skipping_subsequent_frame_line:
                    if line.startswith("    ") and not line.startswith(("    >", "    E ")):
                        continue
                    skipping_subsequent_frame_line = False
                failure_lines.append(line)

        # Include short summary line from end of pytest run
        for line in reversed(lines[-10:]):
            if any(term in line for term in ("failed", "passed", "errors")):
                if line not in failure_lines:
                    failure_lines.append(line)
                break

        compacted = "\n".join(failure_lines) if failure_lines else output
        compacted_tokens = estimate_tokens(compacted)

        if compacted_tokens > max_tokens:
            # Cleanly truncate lines from the middle, retaining first failure + terminal summary
            clean_lines = compacted.splitlines()
            head = clean_lines[:25]
            tail = clean_lines[-15:]
            omitted = max(0, len(clean_lines) - len(head) - len(tail))
            compacted = "\n".join(head + [f"... [{omitted} secondary traceback lines omitted] ..."] + tail)

        return compacted, True


class ProgressiveConversationSummarizer:
    """Summarizes earlier conversation turns while retaining active turn verbatim."""

    @classmethod
    def summarize_history(
        cls,
        history: Sequence[dict[str, Any]],
        max_tokens: int = 1500,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Progressively compacts history into executive recap plus verbatim active turns."""
        if not history or len(history) <= 2:
            return list(history), False

        orig_tokens = estimate_tokens(str(history))
        if len(history) <= 15 and orig_tokens <= max_tokens:
            return list(history), False

        # Determine how many recent messages to keep (default up to 15)
        recent_count = min(15, len(history) - 1)
        while recent_count > 2 and estimate_tokens(str(history[-recent_count:])) > max_tokens * 0.75:
            recent_count -= 1

        active_turns = list(history[-recent_count:])
        historical_turns = history[:-recent_count]

        # Extract executive summary points from historical turns
        goals: list[str] = []
        actions: list[str] = []
        decisions: list[str] = []

        for idx, msg in enumerate(historical_turns, 1):
            role = msg.get("role", "unknown")
            content = str(msg.get("content") or "")

            if role == "user":
                clean_content = content.strip().splitlines()[0] if content.strip() else ""
                if clean_content and len(clean_content) < 120:
                    goals.append(clean_content)
            elif role == "assistant":
                # Look for tool calls or reasoning
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {}).get("name") or tc.get("name") or "tool"
                        actions.append(f"Turn {idx}: invoked `{fn}`")
                elif "decision" in content.lower():
                    decisions.append(f"Turn {idx}: {content[:100].strip()}...")
                else:
                    first_line = content.strip().splitlines()[0] if content.strip() else ""
                    if first_line:
                        actions.append(f"Turn {idx}: {first_line[:90]}...")
            elif role == "tool":
                status = "error" if "error" in content.lower() or "fail" in content.lower() else "success"
                actions.append(f"Turn {idx}: tool returned {status}")

        recap_lines = [
            f"[CONVERSATION PROGRESSIVE RECAP (Turns 1 to {len(historical_turns)} truncated for token efficiency)]",
        ]
        if goals:
            recap_lines.append(f"- User Goals: {'; '.join(goals[:3])}")
        if actions:
            # Keep up to 6 key actions
            recap_lines.append("- Actions Executed:")
            for act in actions[-6:]:
                recap_lines.append(f"  * {act}")
        if decisions:
            recap_lines.append("- Key Decisions:")
            for dec in decisions[-3:]:
                recap_lines.append(f"  * {dec}")

        recap_msg: dict[str, Any] = {
            "role": "system",
            "content": "\n".join(recap_lines),
        }

        compacted = [recap_msg] + active_turns
        return compacted, True


class DynamicHandoffPruner:
    """Comprehensive token-budget-driven pruner for multi-agent handoff payloads."""

    def __init__(self, budget: HandoffTokenBudget | None = None) -> None:
        self.budget = budget or HandoffTokenBudget()

    def prune_payload(
        self,
        payload: dict[str, Any],
        *,
        target_role: AgentRole | str | None = None,
        priority_files: Sequence[str] | None = None,
    ) -> tuple[dict[str, Any], HandoffCompressionReport]:
        """Compresses and prunes handoff payload into token budget without syntax damage."""
        if not payload:
            empty_report = HandoffCompressionReport(0, 0, 0, 1.0, False, False, False, ())
            return payload, empty_report

        effective_budget = self.budget
        if target_role is not None:
            effective_budget = HandoffTokenBudget.for_role(target_role)

        pruned = dict(payload)
        diff_compressed = False
        tests_compacted = False
        history_summarized = False
        truncated_keys: list[str] = []

        # 1. Compress Git Diffs if present
        for key in ("diff", "git_diff", "diffs", "patch"):
            if key in pruned and isinstance(pruned[key], str):
                c_diff, did_comp = SemanticDiffCompressor.compress_diff(
                    pruned[key],
                    max_tokens=effective_budget.diff_budget,
                    priority_files=priority_files,
                )
                if did_comp:
                    pruned[key] = c_diff
                    diff_compressed = True

        # 2. Compact Test Execution Outputs if present
        for key in ("test_output", "test_results", "test_stdout", "test_stderr"):
            if key in pruned and isinstance(pruned[key], str):
                c_test, did_comp = TestOutputCompactor.compact_test_output(
                    pruned[key],
                    max_tokens=effective_budget.test_budget,
                )
                if did_comp:
                    pruned[key] = c_test
                    tests_compacted = True

        # 3. Summarize Conversation History if present
        if "history" in pruned and isinstance(pruned["history"], list):
            c_hist, did_comp = ProgressiveConversationSummarizer.summarize_history(
                pruned["history"],
                max_tokens=effective_budget.history_budget,
            )
            if did_comp:
                pruned["history"] = c_hist
                history_summarized = True

        # 4. Bounded Sentence/Word Boundary String Truncation for remaining huge fields
        max_field_chars = effective_budget.notes_budget * 4
        for k, v in list(pruned.items()):
            if k in ("diff", "git_diff", "diffs", "patch", "test_output", "test_results", "history"):
                continue
            if isinstance(v, str) and len(v) > max_field_chars:
                # Truncate at whitespace boundary cleanly
                cut_idx = v.rfind(" ", 0, max_field_chars - 60)
                if cut_idx == -1:
                    cut_idx = max_field_chars - 60
                pruned[k] = (
                    v[:cut_idx]
                    + f"\n[... remaining {len(v) - cut_idx} characters truncated for token efficiency]\n...[truncated]"
                )
                truncated_keys.append(k)

        # Calculate telemetry tokens
        orig_tokens = estimate_tokens(str(payload))
        comp_tokens = estimate_tokens(str(pruned))
        tokens_saved = max(0, orig_tokens - comp_tokens)
        ratio = comp_tokens / orig_tokens if orig_tokens > 0 else 1.0

        report = HandoffCompressionReport(
            original_estimated_tokens=orig_tokens,
            compressed_estimated_tokens=comp_tokens,
            tokens_saved=tokens_saved,
            compression_ratio=ratio,
            diff_compressed=diff_compressed,
            tests_compacted=tests_compacted,
            history_summarized=history_summarized,
            truncated_keys=tuple(truncated_keys),
        )

        return pruned, report
