import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Orchestration } from "./Orchestration";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
const service = {
  id: "convert",
  name: "Text to CSV",
  kind: "tool",
  lifecycle: "manual",
  runtime_phase: "stopped",
  input_schema: {
    properties: { text: { type: "string", title: "Source text" } },
    required: ["text"],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation(async (path) => {
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
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/orchestration/v1/services/convert/invoke",
        expect.objectContaining({
          inputs: { text: "hello" },
          local_only: true,
          allow_cloud: false,
        }),
      ),
    );
    expect(await screen.findByText(/Job accepted/)).toBeInTheDocument();
  });

  it("saves an explicitly edited graph and keeps local-only as its default", async () => {
    render(<Orchestration />);
    await screen.findByText("Text to CSV");
    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));
    fireEvent.click(screen.getByRole("button", { name: "New workflow" }));
    fireEvent.click(screen.getByRole("button", { name: "+ join" }));
    fireEvent.change(screen.getByLabelText("Input mapping"), {
      target: { value: '{"public":{"$ref":"input.public"}}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save workflow revision" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/orchestration/v1/workflows",
        expect.objectContaining({
          local_only: true,
          nodes: [
            expect.objectContaining({ type: "join", inputs: { public: { $ref: "input.public" } } }),
          ],
        }),
      ),
    );
  });
  it("imports a service definition for an operator without exposing deployment code to users", async () => {
    vi.mocked(api.get).mockImplementation(async (path) => {
      if (path.endsWith("/services")) return [service];
      if (path.endsWith("/me")) return { is_admin: true };
      return [];
    });
    render(<Orchestration />);
    const file = new File(["{}"], "photo.service.json", { type: "application/json" });
    const definition = { schema_version: 2, name: "Photo", endpoint: "http://image:8080/convert" };
    Object.defineProperty(file, "text", {
      value: async () => JSON.stringify({ kind: "service", definition }),
    });
    fireEvent.change(await screen.findByLabelText("Import service definition"), {
      target: { files: [file] },
    });
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/orchestration/v1/services", definition),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
  });
  it("offers OpenShell lifecycle controls without a Komodo update action", async () => {
    vi.mocked(api.get).mockImplementation(async (path) => {
      if (path.endsWith("/services"))
        return [
          {
            ...service,
            openshell: { gateway: "pc", workspace: "team", sandbox: "alice", sandbox_id: "s1" },
          },
        ];
      if (path.endsWith("/me")) return { is_admin: true };
      if (path.endsWith("/runtime")) return { phase: "SANDBOX_PHASE_READY", policy_version: 7 };
      return [];
    });
    render(<Orchestration />);
    fireEvent.click(await screen.findByRole("button", { name: "Check sandbox" }));
    expect(await screen.findByText("READY · policy 7")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "update" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "start" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/orchestration/v1/services/convert/operations/start",
        {},
      ),
    );
  });

  it("shows Relay observations separately from job completion", async () => {
    const run = {
      id: "run-1",
      name: "Hermes task",
      status: "completed",
      steps: [
        {
          id: "agent",
          status: "completed",
          output: {
            value: {
              observability: {
                source: "nemo-relay",
                available: true,
                counts: { llm: { started: 2, ended: 1 } },
                truncated: true,
              },
            },
          },
        },
      ],
    };
    vi.mocked(api.get).mockImplementation(async (path) => {
      if (path.endsWith("/runs")) return [run];
      if (path.endsWith("/runs/run-1")) return run;
      if (path.endsWith("/me")) return { is_admin: false };
      return [];
    });
    render(<Orchestration />);
    fireEvent.click(screen.getByRole("button", { name: "Activity" }));
    fireEvent.click(await screen.findByRole("button", { name: /Hermes task/ }));
    expect(await screen.findByText("NeMo Relay activity")).toBeInTheDocument();
    expect(screen.getByText("llm: 2 started / 1 ended")).toBeInTheDocument();
    expect(screen.getByText(/Counts do not confirm task success/)).toBeInTheDocument();
    expect(screen.getByText(/Partial record window/)).toBeInTheDocument();
  });

  it("submits the selected native permission option for the active run", async () => {
    const run = {
      id: "permission-run",
      name: "Coder permission",
      status: "running",
      permissions: [
        {
          id: "request-1",
          title: "Read project file",
          options: [
            { option_id: "allow-once", name: "Allow once" },
            { option_id: "reject", name: "Reject" },
          ],
        },
      ],
    };
    vi.mocked(api.get).mockImplementation(async (path) => {
      if (path.endsWith("/runs")) return [run];
      if (path.endsWith("/runs/permission-run")) return run;
      if (path.endsWith("/me")) return { is_admin: false };
      return [];
    });
    render(<Orchestration />);
    fireEvent.click(screen.getByRole("button", { name: "Activity" }));
    fireEvent.click(await screen.findByRole("button", { name: /Coder permission/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/orchestration/v1/runs/permission-run/permissions/request-1",
        { option_id: "allow-once" },
      ),
    );
  });
});
