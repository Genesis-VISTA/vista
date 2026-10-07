import { proxyReportPost } from "./_proxy";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /projects/{name}/reports`: saves an edited
 * report to the user's project uploads under `reports/` and returns `{ path }`.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  return proxyReportPost(request, name, "");
}
