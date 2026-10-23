"""探测 GetItemList(玩家) 返回值真实形状（0.6.12 只读）：
类型 / Size 字段 / # 操作符 / 0基1基索引 / 元素喂 GetItemQuantity。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector


def main():
    gc = GameConnector(host="127.0.0.1", port=20770, response_timeout_s=20.0)
    gc.start()
    if not gc.wait_for_game(timeout=90.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    resp = gc.execute_action("probe_inventory_shape", {})
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    (Path(__file__).resolve().parents[1] / "logs" / "live_probe_loot"
     / "inventory_shape.json").write_text(
        json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    gc.stop()


if __name__ == "__main__":
    main()
