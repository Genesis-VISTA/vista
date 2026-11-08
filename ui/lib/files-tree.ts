/**
 * Listing entries as the Files page shows them, and the tree built from their
 * nested names. Pure, so the ordering rules can be tested without a page.
 */

export type FileKind = "uploads" | "outputs";

export type ShownIn = {
  chat_session_id: string;
  chat_title: string;
  run_id: string;
  tool_call_id: string;
};

export type ListedFile = {
  /** Path inside the kind, possibly nested: `run1/plot.png`. */
  name: string;
  size: number;
  modifiedAt: string;
  shown_in?: ShownIn | null;
};

export type FolderNode = {
  type: "folder";
  /** Last segment. */
  name: string;
  /** Full path from the kind's root. */
  path: string;
  children: TreeNode[];
};

export type FileNode = {
  type: "file";
  name: string;
  path: string;
  file: ListedFile;
};

export type TreeNode = FolderNode | FileNode;

function time(value: string): number {
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
}

/** Folders first (by name), then files newest first (by name on a tie). */
function sortNodes(nodes: TreeNode[]): void {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    if (a.type === "file" && b.type === "file") {
      const byTime = time(b.file.modifiedAt) - time(a.file.modifiedAt);
      if (byTime !== 0) return byTime;
    }
    return a.name.localeCompare(b.name);
  });
  for (const node of nodes) if (node.type === "folder") sortNodes(node.children);
}

export function buildTree(files: readonly ListedFile[]): TreeNode[] {
  const root: TreeNode[] = [];
  const folders = new Map<string, FolderNode>();

  for (const file of files) {
    const segments = file.name.split("/").filter(Boolean);
    if (segments.length === 0) continue;
    let siblings = root;
    let prefix = "";
    for (const segment of segments.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${segment}` : segment;
      let folder = folders.get(prefix);
      if (!folder) {
        folder = { type: "folder", name: segment, path: prefix, children: [] };
        folders.set(prefix, folder);
        siblings.push(folder);
      }
      siblings = folder.children;
    }
    siblings.push({
      type: "file",
      name: segments[segments.length - 1],
      path: file.name,
      file,
    });
  }
  sortNodes(root);
  return root;
}

/** The folder paths above a file: `a/b/c.png` gives `a` and `a/b`. */
export function ancestorFolders(path: string): string[] {
  const segments = path.split("/").filter(Boolean).slice(0, -1);
  return segments.map((_, i) => segments.slice(0, i + 1).join("/"));
}

/** Where the chat page opens the turn that showed a file. */
export function shownInHref(shown: ShownIn): string {
  const params = new URLSearchParams({
    chat: shown.chat_session_id,
    run: shown.run_id,
    step: shown.tool_call_id,
  });
  return `/?${params.toString()}`;
}

/** The Files page's own URL for a selection. */
export function filesHref(kind: FileKind, path: string): string {
  return `/files?${new URLSearchParams({ kind, path }).toString()}`;
}

/*
 * The folders open on the Files page, per project. sessionStorage, not
 * localStorage: they should survive going to a chat and back, not a restart.
 */
const OPEN_FOLDERS_KEY = "vista.filesOpenFolders.v1.";

export function readOpenFolders(project: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const parsed: unknown = JSON.parse(
      window.sessionStorage.getItem(OPEN_FOLDERS_KEY + project) ?? "[]",
    );
    return new Set(
      Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === "string") : [],
    );
  } catch {
    return new Set();
  }
}

export function writeOpenFolders(project: string, open: ReadonlySet<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(OPEN_FOLDERS_KEY + project, JSON.stringify([...open]));
  } catch {
    // Private mode or blocked storage: folders simply start closed next time.
  }
}
