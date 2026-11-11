import { useState } from "react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import {
  box,
  errorText,
  selectClass,
  serialize,
  type Credential,
  type Entity,
  type Harness,
  type Json,
} from "./shared";

type Save = { busy: boolean; onSave: (definition: Json) => Promise<unknown> };

export function RuntimeForm({ initial, busy, onSave }: Save & { initial?: Entity | null }) {
  const [name, setName] = useState(initial?.name || "");
  const [backend, setBackend] = useState(initial?.backend || "external");
  const [target, setTarget] = useState(initial?.target_id || "");
  const [gateway, setGateway] = useState(initial?.openshell?.gateway || "");
  const [workspace, setWorkspace] = useState(initial?.openshell?.workspace || "");
  const [sandbox, setSandbox] = useState(initial?.openshell?.sandbox || "");
  const [sandboxId, setSandboxId] = useState(initial?.openshell?.sandbox_id || "");
  const [policy, setPolicy] = useState(initial?.lifecycle || "manual");
  const [health, setHealth] = useState(String(initial?.health_url || ""));
  const [concurrency, setConcurrency] = useState(Number(initial?.concurrency || 1));
  const [idle, setIdle] = useState(Number(initial?.idle_seconds || 300));
  const [project, setProject] = useState(String(initial?.project_id || ""));
  const [shared, setShared] = useState(initial?.visibility === "project");
  const [location, setLocation] = useState(String(initial?.location || "local"));
  const [isolated, setIsolated] = useState(initial?.egress_policy === "isolated");
  return (
    <form
      className={box + " grid gap-3 md:grid-cols-2"}
      onSubmit={(e) => {
        e.preventDefault();
        void onSave({
          schema_version: 2,
          name,
          backend,
          target_id: backend === "komodo" ? target : null,
          openshell:
            backend === "openshell" ? { gateway, workspace, sandbox, sandbox_id: sandboxId } : null,
          lifecycle: backend === "external" ? "manual" : policy,
          health_url: health || null,
          timeout_seconds: Number(initial?.timeout_seconds || 120),
          enabled: initial?.enabled !== false,
          concurrency,
          idle_seconds: idle,
          project_id: project || null,
          visibility: shared && project ? "project" : "private",
          location,
          egress_policy: isolated ? "isolated" : "unrestricted",
          releases: backend === "komodo" ? initial?.releases || {} : {},
          current_release: backend === "komodo" ? initial?.current_release || null : null,
          rollback_safe: backend === "komodo" && initial?.rollback_safe === true,
        });
      }}
    >
      <label className="text-sm">
        Runtime name
        <Input required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="text-sm">
        Execution backend
        <select
          className={selectClass}
          value={backend}
          onChange={(e) => setBackend(e.target.value)}
        >
          <option value="external">Operator-managed service</option>
          <option value="komodo">Komodo stack</option>
          <option value="openshell">OpenShell sandbox</option>
        </select>
      </label>
      {backend === "komodo" && (
        <label className="text-sm">
          Komodo stack ID
          <Input required value={target} onChange={(e) => setTarget(e.target.value)} />
        </label>
      )}
      {backend === "openshell" && (
        <>
          <label className="text-sm">
            Gateway configuration name
            <Input required value={gateway} onChange={(e) => setGateway(e.target.value)} />
          </label>
          <label className="text-sm">
            OpenShell workspace
            <Input required value={workspace} onChange={(e) => setWorkspace(e.target.value)} />
          </label>
          <label className="text-sm">
            Sandbox name
            <Input required value={sandbox} onChange={(e) => setSandbox(e.target.value)} />
          </label>
          <label className="text-sm">
            Sandbox ID
            <Input required value={sandboxId} onChange={(e) => setSandboxId(e.target.value)} />
          </label>
        </>
      )}
      {backend !== "external" && (
        <label className="text-sm">
          Run policy
          <select
            className={selectClass}
            value={policy}
            onChange={(e) => setPolicy(e.target.value)}
          >
            <option value="manual">Operator starts and stops</option>
            <option value="always_on">Always on</option>
            <option value="on_demand">Start on request</option>
          </select>
        </label>
      )}
      <label className="text-sm">
        Readiness URL
        <Input
          type="url"
          required={backend !== "external" && policy !== "manual"}
          value={health}
          onChange={(e) => setHealth(e.target.value)}
        />
      </label>
      <label className="text-sm">
        Concurrent jobs
        <Input
          type="number"
          min={1}
          max={64}
          required
          value={concurrency}
          onChange={(e) => setConcurrency(Number(e.target.value))}
        />
      </label>
      {policy === "on_demand" && backend !== "external" && (
        <label className="text-sm">
          Idle timeout (seconds)
          <Input
            type="number"
            min={10}
            max={86400}
            value={idle}
            onChange={(e) => setIdle(Number(e.target.value))}
          />
        </label>
      )}
      <label className="text-sm">
        Project team ID (optional)
        <Input required={shared} value={project} onChange={(e) => setProject(e.target.value)} />
      </label>
      <label className="flex gap-2 text-sm">
        <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
        Share runtime with project members
      </label>
      <label className="text-sm">
        Data location
        <select
          className={selectClass}
          value={location}
          onChange={(e) => setLocation(e.target.value)}
        >
          <option value="local">Local infrastructure</option>
          <option value="cloud">Cloud service</option>
        </select>
      </label>
      <label className="flex gap-2 text-sm md:col-span-2">
        <input type="checkbox" checked={isolated} onChange={(e) => setIsolated(e.target.checked)} />
        Deployment network policy has been verified to block external egress
      </label>
      <p className="text-xs text-muted-foreground md:col-span-2">
        Choose an existing deployment. Tools bound to this runtime share its job capacity and busy
        protection.
      </p>
      <Button disabled={busy}>{initial ? "Save runtime" : "Register runtime"}</Button>
    </form>
  );
}

export function ProviderForm({
  credentials,
  initial,
  busy,
  onSave,
}: Save & { credentials: Credential[]; initial?: Entity | null }) {
  const [name, setName] = useState(initial?.name || "");
  const [protocol, setProtocol] = useState(initial?.protocol || "chat_completions");
  const [url, setUrl] = useState(initial?.base_url || "");
  const [credential, setCredential] = useState(String(initial?.credential_ref || ""));
  const [model, setModel] = useState(String(initial?.model || ""));
  return (
    <form
      className={box + " grid gap-3 md:grid-cols-2"}
      onSubmit={(e) => {
        e.preventDefault();
        void onSave({
          schema_version: 2,
          name,
          protocol,
          base_url: url,
          credential_ref: credential,
          model: model || null,
        });
      }}
    >
      <label className="text-sm">
        Connection name
        <Input required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="text-sm">
        Provider API
        <select
          className={selectClass}
          value={protocol}
          onChange={(e) => setProtocol(e.target.value)}
        >
          <option value="chat_completions">Chat Completions</option>
          <option value="responses">Responses</option>
          <option value="messages">Messages</option>
        </select>
      </label>
      <label className="text-sm">
        Provider base URL
        <Input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
      </label>
      <label className="text-sm">
        API credential
        <select
          required
          className={selectClass}
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
        >
          <option value="">Choose your credential</option>
          {credentials
            .filter((c) => c.kind !== "native_login")
            .map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
        </select>
      </label>
      <label className="text-sm">
        Default model (optional)
        <Input value={model} onChange={(e) => setModel(e.target.value)} />
      </label>
      <p className="text-xs text-muted-foreground md:col-span-2">
        The selected harness must support this API and authentication method. Subscription logins
        are configured separately on the agent profile.
      </p>
      <Button disabled={busy || !credential}>
        {initial ? "Save connection" : "Create provider connection"}
      </Button>
    </form>
  );
}

export function ServiceForm({
  runtimes,
  initial,
  busy,
  onSave,
}: Save & { runtimes: Entity[]; initial?: Entity | null }) {
  const [name, setName] = useState(initial?.name || "");
  const [endpoint, setEndpoint] = useState(String(initial?.endpoint || ""));
  const [transport, setTransport] = useState(String(initial?.transport || "http"));
  const [tool, setTool] = useState(String(initial?.tool_name || ""));
  const [runtime, setRuntime] = useState(String(initial?.runtime_id || ""));
  const [inputSchema, setInputSchema] = useState(
    serialize(
      initial?.input_schema || {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"],
      },
    ),
  );
  const [error, setError] = useState("");
  return (
    <form
      className={box + " grid gap-3 md:grid-cols-2"}
      onSubmit={(e) => {
        e.preventDefault();
        try {
          const input_schema = JSON.parse(inputSchema);
          setError("");
          void onSave({
            schema_version: 2,
            name,
            kind: initial?.kind || "tool",
            transport,
            endpoint,
            http_method: initial?.http_method || "POST",
            tool_name: transport === "mcp" ? tool : null,
            runtime_id: runtime || null,
            input_schema,
            output_schema: initial?.output_schema || {},
            accepts_identity: initial?.accepts_identity === true,
            idempotent: initial?.idempotent === true,
            credential_ref: initial?.credential_ref || null,
            project_id: initial?.project_id || null,
            visibility: initial?.visibility || "private",
          });
        } catch (err) {
          setError(errorText(err));
        }
      }}
    >
      <label className="text-sm">
        Service name
        <Input required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="text-sm">
        Connection type
        <select
          className={selectClass}
          value={transport}
          onChange={(e) => setTransport(e.target.value)}
        >
          <option value="http">HTTP API</option>
          <option value="mcp">MCP tool</option>
          {transport === "acp" && <option value="acp">Existing agent worker</option>}
        </select>
      </label>
      <label className="text-sm">
        Endpoint URL
        <Input type="url" required value={endpoint} onChange={(e) => setEndpoint(e.target.value)} />
      </label>
      {transport === "mcp" && (
        <label className="text-sm">
          MCP tool name
          <Input required value={tool} onChange={(e) => setTool(e.target.value)} />
        </label>
      )}
      <label className="text-sm">
        Runtime
        <select
          className={selectClass}
          value={runtime}
          onChange={(e) => setRuntime(e.target.value)}
        >
          <option value="">No managed runtime</option>
          {runtimes.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <details className="md:col-span-2">
        <summary className="cursor-pointer text-sm">Input schema</summary>
        <label className="block text-sm">
          Service input schema
          <Textarea
            className="min-h-40 font-mono"
            value={inputSchema}
            onChange={(e) => setInputSchema(e.target.value)}
          />
        </label>
      </details>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <Button disabled={busy}>{initial ? "Save definition" : "Register"}</Button>
    </form>
  );
}

export function AgentProfileForm({
  runtimes,
  providers,
  credentials,
  harnesses,
  services,
  busy,
  onSave,
}: Save & {
  runtimes: Entity[];
  providers: Entity[];
  credentials: Credential[];
  harnesses: Harness[];
  services: Entity[];
}) {
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState("");
  const [harness, setHarness] = useState("");
  const [mode, setMode] = useState("provider");
  const [connection, setConnection] = useState("");
  const [login, setLogin] = useState("");
  const [persona, setPersona] = useState("");
  const [model, setModel] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [budget, setBudget] = useState(20);
  return (
    <form
      className={box + " grid gap-3 md:grid-cols-2"}
      onSubmit={(e) => {
        e.preventDefault();
        void onSave({
          schema_version: 2,
          name,
          runtime_id: runtime,
          harness_key: harness,
          provider_connection_id: mode === "provider" ? connection : null,
          native_login_ref: mode === "native" ? login : null,
          persona,
          model: model || null,
          allowed_tool_ids: tools,
          max_tool_calls: budget,
        });
      }}
    >
      <label className="text-sm">
        Agent name
        <Input required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="text-sm">
        Harness
        <select
          required
          className={selectClass}
          value={harness}
          onChange={(e) => setHarness(e.target.value)}
        >
          <option value="">Select harness</option>
          {harnesses.map((h) => (
            <option key={h.key} value={h.key}>
              {h.name}
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        Runtime
        <select
          required
          className={selectClass}
          value={runtime}
          onChange={(e) => setRuntime(e.target.value)}
        >
          <option value="">Select an authorized runtime</option>
          {runtimes.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        Authentication
        <select className={selectClass} value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="provider">Provider API connection</option>
          <option value="native">Native harness login</option>
        </select>
      </label>
      {mode === "provider" ? (
        <label className="text-sm">
          Provider connection
          <select
            required
            className={selectClass}
            value={connection}
            onChange={(e) => setConnection(e.target.value)}
          >
            <option value="">Select your connection</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.protocol}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label className="text-sm">
          Native login reference
          <select
            required
            className={selectClass}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
          >
            <option value="">Select configured login</option>
            {credentials
              .filter((c) => c.kind === "native_login")
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </label>
      )}
      <label className="text-sm">
        Model (optional)
        <Input value={model} onChange={(e) => setModel(e.target.value)} />
      </label>
      <label className="text-sm md:col-span-2">
        Persona (optional)
        <Textarea value={persona} onChange={(e) => setPersona(e.target.value)} />
      </label>
      <label className="text-sm">
        Tool-call budget
        <Input
          type="number"
          min={0}
          max={1000}
          value={budget}
          onChange={(e) => setBudget(Number(e.target.value))}
        />
      </label>
      <fieldset className="text-sm md:col-span-2">
        <legend>Allowed gateway tools</legend>
        {services
          .filter((s) => s.kind !== "agent")
          .map((s) => (
            <label key={s.id} className="mr-4 inline-flex gap-2">
              <input
                type="checkbox"
                checked={tools.includes(s.id)}
                onChange={(e) =>
                  setTools((current) =>
                    e.target.checked ? [...current, s.id] : current.filter((id) => id !== s.id),
                  )
                }
              />
              {s.name}
            </label>
          ))}
      </fieldset>
      <Button disabled={busy || !runtime || !harness}>Create agent</Button>
    </form>
  );
}
