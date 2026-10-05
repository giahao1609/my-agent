---
name: my-memory
description: Manage tiered memory architecture (L0 session -> L1 working -> L2 project -> L3 persistent) and semantic docs knowledge retrieval.
---

# Tiered Memory & Docs Knowledge Workflow

Activate MyAgent's **Memory Consolidator** and **Docs Knowledge Backend** to retain persistent engineering learnings and search project documentation.

## Execution Guidelines

1. **Session Memory Consolidation**:
   - Use the MCP tool `consolidate_memory(session_id, project_id)` to distill key architectural decisions and engineering learnings from L0 (ephemeral session messages) into L1 (working memory).
   - Repeatedly referenced concepts are promoted automatically to L2 (project scope) and L3 (cross-project persistent patterns).

2. **Project Documentation Search**:
   - Use the MCP tool `search_docs(query, project_id)` to perform BM25/semantic relevance ranking across all project markdown files (`README.md`, `AGENTS.md`, `docs/*.md`).

