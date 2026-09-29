// @ts-check
//
// Where a URL the page asks for should go (design W3). Kept free of any
// Electron import so it can be tested with plain `node --test`.
//
// The page is VISTA's own UI, but the URLs are not all VISTA's: an agent can
// hand the researcher any address (`mcp_url_elicitation`), and the Globus
// authorize link, DOIs and skill repositories are other sites. The rule is by
// origin alone, so no UI code has to know it is running in a window:
//
//   - VISTA's own origin stays in the app: the main window navigates, and a
//     pop-up (a PDF, an image) opens as a child window.
//   - Any other http(s) address goes to the system browser. Opening it in-app
//     would put a Globus or HPC login inside a window with no address bar.
//   - Anything else is refused. `file:` is what a file dropped outside an
//     upload area becomes, `javascript:` and `data:` have no business being a
//     navigation target, and `mailto:` and friends have no link in the UI.

/** @typedef {'in-app' | 'external' | 'deny'} Route */

/**
 * @param {string} origin VISTA's own origin, e.g. `http://127.0.0.1:3000`.
 * @param {string} url The address being opened or navigated to.
 * @returns {Route}
 */
export function classify(origin, url) {
  let target;
  try {
    target = new URL(url);
  } catch {
    return 'deny';
  }
  // Checked before the origin: a `blob:` URL reports the origin that created
  // it, so without this a same-origin blob would count as one of VISTA's pages.
  if (target.protocol !== 'http:' && target.protocol !== 'https:') return 'deny';
  // Compared as origins, not prefixes, so `http://127.0.0.1:30001` cannot
  // pass for VISTA. `localhost` and `127.0.0.1` are different origins with
  // different storage, and the UI only ever uses relative links, so treating
  // one as the other buys nothing.
  return target.origin === originOf(origin) ? 'in-app' : 'external';
}

/**
 * The origin of the address the window was started with.
 *
 * @param {string} url
 * @returns {string}
 */
export function originOf(url) {
  return new URL(url).origin;
}
