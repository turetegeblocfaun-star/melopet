# MeloPet 后端验证环境

这是后端专用验证环境，不是产品前端。它在本机 `127.0.0.1` 上启动 API 和独立 Worker，不向局域网公开。

## 一键启动与自检

```bash
cd '/Users/mima1234/Desktop/AI/MeloPet音灵桌宠/backend'
./scripts/start-validation.sh
```

脚本会自动完成依赖、数据库、API、Worker 和一次全链路验证。看到 `✅ MeloPet 后端全链路验证通过` 即表示本地核心链路正常。

启动后打开：

- 中文一键验收台：<http://127.0.0.1:8010/validation>
- Swagger 接口台：<http://127.0.0.1:8010/docs>
- 存活检查：<http://127.0.0.1:8010/healthz>
- 能力就绪状态：<http://127.0.0.1:8010/readyz>

## 人工验证 Swagger

Swagger 是留给开发人员的技术调试页。非技术验收直接使用上面的中文一键验收台即可。

1. 打开 Swagger，展开 `POST /v1/capabilities/anonymous`，点击 **Try it out** 和 **Execute**。
2. 复制响应中的 `capabilityToken`。
3. 点击页面右上角 **Authorize**，粘贴 token 并确认。Swagger 会自动带 Bearer 认证，不需手写 `Bearer ` 前缀。
4. 按接口顺序调用导入、映射、分析、画像、确认和桌宠候选接口。所有写接口都需要一个新的 `Idempotency-Key`。

用于手工导入的歌单在 `samples/demo-playlist.csv`。

## 复查自动验证结果

启动脚本会把最后一次自检会话写到：

```text
runtime/validation/demo-session.json
```

该文件包含本地 capability token、导入 ID、画像 revision、任务 ID 和候选预览路径，文件权限为 `600`。它是本地开发凭证，不要上传或分享。

只重新执行全链路自检：

```bash
uv run melopet-demo
```

执行完整后端质量门：

```bash
./scripts/verify-backend.sh
```

## 日志与停止

```text
runtime/validation/api.log
runtime/validation/worker.log
```

停止环境：

```bash
./scripts/stop-validation.sh
```

## 当前可验证边界

默认使用规则分析和 `dev-only` SVG 资产，可验证后端契约、持久任务、权限隔离、画像与 PetSpec 链路。由于尚未配置经批准的真实模型、音乐信号来源和生产美术资产，`/readyz` 显示 `degraded` 是预期结果，不是本地验证失败。
