import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReportModal, type ReportDraft } from "@/components/ReportModal";

const DRAFT: ReportDraft = {
  title: "LiF-NaF eutectic density",
  slug_suggestion: "lif-naf-eutectic-density",
  summary: "Computed the eutectic density at 1000 K.",
  body: "## Summary\n\nDensity found.\n\n![Density](/mnt/data/output/plots/density.png)\n\n## Record\n\n- rho = 1.95",
};

function setup(overrides: Partial<React.ComponentProps<typeof ReportModal>> = {}) {
  const handlers = {
    onRegenerate: vi.fn(),
    onSave: vi.fn(),
    onSaveAsSkill: vi.fn(),
    onClose: vi.fn(),
  };
  const utils = render(
    <ReportModal open projectName="molten-salt" draft={DRAFT} {...handlers} {...overrides} />
  );
  return { ...handlers, ...utils };
}

describe("ReportModal", () => {
  it("renders nothing when closed", () => {
    const { container } = setup({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a drafting state with no save actions until the draft arrives", () => {
    setup({ draft: null });
    expect(screen.getByText(/Writing a report of your conversation/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save to project" })).not.toBeInTheDocument();
  });

  it("renders the draft and serves linked plots through the file route", () => {
    setup();
    expect(screen.getByRole("dialog", { name: "Conversation report" })).toBeInTheDocument();
    expect(screen.getByText("Computed the eutectic density at 1000 K.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Record" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Density" })).toHaveAttribute(
      "src",
      "/api/files/outputs/plots/density.png?project_name=molten-salt"
    );
  });

  it("saves the draft as shown", async () => {
    const { onSave } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Save to project" }));
    expect(onSave).toHaveBeenCalledWith({
      title: DRAFT.title,
      summary: DRAFT.summary,
      slug: DRAFT.slug_suggestion,
      body: DRAFT.body,
    });
  });

  it("saves hand edits", async () => {
    const { onSave } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const title = screen.getByLabelText("Title");
    await userEvent.clear(title);
    await userEvent.type(title, "Revised title");
    const body = screen.getByLabelText("Report (Markdown)");
    await userEvent.clear(body);
    await userEvent.type(body, "Edited body");
    await userEvent.click(screen.getByRole("button", { name: "Save to project" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Revised title", body: "Edited body" })
    );
  });

  it("regenerates with the focus hint", async () => {
    const { onRegenerate } = setup();
    await userEvent.type(screen.getByLabelText("Regenerate focus"), "  the density step ");
    await userEvent.click(screen.getByRole("button", { name: "Regenerate with focus…" }));
    expect(onRegenerate).toHaveBeenCalledWith("the density step");
  });

  it("a new draft replaces hand edits", async () => {
    const { rerender, onRegenerate, onSave, onSaveAsSkill, onClose } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getByLabelText("Report (Markdown)"));
    await userEvent.type(screen.getByLabelText("Report (Markdown)"), "stale edit");
    rerender(
      <ReportModal
        open
        projectName="molten-salt"
        draft={{ ...DRAFT, body: "## Summary\n\nFresh draft." }}
        {...{ onRegenerate, onSave, onSaveAsSkill, onClose }}
      />
    );
    expect(screen.getByText("Fresh draft.")).toBeInTheDocument();
    expect(screen.queryByText("stale edit")).not.toBeInTheDocument();
  });

  it("disables saving when drafting failed", () => {
    setup({ draft: { ...DRAFT, title: "", body: "" }, errorMessage: "No inference key configured." });
    expect(screen.getByText("No inference key configured.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save to project" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as skill" })).toBeDisabled();
  });

  it("passes the current body to Save as skill", async () => {
    const { onSaveAsSkill } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Report (Markdown)"), "\nextra note");
    await userEvent.click(screen.getByRole("button", { name: "Save as skill" }));
    expect(onSaveAsSkill).toHaveBeenCalledWith(`${DRAFT.body}\nextra note`);
  });

  it("confirms where the report was saved", () => {
    setup({ savedPath: "reports/lif-naf-eutectic-density.md" });
    expect(screen.getByText("reports/lif-naf-eutectic-density.md")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save again" })).toBeInTheDocument();
  });
});
