// @ts-check
const { contextBridge, ipcRenderer } = require('electron');

const STATE_CHANNEL = 'startup:state';

contextBridge.exposeInMainWorld(
  'vistaStartup',
  Object.freeze({
    /** @param {(state: unknown) => void} callback */
    onState(callback) {
      if (typeof callback !== 'function') throw new TypeError('onState requires a callback');
      /** @param {Electron.IpcRendererEvent} _event @param {unknown} state */
      const listener = (_event, state) => callback(state);
      ipcRenderer.on(STATE_CHANNEL, listener);
      ipcRenderer.send('startup:ready');
      return () => ipcRenderer.removeListener(STATE_CHANNEL, listener);
    },
    retry: () => ipcRenderer.invoke('startup:retry'),
    openLogs: () => ipcRenderer.invoke('startup:open-logs'),
    copyDiagnostics: () => ipcRenderer.invoke('startup:copy-diagnostics'),
    quit: () => ipcRenderer.invoke('startup:quit'),
  }),
);
