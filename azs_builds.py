# -*- coding: utf-8 -*-
"""流派数据：把客户端的天赋树翻译成「这条流派该带什么」。

数据来源全部在 builds.json 里，本模块只负责三件事：
  1. 读表（缺失时安全降级，不让界面崩）
  2. 把「流派要求的技能」和「玩家实际学的技能」对上，算缺不缺
  3. 把「流派推荐的首饰」和「身上实际穿的」对上，算有没有

装备在游戏内存里的写法有两种：
  amulet:fire_lord:r1                  —— 固定装备（炎魔之王项圈 r1）
  amulet:legendary:corruption:r7       —— 词缀装备（腐蚀术词缀 r7）
所以匹配时要同时认 id 和词缀。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDS_FILE = os.path.join(HERE, "builds.json")
GEAR_FILE = os.path.join(HERE, "gear_names.json")

_cache = {}


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def builds():
    if "builds" not in _cache:
        _cache["builds"] = _load(BUILDS_FILE, {}) or {}
    return _cache["builds"]


def gear_names():
    if "gear" not in _cache:
        _cache["gear"] = _load(GEAR_FILE, {}) or {}
    return _cache["gear"]


def specs_for(cls):
    """某个职业的全部流派；cls 用客户端传的小写英文（warlock）。"""
    c = (builds().get("classes") or {}).get((cls or "").strip().lower())
    return list((c or {}).get("specs") or [])


def class_name(cls):
    c = (builds().get("classes") or {}).get((cls or "").strip().lower())
    return (c or {}).get("name") or (cls or "")


def get_spec(cls, spec_id):
    for s in specs_for(cls):
        if s.get("id") == spec_id:
            return s
    return None


def has_specs(cls):
    return bool(specs_for(cls))


def _fmt_bonus(v):
    """把 gear_ranks 里的 {"dmg":174,"targets":5} 变成「174% 伤害 · 5 目标」。"""
    if not isinstance(v, dict):
        return ""
    label = {
        "dmg": "伤害",
        "cd": "冷却",
        "dmg_pct": "伤害",
        "targets": "目标",
        "cnt": "目标",
        "dur": "持续时间",
        "pct": "%",
        "crit": "暴击",
    }
    out = []
    for k, val in v.items():
        if val in (None, 0, 0.0, ""):
            continue
        name = label.get(k, k)
        out.append("%s %s" % (val, name))
    return " · ".join(out)


def skill_rows(spec, learned):
    """流派要求的技能 → 每行带状态。

    learned: {key: {"rank": n, ...}}  来自 AZSSPELLS
    返回 [{"key","zh","must","why","rank","state","note"}]
    state: ok / low / missing
    """
    learned = learned or {}
    rows = []
    for s in (spec or {}).get("skills") or []:
        key = s.get("key") or ""
        info = learned.get(key)
        # state.json 里存的是 {key: rank}，brain 内部是 {key: {...}}，两种都要认
        if isinstance(info, dict):
            rank = info.get("rank")
            cd = info.get("cd")
            cdmax = info.get("cdmax")
        else:
            rank, cd, cdmax = info, None, None
        if rank:
            # 等级 1 也算学会了，但流派核心一般不满足于 1 级
            state = "ok" if (not s.get("must") or rank >= 2) else "low"
            note = "R%s" % rank
        else:
            state = "missing"
            note = "缺少"
        if state != "missing" and cd and cdmax and cd < cdmax * 0.98:
            note += " · 冷却已减 %.0f%%" % ((1 - cd / cdmax) * 100)
        rows.append({
            "key": key,
            "zh": s.get("zh") or key,
            "must": bool(s.get("must")),
            "why": s.get("why") or "",
            "rank": rank,
            "state": state,
            "note": note,
        })
    return rows


def gear_rows(spec, equipped):
    """流派推荐的首饰 → 每行带「有没有」和「当前加成」。

    equipped: {"amulet": {"id":..,"affix":..,"rank":n}, ...}
    返回 [{"name","kind","must","why","have","rank","bonus","state"}]
    """
    equipped = equipped or {}
    # gear_names.json 里的键带类型前缀（amulet.fire_lord），
    # 内存里只有尾段（fire_lord），比较前统一取尾段。
    have_by_id = {}
    have_by_affix = {}
    for slot, info in equipped.items():
        if not isinstance(info, dict):
            continue
        gid = info.get("id")
        if gid:
            have_by_id[gid.split(".")[-1]] = (slot, info)
        aff = info.get("affix")
        if aff:
            have_by_affix[aff] = (slot, info)

    rows = []
    for g in (spec or {}).get("gear") or []:
        want_id = g.get("id")
        want_affix = g.get("affix")
        have = None
        if want_id:
            have = have_by_id.get(want_id.split(".")[-1])
        if not have and want_affix:
            have = have_by_affix.get(want_affix)
        row = {
            "name": g.get("name") or "",
            "kind": g.get("kind") or "",
            "must": bool(g.get("must")),
            "why": g.get("why") or "",
            "brief": g.get("brief") or "",
            "have": bool(have),
            "rank": (have[1].get("rank") if have else None),
            "slot": (have[0] if have else ""),
            "bonus": "",
            "state": "ok" if have else "missing",
        }
        # 行尾那一行字：有就报等级，没有就报缺，后面统一跟上这件装备干什么用
        head = ""
        if have and have[1].get("rank"):
            head = "r%s" % have[1]["rank"]
        elif not have:
            head = "缺少"
        brief = g.get("brief") or ""
        if brief:
            row["bonus"] = (head + " · " + brief) if head else brief
        else:
            row["bonus"] = head
        rows.append(row)
    return rows


def completion(skill_rows_, gear_rows_):
    """面板顶栏的 3/8 那种完成度：核心项里完成了几项。"""
    core = [r for r in skill_rows_ if r.get("must")] + \
           [r for r in gear_rows_ if r.get("must")]
    if not core:
        core = list(skill_rows_) + list(gear_rows_)
    done = sum(1 for r in core if r.get("state") in ("ok", "low"))
    return done, len(core)


def default_spec(cls):
    ss = specs_for(cls)
    return ss[0]["id"] if ss else None


def label_of_gear(gid):
    return gear_names().get(gid) or gid
