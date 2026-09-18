import type { ExecutionResult } from "@/lib/types";

export function formatResultSummary(result: ExecutionResult): string {
  const status = result.ok ? "OK" : "ERROR";
  const output = result.stdout ? result.stdout.slice(0, 240) : "";
  return `${status}${output ? `: ${output}` : ""}`;
}

export function extractPlotPath(stdout: string): string | null {
  const match = stdout.match(/Plot saved to\s+(.+)/);
  if (!match) return null;
  return match[1].trim();
}

export function parseReferencesFromStdout(stdout: string): string[] {
  if (!stdout) return [];
  const refs: string[] = [];
  const lines = stdout.split(/\r?\n/);
  let inReferencesSection = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    if (/^references\b/i.test(trimmed)) {
      inReferencesSection = true;
      continue;
    }

    if (inReferencesSection) {
      if (/^=+$/.test(trimmed)) continue;
      const cleaned = trimmed
        .replace(/^\[\d+\]\s*/, "")
        .replace(/^[-*]\s*/, "")
        .trim();
      if (cleaned) refs.push(cleaned);
      continue;
    }

    const inlineRef = trimmed.match(/\b(10\.\d{4,9}\/\S+|https?:\/\/\S+)/i);
    if (inlineRef?.[1]) refs.push(inlineRef[1]);
  }

  return refs;
}

export function extractReferences(result: ExecutionResult | null): string[] {
  if (!result) return [];
  const candidates: string[] = [];

  if (Array.isArray(result.meta?.references)) {
    for (const item of result.meta.references) {
      if (typeof item === "string" && item.trim()) candidates.push(item.trim());
    }
  }

  const dataRefs = (result.data as Record<string, unknown> | undefined)?.references;
  if (Array.isArray(dataRefs)) {
    for (const item of dataRefs) {
      if (typeof item === "string" && item.trim()) candidates.push(item.trim());
    }
  }

  candidates.push(...parseReferencesFromStdout(result.stdout || ""));
  return Array.from(new Set(candidates));
}

export function extractPredictionSummary(result: ExecutionResult | null): Record<string, unknown> | null {
  if (!result?.stdout) return null;

  const summaryLine = result.stdout
    .split(/\r?\n/)
    .find((line) => line.trim().startsWith("SUMMARY_JSON:"));
  if (!summaryLine) return null;

  const jsonText = summaryLine.replace(/^.*SUMMARY_JSON:\s*/, "").trim();
  if (!jsonText) return null;

  try {
    const parsed = JSON.parse(jsonText);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * One-line preview of an intermediate agent report, used when the bubble is
 * collapsed. Pulls the first markdown heading/meaningful line and strips
 * bullet/heading punctuation so it reads like a chip label.
 */
export function intermediatePreview(content: string): string {
  const firstLine = content
    .split(/\n+/)
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith("```"));
  if (!firstLine) return "Agent update";
  const cleaned = firstLine
    .replace(/^#+\s*/, "")
    .replace(/^[-*>]\s*/, "")
    .replace(/^\*+|\*+$/g, "")
    .trim();
  return cleaned || "Agent update";
}
