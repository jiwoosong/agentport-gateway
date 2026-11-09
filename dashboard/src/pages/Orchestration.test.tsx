import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Orchestration } from "./Orchestration";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
const service = { id: "convert", name: "Text to CSV", kind: "tool", lifecycle: "manual", runtime_phase: "stopped",
  input_schema: { properties: { text: { type: "string", title: "Source text" } }, required: ["text"] } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation(async path => {
    if (path.endsWith("/services")) return [service];
    if (path.endsWith("/me")) return { is_admin: false };
    return [];
  });
  vi.mocked(api.post).mockResolvedValue({ id: "run-1", name: service.name, status: "queued" });
});

describe("AgentPort", () => {
  it("lets a regular user submit a schema-driven job without deployment controls", async () => {
    render(<Orchestration />);
    fireEvent.click(await screen.findByRole("button", { name: "Use service" }));
    expect(screen.queryByRole("button", { name: "Register service" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Source text"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/orchestration/v1/services/convert/invoke",
      expect.objectContaining({ inputs: { text: "hello" }, local_only: true, allow_cloud: false })));
    expect(await screen.findByText(/Job accepted/)).toBeInTheDocument();
  });

  it("saves an explicitly edited graph and keeps local-only as its default", async () => {
    render(<Orchestration />);
    await screen.findByText("Text to CSV");
    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));
    fireEvent.click(screen.getByRole("button", { name: "New workflow" }));
    fireEvent.click(screen.getByRole("button", { name: "+ join" }));
    fireEvent.change(screen.getByLabelText("Input mapping"), { target: { value: '{"public":{"$ref":"input.public"}}' } });
    fireEvent.click(screen.getByRole("button", { name: "Save workflow revision" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/orchestration/v1/workflows",
      expect.objectContaining({ local_only: true, nodes: [expect.objectContaining({ type: "join",
        inputs: { public: { $ref: "input.public" } } })] })));
  });
});
