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

/**
 * A prompt that asks for one required string enum (an Approve/Decline
 * question) is shown as one button per value instead of a form.
 */
export function singleChoice(
  schema: Record<string, unknown>,
): { key: string; options: string[] } | null {
  const props = schema.properties as Record<string, Record<string, unknown>> | undefined;
  if (!props) return null;
  const keys = Object.keys(props);
  if (keys.length !== 1) return null;
  const key = keys[0];
  const required = schema.required;
  if (!Array.isArray(required) || !required.includes(key)) return null;
  const field = props[key];
  const options = field?.enum;
  if (field?.type !== "string" || !Array.isArray(options) || options.length === 0) return null;
  if (!options.every((o) => typeof o === "string")) return null;
  return { key, options: options as string[] };
}

export default function ElicitationModal({ id, message, schema, onSubmit }: Props) {
  const [submitting, setSubmitting] = useState(false);

  const uiSchema = useMemo(() => buildUiSchema(schema), [schema]);
  const choice = useMemo(() => singleChoice(schema), [schema]);

  const handleChoose = (key: string, value: string) => {
    setSubmitting(true);
    onSubmit(id, "accept", { [key]: value });
  };

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
          {choice ? (
            <div className="elicitation-actions">
              {choice.options.map((option, i) => (
                <button
                  key={option}
                  type="button"
                  className={i === 0 ? "button" : "button ghost"}
                  onClick={() => handleChoose(choice.key, option)}
                  disabled={submitting}
                >
                  {option}
                </button>
              ))}
            </div>
          ) : (
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
          )}
        </div>
      </div>
    </div>
  );
}
