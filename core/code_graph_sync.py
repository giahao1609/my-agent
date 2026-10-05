from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.code_graph import CodeNodeKind
from core.polyglot_code_scanner import build_default_scanner_registry, scan_polyglot_project


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    path: str
    sha256: str
    size: int
    mtime_ns: int


def fingerprint_file(root: Path, path: Path) -> FileFingerprint:
    data = path.read_bytes()
    stat = path.stat()
    return FileFingerprint(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(data).hexdigest(), size=stat.st_size, mtime_ns=stat.st_mtime_ns)

@dataclass(frozen=True, slots=True)
class FileChanges:
    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()


def snapshot_code_files(root: Path) -> dict[str, FileFingerprint]:
    root = Path(root).resolve()
    ignored = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".idea", ".gradle", ".next"}
    registry = build_default_scanner_registry()
    result: dict[str, FileFingerprint] = {}
    for current, dirs, files in os.walk(root, topdown=True, onerror=lambda _e: None, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ignored]
        base = Path(current)
        for name in files:
            path = base / name
            try:
                if registry.supports(path):
                    fp = fingerprint_file(root, path)
                    result[fp.path] = fp
            except OSError:
                continue
    return result

def diff_snapshots(old: dict[str, FileFingerprint], new: dict[str, FileFingerprint]) -> FileChanges:
    old_paths = set(old)
    new_paths = set(new)
    added = tuple(sorted(new_paths - old_paths))
    deleted = tuple(sorted(old_paths - new_paths))
    modified = tuple(sorted(p for p in old_paths & new_paths if old[p].sha256 != new[p].sha256))
    return FileChanges(added=added, modified=modified, deleted=deleted)

async def sync_code_graph(project_id: str, root: Path, store):
    await store.set_graph_status(project_id, GraphStatus.INDEXING.value)
    try:
        old = await store.load_file_snapshot(project_id)
        current = await asyncio.to_thread(snapshot_code_files, root)
        changes = diff_snapshots(old, current)
        if old and not (changes.added or changes.modified or changes.deleted):
            await store.set_graph_status(project_id, GraphStatus.READY.value)
            return changes
        changed = set(changes.added) | set(changes.modified)
        seed_symbols = await store.load_symbol_index(project_id) if old else None
        full_refresh = not old or bool(changes.added or changes.deleted)

        if full_refresh:
            result = await asyncio.to_thread(
                scan_polyglot_project,
                project_id,
                root,
            )
        else:
            result = await asyncio.to_thread(
                scan_polyglot_project,
                project_id,
                root,
                include_paths=changed or None,
                seed_symbols=seed_symbols,
            )
            kinds = {
                CodeNodeKind.FUNCTION,
                CodeNodeKind.CLASS,
                CodeNodeKind.METHOD,
                CodeNodeKind.INTERFACE,
                CodeNodeKind.ENUM,
                CodeNodeKind.STRUCT,
                CodeNodeKind.TRAIT,
            }
            for path in changes.modified:
                if hasattr(store, "file_symbol_fingerprints"):
                    old_symbols = await store.file_symbol_fingerprints(project_id, path)
                    new_symbols = {
                        (
                            n.kind.value,
                            n.qualified_name,
                            n.language or "",
                            str(n.metadata.get("go_signature") or ""),
                            str(n.metadata.get("java_signature") or ""),
                            str(n.metadata.get("receiver_form") or ""),
                            bool(n.metadata.get("interface_method")),
                        )
                        for n in result.nodes
                        if n.path == path and n.kind in kinds and n.qualified_name
                    }
                else:
                    old_symbols = await store.file_symbol_names(project_id, path)
                    new_symbols = {
                        n.qualified_name
                        for n in result.nodes
                        if n.path == path and n.kind in kinds and n.qualified_name
                    }

                if old_symbols != new_symbols:
                    full_refresh = True
                    result = await asyncio.to_thread(
                        scan_polyglot_project,
                        project_id,
                        root,
                    )
                    break
        verified = await asyncio.to_thread(snapshot_code_files, root)
        raced = diff_snapshots(current, verified)
        if raced.added or raced.modified or raced.deleted:
            await store.set_graph_status(project_id, GraphStatus.STALE.value, "filesystem changed during graph scan")
            return diff_snapshots(old, verified)
        if full_refresh:
            await store.replace_project_graph(project_id, result.nodes, result.edges)
        else:
            await store.replace_changed_files(project_id, changed, set(changes.deleted), result.nodes, result.edges)
        await store.save_file_snapshot(project_id, verified)
        await store.set_graph_status(project_id, GraphStatus.READY.value)
        return changes
    except Exception as exc:
        await store.set_graph_status(project_id, GraphStatus.FAILED.value, str(exc))
        raise


# Backward-compatible aliases for callers using the original Python-only API names.
snapshot_python_files = snapshot_code_files
sync_python_graph = sync_code_graph

class GraphStatus(StrEnum):
    NOT_INDEXED = "not_indexed"
    INDEXING = "indexing"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"

