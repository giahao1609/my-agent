"""Phase 06B — Unified Context Assembly Engine (ContextBuilder).

Assembles rich, structured, bounded, and sanitized context across:
1. CodeGraph AST symbols, dependencies, and dependents.
2. Durable Memory (L2 project conventions & L3 persistent architectural knowledge).
3. UserModel active preferences (Precedence Hierarchy Rank 1-5).
4. UI Design System Contracts (for UI_CODER and USER_INTERFACE roles).
5. Token Budget constraint with deterministic multi-tier pruning and secret redaction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from core.agent_role import AgentRole
from core.code_graph import CodeGraphBackend, CodeNode, CodeNodeKind
from core.durable_memory_service import DurableMemoryService
from core.memory import MemoryLevel, MemoryStatus, MemoryType
from core.pii_sanitizer import PiiSanitizer
from core.security_scanner import SecretRedactor
from core.user_model import (
    EffectiveUserProfile,
    PreferenceItem,
    calculate_precedence_rank,
)
from core.user_model_engine import DEFAULT_USER_ID, UserModelEngine

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
DEFAULT_TOKEN_BUDGET = 4000
MIN_TOKEN_BUDGET = 256

# Regex for detecting source file paths in instructions
FILE_PATH_PATTERN = re.compile(
    r"\b(?:[a-zA-Z0-9_\-\./]+)\.(?:py|ts|tsx|js|jsx|go|rs|sql|css|html|json|yaml|yml|md)\b"
)

# Regex for detecting symbol-like words (CamelCase or snake_case with 4+ chars)
SYMBOL_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[A-Z][a-zA-Z0-9]{3,}|[a-z]{2,}(?:_[a-z0-9]+)+)\b"
)


@dataclass(frozen=True, slots=True)
class CodeSymbolContext:
    """AST symbol context extracted from CodeGraph."""

    node_id: str
    name: str
    kind: str
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    dependents: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "dependencies": list(self.dependencies),
            "dependents": list(self.dependents),
        }


@dataclass(frozen=True, slots=True)
class MemoryContextItem:
    """Durable memory context item with provenance and relevance facts."""

    memory_id: str
    subject: str
    content: str
    tier: str
    importance: float
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "subject": self.subject,
            "content": self.content,
            "tier": self.tier,
            "importance": self.importance,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class UserPreferenceContextItem:
    """Active user preference context item resolved by UserModelEngine."""

    logical_key: str
    content: str
    category: str
    scope: str
    precedence_rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_key": self.logical_key,
            "content": self.content,
            "category": self.category,
            "scope": self.scope,
            "precedence_rank": self.precedence_rank,
        }


@dataclass(frozen=True, slots=True)
class UnifiedContext:
    """Aggregated, bounded, sanitized, and formatted context bundle."""

    project_id: str
    task_id: str | None
    step_id: str | None
    target_role: AgentRole
    user_preferences: tuple[UserPreferenceContextItem, ...] = field(default_factory=tuple)
    memories: tuple[MemoryContextItem, ...] = field(default_factory=tuple)
    code_symbols: tuple[CodeSymbolContext, ...] = field(default_factory=tuple)
    design_contract: str | None = None
    formatted_prompt_section: str = ""
    token_count_estimate: int = 0
    pruned_items_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "target_role": (
                self.target_role.value
                if isinstance(self.target_role, AgentRole)
                else str(self.target_role)
            ),
            "user_preferences": [p.to_dict() for p in self.user_preferences],
            "memories": [m.to_dict() for m in self.memories],
            "code_symbols": [s.to_dict() for s in self.code_symbols],
            "design_contract": self.design_contract,
            "formatted_prompt_section": self.formatted_prompt_section,
            "token_count_estimate": self.token_count_estimate,
            "pruned_items_count": self.pruned_items_count,
        }


class UnifiedContextBuilder:
    """Unified Context Assembly Engine integrating CodeGraph, Memory, and UserModel."""

    def __init__(
        self,
        *,
        code_graph: CodeGraphBackend | None = None,
        memory_service: DurableMemoryService | None = None,
        user_model_engine: UserModelEngine | None = None,
        design_dir: Path | str | None = None,
        secret_redactor: SecretRedactor | None = None,
        pii_sanitizer: PiiSanitizer | None = None,
        default_token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> None:
        self._code_graph = code_graph
        self._memory_service = memory_service
        self._user_model_engine = user_model_engine
        self._design_dir = Path(design_dir) if design_dir else Path("data/designs")
        self._redactor = secret_redactor or SecretRedactor()
        self._sanitizer = pii_sanitizer or PiiSanitizer()
        self._default_token_budget = max(MIN_TOKEN_BUDGET, default_token_budget)

    async def build_context(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        step_id: str | None = None,
        step_title: str = "",
        step_instruction: str = "",
        target_role: AgentRole = AgentRole.BACKEND_CODER,
        target_files: Sequence[str] | None = None,
        user_id: str | None = None,
        token_budget: int | None = None,
    ) -> UnifiedContext:
        """Assembles a bounded, prioritized, and sanitized context payload."""
        if not project_id or not project_id.strip():
            raise ValueError("project_id is mandatory for context assembly")
        if project_id == "__global__":
            raise ValueError("RESERVED_SENTINEL: project_id='__global__' is forbidden.")

        budget = max(MIN_TOKEN_BUDGET, token_budget or self._default_token_budget)
        eff_user_id = user_id or DEFAULT_USER_ID

        # 1. Retrieve UserModel preferences
        user_prefs = await self._retrieve_user_preferences(
            user_id=eff_user_id,
            project_id=project_id,
        )

        # 2. Retrieve Durable Memories (L2 Project + L3 Global)
        memories = await self._retrieve_memories(
            project_id=project_id,
            step_title=step_title,
            step_instruction=step_instruction,
            target_files=target_files,
        )

        # 3. Retrieve CodeGraph AST symbols
        code_symbols = await self._retrieve_code_symbols(
            project_id=project_id,
            step_title=step_title,
            step_instruction=step_instruction,
            target_files=target_files,
        )

        # 4. Retrieve UI Design Contract if target role is UI-focused
        design_contract: str | None = None
        if target_role in (AgentRole.UI_CODER, AgentRole.USER_INTERFACE):
            design_contract = self._retrieve_design_contract(project_id)

        # 5. Deterministic Token Budget Pruning & Assembly
        return self._assemble_and_prune(
            project_id=project_id,
            task_id=task_id,
            step_id=step_id,
            target_role=target_role,
            user_prefs=user_prefs,
            memories=memories,
            code_symbols=code_symbols,
            design_contract=design_contract,
            token_budget=budget,
            step_title=step_title,
            step_instruction=step_instruction,
        )

    async def _retrieve_user_preferences(
        self,
        *,
        user_id: str,
        project_id: str,
    ) -> list[UserPreferenceContextItem]:
        if self._user_model_engine is None:
            return []

        try:
            profile: EffectiveUserProfile = (
                await self._user_model_engine.get_effective_profile(
                    user_id=user_id,
                    project_id=project_id,
                )
            )
        except Exception as exc:
            logger.warning("Failed to retrieve user preferences: %s", exc)
            return []

        items: list[UserPreferenceContextItem] = []
        for key, pref in profile.preferences_by_key.items():
            # Sanitize content
            safe_content = self._sanitize(pref.content)
            rank = getattr(pref, "precedence_rank", None)
            if rank is None:
                rank = calculate_precedence_rank(pref)
            items.append(
                UserPreferenceContextItem(
                    logical_key=key,
                    content=safe_content,
                    category=pref.category.value if hasattr(pref.category, "value") else str(pref.category),
                    scope="project" if pref.project_id else "global",
                    precedence_rank=rank,
                )
            )

        # Sort by precedence rank descending (Rank 5 down to 1)
        items.sort(key=lambda x: x.precedence_rank, reverse=True)
        return items

    async def _retrieve_memories(
        self,
        *,
        project_id: str,
        step_title: str,
        step_instruction: str,
        target_files: Sequence[str] | None,
    ) -> list[MemoryContextItem]:
        if self._memory_service is None:
            return []

        # Formulate query from keywords
        query_parts: list[str] = []
        if step_title:
            query_parts.append(step_title)
        if target_files:
            for f in target_files:
                query_parts.append(Path(f).stem)

        query = " ".join(query_parts).strip()
        try:
            matches = await self._memory_service.retrieve(
                project_id=project_id,
                query=query,
                limit=15,
                include_global=True,
                status_filter=MemoryStatus.ACTIVE,
            )
        except Exception as exc:
            logger.warning("Failed to retrieve durable memories: %s", exc)
            return []

        items: list[MemoryContextItem] = []
        for m in matches:
            safe_content = self._sanitize(m.content)
            safe_subject = self._sanitize(m.subject)
            tier_str = m.tier.value if hasattr(m.tier, "value") else str(m.tier)
            items.append(
                MemoryContextItem(
                    memory_id=m.memory_id,
                    subject=safe_subject,
                    content=safe_content,
                    tier=tier_str,
                    importance=m.importance,
                    score=m.score,
                )
            )

        # Sort by relevance score descending
        items.sort(key=lambda x: (x.score, x.importance), reverse=True)
        return items

    async def _retrieve_code_symbols(
        self,
        *,
        project_id: str,
        step_title: str,
        step_instruction: str,
        target_files: Sequence[str] | None,
    ) -> list[CodeSymbolContext]:
        if self._code_graph is None:
            return []

        candidate_terms: set[str] = set()
        if target_files:
            for tf in target_files:
                candidate_terms.add(tf)
                candidate_terms.add(Path(tf).stem)

        combined_text = f"{step_title} {step_instruction}"
        for match in FILE_PATH_PATTERN.findall(combined_text):
            candidate_terms.add(match)
            candidate_terms.add(Path(match).stem)
        for match in SYMBOL_IDENTIFIER_PATTERN.findall(combined_text):
            if len(match) >= 4:
                candidate_terms.add(match)

        matched_nodes: dict[str, CodeNode] = {}
        for term in list(candidate_terms)[:8]:
            try:
                nodes = await self._code_graph.find_nodes(
                    project_id=project_id,
                    query=term,
                    limit=5,
                )
                for node in nodes:
                    if node.node_id not in matched_nodes:
                        matched_nodes[node.node_id] = node
            except Exception as exc:
                logger.debug("CodeGraph search error for '%s': %s", term, exc)

        # Prioritize semantic symbols (functions, classes, methods) over files/directories
        symbols: list[CodeSymbolContext] = []
        semantic_kinds = {
            CodeNodeKind.FUNCTION,
            CodeNodeKind.CLASS,
            CodeNodeKind.METHOD,
            CodeNodeKind.INTERFACE,
            CodeNodeKind.STRUCT,
            CodeNodeKind.ROUTE,
            CodeNodeKind.SERVICE,
        }

        # Sort nodes: semantic kinds first
        sorted_nodes = sorted(
            matched_nodes.values(),
            key=lambda n: 0 if n.kind in semantic_kinds else 1,
        )

        for node in sorted_nodes[:8]:
            dep_names: list[str] = []
            dependent_names: list[str] = []

            try:
                deps = await self._code_graph.dependencies(
                    project_id=project_id,
                    node_id=node.node_id,
                    depth=1,
                )
                dep_names = [d.name for d in deps[:5]]
            except Exception:
                pass

            try:
                dependents = await self._code_graph.dependents(
                    project_id=project_id,
                    node_id=node.node_id,
                    depth=1,
                )
                dependent_names = [dp.name for dp in dependents[:5]]
            except Exception:
                pass

            symbols.append(
                CodeSymbolContext(
                    node_id=node.node_id,
                    name=node.name,
                    kind=node.kind.value if hasattr(node.kind, "value") else str(node.kind),
                    path=node.path,
                    line_start=node.line_start,
                    line_end=node.line_end,
                    dependencies=tuple(dep_names),
                    dependents=tuple(dependent_names),
                )
            )

        return symbols

    def _retrieve_design_contract(self, project_id: str) -> str | None:
        """Retrieves and sanitizes the UI design contract if present."""
        candidates = [
            self._design_dir / f"{project_id}.design.md",
            Path("data/designs") / f"{project_id}.design.md",
        ]
        for path in candidates:
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    return self._sanitize(content)
                except Exception as exc:
                    logger.warning("Failed to read design contract at %s: %s", path, exc)
        return None

    def _sanitize(self, text: str) -> str:
        """Applies SecretRedactor and PiiSanitizer sequentially."""
        if not text:
            return ""
        redacted = self._redactor.redact(text)
        sanitized_res = self._sanitizer.sanitize(redacted)
        return sanitized_res.sanitized_text

    def _assemble_and_prune(
        self,
        *,
        project_id: str,
        task_id: str | None,
        step_id: str | None,
        target_role: AgentRole,
        user_prefs: list[UserPreferenceContextItem],
        memories: list[MemoryContextItem],
        code_symbols: list[CodeSymbolContext],
        design_contract: str | None,
        token_budget: int,
        step_title: str,
        step_instruction: str,
    ) -> UnifiedContext:
        """Assembles prompt and performs deterministic multi-tier pruning when over budget."""
        pruned_count = 0

        # Working copies
        active_prefs = list(user_prefs)
        active_mems = list(memories)
        active_symbols = list(code_symbols)
        active_design = design_contract

        def build_rendered(
            prefs: list[UserPreferenceContextItem],
            mems: list[MemoryContextItem],
            syms: list[CodeSymbolContext],
            design: str | None,
        ) -> str:
            sections: list[str] = [
                "## Unified Context (Assembled by ContextBuilder)",
            ]

            # Section: Directives & Role
            sections.append(
                f"### Execution Scope & Role\n"
                f"- **Assigned Specialist Role:** `{target_role.value if isinstance(target_role, AgentRole) else str(target_role)}`\n"
                f"- **Project ID:** `{project_id}`"
            )
            if step_title:
                sections.append(f"- **Step Goal:** {step_title}")
            if step_instruction and step_instruction != step_title:
                sections.append(f"- **Instructions:** {step_instruction}")

            # Section: User Preferences
            if prefs:
                pref_lines: list[str] = ["### Active User Preferences & Directives"]
                for p in prefs:
                    pref_lines.append(
                        f"- [Rank {p.precedence_rank}] `{p.logical_key}` ({p.scope}): {p.content}"
                    )
                sections.append("\n".join(pref_lines))

            # Section: Memory & Conventions
            if mems:
                mem_lines: list[str] = ["### Architectural Decisions & Project Memory"]
                for m in mems:
                    mem_lines.append(
                        f"- [{m.tier.upper()}] **{m.subject}**: {m.content}"
                    )
                sections.append("\n".join(mem_lines))

            # Section: CodeGraph AST Symbols
            if syms:
                sym_lines: list[str] = ["### Code Graph Intelligence & AST Signatures"]
                for s in syms:
                    loc = f" ({s.path}:{s.line_start}-{s.line_end})" if s.path else ""
                    sym_lines.append(f"- **{s.kind.upper()}** `{s.name}`{loc}")
                    if s.dependencies:
                        sym_lines.append(f"  - Dependencies: {', '.join(s.dependencies)}")
                    if s.dependents:
                        sym_lines.append(f"  - Dependents: {', '.join(s.dependents)}")
                sections.append("\n".join(sym_lines))

            # Section: UI Design System Contract
            if design:
                sections.append(
                    f"### UI Design System Contract\n```markdown\n{design.strip()}\n```"
                )

            return "\n\n".join(sections)

        rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
        token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Multi-tier pruning loop if token estimate exceeds budget
        # Priority tiers to prune (least critical first):
        # Tier 1: Trim symbol dependencies and dependents
        if token_estimate > token_budget and active_symbols:
            trimmed_symbols = [
                CodeSymbolContext(
                    node_id=s.node_id,
                    name=s.name,
                    kind=s.kind,
                    path=s.path,
                    line_start=s.line_start,
                    line_end=s.line_end,
                    dependencies=s.dependencies[:2],
                    dependents=s.dependents[:2],
                )
                for s in active_symbols
            ]
            active_symbols = trimmed_symbols
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 2: Prune low importance memories (< 0.75)
        if token_estimate > token_budget and len(active_mems) > 3:
            before_len = len(active_mems)
            active_mems = [m for m in active_mems if m.importance >= 0.75]
            pruned_count += before_len - len(active_mems)
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 3: Truncate design contract if exceedingly long
        if token_estimate > token_budget and active_design and len(active_design) > 800:
            active_design = active_design[:800] + "\n... [Design contract truncated for token efficiency]"
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 4: Prune lower precedence user preferences (Rank < 3)
        if token_estimate > token_budget and len(active_prefs) > 2:
            before_len = len(active_prefs)
            active_prefs = [p for p in active_prefs if p.precedence_rank >= 3]
            pruned_count += before_len - len(active_prefs)
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 5: Limit memories to top 3
        if token_estimate > token_budget and len(active_mems) > 3:
            pruned_count += len(active_mems) - 3
            active_mems = active_mems[:3]
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 6: Limit symbols to top 3
        if token_estimate > token_budget and len(active_symbols) > 3:
            pruned_count += len(active_symbols) - 3
            active_symbols = active_symbols[:3]
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 7: Prune memories to top 1, then drop all memories
        if token_estimate > token_budget and len(active_mems) > 1:
            pruned_count += len(active_mems) - 1
            active_mems = active_mems[:1]
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        if token_estimate > token_budget and active_mems:
            pruned_count += len(active_mems)
            active_mems = []
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 8: Prune symbols to top 1, then drop all symbols
        if token_estimate > token_budget and len(active_symbols) > 1:
            pruned_count += len(active_symbols) - 1
            active_symbols = active_symbols[:1]
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        if token_estimate > token_budget and active_symbols:
            pruned_count += len(active_symbols)
            active_symbols = []
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 9: Drop design contract if still over budget
        if token_estimate > token_budget and active_design:
            active_design = None
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        # Tier 10: Prune user preferences to top 2, then top 1
        if token_estimate > token_budget and len(active_prefs) > 2:
            pruned_count += len(active_prefs) - 2
            active_prefs = active_prefs[:2]
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        if token_estimate > token_budget and len(active_prefs) > 1:
            pruned_count += len(active_prefs) - 1
            active_prefs = active_prefs[:1]
            rendered = build_rendered(active_prefs, active_mems, active_symbols, active_design)
            token_estimate = len(rendered) // CHARS_PER_TOKEN

        return UnifiedContext(
            project_id=project_id,
            task_id=task_id,
            step_id=step_id,
            target_role=target_role,
            user_preferences=tuple(active_prefs),
            memories=tuple(active_mems),
            code_symbols=tuple(active_symbols),
            design_contract=active_design,
            formatted_prompt_section=rendered,
            token_count_estimate=token_estimate,
            pruned_items_count=pruned_count,
        )
