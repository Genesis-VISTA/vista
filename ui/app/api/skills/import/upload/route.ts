import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../_backend";
import { relayImported } from "../_imported";

export const runtime = "nodejs";

/**
 * Proxy for `POST /skills/import/upload` — import a skill from a local folder.
 *
 * Multipart body: one `files` part per file and a `paths` field per file, in
 * the same order, holding its path relative to the picked folder (the
 * browser's `webkitRelativePath`). The paths travel as their own fields
 * because a part's filename is not a reliable place to keep directories. An
 * optional `project` field names the project to load the skill into.
 */
export async function POST(request: Request) {
  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ error: "Invalid form data." }, { status: 400 });
  }

  const files = formData.getAll("files").filter((entry): entry is File => entry instanceof File);
  const paths = formData
    .getAll("paths")
    .filter((entry): entry is string => typeof entry === "string");
  if (files.length === 0) {
    return NextResponse.json({ error: "No files were provided." }, { status: 400 });
  }
  if (files.length !== paths.length) {
    return NextResponse.json(
      { error: `Got ${files.length} files but ${paths.length} paths.` },
      { status: 400 }
    );
  }

  const outgoing = new FormData();
  files.forEach((file, i) => {
    outgoing.append("files", file, file.name);
    outgoing.append("paths", paths[i]);
  });
  const project = formData.get("project");
  if (typeof project === "string" && project) outgoing.append("project", project);

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/skills/import/upload"), {
      method: "POST",
      headers: await backendHeaders({ accept: "application/json" }),
      body: outgoing,
    });
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }
  return relayImported(upstream);
}
