import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SkillEditorModal, type SkillDraftFields } from "@/components/SkillEditorModal";

const DRAFT: SkillDraftFields = {
  name: "salt-density",
  description: "Predict molten salt density",
  body: "# Salt density\n\nRun the MSTDB lookup.",
  author: "",
  repoUrl: "",
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

const save = () => screen.getByRole("button", { name: /Save \(private\)/ });
const publish = () => screen.getByRole("button", { name: "Save & publish" });
// Once the confirmation opens, its buttons share labels with the editor's,
// so scope every query to the dialog under test.
const confirmDialog = () => within(screen.getByRole("dialog", { name: "Publish skill to Skill Hub" }));

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
    expect(publish()).toBeDisabled();
  });

  it("treats whitespace as empty", async () => {
    setup();
    await userEvent.clear(screen.getByLabelText(/Slug/));
    await userEvent.type(screen.getByLabelText(/Slug/), "   ");
    expect(save()).toBeDisabled();
  });

  it("saves privately, trimming and splitting what the user typed", async () => {
    const { onSave } = setup();
    await userEvent.type(screen.getByLabelText(/Author/), "  Sam Baumann  ");
    await userEvent.type(screen.getByLabelText(/Tags/), " Materials Design , Frontier ,, ");
    await userEvent.click(save());

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave).toHaveBeenCalledWith({
      name: "salt-density",
      description: "Predict molten salt density",
      body: "# Salt density\n\nRun the MSTDB lookup.",
      author: "Sam Baumann",
      repoUrl: undefined,
      tags: ["Materials Design", "Frontier"],
      isPublic: false,
    });
  });

  // Publishing cannot be undone, so it must take a second, explicit yes.
  it("does not publish until the confirmation is accepted", async () => {
    const { onSave } = setup();
    await userEvent.click(publish());
    expect(onSave).not.toHaveBeenCalled();
    expect(confirmDialog().getByText(/cannot be made private again/)).toBeInTheDocument();

    await userEvent.click(confirmDialog().getByRole("button", { name: "Save & publish" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave.mock.calls[0][0]).toMatchObject({ name: "salt-density", isPublic: true });
  });

  it("saves nothing when the publish confirmation is dismissed", async () => {
    const { onSave } = setup();
    await userEvent.click(publish());
    // The confirmation carries a Cancel in its header and another in its
    // action row; a user would reach for the second.
    const cancels = confirmDialog().getAllByRole("button", { name: "Cancel" });
    expect(cancels).toHaveLength(2);
    await userEvent.click(cancels[1]);
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Publish skill to Skill Hub" })).not.toBeInTheDocument();
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
