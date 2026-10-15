const { contextBridge, ipcRenderer } = require('electron')

// main 通过 webPreferences.additionalArguments 传入桌面模式下的本地后端地址与访问令牌；
// Vite 开发模式没有这些参数，前端走同源代理且后端不校验令牌
const readArg = (name) => {
  const prefix = `--werewolf-${name}=`
  const arg = process.argv.find((a) => a.startsWith(prefix))
  return arg ? arg.slice(prefix.length) : null
}

contextBridge.exposeInMainWorld('werewolf', {
  apiBase: readArg('api'),
  token: readArg('token'),
  platform: process.platform,
  // 关闭确认：主进程拦截关闭后通知前端弹应用内样式的确认框，前端回报用户选择；
  // 界面挂载后先上报就绪，此后点关闭不再走原生对话框
  closeUiReady: () => ipcRenderer.send('app:close-ui-ready'),
  onCloseRequest: (cb) => ipcRenderer.on('app:close-request', () => cb()),
  closeChoice: (choice, remember) => ipcRenderer.send('app:close-choice', choice, remember),
})
