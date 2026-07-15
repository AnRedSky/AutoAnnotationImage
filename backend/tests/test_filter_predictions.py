"""
Unit tests for filter_predictions_to_categories
覆盖: 1) 完全命中 2) 部分命中 3) 完全不命中 4) 标签名归一化 5) 空类目 6) 空预测
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.services.ai_service import filter_predictions_to_categories, _norm_label


def test_norm():
    assert _norm_label("tabby_cat") == "tabby cat"
    assert _norm_label("Tabby Cat") == "tabby cat"
    assert _norm_label("  TABBY_CAT  ") == "tabby cat"
    assert _norm_label("Egyptian_cat") == "egyptian cat"
    assert _norm_label("室内空间") == "室内空间"
    assert _norm_label("class_579") == "class 579"
    assert _norm_label("") == ""
    print("[OK] test_norm")


def test_full_match():
    """类目与模型 top-K 全部命中"""
    preds = [
        {"top1": "tabby_cat", "top1_conf": 0.7, "top5": [
            {"label": "tabby_cat", "confidence": 0.7},
            {"label": "egyptian_cat", "confidence": 0.2},
        ]},
    ]
    cats = ["Tabby Cat", "Egyptian Cat"]
    out = filter_predictions_to_categories(preds, cats)
    assert out[0] is not None
    # 标签应被改写为项目类目名
    assert out[0]["top1"] == "Tabby Cat"
    assert out[0]["top1_conf"] == 0.7
    assert len(out[0]["top5"]) == 2
    assert out[0]["top5"][0]["label"] == "Tabby Cat"
    assert out[0]["top5"][1]["label"] == "Egyptian Cat"
    print("[OK] test_full_match")


def test_no_match():
    """类目与模型 top-K 完全不同 -> None"""
    preds = [
        {"top1": "tabby_cat", "top1_conf": 0.7, "top5": [
            {"label": "tabby_cat", "confidence": 0.7},
            {"label": "class_579", "confidence": 0.2},
        ]},
    ]
    cats = ["室内空间", "办公桌"]
    out = filter_predictions_to_categories(preds, cats)
    assert out[0] is None, f"expected None, got {out[0]}"
    print("[OK] test_no_match")


def test_partial_match():
    """top-K 部分命中, 应只保留命中的"""
    preds = [
        {"top1": "tabby_cat", "top1_conf": 0.5, "top5": [
            {"label": "tabby_cat", "confidence": 0.5},
            {"label": "class_579", "confidence": 0.3},  # 不命中
            {"label": "desk", "confidence": 0.15},      # 命中
        ]},
    ]
    cats = ["Tabby Cat", "Desk", "办公桌"]
    out = filter_predictions_to_categories(preds, cats)
    assert out[0] is not None
    # top1 应是命中项中置信度最高的
    assert out[0]["top1"] == "Tabby Cat"
    assert out[0]["top1_conf"] == 0.5
    # top5 只保留命中的
    assert len(out[0]["top5"]) == 2
    assert out[0]["top5"][0]["label"] == "Tabby Cat"
    assert out[0]["top5"][1]["label"] == "Desk"
    print("[OK] test_partial_match")


def test_empty_categories():
    """项目无类目 -> 全部 None"""
    preds = [
        {"top1": "tabby_cat", "top1_conf": 0.7, "top5": [
            {"label": "tabby_cat", "confidence": 0.7},
        ]},
        {"top1": "desk", "top1_conf": 0.6, "top5": [
            {"label": "desk", "confidence": 0.6},
        ]},
    ]
    out = filter_predictions_to_categories(preds, [])
    assert out == [None, None]
    print("[OK] test_empty_categories")


def test_empty_predictions():
    """空预测列表"""
    out = filter_predictions_to_categories([], ["cat", "dog"])
    assert out == []
    out = filter_predictions_to_categories(None, ["cat", "dog"])
    assert out == []
    print("[OK] test_empty_predictions")


def test_pred_without_top5():
    """pred 缺 top5 字段"""
    preds = [{}]
    out = filter_predictions_to_categories(preds, ["cat"])
    assert out == [None]
    print("[OK] test_pred_without_top5")


def test_reorder_after_filter():
    """top1 不在类目里但 top2 命中 -> top1 应是 top2"""
    preds = [
        {"top1": "class_999", "top1_conf": 0.8, "top5": [
            {"label": "class_999", "confidence": 0.8},  # 不命中
            {"label": "dog", "confidence": 0.15},        # 命中, 应成为新 top1
        ]},
    ]
    cats = ["Dog", "Cat"]
    out = filter_predictions_to_categories(preds, cats)
    assert out[0] is not None
    assert out[0]["top1"] == "Dog"
    assert out[0]["top1_conf"] == 0.15
    assert len(out[0]["top5"]) == 1
    print("[OK] test_reorder_after_filter")


if __name__ == "__main__":
    test_norm()
    test_full_match()
    test_no_match()
    test_partial_match()
    test_empty_categories()
    test_empty_predictions()
    test_pred_without_top5()
    test_reorder_after_filter()
    print("\n[PASS] all unit tests passed")
