export type Json = Record<string, unknown>;
export type Entity = {
  id: string;
  name: string;
  kind: string;
  owner: string;
  revision: number;
  input_schema?: {
    properties?: Record<string, { type?: string; title?: string; enum?: string[] }>;
    required?: string[];
  };
  runtime_phase?: string;
  lifecycle?: string;
  openshell?: { gateway: string; workspace: string; sandbox: string; sandbox_id: string };
  nodes?: StepDef[];
  local_only?: boolean;
  [key: string]: unknown;
};
export type StepDef = {
  id: string;
  type: string;
  target?: string;
  depends_on: string[];
  inputs: Json;
  position: { x: number; y: number };
  session_id?: string;
  reviewer?: string;
  release?: boolean;
  max_rounds?: number;
  when?: Json;
};
export type Run = {
  id: string;
  name: string;
  status: string;
  result?: Json;
  steps?: { id: string; status: string; output: Json }[];
};
export type Session = { id: string; name: string; agent_id: string };
export const base = "/orchestration/v1";
export const serialize = (value: unknown) => JSON.stringify(value, null, 2);
export const box = "rounded-xl border bg-card p-5 shadow-sm";
export const selectClass = "w-full rounded-md border bg-background p-2 text-sm";

export function errorText(error: unknown): string {
  if (error && typeof error === "object" && "body" in error) {
    const body = (error as { body: { detail?: unknown } }).body;
    if (body?.detail) return typeof body.detail === "string" ? body.detail : serialize(body.detail);
  }
  return error instanceof Error ? error.message : String(error);
}
