const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('videoBridge', {
  onStop: callback => ipcRenderer.on('video:stop', (_event, id) => callback(id))
});

contextBridge.exposeInMainWorld('recordingBridge', {
  chunk: (id, sequence, raw) => ipcRenderer.invoke('recording:chunk', id, sequence, raw),
  fault: (id, message, graceful = false) => ipcRenderer.send('recording:fault', id, message, graceful),
  level: (id, rms) => ipcRenderer.send('recording:level', id, rms)
});
