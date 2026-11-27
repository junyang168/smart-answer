const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('presenter', {
  act: (action, args) => ipcRenderer.invoke('presenter:action', action, args),
  onState: callback => ipcRenderer.on('presenter:state', (_event, state) => callback(state))
});
