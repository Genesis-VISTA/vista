import { describe, expect, it } from "vitest";
import {
  ancestorFolders,
  buildTree,
  filesHref,
  shownInHref,
  type ListedFile,
} from "@/lib/files-tree";

function file(name: string, modifiedAt: string): ListedFile {
  return { name, size: 1, modifiedAt };
}

describe("buildTree", () => {
  it("nests by name, folders first, then files newest first", () => {
    const tree = buildTree([
      file("old.txt", "2026-01-01T00:00:00Z"),
      file("run2/b.png", "2026-01-02T00:00:00Z"),
      file("new.txt", "2026-01-03T00:00:00Z"),
      file("run1/a/deep.csv", "2026-01-01T00:00:00Z"),
      file("run1/z.log", "2026-01-05T00:00:00Z"),
      file("run1/y.log", "2026-01-06T00:00:00Z"),
    ]);
    expect(tree.map((n) => `${n.type}:${n.name}`)).toEqual([
      "folder:run1",
      "folder:run2",
      "file:new.txt",
      "file:old.txt",
    ]);
    const run1 = tree[0];
    if (run1.type !== "folder") throw new Error("expected a folder");
    expect(run1.path).toBe("run1");
    expect(run1.children.map((n) => `${n.type}:${n.name}`)).toEqual([
      "folder:a",
      "file:y.log",
      "file:z.log",
    ]);
    const deep = run1.children[0];
    if (deep.type !== "folder") throw new Error("expected a folder");
    expect(deep.path).toBe("run1/a");
    expect(deep.children[0]).toMatchObject({ type: "file", path: "run1/a/deep.csv" });
  });

  it("is empty for no files", () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe("links", () => {
  it("lists the folders above a file", () => {
    expect(ancestorFolders("a/b/c.png")).toEqual(["a", "a/b"]);
    expect(ancestorFolders("c.png")).toEqual([]);
  });

  it("builds the chat deep link and the files selection link", () => {
    expect(
      shownInHref({ chat_session_id: "c1", chat_title: "T", run_id: "r1", tool_call_id: "t1" }),
    ).toBe("/?chat=c1&run=r1&step=t1");
    expect(filesHref("outputs", "run1/plot.png")).toBe("/files?kind=outputs&path=run1%2Fplot.png");
  });
});
