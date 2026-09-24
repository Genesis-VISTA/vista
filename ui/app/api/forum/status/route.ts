import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * A project's forum state, for the UI to warn about: whether the lab is on,
 * whether this machine has a usable git (`git_ok`, `git_reason`), whether the
 * forum is shared, and how many of our posts are still waiting to publish.
 *
 * Project-scoped, because the repository is: a forum is a room, and who may
 * post to it is who has push access to that repository — a different set of
 * people for every line of work. A project with no repository has no Hypothesis
 * Lab, and this is where the page finds that out. Passed through unchanged; the
 * shape is `ForumStatus` in `lib/debates.ts`.
 */
export async function GET(request: Request) {
  const projectName = new URL(request.url).searchParams.get("project_name");
  if (!projectName) {
    return NextResponse.json({ error: "project_name is required" }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/forum/status`),
      { headers: await backendHeaders({ accept: "application/json" }) }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
