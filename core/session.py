from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .context import ExecutionContext


class SessionState(StrEnum):
    CREATED = 'created'
    STARTING = 'starting'
    RUNNING = 'running'
    WAITING_APPROVAL = 'waiting_approval'
    CANCELLING = 'cancelling'
    STOPPED = 'stopped'
    FAILED = 'failed'


_ALLOWED_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.STARTING, SessionState.STOPPED, SessionState.FAILED}),
    SessionState.STARTING: frozenset({SessionState.RUNNING, SessionState.STOPPED, SessionState.FAILED}),
    SessionState.RUNNING: frozenset({SessionState.WAITING_APPROVAL, SessionState.CANCELLING, SessionState.STOPPED, SessionState.FAILED}),
    SessionState.WAITING_APPROVAL: frozenset({SessionState.RUNNING, SessionState.CANCELLING, SessionState.STOPPED, SessionState.FAILED}),
    SessionState.CANCELLING: frozenset({SessionState.STOPPED, SessionState.FAILED}),
    SessionState.STOPPED: frozenset(),
    SessionState.FAILED: frozenset(),
}


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    context: ExecutionContext
    runtime_id: str | None = None
    model_id: str | None = None
    state: SessionState = SessionState.CREATED

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError('session_id must not be empty')
        if self.runtime_id is not None and not self.runtime_id.strip():
            raise ValueError('runtime_id must not be empty')
        if self.model_id is not None and not self.model_id.strip():
            raise ValueError('model_id must not be empty')

    @property
    def project_id(self) -> str | None:
        return self.context.project_id

    def set_runtime(self, runtime_id: str | None) -> None:
        if runtime_id is not None and not runtime_id.strip():
            raise ValueError('runtime_id must not be empty')
        self.runtime_id = runtime_id

    def set_model(self, model_id: str | None) -> None:
        if model_id is not None and not model_id.strip():
            raise ValueError('model_id must not be empty')
        self.model_id = model_id

    def transition(self, new_state: SessionState) -> None:
        if new_state is self.state:
            return
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(
                f'invalid session transition: {self.state} -> {new_state}'
            )
        self.state = new_state

    @property
    def terminal(self) -> bool:
        return self.state in {SessionState.STOPPED, SessionState.FAILED}
