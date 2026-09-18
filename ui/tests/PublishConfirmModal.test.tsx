import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";

function setup(overrides: Partial<React.ComponentProps<typeof PublishConfirmModal>> = {}) {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();
  const utils = render(
    <PublishConfirmModal
      open
      title="Publish skill to Skill Hub"
      message="Publishing is permanent."
      onCancel={onCancel}
      onConfirm={onConfirm}
      {...overrides}
    />,
  );
  return { onCancel, onConfirm, ...utils };
}

describe("PublishConfirmModal", () => {
  it("renders nothing when closed", () => {
    const { container } = setup({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("names itself as a dialog so the title reaches assistive tech", () => {
    setup();
    expect(screen.getByRole("dialog", { name: "Publish skill to Skill Hub" })).toBeInTheDocument();
    expect(screen.getByText("Publishing is permanent.")).toBeInTheDocument();
  });

  it("confirms only on the confirm action", async () => {
    const { onConfirm, onCancel } = setup({ confirmLabel: "Save & publish" });
    await userEvent.click(screen.getByRole("button", { name: "Save & publish" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("cancels from the header button, the body button, and the backdrop", async () => {
    const { onCancel, onConfirm, container } = setup({ cancelActionLabel: "Keep private" });
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep private" }));
    await userEvent.click(container.querySelector(".modal-backdrop") as Element);
    expect(onCancel).toHaveBeenCalledTimes(3);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  // Publishing is irreversible, so a slow request must not leave a second
  // click able to fire it again, or a stray backdrop click able to cancel it.
  it("locks every control while the publish is in flight", async () => {
    const { onCancel, onConfirm, container } = setup({ busy: true });
    expect(screen.getByRole("button", { name: "Publishing…" })).toBeDisabled();
    // Two of them: one in the header, one in the action row.
    const cancels = screen.getAllByRole("button", { name: "Cancel" });
    expect(cancels).toHaveLength(2);
    cancels.forEach((button) => expect(button).toBeDisabled());
    await userEvent.click(container.querySelector(".modal-backdrop") as Element);
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("uses the default labels when none are supplied", () => {
    setup();
    expect(screen.getByRole("button", { name: "Publish" })).toBeInTheDocument();
  });
});
