"""技能数值读写核心 —— 既是面板（spellpanel.py）的库，也能当命令行用。

技能表在 AzerothSurvivors.exe 的 .rdata 里：113 条 × 120 字节。
表的位置**不写死**：每次先看缓存，缓存对不上就在 exe 里按「技能 id 序列」
重新扫一遍（游戏一更新，表就会挪位置，扫一下就回来了）。
记录内字段（相对 spell_id）：+4 伤害 / +8 冷却 / +12 施法距离 /
+16 范围半径 / +20 弹道 / +24 持续。

命令行：
  read     [--target mem|file]                 输出 JSON：全部技能当前数值
  apply    --req <json 文件> [--target ...]    批量写入
  restore  [--target ...]                      还原所有改过的字段
  locate                                       重新扫描技能表位置
req 文件格式：{"changes": [[记录文件偏移, "dmg", 5000.0], ...]}
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)


def _find_exe():
    """游戏 exe 在哪。

    正常情况就在程序目录的上一层（AZS-DpsGain → AzerothSurvivors）。
    但有两种情况这个假设会失效：
      · 从 dist\\ 里直接运行 —— 上一层是 AZS-DpsGain，那里没有 exe
      · 程序被单独挪到别的目录
    所以上一层找不到时，回头问一次 azs_paths（它会去看 config.json 里存的游戏目录）。
    """
    p = os.path.join(GAME, "AzerothSurvivors.exe")
    if os.path.isfile(p):
        return p
    try:
        import azs_paths as _P
        g = _P.detect_game_dir()
    except Exception:
        g = None
    if g:
        q = os.path.join(g, "AzerothSurvivors.exe")
        if os.path.isfile(q):
            return q
    return p          # 实在找不到就返回原路径，让后面的 open() 报错时带上路径


EXE = _find_exe()
TABLE_JSON = os.path.join(HERE, "spell_table.json")
ABIL_JSON = os.path.join(HERE, "abilities.json")
LOC_JSON = os.path.join(HERE, "table_loc.json")
BACKUP_FILE = os.path.join(HERE, "spell_backup.json")
BACKUP_MEM = os.path.join(HERE, "spell_backup_mem.json")

IMAGE_BASE = 0x140000000
REC_SIZE = 120
REC_COUNT = 113

# 字段语义经「技能描述文本 ↔ 表内数值」双向核对后确定：
#   +12 是「施法距离」（暴风雪/烈焰风暴等地面技 = 30，自身周围技 = 0，横扫 = 7 近战范围）
#   +16 是「AoE 半径」（冰霜新星 10 / 奥爆 9 / 暴风雪 9 / 烈焰风暴 8 —— 与描述逐条吻合）
#   +24 是「持续时间」（奉献 8、暴风雪 7、烈焰风暴 6；瞬发技为 0）
FIELDS = {"dmg": (4, "f"), "cd": (8, "f"), "range": (12, "f"),
          "radius": (16, "f"), "cnt": (20, "I"), "dur": (24, "f")}
FIELD_CN = {"dmg": "伤害", "cd": "冷却", "range": "施法距离",
            "radius": "范围半径", "dur": "持续", "cnt": "弹道"}

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.VirtualProtectEx.restype = wt.BOOL
k32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                 wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.ReadProcessMemory.restype = wt.BOOL
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = wt.BOOL
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]


class _ME32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)), ("modBaseSize", wt.DWORD),
                ("hModule", wt.HMODULE), ("szModule", ctypes.c_char * 256),
                ("szExePath", ctypes.c_char * 260)]


def module_base(pid):
    """exe 有 ASLR，运行期基址不是 0x140000000，得从模块快照里取。"""
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x00000008 | 0x00000010, pid)
    if not snap or snap == wt.HANDLE(-1).value:
        return None
    me = _ME32()
    me.dwSize = ctypes.sizeof(_ME32)
    base = None
    try:
        if k32.Module32First(snap, ctypes.byref(me)):
            while True:
                if me.szModule.decode("mbcs", "replace").lower() == "azerothsurvivors.exe":
                    base = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value
                    break
                if not k32.Module32Next(snap, ctypes.byref(me)):
                    break
    finally:
        k32.CloseHandle(snap)
    return base


# ---------------------------------------------------------------- 定位技能表
_LOC = None


def _anchors():
    """spell_table.json 里的技能 id 序列 —— 用来当「指纹」找表。"""
    try:
        rows = json.load(open(TABLE_JSON, encoding="utf-8"))
        return [int(r["sid"]) for r in rows]
    except Exception:
        return []


def _fingerprint():
    st = os.stat(EXE)
    return "%d:%d" % (st.st_size, int(st.st_mtime))


def _pe_sections():
    """返回 ([(名字, 文件偏移, 文件大小, RVA, 虚拟大小)], 建议加载基址)"""
    with open(EXE, "rb") as f:
        hdr = f.read(0x1000)
        if len(hdr) < 0x200:
            raise OSError("exe 读不完整")
        e = struct.unpack_from("<I", hdr, 0x3C)[0]
        if hdr[e:e + 4] != b"PE\0\0":
            raise OSError("不是有效的 PE 文件")
        nsec = struct.unpack_from("<H", hdr, e + 6)[0]
        optsz = struct.unpack_from("<H", hdr, e + 20)[0]
        opt = e + 24
        magic = struct.unpack_from("<H", hdr, opt)[0]
        imgbase = (struct.unpack_from("<Q", hdr, opt + 24)[0] if magic == 0x20B
                   else struct.unpack_from("<I", hdr, opt + 28)[0])
        so = opt + optsz
        if so + nsec * 40 <= len(hdr):
            tab = hdr[so:so + nsec * 40]
        else:
            f.seek(so)
            tab = f.read(nsec * 40)
    secs = []
    for i in range(nsec):
        b = i * 40
        name = tab[b:b + 8].rstrip(b"\0").decode("mbcs", "replace")
        vs, va, rs, pr = struct.unpack_from("<IIII", tab, b + 8)
        secs.append((name, pr, rs, va, vs))
    return secs, imgbase


def _fo_to_rva(fo, secs):
    for name, pr, rs, va, vs in secs:
        if pr and pr <= fo < pr + rs:
            return va + (fo - pr), name
    return None, None


def _plausible(buf, p):
    """一条记录看起来像不像技能（用于判断表的末尾，兼容以后加卡）。"""
    if p + REC_SIZE > len(buf):
        return False
    sid = struct.unpack_from("<I", buf, p)[0]
    if not 0 < sid <= 0x40000:
        return False
    dmg, cd, rng, rad = struct.unpack_from("<4f", buf, p + 4)
    cnt = struct.unpack_from("<I", buf, p + 20)[0]
    dur = struct.unpack_from("<f", buf, p + 24)[0]
    for v, hi in ((dmg, 1e5), (cd, 7200.0), (rng, 1000.0),
                  (rad, 1000.0), (dur, 3600.0)):
        if v != v or v < 0 or v > hi:
            return False
    return cnt < 100


def _scan_for_table(buf, sids):
    """在整份 exe 里按前几个技能 id 找表，返回 (文件偏移, 记录长度) 或 None。"""
    if not sids:
        return None
    pat = struct.pack("<I", sids[0])
    n = min(6, len(sids))
    best = None
    pos, L = 0, len(buf)
    while True:
        j = buf.find(pat, pos)
        if j < 0:
            break
        pos = j + 1
        for stride in range(96, 241, 4):
            score = 0
            for k in range(1, n):
                p = j + k * stride
                if p + 4 > L or struct.unpack_from("<I", buf, p)[0] != sids[k]:
                    break
                score += 1
            if score >= 4 and (best is None or score > best[0]):
                best = (score, j, stride)
    return (best[1], best[2]) if best else None


def _count_records(buf, fo, stride, sids):
    """数这张表里有多少条记录。

    为什么不再逐条比对 spell id：游戏更新时会往表中间插新技能，一旦插了一条，
    后面所有 id 就整体错位，旧写法会在错位处直接截断
    （2026-11-01 的更新插入了 spell_id 17364，113 条被砍成 65 条）。

    现在改成「字段是否合理 + id 是否重复」来判断边界，sid 序列只在最前面几条
    用来确认锚点没找错。这样以后再加技能也不会断。
    """
    seen = set()
    n = 0
    while n < 512:
        p = fo + n * stride
        if p + stride > len(buf):
            break
        if not (_plausible(buf, p) and _plausible(buf, p + stride)):
            break
        sid = struct.unpack_from("<I", buf, p)[0]
        if sid in seen:
            break
        # 最前面 4 条仍要求与指纹一致，防止一开始就锚到别处
        if n < 4 and n < len(sids) and sid != sids[n]:
            break
        seen.add(sid)
        n += 1
    return n


def locate(force=False):
    """返回技能表位置 {fo,rva,stride,count}；缓存失效则重新扫描。"""
    global _LOC
    if _LOC is not None and not force:
        return _LOC
    sids = _anchors()
    fp = _fingerprint()

    if not force:
        try:
            loc = json.load(open(LOC_JSON, encoding="utf-8"))
            if loc.get("fp") == fp and loc.get("count", 0) >= 50:
                with open(EXE, "rb") as f:
                    f.seek(loc["fo"])
                    head = f.read(4)
                if len(head) == 4 and (not sids or struct.unpack("<I", head)[0] == sids[0]):
                    _LOC = loc
                    return loc
        except Exception:
            pass

    secs, _base = _pe_sections()
    with open(EXE, "rb") as f:
        buf = f.read()
    hit = _scan_for_table(buf, sids)
    if not hit:
        raise RuntimeError("在 exe 里找不到技能表（游戏版本变化太大，需要重新做一次扫描）")
    fo, stride = hit
    rva, sec = _fo_to_rva(fo, secs)
    if rva is None:
        raise RuntimeError("技能表不在任何 PE 节里，文件可能被压缩了")
    count = _count_records(buf, fo, stride, sids)
    loc = {"fp": fp, "fo": fo, "rva": rva, "stride": stride,
           "count": count, "sec": sec}
    try:
        json.dump(loc, open(LOC_JSON, "w", encoding="utf-8"), indent=1)
    except Exception:
        pass
    _LOC = loc
    return loc


_BASE = None


def table_va(fo=None, base=None):
    loc = locate()
    if fo is None:
        fo = loc["fo"]
    return (base or _BASE or IMAGE_BASE) + loc["rva"] + (fo - loc["fo"])


def game_pid():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AzerothSurvivors.exe", "/NH"],
                             capture_output=True).stdout.decode("gbk", "replace")
    except Exception:
        return None
    for line in out.splitlines():
        p = line.split()
        if len(p) > 1 and p[0].lower() == "azerothsurvivors.exe":
            try:
                return int(p[1])
            except ValueError:
                pass
    return None


def open_proc():
    global _BASE
    pid = game_pid()
    if not pid:
        return None, None
    h = k32.OpenProcess(0x0400 | 0x0010 | 0x0020 | 0x0008, False, pid)
    if not h:
        return None, None
    _BASE = module_base(pid) or IMAGE_BASE
    return pid, h


def mem_read(h, addr, n):
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, addr, buf, n, ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def mem_write(h, addr, data):
    old = wt.DWORD()
    if not k32.VirtualProtectEx(h, addr, len(data), 0x04, ctypes.byref(old)):
        return False
    ok = k32.WriteProcessMemory(h, addr, data, len(data), ctypes.byref(ctypes.c_size_t()))
    k32.VirtualProtectEx(h, addr, len(data), old, ctypes.byref(old))
    return bool(ok)


def _blob_ok(blob, loc, sids=None):
    """读回来的数据是不是真的技能表（按前几条 id 核对）。"""
    sids = sids if sids is not None else _anchors()
    if not blob or not sids:
        return True
    need = min(5, len(sids))
    if len(blob) < need * loc["stride"]:
        return False
    for i in range(need):
        if struct.unpack_from("<I", blob, i * loc["stride"])[0] != sids[i]:
            return False
    return True


def read_blob(target):
    loc = locate()
    if target == "mem":
        pid, h = open_proc()
        if not h:
            return None, "offline"
        n = loc["stride"] * loc["count"]
        blob = mem_read(h, table_va(), n)
        if blob is None:
            return None, "unreadable@%X" % table_va()
        if not _blob_ok(blob, loc):
            # 磁盘上的 exe 跟游戏里跑的不是同一个版本 → 重新定位再读一次
            loc = locate(force=True)
            blob = mem_read(h, table_va(), loc["stride"] * loc["count"])
            if blob is None or not _blob_ok(blob, loc):
                return None, "mismatch（游戏版本与 exe 不一致，先重启游戏或重新扫描）"
        return blob, "mem"
    try:
        with open(EXE, "rb") as f:
            f.seek(loc["fo"])
            return f.read(loc["stride"] * loc["count"]), "file"
    except Exception:
        return None, "file"


def parse_blob(blob, loc=None):
    loc = loc or locate()
    stride = loc["stride"]
    out = []
    for i in range(loc["count"]):
        base = i * stride
        if base + stride > len(blob):
            break
        sid = struct.unpack_from("<I", blob, base)[0]
        vals = {}
        for name, (fo, ty) in FIELDS.items():
            vals[name] = round(struct.unpack_from("<f" if ty == "f" else "<I",
                                                  blob, base + fo)[0], 3)
        out.append((loc["fo"] + base, sid, vals))
    return out


def load_names():
    zh, key = {}, {}
    try:
        for r in json.load(open(TABLE_JSON, encoding="utf-8")):
            key[r["sid"]] = r.get("key") or ""
    except Exception:
        pass
    try:
        for k, v in json.load(open(ABIL_JSON, encoding="utf-8")).items():
            if v.get("name_zh") and v.get("spell_id"):
                zh.setdefault(v["spell_id"], v["name_zh"])
    except Exception:
        pass
    return zh, key


def do_read(target):
    try:
        blob, used = read_blob(target)
    except Exception as e:
        return {"ok": False, "target": target, "reason": "定位技能表失败：%s" % e}
    if blob is None:
        return {"ok": False, "target": target, "reason": "读不到（%s）" % used}
    loc = locate()
    zh, key = load_names()
    recs = []
    for off, sid, vals in parse_blob(blob, loc):
        d = {"off": off, "sid": sid, "key": key.get(sid, ""), "zh": zh.get(sid, "")}
        # 游戏更新会往表里塞新技能，名字表（abilities.json / spell_table.json）
        # 还没收录时先给个带 id 的占位名，至少能改数值、能对上号
        d["named"] = bool(d["zh"] or d["key"])
        if not d["zh"]:
            d["zh"] = "技能 #%d" % sid
        d.update(vals)
        recs.append(d)
    return {"ok": True, "target": used, "pid": game_pid(), "count": len(recs),
            "records": recs}


def _backup_path(target):
    return BACKUP_MEM if target == "mem" else BACKUP_FILE


def _pack(field, value):
    fo, ty = FIELDS[field]
    if ty == "f":
        return struct.pack("<f", float(value))
    return struct.pack("<I", int(round(float(value))))


def do_apply(target, changes):
    """changes: [(off, field, value)]"""
    if not changes:
        return {"ok": True, "applied": 0, "errors": [], "backup": 0, "target": target}
    bp = _backup_path(target)
    saved = json.load(open(bp, encoding="utf-8")) if os.path.exists(bp) else {}
    applied, errors = 0, []

    if target == "mem":
        pid, h = open_proc()
        if not h:
            return {"ok": False, "reason": "游戏没在运行，无法写内存", "applied": 0, "errors": []}
        for off, field, value in changes:
            fo, _ty = FIELDS[field]
            addr = table_va(off + fo)
            old = mem_read(h, addr, 4)
            if old is None:
                errors.append("%s@%X 读不到" % (field, addr))
                continue
            saved.setdefault("%X" % addr, old.hex())
            if mem_write(h, addr, _pack(field, value)):
                applied += 1
            else:
                errors.append("%s@%X 写入失败" % (field, addr))
    else:
        try:
            f = open(EXE, "r+b")
        except PermissionError:
            return {"ok": False, "applied": 0, "errors": [],
                    "reason": "exe 正被游戏占用，先退出游戏再改文件（或切到「内存」模式）"}
        try:
            for off, field, value in changes:
                fo, _ty = FIELDS[field]
                pos = off + fo
                f.seek(pos)
                old = f.read(4)
                saved.setdefault("%X" % pos, old.hex())
                f.seek(pos)
                f.write(_pack(field, value))
                applied += 1
        except Exception as e:
            errors.append(str(e))
        finally:
            f.close()

    json.dump(saved, open(bp, "w", encoding="utf-8"), indent=1)
    return {"ok": not errors, "applied": applied, "errors": errors,
            "backup": len(saved), "target": target}


def do_revert(target, offs):
    """把指定记录恢复成备份里的原始值（只恢复备份里确实有的字段）。"""
    bp = _backup_path(target)
    if not os.path.exists(bp):
        return {"ok": True, "reverted": []}
    saved = json.load(open(bp, encoding="utf-8"))
    changes, desc = [], []
    for off in offs:
        for field, (fo, ty) in FIELDS.items():
            key = ("%X" % table_va(off + fo)) if target == "mem" else ("%X" % (off + fo))
            if key not in saved:
                continue
            raw = bytes.fromhex(saved[key])
            val = struct.unpack("<f" if ty == "f" else "<I", raw)[0]
            changes.append((off, field, round(val, 3)))
            desc.append((field, round(val, 3)))
    if not changes:
        return {"ok": True, "reverted": []}
    res = do_apply(target, changes)
    res["reverted"] = desc
    return res


def do_restore(target):
    bp = _backup_path(target)
    if not os.path.exists(bp):
        return {"ok": True, "restored": 0, "reason": "没有备份"}
    saved = json.load(open(bp, encoding="utf-8"))
    n, errors = 0, []
    if target == "mem":
        pid, h = open_proc()
        if not h:
            return {"ok": False, "reason": "游戏没在运行"}
        for key, hexb in saved.items():
            if mem_write(h, int(key, 16), bytes.fromhex(hexb)):
                n += 1
            else:
                errors.append(key)
    else:
        with open(EXE, "r+b") as f:
            for key, hexb in saved.items():
                f.seek(int(key, 16))
                f.write(bytes.fromhex(hexb))
                n += 1
    # 注意：这里故意不删除备份文件。
    # 备份记录的是「第一次改动之前的原值」，留着它面板里的「原 X」列才有参照；
    # 而且删掉之后如果才发现还原失败，就再也没有原始值可用了。
    return {"ok": not errors, "restored": n, "errors": errors, "target": target}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, target, req = args[0], "auto", None
    i = 1
    while i < len(args):
        if args[i] == "--target" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--req" and i + 1 < len(args):
            req = args[i + 1]
            i += 2
        else:
            i += 1

    if cmd == "locate":
        print(json.dumps(locate(force=True), ensure_ascii=False))
        return

    if target == "auto":
        target = "mem" if game_pid() else "file"

    if cmd == "read":
        print(json.dumps(do_read(target), ensure_ascii=False))
    elif cmd == "apply":
        changes = json.load(open(req, encoding="utf-8")).get("changes", []) if req else []
        print(json.dumps(do_apply(target, changes), ensure_ascii=False))
    elif cmd == "restore":
        print(json.dumps(do_restore(target), ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
