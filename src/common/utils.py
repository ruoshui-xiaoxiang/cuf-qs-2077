"""公共工具（审计奥卡姆剃刀：消除跨模块重复实现）。

此前 `_is_num`（entity_schema/action_executor）、3D 距离
（rule_validator._distance3d / action_executor._dist3）、
`_player_pos_from`（closed_loop_runner / statistical_baseline）
在多个模块各自实现同一逻辑。统一收口于此，各模块 import 复用。
"""

import math


class ContextOverflowError(RuntimeError):
    """上下文窗口溢出——系统前置条件②破坏（非意识层判据）。

    意识体的采集窗口只能采客观条件，且窗口容量有限（max_length）。
    观测超出窗口即触发本异常，闭环据此标记「系统不可信」：
    系统仍在产出，但产出建立在残缺观测之上，不可采信；该轮对齐分
    不进判据②（不参与功能死亡判定）。区别于「系统不可用」
    （模型 OOM/未加载 → model_state_unavailable，无产出）。
    """


def is_num(v):
    """数值判定：int/float 且排除 bool（bool 是 int 子类，须剔除）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def dist3(a, b):
    """三维欧氏距离。a/b 为 (x, y, z) 序列。"""
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def player_pos_from(entities):
    """从实体列表提取玩家坐标（第一个 kind==player 的 position）。

    Returns:
        (x, y, z) 或 None（无玩家或坐标不合法）。
    """
    for ent in entities:
        if not isinstance(ent, dict) or ent.get("kind") != "player":
            continue
        pos = ent.get("position")
        if not isinstance(pos, dict):
            continue
        x, y, z = pos.get("x"), pos.get("y"), pos.get("z")
        if all(is_num(v) for v in (x, y, z)):
            return (float(x), float(y), float(z))
    return None
