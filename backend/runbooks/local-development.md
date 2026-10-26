# 本地运行手册

## 初始化

```bash
cd backend
uv sync --extra dev
cp .env.example .env
uv run melopet-init-db
```

开发环境可以保持模型和音乐来源为 `disabled`。如果创建 `.env`，必须先将 `CAPABILITY_TOKEN_PEPPER` 换成本机随机长值；不要把 `.env` 提交或发送给前端。

## 启动两个独立进程

终端一：

```bash
uv run uvicorn melopet_backend.api:app --host 127.0.0.1 --port 8010
```

终端二：

```bash
uv run melopet-worker
```

访问 `/healthz` 检查进程，访问 `/readyz` 查看模型、来源、资产和生产就绪状态。开发态接口文档位于 `/docs`。

## 验证与停止

```bash
uv run ruff check .
uv run pytest
```

使用 `Ctrl+C` 分别停止 API 和 Worker。任务、画像和候选已写入 SQLite；未过期的任务可在下次启动后由 Worker 继续领取。
