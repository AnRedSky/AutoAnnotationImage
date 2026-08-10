# v3.1.0 Phase T 后续 backlog (P1 / P2 from docs/38)

## P1 — 短期, 3 个月内

### P1-1. 模型池化 (Model Pooling)

**问题**: `app/common/ml/ai_service.py:AIService.current_model` 是单例属性。
多模型切换时全加载 / 卸载, 显存与延迟抖动;
同一进程内多请求同时切不同 model_name 会互踩.

**当前状态**: 类 `ModelPool` 已经存在 (`docs/01-` 报告提到),
但实现只是占位 (dictionary).

**要做什么**:
- 在 `AIService` 旁提供真正的 `ModelPool` (LRU + 显存预算)
- 通过 TaskType 路由 (classification / detection / segmentation)
- 配置: `MODEL_POOL_MAX_SIZE_GB`, `MODEL_POOL_EVICT_POLICY=lru`

**RED 测试建议**:
- fixture: 注册 2 个模型 + simulate 显存预算突破
- 断言: 第三模型加载时最不常用被驱逐

### P1-2. Worker Prometheus metrics

**问题**: 当前 `/api/metrics` 是 API 进程的指标. Worker 进程
(worker-train / worker-annotate) 没有暴露指标端点.
排查 "队列堆积 / 任务平均时长" 只能去 logs/celery.log 找.

**做法**:
- 加 `celery-exporter` (`prometheus/client_python`):
  `celery -A app.tasks.workers.celery_app worker --events` 已经开启 events;
  引入 `celery-exporter` 容器, 抓 events 输出 prometheus 端点;
- compose 新增 `prometheus` + `grafana` 服务;
- 关键 alert: `celery_queue_latency_seconds > 600` 持续 5min → 通知.

**注意**: 不是 P0 是 P1, 因为目前只有单 worker 容器, 日志足够定位问题.
真要进 K8s (P2) 时必须先做 P1-2.

### P1-3. Cross-process trace_id

**问题**: API 收到请求 → 提交 Celery task → Worker 处理;
这条链路无 trace_id 串联. 出问题靠人工对齐 timestamp.

**做法**:
- API 收到请求时生成 `trace_id = uuid4().hex[:12]`,
  通过 Redis `set trace:{task_id}` 写一个映射;
- Worker 启动时取 `trace_id`, Celery task headers 增加 `trace_id`;
- 所有 loguru 输出带 `extra={"trace_id": trace_id}`.
- 工具: `app/core/logging_setup.py` 已有, 加 filter.

**依赖**: P1-2 的 metric, 同样的中间件能力.

### P1-4. Chunked training (long task time-limit 缓解)

**问题**: 当前 `task_time_limit=3600s` 适合单次训练 ≤1h.
客户提 5k+ 数据集训练 6h 怎么办?

**做法**: 不需拆分 worker 子进程, 而是把训练函数内部 epoch-by-epoch
check SIGTERM + checkpoint:
- 已在 P0-1 装 SIGTERM handler;
- 训练函数进入 epoch 循环前调 `set_current_task_id(task_id)`,
  每个 epoch 末尾调 `torch.save(checkpoint, ...)`;
- 中断后, 下次 "resume" 任务读最近的 checkpoint 续训.

## P2 — 中期, 6 个月+

### P2-1. K8s 化

**trigger**: 用户提出 "想上一朵云 / 跨国部署".

**前置**: P1-2 + P1-3.

**做法**:
- compose.yaml → helm chart (或 kustomize);
- worker-train / worker-annotate 改 HPA;
- API 副本数 ≥ 2 + sticky session (SSE 除外);
- 健康检查路径: 现有 /api/health 已经返回 503 (新代码 P0-2), 可直接接 K8s readiness.

### P2-2. 推理服务独立化 (model-server)

**trigger**: "API 进程太忙, 在线推理 P99 > 1s" 或 "想接 Triton".

**做法**:
- 新增 `model-server` 独立 Docker image (类似 Triton);
- `app/services/ai_service.py` 改 HTTP client 调 model-server;
- 起 2 副本 + GPU 节点调度.

### P2-3. 模型版本与训练松绑

**trigger**: "客户要求 '查询 6 个月前某次训练, 即使模型已删除'".

**做法**: 当前 `TrainingJob.model_version_id` 物理级联删除, 改成 nullable;
`ModelVersionService.delete()` 允许 "逻辑删除"(保留 N 副本);
查询端不变.

### P2-4. DLQ 与重试策略分级

**trigger**: "失败任务需要人工干预入口".

**做法**:
- 新增 `app/tasks/dlq.py`, 把 celery 5xx 后重试满 3 次的任务
  落 `failed_tasks` 表 (含 last_error, full_state);
- 给管理后台暴露 "重试 / 永久失败" 操作;
- 网络错 (ConnectionError) 自动 retry, 业务错 (ValueError) 直接 FAILURE.

## Notes

- P1/P2 不在本次 session 范围, 列入 backlog 等下次启动 Phase U 再讨论.
- 每次推 P0/P1 时, 重新跑一次 § 六 (docs/38) 的触发条件表.
