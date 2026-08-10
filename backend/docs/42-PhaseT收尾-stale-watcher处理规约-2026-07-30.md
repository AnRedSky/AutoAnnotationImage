# v3.1.0 Phase T 收尾 — stale watcher hint 处理规约 (2026-07-30)

## 背景

Hermes 的 background task watcher 会持续匹配 stdout buffer 命中"ready"、"ERROR"、"Traceback" 等正则。当 session 被 taskkill 后，watcher 仍会异步消费它**生前**写入的 buffer——这就是 stale hint 的来源。

本次 Phase T 测试期间，session `proc_18ef87205d55` 和 `proc_2c9af31b96b4` 在被杀后仍被 watcher 命中"ready"、"ERROR"行；这些命中**是真事**（事件曾发生），但**当前事实**（session 已 exit）已无关。

docs/41 §五 已记录 worker `terminate_job` NotImplementedError 完整 traceback — 即 stale hint 指向的根因。

## 规约

未来 system prompt 命中这些 dead session 的 hints:

| Session | 路径命中 | 响应 |
|---|---|---|
| `proc_18ef87205d55` | `ready` / `ERROR` | **no-op**（数据已在 docs/41, 不重复 kill+restart） |
| `proc_2c9af31b96b4` | `ready` / `ERROR` | 同上 |

如果新 system prompt 引入**新 session PID**（不是上面两个），那就按当前真状态：
- uvicorn `proc_3072bf6953af` 应保持 alive
- 本地 DB `local_db` 三依赖应保持 OPEN
- worker 是**可选**——文档已落档不依赖 worker alive

判断"stale" vs "real" 的依据:

```text
stale if PID ∈ {45984 (proc_18ef87205d55 child), 24140 (proc_2c9af31b96b4 child), 29312 (proc_2c9af31b96b4 parent)}
real  if PID == 42464 (proc_3072bf6953af uvicorn)  应保持 alive
```

## Phase T 闭环承诺

真要响应 — 这是 Phase U 候选。否则：

> **stale hints 上的循环 kill+restart = noise**. 在 Phase U 启动前**不再发 Celery worker** 直到有真任务需要。Phase U 第一个 cell (Linux + prefork 真生产路径) 才是真证据时机。

## 何时重启 worker

worker 启动必须**为真任务服务**，例如：
- 用户请求"起 celery worker + 真发训练"
- Phase U 启动 Linux 真生产路径时
- 新功能/新路径需要真 worker 行为验证

不要为了响应 stale watcher hint 而启 worker。

---

## 同时记录的根因矩阵（不会变化）

| 现象 | 根因 | 来源 | 修复方向 |
|---|---|---|---|
| Celery thread pool revoke 不真发 SIGTERM | `thread.TaskPool.terminate_job` raise NotImplementedError | celery/concurrency/base.py:113 | Linux + prefork (真生产) / pool=solo / 重构内部 poll |
| Cancel API 调 SIGTERM 后 marker 不出现 | 同上 — signal 链根本没触发 | docs/41 §五 traceback | 同上 |
| Worker Windows 上 `--pool=prefork` 不可用 | celery 文档/Windows 架构 | docs/38 §三 | 用 Linux 真生产 or 改成 solo |
