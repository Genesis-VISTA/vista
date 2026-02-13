import { NextResponse } from "next/server";
import path from "path";
import fs from "fs/promises";
import matter from "gray-matter";

export async function GET(
  _request: Request,
  { params }: { params: { slug: string } }
) {
  const slug = params.slug;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  const repoRoot = path.resolve(process.cwd(), "..");
  const skillPath = path.join(repoRoot, "skills", slug, "SKILL.md");

  try {
    const raw = await fs.readFile(skillPath, "utf8");
    const parsed = matter(raw);
    return NextResponse.json({
      slug,
      frontmatter: parsed.data ?? {},
      markdown: parsed.content
    });
  } catch (error) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }
}
