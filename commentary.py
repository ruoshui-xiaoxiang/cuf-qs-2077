"""commentary.py —— 状态驱动的实时解说（伊芙口吻）

读剑星的主角/敌人/HP 状态，在事件点生成一句中文，用 Edge TTS 念出来。
不是给桌宠配预录台词，而是“以伊芙的身份看局面自己开口”。

事件（v1）：
  遇敌、受击、护盾破裂、低血量、敌人脱战、赶路、长时间无事、阵亡/复活
语音：Edge TTS，默认 zh-CN-XiaoyiNeural（晓伊，和鲸鱼娘桌宠同音色）
播放：PyAV 解 MP3 → sounddevice 输出；OBS 抓桌面音频即可。
原则：短句、有冷却、战斗优先；不抢话、不刷屏。

用法：
  py -3.12 commentary.py --say "测试，鲸鱼娘上线。"     # 单句试音
  py -3.12 commentary.py --seconds 120                 # 跑 2 分钟
  py -3.12 commentary.py --seconds 300 --no-idle       # 只报事件，不闲聊
"""
import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import queue
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

import av
import numpy as np
import sounddevice as sd
import edge_tts

import sb_bridge
import hud_live

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
SR = 24000

# 事件线；每条 3~5 个变体，避免复读机
LINES = {
    "enemy": [
        "有敌人。",
        "前面有动静。",
        "准备战斗。",
        "发现目标。",
    ],
    "damage": [
        "受伤了。",
        "疼……",
        "注意闪避。",
        "这一下不轻。",
    ],
    "shield_break": [
        "护盾破了。",
        "盾没了，先拉开。",
        "护盾失效。",
    ],
    "low_hp": [
        "血量不多了。",
        "再挨一下就危险了。",
        "先撤，保住命。",
    ],
    "enemy_gone": [
        "解决了。",
        "目标清除。",
        "结束了。",
    ],
    "travel": [
        "继续赶路。",
        "这里的风沙真大。",
        "还有一段路。",
    ],
    "idle": [
        "接下来去哪？",
        "这里安静得有点不真实。",
        "我在。",
        "需要我做什么？",
    ],
    "death": [
        "倒下了……读档吧。",
        "这次失误了。",
        "重来。",
    ],
    "revive": [
        "回来了。",
        "继续。",
        "这次小心。",
    ],
}


class Speaker:
    """单线程 TTS 队列；高优先级可以打断低优先级。

    engine:
      auto = 先 MiMo 云 TTS（有 MIMO_API_KEY 时），再 Edge，最后 SAPI
      mimo = 只用 MiMo 云 TTS（不占本地显存）
      edge = 只用 Edge TTS
      sapi = 只用 Windows SAPI（离线，Huihui，偏播音腔）
    """

    def __init__(self, voice, gain=0.8, dry=False, engine="auto"):
        self.voice = voice
        self.gain = gain
        self.dry = dry
        self.engine = engine
        self.cache = {}
        self.q = queue.PriorityQueue()
        self.seq = 0
        self.current_prio = 99
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.busy = threading.Event()      # 正在合成/播放；命令行退出前要等它清空
        self.pending = 0                   # 已入队未播完的句数
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def wait_idle(self, timeout=120.0):
        """等到队列空且最后一句播完；返回是否在超时前完成。"""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                done = self.pending <= 0
            if done and self.q.empty() and not self.busy.is_set():
                return True
            time.sleep(0.1)
        return False

    def say(self, text, prio=5):
        if self.dry:
            print("[TTS]", text, flush=True)
            return
        with self.lock:
            self.seq += 1
            self.pending += 1
            self.q.put((prio, self.seq, text))
            if prio < self.current_prio:
                sd.stop()  # 战斗/危险优先，打断闲聊

    def prewarm(self, text):
        """只合成到缓存，不播放；给动态台词用。"""
        key = os.environ.get("MIMO_API_KEY") or os.environ.get("XIAOMI_API_KEY")
        if not key:
            return False
        self._mimo(text, key)
        return True

    def _run(self):
        while not self.stop_flag.is_set():
            try:
                prio, _, text = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.stop_flag.is_set():
                break
            with self.lock:
                self.current_prio = prio
            self.busy.set()
            try:
                used = None
                if self.engine in ("auto", "mimo"):
                    key = os.environ.get("MIMO_API_KEY") or os.environ.get("XIAOMI_API_KEY")
                    if key:
                        try:
                            self._play(self._mimo(text, key))
                            used = "mimo"
                        except Exception as e:
                            if self.engine == "mimo":
                                raise
                            print("[TTS] MiMo 失败，回落:", type(e).__name__, flush=True)
                    elif self.engine == "mimo":
                        raise RuntimeError("MIMO_API_KEY / XIAOMI_API_KEY 未设置")
                if used is None and self.engine in ("auto", "edge"):
                    try:
                        mp3 = os.path.join(HERE, "runs", "_tts_%d.mp3" % int(time.time() * 1000))
                        os.makedirs(os.path.dirname(mp3), exist_ok=True)
                        asyncio.run(edge_tts.Communicate(text, self.voice).save(mp3))
                        self._play(mp3)
                        used = "edge"
                        try:
                            os.remove(mp3)
                        except OSError:
                            pass
                    except Exception as e:
                        if self.engine == "edge":
                            raise
                        print("[TTS] Edge 失败，回落 SAPI:", type(e).__name__, flush=True)
                if used is None or self.engine == "sapi":
                    self._speak_sapi(text)
            except Exception as e:
                print("[TTS] 失败:", type(e).__name__, e, flush=True)
            finally:
                self.busy.clear()
                with self.lock:
                    self.current_prio = 99
                    self.pending = max(0, self.pending - 1)

    @staticmethod
    def _find_audio(o):
        """在 MiMo 的 chat/completions 响应里递归找 base64/URL 音频。"""
        if isinstance(o, dict):
            a = o.get("audio")
            if isinstance(a, dict):
                for k in ("data", "content", "audio", "url"):
                    v = a.get(k)
                    if isinstance(v, str) and len(v) > 64:
                        return v
            elif isinstance(a, str) and len(a) > 64:
                return a
            for k, v in o.items():
                if k in ("data", "audio_url", "url") and isinstance(v, str) \
                        and len(v) > 64 and (v.startswith("http") or v.startswith("data:") or len(v) > 200):
                    return v
                r = Speaker._find_audio(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = Speaker._find_audio(v)
                if r:
                    return r
        return None

    def _mimo(self, text, key):
        """MiMo 云 TTS：POST /chat/completions，audio 参数带 voice/format。

        环境变量：
          MIMO_API_KEY / XIAOMI_API_KEY  必填
          MIMO_BASE_URL  默认 https://api.xiaomimimo.com/v1
          MIMO_MODEL     默认 mimo-v2.5-tts；克隆用 mimo-v2.5-tts-voiceclone
          MIMO_VOICE     预置音色 ID，默认 mimo_default
          MIMO_VOICE_SAMPLE  克隆参考音频路径（默认 assets/voice/eve_ref.mp3）
        """
        base = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").rstrip("/")
        model = os.environ.get("MIMO_MODEL", "mimo-v2.5-tts")
        if "voiceclone" in model:
            sample = os.environ.get("MIMO_VOICE_SAMPLE",
                                   os.path.join(HERE, "assets", "voice", "eve_ref.mp3"))
            if not os.path.exists(sample):
                raise RuntimeError("克隆参考音频不存在: " + sample)
            st = os.stat(sample)
            sig = "%s|%d|%d" % (sample, st.st_mtime_ns, st.st_size)
            if getattr(self, "_voice_sig", None) != sig:
                raw = open(sample, "rb").read()
                mime = "audio/mpeg" if sample.lower().endswith(".mp3") else "audio/wav"
                self._voice_uri = "data:%s;base64,%s" % (
                    mime, base64.b64encode(raw).decode("ascii"))
                self._voice_sig = sig
            voice = self._voice_uri
            tag = "clone:" + sig
        else:
            voice = os.environ.get("MIMO_VOICE", "mimo_default")
            tag = voice
        digest = hashlib.md5((model + "|" + tag + "|" + text).encode("utf-8")).hexdigest()
        out = os.path.join(HERE, "runs", "_mimo_%s.mp3" % digest)
        retried = getattr(self, "_retried", None)
        if retried is None:
            retried = self._retried = set()
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            try:
                cached_ok = out in retried or self._tail_gap(self._decode(out)) >= 0.05
            except Exception:
                cached_ok = False
            if cached_ok:
                self.cache[text] = out
                return out
            # 旧缓存尾巴是硬切的，重录一次
            retried.add(out)

        def fetch(say_text):
            body = {
                "model": model,
                "messages": [{"role": "assistant", "content": say_text}],
                "audio": {"format": "mp3", "voice": voice},
            }
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                base + "/chat/completions", data=data,
                headers={"api-key": key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    raw = r.read()
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "replace")
                raise RuntimeError("MiMo HTTP %s: %s" % (e.code, detail))
            try:
                j = json.loads(raw)
            except Exception:
                raise RuntimeError("MiMo 非 JSON 响应: " + raw[:200].decode("utf-8", "replace"))
            a = self._find_audio(j)
            if not a:
                raise RuntimeError("MiMo 响应里找不到音频: " + json.dumps(j, ensure_ascii=False)[:300])
            if a.startswith("http"):
                with urllib.request.urlopen(a, timeout=40) as r:
                    return r.read()
            if a.startswith("data:"):
                a = a.split(",", 1)[1]
            return base64.b64decode(a)

        def score(blob):
            tmp = out + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
            try:
                return self._tail_gap(self._decode(tmp))
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        blob = fetch(text)
        gap0 = gap = score(blob)
        if gap < 0.05:
            # 句尾被 40ms 帧硬切：加标点重录，取尾音最完整的一版
            for say_text in (text + "。", text + "……"):
                try:
                    b2 = fetch(say_text)
                except Exception:
                    continue
                g2 = score(b2)
                if g2 > gap:
                    blob, gap = b2, g2
                if gap >= 0.05:
                    break
            print("[TTS] 尾音重录 %.0fms → %.0fms：%s"
                  % (gap0 * 1000, gap * 1000, text), flush=True)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(blob)
        self.cache[text] = out
        return out

    def _speak_sapi(self, text):
        """Windows SAPI 离线朗读；Huihui 是当前可用的中文女声。"""
        os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
        txt = os.path.join(HERE, "runs", "_say.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice('Microsoft Huihui Desktop'); "
            "$s.Rate=0; $s.Volume=%d; "
            "$t=[IO.File]::ReadAllText('%s',[Text.Encoding]::UTF8); "
            "$s.Speak($t)" % (int(self.gain * 100), txt.replace("'", "''"))
        )
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", script], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


    def _decode(self, path):
        """解成 24k 单声道 float32；务必 flush 重采样器，否则尾部丢帧。"""
        with av.open(path) as c:
            stream = c.streams.audio[0]
            res = av.AudioResampler(format="s16", layout="mono", rate=SR)
            chunks = []
            for frame in c.decode(stream):
                for f in res.resample(frame):
                    chunks.append(f.to_ndarray()[0].copy())
            for f in res.resample(None):          # flush：把重采样器里的残余吐出来
                chunks.append(f.to_ndarray()[0].copy())
        if not chunks:
            return None
        return np.concatenate(chunks).astype(np.float32) / 32768.0

    @staticmethod
    def _tail_gap(pcm):
        """最后一个有效音节到文件末尾的静音时长（秒）。

        MiMo 按 40ms 帧生成，偶尔会在字中间硬切；硬切时这个值 < 0.05。
        """
        if pcm is None or len(pcm) < SR // 10:
            return 1.0
        idx = np.where(np.abs(pcm) > 0.02)[0]
        if len(idx) == 0:
            return 1.0
        return (len(pcm) - 1 - idx[-1]) / float(SR)

    def _play(self, path):
        pcm = self._decode(path)
        if pcm is None:
            return
        pcm = np.clip(pcm * self.gain, -1.0, 1.0)
        # 尾部收口：20ms 余弦淡出，避免硬切爆音；再垫 250ms 静音，
        # 让句尾听上去是"讲完了"而不是"被掐掉了"。
        nf = min(len(pcm), int(0.02 * SR))
        if nf > 0:
            pcm[-nf:] *= 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, nf)))
        pad = np.zeros(int(0.25 * SR), dtype=np.float32)
        pcm = np.concatenate([pcm, pad])
        sd.play(pcm, SR)
        sd.wait()

    def stop(self):
        self.stop_flag.set()
        sd.stop()


PERSONA = (
    "你是伊芙（EVE），第7空降小队战士，正在夺回地球。"
    "说话冷静、简短、坚定，第一人称；战斗时报点，赶路时只说必要的话。"
    "不卖萌、不自称鲸鱼娘、不叫观众鱼片；不编造游戏里没有的设定。"
    "只输出一句 1~2 个短句的中文台词，不要解释、不要引号、不要列表。"
)


class LineGen:
    """混合模式：给 idle/travel 动态生成伊芙台词，并预先合成到 TTS 缓存。

    - 战斗事件仍用固定预合成句，保证即时；
    - idle/travel 优先用已生成池；池空时先播固定句兜底，再后台生成新句。
    """

    DYNAMIC = ("idle", "travel")

    def __init__(self, spk, prefetch=2):
        self.spk = spk
        self.q = queue.Queue()
        self.pools = {k: [] for k in self.DYNAMIC}
        self.seen = set()
        self.lock = threading.Lock()
        self.path = os.path.join(HERE, "runs", "eve_dynamic_lines.json")
        self._load()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        for _ in range(prefetch):
            for k in self.DYNAMIC:
                self.request(k, {})

    def _load(self):
        try:
            d = json.load(open(self.path, encoding="utf-8"))
            for k in self.DYNAMIC:
                self.pools[k] = list(d.get(k) or [])
                self.seen.update(self.pools[k])
            print("[dynamic] 载入 %d 条" % len(self.seen), flush=True)
        except Exception:
            pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.pools, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def request(self, kind, state):
        if kind not in self.DYNAMIC:
            return
        with self.lock:
            if len(self.pools[kind]) >= 8:
                return
        self.q.put((kind, dict(state or {})))

    def take(self, kind):
        with self.lock:
            p = self.pools.get(kind) or []
            return p.pop(0) if p else None

    def _run(self):
        while True:
            kind, state = self.q.get()
            try:
                text = self._gen(kind, state)
                if not text or text in self.seen:
                    continue
                self.seen.add(text)
                with self.lock:
                    self.pools.setdefault(kind, []).append(text)
                self.spk.prewarm(text)
                self._save()
                print("[dynamic] %s: %s" % (kind, text), flush=True)
            except Exception as e:
                print("[dynamic FAIL] %s: %s %s" % (
                    kind, type(e).__name__, str(e)[:120]), flush=True)

    def _gen(self, kind, state):
        key = os.environ.get("MIMO_API_KEY") or os.environ.get("XIAOMI_API_KEY")
        if not key:
            raise RuntimeError("MIMO_API_KEY 未设置")
        base = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").rstrip("/")
        model = os.environ.get("MIMO_CHAT_MODEL", "mimo-v2.6-flash")
        hp = state.get("hp")
        dist = state.get("enemy_dist")
        speed = state.get("speed")
        ctx = "事件：%s；血量：%s；敌人距离：%s cm；移动速度：%s cm/s" % (
            kind,
            "%.0f%%" % (hp * 100) if isinstance(hp, (int, float)) else "未知",
            "%.0f" % dist if isinstance(dist, (int, float)) else "无",
            "%.0f" % speed if isinstance(speed, (int, float)) else "0")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": PERSONA},
                {"role": "user", "content": ctx},
            ],
            "max_tokens": 80,
            "temperature": 0.9,
        }
        req = urllib.request.Request(
            base + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"api-key": key, "Content-Type": "application/json"})
        text = ""
        # 这个 flash 模型偶尔回空；空就重试一次，别把整条 idle 丢掉
        for _attempt in range(2):
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            text = (((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            text = text.strip('"“”\' \n\t')
            text = re.sub(r"\s+", " ", text)
            text = re.sub(r"^[#*\-\d\.\s]+", "", text)
            if "。" in text or "！" in text or "？" in text:
                parts = re.split(r"(?<=[。！？])", text)
                text = "".join(parts[:2]).strip()
            text = text[:60].strip()
            if len(text) >= 2:
                break
        if len(text) < 2:
            raise RuntimeError("模型返回空台词")
        return text


def snap(prev_hud):
    s = sb_bridge.read(2)
    if not s or s.get("_stale"):
        return None
    e = s.get("enemy") or {}
    return {
        "t": time.time(),
        "x": s["player"]["x"], "y": s["player"]["y"], "z": s["player"]["z"],
        "speed": (s.get("vel") or {}).get("speed", 0),
        "enemy_dist": e.get("dist_cm"),
        "enemy_cls": e.get("class", ""),
        "hp": prev_hud.get("player_hp") if prev_hud else None,
        "shield": list(prev_hud.get("player_shield") or []) if prev_hud else [],
        "enemy_visible": prev_hud.get("enemy_visible") if prev_hud else None,
    }


def pick(k):
    return random.choice(LINES[k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--gain", type=float, default=0.8)
    ap.add_argument("--say", default=None, help="只念一句就退出")
    ap.add_argument("--no-idle", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不合成/播放")
    ap.add_argument("--prewarm", action="store_true",
                    help="把所有固定台词预合成到缓存，直播时零等待")
    ap.add_argument("--no-dynamic", action="store_true",
                    help="关闭 idle/travel 的动态生成，只用固定句")
    ap.add_argument("--test-dynamic", action="store_true",
                    help="现场生成一条伊芙闲聊并播放，测延迟")
    ap.add_argument("--engine", choices=["auto", "mimo", "edge", "sapi"], default="auto",
                    help="auto=MiMo优先→Edge→SAPI；mimo=只用MiMo云TTS；edge=只用Edge；sapi=只用Windows Huihui")
    a = ap.parse_args()

    spk = Speaker(a.voice, a.gain, a.dry_run, a.engine)
    if a.prewarm:
        key = os.environ.get("MIMO_API_KEY") or os.environ.get("XIAOMI_API_KEY")
        if not key:
            raise SystemExit("预合成需要 MIMO_API_KEY（用 start_commentary.py 启动）")
        texts = sorted({t for v in LINES.values() for t in v})
        t0 = time.time()
        ok = 0
        for i, t in enumerate(texts, 1):
            try:
                spk._mimo(t, key)
                ok += 1
                print("[prewarm %d/%d] %s" % (i, len(texts), t), flush=True)
            except Exception as e:
                print("[prewarm FAIL] %s: %s" % (t, e), flush=True)
        print("预合成完成 %d/%d，%.1fs" % (ok, len(texts), time.time() - t0), flush=True)
        return
    if a.say:
        spk.say(a.say, 0)
        if not spk.wait_idle(180):
            print("[TTS] 等待播放超时（180s）", flush=True)
        return

    gen = None if a.no_dynamic else LineGen(spk)
    if a.test_dynamic:
        if gen is None:
            raise SystemExit("--test-dynamic 不能和 --no-dynamic 一起用")
        hud = hud_live.probe_once()
        st = snap(hud) or {}
        t0 = time.time()
        text = gen._gen("idle", st)
        print("[test-dynamic] %.1fs: %s" % (time.time() - t0, text), flush=True)
        spk.say(text, 5)
        spk.wait_idle(180)
        return

    logp = os.path.join(HERE, "runs", "commentary_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(logp), exist_ok=True)
    hud = hud_live.probe_once()
    prev = snap(hud)
    if not prev:
        print("state 不可用（桥没加载？）")
        return
    print("基线", hud_live.fmt(hud), flush=True)

    # 键必须和 emit() 传的 kind 完全一致；之前 shield/gone/low 对不上，
    # 第一次破盾或脱战就会 KeyError 把解说线程打死。
    last = {k: 0 for k in ("enemy", "damage", "shield_break", "low_hp",
                           "enemy_gone", "travel", "idle", "death", "revive")}
    in_combat = False
    travel_since = None
    last_event = time.time()
    next_idle = time.time() + random.uniform(50, 80)
    t0 = time.time()
    hud_t = 0.0
    with open(logp, "a", encoding="utf-8") as f:
        while time.time() - t0 < a.seconds:
            # HUD 1Hz，其余 4Hz
            if time.time() - hud_t >= 1.0:
                h = hud_live.probe_once()
                if h:
                    hud = h
                hud_t = time.time()
            cur = snap(hud)
            if not cur:
                time.sleep(0.25)
                continue
            now = time.time()
            fired = []

            def emit(kind, prio=3, text=None):
                if now - last[kind] < COOLDOWN.get(kind, 10):
                    return
                last[kind] = now
                dyn = False
                if gen and kind in gen.DYNAMIC and not text:
                    line = gen.take(kind)
                    if line:
                        text, dyn = line, True
                    else:
                        gen.request(kind, cur)   # 后台生成，下一次用
                        dyn = "pending"
                fired.append({"t": round(now, 2), "kind": kind, "dynamic": dyn,
                              "hp": cur["hp"], "shield": cur["shield"],
                              "enemy_dist": cur["enemy_dist"]})
                spk.say(text or pick(kind), prio)

            hp, php = cur["hp"], (prev or {}).get("hp")
            sh, psh = cur["shield"], (prev or {}).get("shield") or []
            ed, ped = cur["enemy_dist"], (prev or {}).get("enemy_dist")

            # 阵亡 / 复活
            if hp is not None and hp <= 0.001:
                emit("death", 0)
                in_combat = False
            elif php is not None and php <= 0.001 and hp is not None and hp > 0.5:
                emit("revive", 1)

            # 遇敌
            if ed is not None and ed < 1500 and (ped is None or ped > 2200):
                emit("enemy", 1)
                in_combat = True

            # 受击 / 破盾 / 低血
            if hp is not None and php is not None and hp < php - 0.004:
                emit("damage", 0)
                in_combat = True
            if len(sh) < len(psh) and psh:
                emit("shield_break", 0)
                in_combat = True
            if hp is not None and hp < 0.45 and (php is None or php >= 0.45):
                emit("low_hp", 0)

            # 脱战：敌人远了或血条消失连续 2 拍
            if in_combat and ed is not None and ed > 2500 \
                    and now - last["enemy_gone"] > 8:
                emit("enemy_gone", 2)
                in_combat = False

            # 赶路：速度 > 4m/s 持续 2 秒，且没有近敌
            if cur["speed"] and cur["speed"] > 400 and (ed is None or ed > 2000):
                if travel_since is None:
                    travel_since = now
                elif now - travel_since > 2.0:
                    emit("travel", 4)
                    travel_since = None
            else:
                travel_since = None

            # 闲聊：长时间没有事件，且不在战斗
            if not a.no_idle and now >= next_idle and not in_combat \
                    and (ed is None or ed > 2000):
                if now - last["idle"] > 45:
                    emit("idle", 5)
                    next_idle = now + random.uniform(60, 110)

            if fired:
                last_event = now
                for rec in fired:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
            prev = cur
            time.sleep(0.25)

    print("结束，日志", logp)
    spk.stop()


COOLDOWN = {
    "enemy": 30, "damage": 6, "shield_break": 8, "low_hp": 45,
    "enemy_gone": 15, "travel": 90, "idle": 45, "death": 30, "revive": 10,
}

if __name__ == "__main__":
    main()
