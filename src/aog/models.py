from typing import Any, Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    project_id: str | None = None  # ContextForge team ID, never a client-supplied identity.
    visibility: Literal["private", "project"] = "private"

    @model_validator(mode="after")
    def sharing(self):
        if self.visibility == "project" and not self.project_id:
            raise ValueError("Project sharing requires a ContextForge team")
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

    def sees(self, owner: str, project: str | None, visibility: str) -> bool:
        return (
            self.is_admin
            or self.subject == owner
            or (visibility == "project" and project is not None and project in self.teams)
        )

    def scope_allows(self, permission: str) -> bool:
        return not self.scopes or "*" in self.scopes or permission in self.scopes
