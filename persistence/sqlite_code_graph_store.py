from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import UTC
from pathlib import Path

from core.code_graph import CodeNode, CodeNodeKind, ImpactResult
from core.code_graph_sync import FileFingerprint

SEMANTIC_EDGE_KINDS = ("imports", "calls", "references", "inherits", "implements", "reads", "writes", "tests", "routes_to", "depends_on")

# Lower index = higher priority when sorting find_nodes results.
# Semantic symbols (function, class, method…) surface before structural nodes (file, dir…).
_KIND_PRIORITY: dict[str, int] = {k: i for i, k in enumerate([
    "function", "class", "method", "interface", "enum", "struct", "trait",
    "component", "route", "test", "service", "datastore",
    "module", "package", "namespace", "variable",
    "file", "directory", "config", "resource", "style",
    "image", "container", "external", "project",
])}


class SQLiteCodeGraphStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        # Set to False when the SQLite build lacks FTS5 (rare, but degrade gracefully).
        self._fts_enabled: bool = True

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS code_nodes (project_id TEXT NOT NULL, node_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, path TEXT, qualified_name TEXT, language TEXT, line_start INTEGER, line_end INTEGER, metadata_json TEXT NOT NULL, PRIMARY KEY (project_id, node_id))")
            connection.execute("CREATE TABLE IF NOT EXISTS code_edges (project_id TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL, kind TEXT NOT NULL, metadata_json TEXT NOT NULL, PRIMARY KEY (project_id, source_id, target_id, kind))")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_code_edges_source ON code_edges(project_id, source_id, kind)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_code_edges_target ON code_edges(project_id, target_id, kind)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_code_nodes_name ON code_nodes(project_id, name, kind)")
            connection.execute("CREATE TABLE IF NOT EXISTS code_graph_files (project_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, PRIMARY KEY(project_id, path))")
            connection.execute("CREATE TABLE IF NOT EXISTS code_graph_status (project_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT)")
            # ── FTS5 full-text search index ────────────────────────────────────────
            # Backed by code_nodes (external content table) so the FTS index stays
            # automatically consistent via AFTER INSERT/DELETE/UPDATE triggers.
            # Degrades gracefully to LIKE-based search if this SQLite build lacks FTS5.
            try:
                connection.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS code_nodes_fts USING fts5(
                        node_id     UNINDEXED,
                        project_id  UNINDEXED,
                        name,
                        qualified_name,
                        content='code_nodes',
                        content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 1'
                    )
                """)
                connection.execute("""
                    CREATE TRIGGER IF NOT EXISTS code_nodes_fts_ai
                    AFTER INSERT ON code_nodes BEGIN
                        INSERT INTO code_nodes_fts(rowid, node_id, project_id, name, qualified_name)
                        VALUES (new.rowid, new.node_id, new.project_id, new.name,
                                COALESCE(new.qualified_name, ''));
                    END
                """)
                connection.execute("""
                    CREATE TRIGGER IF NOT EXISTS code_nodes_fts_ad
                    AFTER DELETE ON code_nodes BEGIN
                        INSERT INTO code_nodes_fts(code_nodes_fts, rowid, node_id, project_id, name, qualified_name)
                        VALUES ('delete', old.rowid, old.node_id, old.project_id, old.name,
                                COALESCE(old.qualified_name, ''));
                    END
                """)
                connection.execute("""
                    CREATE TRIGGER IF NOT EXISTS code_nodes_fts_au
                    AFTER UPDATE ON code_nodes BEGIN
                        INSERT INTO code_nodes_fts(code_nodes_fts, rowid, node_id, project_id, name, qualified_name)
                        VALUES ('delete', old.rowid, old.node_id, old.project_id, old.name,
                                COALESCE(old.qualified_name, ''));
                        INSERT INTO code_nodes_fts(rowid, node_id, project_id, name, qualified_name)
                        VALUES (new.rowid, new.node_id, new.project_id, new.name,
                                COALESCE(new.qualified_name, ''));
                    END
                """)
                # Rebuild FTS index from current code_nodes content (idempotent).
                connection.execute("INSERT INTO code_nodes_fts(code_nodes_fts) VALUES('rebuild')")
            except sqlite3.OperationalError:
                # FTS5 extension not available in this SQLite build.
                self._fts_enabled = False
    async def replace_project_graph(self, project_id: str, nodes, edges) -> None:
        await asyncio.to_thread(self._replace_project_graph_sync, project_id, nodes, edges)
    def _replace_project_graph_sync(self, project_id: str, nodes, edges) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM code_edges WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM code_nodes WHERE project_id = ?", (project_id,))
            connection.executemany("INSERT INTO code_nodes (project_id,node_id,kind,name,path,qualified_name,language,line_start,line_end,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)", [(n.project_id,n.node_id,n.kind.value,n.name,n.path,n.qualified_name,n.language,n.line_start,n.line_end,json.dumps(dict(n.metadata),ensure_ascii=False)) for n in nodes])
            connection.executemany("INSERT INTO code_edges (project_id,source_id,target_id,kind,metadata_json) VALUES (?,?,?,?,?)", [(e.project_id,e.source_id,e.target_id,e.kind.value,json.dumps(dict(e.metadata),ensure_ascii=False)) for e in edges])
    async def get_node(self, project_id: str, node_id: str):
        return await asyncio.to_thread(self._get_node_sync, project_id, node_id)
    def _get_node_sync(self, project_id: str, node_id: str):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM code_nodes WHERE project_id = ? AND node_id = ?", (project_id, node_id)).fetchone()
        return self._row_to_node(row) if row is not None else None
    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> CodeNode:
        return CodeNode(project_id=str(row["project_id"]), node_id=str(row["node_id"]), kind=CodeNodeKind(str(row["kind"])), name=str(row["name"]), path=row["path"], qualified_name=row["qualified_name"], language=row["language"], line_start=row["line_start"], line_end=row["line_end"], metadata=json.loads(str(row["metadata_json"])))
    async def find_nodes(self, project_id: str, query: str, *, kinds=None, limit: int = 50):
        return await asyncio.to_thread(self._find_nodes_sync, project_id, query, kinds, limit)

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """Convert a user query string into an FTS5 MATCH expression.

        Each whitespace/punctuation-delimited token becomes a quoted prefix term
        so that e.g. ``"handle"`` matches ``handle_message`` and ``apply``
        matches ``apply_discount`` (unicode61 tokenises on ``_`` and ``.``).
        """
        # Strip FTS5 special chars that would cause MATCH syntax errors.
        clean = re.sub(r'[^\w\s]', ' ', query.strip())
        tokens = clean.split()
        if not tokens:
            return '""*'
        return ' '.join(f'"{t}"*' for t in tokens if t)

    def _find_nodes_sync(self, project_id: str, query: str, kinds, limit: int):
        overfetch = limit * 4  # overfetch so kind-filter still has enough candidates
        rows = None

        if self._fts_enabled:
            try:
                fts_expr = self._build_fts_query(query)
                with self._connect() as c:
                    # JOIN back to code_nodes so we can filter by project_id
                    # and return full node rows. f.rank is the negative BM25
                    # score — lower (more negative) = better match.
                    rows = c.execute(
                        """SELECT n.*
                             FROM code_nodes n
                             JOIN code_nodes_fts f ON n.rowid = f.rowid
                            WHERE f.code_nodes_fts MATCH ?
                              AND n.project_id = ?
                            ORDER BY f.rank
                            LIMIT ?""",
                        (fts_expr, project_id, overfetch),
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = None  # FTS5 unavailable or malformed query → fall through

        if rows is None:
            # Fallback: classic LIKE substring search (no ranking).
            pattern = "%" + query.strip() + "%"
            with self._connect() as c:
                rows = c.execute(
                    "SELECT * FROM code_nodes"
                    " WHERE project_id = ? AND (name LIKE ? OR qualified_name LIKE ?)"
                    " LIMIT ?",
                    (project_id, pattern, pattern, overfetch),
                ).fetchall()

        nodes: list[CodeNode] = [self._row_to_node(r) for r in rows]

        # Kind-priority sort: semantic symbols surface before structural nodes
        # so the AI sees the most relevant hits first without needing kind filters.
        nodes.sort(key=lambda n: _KIND_PRIORITY.get(n.kind.value, 99))

        if kinds:
            allowed = set(kinds)
            nodes = [n for n in nodes if n.kind in allowed]

        return tuple(nodes[:limit])

    async def dependencies(self, project_id: str, node_id: str, *, depth: int = 1):
        return await asyncio.to_thread(self._dependencies_sync, project_id, node_id, depth)
    def _dependencies_sync(self, project_id, node_id, depth):
        if depth < 0:
            raise ValueError("depth must be >= 0")
        if depth == 0:
            return ()
        sql = """WITH RECURSIVE w(id,d) AS (
            SELECT target_id,1 FROM code_edges WHERE project_id=? AND source_id=?
            UNION
            SELECT e.target_id,w.d+1 FROM code_edges e JOIN w ON e.source_id=w.id
            WHERE e.project_id=? AND w.d<?
        )
        SELECT DISTINCT n.* FROM code_nodes n
        JOIN w ON n.node_id=w.id
        WHERE n.project_id=?"""
        with self._connect() as c:
            rows = c.execute(sql, (project_id, node_id, project_id, depth, project_id)).fetchall()
        return tuple(self._row_to_node(r) for r in rows)
    async def dependents(self, project_id: str, node_id: str, *, depth: int = 1):
        return await asyncio.to_thread(self._dependents_sync, project_id, node_id, depth)
    def _dependents_sync(self, project_id, node_id, depth):
        if depth < 0:
            raise ValueError("depth must be >= 0")
        seen = {node_id}
        frontier = {node_id}
        result = []
        with self._connect() as c:
            for _ in range(depth):
                found = set()
                for current in frontier:
                    rows = c.execute("SELECT source_id FROM code_edges WHERE project_id=? AND target_id=?", (project_id, current)).fetchall()
                    found.update(str(r["source_id"]) for r in rows)
                found -= seen
                if not found:
                    break
                seen |= found
                frontier = found
                for nid in found:
                    row = c.execute("SELECT * FROM code_nodes WHERE project_id=? AND node_id=?", (project_id, nid)).fetchone()
                    if row:
                        result.append(self._row_to_node(row))
        return tuple(result)
    async def semantic_dependencies(self, project_id: str, node_id: str, *, depth: int = 1, limit: int | None = None):
        return await asyncio.to_thread(self._semantic_walk_sync, project_id, node_id, depth, False, limit)

    async def semantic_dependents(self, project_id: str, node_id: str, *, depth: int = 1, limit: int | None = None):
        return await asyncio.to_thread(self._semantic_walk_sync, project_id, node_id, depth, True, limit)

    def _semantic_walk_sync(self, project_id, node_id, depth, reverse, limit=None):
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a nonnegative integer")
        if depth.__lt__(0):
            raise ValueError("depth must be >= 0")
        if depth == 0 or limit == 0:
            return ()
        src, dst = ("target_id", "source_id") if reverse else ("source_id", "target_id")
        placeholders = ",".join("?" for _ in SEMANTIC_EDGE_KINDS)
        sql = f"""WITH RECURSIVE w(id,d) AS (
            SELECT {dst},1 FROM code_edges WHERE project_id=? AND {src}=? AND kind IN ({placeholders})
            UNION
            SELECT e.{dst},w.d+1 FROM code_edges e JOIN w ON e.{src}=w.id
            WHERE e.project_id=? AND e.kind IN ({placeholders}) AND w.d<?
        )
        SELECT DISTINCT n.* FROM code_nodes n
        JOIN w ON n.node_id=w.id
        WHERE n.project_id=?"""
        params = (project_id, node_id, *SEMANTIC_EDGE_KINDS, project_id, *SEMANTIC_EDGE_KINDS, depth, project_id)
        if limit is not None:
            sql += " ORDER BY n.node_id LIMIT ?"
            params += (limit,)
        with self._connect() as c:
            rows = c.execute(sql, params).fetchall()
        return tuple(self._row_to_node(r) for r in rows)

    async def impact(self, project_id: str, node_id: str, *, max_depth: int = 5):
        return await asyncio.to_thread(self._impact_sync, project_id, node_id, max_depth)
    def _impact_sync(self, project_id, node_id, max_depth):
        direct = self._semantic_walk_sync(
            project_id,
            node_id,
            1,
            True,
        )
        transitive = self._semantic_walk_sync(
            project_id,
            node_id,
            max_depth,
            True,
        )
        tests = tuple(
            node for node in transitive if node.kind == CodeNodeKind.TEST
        )
        entrypoints = tuple(
            node
            for node in transitive
            if node.kind in (CodeNodeKind.ROUTE, CodeNodeKind.COMPONENT)
        )
        return ImpactResult(
            project_id=project_id,
            root_node_id=node_id,
            direct_dependents=direct,
            transitive_dependents=transitive,
            related_tests=tests,
            entrypoints=entrypoints,
        )

    async def save_file_snapshot(self, project_id: str, snapshot: dict[str, FileFingerprint]):
        await asyncio.to_thread(self._save_file_snapshot_sync, project_id, snapshot)
    def _save_file_snapshot_sync(self, project_id, snapshot):
        with self._connect() as c:
            c.execute("DELETE FROM code_graph_files WHERE project_id = ?", (project_id,))
            c.executemany("INSERT INTO code_graph_files(project_id,path,sha256,size,mtime_ns) VALUES(?,?,?,?,?)", [(project_id, fp.path, fp.sha256, fp.size, fp.mtime_ns) for fp in snapshot.values()])
    async def load_file_snapshot(self, project_id: str):
        return await asyncio.to_thread(self._load_file_snapshot_sync, project_id)
    def _load_file_snapshot_sync(self, project_id):
        with self._connect() as c:
            rows = c.execute("SELECT path,sha256,size,mtime_ns FROM code_graph_files WHERE project_id = ?", (project_id,)).fetchall()
        return {str(r["path"]): FileFingerprint(str(r["path"]), str(r["sha256"]), int(r["size"]), int(r["mtime_ns"])) for r in rows}
    async def set_graph_status(self, project_id: str, status: str, error: str | None = None):
        await asyncio.to_thread(self._set_graph_status_sync, project_id, status, error)
    def _set_graph_status_sync(self, project_id, status, error=None):
        from datetime import datetime
        now = datetime.now(UTC).isoformat()
        with self._connect() as c:
            c.execute("INSERT INTO code_graph_status(project_id,status,updated_at,error) VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, error=excluded.error", (project_id, status, now, error))
    async def get_graph_status(self, project_id: str):
        return await asyncio.to_thread(self._get_graph_status_sync, project_id)
    def _get_graph_status_sync(self, project_id):
        with self._connect() as c:
            row = c.execute("SELECT status,updated_at,error FROM code_graph_status WHERE project_id = ?", (project_id,)).fetchone()
            node_count = c.execute("SELECT COUNT(*) FROM code_nodes WHERE project_id = ?", (project_id,)).fetchone()[0]
            edge_count = c.execute("SELECT COUNT(*) FROM code_edges WHERE project_id = ?", (project_id,)).fetchone()[0]
            file_count = c.execute("SELECT COUNT(*) FROM code_graph_files WHERE project_id = ?", (project_id,)).fetchone()[0]
            # Orphan paths: node paths that no longer exist in the file snapshot.
            # Non-zero means stale entries from deleted/renamed files are still in the graph.
            orphan_count = c.execute(
                """SELECT COUNT(DISTINCT path) FROM code_nodes
                    WHERE project_id = ? AND path IS NOT NULL
                      AND path NOT IN (
                              SELECT path FROM code_graph_files WHERE project_id = ?
                          )""",
                (project_id, project_id),
            ).fetchone()[0]
        base = {"node_count": node_count, "edge_count": edge_count,
                "file_count": file_count, "orphan_node_paths": orphan_count}
        if row is None:
            return {"status": "not_indexed", "updated_at": None, "error": None, **base}
        return {"status": str(row["status"]), "updated_at": str(row["updated_at"]),
                "error": row["error"], **base}
    async def load_symbol_index(self, project_id: str):
        return await asyncio.to_thread(self._load_symbol_index_sync, project_id)
    def _load_symbol_index_sync(self, project_id):
        with self._connect() as c:
            rows = c.execute("SELECT qualified_name,node_id FROM code_nodes WHERE project_id = ? AND qualified_name IS NOT NULL AND length(qualified_name) > 0", (project_id,)).fetchall()
        return {str(r["qualified_name"]): str(r["node_id"]) for r in rows}
    async def replace_changed_files(self, project_id, changed_paths, deleted_paths, nodes, edges):
        await asyncio.to_thread(self._replace_changed_files_sync, project_id, changed_paths, deleted_paths, nodes, edges)

    def _replace_changed_files_sync(self, project_id, changed_paths, deleted_paths, nodes, edges):
        paths = set(changed_paths) | set(deleted_paths)
        new_ids = {n.node_id for n in nodes}
        with self._connect() as c:
            old_ids = set()
            if paths:
                marks = ",".join("?" for _ in paths)
                rows = c.execute(f"SELECT node_id FROM code_nodes WHERE project_id=? AND path IN ({marks})", (project_id, *sorted(paths))).fetchall()
                old_ids = {str(r["node_id"]) for r in rows}
            if old_ids:
                marks = ",".join("?" for _ in old_ids)
                c.execute(f"DELETE FROM code_edges WHERE project_id=? AND source_id IN ({marks})", (project_id, *sorted(old_ids)))
                removed = old_ids - new_ids
                if removed:
                    rmarks = ",".join("?" for _ in removed)
                    c.execute(f"DELETE FROM code_edges WHERE project_id=? AND target_id IN ({rmarks})", (project_id, *sorted(removed)))
            if paths:
                marks = ",".join("?" for _ in paths)
                c.execute(f"DELETE FROM code_nodes WHERE project_id=? AND path IN ({marks})", (project_id, *sorted(paths)))
            c.executemany("INSERT OR REPLACE INTO code_nodes(project_id,node_id,kind,name,path,qualified_name,language,line_start,line_end,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)", [(n.project_id,n.node_id,n.kind.value,n.name,n.path,n.qualified_name,n.language,n.line_start,n.line_end,json.dumps(n.metadata)) for n in nodes])
            c.executemany("INSERT OR REPLACE INTO code_edges(project_id,source_id,target_id,kind,metadata_json) VALUES(?,?,?,?,?)", [(e.project_id,e.source_id,e.target_id,e.kind.value,json.dumps(e.metadata)) for e in edges])
    async def file_symbol_names(self, project_id: str, path: str):
        return await asyncio.to_thread(self._file_symbol_names_sync, project_id, path)

    def _file_symbol_names_sync(self, project_id, path):
        with self._connect() as c:
            kinds = (CodeNodeKind.FUNCTION, CodeNodeKind.CLASS, CodeNodeKind.METHOD, CodeNodeKind.INTERFACE, CodeNodeKind.ENUM, CodeNodeKind.STRUCT, CodeNodeKind.TRAIT)
            placeholders = ", ".join("?" for _ in kinds)
            rows = c.execute(f"SELECT qualified_name FROM code_nodes WHERE project_id=? AND path=? AND kind IN ({placeholders}) AND qualified_name IS NOT NULL", (project_id, path, *(kind.value for kind in kinds))).fetchall()
        return {str(r["qualified_name"]) for r in rows}

    async def file_symbol_fingerprints(self, project_id: str, path: str):
        return await asyncio.to_thread(self._file_symbol_fingerprints_sync, project_id, path)

    def _file_symbol_fingerprints_sync(self, project_id, path):
        with self._connect() as c:
            kinds = (CodeNodeKind.FUNCTION, CodeNodeKind.CLASS, CodeNodeKind.METHOD, CodeNodeKind.INTERFACE, CodeNodeKind.ENUM, CodeNodeKind.STRUCT, CodeNodeKind.TRAIT)
            placeholders = ", ".join("?" for _ in kinds)
            rows = c.execute(
                f"SELECT kind, qualified_name, language, metadata_json FROM code_nodes WHERE project_id=? AND path=? AND kind IN ({placeholders}) AND qualified_name IS NOT NULL",
                (project_id, path, *(kind.value for kind in kinds)),
            ).fetchall()
        result = set()
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            result.add((
                str(row["kind"]),
                str(row["qualified_name"]),
                str(row["language"] or ""),
                str(metadata.get("go_signature") or ""),
                str(metadata.get("java_signature") or ""),
                str(metadata.get("receiver_form") or ""),
                bool(metadata.get("interface_method")),
            ))
        return result
