# -*- coding: utf-8 -*-
"""AZS 静态强度 —— 不依赖战斗记录，只看客户端技能表。

为什么需要它：
  1) 新技能卡在没有伤害榜时（刚开一局 / 换了职业还没打怪）以前一律算不出，
     界面上就是「无法计算」。这里用技能表给一个稳定的强度值兜底。
  2) 「打小怪很快、打 BOSS 很慢」是两套评价。群体强 ≠ 单体强，
     某些技能不需要堆多少强度就能把 BOSS 战变轻松，这里单独给单体评分。

口径（全部来自 spell_table.json / abilities.json）：
  单体指数 = 单发伤害 / 冷却
  群体指数 = 单体指数 × 目标数（范围技能再给一点加成）
  打 BOSS 标记：描述里带「优先攻击首领和精英」「对精英和首领」「低于30%」
  数值只用于同一天赋等级下的横向比较，不是绝对 DPS。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(fname, default):
    try:
        with open(os.path.join(HERE, fname), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


_TABLE = []
for _r in _load("spell_table.json", []):
    if _r.get("zh"):
        _TABLE.append(_r)
_ABIL = _load("abilities.json", {})
_ABIL_ZH = {}
for _r in _ABIL.values():
    if _r.get("name_zh"):
        _ABIL_ZH.setdefault(_r["name_zh"].strip(), _r)

BY_NAME = {}
for _r in _TABLE:
    BY_NAME[_r["zh"].strip()] = _r

# 首领特化：描述里出现这些词，说明这技能是专门打大怪的
BOSS_HINTS = (
    ("优先攻击首领和精英", "首领优先"),
    ("对精英和首领", "对精英加伤"),
    ("对生命值低于30%", "斩杀"),
    ("斩杀", "斩杀"),
)

# 少数技能描述里没有写，但在游戏里明确是单体高伤
BOSS_MANUAL = {
    "灵魂之火": "碎片爆发",
    "炎爆术": "首领优先",
    "瞄准射击": "首领优先",
}


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def row_of(name):
    """技能名 → 合并后的数值行（技能表优先，缺的用 abilities 补）"""
    key = (name or "").strip()
    r = dict(BY_NAME.get(key) or {})
    a = _ABIL_ZH.get(key) or {}
    if not r:
        r = {
            "zh": key,
            "dmg": _num(a.get("base_damage")),
            "cd": _num(a.get("cooldown")),
            "cnt": _num(a.get("targets"), 1.0) or 1.0,
            "dur": 0.0,
            "radius": _num(a.get("radius")),
            "desc": a.get("desc") or "",
        }
    else:
        if not r.get("desc"):
            r["desc"] = a.get("desc") or ""
        if not r.get("cnt"):
            r["cnt"] = _num(a.get("targets"), 1.0) or 1.0
    return r


def _cd(r):
    """有效冷却：太小的值会把指数顶飞，给个下限"""
    return max(0.35, _num(r.get("cd"), 3.0))


def solo(name_or_row):
    """单体指数：只算一个目标，打 BOSS 看这个"""
    r = name_or_row if isinstance(name_or_row, dict) else row_of(name_or_row)
    dmg = _num(r.get("dmg"))
    if dmg <= 0:
        return 0.0
    base = dmg / _cd(r)
    # 持续伤害：要挂满才吃得全，BOSS 战挂得满，小怪战挂不满
    return base


def aoe(name_or_row):
    """群体指数：多目标 + 范围"""
    r = name_or_row if isinstance(name_or_row, dict) else row_of(name_or_row)
    s = solo(r)
    if s <= 0:
        return 0.0
    cnt = max(1.0, _num(r.get("cnt"), 1.0))
    rad = _num(r.get("radius"))
    k = cnt
    if rad >= 6:
        k = max(k, 3.0)      # 以自身为中心的环形/地面技能，实际能罩住更多
    elif rad > 0:
        k = max(k, 2.0)
    return s * k


def boss_tags(name):
    """打 BOSS 的特化标记（最多两个）"""
    key = (name or "").strip()
    out = []
    if key in BOSS_MANUAL:
        out.append(BOSS_MANUAL[key])
    desc = (row_of(key).get("desc") or "")
    for word, tag in BOSS_HINTS:
        if word in desc and tag not in out:
            out.append(tag)
    return out[:2]


def summary(name):
    """给徽标/工具页用的一行结论"""
    s = solo(name)
    a = aoe(name)
    tags = boss_tags(name)
    return {
        "solo": round(s, 1),
        "aoe": round(a, 1),
        "tags": tags,
        "boss_ratio": round(s / a, 2) if a > 0 else 0.0,
    }


def baseline(spell_names):
    """以「当前已学技能」为参照，给每个技能一个 0~100 的相对分

    100 = 你身上单体最强的那个技能。没有已学技能时用全表最高值当基准，
    这样新技能卡也能给出一个稳定的分数。
    """
    names = [n for n in (spell_names or []) if (n or "").strip()]
    vals = [(n, solo(n)) for n in names]
    vals = [(n, v) for n, v in vals if v > 0]
    top = max([v for _, v in vals], default=0.0)
    if top <= 0:
        top = max([solo(r) for r in _TABLE], default=1.0)
    return max(1.0, top)


def rel(name, base=None, spell_names=None):
    """相对强度 0~100+（100 = 基准）"""
    b = base if base else baseline(spell_names)
    return round(solo(name) / max(1e-6, b) * 100.0, 0)


def new_spell_share(name, spell_names):
    """新技能卡的「预计占你总伤害多少」（0~1）

    假设你已经学到的技能按静态单体强度贡献伤害，新技能加进来后按比例分。
    没有已学技能时给 0.15 —— 比「算不出」有用，徽标会标成估算。
    """
    v = solo(name)
    if v <= 0:
        return 0.0
    tot = 0.0
    for n in spell_names or []:
        tot += solo(n)
    if tot <= 0:
        # 一个技能都还没学到：拿全表的中位数当"两个普通技能"，
        # 这样强技能和弱技能至少能拉开差距，而不是三张牌都写 15%
        vals = [solo(r) for r in _TABLE if solo(r) > 0]
        if vals:
            vals.sort()
            med = vals[len(vals) // 2]
            return v / (2.0 * med + v)
        return 0.15
    return v / (tot + v)


def is_killproc(name):
    """击杀触发型（救赎这类）：静态强度没法按冷却算，单独排除免得上榜误导"""
    d = row_of(name).get("desc") or ""
    return bool(re.search(r"你击杀的敌人|击杀的敌人有|每击杀", d))


def board(spell_names=None, limit=12, reliable_only=True):
    """BOSS 强度榜：所有技能按单体指数排序

    reliable_only=True 时只保留「客户端描述里明确写了伤害」的技能，
    因为技能表里有些条目（召唤、减益、buff）的 dmg 字段是占位值。
    """
    rows = []
    for r in _TABLE:
        n = r["zh"]
        s = solo(r)
        if s <= 0:
            continue
        d = r.get("desc") or row_of(n).get("desc") or ""
        if reliable_only and not re.search(r"造成|受到", d):
            continue
        if is_killproc(n):
            continue
        rows.append({
            "name": n,
            "solo": round(s, 1),
            "aoe": round(aoe(r), 1),
            "tags": boss_tags(n),
            "spell_id": r.get("sid"),
        })
    rows.sort(key=lambda x: -x["solo"])
    return rows[:limit]
