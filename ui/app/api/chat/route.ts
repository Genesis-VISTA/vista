import { NextRequest } from "next/server";
import { backendUrl } from "@/lib/backend";

// Thin proxy: forwards useChat's POST to the backend Vercel endpoint and pipes
// the response stream back unchanged.
//
// Elicitation is handled entirely in the backend, which emits a v5 DataChunk
// (type "data-mcp-elicitation") inline with the message stream. This route does NOT
// intercept or transform elicitation events — useChat surfaces them as
// `data-mcp-elicitation` parts on the resulting UIMessage.
//
// Request body (from useChat / DefaultChatTransport):
//   Vercel AI SDK RequestData (id, messages: UIMessage[], ...) + extra `project_name`
//   added via the transport's `body` option.

export async function POST(req: NextRequest) {
  const body = await req.json();
  const projectName: string | undefined = body?.project_name;
  if (!projectName) {
    return new Response(
      JSON.stringify({ error: "project_name is required" }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }

  const upstream = await fetch(
    `${backendUrl}/projects/${encodeURIComponent(projectName)}/agent/run/vercel`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: req.headers.get("accept") ?? "text/event-stream",
      },
      body: JSON.stringify(body),
    },
  );

  const headers = new Headers();
  for (const name of [
    "content-type",
    "x-vercel-ai-ui-message-stream",
    "cache-control",
    "x-accel-buffering",
  ]) {
    const v = upstream.headers.get(name);
    if (v) headers.set(name, v);
  }

  return new Response(upstream.body, { status: upstream.status, headers });
}
