import { NextResponse } from "next/server";
import { resolveElicitation } from "@/lib/elicitation-bridge";

type ElicitationSubmit = {
  id: string;
  action: "accept" | "decline" | "cancel";
  content?: Record<string, unknown>;
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
