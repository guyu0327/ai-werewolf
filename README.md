# AI 狼人杀 🐺🌙

天黑请闭眼 —— 与 AI 们同桌博弈的狼人杀桌面游戏。

你将亲自上场，与多个各有人设、各用不同大模型的 AI 玩家同桌对局：发言、投票、夜晚行动全部由大模型驱动；也可以切换**上帝观战**，看一桌纯 AI 自相残杀，全程思考过程可见。

## 功能特性

- **我是玩家**：1 个真人 + 多个 AI 同桌，可自选角色开局；白天发言、投票放逐（可弃票），夜晚狼人刀人（真人狼一票定刀口，可自刀）、预言家验人、女巫用药
- **上帝观战**：全员 AI 对局，身份、狼队夜聊、AI 思考过程一览无余
- **AI 思考过程**：支持思考型模型（`reasoning_content`），实时流式展示每个 AI 的推理过程，复盘可折叠回看
- **投票可视化**：逐个亮票，「N号 正在投票 → 投给 X号」+ 投票理由全程可见
- **警长竞选**：上警 / 竞选发言 / 投票当选（可弃票）；警长 1.5 票 + 归票权，死后警徽可移交 / 撕毁；警上自爆吞警徽（单爆作废 / 双爆次日重选，建局可选）
- **自爆体系**：狼人白天发言可自爆直接入夜；真人狼一键自爆；AI 狼发言被查杀 / 指狼施压时自动权衡是否自爆
- **语音输入**：发言框内置麦克风，说话实时转写成文字留在框内，确认后手动发送（讯飞语音听写，凭证内置免配置，每日 500 次免费额度）
- **背景音乐**：昼夜自动切换曲目（Kevin MacLeod 作品，CC BY 4.0），音量可调，语音录音时自动压低，设置持久化
- **出局不翻牌**：对局中出局仅置灰不亮身份（白痴被放逐翻牌免死除外），结算后公开所有身份；狼人玩家可与队友互认（座位狼标）
- **严格信息隔离**：每个玩家只见「自己该看的」——真人拿不到视角外的信息，SSE 按身份过滤推送
- **完整复盘**：对局结束后回放全量事件流 + 身份总览，可一键生成 AI 复盘总结（可换模型重新生成）
- **断线重连 / 中途恢复**：SSE 断线自动重连并拉全量快照；进程重启后可从存档续玩
- **板子可配**：内置 6 人屠城局 / 9 人经典屠边局 / 12 人经典局（白痴）/ 12 人守卫局，支持自定义角色配比编辑器
- **多模型混用**：每个 AI 玩家可绑定不同的 OpenAI 兼容模型预设
- **失败不代打**：AI 决策 / 发言调用失败时对局自动暂停并弹窗展示具体报错，修复模型配置后可一键重试继续，或主动终止按故障收尾（绝不代打、不发「……」占位）

## 游戏规则要点

- **屠城局**（6 人板）：狼人存活数 ≥ 好人数即狼胜
- **屠边局**（9 / 12 人板）：神职全灭或村民全灭即狼胜
- 女巫不可自救、一夜只能用一瓶药；猎人被毒不能开枪；守卫只防刀不防毒、不能连守同一人，同守同救仍会死
- 白痴被放逐时翻牌免死，但失去投票权；平票 / 全员弃票无人出局，直接入夜
- 遗言：仅首夜死亡与被放逐者有遗言（自爆翻牌即死无遗言）；天数上限 20，超限和局

## 技术栈

| 端 | 技术 |
|---|---|
| 桌面壳 | Electron（spawn 后端 + 托盘 + 单实例） |
| 前端 | Vue 3 + TypeScript + Vite + Pinia + Sass |
| 后端 | Python 3.12 + FastAPI + uvicorn + openai SDK（OpenAI 兼容多模型） |
| 通信 | REST + SSE（流式发言 / 思考 / 逐票）+ WebSocket（语音听写中继） |
| 持久化 | 本地 JSON（`data/games/*.json`，原子写入，可断点恢复） |

## 快速开始

环境要求：Node.js 18+、Python 3.11+（Windows / macOS / Linux 均可）。

```bash
# 1. 安装前端依赖并构建
npm install
npm --prefix web install
npm run build:web

# 2. 安装后端依赖
pip install -r backend/requirements.txt

# 3. 启动（Electron 拉起后端 + 桌面窗口）
npm start
```

启动后进入设置页：

1. 新增**模型预设**：填 OpenAI 兼容的 `baseUrl / apiKey / 模型名`（DeepSeek、Qwen、GLM、Kimi 等均可）
2. 创建 **AI 玩家**：名字、头像 emoji、人设描述，并绑定模型预设
3. 回大厅选板子、勾选 AI 玩家，开始游戏

> 🔒 API Key 仅保存在本地 `data/settings.json`，不会出现在日志与网络请求中；该目录已在 `.gitignore` 中排除。

## 开发与测试

```bash
# 前端热更新开发（需另起后端）
npm run dev:web
npm run dev:server

# 后端单测（88 个用例：引擎规则 / 整局流程 / 信息隔离 / 警长竞选 / 自爆 / LLM 失败暂停 / 语音中继…）
npm run test:server

# 不接真实模型跑通整局：本地 mock LLM + 双模式端到端冒烟
python scripts/mock_llm.py 9100
python scripts/smoke_play.py          # 玩家局
python scripts/smoke_play.py --god    # 上帝观战局
```

## 打安装包

```bash
npm run dist         # Windows NSIS 安装包 → dist/ai-werewolf-setup-<版本>.exe
npm run dist:dir     # 免安装目录版 → dist/win-unpacked/
# macOS: npm run dist:mac
```

- 安装包为向导式（可选安装目录，按用户安装，无需管理员权限）
- 打包内容：Electron 壳 + 前端产物（`resources/web/dist`）+ 后端（`resources/server`）
- `npm run dist` / `dist:dir` 会先自动执行 `build:web` + `build:server`：后端经 PyInstaller 打成 `server.exe` 一并装入安装目录，目标机器**完全免 Python 环境**（运行时检测到 exe 优先使用；无 exe 时回退源码副本，此时需本机 Python 3.11+ 与后端依赖）
- 应用数据（对局存档 / 设置 / API Key）固定在用户目录 `%APPDATA%/ai-werewolf`，与开发目录隔离，卸载不删除

## 项目结构

```
ai-werewolf/
├── main.js / preload.js / boot.html   # Electron 壳：拉起后端、加载页面
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 入口 + 托管前端静态资源
│   │   ├── models.py                  # pydantic 数据模型
│   │   ├── runner.py                  # asyncio 游戏循环（真人输入 Future 等待）
│   │   ├── events.py                  # SSE hub（快照 + 增量，按视角过滤）
│   │   ├── llm/                       # openai 封装：流式发言 / 结构化决策降级链 / strategy.py 提示词策略库
│   │   ├── voice/                     # 讯飞语音听写中继（凭证加密内置 / WS 签名 / 用量计数）
│   │   ├── engine/                    # 纯规则引擎：板子 / 状态机 / 视图过滤 / 存档
│   │   └── routes/                    # games / settings / stream / voice
│   └── tests/                         # 引擎 / 整局 / 语音单测
├── web/                               # Vue3 前端（牌桌 / 大厅 / 设置 / 复盘）
├── scripts/                           # mock LLM + 端到端冒烟 + 讯飞凭证加密脚本
├── build/                             # 应用图标
└── data/                              # 运行时数据（不入库）
```
