from typing import Any, Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    project_id: str | None = None  # Opaque project ID from the selected identity backend.
    visibility: Literal["private", "project"] = "private"

    @model_validator(mode="after")
    def sharing(self):
        if self.visibility == "project" and not self.project_id:
            raise ValueError("Project sharing requires a project ID")
        return self


class OpenShellTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gateway: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    workspace: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    sandbox: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    sandbox_id: str = Field(min_length=1, max_length=128)


class Service(Definition):
    enabled: bool = True
    kind: Literal["tool", "model", "agent", "paperclip"] = "tool"
    transport: Literal["http", "mcp", "openai", "acp", "paperclip"] = "http"
    endpoint: str
    http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    accepts_identity: bool = False  # Trusted ACL-aware service; never set for a public model endpoint.
    tool_name: str | None = None
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict = Field(default_factory=dict)
    lifecycle: Literal["always_on", "on_demand", "manual"] = "manual"
    stack_id: str | None = None
    openshell: OpenShellTarget | None = None
    releases: dict[str, str] = Field(default_factory=dict)  # Operator-registered immutable Komodo stacks.
    current_release: str | None = None
    rollback_safe: bool = False  # Only stateless or explicitly backward-compatible deployments.
    health_url: str | None = None
    idle_seconds: int = Field(default=300, ge=10, le=86400)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    concurrency: int = Field(default=1, ge=1, le=64)
    location: Literal["local", "cloud"] = "local"
    egress_policy: Literal["isolated", "unrestricted"] = "unrestricted"
    credential_ref: str | None = None
    idempotent: bool = False

    @field_validator("endpoint", "health_url")
    @classmethod
    def safe_url(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError("Only HTTP(S) endpoints without embedded credentials are accepted")
        return value

    @field_validator("input_schema", "output_schema")
    @classmethod
    def schema(cls, value):
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError:
            raise ValueError("Invalid JSON Schema") from None
        return value

    @model_validator(mode="after")
    def runtime(self):
        if self.openshell and (self.stack_id or self.releases or self.current_release or self.rollback_safe):
            raise ValueError("OpenShell and Komodo deployment settings cannot be combined")
        if self.lifecycle != "manual" and (not (self.stack_id or self.openshell) or not self.health_url):
            raise ValueError("Managed services require a deployment target and health_url")
        if self.transport == "mcp" and not self.tool_name:
            raise ValueError("MCP services require tool_name")
        if self.kind == "agent" and self.transport != "acp":
            raise ValueError("Agent services use an ACP worker")
        return self


class Agent(Definition):
    service_id: str
    credential_ref: str | None = None
    persona: str = Field(default="", max_length=20000)
    model: str | None = None
    allowed_service_ids: list[str] = Field(default_factory=list, max_length=100)
    max_tool_calls: int = Field(default=20, ge=0, le=1000)


class RuntimeDefinition(Definition):
    """Operator-owned execution target shared by one or more native gateway tools."""

    schema_version: Literal[2] = 2
    backend: Literal["external", "komodo", "openshell"] = "external"
    backend_ref: Literal["default"] | None = None
    target_id: str | None = None
    openshell: OpenShellTarget | None = None
    enabled: bool = True
    lifecycle: Literal["always_on", "on_demand", "manual"] = "manual"
    health_url: str | None = None
    idle_seconds: int = Field(default=300, ge=10, le=86400)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    concurrency: int = Field(default=1, ge=1, le=64)
    location: Literal["local", "cloud"] = "local"
    egress_policy: Literal["isolated", "unrestricted"] = "unrestricted"
    releases: dict[str, str] = Field(default_factory=dict)
    current_release: str | None = None
    rollback_safe: bool = False

    _safe_health = field_validator("health_url")(Service.safe_url.__func__)

    @model_validator(mode="after")
    def target(self):
        if self.backend == "external":
            if (
                self.backend_ref or self.target_id or self.openshell or self.lifecycle != "manual"
                or self.releases or self.current_release or self.rollback_safe
            ):
                raise ValueError("External runtimes are operator-managed and have no native deployment target")
        elif self.backend == "komodo":
            if not self.target_id or self.openshell:
                raise ValueError("Komodo runtimes require target_id and cannot specify an OpenShell target")
        elif (
            not self.openshell or self.backend_ref or self.target_id or self.releases
            or self.current_release or self.rollback_safe
        ):
            raise ValueError("OpenShell runtimes require an OpenShell target and no Komodo release settings")
        if self.lifecycle != "manual" and not self.health_url:
            raise ValueError("Managed runtimes require health_url")
        return self

    def service_projection(self, service: Service) -> Service:
        """Apply execution settings without persisting a second copy of a gateway tool."""
        return service.model_copy(
            update={
                "enabled": self.enabled and service.enabled,
                "lifecycle": self.lifecycle,
                "stack_id": self.target_id if self.backend == "komodo" else None,
                "openshell": self.openshell,
                "health_url": self.health_url,
                "idle_seconds": self.idle_seconds,
                "timeout_seconds": self.timeout_seconds,
                "concurrency": self.concurrency,
                "location": self.location,
                "egress_policy": self.egress_policy,
                "releases": self.releases,
                "current_release": self.current_release,
                "rollback_safe": self.rollback_safe,
            }
        )


class ProviderConnection(Definition):
    """A protocol-specific provider connection; secrets remain references."""

    schema_version: Literal[2] = 2
    protocol: Literal["chat_completions", "responses", "messages"]
    base_url: str
    credential_ref: str = Field(min_length=1, max_length=200)
    model: str | None = None
    location: Literal["local", "cloud"] = "cloud"

    _safe_base_url = field_validator("base_url")(Service.safe_url.__func__)


class AgentProfile(Definition):
    schema_version: Literal[2] = 2
    harness_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    runtime_id: str = Field(min_length=1, max_length=200)
    provider_connection_id: str | None = None
    native_login_ref: str | None = None
    persona: str = Field(default="", max_length=20000)
    model: str | None = None
    allowed_tool_ids: list[str] = Field(default_factory=list, max_length=100)
    max_tool_calls: int = Field(default=20, ge=0, le=1000)

    @model_validator(mode="after")
    def authentication(self):
        if bool(self.provider_connection_id) == bool(self.native_login_ref):
            raise ValueError("Select exactly one provider connection or native login reference")
        return self


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["tool", "model", "agent", "approval", "condition", "join", "review"]
    target: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    when: dict[str, Any] | None = None
    session_id: str | None = None
    reviewer: str | None = None
    max_rounds: int = Field(default=3, ge=1, le=10)
    release: bool = False
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})


class Workflow(Definition):
    schema_version: Literal[1, 2] = 2
    nodes: list[Node] = Field(min_length=1, max_length=100)
    local_only: bool = True

    @model_validator(mode="after")
    def graph(self):
        from aog.graph import layers

        layers(self.nodes)
        return self


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    local_only: bool = True
    allow_cloud: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["completed", "failed"]
    output: Any = None
    external_execution_stopped: Literal[True]


class Principal(BaseModel):
    subject: str
    teams: list[str] = Field(default_factory=list)
    is_admin: bool = False
    scopes: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    parent_run_id: str | None = None
    allowed_service_ids: list[str] | None = None
    server_id: str | None = None
    token_server_id: str | None = None
    token_id: str | None = None
    token_use: str | None = None
    auth_method: str | None = None
    token_expires_at: int | None = None
    team_scope_restricted: bool = False

    @property
    def public_only(self):
        return self.team_scope_restricted and not self.teams and not self.is_admin

    def sees(self, owner: str, project: str | None, visibility: str) -> bool:
        if self.public_only:
            return visibility == "public"
        return (
            self.is_admin
            or self.subject == owner
            or (visibility == "project" and project is not None and project in self.teams)
        )

    def scope_allows(self, permission: str) -> bool:
        return not self.scopes or "*" in self.scopes or permission in self.scopes or any(
            scope.endswith(".*") and permission.startswith(scope[:-1]) for scope in self.scopes
        )
