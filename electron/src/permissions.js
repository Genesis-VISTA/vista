// @ts-check
//
// Which browser permissions the page gets. Kept free of any Electron import so
// it can be tested with plain `node --test`, as routing.js is.
//
// None, with one exception: VISTA's own pages may write to the clipboard, so
// a Copy button works (the Globus login address, a report body). Chromium puts
// `navigator.clipboard.writeText` behind the `clipboard-sanitized-write`
// permission, which a deny-everything handler refuses. Reading the clipboard
// stays refused, and so does everything else: camera, microphone,
// notifications, location. Another site's page, if one were ever loaded,
// gets nothing either: the check is by origin, as for navigation.

import { classify } from './routing.js';

/** The permissions VISTA's own origin is granted. */
const GRANTED = new Set(['clipboard-sanitized-write']);

/**
 * @param {string} origin VISTA's own origin, e.g. `http://127.0.0.1:3000`.
 * @param {string} permission Electron's permission name.
 * @param {string | undefined} requestingUrl The page or origin asking.
 * @returns {boolean}
 */
export function allowPermission(origin, permission, requestingUrl) {
  if (!GRANTED.has(permission) || !requestingUrl) return false;
  return classify(origin, requestingUrl) === 'in-app';
}
