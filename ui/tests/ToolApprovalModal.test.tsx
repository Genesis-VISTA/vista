import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ToolApprovalModal, { type DecisionMetadata } from "@/components/ToolApprovalModal";

function setup(overrides: Partial<React.ComponentProps<typeof ToolApprovalModal>> = {}) {
  const onSubmit = vi.fn();
  const utils = render(
    <ToolApprovalModal
      id="req-1"
      toolName="submit_hpc_job"
      message="Approve this job submission?"
      onSubmit={onSubmit}
      {...overrides}
    />,
  );
  return { onSubmit, ...utils };
}

const G5: DecisionMetadata = {
  gate: "G5",
  account: "mat123",
  account_verified: true,
  resource_ceiling_passed: true,
  binary_denylist_match: null,
  requested_nodes: 4,
  requested_gpus: 8,
  requested_time_seconds: 3600,
  partition: "batch",
  resolved_script: "#!/bin/bash\n#SBATCH -N 4\nsrun ./density",
};

describe("ToolApprovalModal", () => {
  it("reports an approval with the request id", async () => {
    const { onSubmit } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("req-1", "accept");
  });

  it("reports a decline with the request id", async () => {
    const { onSubmit } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("req-1", "decline");
  });

  // Dismissing by clicking away must never be read as consent.
  it("declines when the backdrop is dismissed", async () => {
    const { onSubmit, container } = setup();
    const backdrop = container.querySelector(".modal-backdrop");
    expect(backdrop).not.toBeNull();
    await userEvent.click(backdrop as Element);
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("req-1", "decline");
  });

  it("keeps a click inside the dialog from dismissing it", async () => {
    const { onSubmit } = setup();
    await userEvent.click(screen.getByText("Approve this job submission?"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  // A double-click on Approve must not submit the same request twice.
  it("disables both actions after the first decision", async () => {
    const { onSubmit } = setup();
    const approve = screen.getByRole("button", { name: "Approve" });
    await userEvent.click(approve);
    expect(screen.getByRole("button", { name: "Submitting..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Decline" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Submitting..." }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("falls back to naming the tool when no message is supplied", () => {
    setup({ message: "" });
    expect(screen.getByText("Approve call to submit_hpc_job?")).toBeInTheDocument();
  });

  describe("G5 decision metadata", () => {
    it("shows the passing policy checks, the resources, and the resolved script", () => {
      const { container } = setup({ decisionMetadata: G5 });
      expect(screen.getByText("PALISADE G5 policy checks")).toBeInTheDocument();
      expect(screen.getByText(/Allocation\/account verified \(mat123\)/)).toBeInTheDocument();
      expect(container.querySelectorAll(".g5-check.fail")).toHaveLength(0);
      expect(container.querySelectorAll(".g5-check.ok")).toHaveLength(3);
      expect(screen.getByText("nodes: 4")).toBeInTheDocument();
      expect(screen.getByText("gpus: 8")).toBeInTheDocument();
      expect(screen.getByText("time: 3600s")).toBeInTheDocument();
      expect(screen.getByText("partition: batch")).toBeInTheDocument();
      expect(screen.getByText(/#SBATCH -N 4/)).toBeInTheDocument();
    });

    // The whole point of the gate is that a failed check is visible before
    // the user approves, so assert the failure styling, not just the label.
    it("marks an unverified account as failed", () => {
      const { container } = setup({
        decisionMetadata: { ...G5, account_verified: false },
      });
      const failed = container.querySelectorAll(".g5-check.fail");
      expect(failed).toHaveLength(1);
      expect(failed[0].textContent).toContain("Allocation/account verified");
    });

    it("marks a denylisted binary as failed", () => {
      const { container } = setup({
        decisionMetadata: { ...G5, binary_denylist_match: "xmrig" },
      });
      const failed = container.querySelectorAll(".g5-check.fail");
      expect(failed).toHaveLength(1);
      expect(failed[0].textContent).toContain("No mining-binary denylist match");
    });

    it("marks a breached resource ceiling as failed", () => {
      const { container } = setup({
        decisionMetadata: { ...G5, resource_ceiling_passed: false },
      });
      const failed = container.querySelectorAll(".g5-check.fail");
      expect(failed).toHaveLength(1);
      expect(failed[0].textContent).toContain("Resource ceiling within limits");
    });

    it("omits the account check when the backend sent no verdict", () => {
      const { container } = setup({
        decisionMetadata: { ...G5, account_verified: null },
      });
      expect(container.querySelectorAll(".g5-check")).toHaveLength(2);
      expect(screen.queryByText(/Allocation\/account verified/)).not.toBeInTheDocument();
    });
  });

  it("shows raw arguments for a tool call with no gate metadata", () => {
    setup({ toolName: "run_bash", args: { command: "ls -la" }, decisionMetadata: null });
    expect(screen.queryByText("PALISADE G5 policy checks")).not.toBeInTheDocument();
    expect(screen.getByText(/"command": "ls -la"/)).toBeInTheDocument();
  });
});
