# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 路径、版本、运行环境。

这个文件负责回答三个问题，全项目其它模块都从这里取答案，不要各自算：
  1. 「我是谁」   —— 版本号、程序目录
  2. 「游戏在哪」 —— 游戏根目录（AzerothSurvivors.exe 所在处）
  3. 「数据放哪」 —— 用户配置、日志、缓存

游戏目录的解析顺序（先命中先赢）：
  环境变量 AZS_GAME_DIR  →  config.json 里记着的路径  →  从程序目录往上找 exe

之所以不写死相对路径，是因为打包后程序可能被放到别处（比如 D 盘单独一个文件夹），
这时 `..` 就不一定是游戏目录了。找不到就让用户手动选一次，记进配置。
"""
import os
import sys

APP_NAME = "AZS 工具箱"
APP_ID = "azs-toolkit"
APP_TITLE_EN = "AZS Toolkit"      # viewport 标题只能用 ASCII，中文会让 DPG 2.3 崩溃
VERSION = "1.2.5"

GAME_EXE_NAME = "AzerothSurvivors.exe"

# 用户配置（不是每次发版都会变的东西）
CONFIG_NAME = "config.json"
# 给 brain.py 用的旧格式设置，工具箱保存时会同步过去，保持兼容
SETTINGS_NAME = "settings.json"


def app_dir():
    """程序所在目录（源码运行 = 本文件所在目录；打包后 = exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def app_path(*parts):
    return os.path.join(app_dir(), *parts)


def logs_dir():
    d = app_path("logs")
    os.makedirs(d, exist_ok=True)
    return d


def log_path(name):
    return os.path.join(logs_dir(), name)


def _looks_like_game(path):
    return bool(path) and os.path.isfile(os.path.join(path, GAME_EXE_NAME))


def _probe_upwards(start, levels=4):
    """从 start 开始逐级往上找 AzerothSurvivors.exe。"""
    cur = os.path.abspath(start)
    for _ in range(levels):
        if _looks_like_game(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def detect_game_dir():
    """不带副作用地猜游戏目录，猜不到返回 None。"""
    env = os.environ.get("AZS_GAME_DIR")
    if _looks_like_game(env):
        return os.path.abspath(env)

    here = app_dir()
    # 常见布局：<游戏根>\AZS-DpsGain\azs_toolkit.py  → 上一级就是游戏根
    hit = _probe_upwards(here)
    if hit:
        return hit
    # 打包后：<游戏根>\AZS工具箱\AZS工具箱.exe，同样上一级命中
    return None


def game_dir():
    """最终的游戏目录；实在找不到就返回程序目录的上一级（至少不会崩）。"""
    return detect_game_dir() or os.path.dirname(app_dir())


def exe_path():
    return os.path.join(game_dir(), GAME_EXE_NAME)


def game_running():
    """只看进程名，不依赖 psutil。"""
    try:
        import ctypes
        from ctypes import wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k32.Process32First.restype = wt.BOOL
        k32.Process32Next.restype = wt.BOOL
        k32.CloseHandle.argtypes = [wt.HANDLE]

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                        ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID",
                                                      ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
                        ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]

        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if not snap or snap == wt.HANDLE(-1).value:
            return False
        pe = PE32()
        pe.dwSize = ctypes.sizeof(PE32)
        want = GAME_EXE_NAME.lower().encode("ascii", "ignore")
        ok = k32.Process32First(snap, ctypes.byref(pe))
        while ok:
            if pe.szExeFile.lower() == want:
                k32.CloseHandle(snap)
                return True
            ok = k32.Process32Next(snap, ctypes.byref(pe))
        k32.CloseHandle(snap)
    except Exception:
        pass
    return False


def is_dev_layout():
    """源码目录运行（而不是打包后的绿色版），用来决定要不要显示调试入口。"""
    return not getattr(sys, "frozen", False) and os.path.isfile(app_path("brain.py"))
