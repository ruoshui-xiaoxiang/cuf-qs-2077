"""explore.py —— 自己跑图 + 对风景有反应

用户 10-24：「自己跑图看看」。
做法：
  · 走：W 连推，卡住（3 秒位移 <1m）就小角度转一次视角再走；
  · 看：画面变化足够大（帧差）且不在战斗、距上一句 >20 秒，就截一帧问模型；
  · 说：伊芙口吻一句 ≤25 字，带上最近 3 句禁止重复；
  · 停：游戏失去焦点 / 遇到近距离敌人就掉头 / 到时间上限。

注意：转视角会动一下鼠标（look_yaw 需要把光标放到窗口中心）。
只在卡住时才转，不是每轮都动。
"""
import argparse
import base64
import io
import json
import os
import random
import time
import urllib.request

from PIL import Image

import capture
import inputs
import sb_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "runs", "scenery")
KEY = os.environ.get("DEEPSEEK_API_KEY", "")
SYS = ("你是《剑星》里的伊芙，正在直播跑图。看到画面里的风景，用不超过25个字说一句感想。"
       "语气冷静、克制，可以有一点吐槽。不解释玩法、不报地名、不重复之前说过的句子。")


def say(img, last):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    b64 = base64.b64encode(buf.getvalue()).decode()
    hint = "你现在看到的是这个画面。"
    if last:
        hint += "不要和这几句重复：" + " / ".join(last)
    body = {
        "model": "deepseek-flash",
        "thinking": {"type": "disabled"},
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": [
                         {"type": "text", "text": hint},
                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
        "max_tokens": 60, "temperature": 0.9,
    }
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.load(r)
    return (d["choices"][0]["message"].get("content") or "").strip(), round(time.time() - t, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--turn", type=float, default=35.0, help="卡住时转的角度")
    ap.add_argument("--cooldown", type=float, default=20.0)
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    ok, hwnd = inputs.focus_game()
    if not ok:
        print("游戏不在前台 → 不启动", flush=True)
        return
    time.sleep(0.4)

    import ctypes
    u = ctypes.windll.user32
    t0 = time.time()
    last_frame = capture.grab()
    last_say = -99.0
    said = []
    spoken = 0
    turn_i = 0
    stuck_anchor = None
    stuck_at = time.time()

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        if u.GetForegroundWindow() != hwnd:
            print("%.1fs 失去焦点 → 退出" % t, flush=True)
            break

        st = sb_bridge.read(0.6) or {}
        e = st.get("enemy") or {}
        p = st.get("player") or {}
        d = e.get("dist_cm", 9e9) / 100.0 if e else 999

        # 敌人贴脸就先转身跑
        if e and d < 15:
            inputs.look_yaw(150 + random.uniform(-20, 20))
            inputs.hold_key("sprint" if False else "fwd", 1.6)
            stuck_at = time.time()
            stuck_anchor = None
            continue

        inputs.hold_key("fwd", 1.5)

        # 卡住检测：3 秒内位移 <1m 就转视角
        pos = (p.get("x", 0), p.get("y", 0))
        if stuck_anchor is None:
            stuck_anchor, stuck_at = pos, time.time()
        elif time.time() - stuck_at > 3.0:
            moved = ((pos[0] - stuck_anchor[0]) ** 2 + (pos[1] - stuck_anchor[1]) ** 2) ** 0.5
            if moved < 100:
                turn_i += 1
                deg = a.turn * (1 if turn_i % 2 else -1)
                print("%.1fs 卡住了(%.0fcm) → 转 %.0f°" % (t, moved, deg), flush=True)
                inputs.look_yaw(deg)
            stuck_anchor, stuck_at = pos, time.time()

        # 风景触发：画面变化 + 冷却 + 不在战斗
        if d > 25 and (t - last_say) > a.cooldown:
            now = capture.grab()
            ch, _ = capture.frame_diff(last_frame, now, grid=(8, 6))
            if ch > 0.30:
                frame = now.crop((0, 0, int(now.width * 0.71), now.height))
                frame = frame.resize((900, int(900 * frame.height / frame.width)))
                try:
                    line, lat = say(frame, said[-3:])
                except Exception as ex:
                    print("%.1fs 说失败: %s" % (t, ex), flush=True)
                    line, lat = "", 0
                if line:
                    spoken += 1
                    said.append(line)
                    fn = os.path.join(OUT, "s_%05d.jpg" % int(t * 10))
                    frame.save(fn, quality=85)
                    print("%6.1fs 伊芙（%.2fs）：%s" % (t, lat, line), flush=True)
                    last_say = t
                    last_frame = now
            else:
                print("%6.1fs 画面变化 %.2f，不值得说" % (t, ch), flush=True)

    print("结束：跑图 %.0fs，说了 %d 句" % (time.time() - t0, spoken), flush=True)


if __name__ == "__main__":
    main()
