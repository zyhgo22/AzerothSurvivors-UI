# -*- coding: utf-8 -*-
"""存档明文的结构化读写：把 RSE1 解出来的纯文本行当数据源。

明文是「key|值」行文本，本项目用到的行：
  resets|2                     已用重置次数（上限 2，用它减）
  perk|keen:2,veteran:2        特权等级（账号级）
  stat|kills=..,gold=..,..     统计数值
  marks|2|3620                 各职业牌子
"""
import re
import pe

PERK_STRIDE = 0x78
_PERK_CACHE = None
_RD = None          # .rdata 内存镜像
_RDQ = None         # 同一块内存按 qword 看


def _rdata():
    """把 .rdata 读进内存一次（38MB），后面全走内存，避免反复读 147MB 的 exe。"""
    global _RD, _RDQ, _BASE, _OFF
    if _RD is not None:
        return
    from array import array
    sec = [s for s in pe.SEC if s[0] == '.rdata'][0]
    _, va, vs, raw, rs = sec
    n = min(vs, rs) & ~7
    buf = pe.read(va, n)
    _RD = buf
    _BASE = pe.IB + va
    a = array('Q')
    a.frombytes(buf[:n])
    _RDQ = a


def _cstr(ptr, n):
    off = ptr - _BASE
    if off < 0 or off + n > len(_RD):
        return None
    try:
        return _RD[off:off + n].decode('utf-8')
    except Exception:
        return None


def _rec_at(i):
    """i = 记录在 .rdata 内的 qword 下标；不像真的就返回 None。"""
    a = _RDQ
    if i + 15 >= len(a):
        return None
    if not (0x140000000 < a[i] < 0x148000000 and 3 <= a[i + 1] <= 20):
        return None
    pid = _cstr(a[i], a[i + 1])
    if not pid or not re.fullmatch(r'[a-z_]+', pid):
        return None
    name = _cstr(a[i + 2], a[i + 3]) if 3 <= a[i + 3] <= 60 else pid
    # 第 4 段是「每级花费」小数组：长度 1~3、每项 5~1000。技能表那一段是文本，会被这条挡掉。
    costs = []
    if not (0x140000000 < a[i + 6] < 0x148000000 and 1 <= a[i + 7] <= 3):
        return None
    off = a[i + 6] - _BASE
    if not (0 <= off and off + a[i + 7] * 4 <= len(_RD)):
        return None
    costs = list(__import__('struct').unpack_from('<%dI' % a[i + 7], _RD, off))
    if not all(5 <= c <= 1000 for c in costs):
        return None
    unlock = ''
    if 0x140000000 < a[i + 10] < 0x148000000 and 3 <= a[i + 11] <= 40:
        unlock = _cstr(a[i + 10], a[i + 11]) or ''
    return dict(id=pid, name=name, icon=_cstr(a[i + 4], a[i + 5]) or '',
                costs=costs, max_rank=len(costs) or 1, unlock=unlock)


def perk_table():
    """在 .rdata 里按结构认出特权表（游戏更新后偏移会变，不写死地址）。"""
    global _PERK_CACHE
    if _PERK_CACHE is not None:
        return _PERK_CACHE
    _rdata()
    a = _RDQ
    best = []
    i = 0
    n = len(a) - 16
    while i < n:
        if not (0x140000000 < a[i] < 0x148000000 and 3 <= a[i + 1] <= 20):
            i += 1
            continue
        s = i
        while s - 15 >= 0 and _rec_at(s - 15):
            s -= 15
        recs = []
        while _rec_at(s):
            recs.append(_rec_at(s))
            s += 15
        if len(recs) > len(best):
            best = recs
        i = s if s > i else i + 1
    _PERK_CACHE = best
    return best


def perk_max():
    return {p['id']: p['max_rank'] for p in perk_table()}


# --------------------------------------------------------------- 明文行读写
def lines_of(text):
    return text.split('\n')


def find_line(lines, key):
    for i, l in enumerate(lines):
        if l == key or l.startswith(key + '|'):
            return i
    return -1


def get_value(lines, key, default=''):
    i = find_line(lines, key)
    if i < 0:
        return default
    parts = lines[i].split('|', 1)
    return parts[1] if len(parts) > 1 else ''


def set_value(lines, key, value):
    i = find_line(lines, key)
    if i < 0:
        lines.append('%s|%s' % (key, value))
    else:
        lines[i] = '%s|%s' % (key, value)
    return lines


def kv_parse(s):
    """'kills=1,gold=2' -> [('kills','1'), ('gold','2')]，保序"""
    out = []
    for item in s.split(','):
        if not item:
            continue
        k, _, v = item.partition('=')
        out.append((k, v))
    return out


def kv_build(pairs):
    return ','.join('%s=%s' % (k, v) for k, v in pairs)


def perk_parse(s):
    """'keen:2,veteran:2' -> {'keen':2, 'veteran':2}"""
    out = {}
    for item in s.split(','):
        if not item:
            continue
        k, _, v = item.partition(':')
        try:
            out[k] = int(v)
        except ValueError:
            pass
    return out


def perk_build(d):
    return ','.join('%s:%d' % (k, v) for k, v in d.items() if v > 0)


def marks_of(lines):
    """marks|2|3620 -> [(职业id, 数值)]"""
    out = []
    for i, l in enumerate(lines):
        if l.startswith('marks|'):
            p = l.split('|')
            if len(p) == 3:
                out.append((i, p[1], p[2]))
    return out


RESET_TOTAL = 2


def reset_used(lines):
    try:
        return int(get_value(lines, 'resets', '0') or 0)
    except ValueError:
        return 0
