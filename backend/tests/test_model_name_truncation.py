"""
test_model_name_truncation
==========================

校验 model_name 长度保护工具函数, 防止写库时 DataError:
- DataError: (pymysql.err.DataError) (1406, "Data too long for column 'model_name'")

覆盖场景:
  - _safe_truncate_model_name
    * 不超长 → 原样返回
    * 超长 → 从左侧截断到 max_len
    * 空字符串 → 原样返回
  - _build_retrain_model_name (再训练)
    * 短名 + 后缀 → 原样拼接
    * 长名 + 后缀 → 截断 base_name, 保留后缀
    * 已有 _r{ts} 后缀的 src_name → 先剥离再加新后缀
    * 后缀长度为 0 (向后兼容) → 走纯截断
    * 边界: base_name 被截断到 0 字符
  - start.py restart 分支代码契约
    * 调用 _build_retrain_model_name (不再用内联 re.sub)
    * 调用 _safe_truncate_model_name (兜底)
"""
import sys
import re
from pathlib import Path


# 强制让 _build_retrain_model_name 中的 re 与生产路径一致
# (避免在 Windows + pyproject 环境下 from app... 触发 DB 初始化)


# ============== 纯函数: 与 start.py 中的实现完全一致 ==============

MODEL_NAME_MAX_LEN = 128
RETRAIN_SUFFIX_LEN = 12


def _strip_retrain_suffix(name: str) -> str:
    return re.sub(r"_r\d+$", "", name)


def _safe_truncate_model_name(name: str, max_len: int = MODEL_NAME_MAX_LEN) -> str:
    if not name:
        return name
    if len(name) <= max_len:
        return name
    return name[:max_len]


def _build_retrain_model_name(
    src_name: str,
    max_len: int = MODEL_NAME_MAX_LEN,
    suffix: str = "",
) -> str:
    base = _strip_retrain_suffix(src_name)
    available = max_len - len(suffix)
    if len(base) > available:
        base = base[:available]
    return f"{base}{suffix}"


# ============== 测试用例 ==============

class TestSafeTruncateModelName:
    """_safe_truncate_model_name 纯截断 (无后缀追加)"""

    def test_short_name_unchanged(self):
        assert _safe_truncate_model_name("resnet50_v1") == "resnet50_v1"

    def test_exact_max_len_unchanged(self):
        name = "a" * 128
        assert _safe_truncate_model_name(name) == name

    def test_over_max_len_truncated(self):
        name = "a" * 200
        result = _safe_truncate_model_name(name)
        assert len(result) == 128
        assert result == "a" * 128

    def test_empty_string(self):
        assert _safe_truncate_model_name("") == ""

    def test_none_passthrough(self):
        # 防御性: None 不应抛异常
        assert _safe_truncate_model_name(None) is None

    def test_custom_max_len(self):
        name = "a" * 50
        assert len(_safe_truncate_model_name(name, max_len=20)) == 20


class TestBuildRetrainModelName:
    """_build_retrain_model_name 构造再训练命名 (截断 + 保留后缀)"""

    def test_short_name_with_suffix(self):
        """短名 + 后缀, 原样拼接"""
        suffix = "_r1701234567"
        result = _build_retrain_model_name("resnet50_v1", suffix=suffix)
        assert result == "resnet50_v1_r1701234567"
        assert len(result) == 12 + 11

    def test_long_name_truncated_keeping_suffix(self):
        """长名 + 后缀, 截断 base_name, 保留后缀

        关键: 后缀必须完整保留 (保证 .pth 文件名唯一 + 用户能看到这是哪次再训练)
        """
        suffix = "_r1701234567"  # 12 字符
        # base_name 长度 200, 加上 12 字符后缀 = 212 → 必须截断
        src = "a" * 200
        result = _build_retrain_model_name(src, suffix=suffix)
        assert len(result) == 128, f"应被截断到 128, 实际 {len(result)}"
        # 后缀必须完整
        assert result.endswith(suffix), f"后缀应保留, 实际结尾: {result[-20:]}"
        assert len(suffix) == 12

    def test_existing_suffix_stripped_before_adding_new(self):
        """已有 _r{ts} 后缀的 src_name, 应先剥离再加新后缀

        场景: 多次再训练, src_name 形如 resnet50_v1_r1701234567
        避免: resnet50_v1_r1701234567_r1701234568 (后缀累积)
        """
        src = "resnet50_v1_r1701234567"
        suffix = "_r1701234568"
        result = _build_retrain_model_name(src, suffix=suffix)
        # 旧后缀应被剥离, 加上新后缀, 不会出现 _r1701234567_r1701234568
        assert result == "resnet50_v1_r1701234568"
        # 校验没有累积多个 _r 后缀
        assert result.count("_r1") == 1

    def test_long_name_with_existing_suffix_stripped_truncated(self):
        """超长 + 已有后缀: 先剥离旧后缀再判断长度, 截断 base_name"""
        suffix = "_r1701234567"
        # 200 字符 base + 12 字符旧后缀 = 212 字符
        src = "a" * 200 + "_r1701234567"
        result = _build_retrain_model_name(src, suffix=suffix)
        assert len(result) == 128
        assert result.endswith(suffix)
        # 前 116 字符应是 'a' (因为旧后缀被剥离, 只剩 200 个 'a')
        # 200 个 'a' 被截断到 128 - 12 = 116
        assert result[:116] == "a" * 116
        # 关键: 中间不能有 "_r1" 两次 (旧后缀已剥离)
        assert result.count("_r1") == 1

    def test_empty_suffix_just_truncates(self):
        """suffix 为空时, 走纯截断 (与 _safe_truncate_model_name 一致)"""
        result = _build_retrain_model_name("a" * 200, suffix="")
        assert len(result) == 128

    def test_no_suffix_keeps_full_base(self):
        """suffix 为空, 短名不变"""
        assert _build_retrain_model_name("resnet50_v1", suffix="") == "resnet50_v1"

    def test_base_truncated_to_zero(self):
        """极端: suffix 长度 >= max_len, base 被截到 0 字符 (不应崩)"""
        suffix = "x" * 128  # 占满全部空间
        result = _build_retrain_model_name("a" * 50, suffix=suffix)
        assert result == suffix  # 只剩后缀
        assert len(result) == 128

    def test_strip_suffix_does_not_match_business_fields(self):
        """剥离 _r{ts} 不能误伤业务字段

        场景: resnet_r2.0 末尾是 .0, 不是纯数字
        """
        result = _strip_retrain_suffix("resnet_r2.0")
        assert result == "resnet_r2.0", f"不应剥离, 实际: {result}"


class TestStartPyContract:
    """校验 start.py / training_service.py 的代码契约"""

    def test_start_py_uses_default_model_name(self):
        """start.py restart 分支应使用 _default_model_name(retrain=True), 不再用 _build_retrain_model_name

        v3.0.0 重构: 之前 restart 会复用 payload 里的 model_name + 内联拼接 _r{ts},
        导致多次再训练后名称累加 (resnet50_v1_r123_r456_r789)。
        新版: 一律重置为 `{base_model}_r_{ts}`, payload 里的 model_name 忽略。
        相关测试详见 test_model_name_naming.py。
        """
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        assert "_default_model_name" in src, (
            "start.py 应调用 _default_model_name 生成默认名"
        )
        # 不应再内联 base_name = re.sub(...) 拼接
        assert "base_name = re.sub(" not in src, (
            "start.py restart 分支不应内联 base_name = re.sub(...) 拼接, "
            "应改用 _default_model_name"
        )

    def test_start_py_defines_safe_truncate(self):
        """start.py 应定义 _safe_truncate_model_name 供 training_service 复用"""
        from app.tasks.api.training import start as start_mod
        assert hasattr(start_mod, "_safe_truncate_model_name"), (
            "start.py 应导出 _safe_truncate_model_name 工具函数"
        )
        assert callable(start_mod._safe_truncate_model_name)

    def test_start_py_uses_safe_truncate_in_relevant_code(self):
        """training_service.start_training 应调用 _safe_truncate_model_name"""
        from app.tasks.service import training_service
        src = Path(training_service.__file__).read_text(encoding="utf-8")
        assert "_safe_truncate_model_name" in src, (
            "training_service.start_training 应截断 model_name 防止超长"
        )

    def test_max_len_matches_db_column(self):
        """MODEL_NAME_MAX_LEN 应与 DB 字段定义一致 (String(128))"""
        # TrainingJob.model_name: String(128)
        # ModelVersion.name: String(128)
        assert MODEL_NAME_MAX_LEN == 128

    def test_start_py_imports_re(self):
        """回归测试: _strip_retrain_suffix 用到 re.sub, 模块顶部必须 import re

        背景: v3.0.0 重构时把 import re 从函数内移到模块顶部, 一旦漏改会触发
              NameError: name 're' is not defined, 这里加一道防线
        """
        import ast
        from app.tasks.api.training import start as start_mod
        src = Path(start_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 找模块顶部 import
        top_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                top_imports.add(node.module)
        assert "re" in top_imports, (
            "start.py 必须在模块顶部 import re (被 _strip_retrain_suffix 使用), "
            f"当前顶部 import: {sorted(top_imports)}"
        )

    def test_start_py_tool_functions_callable(self):
        """start.py 导出的工具函数应可直接调用 (import 时不报错)"""
        from app.tasks.api.training import start as start_mod
        # _strip_retrain_suffix
        assert start_mod._strip_retrain_suffix("a_r123") == "a"
        # _safe_truncate_model_name
        assert start_mod._safe_truncate_model_name("a" * 200) == "a" * 128
        # _build_retrain_model_name
        result = start_mod._build_retrain_model_name("a" * 200, suffix="_r123")
        assert len(result) == 128
        assert result.endswith("_r123")


class TestEndToEndNamingScenarios:
    """端到端命名场景 (模拟用户实际操作)"""

    def test_user_inputs_120_chars_then_retrain(self):
        """用户输入 120 字符 model_name, 再训练时不应超 128"""
        suffix = "_r1701234567"
        # 用户原 model_name 已经是 120 字符
        src = "a" * 120
        result = _build_retrain_model_name(src, suffix=suffix)
        # base_name 120 字符 + suffix 12 = 132, 截断 base_name 到 116
        assert len(result) == 128
        assert result.endswith(suffix)

    def test_retrain_multiple_times_no_accumulation(self):
        """连续再训练 5 次, name 长度不应累积"""
        suffix = "_r1701234567"
        name = "resnet50_v1"
        for _ in range(5):
            name = _build_retrain_model_name(name, suffix=suffix)
        # 不应出现多个 _r 后缀累积
        assert name.count("_r1") == 1
        # 长度不超过 128
        assert len(name) <= 128

    def test_user_inputs_chinese_long_name(self):
        """中文长名场景: 字符数 vs 字节数 (UTF-8 中文 3 字节)

        注意: Python len() 算字符数, MySQL String(128) 在 utf8mb4 下也是字符数
        (utf8mb4 varchar(N) 是 N 个字符, 不是 N 个字节)
        """
        suffix = "_r1701234567"
        # 100 个中文字符 + suffix 12 = 112 (安全)
        src = "模型" * 50  # 100 字符
        result = _build_retrain_model_name(src, suffix=suffix)
        assert len(result) == 112
        # 极端: 130 个中文字符
        src2 = "模型" * 65  # 130 字符
        result2 = _build_retrain_model_name(src2, suffix=suffix)
        # 应被截断到 128
        assert len(result2) == 128
