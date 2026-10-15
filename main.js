const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, shell, nativeImage, nativeTheme } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const net = require('net')
const crypto = require('crypto')

// 开发模式：npm run dev，加载 Vite 开发服务器（需先 npm run dev:web，并自行启动后端 8321）
// 桌面模式：npm start，自动拉起 backend 下的 uvicorn 并加载 web/dist 前端构建产物
const isDev = process.argv.includes('--dev')

const TITLEBAR_THEMES = {
  dark: { color: '#1c1733', symbolColor: '#a49dbb' },
}

if (process.platform === 'win32') {
  nativeTheme.themeSource = 'dark'
}

// 打包态资源在 <安装目录>/resources（extraResources），开发态在项目 resources/ 下
const RESOURCE_ROOT = app.isPackaged ? process.resourcesPath : path.join(__dirname, 'resources')
if (app.isPackaged) app.setName('ai-werewolf')

/** @type {import('child_process').ChildProcess | null} */
let backend = null
let backendPort = null
let backendToken = null
let quitting = false
let closeUiReady = false
/** @type {import('electron').Tray | null} */
let tray = null

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port
      srv.close(() => resolve(port))
    })
    srv.on('error', reject)
  })
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer()
    srv.listen(port, '127.0.0.1', () => srv.close(() => resolve(true)))
    srv.on('error', () => resolve(false))
  })
}

// 固定后端端口：首启挑空闲端口持久化，之后优先复用——页面 origin 稳定，localStorage（BGM 开关等）才能跨启动留存
function portFile() {
  return path.join(app.getPath('userData'), 'backend-port.json')
}

async function resolveBackendPort() {
  let saved = null
  try {
    const v = JSON.parse(fs.readFileSync(portFile(), 'utf8'))
    if (Number.isInteger(v) && v >= 1024 && v <= 65535) saved = v
  } catch { /* 首次运行或文件损坏 */ }
  if (saved && (await isPortFree(saved))) return saved
  const port = await getFreePort()
  try {
    fs.writeFileSync(portFile(), JSON.stringify(port))
  } catch { /* 写不进去就每次重挑 */ }
  return port
}

/** 本地访问令牌：首次启动生成并持久化，防止本机其他进程/网页访问后端 */
function loadOrCreateToken() {
  const file = path.join(app.getPath('userData'), 'token')
  if (fs.existsSync(file)) {
    const token = fs.readFileSync(file, 'utf8').trim()
    if (token) return token
  }
  const token = crypto.randomBytes(32).toString('hex')
  fs.writeFileSync(file, token, { encoding: 'utf8' })
  return token
}

// ---------- 关闭行为偏好：ask（每次询问）/ minimize（隐藏到托盘）/ exit（直接退出） ----------

let closePref = 'ask'

function prefFile() {
  return path.join(app.getPath('userData'), 'close-pref.json')
}

function loadClosePref() {
  try {
    const v = JSON.parse(fs.readFileSync(prefFile(), 'utf8'))
    if (v === 'minimize' || v === 'exit' || v === 'ask') return v
  } catch { /* 首次运行或文件损坏，回到默认询问 */ }
  return 'ask'
}

function saveClosePref(v) {
  closePref = v
  try {
    fs.writeFileSync(prefFile(), JSON.stringify(v))
  } catch { /* 写不进去就本次会话内生效 */ }
}

/** 后端解释器：开发态直接用系统 Python + 源码；打包态优先 resources/server/server.exe（pyinstaller 产物），否则 resources/server 下的源码副本 */
function backendCommand() {
  if (app.isPackaged) {
    const exe = path.join(RESOURCE_ROOT, 'server', 'server.exe')
    if (fs.existsSync(exe)) return { cmd: exe, args: [] }
  }
  // 打包态 __dirname 在 app.asar 内，Python 无法读取，源码副本与前端产物都在 resources/server 下
  const entry = app.isPackaged ? path.join(RESOURCE_ROOT, 'server') : path.join(__dirname, 'backend')
  return {
    cmd: process.platform === 'win32' ? 'python' : 'python3',
    args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort), '--log-level', 'warning'],
    cwd: entry,
  }
}

async function startBackend() {
  const { cmd, args, cwd } = backendCommand()
  const env = {
    ...process.env,
    AT_WEREWOLF_PORT: String(backendPort),
    AT_WEREWOLF_TOKEN: backendToken,
    // 数据目录固定在 userData，避免打包/开发两种形态数据漂移
    AT_WEREWOLF_DATA: path.join(app.getPath('userData'), 'data'),
    PYTHONUNBUFFERED: '1',
  }
  // windowsHide：打包态 server.exe 是控制台子系统程序，不设会在前台闪黑窗
  backend = spawn(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
  backend.stdout.on('data', (d) => process.stdout.write(`[server] ${d}`))
  backend.stderr.on('data', (d) => process.stderr.write(`[server] ${d}`))
  backend.on('exit', (code) => {
    if (!quitting) {
      dialog.showErrorBox('服务异常', `后端服务意外退出（code=${code}），应用即将关闭。`)
      app.quit()
    }
  })

  const url = `http://127.0.0.1:${backendPort}/api/health`
  const deadline = Date.now() + 60_000
  for (;;) {
    if (backend.exitCode !== null) throw new Error('后端进程在启动过程中退出')
    if (Date.now() > deadline) throw new Error('后端服务启动超时')
    try {
      const res = await fetch(url, { headers: { 'X-AT-Token': backendToken } })
      if (res.ok) return
    } catch { /* 尚未就绪，继续等待 */ }
    await new Promise((r) => setTimeout(r, 500))
  }
}

function showMain(win) {
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

function createTray(win) {
  const icon = process.platform === 'win32' ? 'icon.ico' : 'icon.png'
  const iconPath = app.isPackaged ? path.join(RESOURCE_ROOT, icon) : path.join(__dirname, 'build', icon)
  if (!fs.existsSync(iconPath)) return
  const trayImage = nativeImage.createFromPath(iconPath)
  if (process.platform !== 'win32') trayImage.resize({ height: 22 })
  tray = new Tray(trayImage)
  tray.setToolTip('AI 狼人杀')
  // 每次右键重建菜单，radio 项才能反映当前关闭偏好
  const buildMenu = () =>
    Menu.buildFromTemplate([
      { label: '显示主窗口', click: () => showMain(win) },
      { type: 'separator' },
      { label: '关闭窗口时询问', type: 'radio', checked: closePref === 'ask', click: () => saveClosePref('ask') },
      { label: '关闭窗口时最小化到托盘', type: 'radio', checked: closePref === 'minimize', click: () => saveClosePref('minimize') },
      { label: '关闭窗口时直接退出', type: 'radio', checked: closePref === 'exit', click: () => saveClosePref('exit') },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitting = true
          app.quit()
        },
      },
    ])
  tray.on('click', () => showMain(win))
  tray.on('right-click', () => tray.popUpContextMenu(buildMenu()))
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    autoHideMenuBar: true,
    title: 'AI 狼人杀',
    ...(process.platform === 'win32'
      ? { titleBarStyle: 'hidden', titleBarOverlay: { color: '#1c1733', symbolColor: '#a49dbb', height: 32 } }
      : {}),
    show: false,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      additionalArguments: backendPort
        ? [`--werewolf-api=http://127.0.0.1:${backendPort}`, `--werewolf-token=${backendToken}`]
        : [],
    },
  })

  if (isDev) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL || 'http://localhost:5175')
    win.show()
  } else {
    win.loadFile(path.join(__dirname, 'boot.html'))
    win.show()
  }

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })

  win.on('close', (e) => {
    if (quitting) return
    if (closePref === 'minimize') {
      e.preventDefault()
      win.hide()
      return
    }
    if (closePref === 'exit') {
      quitting = true
      app.quit()
      return
    }
    e.preventDefault()
    if (closeUiReady && !win.webContents.isCrashed()) {
      win.webContents.send('app:close-request')
    } else {
      quitting = true
      app.quit()
    }
  })
  createTray(win)
  return win
}

// ---------- 关闭确认（应用内样式弹窗，由渲染进程绘制与响应） ----------

ipcMain.on('app:close-ui-ready', () => {
  closeUiReady = true
})

ipcMain.on('app:close-choice', (_e, choice, remember) => {
  const [win] = BrowserWindow.getAllWindows()
  if (choice === 'minimize') {
    if (remember) saveClosePref('minimize')
    if (win) win.hide()
  } else {
    if (remember) saveClosePref('exit')
    quitting = true
    app.quit()
  }
})

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const [win] = BrowserWindow.getAllWindows()
    if (win) showMain(win)
  })

  app.whenReady().then(async () => {
    closePref = loadClosePref()
    // 麦克风（语音输入）放行；其余权限维持默认拒绝
    const { session } = require('electron')
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
      callback(permission === 'media')
    })
    if (isDev) {
      createWindow()
    } else {
      try {
        backendPort = await resolveBackendPort()
        backendToken = loadOrCreateToken()
        const win = createWindow()
        await startBackend()
        // 前端产物：后端同时托管 web/dist，这里直接加载后端地址（同源免 CORS）
        win.loadURL(`http://127.0.0.1:${backendPort}`)
      } catch (err) {
        dialog.showErrorBox('启动失败', `后端服务未能启动：\n${err.message}`)
        app.quit()
        return
      }
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('before-quit', () => {
    quitting = true
    if (backend && backend.exitCode === null) backend.kill()
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
}
