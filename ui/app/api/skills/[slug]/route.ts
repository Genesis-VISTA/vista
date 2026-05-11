import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

type BackendSkillDetail = {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  metadata?: Record<string, string | string[]> | null;
  tags?: string[];
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
      headers: { accept: "application/json" },
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
  if (detail.metadata != null) frontmatter.metadata = detail.metadata;
  if (detail.tags != null) frontmatter.tags = detail.tags;

  return NextResponse.json({
    slug,
    frontmatter,
    markdown: detail.body ?? "",
  });
}
