import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

// Proves the two pieces of resolution that usually break first on a Next
// project: the `@/` alias and a bare CSS import in the module graph.
import "@/app/globals.css";
import { formatResultSummary } from "@/lib/result-parsing";

describe("test harness", () => {
  it("resolves the @/ path alias", () => {
    expect(formatResultSummary({ ok: true, stdout: "hi" } as never)).toBe("OK: hi");
  });

  it("renders a component into jsdom with jest-dom matchers available", () => {
    render(<p>harness</p>);
    expect(screen.getByText("harness")).toBeInTheDocument();
  });
});
