"""直播用解说看守：commentary.py 崩了自动拉起，别让直播突然没声音。

用法：py -3.12 commentary_watchdog.py [--seconds 7200]
日志仍由 commentary.py 自己写 runs/commentary_*.jsonl；
本进程只把启动/退出打到 stdout（重定向到 runs/commentary_watch.log）。
"""
import subprocess
import sys
import time

PY = sys.executable
ARGS = [PY, "start_commentary.py"] + sys.argv[1:]

while True:
    t0 = time.time()
    rc = subprocess.call(ARGS)
    dt = time.time() - t0
    print("[watchdog] commentary 退出 rc=%s after %.1fs" % (rc, dt), flush=True)
    # 正常跑满 --seconds 就退出循环，不再拉起
    if rc == 0 and dt > 60:
        print("[watchdog] 正常结束", flush=True)
        break
    time.sleep(3)
