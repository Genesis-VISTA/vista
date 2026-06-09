import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

type BackendSkillDetail = {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  metadata?: Record<string, string | string[]> | null;
  author?: string | null;
  repo_url?: string | null;
  is_public?: boolean;
  created_at?: string;
  updated_at?: string;
  body: string;
};

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/skills/${encodeURIComponent(slug)}`), {
      headers: await backendHeaders({ accept: "application/json" }),
    });
  } catch {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  if (upstream.status === 404) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }
  if (!upstream.ok) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  const detail = (await upstream.json()) as BackendSkillDetail;
  return NextResponse.json(reshape(slug, detail));
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/skills/${encodeURIComponent(slug)}`), {
      method: "PATCH",
      headers: await backendHeaders({
        "content-type": "application/json",
        accept: "application/json",
      }),
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }

  if (upstream.status === 404) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }
  if (!upstream.ok) {
    const text = await upstream.text().catch(() => "");
    return NextResponse.json(
      { error: text || "Failed to update skill" },
      { status: upstream.status }
    );
  }

  const detail = (await upstream.json()) as BackendSkillDetail;
  return NextResponse.json(reshape(slug, detail));
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/skills/${encodeURIComponent(slug)}`), {
      method: "DELETE",
      headers: await backendHeaders({ accept: "application/json" }),
    });
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }

  if (upstream.status === 404) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }
  if (!upstream.ok) {
    const text = await upstream.text().catch(() => "");
    return NextResponse.json(
      { error: text || "Failed to delete skill" },
      { status: upstream.status }
    );
  }
  return new NextResponse(null, { status: 204 });
}

function reshape(slug: string, detail: BackendSkillDetail) {
  // Reshape into the {slug, frontmatter, markdown} envelope the frontend
  // expects. `body` becomes `markdown`; the rest of the AgentSkills metadata
  // becomes `frontmatter` (matching the layout of SKILL.md's YAML header).
  const frontmatter: Record<string, unknown> = {
    name: detail.name,
    description: detail.description,
  };
  if (detail.license != null) frontmatter.license = detail.license;
  if (detail.compatibility != null) frontmatter.compatibility = detail.compatibility;
  if (detail.allowed_tools != null) frontmatter["allowed-tools"] = detail.allowed_tools;
  // Tags now live inside `metadata` (under a `tags` key), so they round-trip
  // into the frontmatter via the line above; no separate `tags` field anymore.
  if (detail.metadata != null) frontmatter.metadata = detail.metadata;
  if (detail.author != null) frontmatter.author = detail.author;
  if (detail.repo_url != null) frontmatter.repo_url = detail.repo_url;
  if (detail.is_public != null) frontmatter.is_public = detail.is_public;

  return {
    slug,
    frontmatter,
    markdown: detail.body ?? "",
    author: detail.author ?? null,
    repoUrl: detail.repo_url ?? null,
    isPublic: detail.is_public ?? false,
  };
}
