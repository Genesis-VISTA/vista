import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

// Proves the two pieces of resolution that usually break first on a Next
// project: the `@/` alias and a bare CSS import in the module graph.
import "@/app/globals.css";
import { extractPlotPath } from "@/lib/result-parsing";

describe("test harness", () => {
  it("resolves the @/ path alias", () => {
    expect(extractPlotPath("Plot saved to /tmp/density.png")).toBe("/tmp/density.png");
  });

  it("renders a component into jsdom with jest-dom matchers available", () => {
    render(<p>harness</p>);
    expect(screen.getByText("harness")).toBeInTheDocument();
  });
});
