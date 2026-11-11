import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvokeForm } from "./InvokeForm";
import { binaryArtifact, downloadBinaryArtifact } from "./results";

const photoTool = {
  id: "photo",
  name: "Photo to WebP",
  kind: "tool",
  owner: "operator",
  revision: 1,
  input_schema: {
    properties: {
      image_base64: {
        type: "string",
        title: "PNG or JPEG photo",
        contentEncoding: "base64",
        maxLength: 27_962_028,
      },
      quality: { type: "integer", title: "WebP quality", default: 80, minimum: 1, maximum: 100 },
    },
    required: ["image_base64"],
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Image tool input and output", () => {
  it("maps uploaded photo bytes into the declared base64 field and supplies schema defaults", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(
      <InvokeForm
        entity={photoTool}
        sessions={[]}
        busy={false}
        onSubmit={submit}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    const file = new File([new Uint8Array([1, 2, 3])], "photo.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("PNG or JPEG photo"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
    fireEvent.submit(screen.getByRole("button", { name: "Run" }).closest("form")!);
    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith(
        expect.objectContaining({ inputs: { image_base64: "AQID", quality: 80 } }),
      ),
    );
  });

  it("rejects unsupported or oversized uploads before enabling the job", async () => {
    const submit = vi.fn();
    render(
      <InvokeForm
        entity={photoTool}
        sessions={[]}
        busy={false}
        onSubmit={submit}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("PNG or JPEG photo"), {
      target: { files: [new File(["<svg/>"], "image.svg", { type: "image/svg+xml" })] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PNG or JPEG");
    const large = new File(["image"], "photo.jpg", { type: "image/jpeg" });
    Object.defineProperty(large, "size", { value: 20 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByLabelText("PNG or JPEG photo"), { target: { files: [large] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("20 MiB");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("downloads decoded bytes with a safe filename without rendering an HTML preview", () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:result");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const artifact = binaryArtifact({
      filename: "folder/converted.webp",
      media_type: "image/webp",
      content_base64: "AQID",
    });
    expect(artifact).not.toBeNull();
    downloadBinaryArtifact(artifact!);
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe("image/webp");
    expect(blob.size).toBe(3);
    expect((click.mock.instances[0] as HTMLAnchorElement).download).toBe("converted.webp");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:result");
    expect(binaryArtifact({ filename: "text.txt", text: "hello" })).toBeNull();
  });
});
