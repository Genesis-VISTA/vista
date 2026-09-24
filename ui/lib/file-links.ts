/**
 * Link attributes for a "Download" link to a file an agent produced.
 *
 * VISTA's own files (`/api/files/...`) are downloaded rather than opened: in a
 * browser that saves the file instead of leaving a stray tab, and in the VISTA
 * window, which has no tabs, it raises a save dialog instead of a second
 * window. Anything else is someone else's URL, so it opens in a new tab as
 * before -- and the window sends that to the system browser.
 *
 * Decided from the string alone, not `window.location`, so the server render
 * and the client render agree.
 */
export function fileLinkProps(url: string): { download: string } | { target: "_blank"; rel: "noreferrer" } {
  const isAppPath = url.startsWith("/") && !url.startsWith("//");
  return isAppPath ? { download: "" } : { target: "_blank", rel: "noreferrer" };
}
