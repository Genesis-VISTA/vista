import { NextResponse } from "next/server";
import path from "path";
import fs from "fs";
import { findSkills, findSkillMd, readProperties } from "@/lib/skills";
import { config } from "@/app/config";

export async function GET() {
  try {
    const skillDirs = findSkills([config.skillsDir]);

    const skills = skillDirs.flatMap((skillDir) => {
      try {
        const props = readProperties(skillDir);
        const skillMd = findSkillMd(skillDir);
        let addedAt: number | null = null;
        if (skillMd) {
          try {
            // Use the SKILL.md mtime as a "recently added" proxy. We don't
            // shell out to git here so the response stays fast and works in
            // trees without git history.
            addedAt = fs.statSync(skillMd).mtimeMs;
          } catch {
            addedAt = null;
          }
        }
        return [{
          slug: path.basename(skillDir),
          name: props.name,
          description: props.description,
          path: path.relative(path.dirname(config.skillsDir), skillDir).split(path.sep).join("/"),
          metadata: props.metadata,
          tags: props.tags ?? [],
          addedAt,
        }];
      } catch (error) {
        console.warn(`[skills] Skipping invalid skill '${skillDir}':`, error);
        return [];
      }
    });

    skills.sort((a, b) => a.slug.localeCompare(b.slug));

    return NextResponse.json(skills);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
