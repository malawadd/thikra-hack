// Minimal SSE parser for streaming JSON frames over `fetch`. EventSource
// is incompatible with POST bodies, so we hand-roll the parser.
import type { SseFrame } from "@/types/pipeline";

export async function* streamSse(url: string, body: unknown): AsyncGenerator<SseFrame> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    throw new Error(`SSE request failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    // SSE frames are separated by a blank line.
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const lines = frame.split("\n");
      const dataLines = lines.filter((l) => l.startsWith("data: ")).map((l) => l.slice(6));
      if (dataLines.length === 0) continue;
      try {
        yield JSON.parse(dataLines.join("\n")) as SseFrame;
      } catch {
        // Drop malformed frames silently — never crash the live feed.
      }
    }
  }
}
