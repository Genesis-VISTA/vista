// @ts-check
//
// The main window's bridge. Settings > About asks the window to restart into an
// update the backend has already downloaded and verified (openspec change
// `in-app-updates`, design D4): the page passes a version and nothing else;
// main.js decides whether to honour it (update-bridge.js). A project's settings
// ask for the system folder picker to link a folder (`linked-folders`,
// folder-picker.js). Only the main window's top frame gets this: child windows
// and iframes have no preload.
const { contextBridge, ipcRenderer } = require('electron');

const EVENT_CHANNEL = 'app:update-event';

contextBridge.exposeInMainWorld(
  'vistaApp',
  Object.freeze({
    isVistaApp: true,
    /** @param {string} version */
    restartToUpdate: (version) => ipcRenderer.invoke('app:restart-to-update', String(version)),
    /**
     * The system folder picker, for linking a folder to a project
     * (openspec change `linked-folders`): the path picked, or null.
     * @returns {Promise<string | null>}
     */
    pickFolder: () => ipcRenderer.invoke('app:pick-folder'),
    /** @param {(event: unknown) => void} callback */
    onUpdateEvent(callback) {
      if (typeof callback !== 'function') throw new TypeError('onUpdateEvent requires a callback');
      /** @param {Electron.IpcRendererEvent} _event @param {unknown} payload */
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on(EVENT_CHANNEL, listener);
      return () => ipcRenderer.removeListener(EVENT_CHANNEL, listener);
    },
  }),
);
