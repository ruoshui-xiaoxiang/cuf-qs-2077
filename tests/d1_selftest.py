"""D1 隐空间方向干预离线自测（不加载模型，纯逻辑）。

覆盖：
1. mean_diff_normalize 手算对照 + fail-loud（空侧/维度不齐/零差分）
2. make_steering_hook 钩子数学（hidden + α·v，tuple 输出结构保持）
3. 度量通道隔离结构断言：encode/extract_hidden 不引用转向，
   generate_text/generate_chat_text 才引用（干预只加生成侧铁则）
4. ModelAdapter 未加载时 set_steering fail-loud

用法: py -3.12 tests\\d1_selftest.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w2.model_adapter import (ModelAdapter, make_steering_hook,
                              mean_diff_normalize)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))


def main():
    direction, meta = mean_diff_normalize(
        [[2.0, 0.0], [4.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0]])
    check("mean_diff_axis_direction",
          abs(direction[0] - 1.0) < 1e-9 and abs(direction[1]) < 1e-9,
          direction)
    check("mean_diff_meta",
          meta["n_pos"] == 2 and meta["n_neg"] == 2
          and abs(meta["raw_diff_norm"] - 3.0) < 1e-9
          and abs(meta["pos_mean_norm"] - 3.0) < 1e-9
          and meta["neg_mean_norm"] == 0.0,
          meta)

    direction2, _ = mean_diff_normalize(
        [[0.0, 3.0]], [[0.0, 0.0]])
    check("mean_diff_unit_norm",
          abs(direction2[1] - 1.0) < 1e-9, direction2)

    for label, pos, neg in [
        ("empty_pos", [], [[0.0]]),
        ("empty_neg", [[0.0]], []),
        ("dim_mismatch", [[0.0, 0.0]], [[0.0]]),
        ("zero_diff", [[1.0, 2.0]], [[1.0, 2.0]]),
    ]:
        try:
            mean_diff_normalize(pos, neg)
            check(f"mean_diff_fail_loud_{label}", False, "no raise")
        except ValueError:
            check(f"mean_diff_fail_loud_{label}", True)

    import torch
    hidden = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    vec = torch.tensor([0.5, -0.5])
    hook = make_steering_hook(2.0, vec)
    out = hook(None, None, (hidden, "attn"))
    check("steer_hook_tuple_shape",
          isinstance(out, tuple) and out[1] == "attn", type(out))
    expected = torch.tensor([[2.0, 1.0], [4.0, 3.0]])
    check("steer_hook_math", torch.allclose(out[0], expected),
          out[0].tolist())
    out2 = hook(None, None, hidden)
    check("steer_hook_plain_tensor", torch.is_tensor(out2)
          and torch.allclose(out2, expected))
    hook_neg = make_steering_hook(-1.0, vec)
    out3 = hook_neg(None, None, hidden)
    check("steer_hook_negative_alpha",
          torch.allclose(out3, torch.tensor([[0.5, 2.5], [2.5, 4.5]])))

    src = (ROOT / "src" / "w2" / "model_adapter.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    def fn_source(name):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(src, node)
        return None

    encode_src = fn_source("ModelAdapter.encode") or fn_source("encode")
    gen_src = fn_source("generate_text")
    chat_src = fn_source("generate_chat_text")
    extract_src = fn_source("extract_hidden")
    direction_src = fn_source("extract_direction")
    check("encode_exists", encode_src is not None)
    check("encode_no_steering", encode_src is not None
          and "_steer" not in encode_src)
    check("extract_hidden_no_steering", extract_src is not None
          and "_steer" not in extract_src)
    check("extract_direction_no_steering_hook", direction_src is not None
          and "_steer_handle" not in direction_src)
    check("generate_text_steered", gen_src is not None
          and "_steer_handle" in gen_src and "handle.remove()" in gen_src)
    check("generate_chat_text_steered", chat_src is not None
          and "_steer_handle" in chat_src and "handle.remove()" in chat_src)

    adapter = ModelAdapter(
        model_id="dummy", device="cpu", load_in_4bit=False,
        protocol={"layer_index": -1, "token_position": "last_non_pad",
                  "pooling": "last", "read_timing": "post_forward"})
    try:
        adapter.set_steering(16, 2.0, [0.0] * 8)
        check("set_steering_requires_load", False, "no raise")
    except RuntimeError:
        check("set_steering_requires_load", True)
    try:
        adapter.extract_direction(16, ["a"], ["b"])
        check("extract_direction_requires_load", False, "no raise")
    except RuntimeError:
        check("extract_direction_requires_load", True)
    check("steering_status_none_initial", adapter.steering_status() is None)
    adapter.clear_steering()
    check("clear_steering_safe_before_load", adapter.steering_status() is None)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             (" | " + detail if detail and not ok else "")))
    print("\n=== D1 selftest: %d/%d passed ===" % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
