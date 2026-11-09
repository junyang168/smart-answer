const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('musicBridge', { report: state => ipcRenderer.send('music:state', state) });
