import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SkillEditorModal, type SkillDraftFields } from "@/components/SkillEditorModal";

const DRAFT: SkillDraftFields = {
  name: "salt-density",
  description: "Predict molten salt density",
  body: "# Salt density\n\nRun the MSTDB lookup.",
  tags: "",
};

function setup(overrides: Partial<React.ComponentProps<typeof SkillEditorModal>> = {}) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const onCancel = vi.fn();
  const utils = render(
    <SkillEditorModal open initial={DRAFT} onSave={onSave} onCancel={onCancel} {...overrides} />,
  );
  return { onSave, onCancel, ...utils };
}

const save = () => screen.getByRole("button", { name: "Save" });

describe("SkillEditorModal", () => {
  it("renders nothing when closed", () => {
    const { container } = setup({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a drafting state while the backend is still writing the draft", () => {
    setup({ initial: null });
    expect(screen.getByText("Drafting from your conversation…")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Slug/)).not.toBeInTheDocument();
  });

  it("fills the form from the draft once it arrives", () => {
    setup();
    expect(screen.getByLabelText(/Slug/)).toHaveValue("salt-density");
    expect(screen.getByLabelText(/Description/)).toHaveValue("Predict molten salt density");
    expect(screen.getByLabelText(/^Body/)).toHaveValue("# Salt density\n\nRun the MSTDB lookup.");
  });

  it("surfaces a save error above the form", () => {
    setup({ errorMessage: "A skill named salt-density already exists." });
    expect(screen.getByText("A skill named salt-density already exists.")).toBeInTheDocument();
  });

  it.each([
    ["slug", /Slug/],
    ["description", /Description/],
    ["body", /^Body/],
  ])("refuses to save with an empty %s", async (_field, label) => {
    setup();
    await userEvent.clear(screen.getByLabelText(label));
    expect(save()).toBeDisabled();
  });

  it("treats whitespace as empty", async () => {
    setup();
    await userEvent.clear(screen.getByLabelText(/Slug/));
    await userEvent.type(screen.getByLabelText(/Slug/), "   ");
    expect(save()).toBeDisabled();
  });

  it("saves, trimming and splitting what the user typed", async () => {
    const { onSave } = setup();
    await userEvent.type(screen.getByLabelText(/Tags/), " Materials Design , Frontier ,, ");
    await userEvent.click(save());

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave).toHaveBeenCalledWith({
      name: "salt-density",
      description: "Predict molten salt density",
      body: "# Salt density\n\nRun the MSTDB lookup.",
      tags: ["Materials Design", "Frontier"],
    });
  });

  // Publishing goes to a collaboration server, from the Skills page; the
  // editor only saves to this computer.
  it("offers no publish", () => {
    setup();
    expect(screen.queryByRole("button", { name: /publish/i })).not.toBeInTheDocument();
  });

  it("cancels from the header and from the backdrop", async () => {
    const { onCancel, container } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(container.querySelector(".modal-backdrop") as Element);
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("keeps a click inside the dialog from dismissing it", async () => {
    const { onCancel } = setup();
    await userEvent.click(screen.getByRole("dialog", { name: "Save as skill" }));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
