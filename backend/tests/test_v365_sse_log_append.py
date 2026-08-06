"""
v3.6.5 HOTFIX — 训练详情 SSE 帧 → log.value 追加 回归测试
==============================================================

**Bug 场景 (用户报告 2026-08-06)**:
训练任务详情页的日志面板在训练过程中不会实时更新, 只能看到打开时拉取到的历史日志。
- 用户启动训练 → 详情面板能显示历史日志 (从 /api/training/jobs/{id}/log 拉)
- worker 每帧调 set_task_state (PROGRESS, ...) → 后端写 TrainingJob.log
- SSE 推送到详情页 (data.state / data.current_epoch / data.message)
- 但前端 onStreamFrame 只更新 ref, 从未向 log.value 追加新行
- 结果: 日志面板卡在打开瞬间的状态, 用户体验差

**根因 (前端)**:
[useTrainingDetailStream.ts](file:///d:/works/WorkBuddy/Myhome/ThesisDesignImplementation/thesis-image-annotation/frontend/src/composables/useTrainingDetailStream.ts)
的 `onStreamFrame` 函数 (原 v3.6.4 前):
```js
const onStreamFrame = (data: any) => {
  const newState = data.state || 'PROGRESS'
  state.value = newState
  progress.value = Number(data.progress || 0)
  currentEpoch.value = data.current_epoch ?? null
  totalEpochs.value = data.total_epochs ?? totalEpochs.value
  message.value = data.message || message.value
  // ⚠️ 没有向 log.value 追加任何新行
  ...
}
```

**修复 (v3.6.5)**:
1. 新增 `appendLogFromSseFrame(data)` 辅助函数: 基于 (state, current_epoch) 签名去重,
   新帧签名不同则追加到 log.value, 并调 saveDetailLog 持久化到后端
2. `onStreamFrame` 中调 `appendLogFromSseFrame(data)`, 保证每帧都过签名过滤
3. `openDetail` 中重置签名 + 用 DB 当前 state/current_epoch 初始化,
   避免历史 log 的最后一行被 SSE 重复追加

**为什么用 (state, current_epoch) 而不是 (state, current_epoch, message)**:
- classification 的 progress_cb 每个 batch 推不同 message (e.g. "Epoch 1/20 batch 1/200"),
  20 epoch × 200 batch = 4000 行, 用户无法阅读
- epoch_callback 每 epoch 推一次, current_epoch=N, 与上一帧 (undefined) 不同 → 追加 1 行
- 新 epoch 首个 per-batch 帧 (current_epoch=undefined) 与 epoch=N-1 不同 → 追加 1 行
  "Epoch N batch 1/Y" 作为新 epoch 起始标记 (信息无害)
- 终态帧 state 变化 → 追加 1 行

**测试模式 (静态分析)**:
- 与 v3.6.3.1 契约测试一致, 纯文本解析, 不依赖 Node.js 实际运行
- 防止未来重构时误删 onStreamFrame → log.value 的连接
- 任何对 appendLogFromSseFrame 的修改都应同步更新本测试

**运行方式**:
    cd backend && python -m pytest tests/test_v365_sse_log_append.py -v
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pytest


# ============== 路径常量 ==============

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
COMPOSABLE = (
    FRONTEND_ROOT / "src" / "composables" / "useTrainingDetailStream.ts"
)


# ============== 静态分析工具 ==============

def _read_source() -> str:
    """读取前端 composable 源码 (UTF-8)"""
    assert COMPOSABLE.exists(), (
        f"v3.6.5 HOTFIX 测试目标文件不存在: {COMPOSABLE}\n"
        f"  请确认前端目录结构与测试一致"
    )
    return COMPOSABLE.read_text(encoding="utf-8")


def _find_function_body(src: str, func_name: str) -> Optional[str]:
    """提取指定函数 (const/let/var/function) 的函数体源码

    使用括号匹配定位 {...} 范围, 避免对 TS AST 的依赖
    """
    # 匹配: const appendLogFromSseFrame = (data: any) => { ... }
    #   或: function appendLogFromSseFrame(data) { ... }
    pattern = (
        rf"(?:const|let|var|function)\s+{re.escape(func_name)}\s*[=(]"
        rf"[^;{{}}]*?[{{]"
    )
    m = re.search(pattern, src, flags=re.DOTALL)
    if not m:
        return None
    start = m.end() - 1  # 指向 {
    depth = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    return None


# ============== T1: 关键函数存在性 ==============

class TestV365SseLogAppend:
    """v3.6.5 HOTFIX 回归测试: SSE 帧 → log.value 追加"""

    def test_append_log_from_sse_frame_function_exists(self):
        """回归点 1: appendLogFromSseFrame 函数必须存在

        背景: 如果未来有人误删该函数, log.value 将再次停止更新。
        """
        src = _read_source()
        body = _find_function_body(src, "appendLogFromSseFrame")
        assert body is not None, (
            "v3.6.5 HOTFIX 回归: useTrainingDetailStream.ts 缺少 "
            "appendLogFromSseFrame 函数。\n"
            "该函数负责把 SSE 帧追加到 log.value, 删除会导致训练日志面板不更新。"
        )
        # 关键调用: log.value.push + saveDetailLog
        assert "log.value.push" in body, (
            "v3.6.5 HOTFIX 回归: appendLogFromSseFrame 函数体中缺少 "
            "log.value.push 调用。"
        )
        assert "saveDetailLog" in body, (
            "v3.6.5 HOTFIX 回归: appendLogFromSseFrame 函数体中缺少 "
            "saveDetailLog 调用 (用于后端持久化)。"
        )

    def test_on_stream_frame_calls_append_log(self):
        """回归点 2: onStreamFrame 必须调 appendLogFromSseFrame

        背景: 如果只定义函数但不在 onStreamFrame 中调用, 等于无修复。
        """
        src = _read_source()
        body = _find_function_body(src, "onStreamFrame")
        assert body is not None, (
            "useTrainingDetailStream.ts 缺少 onStreamFrame 函数"
        )
        assert "appendLogFromSseFrame" in body, (
            "v3.6.5 HOTFIX 回归: onStreamFrame 中未调用 appendLogFromSseFrame。\n"
            "请在 onStreamFrame 中加一行 appendLogFromSseFrame(data), "
            "确保每帧都过签名过滤后追加到 log.value。"
        )

    def test_signature_uses_state_and_current_epoch_only(self):
        """回归点 3: 签名只用 (state, current_epoch), 不含 message

        背景: 之前误用 (state, current_epoch, message) 三元组, 会被 classification
              的 per-batch message 变化穿透, 一个 20 epoch 训练产生 4000+ 行。
        修复: 显式只用 (state, current_epoch) 二元组, 配合 epoch_callback 推 epoch。
        """
        src = _read_source()
        body = _find_function_body(src, "appendLogFromSseFrame")
        assert body is not None, "缺少 appendLogFromSseFrame 函数"

        # 应当出现的字段
        assert "newState" in body, (
            "v3.6.5 签名必须包含 newState (data.state)"
        )
        assert "newEpoch" in body, (
            "v3.6.5 签名必须包含 newEpoch (data.current_epoch)"
        )
        # 不应基于 newMsg 判重
        assert "newMsg" not in body and "lastLoggedMessage" not in body, (
            "v3.6.5 HOTFIX 回归: 签名不应包含 message 字段。\n"
            "原因: classification 的 progress_cb 每 batch 推不同 message, "
            "会导致 20 epoch × 200 batch = 4000 行日志。\n"
            "请改用 (state, current_epoch) 二元组签名。"
        )
        # 显式二元组签名
        assert re.search(
            r"newState\s*!==\s*lastLoggedState\s*\|\|\s*newEpoch\s*!==\s*lastLoggedEpoch",
            body,
        ), (
            "v3.6.5 签名判重必须显式比较 (newState, newEpoch) 与 (lastLoggedState, lastLoggedEpoch)"
        )

    def test_log_line_format_matches_backend(self):
        """回归点 4: buildLogLineFromSseFrame 格式与后端 _build_log_line 一致

        背景: 格式不统一会导致前端日志行与后端历史 log 行拼接时格式混乱。
        后端格式: [YYYY-MM-DD HH:MM:SS] state=PROGRESS progress=42.5% epoch=8/20 msg=...
        前端必须用相同字段顺序与命名, 否则用户视觉错位。
        """
        src = _read_source()
        body = _find_function_body(src, "buildLogLineFromSseFrame")
        assert body is not None, "缺少 buildLogLineFromSseFrame 函数"

        # 关键字段必须出现
        for required in ["state=", "progress=", "epoch=", "msg="]:
            assert required in body, (
                f"buildLogLineFromSseFrame 缺少字段 {required!r}, "
                f"与后端 _build_log_line 格式不一致"
            )
        # 必须用 ISO 时间戳 (与后端 datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") 对齐)
        assert "toISOString" in body, (
            "buildLogLineFromSseFrame 应使用 new Date().toISOString() 拼时间戳"
        )

    def test_open_detail_initializes_signature(self):
        """回归点 5: openDetail 中必须初始化 lastLogged* 签名

        背景: 如果不初始化, 第一次 SSE 帧会与 null 比较, 所有帧都被当作新行追加。
        修复: 用 DB 当前 (state, current_epoch) 初始化, 第一个匹配的 SSE 帧被去重。
        """
        src = _read_source()
        body = _find_function_body(src, "openDetail")
        assert body is not None, "缺少 openDetail 函数"

        # 1) 必有的重置语句
        assert "lastLoggedState = null" in body, (
            "v3.6.5 修复: openDetail 必须先重置 lastLoggedState = null"
        )
        assert "lastLoggedEpoch = null" in body, (
            "v3.6.5 修复: openDetail 必须先重置 lastLoggedEpoch = null"
        )
        # 2) 必有的初始化语句 (从 DB 拿 d.state / d.current_epoch)
        assert re.search(
            r"lastLoggedState\s*=\s*d\.state",
            body,
        ), "v3.6.5 修复: openDetail 必须用 d.state 初始化 lastLoggedState"
        assert re.search(
            r"lastLoggedEpoch\s*=.*d\.current_epoch",
            body,
        ), "v3.6.5 修复: openDetail 必须用 d.current_epoch 初始化 lastLoggedEpoch"

    def test_save_detail_log_throttle_preserved(self):
        """回归点 6: saveDetailLog 仍带 500ms 节流 (防止 SSE 风暴刷后端)

        背景: saveDetailLog 是 POST /api/training/jobs/{id}/log, 每帧调用会刷后端。
        修复保留 500ms 节流, 每秒最多 2 次 POST, 不影响训练主流程。
        """
        src = _read_source()
        body = _find_function_body(src, "saveDetailLog")
        assert body is not None, "缺少 saveDetailLog 函数"
        # 500ms 节流标记
        assert "500" in body, (
            "saveDetailLog 应保留 500ms 节流 (防 SSE 风暴刷后端 /log 端点)"
        )
        # appendLog POST
        assert "appendLog" in body or "trainingApi.appendLog" in body, (
            "saveDetailLog 必须调 trainingApi.appendLog 持久化到后端"
        )


# ============== T2: 反向验证 (模拟 buggy 状态) ==============

class TestV365NegativeCases:
    """反向验证: 如果代码被改回 buggy 状态, 测试会立即失败"""

    def test_no_log_value_push_in_on_stream_frame_directly(self):
        """onStreamFrame 中不应直接写 log.value.push, 必须通过 appendLogFromSseFrame

        背景: 直接 push 会绕过签名去重, 退化为 v3.6.5 之前的 bug 状态。
        """
        src = _read_source()
        body = _find_function_body(src, "onStreamFrame")
        assert body is not None, "缺少 onStreamFrame"
        # 检查 onStreamFrame 中是否直接调用了 log.value.push
        # 允许存在的形式: appendLogFromSseFrame(...) 内部 push
        # 不允许: onStreamFrame 自己 push
        on_stream_push = re.search(
            r"log\.value\.push",
            body or "",
        )
        # 注释中允许提到, 但代码中不允许
        # 简单办法: 如果 onStreamFrame 直接 push, 报错
        # 现实: appendLogFromSseFrame 在另一个函数中 push, 不会出现在 onStreamFrame
        assert not on_stream_push, (
            "v3.6.5 规范: onStreamFrame 不应直接 push 到 log.value, "
            "必须通过 appendLogFromSseFrame(data) 走签名去重。\n"
            "原因: 直接 push 会绕过 lastLogged* 签名, 退化为每帧都追加, "
            "20 epoch × 200 batch = 4000 行日志。"
        )

    def test_no_message_in_signature(self):
        """显式禁止: 签名不应包含 message 字段

        这是 v3.6.5 修复的关键设计: 不基于 message 去重。
        """
        src = _read_source()
        body = _find_function_body(src, "appendLogFromSseFrame")
        assert body is not None, "缺少 appendLogFromSseFrame"
        # 禁止基于 message 比较
        for forbidden in ["newMsg !==", "lastLoggedMessage"]:
            assert forbidden not in body, (
                f"v3.6.5 规范: 签名不应使用 {forbidden!r}, "
                f"会导致 per-batch message 变化穿透去重, 日志行数爆炸。"
            )


# ============== T3: SSE payload 格式契约 (后端 SSE 端点必须给 current_epoch) ==============

class TestSsePayloadContract:
    """后端 SSE 端点必须持续推送 current_epoch 字段, 否则 v3.6.5 修复会失效"""

    SSE_PROGRESS = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "tasks"
        / "api"
        / "training"
        / "progress.py"
    )

    def test_sse_payload_includes_current_epoch(self):
        """SSE payload 必须包含 current_epoch 字段

        背景: v3.6.5 修复依赖 data.current_epoch 判重, 如果后端 SSE 不带这个字段,
              所有 per-epoch 帧会被去重掉, 详情页日志只剩 1-2 行。
        """
        src = self.SSE_PROGRESS.read_text(encoding="utf-8")
        # 找到 payload = { ... } 块
        m = re.search(r"payload\s*=\s*\{([^}]+)\}", src, flags=re.DOTALL)
        assert m, "未找到 SSE payload = { ... } 块"
        payload_body = m.group(1)
        assert "current_epoch" in payload_body, (
            "v3.6.5 回归: SSE payload 不再包含 current_epoch 字段。\n"
            "v3.6.5 前端修复依赖 current_epoch 做日志去重, "
            "后端必须保留该字段。"
        )

    def test_sse_endpoint_streams_progress(self):
        """SSE 端点路径 /progress/stream/{task_id} 必须存在"""
        src = self.SSE_PROGRESS.read_text(encoding="utf-8")
        assert '"/progress/stream/' in src, (
            "SSE 端点路径 /progress/stream/{task_id} 必须存在"
        )
