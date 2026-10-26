# 音乐桌宠 · MeloPet

从歌单导入与歌曲氛围画像出发，生成、选择音灵，并把它放到桌面。桌宠支持待机、聆听、跳舞、睡觉、拖拽、右键菜单和低动态模式。

本仓库提供可阅读的后端、网页前端、桌面程序和启动器源码。公开版本从本地工程导出，运行数据、已填写的密钥、依赖目录和缓存不包含在仓库中。

## 直接看源码

- [后端 API、画像、任务和模型接入](backend/src/melopet_backend/)
- [网页主界面](frontend/components/melopet-app.tsx)
- [本地音频特征分析](frontend/lib/audio-analysis.ts)
- [动态桌面宠物](desktop-pet/pet.py)
- [完整演示启动器](scripts/start-all.py)
- [后端测试](backend/tests/)

## 目录

```text
backend/       FastAPI、SQLite、Worker、画像和音灵业务
frontend/      React、TypeScript、vinext / Vite 网页界面
desktop-pet/   PySide6 桌面程序、动作素材与本地控制
scripts/       跨平台启动、停止和打包脚本
pet.py         桌宠动作控制入口
```

## 本地体验

准备 Python 3.11 和可用的网络连接。启动器会优先使用本机 uv 与 Node.js；缺少依赖时，在项目内准备环境。公开源码不附带预打包的 Python、uv、Node.js 或依赖安装目录。

```bash
git clone https://github.com/turetegeblocfaun-star/melopet.git
cd melopet
python3 scripts/start-all.py
```

Windows 可使用 `python scripts/start-all.py`。也可在已有 Python 环境的电脑上使用根目录的 macOS / Windows 启动脚本。

- 页面：<http://127.0.0.1:4173/>
- API 文档：<http://127.0.0.1:8010/docs>
- 停止：`python3 scripts/stop-all.py`

可使用 [`backend/samples/demo-playlist.csv`](backend/samples/demo-playlist.csv) 验证导入流程。默认演示使用保守规则与开发预览；真实模型和图片服务需要自行配置。

## 分开开发

后端：

```bash
cd backend
uv sync --extra dev
uv run melopet-init-db
uv run uvicorn melopet_backend.api:app --host 127.0.0.1 --port 8010
```

另开终端在 `backend/` 运行 `uv run melopet-worker`。前端：

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

独立桌宠的交互与启动方式见 [desktop-pet/README.md](desktop-pet/README.md)。

## 验证

```bash
cd backend
uv run ruff check .
uv run pytest
```

前端检查：

```bash
cd frontend
npm ci
npm run check
```

## 数据与配置

配置示例位于 [`backend/.env.example`](backend/.env.example) 和 [`frontend/.env.example`](frontend/.env.example)。数据库、上传内容、匿名访问令牌、桌宠个人配置和日志由运行时在本机生成；请勿将这些本地文件提交到仓库。

歌曲氛围描述的是歌曲特征，不用于诊断用户的心理、性格或健康状态。内置开发预览素材不代表对生产版权或模型效果作出保证。
