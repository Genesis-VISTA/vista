"use client";

// Modal for MCP elicitation requests — form mode uses @rjsf, URL mode uses a confirm dialog.
// Receives the request via a v5 `data-mcp-elicitation` UI message part.

import { useMemo, useState } from "react";
import Form from "@rjsf/core";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import type { ElicitationData } from "@/lib/types";

interface ElicitationModalProps {
  request: ElicitationData;
  onSubmit: (
    id: string,
    action: "accept" | "decline" | "cancel",
    content?: Record<string, unknown>,
  ) => void;
}

function buildUiSchema(schema: Record<string, unknown>): UiSchema {
  const ui: UiSchema = {};
  const props = schema.properties as Record<string, unknown> | undefined;
  if (!props) return ui;
  for (const key of Object.keys(props)) {
    if (/password|passcode|pin/i.test(key)) {
      ui[key] = { "ui:widget": "password" };
    }
  }
  return ui;
}

export default function ElicitationModal({ request, onSubmit }: ElicitationModalProps) {
  const [submitting, setSubmitting] = useState(false);
  const schema = request.mode !== "url" ? request.requested_schema : null;
  const uiSchema = useMemo(() => (schema ? buildUiSchema(schema) : {}), [schema]);

  if (request.mode === "url") {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        onClick={() => onSubmit(request.elicitation_id, "cancel")}
      >
        <div
          className="max-w-md w-full rounded-md bg-white p-5 shadow-xl"
          onClick={(e) => e.stopPropagation()}
        >
          <h3 className="font-semibold mb-2 text-gray-900">Confirm</h3>
          <p className="text-sm text-gray-700 whitespace-pre-wrap">{request.message}</p>
          <p className="mt-3 text-xs break-all text-blue-700">{request.url}</p>
          <div className="mt-4 flex gap-2 justify-end">
            <button
              type="button"
              className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50"
              onClick={() => onSubmit(request.elicitation_id, "cancel")}
            >
              Cancel
            </button>
            <button
              type="button"
              className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
              onClick={() => {
                onSubmit(request.elicitation_id, "accept");
                window.open(request.url, "_blank", "noopener,noreferrer");
              }}
            >
              Open Link
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={() => onSubmit(request.elicitation_id, "cancel")}
    >
      <div
        className="max-w-lg w-full rounded-md bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="font-semibold mb-2 text-gray-900">Confirm</h3>
        {request.message && (
          <p className="text-sm text-gray-700 whitespace-pre-wrap mb-3">{request.message}</p>
        )}
        <Form
          schema={schema as RJSFSchema}
          uiSchema={uiSchema}
          validator={validator}
          disabled={submitting}
          onSubmit={(data) => {
            setSubmitting(true);
            onSubmit(request.elicitation_id, "accept", data.formData);
          }}
        >
          <div className="mt-3 flex gap-2 justify-end">
            <button
              type="button"
              className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50"
              disabled={submitting}
              onClick={() => {
                setSubmitting(true);
                onSubmit(request.elicitation_id, "cancel");
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50"
              disabled={submitting}
              onClick={() => {
                setSubmitting(true);
                onSubmit(request.elicitation_id, "decline");
              }}
            >
              Decline
            </button>
            <button
              type="submit"
              className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
              disabled={submitting}
            >
              {submitting ? "Submitting…" : "Submit"}
            </button>
          </div>
        </Form>
      </div>
    </div>
  );
}
