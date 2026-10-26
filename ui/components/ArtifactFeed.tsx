"use client";

import { useState } from "react";
import Link from "next/link";
import FilePreview from "@/components/FilePreview";
import type { DisplayedFile } from "@/lib/displayed-files";
import { filesHref } from "@/lib/files-tree";

type Props = {
  items: DisplayedFile[];
  projectName: string | null;
  /** Element ids the page is pointing at; a card among them is highlighted. */
  highlighted?: readonly string[];
  onZoom: (image: { src: string; alt: string }) => void;
  /** Scroll the conversation to the turn that displayed this file. */
  onJumpToTurn: (item: DisplayedFile) => void;
};

/** The DOM id of a feed card, which a jump to its turn scrolls to and highlights. */
export function artifactElementId(item: Pick<DisplayedFile, "run_id" | "tool_call_id">): string {
  return `artifact-${item.run_id}-${item.tool_call_id}`;
}

/**
 * The image for one display. The display's id is part of the URL: a page reuses
 * an image it has already loaded from the same URL, so a re-plot to the same path
 * would otherwise show the old figure under the new display.
 */
function fileSrc(item: DisplayedFile, projectName: string | null): string {
  const encoded = item.path.split("/").map(encodeURIComponent).join("/");
  const params = new URLSearchParams();
  if (projectName) params.set("project_name", projectName);
  params.set("preview", "1");
  params.set("v", item.tool_call_id);
  return `/api/files/${item.kind}/${encoded}?${params.toString()}`;
}

function FeedItem({
  item,
  projectName,
  highlighted,
  onZoom,
  onJumpToTurn,
}: { item: DisplayedFile } & Omit<Props, "items">) {
  const [open, setOpen] = useState(false);
  const isImage = item.mime_type.startsWith("image/");
  const id = artifactElementId(item);

  return (
    <li
      id={id}
      className={`artifact-item${highlighted?.includes(id) ? " turn-highlight" : ""}`}
      data-status={item.status}
    >
      {item.status === "current" && isImage ? (
        <button
          type="button"
          className="artifact-zoom"
          onClick={() => onZoom({ src: fileSrc(item, projectName), alt: item.name })}
          title="Show this figure full size"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={fileSrc(item, projectName)} alt={item.name} />
        </button>
      ) : null}

      <div className="artifact-item-head">
        {item.status === "current" && !isImage ? (
          <button
            type="button"
            className="artifact-item-toggle"
            aria-expanded={open}
            onClick={() => setOpen((prev) => !prev)}
          >
            <span aria-hidden="true">{open ? "▾" : "▸"}</span> {item.name}
          </button>
        ) : (
          <span className="artifact-item-name">{item.name}</span>
        )}
        {item.status === "changed" && (
          <span className="artifact-item-note">file has changed on disk</span>
        )}
        {item.status === "missing" && (
          <span className="artifact-item-note">file no longer exists</span>
        )}
        <span className="artifact-item-actions">
          {item.status !== "missing" && (
            <Link className="button ghost button-xs" href={filesHref(item.kind, item.path)}>
              Open in Files
            </Link>
          )}
          <button type="button" className="button ghost button-xs" onClick={() => onJumpToTurn(item)}>
            Show in conversation
          </button>
        </span>
      </div>

      {item.status === "current" && !isImage && open && (
        <FilePreview
          kind={item.kind}
          path={item.path}
          name={item.name}
          projectName={projectName}
          version={item.tool_call_id}
        />
      )}
    </li>
  );
}

/** The files `display_file` showed in the open conversation, newest first. */
export default function ArtifactFeed({ items, ...rest }: Props) {
  if (items.length === 0) return null;
  return (
    <ul className="artifact-feed" aria-label="Files shown in this conversation">
      {items.map((item) => (
        <FeedItem key={`${item.run_id}/${item.tool_call_id}`} item={item} {...rest} />
      ))}
    </ul>
  );
}
