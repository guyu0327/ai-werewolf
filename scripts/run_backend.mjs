#!/usr/bin/env node
/**
 * 后端 Python 入口（跨平台）：所有需要 Python 的 npm script 统一走这里。
 *
 * 为什么需要它：PATH 上不一定有 `python`（macOS 出厂通常只有 /usr/bin/python3，
 * 且在装了 Xcode 的机器上常是桩程序，没有 pip、没有项目依赖）。项目约定后端环境
 * 在 backend/.venv，所以优先直接调用该解释器，避免依赖 shell 里 PATH 的运气。
 *
 * 用法：
 *   node scripts/run_backend.mjs build   # PyInstaller 打包后端 → backend/dist/server
 *   node scripts/run_backend.mjs test    # 跑后端单测
 *   node scripts/run_backend.mjs dev     # 本地起后端（配合 npm run dev:web）
 */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BACKEND = path.join(ROOT, 'backend')
const IS_WIN = process.platform === 'win32'

const VENV_PYTHON = IS_WIN
  ? path.join(BACKEND, '.venv', 'Scripts', 'python.exe')
  : path.join(BACKEND, '.venv', 'bin', 'python')

const MODES = {
  build: {
    args: [
      '-m', 'PyInstaller', '-y', '--clean',
      '-n', 'server',
      '--distpath', 'dist',
      '--workpath', 'build/pyinstaller',
      '--collect-all', 'uvicorn',
      'run_server.py',
    ],
    // PyInstaller 不在 requirements.txt 里（只有打包时才需要），单独给出可复制的安装命令
    needs: 'PyInstaller',
    installHint: `  uv pip install --python backend/.venv/bin/python pyinstaller\n  （没有 uv 时：backend/.venv/bin/python -m pip install pyinstaller）`,
  },
  test: {
    args: ['-m', 'pytest', 'tests', '-q'],
    needs: 'pytest',
    installHint: '  uv pip install --python backend/.venv/bin/python -r backend/requirements.txt',
  },
  dev: {
    args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8321'],
    needs: 'uvicorn',
    installHint: '  uv pip install --python backend/.venv/bin/python -r backend/requirements.txt',
  },
}

const mode = process.argv[2]
const spec = MODES[mode]
if (!spec) {
  console.error(`未知模式：${mode ?? '(未指定)'}\n可用模式：${Object.keys(MODES).join(' / ')}`)
  process.exit(1)
}

/** 候选解释器：venv 优先，其余靠 PATH 兜底 */
function candidates() {
  const list = []
  if (existsSync(VENV_PYTHON)) list.push(VENV_PYTHON)
  list.push(IS_WIN ? 'python' : 'python3', 'python')
  return list
}

/** 该解释器能跑且装了指定模块吗 */
function usable(python, needs) {
  const probe = spawnSync(python, ['-c', `import ${needs}`], { stdio: 'ignore' })
  return probe.status === 0
}

const python = candidates().find((p) => usable(p, spec.needs))
if (!python) {
  console.error(
    `\n无法启动后端构建：找不到装有 ${spec.needs} 的 Python 解释器。\n\n` +
      `期望的虚拟环境解释器：${VENV_PYTHON}\n` +
      (existsSync(VENV_PYTHON) ? '（该文件存在，但缺少所需模块）\n' : '（该文件不存在，需先创建虚拟环境）\n') +
      `\n请在项目根目录执行：\n` +
      `  cd backend && uv venv --python 3.12 && uv pip install -r requirements.txt\n` +
      `  ${spec.installHint}\n`
  )
  process.exit(1)
}

console.log(`[backend:${mode}] ${python} ${spec.args.join(' ')}`)
const child = spawnSync(python, spec.args, { cwd: BACKEND, stdio: 'inherit', env: process.env })
process.exit(child.status ?? 1)
