"""S13 模型适配器自测。

默认运行纯逻辑部分（协议解析 / token 选择 / 池化 / 配置读取 / fail-loud），
不加载模型、不需要 GPU。

加 --live 参数运行实机模型部分：加载 Qwen2.5-7B-Instruct（4bit）→
encode 提取隐状态 → 与 QSKernel 接口对齐（g 与 s_t 同维度、对齐分可计算）。
注意：--live 会占用显存（约 5-6GB），游戏在线时请先确认显存余量。

验收标准：隐状态可按协议稳定读取。
反套壳铁则 #1：适配器只产出向量，不参与裁决。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from w2.model_adapter import (
    ModelAdapter, resolve_token_index, pool_hidden_state,
)
from w2.qs_kernel import QSKernel

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def expect_raises(name, fn, substr=None):
    global PASS, FAIL
    try:
        fn()
        FAIL += 1
        print(f"  FAIL  {name}  (no exception raised)")
    except (ValueError, RuntimeError) as e:
        if substr is None or substr in str(e):
            PASS += 1
            print(f"  PASS  {name}")
        else:
            FAIL += 1
            print(f"  FAIL  {name}  (wrong message: {e})")


def test_pure_logic():
    # ── 1. resolve_token_index ──
    print("\n=== 1. resolve_token_index ===")
    ids = [101, 202, 303, 0]
    mask = [1, 1, 1, 0]
    check("last_non_pad picks index 2",
          resolve_token_index(ids, mask, "last_non_pad") == 2)
    check("last picks index 3",
          resolve_token_index(ids, mask, "last") == 3)
    # 全有效序列
    mask_full = [1, 1, 1, 1]
    check("last_non_pad all-valid picks tail",
          resolve_token_index(ids, mask_full, "last_non_pad") == 3)
    # 中间有 pad（右侧之外）
    check("last_non_pad skips internal pad",
          resolve_token_index([1, 0, 2], [1, 0, 1], "last_non_pad") == 2)
    # fail-loud
    expect_raises("unknown token_position raises",
                  lambda: resolve_token_index(ids, mask, "first"),
                  "unsupported token_position")
    expect_raises("empty sequence raises",
                  lambda: resolve_token_index([], [], "last_non_pad"),
                  "empty")
    expect_raises("all-pad raises",
                  lambda: resolve_token_index([0, 0], [0, 0], "last_non_pad"),
                  "no non-pad")
    expect_raises("mask length mismatch raises",
                  lambda: resolve_token_index(ids, [1, 1], "last_non_pad"),
                  "length")

    # ── 2. pool_hidden_state ──
    print("\n=== 2. pool_hidden_state ===")
    hidden = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    check("pooling last picks target row",
          pool_hidden_state(hidden, 1, "last") == [3.0, 4.0])
    expect_raises("unknown pooling raises",
                  lambda: pool_hidden_state(hidden, 0, "mean"),
                  "unsupported pooling")
    expect_raises("index out of range raises",
                  lambda: pool_hidden_state(hidden, 5, "last"),
                  "out of range")
    expect_raises("negative index raises",
                  lambda: pool_hidden_state(hidden, -1, "last"),
                  "out of range")

    # ── 3. 协议校验 fail-loud ──
    print("\n=== 3. 协议校验 ===")
    good_proto = {"layer_index": -1, "token_position": "last_non_pad",
                  "pooling": "last", "read_timing": "post_forward"}
    a = ModelAdapter("dummy/model", protocol=good_proto)
    check("valid protocol accepted", a.protocol == good_proto)
    check("not loaded initially", a.loaded is False)

    expect_raises("missing protocol field raises",
                  lambda: ModelAdapter("dummy/model",
                                       protocol={"layer_index": -1}),
                  "missing field")
    expect_raises("bad layer_index type raises",
                  lambda: ModelAdapter("dummy/model",
                                       protocol={**good_proto, "layer_index": "-1"}),
                  "must be int")
    expect_raises("bad token_position raises",
                  lambda: ModelAdapter("dummy/model",
                                       protocol={**good_proto, "token_position": "first"}),
                  "unsupported token_position")
    expect_raises("bad read_timing raises",
                  lambda: ModelAdapter("dummy/model",
                                       protocol={**good_proto, "read_timing": "pre_forward"}),
                  "unsupported read_timing")

    # encode before load must fail loud
    expect_raises("encode before load raises",
                  lambda: a.encode("hello"), "not loaded")

    # ── 4. from_config ──
    print("\n=== 4. from_config ===")
    # 断言与 config 单一事实源对比（不硬编码魔数）：
    # 读 config 的 model 段，校验 from_config 全字段透传一致。
    import json as _json
    import pathlib as _pl
    _cfg_path = _pl.Path(__file__).resolve().parents[1] / "config" / "experiment_config.json"
    with open(_cfg_path, "r", encoding="utf-8") as _f:
        _m = _json.load(_f)["model"]
    cfg_adapter = ModelAdapter.from_config()
    check("model_id from config",
          cfg_adapter.model_id == _m["model_id"],
          f"got {cfg_adapter.model_id}, cfg={_m['model_id']}")
    check("device from config",
          cfg_adapter.device == _m.get("device", "cuda:0"),
          f"got {cfg_adapter.device}")
    check("load_in_4bit from config",
          cfg_adapter.load_in_4bit is bool(_m.get("load_in_4bit", True)))
    check("protocol.layer_index == -1",
          cfg_adapter.protocol["layer_index"] == -1)
    check("protocol.token_position == last_non_pad",
          cfg_adapter.protocol["token_position"] == "last_non_pad")
    check("protocol.pooling == last",
          cfg_adapter.protocol["pooling"] == "last")
    check("protocol.read_timing == post_forward",
          cfg_adapter.protocol["read_timing"] == "post_forward")
    check("hf_home from config",
          cfg_adapter.hf_home == _m.get("hf_home"),
          f"got {cfg_adapter.hf_home}, cfg={_m.get('hf_home')}")


def test_live():
    print("\n=== LIVE: 加载模型并提取隐状态 ===")
    adapter = ModelAdapter.from_config()
    adapter.load()
    check("model loaded", adapter.loaded)

    n_layers = len(adapter._model.model.layers)
    check("hook resolved to last layer",
          adapter._resolved_layer == n_layers - 1,
          f"resolved={adapter._resolved_layer}, n_layers={n_layers}")

    # 隐状态提取
    g_emb = adapter.compute_target_g("维持公理稳态：攻击导致HP下降的规则保持有效")
    s_t = adapter.extract_hidden("观测：一次攻击事件后目标HP从100降到80")
    check("g embedding is list of float",
          isinstance(g_emb, list) and isinstance(g_emb[0], float))
    check("s_t is list of float",
          isinstance(s_t, list) and isinstance(s_t[0], float))
    check("g and s_t same dimension (同空间)",
          len(g_emb) == len(s_t),
          f"g={len(g_emb)}, s_t={len(s_t)}")
    dim = len(g_emb)
    check("dimension == model hidden_size",
          dim == adapter._model.config.hidden_size,
          f"got {dim}, expected {adapter._model.config.hidden_size}")
    check("g not zero vector", any(abs(x) > 1e-9 for x in g_emb))
    check("s_t not zero vector", any(abs(x) > 1e-9 for x in s_t))

    # 确定性：同一输入两次提取结果一致（协议稳定性）
    s_t2 = adapter.extract_hidden("观测：一次攻击事件后目标HP从100降到80")
    same = all(abs(a - b) < 1e-5 for a, b in zip(s_t, s_t2))
    check("repeat extraction deterministic", same)

    # 与 QSKernel 接口对齐
    qs = QSKernel.from_config()
    g = qs.compute_target_g(g_emb)
    check("QSKernel accepts g embedding", g is not None)
    score = qs.compute_alignment_score(s_t)
    check("alignment score computed", score is not None)
    check("score in [-1, 1]", -1.0 <= score <= 1.0, f"got {score}")

    # 不同文本的隐状态应不同（向量有区分度）
    s_other = adapter.extract_hidden("无关文本：今天天气晴朗适合散步")
    diff = any(abs(a - b) > 1e-5 for a, b in zip(s_t, s_other))
    check("different texts yield different hidden states", diff)

    adapter.unload()
    check("unload clears model", not adapter.loaded)


def main():
    live = "--live" in sys.argv
    test_pure_logic()
    if live:
        test_live()
    else:
        print("\n（纯逻辑部分完成；加 --live 参数运行实机模型测试）")

    print(f"\n{'='*40}")
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
