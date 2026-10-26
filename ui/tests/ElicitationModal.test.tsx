import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ElicitationModal from "@/components/ElicitationModal";

const SCHEMA = {
  type: "object",
  required: ["username"],
  properties: {
    username: { type: "string", title: "Username" },
    api_password: { type: "string", title: "API password" },
    remember: { type: "boolean", title: "Remember me" },
  },
};

function setup(overrides: Partial<React.ComponentProps<typeof ElicitationModal>> = {}) {
  const onSubmit = vi.fn();
  const utils = render(
    <ElicitationModal
      id="elicit-1"
      message="The cluster needs your credentials."
      schema={SCHEMA}
      onSubmit={onSubmit}
      {...overrides}
    />,
  );
  return { onSubmit, ...utils };
}

describe("ElicitationModal", () => {
  it("shows the prompt and a control for every schema property", () => {
    setup();
    expect(screen.getByText("The cluster needs your credentials.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Username/)).toBeInTheDocument();
    expect(screen.getByLabelText(/API password/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Remember me/)).toBeInTheDocument();
  });

  // A field the backend names as a secret must not be typed in clear text.
  it("masks properties whose name reads as a secret", () => {
    setup();
    expect(screen.getByLabelText(/API password/)).toHaveAttribute("type", "password");
    expect(screen.getByLabelText(/Username/)).toHaveAttribute("type", "text");
  });

  it.each(["passcode", "PIN", "userPassword"])(
    "masks the %s property too",
    (name) => {
      setup({
        schema: { type: "object", properties: { [name]: { type: "string", title: name } } },
      });
      expect(screen.getByLabelText(new RegExp(name))).toHaveAttribute("type", "password");
    },
  );

  it("returns the entered values on submit", async () => {
    const { onSubmit } = setup();
    await userEvent.type(screen.getByLabelText(/Username/), "sbaumann");
    await userEvent.type(screen.getByLabelText(/API password/), "hunter2");
    await userEvent.click(screen.getByLabelText(/Remember me/));
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit).toHaveBeenCalledWith("elicit-1", "accept", {
      username: "sbaumann",
      api_password: "hunter2",
      remember: true,
    });
  });

  // Declining is a deliberate "no", and must carry no form content with it.
  it("declines with no content", async () => {
    const { onSubmit } = setup();
    await userEvent.type(screen.getByLabelText(/Username/), "sbaumann");
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("elicit-1", "decline");
  });

  it("cancels with no content", async () => {
    const { onSubmit } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("elicit-1", "cancel");
  });

  it("cancels when the backdrop is dismissed", async () => {
    const { onSubmit, container } = setup();
    await userEvent.click(container.querySelector(".modal-backdrop") as Element);
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("elicit-1", "cancel");
  });

  it("keeps a click inside the dialog from dismissing it", async () => {
    const { onSubmit } = setup();
    await userEvent.click(screen.getByText("The cluster needs your credentials."));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not submit while a required field is empty", async () => {
    const { onSubmit } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("disables every action once a decision is made", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(screen.getByRole("button", { name: "Submitting..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  describe("one choice", () => {
    const CHOICE = {
      type: "object",
      title: "ScalarElicitationType",
      required: ["value"],
      properties: { value: { type: "string", title: "Value", enum: ["Approve", "Decline"] } },
    };

    it("shows exactly the two options as buttons, with no form", () => {
      const { container } = setup({ message: "Download 2 files?", schema: CHOICE });
      expect(screen.getByText("Download 2 files?")).toBeInTheDocument();
      expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Approve", "Decline"]);
      expect(container.querySelector("form")).toBeNull();
    });

    it.each(["Approve", "Decline"])("accepts with %s as the value", async (label) => {
      const { onSubmit } = setup({ schema: CHOICE });
      await userEvent.click(screen.getByRole("button", { name: label }));
      expect(onSubmit).toHaveBeenCalledExactlyOnceWith("elicit-1", "accept", { value: label });
    });

    it("disables both buttons after one press", async () => {
      setup({ schema: CHOICE });
      await userEvent.click(screen.getByRole("button", { name: "Approve" }));
      expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Decline" })).toBeDisabled();
    });

    it("still cancels when the backdrop is dismissed", async () => {
      const { onSubmit, container } = setup({ schema: CHOICE });
      await userEvent.click(container.querySelector(".modal-backdrop") as Element);
      expect(onSubmit).toHaveBeenCalledExactlyOnceWith("elicit-1", "cancel");
    });

    it("keeps the form for an optional enum", () => {
      setup({ schema: { ...CHOICE, required: [] } });
      expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument();
    });
  });

  it("renders without a message", () => {
    setup({ message: "" });
    expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument();
  });
});
