# -*- coding: utf-8 -*-
"""RSE1 存档加解密：借游戏自身的 keystream / tag 函数完成（无需知道内部密钥）

【重要】下面 _init() 里的两个地址是「按某个版本的游戏 exe 量出来的」。
游戏一更新，exe 里的函数就会挪位置，这两个地址就指到别的代码中间去，
一调用就是访问违例 —— 整个进程瞬间消失，Python 的 try/except 完全抓不住，
用户看到的现象是「点了没反应 / 软件闪退」。

所以对外提供了一个 probe()：在【子进程】里试调一次，崩了父进程还能活着报错。
启动流程必须先 probe 再干活。
"""
import ctypes, base64, json, os, secrets, subprocess, sys
import emulate, pe

_ready = False
def _init():
    global _ready, KS, H
    if _ready: return
    emulate.map_image()
    KS = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)(0x145b44860)
    H  = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(0x145b44ae0)
    _ready = True


class NotCompatible(RuntimeError):
    """游戏 exe 里的函数换位置了，硬编码地址不再有效。"""


def _probe_child():
    """子进程入口：只做最小一次调用，验证地址还有效。"""
    _init()
    s = ctypes.create_string_buffer(b"\x00" * 16, 16)
    b = ctypes.create_string_buffer(b"\x00" * 16, 16)
    KS(ctypes.cast(s, ctypes.c_void_p), ctypes.cast(b, ctypes.c_void_p), 16)
    sys.stdout.write("PROBE_OK")
    sys.stdout.flush()


def _probe_once(timeout):
    here = os.path.dirname(os.path.abspath(__file__))
    code = ("import sys;sys.path.insert(0,%r);import savecrypt;"
            "savecrypt._probe_child()" % here)
    try:
        r = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, "自检超时（%d 秒）。游戏是不是正在更新？稍后再试。" % timeout
    except Exception as e:
        return False, "自检没跑起来：%s" % e

    if r.returncode == 0 and b"PROBE_OK" in (r.stdout or b""):
        return True, ""

    rc = r.returncode & 0xFFFFFFFF if r.returncode < 0 else r.returncode
    return False, (
        "存档修改器跟不上这次游戏更新（游戏内部函数换了位置）。\n\n"
        "你的存档是好的，别担心 —— 只是这个工具的「解密」环节暂时对不上。\n"
        "等作者适配新版本之后就能继续用。\n\n"
        "自检退出码 0x%X" % rc)


def _probe_cache():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs",
                        "probe.json")


def probe(force=False, timeout=25):
    """确认硬编码地址在当前这版游戏上还能用。返回 (ok, 给用户看的说明)。

    必须开子进程：地址失效时是访问违例，父进程会被一起带走。

    结果按 exe 的「大小 + 修改时间」缓存 —— 游戏没更新就不用每次都等那两秒。
    """
    try:
        st = os.stat(pe.exe_path())
        key = "%d-%d" % (st.st_size, int(st.st_mtime))
    except Exception:
        key = ""

    if not force and key:
        try:
            with open(_probe_cache(), "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("key") == key:
                return bool(c.get("ok")), ("" if c.get("ok") else c.get("why") or "")
        except Exception:
            pass

    ok, why = _probe_once(timeout)
    if key:
        try:
            os.makedirs(os.path.dirname(_probe_cache()), exist_ok=True)
            with open(_probe_cache(), "w", encoding="utf-8") as f:
                json.dump({"key": key, "ok": ok, "why": why}, f, ensure_ascii=False)
        except Exception:
            pass
    return ok, why


def _keystream_xor(salt, data):
    """原地 XOR（加解密同一操作）"""
    _init()
    b = ctypes.create_string_buffer(bytes(data), max(len(data),1))
    s = ctypes.create_string_buffer(bytes(salt), 16)
    KS(ctypes.cast(s, ctypes.c_void_p), ctypes.cast(b, ctypes.c_void_p), len(data))
    return b.raw[:len(data)]

def _tag(salt, ct):
    _init()
    parts = [b'tag', bytes(salt), bytes(ct)]
    keep = [ctypes.create_string_buffer(p, max(len(p),1)) for p in parts]
    spans = (ctypes.c_uint64*6)()
    for i,k in enumerate(keep):
        spans[i*2]   = ctypes.cast(k, ctypes.c_void_p).value
        spans[i*2+1] = len(parts[i])
    out = ctypes.create_string_buffer(32)
    H(ctypes.cast(out, ctypes.c_void_p), ctypes.cast(spans, ctypes.c_void_p))
    return out.raw[:20]

def decode_blob(text):
    raw = text.strip()
    if isinstance(raw, bytes):
        if not raw.startswith(b'RSE1:'): raise ValueError('不是 RSE1 存档')
        raw = raw[5:]
    else:
        if not raw.startswith('RSE1:'): raise ValueError('不是 RSE1 存档')
        raw = raw[5:]
    d = base64.b64decode(raw)
    if len(d) < 36: raise ValueError('存档太短')
    salt, ct, tag = d[:16], d[16:-20], d[-20:]
    if _tag(salt, ct) != tag:
        raise ValueError('tag 校验失败（不是本游戏的存档，或被改坏）')
    return _keystream_xor(salt, ct), salt

def encrypt_blob(plain, salt=None):
    salt = salt or secrets.token_bytes(16)
    ct = _keystream_xor(salt, plain)          # XOR 对称
    tag = _tag(salt, ct)
    return 'RSE1:' + base64.b64encode(salt + ct + tag).decode()

SAVE = os.path.join(os.environ['APPDATA'], 'wenilla-rogue', 'characters.txt')

def load_plain(path=SAVE):
    return decode_blob(open(path,'rb').read())

def save_plain(plain, path=SAVE, salt=None):
    blob = encrypt_blob(plain, salt)
    with open(path, 'wb') as f:
        f.write(blob.encode())
    return len(blob)
