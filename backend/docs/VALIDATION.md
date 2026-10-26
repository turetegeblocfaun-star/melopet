# 后端验证记录

验证日期：2026-09-11  
实现阶段：M1 后端核心链路（不含前端/桌面端）  
Python：3.11.16

## 自动化结果

```text
ruff check: passed
pytest: 57 passed
statement coverage: 85%
compileall: passed
uv lock --check: passed
bandit: 0 issues
pip-audit: 0 known vulnerabilities
```

覆盖的关键合同：

- CSV/粘贴导入、字段映射、有效/无效/重复统计。
- 无允许信号时不得依据歌名或艺人猜测，必须进入 UNKNOWN。
- 每首歌一个主标签或 UNKNOWN；整数占比总和严格为 100%。
- capability token 对象级隔离、跨用户拒绝和写接口幂等冲突。
- 画像纠正创建新 revision，旧 revision 不被覆盖，陈旧修改返回冲突。
- SQLite 持久任务、独立 Worker、租约防双领、租约过期接管、取消。
- 三份 PetSpec 稳定重放、开发态资产 allowlist/hash 校验、模板文案降级和 SVG 预览。
- PetSpec `copyRef`、候选选择持久化、幂等重放和选择版本冲突。
- 模型调用 `tools=[]`、JSON Schema 校验、最多一次格式修正和调用账本字段。
- 危险 CSV 单元格只作为不可信文本；审计详情不记录歌曲正文。
- ConsentDecision 查询/撤回、删除立即失效、队列任务中止和全链路物理清理。
- SignalPackage、Workflow 版本、task heartbeat、Worker 存活与损坏 journal 恢复。

## 真实进程 HTTP 冒烟

API 与 Worker 以两个独立进程运行，实际 HTTP 链路结果：

```json
{
  "importStatus": "MAPPING_REQUIRED",
  "normalizedCount": 12,
  "analysisStatus": "PROFILE_READY",
  "profileKnown": 12,
  "confirmed": true,
  "petStatus": "CANDIDATES_READY",
  "candidateCount": 3,
  "selected": true,
  "previewBytes": 992
}
```

停止并重新启动 API 后，使用原 capability token 成功读取重启前创建的导入对象，证明当前持久化链路不依赖进程内状态。

## 明确未通过或不可宣称通过的层

- 没有经批准的模型 Key、具体模型、处理地域和预算，因此真实模型 E2E 为 `待验收`。
- 没有经批准的音乐信号供应商；MusicBrainz/AcousticBrainz、Apple Music 均未接入，Spotify 保持阻断。
- 当前目录是 `dev-only` 占位资产，不能作为视觉满意度、版权或生产资产验收证据。
- 没有目标 OS 决策，本轮没有前端、桌面窗口、签名安装、激活、更新或卸载能力。
- M0 业务、来源、版权、成本和 Owner 阻断项尚未关闭，因此不能标记为生产就绪。
