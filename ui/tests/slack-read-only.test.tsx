/**
 * A conversation started from Slack is read-only on the desktop (openspec change
 * slack-channel, task 4.2): its prompts are shown without answer controls and
 * its queued messages without edit or delete.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ElicitationModal from "@/components/ElicitationModal";
import MessageQueue from "@/components/MessageQueue";
import ToolApprovalModal from "@/components/ToolApprovalModal";
import { ANSWER_IN_SLACK } from "@/lib/slack";

describe("a Slack conversation's prompts on the desktop", () => {
  it("shows a tool approval with a note and Close, and answers nothing", async () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <ToolApprovalModal
        id="e1"
        toolName="submit_hpc_job"
        message="Submit flibe-md to Frontier?"
        args={{ nodes: 2 }}
        onSubmit={onSubmit}
        elsewhere={ANSWER_IN_SLACK}
        onClose={onClose}
      />,
    );
    expect(screen.getByText("Submit flibe-md to Frontier?")).toBeInTheDocument();
    expect(screen.getByText(ANSWER_IN_SLACK)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows a choice or form with a note and Close, and answers nothing", async () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <ElicitationModal
        id="e2"
        message="Approve the transfer?"
        schema={{
          type: "object",
          properties: { choice: { type: "string", enum: ["Approve", "Decline"] } },
          required: ["choice"],
        }}
        onSubmit={onSubmit}
        elsewhere={ANSWER_IN_SLACK}
        onClose={onClose}
      />,
    );
    expect(screen.getByText("Approve the transfer?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Submit/ })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("a Slack conversation's queued messages on the desktop", () => {
  it("are listed without edit or delete", () => {
    render(
      <MessageQueue
        items={[{ id: 1, kind: "message", text: "and Lux?", state: "queued", created_at: "2026-11-17T10:00:00Z" }]}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        readOnly
      />,
    );
    expect(screen.getByText("and Lux?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit queued message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete queued message" })).toBeNull();
  });
});
