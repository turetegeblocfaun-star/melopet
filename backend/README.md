# MeloPet 后端（M1 核心链路）

这是依据专业 PRD v1.0.0、技术适配声明和阶段技术文档实现的后端。当前包含 API、SQLite 持久化、独立 Worker、规则/模型适配边界、画像与纠正版本、PetSpec、候选选择、授权撤回、异步物理删除、开发态 SVG 预览，以及对 `../desktop-pet` 的本地激活与动作控制。

## 当前边界

- 支持 UTF-8 CSV、粘贴文本和网易云歌单链接；链接歌曲的公开曲风、情绪标签与 BPM 采用 8 路有界并发读取，并按歌曲缓存 30 天。
- owner 从匿名 capability token 推导，调用方传入 ownerId 不产生权限。
- 默认 `MODEL_PROVIDER=disabled`、`MUSIC_SIGNAL_PROVIDER=disabled`。内置网易云公开元数据与本地音频数值特征仍可进入保守规则；没有允许信号的歌曲进入 `UNKNOWN`，不会根据歌名或艺人调用模型记忆猜测。
- 资产目录全部标记为 `dev-only`，预览只用于后端契约验证，不能作为视觉价值或生产版权验收证据。
- Spotify、Apple Music、OAuth、RAG、SFT、多 Agent、自由文生图、桌面激活、分享和商城均未实现。
- 当前重复歌曲采用可配置前置基线“规范化歌名+艺人相同则保留第一条，其余标记排除”。这是 M0 待冻结项，不是生产承诺。

## 启动

单独验证后端或启动本地前端前，推荐先启动后端验证环境：

```bash
./scripts/start-validation.sh
```

该命令会启动 API 和独立 Worker，并自动跑完一次真实 HTTP 全链路验证。操作手册见 [runbooks/validation-environment.md](runbooks/validation-environment.md)。

如果需要手动分别启动进程：

```bash
cd backend
uv sync --extra dev
uv run melopet-init-db
uv run uvicorn melopet_backend.api:app --host 127.0.0.1 --port 8010
```

另开终端启动独立 Worker：

```bash
cd backend
uv run melopet-worker
```

接口文档：`http://127.0.0.1:8010/docs`。

## 最小调用顺序

1. `POST /v1/capabilities/anonymous` 获取 capability token。
2. 带 `Authorization: Bearer <token>` 和 `Idempotency-Key` 调用 `POST /v1/playlist-imports`。
3. `PUT /v1/playlist-imports/{id}/mapping` 确认字段。
4. `POST /v1/playlist-imports/{id}/analyses` 创建持久任务。
5. Worker 完成后，从 `GET /v1/tasks/{taskId}` 取得 `profileRevision`。
6. 纠正、确认画像，再创建 Pet 候选任务。
7. 从 `GET /v1/pet-candidates/{taskId}` 取得候选后，调用 `POST /v1/pet-candidates/{taskId}/select` 持久化选择。

## 验证

```bash
uv run ruff check .
uv run pytest
```

一次运行全部质量门（测试、覆盖率、安全与依赖扫描）：

```bash
./scripts/verify-backend.sh
```

真实模型和真实音乐来源没有 Key/合同，不能宣称已验收。其状态通过 `GET /readyz` 明确显示为降级模式。
完整实现与外部验收边界见 [docs/BACKEND_COMPLETION_MATRIX.md](docs/BACKEND_COMPLETION_MATRIX.md)。
