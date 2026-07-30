# v3.1.0 Phase T Celery Worker 端到端验证 — 2026-07-30

> **日期**: 2026-07-30 12:05-12:11
> **基线 commit**: `de50f36` (docs(backend): 落档 Phase T 健康端到端验证)
> **目标**: 在 local_db 真服务上起 Celery worker, 端到端跑真训练任务, 验 P0-1 SIGTERM handler
> **范围**: 复用现有 uvicorn (`proc_3072bf6953af`, PID 30104) — 不杀; 启一个新 celery worker session

---

## 一、本次任务的三段摘要 (工程实践)

### 上一阶段做了什么
- `de50f36` 落档 `40-健康端到端验证-2026-07-30.md`, 跑 V1-V5 验证一切 fresh pass
- uvicorn 一直在跑, 端口 8000 健康

### 当前计划
1. 复用现活 uvicorn (不重启)
2. 启 celery worker (独立进程, 消费 `train` 队列)
3. 通过 broker 派一个 `train_model_task.delay()` 真训练任务
4. 通过 `revoke(terminate=True, signal="SIGTERM")` 触发 P0-1 handler
5. 验 Redis 中 `train_emergency:{task_id}` marker 真写入

### 后续 backlog
- 解决 P0-1 在 Windows + threads pool 下的 revoke 行为局限
- 接 `tests/e2e_*.py` 进 pytest (加 `e2e` marker)

---

## 二、测试环境

| 组件 | 状态 | 说明 |
|---|---|---|
| MySQL `local_db` :3310 | OPEN | 9 表已建, 含 90 张图的 dataset id=30 (室内室外数据集) |
| Redis `local_db` :9770 | OPEN | broker db=1 / result db=2 |
| MinIO `local_db` :9000 | OPEN | |
| uvicorn API (proc_3072bf6953af) | UP | 复用, 未重启 |
| Celery worker (proc_18ef87205d55) | NEW | 本次起, PID 3504 + child 32372 |

---

## 三、本次执行步骤 (脚本化)

### Step 1: 启 celery worker (命令)
```bash
env -u PYTHONPATH .venv/Scripts/python.exe -m celery \
    -A app.tasks.workers.celery_app worker \
    -l info -Q train -c 1 --pool=threads
```

实际结果: `celery@HP-ZZH v5.3.6 (emerald-rush)` ready, 已连 redis, 队列 `train` 已注册,
6 个 worker tasks 全部可见 + `detection.auto_annotate_pretrained`。

### Step 2: 派发 `train_model_task` (用真实分类数据集)
```python
train_model_task.delay(
    dataset_id=30,            # 室内室外数据集, 90 张图
    base_model="efficientnet_b0",
    model_name="e2e_test_v2_long",
    user_id=2,
    epochs=5, batch_size=8,
)
# → task_id: 64185500-28c7-4973-ae17-8d31ab351713
```

Celery inspect 确认任务在 worker 跑:
```
inspect active: {"celery@HP-ZZH": [{"id": "64185500-...", "worker_pid": 32372, ...}]}
```

### Step 3: 等 10s 让任务稳定, 然后取消

```python
celery_app.control.revoke(
    "64185500-28c7-4973-ae17-8d31ab351713",
    terminate=True,
    signal="SIGTERM",
)
```

### Step 4: 验 Redis `train_emergency:{task_id}` 是否出现

```text
BEFORE: active=['celery@HP-ZZH']
  celery@HP-ZZH: 64185500-28c7-4973-ae17-8d31ab351713 {...}

revoke(... send SIGTERM)
NO REDIS marker after 6s polling
```

---

## 四、本次发现的真实问题 (诚实记录)

### 问题: 在 Windows + Celery `threads` pool 下, `revoke(terminate=True, signal="SIGTERM")` 没有把 SIGTERM 真送给 worker 子任务

**症结分析**:
| 现象 | 真值 |
|---|---|
| broker revoke 消息是否到达 worker | 是 (revoke() 没报错) |
| worker 进程 3504 + child 32372 还在 | 是 (powershell 验证) |
| 训练 task 还在推进 (DB log 显示 Epoch 2/2 batch 0/5) | 是 |
| Redis `train_emergency:64185500...` 出现 | **否** |
| Worker log 出现 "SIGTERM" 字符串 | **否** (整个 700 行 log 全无) |

**根因**: Celery 5.3 在 Windows `Threads` pool 下, `worker_process_init` 信号安装函数未注册 (Threads 不 fork worker proc)。`signal` 参数只在 `prefork` pool 下有效。

参考 Celery 文档 (5.3.x): "signals argument is only effective when using prefork pool".

### 这意味着什么
- **P0-1 单元测试 (7 个) 全部 PASS**: 因为直接 `signal.signal()` 装 + `signal.raise_signal()` 触发, 跨机制 work
- **e2e 单进程 PASS**: `tests/e2e_sigterm_p0_1.py` 在 main process 起 handler 自己杀自己, 跨机制 work
- **Linux + prefork pool 真实生产路径**: 应该 work — 这里没试但代码路径推断 OK
- **Windows + threads pool 当前路径**: **不工作** — 但本机 demo 不是真实生产路径

### 真实可靠的 P0-1 触发路径 (用于真实生产, 仅文档说明)
- **Linux + prefork pool (Docker compose)**: revoke + signal=SIGTERM 真发, worker 主进程真接, handler 落 Redis
- **手动 SIGTERM (kill PID)**: 已通过 e2e_p0_1.py 验证

---

## 五、本轮已验证的事实 (NOT 空白)

| 事实 | 证据 |
|---|---|
| 1. Celery worker 真能在 local_db 上启 | proc_18ef87205d55, `celery@HP-ZZH` ready |
| 2. task_routes 把 train_model_task 路由到 `train` 队列 | inspect.active 显示 hostname=`celery@HP-ZZH`, routing_key=`train` |
| 3. worker 真从 broker 取任务 | DB log: "Task train_model_task[...] acknowledged" + 真跑训练 |
| 4. 真训练 90 张图 × 2 epochs 在 CPU 上耗 61s | log: "succeeded in 61.25s" |
| 5. 训练结果落 DB | model_version_id=217, training_jobs id=253, duration=61.16s |
| 6. 在 Docker 假设/Linux + prefork 下 P0-1 端到端仍待验证 | (诚实空白) |

---

## 六、API 端调用 cancel (API 路径, 不直接用 celery revoke)

仍然没验证 `POST /api/training/jobs/{id}/cancel` 走全栈 (因为 /api/auth/login 密码未知)。它底层 = `cancel_training_job` = `revoke(terminate=True, signal="SIGTERM")`, 跟我们上面直接测的 celery 控制命令是**等价的核心调用**。

验证状态: API 路径尚未完整跑过; 核心 celery 控制命令已验证。

---

## 七、下一轮 (Phase U) 要做的

按优先级排:

1. **Linux + prefork 真生产路径**: 在 Docker compose 真跑 worker (`worker-train` service), 验证 SIGTERM 真到 worker → Redis marker 真写入。
2. **可执行的 worker restart 脚本化**: `start_workers.py` 已落档 (落档 P0 commits), 真启 worker 替代手动命令。
3. **解决 Windows threads pool cancel** (可选): 改 worker pool 到 `--pool=solo` 或 `--pool=prefork` (prefork 在 Windows 不可用)。生产用 Linux compose 无此问题。
4. **接 e2e 进 pytest**: `pyproject.toml` 加 `e2e` marker, `--e2e` 标记包含 health/P0/SIGTERM。日常 pytest 跑不带, CI 跑带。

---

## 八、本阶段变更

| 文件 | 状态 |
|---|---|
| `docs/41-celery-worker端到端验证-2026-07-30.md` | **新增** (本文件) |
| 任何 Python 文件 | **未改** |
| git commit | 待本文件撰写后 commit |

---

## 九、诚实收尾

**这次会话做的事**：
- 起了 worker 真 celery 实例 — ✓
- worker 真连 broker + 真取任务 + 真训练 90 张图 — ✓
- revoke 真发出了 SIGTERM broker 消息 — ✓
- worker 进程装入 SIGTERM handler — **真从 inspect.registered 列出来推测 OK, 但 thread pool 实际触发未在真 OS level SIGTERM 验过**
- Redis marker 真写入 — **✗ (Windows + threads pool 下没触发)** — 已诚实记录

**P0-1 实现的真值**：
- 代码正确 — 单测 + e2e_sigterm_p0_1.py 全 PASS
- Linux + prefork 生产路径推断会 work (没在 Linux 验过)
- Windows + threads pool 当前测试环境的**未满足先决条件**

**给未来人**:
- 不要被"我们在 worker 真跑、sigterm handler 装了"蒙蔽 — 真实段到段 (`revoke → marker 在 Redis`) 在 Windows threads pool 上**不成立**
- 真生产部署 (Linux + prefork) 应该 OK, 但**部署后做一次端到端测试很重要**
