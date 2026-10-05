from __future__ import annotations


class MyAgentError(Exception):
    code = 'my_agent_error'

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(MyAgentError):
    code = 'configuration_error'


class CapabilityUnavailableError(MyAgentError):
    code = 'capability_unavailable'


class CapabilityMockedError(CapabilityUnavailableError):
    code = 'capability_mocked'


class NotReadyError(MyAgentError):
    code = 'not_ready'


class AuthorizationError(MyAgentError):
    code = 'authorization_error'


class PermissionDeniedError(MyAgentError):
    code = 'permission_denied'


class BackendError(MyAgentError):
    code = 'backend_error'


class SandboxError(MyAgentError):
    code = 'sandbox_error'


class ToolExecutionError(MyAgentError):
    code = 'tool_execution_error'


class ValidationError(MyAgentError):
    code = 'validation_error'
