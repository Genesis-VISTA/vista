import { NextResponse } from "next/server";
import { resolveElicitation } from "@/lib/elicitation-bridge";
import type { ElicitResult } from "@modelcontextprotocol/sdk/types.js";

type ElicitationSubmit = {
  id: string;
  action: ElicitResult["action"];
  content?: ElicitResult["content"];
};

export async function POST(request: Request) {
  let body: ElicitationSubmit;
  try {
    body = (await request.json()) as ElicitationSubmit;
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON" }, { status: 400 });
  }

  const { id, action, content } = body;
  if (!id || !action) {
    return NextResponse.json({ ok: false, error: "Missing id or action" }, { status: 400 });
  }

  const found = resolveElicitation(id, { action, content });
  if (!found) {
    return NextResponse.json(
      { ok: false, error: "Unknown or expired elicitation" },
      { status: 404 }
    );
  }

  return NextResponse.json({ ok: true });
}
