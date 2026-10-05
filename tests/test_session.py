from core.context import ExecutionContext
from core.session import SessionRecord, SessionState


def test_session_record_does_not_require_project_scope() -> None:
    record = SessionRecord(
        session_id="session-1",
        context=ExecutionContext(workspace_id="workspace-1"),
    )

    assert record.project_id is None
    assert record.state is SessionState.CREATED


def test_session_record_uses_project_scope_from_context() -> None:
    record = SessionRecord(
        session_id="session-1",
        context=ExecutionContext(
            workspace_id="workspace-1",
            project_id="project-1",
        ),
    )

    assert record.project_id == "project-1"


def test_session_record_has_no_hardcoded_runtime() -> None:
    record = SessionRecord(
        session_id="session-1",
        context=ExecutionContext(workspace_id="workspace-1"),
    )

    assert record.runtime_id is None
    assert record.model_id is None


def test_session_record_can_switch_runtime_and_model() -> None:
    record = SessionRecord(
        session_id="session-1",
        context=ExecutionContext(workspace_id="workspace-1"),
    )

    record.set_runtime("antigravity")
    record.set_model("anti-model-a")

    assert record.runtime_id == "antigravity"
    assert record.model_id == "anti-model-a"

    record.set_runtime("codex")
    record.set_model("codex-model")

    assert record.runtime_id == "codex"
    assert record.model_id == "codex-model"

    record.set_runtime("claude")
    record.set_model("claude-model")

    assert record.runtime_id == "claude"
    assert record.model_id == "claude-model"


def test_session_record_can_clear_active_runtime_and_model() -> None:
    record = SessionRecord(
        session_id="session-1",
        context=ExecutionContext(workspace_id="workspace-1"),
        runtime_id="antigravity",
        model_id="anti-model-a",
    )

    record.set_runtime(None)
    record.set_model(None)

    assert record.runtime_id is None
    assert record.model_id is None
