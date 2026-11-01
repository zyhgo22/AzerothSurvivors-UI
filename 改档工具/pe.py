# -*- coding: utf-8 -*-
"""PE 段表解析 —— 找出 .rdata / .text 在文件里的位置。

【为什么路径要动态找】
以前 EXE 是写死的绝对路径（F:/yx/...），换台电脑就崩在 import 阶段：
异常发生在模块顶层，savepanel.py 的 try/except 根本来不及捕获，
连崩溃日志都写不出来，用户看到的现象只是「点了没反应」。
现在改成「用到哪个才找哪个」，并且按 环境变量 → 逐级向上 → config.json 的顺序找。

对外仍然保留 pe.EXE / pe.SEC 这套老写法（靠模块级 __getattr__ 惰性求值），
所以 emulate.py、savedata.py 一个字都不用改。
"""
import io
import json
import os
import struct

IB = 0x140000000               # 游戏 exe 的映像基址
_EXE_NAME = "AzerothSurvivors.exe"


class ExeNotFound(RuntimeError):
    """找不到游戏主程序。消息是直接给用户看的，上层不要再包一层。"""


def _config_game_dir():
    """从工具箱的 config.json 里读用户选过的游戏目录；读不到就当没有。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with io.open(os.path.join(root, "config.json"), "r", encoding="utf-8") as fh:
            return ((json.load(fh) or {}).get("game") or {}).get("dir") or ""
    except Exception:
        return ""


def _candidates():
    """所有可能藏着 AzerothSurvivors.exe 的位置，先命中先赢。"""
    out = []
    env = (os.environ.get("AZS_GAME_DIR") or "").strip()
    if env:
        out.append(os.path.join(env, _EXE_NAME))
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):                 # <游戏根>\AZS工具箱\改档工具\pe.py
        out.append(os.path.join(cur, _EXE_NAME))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    gd = _config_game_dir()
    if gd:
        out.append(os.path.join(gd, _EXE_NAME))
    return out


_EXE_CACHE = None


def exe_path():
    global _EXE_CACHE
    if _EXE_CACHE is None:
        for c in _candidates():
            if c and os.path.isfile(c):
                _EXE_CACHE = os.path.abspath(c)
                break
        else:
            raise ExeNotFound(
                "找不到游戏主程序 %s。\n\n"
                "请打开「AZS 工具箱」→ 06 设置 → 游戏目录，\n"
                "选到 AzerothSurvivors.exe 所在的那个文件夹，然后再试一次。"
                % _EXE_NAME)
    return _EXE_CACHE


_DATA_CACHE = None


def data():
    """整个 exe 的字节。读一次就缓存，改档工具里好几处都要用（文件不小，别反复读）。"""
    global _DATA_CACHE
    if _DATA_CACHE is None:
        with open(exe_path(), "rb") as fh:
            _DATA_CACHE = fh.read()
    return _DATA_CACHE


def sections(exe=None):
    d = data() if exe is None else open(exe, "rb").read()
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    optsz = struct.unpack_from('<H', d, pe + 20)[0]
    base = pe + 24 + optsz
    out = []
    for i in range(nsec):
        o = base + i * 40
        name = d[o:o + 8].rstrip(b'\0').decode()
        vs, va, rs, raw = struct.unpack_from('<IIII', d, o + 8)
        out.append((name, va, vs, raw, rs))
    return out


_SEC_CACHE = None


def sec():
    global _SEC_CACHE
    if _SEC_CACHE is None:
        _SEC_CACHE = sections()
    return _SEC_CACHE


def invalidate():
    """用户换了游戏目录之后调一下，下次访问重新找。"""
    global _EXE_CACHE, _DATA_CACHE, _SEC_CACHE
    _EXE_CACHE = _DATA_CACHE = _SEC_CACHE = None


def rva2fo(rva):
    for name, va, vs, raw, rs in sec():
        if va <= rva < va + max(vs, rs):
            return raw + (rva - va)
    return None


def va2fo(va):
    return rva2fo(va - IB)


def read(rva, n):
    fo = rva2fo(rva)
    if fo is None:
        raise ValueError("RVA 0x%X 不在任何区段里（游戏版本可能变了）" % rva)
    return data()[fo:fo + n]


def u8(rva):
    return read(rva, 1)[0]


def u32(rva):
    return struct.unpack('<I', read(rva, 4))[0]


def i32(rva):
    return struct.unpack('<i', read(rva, 4))[0]


def __getattr__(name):
    """兼容老写法：pe.EXE / pe.SEC 仍然能用，但推迟到真正访问时才读盘。"""
    if name == "EXE":
        return exe_path()
    if name == "SEC":
        return sec()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


if __name__ == '__main__':
    print("exe:", exe_path())
    for s in sec():
        print("%-8s va=0x%08X vsz=0x%08X raw=0x%08X rsz=0x%08X" % s)
