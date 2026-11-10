// AgentPort extension. Original application shell/auth: ContextForge Web UI (Apache-2.0).
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { GraphEditor } from "./orchestration/GraphEditor";
import { InvokeForm } from "./orchestration/InvokeForm";
import {
  base,
  box,
  errorText,
  selectClass,
  serialize,
  type Entity,
  type Json,
  type Run,
  type Session,
} from "./orchestration/shared";

function download(name: string, value: unknown, plain = false) {
  const url = URL.createObjectURL(
    new Blob([plain ? String(value) : serialize(value)], {
      type: plain ? "text/plain" : "application/json",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function Orchestration() {
  const [tab, setTab] = useState("services");
  const [services, setServices] = useState<Entity[]>([]);
  const [agents, setAgents] = useState<Entity[]>([]);
  const [workflows, setWorkflows] = useState<Entity[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [admin, setAdmin] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<Entity | null>(null);
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [editor, setEditor] = useState<Entity | "new" | null>(null);
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [registration, setRegistration] = useState(
    serialize({
      schema_version: 1,
      name: "New service",
      kind: "tool",
      transport: "http",
      endpoint: "http://converter:8080/convert",
      lifecycle: "manual",
      egress_policy: "isolated",
      input_schema: {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"],
      },
    }),
  );
  const [agentName, setAgentName] = useState("");
  const [agentService, setAgentService] = useState("");
  const [persona, setPersona] = useState("");
  const [credentials, setCredentials] = useState<{ id: string; name: string }[]>([]);
  const [credentialRef, setCredentialRef] = useState("");
  const [secretName, setSecretName] = useState("");
  const [secretToken, setSecretToken] = useState("");
  const [editingService, setEditingService] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const [allowedTools, setAllowedTools] = useState<string[]>([]);
  const [toolBudget, setToolBudget] = useState(20);

  const refresh = useCallback(async () => {
    const [s, a, w, r, me, ss, cs] = await Promise.all([
      api.get<Entity[]>(`${base}/services`),
      api.get<Entity[]>(`${base}/agents`),
      api.get<Entity[]>(`${base}/workflows`),
      api.get<Run[]>(`${base}/runs`),
      api.get<{ is_admin: boolean }>(`${base}/me`),
      api.get<Session[]>(`${base}/sessions`),
      api.get<{ id: string; name: string }[]>(`${base}/credentials`),
    ]);
    setServices(s);
    setAgents(a);
    setWorkflows(w);
    setRuns(r);
    setAdmin(me.is_admin);
    setSessions(ss);
    setCredentials(cs);
  }, []);
  useEffect(() => {
    void refresh().catch((e) => setError(errorText(e)));
  }, [refresh]);
  const selectedRunId = selectedRun?.id;
  useEffect(() => {
    if (tab !== "runs") return;
    const timer = setInterval(() => {
      void api
        .get<Run[]>(`${base}/runs`)
        .then(setRuns)
        .catch((e) => setError(errorText(e)));
      if (selectedRunId !== undefined)
        void api
          .get<Run>(`${base}/runs/${selectedRunId}`)
          .then(setSelectedRun)
          .catch((e) => setError(errorText(e)));
    }, 2500);
    return () => clearInterval(timer);
  }, [tab, selectedRunId]);
  async function action(work: () => Promise<unknown>, message = "Saved") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      await refresh();
      setNotice(message);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-7xl space-y-6 p-4 md:p-7">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
            Independent AI Gateway
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">AgentPort</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your tools, agents and workflows. One place to run them.
          </p>
        </div>
        <Button variant="outline" onClick={() => void action(refresh, "Updated")}>
          Refresh
        </Button>
      </div>
      <nav aria-label="Orchestration sections" className="flex flex-wrap gap-2">
        {[
          ["services", "Services"],
          ["agents", "Agents & sessions"],
          ["workflows", "Workflows"],
          ["runs", "Activity"],
        ].map(([id, label]) => (
          <Button
            key={id}
            variant={tab === id ? "default" : "outline"}
            onClick={() => {
              setTab(id);
              setSelected(null);
            }}
          >
            {label}
          </Button>
        ))}
      </nav>
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-400 bg-red-50 p-3 text-sm text-red-900 whitespace-pre-wrap"
        >
          {error}
        </div>
      )}
      {notice && (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      )}
      {tab === "services" && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold">Available services</h2>
            {admin && (
              <Button
                onClick={() => {
                  setEditingService(null);
                  setRegister(!register);
                }}
              >
                Register service
              </Button>
            )}
          </div>
          {register && (
            <form
              className={box}
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  if (editingService)
                    await api.put(`${base}/services/${editingService}`, JSON.parse(registration));
                  else await api.post(`${base}/services`, JSON.parse(registration));
                  setRegister(false);
                });
              }}
            >
              <label className="block text-sm font-medium">
                Service definition
                <Textarea
                  className="mt-2 min-h-64 font-mono"
                  value={registration}
                  onChange={(e) => setRegistration(e.target.value)}
                />
              </label>
              <p className="my-3 text-xs text-muted-foreground">
                Operator only. Mark a service isolated only after its deployment blocks external
                egress.
              </p>
              <Button disabled={busy}>{editingService ? "Save definition" : "Register"}</Button>
            </form>
          )}
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {services.map((s) => (
              <article className={box} key={s.id}>
                <div className="flex justify-between gap-2">
                  <h3 className="font-semibold">{s.name}</h3>
                  <span className="rounded bg-muted px-2 py-1 text-xs">{s.kind}</span>
                </div>
                <p className="mt-3 text-sm text-muted-foreground">
                  {admin
                    ? s.lifecycle === "manual"
                      ? "Managed externally"
                      : `${s.lifecycle?.replace(/_/g, " ")} · ${s.runtime_phase}`
                    : "Send a request and receive a result"}
                </p>
                {s.openshell && (
                  <p className="mt-2 text-xs text-muted-foreground">OpenShell sandbox</p>
                )}
                {admin && s.openshell && <RuntimeStatus service={s} />}
                <div className="mt-5 flex flex-wrap gap-2">
                  <Button onClick={() => setSelected(s)}>Use service</Button>
                  {admin && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        void action(async () => {
                          const value = await api.get<{ definition: Json }>(
                            `${base}/services/${s.id}/export`,
                          );
                          setRegistration(serialize(value.definition));
                          setEditingService(s.id);
                          setRegister(true);
                        }, "Editing service definition")
                      }
                    >
                      Configure
                    </Button>
                  )}
                  {admin && (s.stack_id || s.openshell)
                    ? (s.openshell ? ["start", "stop"] : ["start", "stop", "update"]).map((op) => (
                        <Button
                          key={op}
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            void action(
                              () => api.post(`${base}/services/${s.id}/operations/${op}`, {}),
                              `Deployment ${op} completed`,
                            )
                          }
                        >
                          {op}
                        </Button>
                      ))
                    : null}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void action(
                        async () =>
                          download(
                            `${s.name}.json`,
                            await api.get(`${base}/services/${s.id}/export`),
                          ),
                        "Exported",
                      )
                    }
                  >
                    Export
                  </Button>
                </div>
                {admin &&
                  s.openshell &&
                  ["error", "starting", "stopping", "updating"].includes(s.runtime_phase || "") && (
                    <RuntimeReconcile service={s} busy={busy} action={action} />
                  )}
                {admin && s.stack_id ? (
                  <div className="mt-3 space-y-2">
                    <label className="block text-xs">
                      Deploy registered release
                      <select
                        className={selectClass}
                        value=""
                        disabled={busy}
                        onChange={(e) => {
                          if (e.target.value)
                            void action(
                              () =>
                                api.post(
                                  `${base}/services/${s.id}/releases/${encodeURIComponent(e.target.value)}/deploy`,
                                  {},
                                ),
                              "Release operation completed; check current runtime",
                            );
                        }}
                      >
                        <option value="">Choose release</option>
                        {Object.keys((s.releases || {}) as Json).map((v) => (
                          <option key={v}>{v}</option>
                        ))}
                      </select>
                    </label>
                    {["error", "starting", "stopping", "updating"].includes(
                      s.runtime_phase || "",
                    ) && <RuntimeReconcile service={s} busy={busy} action={action} />}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
          {!services.length && (
            <p className={box}>
              No services are available yet. An operator can register a service definition.
            </p>
          )}
        </>
      )}
      {tab === "agents" && (
        <>
          <h2 className="text-xl font-semibold">Agents & sessions</h2>
          <details className={box}>
            <summary className="cursor-pointer text-sm font-medium">
              Personal provider credentials
            </summary>
            <form
              className="mt-3 flex flex-wrap gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  const value = await api.post<{ id: string }>(`${base}/credentials`, {
                    name: secretName,
                    token: secretToken,
                  });
                  setCredentialRef(value.id);
                  setSecretToken("");
                  setSecretName("");
                }, "Credential stored. The value cannot be read back.");
              }}
            >
              <Input
                aria-label="Credential name"
                placeholder="Name"
                value={secretName}
                onChange={(e) => setSecretName(e.target.value)}
                required
              />
              <Input
                aria-label="Provider API key"
                type="password"
                autoComplete="off"
                value={secretToken}
                onChange={(e) => setSecretToken(e.target.value)}
                required
              />
              <Button disabled={busy}>Save personal credential</Button>
            </form>
          </details>
          <form
            className={box + " grid gap-3 md:grid-cols-2"}
            onSubmit={(e) => {
              e.preventDefault();
              void action(() =>
                api.post(`${base}/agents`, {
                  name: agentName,
                  service_id: agentService,
                  credential_ref: credentialRef || null,
                  persona,
                  model: model || null,
                  allowed_service_ids: allowedTools,
                  max_tool_calls: toolBudget,
                }),
              );
            }}
          >
            <label className="text-sm">
              Name
              <Input required value={agentName} onChange={(e) => setAgentName(e.target.value)} />
            </label>
            <label className="text-sm">
              Runtime
              <select
                required
                className={selectClass}
                value={agentService}
                onChange={(e) => setAgentService(e.target.value)}
              >
                <option value="">Select an authorized runtime</option>
                {services
                  .filter((s) => s.kind === "agent")
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="text-sm md:col-span-2">
              Persona (optional)
              <Textarea value={persona} onChange={(e) => setPersona(e.target.value)} />
            </label>
            <label className="text-sm">
              Model (optional)
              <Input value={model} onChange={(e) => setModel(e.target.value)} />
            </label>
            <label className="text-sm">
              Personal credential
              <select
                className={selectClass}
                value={credentialRef}
                onChange={(e) => setCredentialRef(e.target.value)}
              >
                <option value="">Worker-managed authentication</option>
                {credentials.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Tool-call budget
              <Input
                type="number"
                min={0}
                max={1000}
                value={toolBudget}
                onChange={(e) => setToolBudget(Number(e.target.value))}
              />
            </label>
            <fieldset className="text-sm md:col-span-2">
              <legend className="mb-2">Allowed gateway tools</legend>
              {services
                .filter((s) => s.kind !== "agent")
                .map((s) => (
                  <label key={s.id} className="mr-4 inline-flex gap-2">
                    <input
                      type="checkbox"
                      checked={allowedTools.includes(s.id)}
                      onChange={(e) =>
                        setAllowedTools((v) =>
                          e.target.checked ? [...v, s.id] : v.filter((id) => id !== s.id),
                        )
                      }
                    />
                    {s.name}
                  </label>
                ))}
              <p className="mt-2 text-xs text-muted-foreground">
                The gateway also checks your current permissions. Native harness tools stay inside
                the worker container.
              </p>
            </fieldset>
            <Button disabled={busy || !agentService}>Create agent</Button>
          </form>
          <div className="grid gap-4 md:grid-cols-2">
            {agents.map((a) => (
              <article key={a.id} className={box}>
                <h3 className="font-semibold">{a.name}</h3>
                <p className="my-3 text-sm text-muted-foreground">
                  {String(a.persona || "Native agent instructions")}
                </p>
                <Button onClick={() => setSelected(a)}>Start or resume work</Button>
              </article>
            ))}
          </div>
        </>
      )}
      {tab === "workflows" && (
        <>
          <div className="flex justify-between">
            <h2 className="text-xl font-semibold">Your workflows</h2>
            <Button onClick={() => setEditor("new")}>New workflow</Button>
          </div>
          {editor && (
            <GraphEditor
              key={typeof editor === "string" ? editor : editor.id}
              initial={editor === "new" ? null : editor}
              services={services}
              agents={agents}
              onClose={() => setEditor(null)}
              onSave={async (definition, id) => {
                await action(async () => {
                  if (id) await api.put(`${base}/workflows/${id}`, definition);
                  else await api.post(`${base}/workflows`, definition);
                  setEditor(null);
                });
              }}
            />
          )}
          <div className="grid gap-4 md:grid-cols-2">
            {workflows.map((w) => (
              <article key={w.id} className={box}>
                <h3 className="font-semibold">{w.name}</h3>
                <p className="my-3 text-sm text-muted-foreground">
                  Revision {w.revision} · {w.nodes?.length} steps ·{" "}
                  {w.local_only ? "Local only" : "Cloud steps require release"}
                </p>
                <div className="flex gap-2">
                  <Button onClick={() => setSelected(w)}>Run</Button>
                  <Button variant="outline" onClick={() => setEditor(w)}>
                    Edit graph
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void action(
                        async () =>
                          download(
                            `${w.name}.json`,
                            await api.get(`${base}/workflows/${w.id}/export`),
                          ),
                        "Exported",
                      )
                    }
                  >
                    Export
                  </Button>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
      {selected && (
        <InvokeForm
          entity={selected}
          sessions={sessions}
          busy={busy}
          onClose={() => setSelected(null)}
          onSubmit={(values) =>
            action(async () => {
              const collection =
                selected.kind === "workflow"
                  ? "workflows"
                  : selected.kind === "agent" && tab === "agents"
                    ? "agents"
                    : "services";
              const run = await api.post<Run>(
                `${base}/${collection}/${selected.id}/invoke`,
                values,
              );
              setSelectedRun(run);
              setSelected(null);
              setTab("runs");
            }, "Job accepted. You can leave this page and return later.")
          }
        />
      )}
      {tab === "runs" && (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <div className="space-y-2">
            {runs.map((r) => (
              <button
                key={r.id}
                className={box + " w-full text-left hover:border-primary"}
                onClick={() =>
                  void action(
                    async () => setSelectedRun(await api.get<Run>(`${base}/runs/${r.id}`)),
                    "",
                  )
                }
              >
                <span className="block font-medium">{r.name}</span>
                <span className="mt-2 block text-sm text-muted-foreground">
                  {r.status.replace(/_/g, " ")}
                </span>
              </button>
            ))}
            {!runs.length && <p className={box}>Your jobs will appear here.</p>}
          </div>
          {selectedRun && (
            <article className={box + " space-y-4 min-w-0"}>
              <div className="flex flex-wrap justify-between gap-2">
                <h2 className="text-xl font-semibold">{selectedRun.name}</h2>
                <span className="text-sm">{selectedRun.status.replace(/_/g, " ")}</span>
              </div>
              {!["completed", "failed", "cancelled"].includes(selectedRun.status) && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void action(
                      () => api.post(`${base}/runs/${selectedRun.id}/cancel`, {}),
                      "Cancellation requested",
                    )
                  }
                >
                  Request cancellation
                </Button>
              )}
              {selectedRun.steps?.map((s) => (
                <section key={s.id} className="rounded-lg border p-4">
                  <div className="flex justify-between">
                    <strong>{s.id}</strong>
                    <span className="text-sm">{s.status.replace(/_/g, " ")}</span>
                  </div>
                  <RelayActivity output={s.output.value} />
                  <details open={s.status === "waiting_approval"} className="my-3">
                    <summary className="cursor-pointer text-xs text-muted-foreground">
                      {s.status === "waiting_approval" ? "Payload to approve" : "Step details"}
                    </summary>
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {serialize(s.output)}
                    </pre>
                  </details>
                  {s.status === "waiting_approval" && (
                    <div className="space-y-2">
                      <p className="text-sm">
                        Review the exact payload above before approving
                        {Boolean(s.output.release) ? " its release to a cloud step" : ""}.
                      </p>
                      <div className="flex gap-2">
                        {[true, false].map((approved) => (
                          <Button
                            key={String(approved)}
                            variant={approved ? "default" : "outline"}
                            disabled={busy}
                            onClick={() =>
                              void action(
                                () =>
                                  api.post(`${base}/runs/${selectedRun.id}/steps/${s.id}/approve`, {
                                    approved,
                                  }),
                                "Decision recorded",
                              )
                            }
                          >
                            {approved ? "Approve" : "Decline"}
                          </Button>
                        ))}
                      </div>
                    </div>
                  )}
                  {s.status === "needs_attention" && (
                    <p className="text-sm">
                      An operator must verify the external execution before resuming. This job will
                      not be replayed automatically.
                    </p>
                  )}
                  {s.status === "needs_attention" && admin && (
                    <StepReconcile run={selectedRun.id} step={s.id} action={action} busy={busy} />
                  )}
                </section>
              ))}
              {selectedRun.result && (
                <>
                  <details>
                    <summary className="cursor-pointer text-sm text-muted-foreground">
                      Structured result
                    </summary>
                    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-3 text-xs">
                      {serialize(selectedRun.result)}
                    </pre>
                  </details>
                  <Button
                    onClick={() => download(`result-${selectedRun.id}.json`, selectedRun.result)}
                  >
                    Download result
                  </Button>
                </>
              )}
              {selectedRun.result &&
                Object.entries(selectedRun.result).map(([id, output]) => {
                  const file = output as { filename?: string; text?: string } | null;
                  return file?.filename && typeof file.text === "string" ? (
                    <Button
                      key={id}
                      variant="outline"
                      onClick={() => download(file.filename!, file.text, true)}
                    >
                      Download {file.filename}
                    </Button>
                  ) : null;
                })}
            </article>
          )}
        </div>
      )}
    </section>
  );
}

type Action = (work: () => Promise<unknown>, message?: string) => Promise<void>;
function RuntimeReconcile({
  service,
  busy,
  action,
}: {
  service: Entity;
  busy: boolean;
  action: Action;
}) {
  const [verified, setVerified] = useState(false);
  return (
    <div className="space-y-2">
      <label className="flex gap-2 text-xs">
        <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
        I verified the deployment state in its runtime backend
      </label>
      {["running", "stopped"].map((phase) => (
        <Button
          key={phase}
          size="sm"
          variant="outline"
          disabled={!verified || busy}
          onClick={() =>
            void action(
              () =>
                api.post(`${base}/services/${service.id}/reconcile`, {
                  phase,
                  deployment_verified: true,
                }),
              "Runtime reconciled",
            )
          }
        >
          Confirm {phase}
        </Button>
      ))}
    </div>
  );
}

function StepReconcile({
  run,
  step,
  busy,
  action,
}: {
  run: string;
  step: string;
  busy: boolean;
  action: Action;
}) {
  const [output, setOutput] = useState("{}");
  const [verified, setVerified] = useState(false);
  return (
    <div className="space-y-2">
      <label className="block text-xs">
        Verified external output
        <Textarea
          className="font-mono"
          value={output}
          onChange={(e) => setOutput(e.target.value)}
        />
      </label>
      <label className="flex gap-2 text-xs">
        <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
        I verified the external execution has stopped
      </label>
      {["completed", "failed"].map((outcome) => (
        <Button
          key={outcome}
          size="sm"
          variant="outline"
          disabled={!verified || busy}
          onClick={() =>
            void action(
              () =>
                api.post(`${base}/runs/${run}/steps/${step}/resolve`, {
                  outcome,
                  output: JSON.parse(output),
                  external_execution_stopped: true,
                }),
              "Execution reconciled",
            )
          }
        >
          Confirm {outcome}
        </Button>
      ))}
    </div>
  );
}

function RuntimeStatus({ service }: { service: Entity }) {
  const [status, setStatus] = useState("");
  const [pending, setPending] = useState(false);
  return (
    <div className="mt-2 space-y-2">
      <Button
        size="sm"
        variant="ghost"
        disabled={pending}
        onClick={async () => {
          setPending(true);
          try {
            const value = await api.get<{ phase: string; policy_version: number }>(
              `${base}/services/${service.id}/runtime`,
            );
            setStatus(
              `${value.phase.replace("SANDBOX_PHASE_", "")} · policy ${value.policy_version}`,
            );
          } catch (error) {
            setStatus(errorText(error));
          } finally {
            setPending(false);
          }
        }}
      >
        Check sandbox
      </Button>
      {status && (
        <p role="status" className="text-xs">
          {status}
        </p>
      )}
    </div>
  );
}

function RelayActivity({ output }: { output: unknown }) {
  const value = (
    output as {
      observability?: {
        source?: string;
        available?: boolean;
        counts?: Record<string, { started: number; ended: number }>;
        truncated?: boolean;
        rotated?: boolean;
      };
    } | null
  )?.observability;
  if (value?.source !== "nemo-relay") return null;
  return (
    <div className="my-3 rounded border p-3 text-sm">
      <strong>NeMo Relay activity</strong>
      {!value.available ? (
        <p>No local Relay records available.</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            Worker observation window. Counts do not confirm task success.
          </p>
          <div className="mt-2 flex flex-wrap gap-3">
            {["agent", "turn", "tool", "llm"].map((category) => (
              <span key={category}>
                {category}: {Number(value.counts?.[category]?.started || 0)} started /{" "}
                {Number(value.counts?.[category]?.ended || 0)} ended
              </span>
            ))}
          </div>
          {(value.truncated || value.rotated) && (
            <p>Partial record window; inspect the worker locally.</p>
          )}
        </>
      )}
    </div>
  );
}
