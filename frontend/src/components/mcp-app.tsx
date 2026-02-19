import { AppRenderer, type AppRendererHandle } from "@mcp-ui/client";
import { useMemo, useRef } from "react";

// sandbox_proxy.html is served from /public by Vite
const SANDBOX_URL = new URL("/sandbox_proxy.html", window.location.origin);
// Stable reference — must not be recreated per render.

interface McpAppProps {
  toolName: string;
  toolInput: Record<string, unknown>;
  toolOutput: unknown;
  resourceUri: string;
}

/**
 * Convert pydantic-ai's serialized tool output to MCP CallToolResult format.
 *
 * pydantic-ai converts MCP content types to its own internal types:
 *   - ImageContent → { data: "<base64>", media_type: "image/png" }
 *   - TextContent  → "<text string>"
 *
 * AppRenderer expects MCP format:
 *   { content: [{ type: "image", data: "...", mimeType: "..." }, { type: "text", text: "..." }] }
 */
function toMcpToolResult(output: unknown): { content: unknown[] } { // TODO: What.
  const items = Array.isArray(output) ? output : [output];
  const content = items.map((item) => {
    if (item && typeof item === "object") {
      const obj = item as Record<string, unknown>;
      // pydantic-ai BinaryImage/BinaryContent: { data, media_type }
      if (typeof obj["data"] === "string" && typeof obj["media_type"] === "string") {
        return {
          type: obj["media_type"].startsWith("image/") ? "image" : "blob",
          data: obj["data"],
          mimeType: obj["media_type"],
        };
      }
      // Already in MCP format or unknown object
      return item;
    }
    if (typeof item === "string") {
      return { type: "text", text: item };
    }
    return item;
  });
  return { content };
}

/**
 * Renders an MCP App
 */
export function McpApp({ toolName, toolInput, toolOutput, resourceUri }: McpAppProps) {
  const appRef = useRef<AppRendererHandle>(null);
  const toolResult = useMemo(() => toMcpToolResult(toolOutput), [toolOutput]);

  return (
    <div className="not-prose mt-2 w-full overflow-hidden rounded-md border">
      <AppRenderer
        ref={appRef}
        toolName={toolName}
        toolResourceUri={resourceUri}
        sandbox={{ url: SANDBOX_URL }}
        toolInput={toolInput}
        toolResult={toolResult as any}
        hostContext={{displayMode: "inline"}}
        onReadResource={async ({ uri }) => {
          const response = await fetch(
            `${BACKEND_URL}/mcp-resources?uri=${encodeURIComponent(uri)}`
          );
          if (!response.ok) {
            throw new Error(`Failed to read resource: ${uri}`);
          }
          return response.json();
        }}
        onOpenLink={async ({ url }) => {
          window.open(url, "_blank");
          return {};
        }}
        onMessage={async (params) => {
          console.log("MCP App message:", params);
          return {};
        }}
        onError={(error) => {
          console.error("MCP App error:", error);
        }}
        // onCallTool
        // onListResources
      />
    </div>
  );
}
