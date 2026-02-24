import { AppRenderer, type AppRendererHandle, type McpUiHostContext } from "@mcp-ui/client";
import { useMemo, useRef, useState } from "react";

const SANDBOX_URL = new URL("/sandbox_proxy.html", window.location.origin);

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
function toMcpToolResult(output: unknown): { content: unknown[] } {
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
      // VercelAIAdapter deserializes things that look like JSON
      return { type: "text", text: JSON.stringify(obj) };
    }
    if (typeof item === "string") {
      return { type: "text", text: item };
    }
    return item;
  });
  return { content };
}

interface McpAppProps {
  toolName: string;
  toolInput: Record<string, unknown>;
  toolOutput: unknown;
  resourceUri: string;
}

/**
 * Renders an MCP App
 */
export function McpApp({ toolName, toolInput, toolOutput, resourceUri }: McpAppProps) {
  const appRef = useRef<AppRendererHandle>(null);
  const toolResult = useMemo(() => toMcpToolResult(toolOutput), [toolOutput]);
  const [height, setHeight] = useState(0);

  const hostContext: McpUiHostContext = {
      displayMode: "inline",
      availableDisplayModes: ["inline"],
      // toolInfo: undefined,
      // theme: undefined,
      // styles: undefined,
      // containerDimensions: undefined,
      locale: navigator.language,
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      userAgent: navigator.userAgent,
      platform: "web",
      // deviceCapabilities: undefined,
      // safeAreaInsets: undefined,
  };

  return (
    <div
      className="not-prose mt-2 w-full overflow-hidden rounded-md border"
      style={{ height: height > 0 ? height : undefined }}
    >
      <AppRenderer
        ref={appRef}
        toolName={toolName}
        toolResourceUri={resourceUri}
        sandbox={{ url: SANDBOX_URL }}
        toolInput={toolInput}
        // toolInputPartial=
        toolResult={toolResult as any}
        // toolCancelled=
        hostContext={hostContext}
        onOpenLink={async ({ url }) => {
          console.log("MCP APP onOpenLink", {url})
          window.open(url, "_blank");
          return {};
        }}
        // onMessage={async (params) => {
        //   // Guest requested to append a message to chat
        //   return {}
        // }}
        onLoggingMessage={({ level, logger, data }) => {
          console.log(`[MCP App]${logger ? ` [${logger}]` : ""}[${level ?? "info"}]:`, data);
        }}
        onError={(error) => {
          console.error("MCP App error:", error);
        }}
        onSizeChanged={({ height }) => {
          if (height && height > 0) {
            setHeight(height);
          }
        }}

        // These are proxies to the MCP server. The MCP App can call tools, and read resources
        // onCallTool={async (params) => {}}
        // onListResources={async (params) => {}}
        // onListResourceTemplates={async () => {}}
        onReadResource={async ({ uri }) => {
          const response = await fetch(
            `${BACKEND_URL}/mcp/resources?uri=${encodeURIComponent(uri)}`
          );
          if (!response.ok) {
            throw new Error(`Failed to read resource: ${uri}`);
          }
          return response.json();
        }}
        // Handle other MCP messages like sampling/createMessage
        // onFallbackRequest={async (request) => {}}
      />
    </div>
  );
}
