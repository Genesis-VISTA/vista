/**
 * Mock OpenAI-compatible chat/completions HTTP server.
 *
 * Tests configure what the "model" returns via setCompletion() or
 * setCompletionHandler() before calling the route handler under test.
 */
import { createServer, type Server, type IncomingMessage, type ServerResponse } from "http";

type CompletionRequest = {
  model?: string;
  messages?: unknown[];
  tools?: unknown[];
  [key: string]: unknown;
};

type CompletionChoice = {
  index: number;
  message: {
    role: "assistant";
    content: string | null;
    tool_calls?: Array<{
      id: string;
      type: "function";
      function: { name: string; arguments: string };
    }>;
  };
  finish_reason: string;
};

type CompletionResponse = {
  id: string;
  object: string;
  choices: CompletionChoice[];
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
};

export type RequestHandler = (body: CompletionRequest) => CompletionResponse;

let server: Server | null = null;
let port = 0;
let handler: RequestHandler = () => makeTextResponse("Hello from mock model");
let requestLog: CompletionRequest[] = [];

/** Build a simple text-only completion response. */
export function makeTextResponse(content: string): CompletionResponse {
  return {
    id: `chatcmpl-test-${Date.now()}`,
    object: "chat.completion",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content },
        finish_reason: "stop",
      },
    ],
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
  };
}

/** Build a tool-call completion response. */
export function makeToolCallResponse(
  calls: Array<{ name: string; arguments: Record<string, unknown> }>,
  content: string | null = null
): CompletionResponse {
  return {
    id: `chatcmpl-test-${Date.now()}`,
    object: "chat.completion",
    choices: [
      {
        index: 0,
        message: {
          role: "assistant",
          content,
          tool_calls: calls.map((c, i) => ({
            id: `call_${i}_${Date.now()}`,
            type: "function" as const,
            function: { name: c.name, arguments: JSON.stringify(c.arguments) },
          })),
        },
        finish_reason: "tool_calls",
      },
    ],
  };
}

/**
 * Set the mock to return a plain text completion.
 * Convenience wrapper around setCompletionHandler.
 */
export function setCompletion(content: string): void {
  handler = () => makeTextResponse(content);
}

/**
 * Set a sequence of responses. Each call to /chat/completions consumes
 * the next response in the array. Useful for multi-turn tool-call loops.
 */
export function setCompletionSequence(responses: CompletionResponse[]): void {
  let idx = 0;
  handler = () => {
    const resp = responses[idx] ?? makeTextResponse("(sequence exhausted)");
    if (idx < responses.length) idx++;
    return resp;
  };
}

/**
 * Set a fully custom handler that receives the request body and returns
 * a CompletionResponse.
 */
export function setCompletionHandler(fn: RequestHandler): void {
  handler = fn;
}

/** Return all requests received since the last reset. */
export function getRequestLog(): CompletionRequest[] {
  return [...requestLog];
}

/** Clear the request log. */
export function clearRequestLog(): void {
  requestLog = [];
}

/** Start the mock server; returns the base URL (e.g. http://127.0.0.1:PORT/v1). */
export function startMockOpenAI(): Promise<string> {
  return new Promise((resolve, reject) => {
    server = createServer((req: IncomingMessage, res: ServerResponse) => {
      // Only respond to POST on any path
      if (req.method !== "POST") {
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "Not found" }));
        return;
      }

      let body = "";
      req.on("data", (chunk: Buffer) => {
        body += chunk.toString();
      });
      req.on("end", () => {
        try {
          const parsed: CompletionRequest = JSON.parse(body);
          requestLog.push(parsed);
          const response = handler(parsed);
          res.writeHead(200, { "content-type": "application/json" });
          res.end(JSON.stringify(response));
        } catch (err) {
          res.writeHead(500, { "content-type": "application/json" });
          res.end(
            JSON.stringify({
              error: { message: err instanceof Error ? err.message : "Internal mock error" },
            })
          );
        }
      });
    });

    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server!.address();
      if (addr && typeof addr === "object") {
        port = addr.port;
        resolve(`http://127.0.0.1:${port}/v1`);
      } else {
        reject(new Error("Failed to get server address"));
      }
    });
  });
}

/** Stop the mock server. */
export function stopMockOpenAI(): Promise<void> {
  return new Promise((resolve) => {
    if (!server) {
      resolve();
      return;
    }
    server.close(() => {
      server = null;
      resolve();
    });
  });
}

/** Reset handler and request log to defaults. */
export function resetMockOpenAI(): void {
  handler = () => makeTextResponse("Hello from mock model");
  requestLog = [];
}
