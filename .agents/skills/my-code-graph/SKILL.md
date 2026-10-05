---
name: my-code-graph
description: Scan and analyze repository intelligence via Code Graph (Tree-sitter AST, symbol lookup, dependency graphs, impact analysis, and semantic context).
---

# Code Graph & Repository Intelligence Workflow

Activate MyAgent's **Code Graph** engine to perform semantic codebase analysis without full-scanning or bloating the context window.

## Execution Guidelines

1. **Code Graph Synchronization**:
   - Use the MCP tool `sync_code_graph(project_id)` to parse polyglot ASTs via Tree-sitter (Python, TypeScript, JavaScript, Go, Rust).
   - Inspect graph synchronization status using `code_graph_status(project_id)`.

2. **Symbol Lookup**:
   - Use the MCP tool `find_code_symbol(symbol_name, project_id)` to quickly locate Classes, Functions, Interfaces, and Structs with exact file paths and line ranges.

3. **Dependency & Impact Analysis**:
   - `code_dependencies(node_id, depth)`: Inspect upstream functions, classes, and modules that this node depends on.
   - `code_dependents(node_id, depth)`: Inspect downstream callers and referencing files.
   - `impact_analysis(changed_files)`: Evaluate the blast radius before modifying any source code.

4. **Targeted Code Context Extraction**:
   - Use `code_context(node_id, context_lines, tier='symbol'|'neighbors')` to extract precise code definitions and immediate neighbor nodes without context overflow.

