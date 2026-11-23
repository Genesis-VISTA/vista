import { relay } from "../../../../../_relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxy for one directory of a linked folder (`?dir=`, within it; the folder itself
 * by default), as the Files page's tree expands it.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ name: string; link: string }> },
) {
  const { name, link } = await params;
  const dir = new URL(request.url).searchParams.get("dir") ?? "";
  const query = dir ? `?${new URLSearchParams({ dir }).toString()}` : "";
  return relay(
    `/projects/${encodeURIComponent(name)}/linked-folders/${encodeURIComponent(link)}/entries${query}`,
  );
}
