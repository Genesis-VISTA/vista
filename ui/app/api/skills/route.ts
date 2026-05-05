import { NextResponse } from "next/server";
import path from "path";
import { findSkills, readProperties } from "@/lib/skills";
import { config } from "@/app/config";

const DEFAULT_TAB = "molten-salt";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const tab = url.searchParams.get("tab");

    const skillDirs = findSkills([config.skillsDir]);

    const skills = skillDirs.flatMap((skillDir) => {
      try {
        const props = readProperties(skillDir);
        return [{
          slug: path.basename(skillDir),
          name: props.name,
          description: props.description,
          path: path.relative(path.dirname(config.skillsDir), skillDir).split(path.sep).join("/"),
          metadata: props.metadata,
          tags: props.tags ?? [],
        }];
      } catch (error) {
        console.warn(`[skills] Skipping invalid skill '${skillDir}':`, error);
        return [];
      }
    });

    const filtered = tab
      ? skills.filter((s) => (s.metadata?.tab ?? DEFAULT_TAB) === tab)
      : skills;

    filtered.sort((a, b) => a.slug.localeCompare(b.slug));

    return NextResponse.json(filtered);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
