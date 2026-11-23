import { NextResponse } from "next/server";
import { jsonBody, relay } from "../../../../_relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Proxies for renaming (`PATCH`, `{name}`) and unlinking (`DELETE`) one linked folder. */
export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ name: string; link: string }> },
) {
  const { name, link } = await params;
  const body = await jsonBody(request);
  if (body instanceof NextResponse) return body;
  return relay(
    `/projects/${encodeURIComponent(name)}/linked-folders/${encodeURIComponent(link)}`,
    { method: "PATCH", body },
  );
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ name: string; link: string }> },
) {
  const { name, link } = await params;
  return relay(
    `/projects/${encodeURIComponent(name)}/linked-folders/${encodeURIComponent(link)}`,
    { method: "DELETE" },
  );
}
