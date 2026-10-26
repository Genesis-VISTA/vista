import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FilePreview from "@/components/FilePreview";

function stubFetch(text: string | Uint8Array) {
  const bytes = typeof text === "string" ? new TextEncoder().encode(text) : text;
  const fn = vi.fn(async () => {
    let sent = false;
    return new Response(
      new ReadableStream<Uint8Array>({
        pull(c) {
          if (!sent) {
            sent = true;
            c.enqueue(bytes);
          } else c.close();
        },
      }),
    );
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const base = { kind: "outputs" as const, projectName: "p q" };
const pwned = () => (window as unknown as { __pwned?: number }).__pwned;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as { __pwned?: number }).__pwned;
});

describe("FilePreview", () => {
  it.each(["a.png", "a.JPG", "a.jpeg", "a.gif", "a.webp", "a.svg"])("shows %s as an image with ?preview=1", (name) => {
    render(<FilePreview {...base} path={`run 1/${name}`} name={name} size={10} />);
    const img = screen.getByRole("img");
    expect(img.getAttribute("src")).toBe(`/api/files/outputs/run%201/${name}?project_name=p%20q&preview=1`);
    expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).not.toContain("preview");
  });

  it("gives each version of a file its own image URL, so a re-plot isn't the old picture", () => {
    const { unmount } = render(<FilePreview {...base} path="p.png" name="p.png" version="t1" />);
    const first = screen.getByRole("img").getAttribute("src");
    unmount();
    render(<FilePreview {...base} path="p.png" name="p.png" version="t2" />);
    const second = screen.getByRole("img").getAttribute("src");
    expect(first).toContain("preview=1&v=t1");
    expect(second).toContain("preview=1&v=t2");
    // Download is the file as it is now, so it carries no version.
    expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
      "/api/files/outputs/p.png?project_name=p%20q",
    );
  });

  it("opens a PDF with a button, not inline", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<FilePreview {...base} path="r.pdf" name="r.pdf" size={10} />);
    screen.getByRole("button", { name: "Open PDF" }).click();
    expect(open).toHaveBeenCalledWith(expect.stringContaining("preview=1"), "_blank");
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("offers only download for an unknown type", () => {
    render(<FilePreview {...base} path="a.bin" name="a.bin" size={10} />);
    expect(screen.getByText("No preview for this type.")).toBeInTheDocument();
    // One Download, as the main (filled) action rather than the bar's ghost one.
    const [download, ...others] = screen.getAllByRole("link", { name: "Download" });
    expect(others).toHaveLength(0);
    expect(download).toHaveClass("button");
    expect(download).not.toHaveClass("ghost");
    expect(download.getAttribute("href")).not.toContain("preview");
  });

  it("pretty-prints whole JSON in a pre", async () => {
    stubFetch('{"a":1}');
    render(<FilePreview {...base} path="a.json" name="a.json" size={7} />);
    const pre = await waitFor(() => {
      const el = document.querySelector("pre");
      expect(el).not.toBeNull();
      return el!;
    });
    expect(pre.textContent).toBe('{\n  "a": 1\n}');
  });

  it("notes truncation past the 1 MiB cap", async () => {
    stubFetch(new Uint8Array(1_048_576 + 5000).fill(97));
    render(<FilePreview {...base} path="big.log" name="big.log" size={1_053_576} />);
    expect(await screen.findByRole("note")).toHaveTextContent(/first 1,024 KiB of 1,029 KiB/);
  });

  it("states no total when the size isn't known, as in the Artifacts feed", async () => {
    stubFetch(new Uint8Array(1_048_576 + 5000).fill(97));
    render(<FilePreview {...base} path="big.log" name="big.log" />);
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("Showing the first 1,024 KiB. Download the file for the rest.");
  });

  it("shows no note for a small text file", async () => {
    stubFetch("hello");
    render(<FilePreview {...base} path="a.txt" name="a.txt" size={5} />);
    await screen.findByText("hello");
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("renders CSV as a table and says how many rows were omitted", async () => {
    const rows = ["h1,h2", ...Array.from({ length: 600 }, (_, i) => `${i},x`)].join("\n");
    stubFetch(rows);
    render(<FilePreview {...base} path="t.csv" name="t.csv" size={rows.length} />);
    await screen.findByRole("table");
    expect(document.querySelectorAll("tr")).toHaveLength(500);
    expect(screen.getByText(/101 more rows not shown/)).toBeInTheDocument();
  });

  it("renders markdown without raw HTML and turns images into links", async () => {
    stubFetch("# Title\n\n<script>window.__pwned=1</script>\n\n![plot](https://example.com/x.png)\n\n| a |\n|---|\n| 1 |\n");
    const { container } = render(<FilePreview {...base} path="n.md" name="n.md" size={100} />);
    await screen.findByRole("heading", { name: "Title" });
    expect(container.querySelector("script")).toBeNull();
    expect(pwned()).toBeUndefined();
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByRole("link", { name: "plot" });
    expect(link.getAttribute("href")).toBe("https://example.com/x.png");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("puts HTML in the sandboxed iframe and runs no script", async () => {
    stubFetch("<p>ok</p><script>window.__pwned=1</script>");
    const { container } = render(<FilePreview {...base} path="r.html" name="r.html" size={50} />);
    const frame = await screen.findByTitle("Tool HTML Output");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame.getAttribute("srcdoc")).not.toContain("<script");
    expect(container.querySelector("script")).toBeNull();
    expect(pwned()).toBeUndefined();
  });

  it("shows an error when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    render(<FilePreview {...base} path="a.txt" name="a.txt" size={5} />);
    expect(await screen.findByText(/Could not load the file \(404\)/)).toBeInTheDocument();
  });
});
