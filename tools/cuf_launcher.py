"""CUF-QS 一站式启动器（监督者）——S29.20。

把「模型驻留服务 + 监控面板 + 实验循环」封装为单入口，带监督重启：
- 组件崩溃自动重启（指数退避，5 分钟内 >5 次崩溃则放弃该组件）；
- 数据实时落盘不受影响：实验归档本就以 rounds.jsonl 逐轮追加，
  面板只读归档、无状态——监督者重启任何组件都不丢数据；
- 重连：游戏断连由闭环 S17 容错等待重连、CufBridge 侧自动重试，
  监督者负责让等待方（闭环进程）始终活着；
- 独立于 IDE 终端生存：本进程跑在自己的控制台里（或打包为
  cuf_launcher.exe 双击运行），不再受终端回收影响。
- 单实例（S29.22）：已有监督者在跑时，重复启动（双击快捷方式）不会
  再开第二个实例，而是直接打开已服务的面板后退出。

命令：
  start [--watch DIR] [--experiment SCRIPT ...] [--no-model] [--no-panel]
          [--model-port 20772] [--panel-port 8090]
      监督运行。--watch 为面板数据目录（默认 logs\\live_7b_v3）；
      --experiment 后跟实验脚本路径与参数（如 tests\\live_7b_loop.py）。
  stop        按 PID 文件停掉全部受管组件
  status      端口/进程健康报告（退出码 0=全绿）

打包（纯标准库，产物小）：
  py -3.12 -m PyInstaller --onefile --name cuf_launcher tools\\cuf_launcher.py

PID 文件：logs\\launcher\\{model_server,panel,experiment}.pid
监督日志：logs\\launcher\\supervisor.log（同时打印到控制台）
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def _find_root():
    """项目根定位：脚本模式用文件位置；EXE（frozen）模式从 exe 所在
    目录逐级向上找 src 标记，兜底当前工作目录。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        for cand in (base, base.parent, Path.cwd(), Path.cwd().parent):
            if (cand / "src" / "w2" / "model_server.py").exists():
                return cand
        return Path.cwd()
    return Path(__file__).resolve().parents[1]


ROOT = _find_root()
LAUNCH_DIR = ROOT / "logs" / "launcher"
DEFAULT_WATCH = ROOT / "logs" / "live_7b_v3"
SINGLETON_MUTEX_NAME = "CUF-QS-Launcher-Singleton"
_SINGLETON_HOLDER = []  # 持有互斥锁句柄直到进程退出
# 子进程解释器：脚本模式用当前解释器；EXE 模式回落到系统 py -3.12
# （torch/transformers 装在真实解释器里，EXE 本身只是纯标准库监督者）
if getattr(sys, "frozen", False):
    PY = [shutil.which("py") or "py", "-3.12"]
else:
    PY = [sys.executable]


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
        with (LAUNCH_DIR / "supervisor.log").open(
                "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def acquire_singleton():
    """单实例互斥（S29.22）。

    Windows：命名互斥锁。首个监督者创建并持有句柄；后续实例创建同名
    锁时得到 ERROR_ALREADY_EXISTS。进程退出（含崩溃/被杀）后系统自动
    释放锁，无死锁残留——比 PID 文件锁可靠。
    非 Windows：退化为 supervisor.pid + 存活探测。
    返回 True 表示本进程是唯一实例，可以继续启动。
    """
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        handle = k32.CreateMutexW(None, False, SINGLETON_MUTEX_NAME)
        if not handle:
            return True  # 创建失败时不拦启动，退化为无锁
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _SINGLETON_HOLDER.append(handle)
        return True
    pid = read_pid("supervisor")
    return not proc_alive(pid)


def pid_file(name):
    return LAUNCH_DIR / f"{name}.pid"


def save_pid(name, pid):
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    pid_file(name).write_text(str(pid), encoding="utf-8")


def read_pid(name):
    try:
        return int(pid_file(name).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def proc_alive(pid):
    """进程存活探测。

    Windows 上绝不能用 os.kill(pid, 0)——CPython 会把它翻译成
    TerminateProcess（可能直接杀死目标），且权限不足时误报死亡。
    正确做法：OpenProcess + GetExitCodeProcess（STILL_ACTIVE=259）。
    """
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid):
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception as e:
        log(f"kill {pid} failed: {e}")


class Component:
    """受管组件：子进程 + 崩溃重启（指数退避）。"""

    MAX_CRASHES = 5
    WINDOW_S = 300.0

    def __init__(self, name, cmd, wait_ready=None, one_shot=False):
        self.name = name
        self.cmd = cmd
        self.wait_ready = wait_ready  # callable -> bool（端口/进程就绪）
        self.one_shot = one_shot  # 一次性实验：正常结束（退出码 0）
        # 视为完成、不再重启；只有非零退出（崩溃）才重启
        self.completed = False
        self.proc = None
        self.crashes = []
        self.spawned_by_us = False
        self.port_down_since = None

    def adopt_or_spawn(self):
        pid = read_pid(self.name)
        if self.wait_ready is not None:
            # 端口型组件：以「端口真在服务」为接管依据——进程残留但
            # 端口已死不是存活（曾把僵尸 py 启动器误判为存活实例）
            if self.wait_ready():
                log(f"[{self.name}] 外部实例已在服务（端口占用），接管")
                self.proc = _ProcHandle(pid)
                return
            if proc_alive(pid):
                log(f"[{self.name}] 残留进程 pid={pid} 未服务端口，清理后重建")
                kill_pid(pid)
                time.sleep(1)
            self._spawn()
            return
        if proc_alive(pid):
            log(f"[{self.name}] 接管存活实例 pid={pid}")
            self.proc = _ProcHandle(pid)
            return
        self._spawn()

    def _spawn(self):
        # 子进程输出重定向到日志文件（排查通道；DEVNULL 会丢失
        # 模型加载进度/错误现场）
        LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
        out = (LAUNCH_DIR / f"{self.name}.out.log").open(
            "a", encoding="utf-8")
        flags = 0
        if sys.platform == "win32":
            # 子进程脱离监督者控制台（DETACHED_PROCESS）——否则关闭
            # 监督者窗口时，CTRL_CLOSE 会连坐杀死全部子进程
            # （S29.21：模型服务曾因此被窗口关闭事件杀死，
            # forrtl error 200）
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(
            self.cmd, cwd=str(ROOT), stdout=out, stderr=out,
            creationflags=flags)
        self.spawned_by_us = True
        save_pid(self.name, self.proc.pid)
        log(f"[{self.name}] 已启动 pid={self.proc.pid} "
            f"cmd={' '.join(str(c) for c in self.cmd)}")

    def check(self):
        if self.completed:
            return True
        port_up = bool(self.wait_ready) and self.wait_ready()
        proc_ok = self.proc is not None and (
            self.proc.poll() is None if hasattr(self.proc, "poll")
            else proc_alive(getattr(self.proc, "pid", None)))
        # 一次性实验的退出语义：正常结束（退出码 0）= 完成，不重启；
        # 此前一律当崩溃重启，会把跑完的实验反复追加轮次。
        if self.one_shot and not proc_ok and self.proc is not None:
            rc = getattr(self.proc, "returncode", None)
            if rc == 0 or (rc is None and not self.spawned_by_us):
                log(f"[{self.name}] 正常结束（exit={rc}），不再监督")
                self.completed = True
                return True
        if port_up:
            self.port_down_since = None
            return True
        # 端口型组件：进程活但端口未服务——自启动的实例可能在正常
        # 加载（模型加载 1-13 分钟），等待；外部接管的实例长时间
        # 不服务则清理重建。
        if self.wait_ready is not None and proc_ok:
            now = time.time()
            if self.port_down_since is None:
                self.port_down_since = now
            if self.spawned_by_us or now - self.port_down_since < 900:
                return True
            pid = getattr(self.proc, "pid", None)
            log(f"[{self.name}] 进程活但端口 >15 分钟未服务，清理重建 "
                f"(pid={pid})")
            kill_pid(pid)
            proc_ok = False
        if proc_ok:
            return True
        now = time.time()
        self.crashes = [t for t in self.crashes
                        if now - t < self.WINDOW_S]
        if len(self.crashes) >= self.MAX_CRASHES:
            log(f"[{self.name}] {self.WINDOW_S:.0f}s 内崩溃 "
                f"{len(self.crashes)} 次，放弃重启（请人工检查）")
            return False
        self.crashes.append(now)
        delay = min(2 ** len(self.crashes), 30)
        log(f"[{self.name}] 退出/失联，{delay}s 后自动重启")
        time.sleep(delay)
        self._spawn()
        return True

    def stop(self):
        pid = getattr(self.proc, "pid", None) or read_pid(self.name)
        if proc_alive(pid):
            log(f"[{self.name}] 停止 pid={pid}")
            kill_pid(pid)
        try:
            pid_file(self.name).unlink()
        except OSError:
            pass


class _ProcHandle:
    """接管外部进程时的轻量句柄（无 Popen 对象）。"""

    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None if proc_alive(self.pid) else 0


def cmd_start(args):
    if not acquire_singleton():
        sup = read_pid("supervisor")
        log(f"监督者已在运行（pid={sup or '?'}），不重复启动第二个实例")
        if not args.no_panel and port_in_use(args.panel_port):
            url = f"http://127.0.0.1:{args.panel_port}/"
            log(f"面板已在服务，直接打开现有面板：{url}")
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
        sys.exit(0)
    comps = []
    if not args.no_model:
        comps.append(Component(
            "model_server",
            PY + [str(ROOT / "src" / "w2" / "model_server.py")],
            wait_ready=lambda: port_in_use(args.model_port)))
    if not args.no_panel:
        comps.append(Component(
            "panel",
            PY + [str(ROOT / "src" / "w3" / "monitor.py"),
                  str(args.watch), "--port", str(args.panel_port)],
            wait_ready=lambda: port_in_use(args.panel_port)))
    if args.experiment:
        comps.append(Component(
            "experiment",
            PY + [str(Path(a)) if a.endswith(".py") else a
                  for a in args.experiment],
            wait_ready=None, one_shot=True))

    log(f"CUF-QS 监督者启动：组件 = {[c.name for c in comps]}")
    save_pid("supervisor", os.getpid())
    for c in comps:
        c.adopt_or_spawn()
    # 面板就绪后自动打开浏览器（S29.21 用户需求：不用手动开）
    if not args.no_panel and not args.no_browser:
        _open_panel_when_ready(args.panel_port)
    try:
        while True:
            for c in comps:
                try:
                    c.check()
                except Exception as e:
                    # 监督循环容错：单组件检查异常不得杀死整个循环
                    log(f"[{c.name}] check 异常：{e}")
            time.sleep(3)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，停止全部组件...")
    finally:
        for c in comps:
            c.stop()
        try:
            pid_file("supervisor").unlink()
        except OSError:
            pass
        log("监督者退出")


def _open_panel_when_ready(port, timeout_s=30):
    import webbrowser
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_in_use(port):
            log(f"面板就绪，自动打开浏览器：{url}")
            try:
                webbrowser.open(url)
            except Exception as e:
                log(f"浏览器打开失败（可手动访问 {url}）：{e}")
            return
        time.sleep(1)
    log(f"面板 {timeout_s}s 内未就绪，稍后可手动访问 {url}")


def cmd_stop(_args):
    # 先停监督者本体（防止它把正要停止的组件重新拉起——停止/复活打架）
    sup = read_pid("supervisor")
    if proc_alive(sup):
        log(f"停止监督者 pid={sup}")
        subprocess.run(["taskkill", "/PID", str(sup), "/F"],
                       capture_output=True, timeout=10)
        time.sleep(1)
    try:
        pid_file("supervisor").unlink()
    except OSError:
        pass
    for name in ("experiment", "panel", "model_server"):
        pid = read_pid(name)
        if proc_alive(pid):
            log(f"停止 {name} pid={pid}")
            kill_pid(pid)
        try:
            pid_file(name).unlink()
        except OSError:
            pass
    log("stop 完成")


def cmd_status(args):
    ok = True
    for name, port in (("model_server", args.model_port),
                       ("panel", args.panel_port)):
        pid = read_pid(name)
        alive = proc_alive(pid)
        serving = port_in_use(port)
        state = "OK" if serving else ("进程活但端口未服务" if alive
                                      else "未运行")
        if not serving:
            ok = False
        log(f"{name:<13} 端口 {port:<6} pid={pid or '-':<7} {state}")
    pid = read_pid("experiment")
    log(f"experiment    pid={pid or '-':<7} "
        f"{'运行中' if proc_alive(pid) else '未运行'}")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description="CUF-QS 一站式启动器（监督者）")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("start", help="监督运行（模型服务+面板+可选实验）")
    ps.add_argument("--watch", default=str(DEFAULT_WATCH),
                    help="面板数据目录（默认 logs\\live_7b_v3）")
    ps.add_argument("--experiment", nargs="+", default=None,
                    help="实验脚本与参数（如 tests\\live_7b_loop.py）")
    ps.add_argument("--no-model", action="store_true")
    ps.add_argument("--no-panel", action="store_true")
    ps.add_argument("--no-browser", action="store_true",
                    help="面板就绪后不自动打开浏览器")
    ps.add_argument("--model-port", type=int, default=20772)
    ps.add_argument("--panel-port", type=int, default=8090)
    ps.set_defaults(fn=cmd_start)

    pp = sub.add_parser("stop", help="停止全部受管组件")
    pp.set_defaults(fn=cmd_stop)

    pt = sub.add_parser("status", help="健康报告")
    pt.add_argument("--model-port", type=int, default=20772)
    pt.add_argument("--panel-port", type=int, default=8090)
    pt.set_defaults(fn=cmd_status)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
