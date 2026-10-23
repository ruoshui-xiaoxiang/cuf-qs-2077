r"""启动解说层：从 E:\密钥文件\mimo.txt 读按量付费 key，走 MiMo 云 TTS。

key 不写进项目；每次启动时读取。
用法：
  py -3.12 start_commentary.py --say "测试"
  py -3.12 start_commentary.py --seconds 300
  $env:MIMO_VOICE="茉莉"; py -3.12 start_commentary.py --seconds 300
"""
import os
import re
import subprocess
import sys

KEY_FILE = r"E:\密钥文件\mimo.txt"


def read_key():
    s = open(KEY_FILE, encoding="utf-8", errors="ignore").read()
    m = re.search(r"按量付费[：:]\s*(\S+)", s)
    if not m:
        raise SystemExit("mimo.txt 里找不到“按量付费” key")
    return m.group(1).strip()


def main():
    key = read_key()
    here = os.path.dirname(os.path.abspath(__file__))
    sample = os.path.join(here, "assets", "voice", "eve_ref.mp3")
    env = os.environ.copy()
    env["MIMO_API_KEY"] = key
    env["MIMO_BASE_URL"] = "https://api.xiaomimimo.com/v1"
    if os.path.exists(sample):
        env.setdefault("MIMO_MODEL", "mimo-v2.5-tts-voiceclone")
        env.setdefault("MIMO_VOICE_SAMPLE", sample)
        mode = "伊芙克隆"
    else:
        env.setdefault("MIMO_MODEL", "mimo-v2.5-tts")
        env.setdefault("MIMO_VOICE", "mimo_default")
        mode = "预置音色（缺少 eve_ref.mp3）"
    cmd = [sys.executable, "commentary.py", "--engine", "mimo"] + sys.argv[1:]
    print("MiMo 按量付费 | %s | model=%s | key=%s...%s" % (
        mode, env["MIMO_MODEL"], key[:4], key[-4:]), flush=True)
    raise SystemExit(subprocess.run(cmd, cwd=here, env=env).returncode)


if __name__ == "__main__":
    main()
