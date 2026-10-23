"""开播资源监视：每 2 秒记录 RAM/CPU/GPU，写 runs/stream_monitor_*.csv。

用法：py -3.12 stream_monitor.py --seconds 1800
"""
import argparse
import csv
import os
import subprocess
import time

import psutil


def gpu():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"], text=True, timeout=3).strip()
        p = [x.strip() for x in out.split(",")]
        return float(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])
    except Exception:
        return (None,) * 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--interval", type=float, default=2)
    a = ap.parse_args()
    os.makedirs("runs", exist_ok=True)
    path = "runs/stream_monitor_%s.csv" % time.strftime("%Y%m%d_%H%M%S")
    t0 = time.time()
    last_print = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "ram_total_gb", "ram_free_gb", "ram_used_pct",
                    "commit_gb", "cpu_pct", "gpu_pct", "vram_used_mb",
                    "vram_total_mb", "gpu_temp_c", "gpu_power_w"])
        while time.time() - t0 < a.seconds:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            cpu = psutil.cpu_percent(interval=None)
            gu, vu, vt, gt, gp = gpu()
            row = [round(time.time() - t0, 1),
                   round(vm.total / 1e9, 1), round(vm.available / 1e9, 1),
                   vm.percent, round(sw.used / 1e9, 1), cpu,
                   gu, vu, vt, gt, gp]
            w.writerow(row)
            f.flush()
            if time.time() - last_print >= 10:
                last_print = time.time()
                print("t=%5.0fs RAM 空闲 %.1fGB/%.1fGB CPU %.0f%% GPU %.0f%% VRAM %.0f/%.0fMB %s°C"
                      % (row[0], row[2], row[1], cpu, gu or 0, vu or 0, vt or 0,
                         ("%.0f" % gt) if gt else "?"), flush=True)
            time.sleep(a.interval)
    print("日志", path)


if __name__ == "__main__":
    main()
