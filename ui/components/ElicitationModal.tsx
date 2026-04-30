"use client";

import { useState, useMemo } from "react";
import Form from "@rjsf/core";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";

type Props = {
  id: string;
  message: string;
  schema: Record<string, unknown>;
  onSubmit: (id: string, action: "accept" | "decline" | "cancel", content?: Record<string, unknown>) => void;
};

/**
 * Auto-generates uiSchema to render password fields for properties
 * whose name contains "password" (case-insensitive).
 */
function buildUiSchema(schema: Record<string, unknown>): UiSchema {
  const ui: UiSchema = {};
  const props = schema.properties as Record<string, unknown> | undefined;
  if (!props) return ui;

  for (const key of Object.keys(props)) {
    if (/password|passcode|pin/i.test(key.toLocaleLowerCase())) {
      ui[key] = { "ui:widget": "password" };
    }
  }
  return ui;
}

export default function ElicitationModal({ id, message, schema, onSubmit }: Props) {
  const [submitting, setSubmitting] = useState(false);

  const uiSchema = useMemo(() => buildUiSchema(schema), [schema]);

  const handleAccept = (data: { formData?: Record<string, unknown> }) => {
    setSubmitting(true);
    onSubmit(id, "accept", data.formData);
  };

  const handleDecline = () => {
    setSubmitting(true);
    onSubmit(id, "decline");
  };

  const handleCancel = () => {
    setSubmitting(true);
    onSubmit(id, "cancel");
  };

  return (
    <div className="modal-backdrop" onClick={handleCancel}>
      <div className="modal elicitation-modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <div className="panel-title">Confirm</div>
        </div>
        <div className="modal-body">
          {message && <p className="elicitation-message" style={{ whiteSpace: "pre-wrap" }}>{message}</p>}
          <Form
            schema={schema as RJSFSchema}
            uiSchema={uiSchema}
            validator={validator}
            onSubmit={handleAccept}
            disabled={submitting}
          >
            {/* Custom buttons — hides RJSF default submit */}
            <div className="elicitation-actions">
              <button type="submit" className="button" disabled={submitting}>
                {submitting ? "Submitting..." : "Submit"}
              </button>
              <button type="button" className="button ghost" onClick={handleDecline} disabled={submitting}>
                Decline
              </button>
              <button type="button" className="button ghost" onClick={handleCancel} disabled={submitting}>
                Cancel
              </button>
            </div>
          </Form>
        </div>
      </div>
    </div>
  );
}
