import { describe, expect, it } from "vitest";
import { fileLinkProps } from "@/lib/file-links";

describe("fileLinkProps", () => {
  it.each([
    "/api/files/outputs/salt-plots/BeF2-NaF-UF4_phase_diagram.png?project_name=molten-salt",
    "/api/files/outputs/44379/log-44379.out?project_name=molten-salt",
  ])("downloads VISTA's own file %s", (url) => {
    expect(fileLinkProps(url)).toEqual({ download: "" });
  });

  it.each([
    "https://example.org/figure.png",
    "http://127.0.0.1:8000/files/x.png",
    // Protocol-relative: another host, despite the leading slash.
    "//evil.example/x.png",
  ])("opens %s in a new tab", (url) => {
    expect(fileLinkProps(url)).toEqual({ target: "_blank", rel: "noreferrer" });
  });
});
