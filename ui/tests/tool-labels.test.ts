import { describe, expect, it } from "vitest";
import { hasToolLabel, labelForTool } from "@/lib/tool-labels";

describe("labelForTool", () => {
  it("names the tools a scientist would otherwise read as log lines", () => {
    expect(labelForTool("rag_search")).toBe("Searching the literature");
    expect(labelForTool("submit_hpc_job")).toBe("Submitting a job to the cluster");
  });

  // Tools come from MCP servers and the list is not fixed. An unmapped tool
  // must read as itself rather than as nothing.
  it("falls back to the raw name for anything unmapped", () => {
    expect(labelForTool("some_future_tool")).toBe("some_future_tool");
    expect(hasToolLabel("some_future_tool")).toBe(false);
  });

  it("looks through an MCP server namespace prefix", () => {
    expect(labelForTool("vista__rag_search")).toBe("Searching the literature");
    expect(labelForTool("vista__unknown_tool")).toBe("vista__unknown_tool");
  });

  it("does not invent a label for an empty name", () => {
    expect(labelForTool("")).toBe("");
  });
});
