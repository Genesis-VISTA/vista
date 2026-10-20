import { describe, expect, it } from "vitest";
import { readRefusedSend } from "@/lib/refused-send";

describe("readRefusedSend", () => {
  it("shows the running turn when the conversation is busy", () => {
    const body = { detail: "This conversation already has a turn running.", code: "run_busy" };
    expect(readRefusedSend(409, body)).toEqual({ kind: "attach" });
  });

  it("says why when no inference key or model is configured", () => {
    const detail = "No inference API key is configured, so 'claude-sonnet' cannot be reached.";
    expect(readRefusedSend(409, { detail, code: "inference_not_configured" })).toEqual({
      kind: "note",
      message: detail,
    });
  });

  it("does not take a 409 without a code for a busy conversation", () => {
    expect(readRefusedSend(409, { detail: "Conflict." })).toEqual({
      kind: "note",
      message: "Conflict.",
    });
  });

  it("uses the proxy's wrapped error for a body that was not JSON", () => {
    expect(readRefusedSend(500, { ok: false, error: "Internal Server Error" })).toEqual({
      kind: "note",
      message: "Internal Server Error",
    });
  });

  it.each([null, "not an object", { detail: 42 }, { detail: "" }])(
    "falls back to the status when the body says nothing usable (%j)",
    (body) => {
      expect(readRefusedSend(502, body)).toEqual({
        kind: "note",
        message: "Backend returned 502",
      });
    },
  );
});
