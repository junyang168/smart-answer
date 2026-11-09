const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('presenter', {
  act: (action, args) => ipcRenderer.invoke('presenter:action', action, args),
  onMusic: callback => ipcRenderer.on('presenter:music-state', (_event, state) => callback(state)),
  onState: callback => ipcRenderer.on('presenter:state', (_event, state) => callback(state))
});
