# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 进程托管。

工具箱要像「大脚插件」那样，自己把三个东西管起来：

    游戏  ──stdout──▶  brain.py  ──state.json──▶  overlay.ps1

为什么要从工具箱启动游戏：游戏的 addon 调试输出只有在启动前设了 RUST_LOG
才会出现。以前靠 run.cmd 设环境变量，现在由这里设，所以**必须从工具箱点开始**，
直接从桌面快捷方式开游戏是拿不到数据的（这一点在界面上要明说）。
"""
import os
import subprocess
import sys
import time

import azs_paths as P

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        import ctypes
        from ctypes import wintypes as wt

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k.CloseHandle.argtypes = [wt.HANDLE]
        _k32 = k
    return _k32


def pid_alive(pid):
    """跨进程查存活：不依赖 psutil，也不怕进程不是自己启动的。"""
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes as wt

        k = _kernel32()
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wt.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259      # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def find_python():
    """找一个能跑脚本的 python(w).exe。

    绿色版优先用包内自带的 runtime/，这样朋友电脑上装没装 Python 都无所谓。
    """
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(P.app_dir(), "runtime", "pythonw.exe"))
        cands.append(os.path.join(P.app_dir(), "runtime", "python.exe"))
    exe = sys.executable or ""
    if exe:
        base = os.path.dirname(exe)
        cands.append(os.path.join(base, "pythonw.exe"))
        cands.append(exe)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for name in ("pythonw", "python"):
        try:
            out = subprocess.run(["where", name], capture_output=True, text=True,
                                 timeout=5, creationflags=CREATE_NO_WINDOW)
            first = (out.stdout or "").strip().splitlines()
            if first and os.path.isfile(first[0]):
                return first[0]
        except Exception:
            pass
    return ""


def find_powershell():
    for name in ("powershell.exe", "pwsh.exe"):
        try:
            out = subprocess.run(["where", name], capture_output=True, text=True,
                                 timeout=5, creationflags=CREATE_NO_WINDOW)
            lines = (out.stdout or "").strip().splitlines()
            if lines:
                return lines[0]
        except Exception:
            pass
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    default = os.path.join(sysroot, "System32", "WindowsPowerShell", "v1.0",
                           "powershell.exe")
    return default if os.path.isfile(default) else "powershell.exe"


class ProcManager:
    """统一管理 brain / overlay / 游戏这三个进程。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.brain_proc = None
        self.overlay_proc = None
        self.game_proc = None
        self._game_log = None
        self._overlay_err = None
        self.last_error = ""

    # ------------------------------------------------------------ 悬浮层报错
    def _overlay_err_tail(self, n=3):
        """把 logs\\overlay.err 最后几行揪出来，直接显示给用户看。"""
        try:
            if self._overlay_err:
                self._overlay_err.flush()
            raw = open(P.log_path("overlay.err"), "rb").read()
            text = raw.decode("gbk", "replace").strip()
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            if not lines:
                return ""
            return " / ".join(lines[-n:])[:160]
        except Exception:
            return ""

    # -------------------------------------------------------------- pid 文件
    def _pid_file(self, which):
        return P.log_path("%s.pid" % which)

    def _read_pid(self, which):
        try:
            with open(self._pid_file(which), "r", encoding="ascii") as fh:
                return int((fh.read() or "0").strip() or 0)
        except Exception:
            return 0

    def _write_pid(self, which, pid):
        try:
            with open(self._pid_file(which), "w", encoding="ascii") as fh:
                fh.write(str(pid or ""))
        except Exception:
            pass

    def _clear_pid(self, which):
        try:
            os.remove(self._pid_file(which))
        except Exception:
            pass

    # -------------------------------------------------------------- 状态
    def brain_pid(self):
        if self.brain_proc and self.brain_proc.poll() is None:
            return self.brain_proc.pid
        pid = self._read_pid("brain")
        return pid if pid_alive(pid) else 0

    def overlay_pid(self):
        if self.overlay_proc and self.overlay_proc.poll() is None:
            return self.overlay_proc.pid
        pid = self._read_pid("overlay")
        return pid if pid_alive(pid) else 0

    def game_pid(self):
        if self.game_proc and self.game_proc.poll() is None:
            return self.game_proc.pid
        return 0

    def status(self):
        return {
            "game": P.game_running(),
            "brain": bool(self.brain_pid()),
            "overlay": bool(self.overlay_pid()),
        }

    # -------------------------------------------------------------- brain
    def start_brain(self):
        if self.brain_pid():
            return True, "已经在跑了"
        py = find_python()
        if not py:
            return False, "找不到 Python 运行时（分发包里应该有 runtime 文件夹）"
        script = P.app_path("brain.py")
        if not os.path.isfile(script):
            return False, "缺少 brain.py"
        # 保证 brain 读到最新设置
        self.cfg.sync_settings()
        try:
            logf = open(P.log_path("brain.out"), "ab")
        except Exception:
            logf = subprocess.DEVNULL
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["AZS_GAME_DIR"] = P.game_dir()
            self.brain_proc = subprocess.Popen(
                [py, script], cwd=P.app_dir(), env=env,
                stdout=logf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                close_fds=True)
            self._write_pid("brain", self.brain_proc.pid)
            time.sleep(0.35)
            if self.brain_proc.poll() is not None:
                return False, "数据大脑启动后立刻退出了，看 logs/brain.err"
            return True, "数据大脑已启动"
        except Exception as e:
            self.last_error = str(e)
            return False, "启动数据大脑失败：%s" % e
        finally:
            try:
                if logf is not subprocess.DEVNULL:
                    logf.close()
            except Exception:
                pass

    def stop_brain(self):
        pid = self.brain_pid()
        if not pid:
            self._clear_pid("brain")
            return True, "本来就没在跑"
        _kill(pid)
        self._clear_pid("brain")
        self.brain_proc = None
        return True, "数据大脑已停止"

    # -------------------------------------------------------------- overlay
    def start_overlay(self):
        if self.overlay_pid():
            return True, "已经在跑了"
        ps = find_powershell()
        script = P.app_path("overlay.ps1")
        if not os.path.isfile(script):
            return False, "缺少 overlay.ps1"
        try:
            env = os.environ.copy()
            # 悬浮层自己的错误以前被直接丢进黑洞，出问题只剩一句「启动失败」。
            # 现在落到 logs\overlay.err，下次能看见 PowerShell 到底报了什么。
            err_path = P.log_path("overlay.err")
            self._overlay_err = open(err_path, "wb")
            self.overlay_proc = subprocess.Popen(
                [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", script],
                cwd=P.app_dir(), env=env,
                stdout=subprocess.DEVNULL, stderr=self._overlay_err,
                # 这里只能给 CREATE_NO_WINDOW，**不能加 DETACHED_PROCESS**：
                # 加了之后 PowerShell 会认为没有控制台宿主，脚本刚跑就 exit 0，
                # 现象就是「点了开始，悬浮层灯一闪，什么也没有」。实测验证过：
                #   CREATE_NO_WINDOW          → 活
                #   DETACHED_PROCESS          → 退出码 0，秒退
                #   两个都给                   → 退出码 0，秒退
                creationflags=CREATE_NO_WINDOW,
                close_fds=True)
            self._write_pid("overlay", self.overlay_proc.pid)
            time.sleep(0.5)
            if self.overlay_proc.poll() is not None:
                tail = self._overlay_err_tail()
                return False, "悬浮层启动失败%s" % ("：" + tail if tail else "，看 logs/overlay.err")
            return True, "悬浮层已启动"
        except Exception as e:
            self.last_error = str(e)
            return False, "启动悬浮层失败：%s" % e

    def stop_overlay(self):
        pid = self.overlay_pid()
        if not pid:
            self._clear_pid("overlay")
            return True, "本来就没在跑"
        _kill(pid)
        self._clear_pid("overlay")
        self.overlay_proc = None
        return True, "悬浮层已停止"

    def restart_overlay(self):
        self.stop_overlay()
        time.sleep(0.3)
        return self.start_overlay()

    # -------------------------------------------------------------- 游戏
    def start_game(self):
        if P.game_running():
            return True, "游戏已经在运行"
        game_dir = self.cfg.game_dir()
        exe = os.path.join(game_dir, P.GAME_EXE_NAME) if game_dir else ""
        if not exe or not os.path.isfile(exe):
            return False, "找不到游戏本体，请在「设置」里指定游戏目录"
        try:
            logf = open(P.log_path("client.log"), "wb")
        except Exception:
            logf = subprocess.DEVNULL
        try:
            env = os.environ.copy()
            env["RUST_LOG"] = "benilla_app::ui_chat::net=debug"
            self.game_proc = subprocess.Popen(
                [exe], cwd=game_dir, env=env,
                stdout=logf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NEW_PROCESS_GROUP)
            self._game_log = logf
            return True, "游戏已启动"
        except Exception as e:
            self.last_error = str(e)
            return False, "启动游戏失败：%s" % e

    def start_all(self):
        """按依赖顺序拉起三件套。返回 (ok, 说明列表)。"""
        msgs = []
        ok_all = True
        o, m = self.start_brain()
        msgs.append(("数据大脑", o, m))
        ok_all &= o
        o, m = self.start_overlay()
        msgs.append(("悬浮层", o, m))
        ok_all &= o
        if self.cfg.data["game"].get("auto_launch", True):
            o, m = self.start_game()
            msgs.append(("游戏", o, m))
            ok_all &= o
        return ok_all, msgs

    def restart_all(self):
        self.stop_overlay()
        self.stop_brain()
        time.sleep(0.4)
        return self.start_all()

    def shutdown(self):
        """工具箱退出时调用：只停后台，不动游戏（用户可能还在玩）。"""
        self.stop_overlay()
        self.stop_brain()


def _kill(pid, wait=2.0):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=6,
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < wait:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False
