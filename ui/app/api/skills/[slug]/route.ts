import { NextResponse } from "next/server";
import path from "path";
import fs from "fs";
import matter from "gray-matter";
import { findSkillMd } from "@/lib/skills";
import { config } from "@/app/config";

export async function GET(
  _request: Request,
  { params }: { params: { slug: string } }
) {
  const slug = params.slug;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  const skillDir = path.join(config.skillsDir, slug);
  const skillMd = findSkillMd(skillDir);
  if (skillMd === null) {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }

  try {
    const raw = fs.readFileSync(skillMd, "utf8");
    const parsed = matter(raw);
    return NextResponse.json({
      slug,
      frontmatter: parsed.data ?? {},
      markdown: parsed.content,
    });
  } catch {
    return NextResponse.json({ error: "Skill not found" }, { status: 404 });
  }
}
