"""批量回归运行器——系统整理用。

按顺序运行全部离线自测套件（mock 驱动，不需要游戏/GPU）。
使用显式端口 20771 避免与真实游戏 20770 冲突。

用法: py tests/_run_regression.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PORT = "20771"

# (脚本名, 是否需要 py -3.12, 额外参数)
SUITES = [
    ("s2_selftest.py", False, [PORT]),
    ("s3_selftest.py", False, [PORT]),
    ("s4_selftest.py", False, [PORT]),
    ("s5_selftest.py", False, []),
    ("s5_lua_selftest.py", False, []),
    ("s6_selftest.py", False, []),
    ("s7_selftest.py", False, []),
    ("s8_selftest.py", False, []),
    ("s9_selftest.py", False, []),
    ("s10_selftest.py", False, []),
    ("s11_selftest.py", False, []),
    ("s12_selftest.py", False, []),
    ("s13_selftest.py", False, []),  # 纯逻辑 27 断言，不需要 GPU
    ("s14_selftest.py", False, []),
    ("s15_selftest.py", False, []),
    ("s16_selftest.py", False, []),
    ("s17_selftest.py", False, []),
    ("s18_selftest.py", False, []),
    ("s19_selftest.py", False, []),
    ("s20_selftest.py", False, []),
    ("s21_selftest.py", False, []),
    ("s22_selftest.py", False, []),
    ("s23_selftest.py", False, []),
    ("s25_selftest.py", False, []),
    ("s28_selftest.py", False, []),
    ("s29_selftest.py", False, []),
    ("s30_selftest.py", False, []),
    ("l2_selftest.py", False, []),
    ("d1_selftest.py", False, []),
]


def run_one(name, use_312, extra_args):
    script = TESTS / name
    if not script.exists():
        return name, "SKIP", "file not found", 0
    cmd = [sys.executable, str(script)] + extra_args
    if use_312:
        cmd = ["py", "-3.12", str(script)] + extra_args
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120,
                           cwd=str(ROOT), encoding='utf-8', errors='replace')
        elapsed = time.monotonic() - t0
        if r.returncode == 0:
            # 尝试从输出提取断言数
            lines = r.stdout.strip().split("\n")
            summary = lines[-1] if lines else ""
            return name, "PASS", summary, elapsed
        else:
            # 取最后几行输出作为错误信息
            tail = "\n".join(r.stdout.strip().split("\n")[-5:])
            if r.stderr.strip():
                tail += "\nSTDERR: " + " | ".join(
                    r.stderr.strip().split("\n")[-3:])
            return name, "FAIL", tail, elapsed
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return name, "TIMEOUT", f">{120}s", elapsed
    except Exception as e:
        return name, "ERROR", str(e), time.monotonic() - t0


def main():
    print(f"=== CUF-QS 全量回归 (port={PORT}) ===")
    print(f"Python: {sys.executable}")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    results = []
    total_pass = 0
    total_fail = 0
    total_skip = 0
    t_start = time.monotonic()

    for name, use_312, extra in SUITES:
        name_short = name.replace(".py", "")
        print(f"  {name_short:20s} ... ", end="", flush=True)
        n, status, detail, elapsed = run_one(name, use_312, extra)
        results.append((n, status, detail, elapsed))
        if status == "PASS":
            total_pass += 1
            print(f"PASS ({elapsed:.1f}s) {detail}")
        elif status == "SKIP":
            total_skip += 1
            print(f"SKIP ({detail})")
        else:
            total_fail += 1
            print(f"{status} ({elapsed:.1f}s)")
            print(f"    {detail[:200]}")

    total_elapsed = time.monotonic() - t_start
    print()
    print(f"=== 回归完成 ===")
    print(f"PASS: {total_pass} / FAIL: {total_fail} / SKIP: {total_skip} / TOTAL: {len(SUITES)}")
    print(f"耗时: {total_elapsed:.1f}s")

    if total_fail > 0:
        print("\n失败项:")
        for n, status, detail, elapsed in results:
            if status not in ("PASS", "SKIP"):
                print(f"  {n}: {status} — {detail[:120]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
