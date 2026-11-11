export type BinaryArtifact = { filename: string; media_type: string; content_base64: string };

export function resultSummary(value: unknown): string {
  return JSON.stringify(
    value,
    (key, item) =>
      key === "content_base64" && typeof item === "string"
        ? `[${item.length} base64 characters; use the file download]`
        : item,
    2,
  );
}

export function binaryArtifact(value: unknown): BinaryArtifact | null {
  if (!value || typeof value !== "object") return null;
  const file = value as Partial<BinaryArtifact>;
  return typeof file.filename === "string" &&
    typeof file.content_base64 === "string" &&
    typeof file.media_type === "string"
    ? (file as BinaryArtifact)
    : null;
}

export function downloadBinaryArtifact(file: BinaryArtifact) {
  if (file.content_base64.length > 27_962_028)
    throw new Error("Result exceeds the 20 MiB download limit");
  const decoded = atob(file.content_base64);
  const bytes = Uint8Array.from(decoded, (char) => char.charCodeAt(0));
  if (bytes.length > 20 * 1024 * 1024) throw new Error("Result exceeds the 20 MiB download limit");
  const mediaType = ["image/webp", "image/png", "image/jpeg"].includes(file.media_type)
    ? file.media_type
    : "application/octet-stream";
  const url = URL.createObjectURL(new Blob([bytes], { type: mediaType }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.filename.split(/[\\/]/).pop() || "result.bin";
  anchor.click();
  URL.revokeObjectURL(url);
}
