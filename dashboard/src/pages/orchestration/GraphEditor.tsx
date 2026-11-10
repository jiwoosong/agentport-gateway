import { useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Node as FlowNode,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import { box, selectClass, serialize, type Entity, type Json, type StepDef } from "./shared";

export function GraphEditor({
  initial,
  services,
  agents,
  onSave,
  onClose,
}: {
  initial: Entity | null;
  services: Entity[];
  agents: Entity[];
  onSave: (definition: Json, id?: string) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial?.name || "New workflow");
  const [local, setLocal] = useState(initial?.local_only !== false);
  const [defs, setDefs] = useState<StepDef[]>(initial?.nodes || []);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>(
    (initial?.nodes || []).map((n) => ({
      id: n.id,
      position: n.position,
      data: { label: `${n.id} · ${n.type}` },
    })),
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(
    (initial?.nodes || []).flatMap((n) =>
      n.depends_on.map((from) => ({ id: `${from}-${n.id}`, source: from, target: n.id })),
    ),
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [input, setInput] = useState("{}");
  const [when, setWhen] = useState("");
  const [error, setError] = useState("");
  const active = defs.find((n) => n.id === selected);
  const connect = useCallback(
    (value: Connection) => setEdges((e) => addEdge(value, e)),
    [setEdges],
  );
  function add(type: string) {
    const id = `${type}_${crypto.randomUUID().slice(0, 8)}`;
    const def = {
      id,
      type,
      depends_on: [],
      inputs: {},
      position: { x: 50 + defs.length * 30, y: 60 + defs.length * 70 },
    };
    setDefs((d) => [...d, def]);
    setNodes((n) => [...n, { id, position: def.position, data: { label: `${id} · ${type}` } }]);
    setSelected(id);
    setInput("{}");
    setWhen("");
  }
  function patch(value: Partial<StepDef>) {
    setDefs((d) => d.map((n) => (n.id === selected ? { ...n, ...value } : n)));
  }
  return (
    <div className={box + " space-y-4"}>
      <div className="flex flex-wrap gap-3">
        <Input
          aria-label="Workflow name"
          className="max-w-md"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button variant="outline" onClick={onClose}>
          Close editor
        </Button>
      </div>
      <label className="flex gap-2 text-sm">
        <input type="checkbox" checked={local} onChange={(e) => setLocal(e.target.checked)} />
        Local only
      </label>
      <div className="flex flex-wrap gap-2">
        {["tool", "model", "agent", "approval", "condition", "join", "review"].map((type) => (
          <Button key={type} size="sm" variant="outline" onClick={() => add(type)}>
            + {type}
          </Button>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="h-[480px] rounded-lg border bg-slate-50" aria-label="Workflow graph">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            onNodeClick={(_, n) => {
              const def = defs.find((d) => d.id === n.id);
              setSelected(n.id);
              setInput(serialize(def?.inputs || {}));
              setWhen(def?.when ? serialize(def.when) : "");
            }}
            fitView
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
        {active && (
          <div className="space-y-3 text-sm">
            <strong>{active.id}</strong>
            {["tool", "model", "agent", "review"].includes(active.type) && (
              <label className="block">
                Target
                <select
                  className={selectClass}
                  value={active.target || ""}
                  onChange={(e) => patch({ target: e.target.value })}
                >
                  <option value="">Select</option>
                  {(["agent", "review"].includes(active.type) ? agents : services).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {active.type === "review" && (
              <>
                <label className="block">
                  Reviewer
                  <select
                    className={selectClass}
                    value={active.reviewer || ""}
                    onChange={(e) => patch({ reviewer: e.target.value })}
                  >
                    <option value="">Select</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  Maximum rounds
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    value={active.max_rounds || 3}
                    onChange={(e) => patch({ max_rounds: Number(e.target.value) })}
                  />
                </label>
              </>
            )}
            {["approval", "review"].includes(active.type) && (
              <label className="flex gap-2">
                <input
                  type="checkbox"
                  checked={active.release || false}
                  onChange={(e) => patch({ release: e.target.checked })}
                />
                {active.type === "review"
                  ? "Ask approval before each cloud request"
                  : "Release approved inputs to cloud"}
              </label>
            )}
            {["agent", "review"].includes(active.type) && (
              <label className="block">
                Session ID (blank creates a session)
                <Input
                  value={active.session_id || ""}
                  onChange={(e) => patch({ session_id: e.target.value || undefined })}
                />
              </label>
            )}
            <label className="block">
              Input mapping
              <Textarea
                className="min-h-40 font-mono text-xs"
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  try {
                    patch({ inputs: JSON.parse(e.target.value) });
                    setError("");
                  } catch {
                    setError("Input mapping must be valid JSON");
                  }
                }}
              />
            </label>
            <p className="text-xs text-muted-foreground">
              Connect nodes before referencing their outputs. Example:{" "}
              {serialize({ text: { $ref: "input.text" } })}
            </p>
            <label className="block">
              Run condition (optional)
              <Textarea
                className="font-mono text-xs"
                value={when}
                placeholder='{"left":{"$ref":"nodes.check.matched"},"op":"eq","right":true}'
                onChange={(e) => {
                  setWhen(e.target.value);
                  try {
                    patch({ when: e.target.value.trim() ? JSON.parse(e.target.value) : undefined });
                    setError("");
                  } catch {
                    setError("Run condition must be valid JSON");
                  }
                }}
              />
            </label>
          </div>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <Button
        disabled={Boolean(error) || !nodes.length}
        onClick={() =>
          void onSave(
            {
              schema_version: 1,
              name,
              project_id: initial?.project_id || null,
              visibility: initial?.visibility || "private",
              local_only: local,
              nodes: nodes.map((n) => ({
                ...defs.find((d) => d.id === n.id),
                position: n.position,
                depends_on: edges.filter((e) => e.target === n.id).map((e) => e.source),
              })),
            },
            initial?.id,
          )
        }
      >
        Save workflow revision
      </Button>
    </div>
  );
}
