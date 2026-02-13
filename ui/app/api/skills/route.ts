import { NextResponse } from "next/server";
import path from "path";
import fs from "fs/promises";
import matter from "gray-matter";

async function listSkillFiles(rootDir: string): Promise<string[]> {
  const results: string[] = [];

  async function walk(dir: string) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(fullPath);
      } else if (entry.isFile() && entry.name === "SKILL.md") {
        results.push(fullPath);
      }
    }
  }

  await walk(rootDir);
  return results;
}

export async function GET() {
  try {
    const repoRoot = path.resolve(process.cwd(), "..");
    const skillsRoot = path.join(repoRoot, "skills");
    await fs.access(skillsRoot);
    const skillFiles = await listSkillFiles(skillsRoot);

    const skills = await Promise.all(
      skillFiles.map(async (filePath) => {
        const raw = await fs.readFile(filePath, "utf8");
        const parsed = matter(raw);
        const slug = path.basename(path.dirname(filePath));
        return {
          slug,
          name: typeof parsed.data.name === "string" ? parsed.data.name : slug,
          description: typeof parsed.data.description === "string" ? parsed.data.description : "",
          path: path.relative(repoRoot, filePath).split(path.sep).join("/")
        };
      })
    );

    skills.sort((a, b) => {
      const slugCmp = a.slug.localeCompare(b.slug);
      if (slugCmp !== 0) return slugCmp;
      return a.name.localeCompare(b.name);
    });

    return NextResponse.json(skills);
  } catch (error) {
    return NextResponse.json([], { status: 200 });
  }
}
