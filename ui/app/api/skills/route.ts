import { NextResponse } from "next/server";
import path from "path";
import { findSkills, readProperties } from "@/lib/skills";
import { config } from "@/app/config";

export async function GET() {
  try {
    const skillDirs = findSkills([config.skillsDir]);

    const skills = skillDirs.flatMap((skillDir) => {
      try {
        const props = readProperties(skillDir);
        return [{
          slug: path.basename(skillDir),
          name: props.name,
          description: props.description,
          path: path.relative(path.dirname(config.skillsDir), skillDir).split(path.sep).join("/"),
        }];
      } catch {
        return [];
      }
    });

    skills.sort((a, b) => a.slug.localeCompare(b.slug));

    return NextResponse.json(skills);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
