const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('videoBridge', {
  onStop: callback => ipcRenderer.on('video:stop', (_event, id) => callback(id))
});
