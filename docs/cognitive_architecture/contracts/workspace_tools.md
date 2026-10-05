# Contract: Workspace Tools

**Frozen at Phase 00**  
**Source:** tools/workspace_tools.py, core/tool_policy.py, core/tools.py

## 1. Tool Permission Model (core/tools.py)

```python
class ToolPermission(StrEnum):
    READ    = "read"
    WRITE   = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    ADMIN   = "admin"

@dataclass(frozen=True)
class ToolDefinition:
    name:        str
    description: str
    permissions: frozenset[ToolPermission] = frozenset()
    schema:      dict[str, Any] = field(default_factory=dict)
```

## 2. Tool Policy Model (core/tool_policy.py)

```python
class ToolDecision(StrEnum):
    ALLOW           = "allow"
    DENY            = "deny"
    REQUIRE_APPROVAL = "require_approval"

@dataclass(frozen=True)
class ToolPolicyResult:
    decision: ToolDecision
    reason:   str
```

## 3. Role-Based Tool Access (core/agent_role.py -> RolePolicyEngine)

Read-only roles (PLANNER, ARCHITECT, RESEARCHER, TESTER, SECURITY_REVIEWER, REVIEWER):
- DENY all tools with ToolPermission.WRITE

Coder roles (BACKEND_CODER, UI_CODER):
- ALLOW: view_file, list_dir, grep_search, code_context,
         replace_file_content, multi_replace_file_content, write_to_file, run_command
- DENY: delete_path, drop_database, drop_table

## 4. Workspace Tool Categories

### Read-only tools (auto-authorized for all roles)

- view_file
- list_dir
- grep_search
- code_context
- find_code_symbol
- code_dependencies
- code_dependents
- impact_analysis

### Write tools (BACKEND_CODER, UI_CODER only)

- replace_file_content
- multi_replace_file_content
- write_to_file

### Execute tools (BACKEND_CODER, UI_CODER, TESTER, SECURITY_REVIEWER, REVIEWER)

- run_command

### Destructive tools (ALL roles DENIED)

- delete_path
- drop_database
- drop_table

## 5. Approval Gate (core/control_plane.py)

Tools requiring human approval:
- Registered via MyAgentControlPlane.register_pending_approval()
- Stored in PendingApprovalStore (durable: SQLitePendingApprovalStore)
- Resolved via MyAgentControlPlane.resolve_user_approval()

## Contract Notes for Future Phases

- DO NOT bypass RolePolicyEngine for new tool categories
- Destructive tool list MUST be maintained in parallel with RolePolicyEngine.ROLE_RULES
- Approval store injection pattern MUST remain consistent
- New tools added to workspace_tools.py MUST have ToolDefinition with explicit permissions
