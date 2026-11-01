# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 全局热键。

为什么不用第三方库：要多装一个依赖，发给朋友就多一层出错的可能。
Windows 自带 RegisterHotKey，用 ctypes 调一下就行，零依赖。

实现要点（踩过的坑都记在这）：
  * RegisterHotKey 必须由「拥有消息队列的那个线程」调用，而且 WM_HOTKEY
    只会投递到那个线程的队列。所以全部注册/注销都放在同一个后台线程里做，
    外面只往队列里塞命令 + PostThreadMessage 把线程叫醒。
  * 用 GetMessage 阻塞等待（而不是 50ms 轮询），按键响应是即时的，静态 CPU 占用为 0。
  * 热键被别的软件占用时 RegisterHotKey 返回 0，必须把错误抛给界面，
    否则用户会以为「按了没反应」但完全不知道原因。
"""
import ctypes
import queue
import threading
import time
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
kernel32.GetCurrentThreadId.restype = wt.DWORD

WM_HOTKEY = 0x0312
WM_APP_WAKE = 0x8000 + 1     # 自定义唤醒消息
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 修饰键的界面写法 → Win32 常量
MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN,
}

# 常见按键 → 虚拟键码
NAMED_KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _i in range(1, 25):
    NAMED_KEYS["f%d" % _i] = 0x6F + _i

# 游戏里常用、且不容易和系统/输入法冲突的候选键，界面上做下拉给用户挑
SUGGESTED = ["F6", "F7", "F8", "F9", "F10", "F11", "F12",
             "Ctrl+F8", "Ctrl+F9", "Alt+F8", "Alt+F9", "Shift+F8",
             "Ctrl+Shift+F8", "Ctrl+Alt+F8"]


class HotkeyError(Exception):
    pass


def parse_combo(combo):
    """'Ctrl+Alt+F8' → (mods, vk)。写法不合法就抛 HotkeyError，中文说明原因。"""
    if not combo or not str(combo).strip():
        raise HotkeyError("还没设置按键")
    parts = [p.strip() for p in str(combo).replace("＋", "+").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("按键写法不对")
    mods = 0
    key = None
    for p in parts:
        low = p.lower()
        if low in MODS:
            mods |= MODS[low]
        elif key is None:
            key = low
        else:
            raise HotkeyError("只能有一个主键，比如 Ctrl+F8")
    if key is None:
        raise HotkeyError("缺少主键，比如 Ctrl+F8 里的 F8")

    if key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    else:
        raise HotkeyError("认不出这个按键：%s" % key)
    return mods | MOD_NOREPEAT, vk


def normalise(combo):
    """把用户输入整理成统一写法，用于显示：ctrl+f8 → Ctrl+F8。"""
    mods, vk = parse_combo(combo)
    names = []
    if mods & MOD_CONTROL:
        names.append("Ctrl")
    if mods & MOD_ALT:
        names.append("Alt")
    if mods & MOD_SHIFT:
        names.append("Shift")
    if mods & MOD_WIN:
        names.append("Win")
    if 0x70 <= vk <= 0x87:
        names.append("F%d" % (vk - 0x6F))
    elif 0x30 <= vk <= 0x39:
        names.append(chr(vk))
    elif 0x41 <= vk <= 0x5A:
        names.append(chr(vk))
    else:
        for k, v in NAMED_KEYS.items():
            if v == vk:
                names.append(k.upper() if len(k) > 1 else k)
                break
        else:
            names.append("0x%02X" % vk)
    return "+".join(names)


class HotkeyManager:
    """一个后台线程管所有热键。

    用法：
        hm = HotkeyManager()
        hm.start()
        hm.bind("toggle_overlay", "F8", lambda: print("按了 F8"))
        ...
        hm.stop()
    """

    def __init__(self):
        self._q = queue.Queue()
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self._alive = False
        self._callbacks = {}
        self._registered = {}     # name -> (mods, vk)
        self._next_id = 1
        self._id_by_name = {}
        self._errors = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    def start(self):
        if self._thread and self._thread.is_alive():
            return True
        self._alive = True
        self._thread = threading.Thread(target=self._run, name="azs-hotkey",
                                        daemon=True)
        self._thread.start()
        return self._ready.wait(3.0)

    def stop(self):
        if not self._alive:
            return
        self._alive = False
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None
        self._tid = None

    # ------------------------------------------------------------ 对外接口
    def bind(self, name, combo, callback):
        """注册一个热键。失败抛 HotkeyError（带人话原因）。"""
        mods, vk = parse_combo(combo)      # 先本地校验，写错了不用惊动后台线程
        res = {"ok": False, "err": ""}
        done = threading.Event()
        self._q.put(("bind", name, combo, mods, vk, callback, res, done))
        self._wake()
        if not done.wait(2.0):
            raise HotkeyError("后台线程没响应")
        if not res["ok"]:
            raise HotkeyError(res["err"])
        return True

    def unbind(self, name):
        done = threading.Event()
        self._q.put(("unbind", name, done))
        self._wake()
        done.wait(1.0)

    def unbind_all(self):
        done = threading.Event()
        self._q.put(("unbind_all", done))
        self._wake()
        done.wait(1.5)

    def rebind(self, name, combo, callback):
        try:
            self.unbind(name)
        except Exception:
            pass
        return self.bind(name, combo, callback)

    def registered(self):
        with self._lock:
            return dict(self._registered)

    def last_errors(self):
        with self._lock:
            return list(self._errors)

    # ------------------------------------------------------------ 内部
    def _wake(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_APP_WAKE, 0, 0)

    def _note_error(self, msg):
        with self._lock:
            self._errors.append((time.strftime("%H:%M:%S"), msg))
            del self._errors[:-20]

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        # 先让线程拥有消息队列：PeekMessage 会顺带创建它
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._ready.set()

        while self._alive:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            if msg.message == WM_HOTKEY:
                self._dispatch(int(msg.wParam))
            elif msg.message == WM_APP_WAKE:
                self._drain()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 退出前清掉所有注册，避免热键残留到下一次启动
        self._do_unbind_all()

    def _drain(self):
        while True:
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                return
            kind = cmd[0]
            if kind == "bind":
                _, name, combo, mods, vk, cb, res, done = cmd
                self._do_unbind(name)
                hid = self._next_id
                self._next_id += 1
                if user32.RegisterHotKey(None, hid, mods, vk):
                    self._callbacks[hid] = cb
                    self._id_by_name[name] = hid
                    with self._lock:
                        self._registered[name] = combo
                    res["ok"] = True
                else:
                    err = ctypes.get_last_error()
                    if err == 1409:
                        res["err"] = "这个按键已经被别的软件占用了（换一个试试）"
                    else:
                        res["err"] = "注册失败，系统错误码 %d" % err
                    self._note_error("%s: %s" % (combo, res["err"]))
                done.set()
            elif kind == "unbind":
                _, name, done = cmd
                self._do_unbind(name)
                done.set()
            elif kind == "unbind_all":
                done = cmd[1]
                self._do_unbind_all()
                done.set()

    def _do_unbind(self, name):
        hid = self._id_by_name.pop(name, None)
        if hid is None:
            return
        user32.UnregisterHotKey(None, hid)
        self._callbacks.pop(hid, None)
        with self._lock:
            self._registered.pop(name, None)

    def _do_unbind_all(self):
        for hid in list(self._callbacks):
            user32.UnregisterHotKey(None, hid)
        self._callbacks.clear()
        self._id_by_name.clear()
        with self._lock:
            self._registered.clear()

    def _dispatch(self, hid):
        cb = self._callbacks.get(hid)
        if not cb:
            return
        try:
            cb()
        except Exception:
            pass
