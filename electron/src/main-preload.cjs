// @ts-check
//
// The main window's one bridge (openspec change `in-app-updates`, design D4):
// Settings > About asks the window to restart into an update the backend has
// already downloaded and verified. The page passes a version and nothing else;
// main.js decides whether to honour it (update-bridge.js). Only the main
// window's top frame gets this: child windows and iframes have no preload.
const { contextBridge, ipcRenderer } = require('electron');

const EVENT_CHANNEL = 'app:update-event';

contextBridge.exposeInMainWorld(
  'vistaApp',
  Object.freeze({
    isVistaApp: true,
    /** @param {string} version */
    restartToUpdate: (version) => ipcRenderer.invoke('app:restart-to-update', String(version)),
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
