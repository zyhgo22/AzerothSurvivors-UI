# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 程序入口。

双击「AZS工具箱.exe」或「启动.cmd」走的就是这里。

启动流程：
   单实例检查 → 收尾上次残留 → 建界面 → 起热键 → 进主循环

任何异常都会写进 logs/toolkit_crash.log 并弹一个中文提示框，
不会出现「点了没反应、什么都没有」的情况。
"""
import ctypes
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

LOG = os.path.join(HERE, "logs", "toolkit_crash.log")


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8", buffering=1) as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _message_box(text, title="AZS 工具箱"):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def _single_instance():
    """同名互斥体，防止连点两次开出两个工具箱抢热键。"""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        handle = k32.CreateMutexW(None, False, "Global\\AZSToolkitSingleton")
        err = ctypes.get_last_error()
        if err == 183:      # ERROR_ALREADY_EXISTS
            return None, handle
        return True, handle
    except Exception:
        return True, None


def _focus_existing():
    """已经开着一个了，就把它的窗口拉到最前，而不是报错了事。"""
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = u.FindWindowW(None, "AZS Toolkit")
        if hwnd:
            u.ShowWindow(hwnd, 9)
            u.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


def main():
    import faulthandler
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        _fh = open(LOG, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_fh)
    except Exception:
        pass

    ok, mutex = _single_instance()
    if ok is None:
        if _focus_existing():
            return 0
        _message_box("工具箱已经在运行了，请看任务栏。")
        return 0

    try:
        import azs_paths as P
        import azs_config as C

        cfg = C.get()
        if not cfg.data.get("first_run_done"):
            # 首次运行：把能自动认出来的东西先填好，少让用户手工配置
            gd = P.detect_game_dir()
            if gd:
                cfg.set_game_dir(gd)
            cfg.data["first_run_done"] = True
            cfg.save()
        cfg.write_control()

        from azs_ui import Toolkit

        tk = Toolkit()
        tk.build()
        tk.run()
        return 0
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _log("启动失败:\n" + tb)
        _message_box("工具箱启动失败：\n\n" + tb[-1400:] +
                     "\n\n完整日志：\n" + LOG)
        return 1
    finally:
        try:
            if mutex:
                ctypes.windll.kernel32.CloseHandle(mutex)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
