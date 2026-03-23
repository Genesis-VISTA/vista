/**
 * @jest-environment jsdom
 */
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock ReactMarkdown since it has ESM issues in jest
jest.mock("react-markdown", () => {
  return function MockReactMarkdown({ children }: { children: string }) {
    return <span data-testid="markdown">{children}</span>;
  };
});

// Mock SandboxedHtmlCard
jest.mock("@/components/SandboxedHtmlCard", () => {
  return function MockSandboxedHtmlCard({ html }: { html: string }) {
    return <div data-testid="sandboxed-html" dangerouslySetInnerHTML={{ __html: html }} />;
  };
});

// Mock crypto.randomUUID
let uuidCounter = 0;
Object.defineProperty(globalThis, "crypto", {
  value: {
    randomUUID: () => `test-uuid-${++uuidCounter}`,
  },
});

/** Create a mock Response-like object that works in jsdom */
function jsonResponse(data: unknown, status = 200): Response {
  const body = JSON.stringify(data);
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => JSON.parse(body),
    text: async () => body,
    headers: new Headers({ "content-type": "application/json" }),
    clone: function () { return this; },
  } as unknown as Response;
}

// Set up fetch mock
const fetchMock = jest.fn();
globalThis.fetch = fetchMock;

import HomePage from "@/app/page";

function mockFetchResponses(overrides: Record<string, unknown> = {}) {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/mcp/health") {
      return jsonResponse(
        overrides["/api/mcp/health"] ?? { ok: true, mcpBaseUrl: "http://localhost:8000/mcp" }
      );
    }
    if (url === "/api/mcp/tools") {
      return jsonResponse(
        overrides["/api/mcp/tools"] ?? { ok: true, tools: [{ name: "bash", description: "Run bash" }] }
      );
    }
    if (url === "/api/uploads") {
      return jsonResponse(overrides["/api/uploads"] ?? []);
    }
    if (url === "/api/skills") {
      return jsonResponse(overrides["/api/skills"] ?? []);
    }
    if (url === "/api/chat") {
      return jsonResponse(
        overrides["/api/chat"] ?? { ok: true, response: "Mock assistant response", toolCalls: [] }
      );
    }
    return jsonResponse({});
  });
}

beforeEach(() => {
  uuidCounter = 0;
  fetchMock.mockReset();
  mockFetchResponses();
});

describe("HomePage", () => {
  it("renders the page with chat input", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    const input = screen.getByPlaceholderText(/ask about molten salts/i);
    expect(input).toBeInTheDocument();
  });

  it("shows welcome message when no messages", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    expect(screen.getByText(/ask me about molten salts/i)).toBeInTheDocument();
  });

  it("shows the chat panel title", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    expect(screen.getByText("Chat with")).toBeInTheDocument();
  });

  it("shows the send button", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    const sendBtn = screen.getByRole("button", { name: "⏎" });
    expect(sendBtn).toBeInTheDocument();
  });

  it("sends a chat message and displays the response", async () => {
    const user = userEvent.setup();

    mockFetchResponses({
      "/api/chat": {
        ok: true,
        response: "NaCl has a melting point of 801 degrees.",
        toolCalls: [],
      },
    });

    await act(async () => {
      render(<HomePage />);
    });

    const input = screen.getByPlaceholderText(/ask about molten salts/i);
    await act(async () => {
      await user.type(input, "What is the melting point of NaCl?");
      await user.keyboard("{Enter}");
    });

    // User message should appear
    await waitFor(() => {
      expect(screen.getByText("What is the melting point of NaCl?")).toBeInTheDocument();
    });

    // Assistant response should appear (rendered through MockReactMarkdown)
    await waitFor(() => {
      expect(screen.getByText("NaCl has a melting point of 801 degrees.")).toBeInTheDocument();
    });

    // Verify /api/chat was called
    const chatCalls = fetchMock.mock.calls.filter((c: unknown[]) => c[0] === "/api/chat");
    expect(chatCalls.length).toBe(1);
  });

  it("shows loading state while waiting for response", async () => {
    const user = userEvent.setup();

    // Make the chat response slow
    let resolveChat!: (value: unknown) => void;
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/chat") {
        return new Promise((resolve) => {
          resolveChat = resolve;
        });
      }
      if (url === "/api/mcp/health") {
        return jsonResponse({ ok: true, mcpBaseUrl: "http://localhost:8000/mcp" });
      }
      if (url === "/api/mcp/tools") {
        return jsonResponse({ ok: true, tools: [] });
      }
      if (url === "/api/uploads") return jsonResponse([]);
      if (url === "/api/skills") return jsonResponse([]);
      return jsonResponse({});
    });

    await act(async () => {
      render(<HomePage />);
    });

    const input = screen.getByPlaceholderText(/ask about molten salts/i);
    await act(async () => {
      await user.type(input, "Hello");
      await user.keyboard("{Enter}");
    });

    // Button should show loading text
    await waitFor(() => {
      expect(screen.getByText(/agent working/i)).toBeInTheDocument();
    });

    // Resolve the chat response using jsonResponse helper
    await act(async () => {
      resolveChat(jsonResponse({ ok: true, response: "Hi there!", toolCalls: [] }));
    });

    // Loading state should clear
    await waitFor(() => {
      expect(screen.queryByText(/agent working/i)).not.toBeInTheDocument();
    });
  });

  it("displays error message when chat API fails", async () => {
    const user = userEvent.setup();

    mockFetchResponses({
      "/api/chat": {
        ok: false,
        response: "",
        error: "Service unavailable",
      },
    });

    await act(async () => {
      render(<HomePage />);
    });

    const input = screen.getByPlaceholderText(/ask about molten salts/i);
    await act(async () => {
      await user.type(input, "Test error");
      await user.keyboard("{Enter}");
    });

    await waitFor(() => {
      expect(screen.getByText(/agent unavailable/i)).toBeInTheDocument();
    });
  });

  it("handles network error gracefully", async () => {
    const user = userEvent.setup();

    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/chat") throw new Error("Network error");
      if (url === "/api/mcp/health") return jsonResponse({ ok: true, mcpBaseUrl: "x" });
      if (url === "/api/mcp/tools") return jsonResponse({ ok: true, tools: [] });
      if (url === "/api/uploads") return jsonResponse([]);
      if (url === "/api/skills") return jsonResponse([]);
      return jsonResponse({});
    });

    await act(async () => {
      render(<HomePage />);
    });

    const input = screen.getByPlaceholderText(/ask about molten salts/i);
    await act(async () => {
      await user.type(input, "Will fail");
      await user.keyboard("{Enter}");
    });

    await waitFor(() => {
      expect(screen.getByText(/agent unavailable/i)).toBeInTheDocument();
    });
  });

  it("does not send empty messages", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    // Click send with empty input
    const sendBtn = screen.getByRole("button", { name: "⏎" });
    await act(async () => {
      sendBtn.click();
    });

    // No chat call should have been made
    const chatCalls = fetchMock.mock.calls.filter((c: unknown[]) => c[0] === "/api/chat");
    expect(chatCalls).toHaveLength(0);
  });

  it("displays tool call summary messages", async () => {
    const user = userEvent.setup();

    mockFetchResponses({
      "/api/chat": {
        ok: true,
        response: "Here is the phase diagram.",
        toolCalls: [
          {
            tool: "run_bash",
            args: { command: "python plot.py" },
            stdout: "Plot saved to /output/plot.png",
            stderr: "",
            ok: true,
            plotPath: "/output/plot.png",
            displayHtml: '<img src="data:image/png;base64,iVBOR" />',
          },
        ],
      },
    });

    await act(async () => {
      render(<HomePage />);
    });

    const input = screen.getByPlaceholderText(/ask about molten salts/i);
    await act(async () => {
      await user.type(input, "Show phase diagram");
      await user.keyboard("{Enter}");
    });

    await waitFor(() => {
      expect(screen.getByText("Here is the phase diagram.")).toBeInTheDocument();
    });

    // Tool summary message should appear
    await waitFor(() => {
      expect(screen.getByText(/code execution completed/i)).toBeInTheDocument();
    });
  });

  it("shows panel headers", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    expect(screen.getByText("Data")).toBeInTheDocument();
    expect(screen.getByText("Skills")).toBeInTheDocument();
    expect(screen.getByText("Latest Output")).toBeInTheDocument();
  });

  it("shows MCP status buttons", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    expect(screen.getByText("MCP Status")).toBeInTheDocument();
    expect(screen.getByText("List MCP Tools")).toBeInTheDocument();
  });

  it("displays skills from the API", async () => {
    mockFetchResponses({
      "/api/skills": [
        {
          slug: "salt-analysis",
          name: "Salt Analysis",
          description: "Analyze salt properties",
          path: "skills/salt-analysis",
        },
      ],
    });

    await act(async () => {
      render(<HomePage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Salt Analysis")).toBeInTheDocument();
    });
  });

  it("shows no-figure placeholder in output panel", async () => {
    await act(async () => {
      render(<HomePage />);
    });

    expect(screen.getByText("No figure yet.")).toBeInTheDocument();
    expect(screen.getByText("No execution yet.")).toBeInTheDocument();
  });
});
