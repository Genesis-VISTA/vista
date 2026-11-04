// @ts-check
// The update status window's only bridge: it receives what to show.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld(
  'vistaUpdate',
  Object.freeze({
    /** @param {(view: unknown) => void} callback */
    onStatus(callback) {
      if (typeof callback !== 'function') throw new TypeError('onStatus requires a callback');
      ipcRenderer.on('update:status', (_event, view) => callback(view));
      ipcRenderer.send('update:ready');
    },
  }),
);
