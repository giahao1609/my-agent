from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import CoderRuntimeMode, Settings
from my_agent_mcp import server


def test_coder_runtime_mode_values() -> None:
    assert CoderRuntimeMode.EXTERNAL.value == "external"
    assert CoderRuntimeMode.IN_PROCESS.value == "in_process"


def test_settings_default_to_external_coder_runtime() -> None:
    settings = Settings()

    assert settings.coder_runtime_mode is CoderRuntimeMode.EXTERNAL


@pytest.mark.asyncio
async def test_next_coder_command_rejected_in_in_process_mode(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )

    async def fail_if_stack_touched():
        raise AssertionError(
            "runtime stack must not be touched"
        )

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fail_if_stack_touched,
    )

    with pytest.raises(
        RuntimeError,
        match="external coder runtime mode",
    ):
        await server.next_coder_command(
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_publish_coder_event_rejected_in_in_process_mode(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )

    async def fail_if_stack_touched():
        raise AssertionError(
            "runtime stack must not be touched"
        )

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fail_if_stack_touched,
    )

    with pytest.raises(
        RuntimeError,
        match="external coder runtime mode",
    ):
        await server.publish_coder_event(
            session_id="session-1",
            event_type="text",
            payload={"text": "hello"},
        )
