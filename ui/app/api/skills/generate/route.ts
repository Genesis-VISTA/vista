import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `POST /skills/generate`.
 *
 * The client posts the active chat's `message_history` (raw PydanticAI
 * `ModelMessage` objects accumulated by the chat page) plus an optional
 * `hint`. The backend runs the LLM with `output_type=SkillDraft` and returns
 * `{ name_suggestion, description_suggestion, body }`, which the UI uses to
 * pre-fill the Save-as-Skill modal.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/skills/generate"), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }

  const text = await upstream.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { error: text };
  }
  if (!upstream.ok) {
    return NextResponse.json(payload ?? { error: "Failed" }, { status: upstream.status });
  }
  return NextResponse.json(payload, { status: 200 });
}
