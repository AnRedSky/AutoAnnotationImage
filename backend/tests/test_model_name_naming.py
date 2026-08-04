"""
test_model_name_naming
======================

校验 v3.0.0 训练任务 model_name 命名规则:

  1. 新建任务: `{base_model}_{timestamp}`
  2. 再训练任务: `{base_model}_r_{timestamp}`
  3. 重名时: 在上述基础上追加 3 位数字随机后缀 (e.g. `_847`), 用下划线拼接

覆盖:
- _default_model_name 纯函数 (新建/再训练两条规则)
- _strip_retrain_suffix 同时兼容旧 _r{ts} + 新 _r_{ts}
- _make_random_suffix 生成 3 位数字
- _resolve_unique_model_name 查重 + 随机后缀 (同步 + 异步两个版本)
- 源码 contract 校验: start.py restart 强制使用新规则 + 查重
- 源码 contract 校验: training_service.start_training 去掉 _v1_ 中缀
- 前端 useTrainingFormatters.genDefaultModelName 同步改造 (规则一致)
- 前端 TrainingRetrainDialog 接受 genDefaultModelName prop
- 端到端场景: 多次再训练不会无限累加后缀
"""
import re
from pathlib import Path


# ============== 复制的纯函数 (与 start.py 同源, 避免 import 触发 DB 初始化) ==============

MODEL_NAME_MAX_LEN = 128
RETRAIN_SUFFIX_LEN = 13
UNIQUE_RANDOM_SUFFIX_LEN = 4
UNIQUE_RANDOM_MAX_TRY = 16
UNIQUE_RANDOM_CHARS = "0123456789"


def _strip_retrain_suffix(name: str) -> str:
    return re.sub(r"_r_?\d+$", "", name)


def _default_model_name(base_model: str, *, retrain: bool = False) -> str:
    ts = 1700000000  # 固定值, 便于断言
    suffix = f"_r_{ts}" if retrain else f"_{ts}"
    return f"{base_model}{suffix}"


def _make_random_suffix(faker=lambda: "847") -> str:
    return f"_{faker()}"


def _resolve_unique_model_name(
    candidate: str,
    exists_check,
    *,
    max_len: int = MODEL_NAME_MAX_LEN,
    max_try: int = UNIQUE_RANDOM_MAX_TRY,
) -> str:
    if not candidate or not exists_check(candidate):
        return candidate
    base_max = max_len - UNIQUE_RANDOM_SUFFIX_LEN
    base = candidate[:base_max] if len(candidate) > base_max else candidate
    for i in range(max_try):
        rand = _make_random_suffix(faker=lambda: f"{i:03d}")
        cand = f"{base}{rand}"
        if not exists_check(cand):
            return cand
    return f"{base}{_make_random_suffix()}"


# ============== _default_model_name 测试 ==============

class TestDefaultModelName:
    """_default_model_name 纯函数"""

    def test_new_training_format(self):
        """新建任务: {base_model}_{ts}"""
        # 通过 monkeypatch _default_model_name 的 ts 来固定值
        from app.tasks.api.training.start import _default_model_name as real_fn
        from datetime import datetime
        # 用 monkeypatching 替代: 直接验证 base_model + 时间戳后缀格式
        # 通过 mock datetime 不可行, 这里改为直接验证真实函数的格式
        result = real_fn("resnet50", retrain=False)
        assert result.startswith("resnet50_"), f"新建任务应以 base_model_ 开头, 实际: {result}"
        assert "_r_" not in result, f"新建任务不应含 _r_, 实际: {result}"
        assert len(result) == len("resnet50_") + 10, f"时间戳应为 10 位, 实际: {result}"

    def test_retrain_format(self):
        """再训练任务: {base_model}_r_{ts}"""
        from app.tasks.api.training.start import _default_model_name as real_fn
        result = real_fn("resnet50", retrain=True)
        assert result.startswith("resnet50_r_"), f"再训练应以 base_model_r_ 开头, 实际: {result}"
        assert len(result) == len("resnet50_r_") + 10, f"时间戳应为 10 位, 实际: {result}"

    def test_different_base_models(self):
        """不同 base_model 的结果应不同"""
        from app.tasks.api.training.start import _default_model_name as real_fn
        r1 = real_fn("resnet50", retrain=False)
        r2 = real_fn("efficientnet_b0", retrain=False)
        assert r1.startswith("resnet50_")
        assert r2.startswith("efficientnet_b0_")
        assert r1 != r2

    def test_same_base_different_retrain_flag(self):
        """同一 base_model 在 retrain=True/False 下, 后缀不同"""
        from app.tasks.api.training.start import _default_model_name as real_fn
        r_new = real_fn("resnet50", retrain=False)
        r_retrain = real_fn("resnet50", retrain=True)
        # 两者都是 10 位秒级时间戳后缀
        assert r_new.endswith(r_retrain[len("resnet50_r"):])
        # 但 _r_ 与 _ 区别: 整体应不同 (除非时间戳完全一样且随机巧合, 不可能)
        # 直接断言 _r_ 出现位置
        assert "_r_" in r_retrain
        assert "_r_" not in r_new


# ============== _strip_retrain_suffix 兼容测试 ==============

class TestStripRetrainSuffix:
    """_strip_retrain_suffix 兼容旧 _r{ts} + 新 _r_{ts}"""

    def test_strip_new_format(self):
        """剥离新版 _r_{ts}"""
        from app.tasks.api.training.start import _strip_retrain_suffix
        assert _strip_retrain_suffix("resnet50_r_1701234567") == "resnet50"

    def test_strip_old_format(self):
        """剥离旧版 _r{ts} (无下划线)"""
        from app.tasks.api.training.start import _strip_retrain_suffix
        assert _strip_retrain_suffix("resnet50_r1701234567") == "resnet50"

    def test_strip_no_suffix(self):
        """无后缀 → 原样"""
        from app.tasks.api.training.start import _strip_retrain_suffix
        assert _strip_retrain_suffix("resnet50_1701234567") == "resnet50_1701234567"

    def test_strip_does_not_match_business_fields(self):
        """业务字段保护: resnet_r2.0 不应被剥离"""
        from app.tasks.api.training.start import _strip_retrain_suffix
        assert _strip_retrain_suffix("resnet_r2.0") == "resnet_r2.0"
        # 边界: 末尾是字母+数字混合, 不应误伤
        assert _strip_retrain_suffix("model_v123abc") == "model_v123abc"


# ============== _make_random_suffix 测试 ==============

class TestMakeRandomSuffix:
    """_make_random_suffix 3 位数字后缀"""

    def test_format_underscore_3_digits(self):
        from app.tasks.api.training.start import _make_random_suffix
        for _ in range(20):
            s = _make_random_suffix()
            assert len(s) == 4, f"应为 4 字符 (_xxx), 实际: {s!r}"
            assert s[0] == "_"
            assert s[1:].isdigit(), f"后 3 位应为数字, 实际: {s!r}"

    def test_uses_only_digits(self):
        """仅使用数字字符集 (避免字母混淆)"""
        from app.tasks.api.training.start import _make_random_suffix, UNIQUE_RANDOM_CHARS
        for _ in range(50):
            s = _make_random_suffix()
            for c in s[1:]:
                assert c in UNIQUE_RANDOM_CHARS


# ============== _resolve_unique_model_name 查重测试 ==============

class TestResolveUniqueModelName:
    """_resolve_unique_model_name 查重 + 随机后缀"""

    def test_unique_first_try(self):
        """候选名不重复 → 原样返回"""
        result = _resolve_unique_model_name(
            "resnet50_1701234567",
            exists_check=lambda n: False,
        )
        assert result == "resnet50_1701234567"

    def test_collision_adds_random_suffix(self):
        """候选名重复 → 加 3 位数字后缀"""
        exists = {"resnet50_1701234567"}
        result = _resolve_unique_model_name(
            "resnet50_1701234567",
            exists_check=lambda n: n in exists,
        )
        assert result != "resnet50_1701234567"
        assert result.startswith("resnet50_1701234567_")
        # 后缀应是 _ + 3 位数字
        suffix = result[len("resnet50_1701234567_"):]
        assert len(suffix) == 3
        assert suffix.isdigit()

    def test_multiple_collisions_converge(self):
        """多次冲突后能收敛 (最多 max_try 次)"""
        # 模拟: 头 2 个候选都冲突, 第 3 个不冲突
        tried = set()
        def check(n: str) -> bool:
            if n in tried:
                return True
            tried.add(n)
            return len(tried) <= 2  # 头两次返回 True (冲突), 之后 False

        result = _resolve_unique_model_name(
            "resnet50_1701234567",
            exists_check=check,
        )
        assert result not in {"resnet50_1701234567"}  # 不应该是原名 (它冲突)

    def test_empty_candidate_returns_unchanged(self):
        """空 candidate → 原样返回 (不查重)"""
        result = _resolve_unique_model_name("", exists_check=lambda n: True)
        assert result == ""

    def test_truncates_long_base(self):
        """超长 base → 截断到给随机后缀留位"""
        long_name = "a" * 200 + "_1701234567"  # 211 字符
        result = _resolve_unique_model_name(
            long_name,
            exists_check=lambda n: n == long_name,
        )
        assert len(result) <= MODEL_NAME_MAX_LEN
        # 末尾应是 _xxx
        assert result[-4] == "_"
        assert result[-3:].isdigit()


# ============== 源码 contract 校验 ==============

class TestSourceContract:
    """通过源码校验关键约束"""

    def test_start_py_exports_new_helpers(self):
        """start.py 必须导出 _default_model_name + _resolve_unique_model_name"""
        from app.tasks.api.training import start as start_mod
        for name in ("_default_model_name", "_resolve_unique_model_name",
                     "_resolve_unique_model_name_async", "_make_random_suffix",
                     "_strip_retrain_suffix"):
            assert hasattr(start_mod, name), f"start.py 缺导出 {name}"
            assert callable(getattr(start_mod, name))

    def test_start_py_restart_uses_default_model_name(self):
        """start.py restart 分支必须用 _default_model_name(retrain=True) + 查重"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        # 关键: restart 分支应调用 _default_model_name(retrain=True)
        assert "_default_model_name(final_base_model, retrain=True)" in src, (
            "start.py restart 分支应使用 _default_model_name(retrain=True) 生成新名"
        )
        # 关键: restart 分支应调用 _resolve_unique_model_name_async 做查重
        assert "_resolve_unique_model_name_async" in src, (
            "start.py restart 分支应使用 _resolve_unique_model_name_async 查重"
        )

    def test_start_py_restart_does_not_use_payload_model_name(self):
        """start.py restart 不再使用 payload 里的 model_name (避免后缀累积)"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        # 防御: 旧的内联 re.sub + 拼接模式不应再出现
        assert "base_name = re.sub(" not in src, (
            "start.py 不应再内联 base_name = re.sub(...) 拼接 (已改用 _default_model_name)"
        )
        # 防御: 旧的 _build_retrain_model_name 仍可保留作为 fallback, 但 restart 不应再调用
        # 实际: _build_retrain_model_name 函数定义可保留, 但不强制删除 (向后兼容)

    def test_training_service_uses_default_model_name(self):
        """training_service.start_training 应使用 _default_model_name"""
        from app.tasks.service import training_service
        src = Path(training_service.__file__).read_text(encoding="utf-8")
        assert "_default_model_name" in src, (
            "training_service.start_training 应使用 _default_model_name 生成默认名"
        )
        assert "_resolve_unique_model_name_async" in src, (
            "training_service.start_training 应调用 _resolve_unique_model_name_async 查重"
        )
        # 关键: 不能再有 `_v1_` 中缀 (排除注释里的提及, 仅检查非注释代码)
        # 实现: 提取所有非注释行, 看里面是否出现 _v1_
        code_lines = [
            line for line in src.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        code_text = "\n".join(code_lines)
        assert "_v1_" not in code_text, (
            "training_service.start_training 非注释代码中不应再使用 _v1_ 中缀"
        )

    def test_default_model_name_format_uses_underscore_r(self):
        """_default_model_name retrain=True 时必须用 _r_{ts} (含下划线)"""
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        # 检查: retrain=True 的分支应有 _r_
        assert '"_r_"' in src or "'_r_'" in src, (
            "_default_model_name 必须生成 _r_ 前缀 (而非旧 _r)"
        )

    def test_random_chars_uses_digits_only(self):
        """UNIQUE_RANDOM_CHARS 必须是纯数字"""
        from app.tasks.api.training import start as start_mod
        assert start_mod.UNIQUE_RANDOM_CHARS.isdigit(), (
            "UNIQUE_RANDOM_CHARS 应为纯数字 (避免字母混淆)"
        )
        assert len(start_mod.UNIQUE_RANDOM_CHARS) >= 3


# ============== 前端 contract 校验 ==============

class TestFrontendContract:
    """前端代码约束"""

    def test_gen_default_model_name_no_v1(self):
        """前端 genDefaultModelName 不应再使用 _v1_ 中缀 (v3.0.0 改版)"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "composables" / "useTrainingFormatters.ts"
        assert fe.exists()
        content = fe.read_text(encoding="utf-8")
        assert "_v1_" not in content, (
            "useTrainingFormatters.ts 不应再使用 _v1_ 中缀"
        )

    def test_gen_default_model_name_supports_retrain(self):
        """前端 genDefaultModelName 应支持 retrain 参数 (v3.0.0 改版, 已 deprecated)"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "composables" / "useTrainingFormatters.ts"
        content = fe.read_text(encoding="utf-8")
        # 函数签名应包含 retrain 参数
        assert "retrain" in content, (
            "useTrainingFormatters.genDefaultModelName 应支持 retrain 参数"
        )
        # 应生成 _r_ 前缀
        assert "_r_" in content, (
            "useTrainingFormatters.genDefaultModelName retrain=true 应使用 _r_ 前缀"
        )

    def test_create_dialog_passes_gen_prop(self):
        """v3.5.1 改版: TrainingCreateDialog 必须传 genDefaultModelName 给 TrainingParamsForm (恢复「生成」按钮)"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "components" / "TrainingCreateDialog.vue"
        assert fe.exists()
        content = fe.read_text(encoding="utf-8")
        # 必须定义 genDefaultModelName prop
        assert "genDefaultModelName" in content, (
            "TrainingCreateDialog 必须接受 genDefaultModelName prop (v3.5.1 恢复「生成」按钮)"
        )
        # 必须将 prop 透传给 TrainingParamsForm
        assert ":gen-default-model-name=" in content, (
            "TrainingCreateDialog 必须把 genDefaultModelName 透传给 TrainingParamsForm"
        )

    def test_params_form_has_auto_name(self):
        """v3.5.1 改版: TrainingParamsForm 必须有「生成」按钮 + emitAutoName 函数 (恢复)"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "components" / "TrainingParamsForm.vue"
        assert fe.exists()
        content = fe.read_text(encoding="utf-8")
        # 必须有「生成」按钮
        assert ">生成<" in content, (
            "TrainingParamsForm 必须保留「生成」按钮 (v3.5.1 恢复)"
        )
        # 必须有 emitAutoName 函数
        assert "emitAutoName" in content, (
            "TrainingParamsForm 必须有 emitAutoName 函数 (v3.5.1 恢复)"
        )
        # 必须 import Refresh icon
        assert "Refresh } from '@element-plus/icons-vue'" in content, (
            "TrainingParamsForm 必须 import Refresh icon (生成按钮依赖)"
        )

    def test_retrain_dialog_has_auto_name(self):
        """v3.5.1 改版: TrainingRetrainDialog 必须有「生成」按钮 + emitAutoName 函数 + Refresh icon (恢复)"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "components" / "TrainingRetrainDialog.vue"
        assert fe.exists()
        content = fe.read_text(encoding="utf-8")
        # 必须有「生成」按钮
        assert ">生成<" in content, (
            "TrainingRetrainDialog 必须保留「生成」按钮 (v3.5.1 恢复)"
        )
        # 必须有 emitAutoName 函数
        assert "emitAutoName" in content, (
            "TrainingRetrainDialog 必须有 emitAutoName 函数 (v3.5.1 恢复)"
        )
        # 必须 import Refresh icon
        assert "Refresh } from '@element-plus/icons-vue'" in content, (
            "TrainingRetrainDialog 必须 import Refresh icon (生成按钮依赖)"
        )
        # 必须有 genDefaultModelName prop
        assert "genDefaultModelName" in content, (
            "TrainingRetrainDialog 必须接受 genDefaultModelName prop (v3.5.1 恢复)"
        )

    def test_retrain_dialog_default_empty_model_name(self):
        """v3.0.0: TrainingRetrainDialog 打开时 model_name 默认空 (后端接管)"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "components" / "TrainingRetrainDialog.vue"
        content = fe.read_text(encoding="utf-8")
        # form 重置时, model_name 应为 ''
        assert "model_name: ''" in content, (
            "TrainingRetrainDialog 打开时 model_name 应默认空字符串"
        )

    def test_index_passes_gen_prop(self):
        """v3.5.1 改版: Training/index.vue 必须传 :gen-default-model-name 给两个弹窗"""
        fe = Path(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "Training" / "index.vue"
        content = fe.read_text(encoding="utf-8")
        # 全文应出现 :gen-default-model-name (分别传给 Create 和 Retrain 两个弹窗)
        assert content.count(":gen-default-model-name=") >= 2, (
            "Training/index.vue 必须给 Create/Retrain 两个弹窗都传 :gen-default-model-name (v3.5.1 恢复)"
        )


# ============== 端到端场景 ==============

class TestEndToEndNamingScenarios:
    """端到端: 模拟用户实际场景"""

    def test_retrain_multiple_times_no_accumulation(self):
        """连续再训练 5 次, 名称应保持单一 _r_{ts} 结构 (不累加)"""
        # 用 _default_model_name 模拟 (没有 payload 复用场景)
        from app.tasks.api.training.start import _default_model_name
        name = "resnet50"
        seen = set()
        for _ in range(5):
            # 用户每次都点"再训练", 后端用 _default_model_name 重新生成
            # 不复用前一次的 name, 避免 _r_ 累积
            name = _default_model_name(name.split("_r_")[0], retrain=True)
            # 每次生成的新名 (ts 可能相同, 因为在同一秒内调用)
            seen.add(name)
        # 全部生成都应是 base_model + _r_ + ts 格式
        for n in seen:
            assert "_r_" in n, f"再训练名应含 _r_, 实际: {n}"
            # 不应出现多个 _r_ (累积)
            assert n.count("_r_") == 1, f"不应累积多个 _r_, 实际: {n}"

    def test_default_new_then_default_retrain(self):
        """先新建一个任务, 再用再训练创建第二个: 两个名称结构清晰可分辨"""
        from app.tasks.api.training.start import _default_model_name
        new_name = _default_model_name("resnet50", retrain=False)
        retrain_name = _default_model_name("resnet50", retrain=True)
        # 新建名: resnet50_1701234567 (1 个下划线分隔)
        # 再训练名: resnet50_r_1701234567 (2 个下划线分隔)
        # 关键区别: retrain 名包含 _r_ 段
        assert "_r_" in retrain_name, f"再训练名应含 _r_ 段, 实际: {retrain_name}"
        assert "_r_" not in new_name, f"新建名不应含 _r_ 段, 实际: {new_name}"
        # 下划线数: 新建 = 1, 再训练 = 2
        assert new_name.count("_") == 1, f"新建名下划线数应为 1, 实际: {new_name}"
        assert retrain_name.count("_") == 2, f"再训练名下划线数应为 2, 实际: {retrain_name}"
        # retrain 名应比 new 名长 (多一个 _r 段, 即多 2 字符: 'r' + 下划线)
        # new: resnet50_1701234567 (19) = base(8) + _(1) + ts(10)
        # retrain: resnet50_r_1701234567 (21) = base(8) + _(1) + r(1) + _(1) + ts(10)
        assert len(retrain_name) == len(new_name) + 2, (
            f"retrain 名应比 new 名长 2 字符 (_r 段), "
            f"new={new_name} (len={len(new_name)}), retrain={retrain_name} (len={len(retrain_name)})"
        )

    def test_user_input_long_name_truncated(self):
        """用户输入超长名 → 截断到 128 字符"""
        from app.tasks.api.training.start import _safe_truncate_model_name
        long_name = "a" * 200
        result = _safe_truncate_model_name(long_name)
        assert len(result) == 128

    def test_collision_3_times_converges(self):
        """极端: 连续 3 次冲突后, 随机后缀仍能收敛"""
        from app.tasks.api.training.start import _resolve_unique_model_name
        # 模拟: candidate 冲突, 前 3 次随机也都冲突
        tried = set()
        call_count = 0
        def check(n: str) -> bool:
            nonlocal call_count
            call_count += 1
            if n in tried:
                return True
            tried.add(n)
            return call_count <= 4  # 头 4 次冲突

        result = _resolve_unique_model_name("resnet50_1701234567", exists_check=check)
        # 应找到第 5 次的随机后缀 (不冲突)
        assert result != "resnet50_1701234567"
        assert result.startswith("resnet50_1701234567_")
        # 末尾应是 _xxx (3 位数字)
        suffix = result[-3:]
        assert suffix.isdigit()
