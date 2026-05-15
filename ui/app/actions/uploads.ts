"use server";

// Server action for deleting an uploaded file. The list, upload, and download
// flows stay as route handlers (multipart upload + binary download cannot be
// expressed as server actions cleanly).

import { backendUrl } from "@/lib/backend";

export async function deleteUpload(name: string): Promise<void> {
  const res = await fetch(`${backendUrl}/uploads/${encodeURIComponent(name)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Failed to delete upload (${res.status}): ${detail || res.statusText}`);
  }
}
