import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ImageLightbox } from "@/components/ImageLightbox";

const SRC = "/api/files/outputs/salt-plots/mstdb_overview.png?project_name=molten-salt";

function setup(overrides: Partial<React.ComponentProps<typeof ImageLightbox>> = {}) {
  const onClose = vi.fn();
  const utils = render(
    <ImageLightbox src={SRC} alt="mstdb_overview.png" onClose={onClose} {...overrides} />,
  );
  return { onClose, ...utils };
}

describe("ImageLightbox", () => {
  it("shows the figure at the URL it was given", () => {
    setup();
    const image = screen.getByRole("img", { name: "mstdb_overview.png" });
    expect(image).toHaveAttribute("src", SRC);
  });

  it("is a dialog named after the figure", () => {
    setup();
    const dialog = screen.getByRole("dialog", { name: "mstdb_overview.png" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("closes on the close button", async () => {
    const { onClose } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape, like every other dialog in the app", async () => {
    const { onClose } = setup();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a click outside the figure", async () => {
    const { onClose, container } = setup();
    await userEvent.click(container.querySelector(".lightbox-backdrop")!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays open when the figure itself is clicked", async () => {
    const { onClose } = setup();
    await userEvent.click(screen.getByRole("img", { name: "mstdb_overview.png" }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("downloads one of VISTA's own files rather than opening a tab", () => {
    setup();
    const link = screen.getByRole("link", { name: "Download" });
    expect(link).toHaveAttribute("href", SRC);
    expect(link).toHaveAttribute("download");
    expect(link).not.toHaveAttribute("target");
  });

  it("opens someone else's image in a new tab", () => {
    setup({ src: "https://example.org/figure.png" });
    const link = screen.getByRole("link", { name: "Download" });
    expect(link).not.toHaveAttribute("download");
    expect(link).toHaveAttribute("target", "_blank");
  });
});
