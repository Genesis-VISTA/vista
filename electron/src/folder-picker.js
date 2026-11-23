// @ts-check
//
// The system folder picker, for linking a folder to a project (openspec change
// `linked-folders`). The page asks for a folder and gets back the path the
// researcher chose, or null; it passes nothing in. Only the main window's top
// frame is answered, as for the update bridge, and the picker is modal to it.

/**
 * @typedef {{
 *   showOpenDialog(
 *     window: unknown,
 *     options: { properties: string[], title?: string, buttonLabel?: string },
 *   ): Promise<{ canceled: boolean, filePaths: string[] }>,
 * }} Dialog
 */

/**
 * The folder the researcher picked, or null when they cancelled or the request
 * did not come from the main window's top frame.
 *
 * @param {{ dialog: Dialog, window: unknown, fromMainWindow: boolean, fromMainFrame: boolean }} request
 * @returns {Promise<string | null>}
 */
export async function pickFolder({ dialog, window, fromMainWindow, fromMainFrame }) {
  if (!fromMainWindow || !fromMainFrame || !window) return null;
  const result = await dialog.showOpenDialog(window, {
    properties: ['openDirectory'],
    title: 'Link a folder',
    buttonLabel: 'Link',
  });
  if (result.canceled) return null;
  const [folder] = result.filePaths;
  return typeof folder === 'string' && folder ? folder : null;
}
