"""tactician.py —— 模型战术层（每秒一拍）

为什么这么切：
  · 实测 deepseek-flash 带思考要 1.6~2.7s，做不了实时；
  · 关掉思考（thinking.type=disabled）后只要 **0.5s**，每秒一拍完全够；
  · 所以：**模型只管选战术意图**（push/poke/retreat/skill/heal/wait），
    脚本负责 200ms 级的闪避/连段/按键。决策权归模型，手脚是脚本。

输入是一行状态摘要（不打图，省延迟），输出一个词。
失败/超时→返回 None，调用方沿用上一个意图（意图带 TTL）。
"""
import json
import os
import time
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
SYS = ("你在实时操作《剑星》里的伊芙打首领。只输出一个词，不要解释："
       "push(贴上去连段) / poke(试探两刀) / retreat(拉开) / skill(放技能) / heal(喝药) / wait(等破绽)")
INTENTS = ("push", "poke", "retreat", "skill", "heal", "wait")


class Tactician:
    def __init__(self, key=None, timeout=6.0):
        self.key = key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.timeout = timeout

    def decide(self, digest):
        """返回 (intent, latency_s)；失败返回 (None, latency)。"""
        if not self.key:
            return None, 0.0
        body = {
            "model": MODEL,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": digest}],
            "max_tokens": 8,
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.load(r)
            txt = (d["choices"][0]["message"].get("content") or "").strip().lower()
        except Exception:
            return None, round(time.time() - t, 2)
        for w in INTENTS:
            if w in txt:
                return w, round(time.time() - t, 2)
        return None, round(time.time() - t, 2)
