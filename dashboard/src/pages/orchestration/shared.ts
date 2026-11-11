export type Json = Record<string, unknown>;
export type Entity = {
  id: string;
  name: string;
  kind: string;
  owner: string;
  revision: number;
  input_schema?: {
    properties?: Record<
      string,
      {
        type?: string;
        title?: string;
        enum?: string[];
        contentEncoding?: string;
        maxLength?: number;
        minimum?: number;
        maximum?: number;
        default?: unknown;
      }
    >;
    required?: string[];
  };
  runtime_phase?: string;
  lifecycle?: string;
  runtime_id?: string;
  backend?: string;
  target_id?: string;
  protocol?: string;
  base_url?: string;
  harness_key?: string;
  provider_connection_id?: string;
  native_login_ref?: string;
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
  permissions?: {
    id: string;
    title?: string;
    options: { option_id: string; name: string }[];
  }[];
};
export type Session = { id: string; name: string; agent_id: string; resume_supported?: boolean };
export type Credential = { id: string; name: string; kind?: string };
export type Harness = { key: string; name: string };
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
