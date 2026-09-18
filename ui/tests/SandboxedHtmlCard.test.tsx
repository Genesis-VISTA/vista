import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";

function srcDocOf(html: string): string {
  render(<SandboxedHtmlCard html={html} />);
  return screen.getByTitle("Tool HTML Output").getAttribute("srcdoc") ?? "";
}

describe("SandboxedHtmlCard", () => {
  // Tool output is model-influenced content. The iframe is the containment
  // boundary, so its attributes are the thing worth pinning down.
  it("renders an iframe with every capability revoked", () => {
    render(<SandboxedHtmlCard html="<p>hi</p>" />);
    const frame = screen.getByTitle("Tool HTML Output");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame).not.toHaveAttribute("src");
  });

  it("stamps a deny-by-default content security policy into the document", () => {
    const doc = srcDocOf("<p>hi</p>");
    expect(doc).toContain("default-src 'none'");
    expect(doc).toContain("img-src data:");
    expect(doc).toContain("style-src 'unsafe-inline'");
  });

  it("keeps benign markup", () => {
    const doc = srcDocOf("<h1>Density</h1><table><tr><td>1073 K</td></tr></table>");
    expect(doc).toContain("<h1>Density</h1>");
    expect(doc).toContain("1073 K");
  });

  it.each([
    ["script tag", "<p>ok</p><script>fetch('/api/projects')</script>", "fetch("],
    ["iframe", "<iframe src='https://example.com'></iframe>", "example.com"],
    ["object", "<object data='evil.swf'></object>", "evil.swf"],
    ["embed", "<embed src='evil.swf'>", "evil.swf"],
    ["form", "<form action='https://example.com'><input name='x'></form>", "example.com"],
  ])("strips a %s", (_label, html, forbidden) => {
    expect(srcDocOf(html)).not.toContain(forbidden);
  });

  it.each(["onerror", "onload", "onclick", "onsubmit"])(
    "strips the %s handler attribute",
    (attr) => {
      const doc = srcDocOf(`<img src="x" ${attr}="alert(1)">`);
      expect(doc).not.toContain(attr);
      expect(doc).not.toContain("alert(1)");
    },
  );

  it("strips javascript: URLs", () => {
    expect(srcDocOf('<a href="javascript:alert(1)">click</a>')).not.toContain("javascript:");
  });
});
