import { useState } from "react";
import { Button } from "../../components/ui/button";
import { Textarea } from "../../components/ui/textarea";
import { box, errorText, selectClass, type Entity, type Json, type Session } from "./shared";

export function InvokeForm({
  entity,
  sessions,
  onSubmit,
  onClose,
  busy,
}: {
  entity: Entity;
  sessions: Session[];
  onSubmit: (values: Json) => Promise<void>;
  onClose: () => void;
  busy: boolean;
}) {
  const properties = entity.input_schema?.properties;
  const [values, setValues] = useState<Json>({});
  const [raw, setRaw] = useState('{"prompt": ""}');
  const [session, setSession] = useState("");
  const [local, setLocal] = useState(entity.local_only !== false);
  const [release, setRelease] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className={box + " space-y-4"}
      onSubmit={(e) => {
        e.preventDefault();
        try {
          const inputs = properties
            ? Object.fromEntries(
                Object.entries(values).map(([key, value]) => [
                  key,
                  ["object", "array"].includes(properties[key]?.type || "") &&
                  typeof value === "string"
                    ? JSON.parse(value)
                    : value,
                ]),
              )
            : JSON.parse(raw);
          setError("");
          void onSubmit({
            inputs,
            local_only: local,
            allow_cloud: release,
            session_id: session || null,
            idempotency_key: crypto.randomUUID(),
          });
        } catch (err) {
          setError(errorText(err));
        }
      }}
    >
      <div className="flex justify-between">
        <h2 className="text-xl font-semibold">{entity.name}</h2>
        <Button type="button" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      {properties ? (
        Object.entries(properties).map(([name, spec]) => (
          <label key={name} className="block text-sm">
            {spec.title || name}
            {spec.enum ? (
              <select
                className={selectClass}
                value={String(values[name] || "")}
                required={entity.input_schema?.required?.includes(name)}
                onChange={(e) => setValues({ ...values, [name]: e.target.value })}
              >
                <option value="">Select</option>
                {spec.enum.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            ) : spec.type === "boolean" ? (
              <input
                className="ml-2"
                type="checkbox"
                checked={Boolean(values[name])}
                onChange={(e) => setValues({ ...values, [name]: e.target.checked })}
              />
            ) : (
              <Textarea
                required={entity.input_schema?.required?.includes(name)}
                value={String(values[name] ?? "")}
                onChange={(e) =>
                  setValues({
                    ...values,
                    [name]:
                      spec.type === "number" || spec.type === "integer"
                        ? Number(e.target.value)
                        : e.target.value,
                  })
                }
              />
            )}
          </label>
        ))
      ) : (
        <label className="block text-sm">
          Request fields
          <Textarea
            className="min-h-28 font-mono"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
          />
        </label>
      )}
      {properties?.text && (
        <label className="block text-sm">
          Or load a text file
          <input
            className="mt-2 block text-sm"
            type="file"
            accept="text/*,.csv,.json"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              if (file.size > 500000) {
                setError("Maximum file size is 500 KB");
                return;
              }
              setValues({ ...values, text: await file.text() });
            }}
          />
        </label>
      )}
      {entity.kind === "agent" && (
        <label className="block text-sm">
          Session
          <select
            className={selectClass}
            value={session}
            onChange={(e) => setSession(e.target.value)}
          >
            <option value="">New session</option>
            {sessions
              .filter((s) => s.agent_id === entity.id)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} · {s.id.slice(0, 8)}
                </option>
              ))}
          </select>
        </label>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={local}
          onChange={(e) => {
            setLocal(e.target.checked);
            setRelease(false);
          }}
        />
        Local-only execution
      </label>
      {!local && entity.kind !== "workflow" && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={release} onChange={(e) => setRelease(e.target.checked)} />
          Allow sending these exact inputs to the selected cloud service
        </label>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <Button disabled={busy}>Run</Button>
    </form>
  );
}
