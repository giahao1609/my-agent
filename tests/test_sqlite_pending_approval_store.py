from __future__ import annotations

import pytest
from pathlib import Path

from persistence.sqlite_pending_approval_store import SqlitePendingApprovalStore


@pytest.mark.asyncio
async def test_save_and_get(tmp_path: Path) -> None:
    store = SqlitePendingApprovalStore(tmp_path / "approvals.db")
    await store.initialize()

    approval = {
        "tool_call_id": "call-abc",
        "tool_name": "delete_path",
        "arguments": {"path": "cache/", "recursive": True},
        "reason": "Xóa thư mục",
        "risk_explanation": "Sẽ xóa toàn bộ cache",
        "prompt": "Bạn có muốn xóa?",
        "task_id": "task-1",
    }
    await store.save(approval)

    result = await store.get("call-abc")
    assert result is not None
    assert result["tool_call_id"] == "call-abc"
    assert result["tool_name"] == "delete_path"
    assert result["arguments"] == {"path": "cache/", "recursive": True}
    assert result["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_list_by_task(tmp_path: Path) -> None:
    store = SqlitePendingApprovalStore(tmp_path / "approvals.db")
    await store.initialize()

    for i in range(3):
        await store.save({
            "tool_call_id": f"call-{i}",
            "tool_name": "run_command",
            "arguments": {},
            "reason": f"reason-{i}",
            "risk_explanation": "",
            "prompt": "proceed?",
            "task_id": "task-10" if i < 2 else "task-99",
        })

    task10 = await store.list_by_task("task-10")
    assert len(task10) == 2
    assert all(a["task_id"] == "task-10" for a in task10)

    task99 = await store.list_by_task("task-99")
    assert len(task99) == 1


@pytest.mark.asyncio
async def test_delete_removes_record(tmp_path: Path) -> None:
    store = SqlitePendingApprovalStore(tmp_path / "approvals.db")
    await store.initialize()

    await store.save({
        "tool_call_id": "call-del",
        "tool_name": "delete_path",
        "arguments": {},
        "reason": "",
        "risk_explanation": "",
        "prompt": "ok?",
        "task_id": "task-1",
    })

    deleted = await store.delete("call-del")
    assert deleted is not None
    assert deleted["tool_call_id"] == "call-del"

    after = await store.get("call-del")
    assert after is None


@pytest.mark.asyncio
async def test_list_all(tmp_path: Path) -> None:
    store = SqlitePendingApprovalStore(tmp_path / "approvals.db")
    await store.initialize()

    for i in range(5):
        await store.save({
            "tool_call_id": f"call-{i}",
            "tool_name": "run_command",
            "arguments": {},
            "reason": "",
            "risk_explanation": "",
            "prompt": "",
            "task_id": f"task-{i}",
        })

    all_records = await store.list_all()
    assert len(all_records) == 5


@pytest.mark.asyncio
async def test_save_overwrites_existing(tmp_path: Path) -> None:
    """Saving the same tool_call_id twice should update the record, not duplicate it."""
    store = SqlitePendingApprovalStore(tmp_path / "approvals.db")
    await store.initialize()

    await store.save({
        "tool_call_id": "call-upsert",
        "tool_name": "delete_path",
        "arguments": {"path": "old"},
        "reason": "v1",
        "risk_explanation": "",
        "prompt": "",
        "task_id": "task-1",
    })
    await store.save({
        "tool_call_id": "call-upsert",
        "tool_name": "delete_path",
        "arguments": {"path": "new"},
        "reason": "v2",
        "risk_explanation": "",
        "prompt": "",
        "task_id": "task-1",
    })

    all_records = await store.list_all()
    assert len(all_records) == 1
    assert all_records[0]["reason"] == "v2"


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_none(tmp_path: Path) -> None:
    store = SqlitePendingApprovalStore(tmp_path / "approvals.db")
    await store.initialize()

    result = await store.delete("call-does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_persistence_across_instances(tmp_path: Path) -> None:
    """Data saved by one store instance should be readable by a new instance on the same DB."""
    db_path = tmp_path / "approvals.db"
    store1 = SqlitePendingApprovalStore(db_path)
    await store1.initialize()
    await store1.save({
        "tool_call_id": "call-persist",
        "tool_name": "run_command",
        "arguments": {},
        "reason": "durability test",
        "risk_explanation": "",
        "prompt": "",
        "task_id": "task-1",
    })

    # Simulate restart by creating a fresh instance
    store2 = SqlitePendingApprovalStore(db_path)
    await store2.initialize()
    result = await store2.get("call-persist")
    assert result is not None
    assert result["reason"] == "durability test"
