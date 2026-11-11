import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentProfileForm, ProviderForm, RuntimeForm, ServiceForm } from "./RuntimeForms";

const runtime = {
  id: "runtime-1",
  name: "GPU worker",
  kind: "runtime",
  owner: "operator",
  revision: 1,
};
const credentials = [
  { id: "key", name: "Personal key" },
  { id: "login", name: "Native account", kind: "native_login" },
];

describe("Orchestration configuration", () => {
  it("registers one execution target without creating a tool definition", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RuntimeForm busy={false} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Runtime name"), { target: { value: "GPU tools" } });
    fireEvent.change(screen.getByLabelText("Execution backend"), { target: { value: "komodo" } });
    fireEvent.change(screen.getByLabelText("Komodo stack ID"), { target: { value: "stack-1" } });
    fireEvent.change(screen.getByLabelText("Run policy"), { target: { value: "on_demand" } });
    fireEvent.change(screen.getByLabelText("Readiness URL"), {
      target: { value: "http://gpu/health" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Register runtime" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          schema_version: 2,
          backend: "komodo",
          target_id: "stack-1",
          lifecycle: "on_demand",
        }),
      ),
    );
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("endpoint");
  });

  it("binds a native tool to an existing runtime instead of copying deployment settings", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<ServiceForm busy={false} onSave={onSave} runtimes={[runtime]} />);
    fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Segment image" } });
    fireEvent.change(screen.getByLabelText("Endpoint URL"), {
      target: { value: "http://gpu/segment" },
    });
    fireEvent.change(screen.getByLabelText("Runtime"), { target: { value: runtime.id } });
    fireEvent.click(screen.getByRole("button", { name: "Register" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          schema_version: 2,
          runtime_id: runtime.id,
          endpoint: "http://gpu/segment",
        }),
      ),
    );
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("stack_id");
  });

  it("keeps provider protocol explicit and sends only a credential reference", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<ProviderForm busy={false} onSave={onSave} credentials={credentials} />);
    fireEvent.change(screen.getByLabelText("Connection name"), {
      target: { value: "My provider" },
    });
    fireEvent.change(screen.getByLabelText("Provider API"), { target: { value: "responses" } });
    fireEvent.change(screen.getByLabelText("Provider base URL"), {
      target: { value: "https://provider.example/v1" },
    });
    fireEvent.change(screen.getByLabelText("API credential"), { target: { value: "key" } });
    expect(screen.queryByRole("option", { name: "Native account" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create provider connection" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ protocol: "responses", credential_ref: "key" }),
      ),
    );
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("token");
  });

  it("creates a native-login profile without conflating it with a provider API key", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentProfileForm
        busy={false}
        onSave={onSave}
        runtimes={[runtime]}
        providers={[]}
        credentials={credentials}
        harnesses={[{ key: "codex", name: "Codex" }]}
        services={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Coder" } });
    fireEvent.change(screen.getByLabelText("Harness"), { target: { value: "codex" } });
    fireEvent.change(screen.getByLabelText("Runtime"), { target: { value: runtime.id } });
    fireEvent.change(screen.getByLabelText("Authentication"), { target: { value: "native" } });
    fireEvent.change(screen.getByLabelText("Native login reference"), {
      target: { value: "login" },
    });
    expect(screen.queryByRole("option", { name: "Personal key" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          schema_version: 2,
          harness_key: "codex",
          runtime_id: runtime.id,
          native_login_ref: "login",
          provider_connection_id: null,
        }),
      ),
    );
  });
});
