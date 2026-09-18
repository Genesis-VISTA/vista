"use client";

import { useEffect } from "react";

type Props = {
  src: string;
  alt: string;
  onClose: () => void;
};

/**
 * A generated plot at full size.
 *
 * The artifacts column is a fixed 452px, which is enough to see that a figure
 * exists and not much more — axis labels and legends are the first things to
 * go. Rather than make the column resizable for one kind of content, the
 * figure opens over the app at whatever size the window allows.
 *
 * Deliberately not the `.modal` shell: that is a bordered card sized for
 * forms, and a figure wants the space instead.
 */
export function ImageLightbox({ src, alt, onClose }: Props) {
  // Close on Escape so it behaves like every other dialog in the app.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop lightbox-backdrop" onClick={onClose}>
      <div
        className="lightbox"
        role="dialog"
        aria-modal="true"
        aria-label={alt}
        onClick={(e) => e.stopPropagation()}
      >
        <img className="lightbox-image" src={src} alt={alt} />
        <div className="lightbox-bar">
          <span className="lightbox-name">{alt}</span>
          {/* Opening in a tab is the way to get at the file itself — zoom
              further, save it, drop it in a document. */}
          <a
            className="button ghost button-sm"
            href={src}
            target="_blank"
            rel="noreferrer"
          >
            Download
          </a>
          <button type="button" className="button ghost button-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export default ImageLightbox;
