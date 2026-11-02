# -*- coding: utf-8 -*-
"""AZS 收益模型 —— 卡面解析 + 按当前 build 计算边际收益

所有公式都用客户端日志/本地化文案验证过，不是拍脑袋：

  1) 卡面 "当前值 » 之后值" 表示「结算后的面板值」，
     这张卡的增益是**差值**，不是右边那个数。
     例：`+418% » +430% 伤害` 在 D=5.18 时只有 +2.3%，
         当成 +430% 会高估 36 倍。

  2) 伤害是加法池：D = 1 + Σ伤害%
     日志实测 1.360 --(+10%)--> 1.490（1.36+0.13），不是乘算的 1.496，
     所以边际收益 = Δ/D，越堆越弱。

  3) 冷却面板显示的是「冷却加速累计值」R，不是冷却时间本身。
     实测 R = 93 / cd − 100：
       cd=0.816 → R=13.9，卡面正好写 -14%
       cd=0.641 → R=45.1，卡面正好写 -44%
     卡面 -112% 也能解释（R 可以无限大，冷却时间不会为负）。
     所以一张卡的收益 = (100+R_after)/(100+R_before) − 1。

  4) 暴击超过 100% 的部分按 1:1 转成暴击伤害（游戏原文），不会浪费。

  5) DoT 叠层上限由局内等级决定：min(1 + lvl//15, 4)。
     持续 ÷ 冷却 > 上限时，时长/冷却对叠层部分是 0 收益；
     但吞噬魔法（术士宠物天赋）会开第二条通道，让时长重新有价值。

  6) 额外弹道不一定是全额伤害（驱邪术跳跃 75%、裂空之眼跳跃 30%）。

本模块只做纯计算，不读日志、不写文件，方便单独测试。
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


# ---------------------------------------------------------------- 静态数据
SPELL_TABLE = {}
for _x in _load("spell_table.json", []):
    if _x.get("zh"):
        SPELL_TABLE[_x["zh"]] = _x

ABILITIES = _load("abilities.json", {})
ABIL_BY_NAME = {}
for _r in ABILITIES.values():
    for _k in (_r.get("name_zh"), _r.get("name")):
        if _k:
            ABIL_BY_NAME.setdefault(_k.strip(), _r)

# 装备（项链/戒指/饰品）每个等级的实际数值。
# 由 _build_gear_ranks.py 从 client.log 挖出来；挖不到就退回"只看卡面箭头"。
GEAR_RANKS = _load("gear_ranks.json", {})


def abil(name):
    return ABIL_BY_NAME.get((name or "").strip())


def local_damage_pool(name):
    """技能自带的伤害加成（卡面描述里的「+18% 伤害」），返回 0.18 这种"""
    a = abil(name) or {}
    m = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*%\s*伤害", a.get("desc") or "")
    if m:
        try:
            return float(m.group(1)) / 100.0
        except ValueError:
            return 0.0
    return 0.0


# 多目标收益折减：技能卡 0.9（首领战通常有 3~4 个目标），
# 全局卡 0.75（有些法术只打到 1 个目标，第 2 个目标吃不满）
COVERAGE_SKILL = 0.9
COVERAGE_GLOBAL = 0.75


# 冷却：R = CD_K / cd − 100（见文件头第 3 条）
CD_K = 93.0
# 实际冷却 = 基础冷却 × CD_SCALE × cd。CD_SCALE 用 30 秒的「感知」反推：
# cd=0.417 时客户端报 cdmax=17.5 → 30×1.4×0.417=17.5
CD_SCALE = 1.4
# 暴击溢出转暴伤的比率（1.0 = 每 1 点几率 → 1 点暴伤）
CRIT_OVERFLOW = 1.0
# 技能表 dur 与卡面秒数的比例：痛苦诅咒 9→15、吸取生命 3→5，都是 5/3
DOT_DUR_SCALE = 5.0 / 3.0
# 目标平均能活多久（秒）：DoT 挂在怪身上，怪死了剩下的持续时间就白给了。
# 成型的多弹道 build 里小怪常常两三个跳动就没了；这个值越大，
# 「+持续时间」卡越有价值、减 CD 卡的相对价值越低。可在 settings.json 里改。
DOT_LIFETIME = 4.0

# 已经在游戏里看到「在X秒内分Y跳」的技能，直接写死（优先于表的 dur）
TOOLTIP_DOT = {
    "痛苦诅咒": {"dur": 15.0, "ticks": 15},
    "吸取生命": {"dur": 5.0, "ticks": 5},
}

# 叠层型 DoT（同一敌人身上可以叠多层）
DOT_SPELLS = {
    "痛苦诅咒", "腐蚀术", "献祭", "吸取生命", "生命虹吸", "痛苦无常",
    "暗言术：痛", "噬灵瘟疫",
    "烈焰震击", "月火术", "虫群", "毒蛇钉刺", "撕裂", "割裂",
}
# 地面/持续区域（同一块地不会叠层，但可以同时铺两块）
GROUND_SPELLS = {
    "奉献", "暴风雪", "烈焰风暴", "火焰之雨", "飓风", "乱射",
    "神圣之盾", "灼热图腾", "熔岩图腾", "献祭陷阱", "冰霜陷阱",
    "光束泉", "荆棘术",
}

# 额外目标不是全额伤害的技能：技能名 → 额外目标的伤害比例
# 驱邪术「跳跃到另外N个敌人，造成75%」；裂空之眼系列是 30%（卡面自带，会覆盖这里）
EXTRA_TARGET_RATIO = {
    "驱邪术": 0.75,
    "瞄准射击": 0.60,
}

# 职业档案：决定哪些技能按 DoT 结算、哪些天赋默认开启
PROFILES = {
    "paladin": {
        "cn": "圣骑士",
        "dots": set(),
        "grounds": {"奉献", "神圣之盾"},
        "talents": {"consume_magic": False, "echo_cascade": False, "dot_stack_bonus": 0},
    },
    "warlock": {
        "cn": "术士",
        "dots": {"痛苦诅咒", "腐蚀术", "献祭", "吸取生命"},
        "grounds": {"火焰之雨", "地狱烈焰"},
        "talents": {"consume_magic": False, "echo_cascade": False, "dot_stack_bonus": 0},
    },
    "priest": {
        "cn": "牧师",
        "dots": {"暗言术：痛", "噬灵瘟疫"},
        "grounds": {"神圣新星"},
        "talents": {"consume_magic": False, "echo_cascade": False, "dot_stack_bonus": 0},
    },
}
NEUTRAL_PROFILE = {
    "cn": "未知",
    "dots": DOT_SPELLS,
    "grounds": GROUND_SPELLS,
    "talents": {"consume_magic": False, "echo_cascade": False, "dot_stack_bonus": 0},
}


class Ctx(object):
    """一次选卡时的 build 快照"""

    def __init__(self, stats=None, lvl=1, cls="", meter=None, share_fn=None,
                 talents=None, settings=None, gear=None):
        s = stats or {}
        self.stats = s
        self.lvl = int(lvl or 1)
        self.cls = (cls or "").strip().lower()
        self.meter = meter or []
        self._share = share_fn
        self.settings = settings or {}
        # 身上穿的项链/戒指/饰品：{"amulet": ("corruption", 7), ...}
        # 装备卡只能戴一件，换装时要知道"现在那件是几级"
        self.gear = gear or {}

        self.dmg = _f(s.get("dmg"), 1.0, 1.0)
        self.cd = _f(s.get("cd"), 1.0, 0.05)
        self.crit = _f(s.get("crit"), 0.0)
        self.cm = _f(s.get("cm"), 2.0, 1.0)
        self.echo = _f(s.get("echo"), 0.0)
        self.cnt = _f(s.get("cnt"), 0.0)
        self.area = _f(s.get("area"), 1.0)
        self.doth = _f(s.get("doth"), 0.0)
        self.dotd = _f(s.get("dotd"), 0.0)
        self.dots = int(_f(s.get("dots"), 0) or 0)

        prof = PROFILES.get(self.cls, NEUTRAL_PROFILE)
        self.profile = prof
        # 额外目标的伤害比例：默认表可以被 settings.extra_target_ratio 覆盖，
        # 例如 {"驱邪术": 1.0} 表示没点跳跃天赋、额外目标吃全额
        self.extra_ratio = dict(EXTRA_TARGET_RATIO)
        ov = self.settings.get("extra_target_ratio") or {}
        if isinstance(ov, dict):
            for _k, _v in ov.items():
                try:
                    self.extra_ratio[str(_k)] = float(_v)
                except (TypeError, ValueError):
                    pass
        self.talents = dict(prof["talents"])
        if talents:
            self.talents.update(talents)

    # -- 冷却加速累计值
    @property
    def r_now(self):
        return max(-90.0, CD_K / self.cd - 100.0)

    @property
    def stack_cap(self):
        """当前 DoT 叠层上限：等级台阶 + 天赋加成"""
        base = self.dots if self.dots > 0 else min(1 + self.lvl // 15, 4)
        return max(1, int(base + self.talents.get("dot_stack_bonus", 0)))

    def share(self, name):
        if self._share:
            v = self._share(name)
            if v is not None:
                return v
        key = (name or "").strip()
        for r in self.meter:
            if (r.get("name") or "").strip() == key:
                return r.get("share")
        return None

    def dot_share(self):
        if self.dotd > 0.01:
            return min(1.0, self.dotd)
        tot = 0.0
        for r in self.meter:
            if is_dot(r.get("name")):
                tot += r.get("share") or 0.0
        if tot > 0:
            return min(1.0, tot)
        return 0.0


def _f(v, default, lo=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = default
    if lo is not None:
        x = max(lo, x)
    return x


def is_damage_skill(name):
    """这个技能本身是否造成伤害（圣盾术/圣光术这类没有）"""
    a = abil(name) or {}
    try:
        if float(a.get("base_damage") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    d = a.get("desc") or ""
    return bool(re.search(r"造成\s*\d+\s*点|受到\s*\d+\s*点", d))


def is_dot(name):
    return (name or "").strip() in DOT_SPELLS


def is_ground(name):
    return (name or "").strip() in GROUND_SPELLS


# ---------------------------------------------------------------- 暴击
def crit_factor(p, cm):
    """暴击乘区。超过 100% 的部分转暴伤：必暴状态下每点溢出 = +1% 伤害"""
    p = max(0.0, p)
    cm = max(1.0, cm)
    f = 1.0 + min(p, 1.0) * (cm - 1.0)
    if p > 1.0:
        f += (p - 1.0) * CRIT_OVERFLOW
    return f


# ---------------------------------------------------------------- DoT 参数
def dot_params(name, ctx):
    """返回 (持续秒数, 实际冷却秒数, 目标数, 叠层上限, 类型)

    冷却用「基础冷却 × 1.4 × cd」，和客户端 AZSSPELLS 报的 cdmax 一致。
    """
    name = (name or "").strip()
    t = SPELL_TABLE.get(name) or {}
    a = abil(name) or {}

    tt = TOOLTIP_DOT.get(name) or {}
    dur = tt.get("dur")
    if dur is None:
        # 技能描述里常写着「在15秒内分15跳」「持续11秒」，比表里的 dur 可靠
        desc = a.get("desc") or ""
        m = re.search(r"(?:在|持续\s*)\s*(\d+(?:\.\d+)?)\s*秒", desc)
        if m:
            dur = float(m.group(1))
    if dur is None:
        d0 = t.get("dur") or 0.0
        dur = d0 * DOT_DUR_SCALE if d0 else 0.0

    cd0 = a.get("cooldown") or t.get("cd") or 0.0
    cd = float(cd0) * CD_SCALE * ctx.cd

    targets = a.get("targets") or t.get("targets") or t.get("cnt") or 1
    try:
        targets = max(1.0, float(targets))
    except (TypeError, ValueError):
        targets = 1.0

    if name in ctx.profile["dots"] or is_dot(name):
        kind = "dot"
        cap = ctx.stack_cap
    elif name in ctx.profile["grounds"] or is_ground(name):
        kind = "ground"
        cap = 1.5          # 可以同时铺两块，但很难更多
    else:
        kind = "direct"
        cap = 1.0
    return dur, cd, targets, cap, kind


def dot_stacks_now(name, ctx):
    dur, cd, _, cap, kind = dot_params(name, ctx)
    if cd <= 0 or dur <= 0:
        return 1.0, 1.0, cap
    need = dur / cd
    return min(need, cap), need, cap


def consume_magic_ratio(name, ctx, dur_mult):
    """吞噬魔法：按剩余持续时间的 10% 结算 150%，净 5%（跳不完时净 15%）

    DoT 被 CD 不断刷新，吞噬吃掉 10% 后下一跳就补回来，所以稳态收益
    近似与「持续时间长度」成正比。这里只算这条通道的相对增量。
    """
    t = ctx.talents.get("consume_magic")
    if not t:
        return 0.0
    dur, cd, targets, cap, kind = dot_params(name, ctx)
    if kind != "dot" or dur <= 0:
        return 0.0
    interval = _f(ctx.settings.get("consume_interval"), 6.0, 0.5)
    # 每次吞噬：1.5 × 10% × 平均剩余(≈0.9×持续) ；每秒次数 = 1/interval
    per_sec = 1.5 * 0.1 * 0.9 * dur / interval      # 以「单层每秒伤害」为单位
    base = dot_instances(name, ctx, dur, cd, targets, cap, kind)   # 当前挂着几层
    if base <= 0:
        return 0.0
    # 时长 +x% 时，两条通道同比放大；返回吞噬通道在总伤害里的占比
    return per_sec / (base + per_sec)


# ---------------------------------------------------------------- 卡面解析
class Effect(object):
    __slots__ = ("stat", "delta", "before", "after", "label")

    def __init__(self, stat, delta, before=None, after=None, label=""):
        self.stat = stat
        self.delta = delta
        self.before = before
        self.after = after
        self.label = label

    def __repr__(self):
        return "Effect(%s,%+.3g,%s,%s)" % (self.stat, self.delta, self.before, self.after)


# 卡面箭头：中英文客户端分别是 » / -> / →
ARROW_RE = re.compile(
    r"([+\-]?\s*\d+(?:\.\d+)?)\s*%?\s*(?:»|→|->|—>)\s*"
    r"([+\-]?\s*\d+(?:\.\d+)?)\s*%?\s*([^\d+\-]{0,24})"
)


def stat_of_label(label):
    s = label or ""
    if not s:
        return None
    if "层" in s and ("持续" in s or "dot" in s.lower()):
        return "dot_stacks"
    if "跳动" in s or ("持续伤害" in s and ("快" in s or "速" in s)):
        return "dot_haste"
    if "减免" in s or "减伤" in s or "护甲" in s:
        return "armor"
    if "回声" in s:
        return "echo"
    if "再次打击" in s:
        # 「灼热之痛的命中 30% 几率再次打击」是技能自己的，不是全局回声，
        # 用 ctx.echo 去叠会把两者搅在一起
        return "echo_self"
    if "冷却" in s:
        return "cooldown"
    if "两次" in s:
        return "doublecast"
    if "暴击伤害" in s:
        return "crit_damage"
    if "暴击" in s:
        return "crit"
    if "目标" in s:
        return "targets"
    if "范围" in s:
        return "area"
    if "急速" in s:
        return "haste"
    if "持续" in s:
        return "duration"
    if "伤害" in s:
        return "damage"
    if any(k in s for k in ("生命", "减伤", "护甲")):
        return "health"
    if any(k in s for k in ("移速", "移动速度")):
        return "speed"
    if any(k in s for k in ("金币",)):
        return "gold"
    if any(k in s for k in ("经验",)):
        return "xp"
    if any(k in s for k in ("幸运",)):
        return "luck"
    if any(k in s for k in ("拾取",)):
        return "pickup"
    if any(k in s for k in ("治疗", "回复", "恢复", "吸血")):
        return "heal"
    # 「爆发几率 12.3% » 13.6%」这类前缀标签：放在最后，
    # 避免把「几率对额外目标」「几率造成伤害」抢成触发几率
    if "几率" in s or "概率" in s:
        if any(k in s for k in ("闪避", "格挡", "招架", "躲闪", "免伤", "暴击")):
            return "chance_def"
        return "proc_chance"
    return None


# 无箭头时的逐条正则（顺序有意义：具体的写在前面）
PLAIN_PATTERNS = [
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*冷却", "cooldown", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*暴击伤害", "crit_damage", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*暴击(?:几率|率)?(?!伤害)", "crit", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*急速", "haste", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:回声|再次打击)", "echo", 2, 1),
    # 「30% 几率再次打击，造成 100%」：几率写在词后面，上面那条抓不到
    (r"(\d+(?:\.\d+)?)\s*%\s*(?:的)?几率[^\n]{0,6}?再次打击", "echo_self", 1, None),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:持续伤害|DoT)\S{0,4}(?:跳动)?(?:速度)?(?:加快|提高)", "dot_haste", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*伤害减免", "health", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*伤害", "damage", 2, 1),
    (r"伤害\s*([+\-]?)\s*(\d+(?:\.\d+)?)\s*%", "damage", 2, 1),
    (r"([+\-]?)\s*(\d+)\s*个?\s*目标", "targets", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:效果)?范围", "area", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:持续)?时间", "duration", 2, 1),
    (r"(\d+(?:\.\d+)?)\s*%\s*(?:的)?几率[^。\n]{0,8}?两次", "doublecast", 1, None),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*最大生命值?", "health", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*移动速度", "speed", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*拾取范围", "pickup", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*金币", "gold", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*经验值", "xp", 2, 1),
    (r"([+\-]?)\s*(\d+(?:\.\d+)?)\s*%\s*幸运", "luck", 2, 1),
]


def parse_line(line):
    """解析一行卡面，返回 [Effect]。有 » 就用差值，没有就按正则取显示值。"""
    line = (line or "").strip()
    if not line:
        return []
    m = ARROW_RE.search(line)
    if m:
        try:
            before = float(m.group(1))
            after = float(m.group(2))
        except ValueError:
            return []
        stat = stat_of_label(m.group(3))
        if not stat:
            # 「爆发几率 12.3% » 13.6%」：标签写在数字前面，
            # 取箭头左侧最近一段「不含数字/百分号/正负号」的文字再看一次
            head = line[max(0, m.start() - 14):m.start()]
            head = re.split(r"[\d%+\-]", head)[-1].strip()
            stat = stat_of_label(head)
        if not stat:
            return []
        if stat == "cooldown":
            b, a = abs(before), abs(after)
        else:
            b, a = before, after
        return [Effect(stat, a - b, b, a, m.group(3).strip())]

    # 「你的暴击会灼烧并使目标流血，在4秒内额外造成80%」
    mb = re.search(r"暴击[^\n]{0,14}?(?:灼烧|流血|额外造成)[^\n]{0,8}?(\d+(?:\.\d+)?)\s*%", line)
    if mb:
        return [Effect("crit_burn", float(mb.group(1)), None, None, line)]

    # 「你的持续伤害法术跳动速度加快36%」——数字在词后面，正则顺序对不上，
    # 单独先抓一遍
    if "持续伤害" in line and ("跳动" in line or "加快" in line or "急速" in line):
        mm = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
        if mm:
            return [Effect("dot_haste", float(mm.group(1)), None, None, line)]
    # 「持续伤害层数 +1」这类
    if "层" in line and ("持续伤害" in line or "DoT" in line):
        mm = re.search(r"([+\-]?\d+)\s*层", line)
        if mm:
            return [Effect("dot_stacks", float(mm.group(1)), None, None, line)]

    out = []
    seen = set()
    for pat, stat, gi, si in PLAIN_PATTERNS:
        for mm in re.finditer(pat, line):
            try:
                val = float(mm.group(gi))
            except (ValueError, IndexError):
                continue
            if si is not None and (mm.group(si) or "") == "-":
                val = -val
            key = (stat, round(val, 4))
            if key in seen:
                continue
            seen.add(key)
            out.append(Effect(stat, val, None, None, line))
    return out


def parse_card(text):
    """把整张卡的文本拆成 Effect 列表"""
    out = []
    for line in (text or "").split("\n"):
        out.extend(parse_line(line))
    return out


# ---------------------------------------------------------------- 收益计算
NON_DPS = {"health", "speed", "gold", "xp", "luck", "pickup", "heal", "armor",
           "chance_def"}
NON_DPS_CN = {"health": "生命", "speed": "移速", "gold": "金币", "xp": "经验",
              "luck": "幸运", "pickup": "拾取", "heal": "回复", "armor": "减伤",
              "chance_def": "防御几率"}


def _target_model(name, ctx, n):
    """+n 个目标：把「全额目标」和「衰减目标」分开算有效目标数"""
    dur, cd, base_t, cap, kind = dot_params(name, ctx)
    t = max(1.0, base_t + ctx.cnt)
    ratio = ctx.extra_ratio.get((name or "").strip(), 1.0)
    r = max(0.05, ratio)
    eff = 1.0 + r * (t - 1.0)
    eff2 = 1.0 + r * (t + n - 1.0)
    return eff2 / eff, t, r


def eval_effects(effects, ctx, subj, share):
    """返回 (ratio, notes, confident, dmg_related)

    ratio 是「作用对象」的倍率；若 subj 为空则作用于总伤害。
    """
    ratio = 1.0
    notes = []
    conf = True
    dmg_related = False
    local_pool = None      # 技能自带的伤害加成池（来自箭头的左值）

    for e in effects:
        st, d = e.stat, e.delta
        if st == "damage":
            dmg_related = True
            if e.before is not None and subj:
                b, a = e.before, e.after
                ratio *= (100.0 + a) / (100.0 + b)
                local_pool = b / 100.0          # 只存「加成部分」，池子 = 1 + 它
                notes.append("伤害+%g/%g" % (a - b, 100 + b))
            elif subj:
                # 无箭头时卡面只给一个「+X% 伤害」，它是加进伤害池的。
                # 池子至少要算上已经堆起来的全局伤害（ctx.dmg），
                # 否则「+152%」会被当成 ×2.52 —— 实际在 247% 的池里只有 ×1.62。
                # （2026-11-01 用户截图：瘟疫坠饰被算成 +340%，虚高约 15 倍）
                pool = max(ctx.dmg, 1.0 + (local_pool if local_pool is not None
                                           else local_damage_pool(subj)))
                ratio *= 1.0 + d / 100.0 / pool
                if pool > 1.001:
                    notes.append("+%g%%伤害/池%.0f%%" % (d, (pool - 1.0) * 100))
                else:
                    notes.append("+%g%%伤害" % d)
            else:
                ratio *= 1.0 + d / 100.0 / ctx.dmg
                notes.append("伤害+%g/%g" % (d, round(ctx.dmg * 100)))
        elif st == "cooldown":
            dmg_related = True
            if subj and (is_dot(subj) or subj in ctx.profile["dots"]
                         or is_ground(subj) or subj in ctx.profile["grounds"]):
                r, note, c = _dot_cd_gain(subj, ctx, e)
                ratio *= r
                notes.append(note)
                conf = conf and c
            elif e.before is not None:
                b, a = e.before, e.after
                ratio *= (100.0 + a) / (100.0 + b)
                notes.append("冷却%.0f→%.0f" % (b, a))
            elif subj:
                # 技能卡「-5% 冷却时间」：该技能冷却时间 × (1-5%)
                if 1.0 + d / 100.0 <= 0.05:
                    notes.append("冷却已接近0")
                else:
                    ratio *= 1.0 / (1.0 + d / 100.0)
                    notes.append("冷却%+.0f%%" % d)
                    conf = False
            else:
                b, a = ctx.r_now, ctx.r_now - d
                ratio *= (100.0 + a) / (100.0 + b)
                notes.append("冷却%.0f→%.0f" % (b, a))
        elif st == "crit":
            dmg_related = True
            f0 = crit_factor(ctx.crit, ctx.cm)
            f1 = crit_factor(ctx.crit + d / 100.0, ctx.cm)
            ratio *= f1 / f0
            notes.append("暴击%.0f%%→%.0f%%" % (ctx.crit * 100, (ctx.crit + d / 100.0) * 100))
            if ctx.crit >= 1.0:
                conf = False   # 已溢出，靠转暴伤，数值依赖转换率
        elif st == "echo":
            dmg_related = True
            d = e.delta / 100.0
            if ctx.talents.get("echo_cascade"):
                def _g(x):
                    return 1.0 + x + x * x + x ** 3
                ratio *= _g(ctx.echo + d) / max(1e-6, _g(ctx.echo))
                notes.append("回声%.0f%%→%.0f%%(连锁)" % (ctx.echo * 100, (ctx.echo + d) * 100))
                conf = False
            else:
                ratio *= (1.0 + ctx.echo + d) / max(1e-6, 1.0 + ctx.echo)
                notes.append("回声%.0f%%→%.0f%%" % (ctx.echo * 100, (ctx.echo + d) * 100))
        elif st == "echo_self":
            # 技能专属的「几率再次打击」：独立乘区，期望伤害直接 ×(1+p)
            dmg_related = True
            ratio *= 1.0 + e.delta / 100.0
            notes.append("再打击%g%%" % e.delta)
            conf = False
        elif st == "crit_burn":
            dmg_related = True
            ratio *= 1.0 + min(ctx.crit, 1.0) * d / 100.0
            notes.append("暴击灼烧+%g%%×%.0f%%" % (d, min(ctx.crit, 1.0) * 100))
            conf = False
        elif st == "crit_damage":
            dmg_related = True
            f0 = crit_factor(ctx.crit, ctx.cm)
            f1 = crit_factor(ctx.crit, ctx.cm + d / 100.0)
            ratio *= f1 / f0
            notes.append("暴伤%.0f%%→%.0f%%" % (ctx.cm * 100, (ctx.cm + d / 100.0) * 100))
        elif st == "proc_chance":
            # 触发几率线性作用于该技能自己的伤害：期望值 ∝ 几率
            dmg_related = True
            if e.before and e.before > 0 and e.after is not None:
                ratio *= e.after / e.before
                notes.append("触发%.1f%%→%.1f%%" % (e.before, e.after))
            elif e.before is not None:
                ratio *= (1.0 + e.after / 100.0) / max(1e-6, 1.0 + e.before / 100.0)
                notes.append("触发%.1f%%→%.1f%%" % (e.before, e.after))
            else:
                ratio *= 1.0 + d / 100.0
                notes.append("触发几率+%g%%" % d)
            conf = False   # 有「每秒最多 N 次」上限时，高密度战斗会被吃掉一部分
        elif st == "duration":
            if subj and (is_dot(subj) or subj in ctx.profile["dots"]
                         or is_ground(subj) or subj in ctx.profile["grounds"]):
                dmg_related = True
                r, note, c = _dot_duration_gain(subj, ctx, d)
                ratio *= r
                notes.append(note)
                conf = conf and c
            elif subj:
                if is_damage_skill(subj):
                    # 有伤害但拿不到时长（引导类）：不给假收益
                    notes.append("%s持续+%g%%(无时长数据)" % (subj, d))
                    conf = False
                else:
                    # 免疫/护盾/加速类持续时间：不影响伤害
                    notes.append("%s持续+%g%%(非伤害)" % (subj, d))
            else:
                # 全局持续时间：只对 DoT 部分有效，且默认已触顶
                dmg_related = True
                ds = ctx.dot_share()
                r = 1.0 + ds * (d / 100.0) * 0.3
                ratio *= r
                notes.append("持续+%g%%(DoT %.0f%%)" % (d, ds * 100))
                conf = False
        elif st == "dot_haste":
            dmg_related = True
            ds = ctx.dot_share()
            if subj:
                ds = share if share is not None else ds
            ratio *= 1.0 + ds * d / 100.0
            notes.append("跳速+%g%%" % d)
            conf = False
        elif st == "dot_stacks":
            dmg_related = True
            ratio *= _stack_gain(ctx, d)
            notes.append("层数+%g" % d)
            conf = False
        elif st == "targets":
            dmg_related = True
            conf = False
            if subj:
                r, t, rr = _target_model(subj, ctx, d)
                ratio *= 1.0 + (r - 1.0) * COVERAGE_SKILL
                if rr < 0.99:
                    notes.append("目标%g→%g(%.0f%%)" % (t, t + d, rr * 100))
                else:
                    notes.append("目标%g→%g" % (t, t + d))
            else:
                g = _global_target_gain(ctx, d)
                ratio *= 1.0 + (g - 1.0) * COVERAGE_GLOBAL
                notes.append("全局目标+%g" % d)
        elif st == "doublecast":
            dmg_related = True
            p = d / 100.0
            ratio *= 1.0 + p
            notes.append("双次%.0f%%" % d)
            conf = False
        elif st == "haste":
            dmg_related = True
            ratio *= 1.0 + d / 100.0
            notes.append("急速+%g%%" % d)
        elif st == "area":
            dmg_related = True
            ratio *= 1.0 + d / 100.0 * 0.5
            notes.append("范围+%g%%" % d)
            conf = False
        elif st in NON_DPS:
            notes.append("%s+%g%%" % (NON_DPS_CN.get(st, st), d))
        else:
            notes.append("?")

    return ratio, notes, conf, dmg_related


def dot_is_spread(name, targets, kind):
    """这个 DoT 是「每次挑新目标」还是「同一目标叠层」

    游戏原文（痛苦诅咒 / 吸取生命）写的是「折磨…生命值最高的N个敌人（尚未受折磨的）」，
    也就是每次施放都挑还没被点过的目标 —— 减 CD 的价值在"更快铺开覆盖"，
    而不是"在一个目标身上叠更高"。腐蚀术这类单目标 DoT 才是叠层型。

    判据只用技能描述里的「尚未受折磨」，不用表里的 targets：
    spell_table 是旧版本抓的，targets 字段不可靠（腐蚀术就误报成 2）。
    """
    if kind == "ground":
        return True
    desc = (abil((name or "").strip()) or {}).get("desc") or ""
    return "未受折磨" in desc or "尚未" in desc


def dot_demand(name, ctx, dur, cd, targets, kind):
    """不受"能容纳多少"限制时，稳态想挂多少层 = targets × min(持续, 怪寿命) / 冷却

    叠层型 DoT 的每次施放都打在同一个目标上，所以按 1 个目标算；
    点名型才乘卡面/描述里的 targets。
    """
    if dur <= 0 or cd <= 0:
        return 0.0
    spread = dot_is_spread(name, targets, kind)
    t = max(1.0, targets) if spread else 1.0
    life = _f(ctx.settings.get("dot_lifetime"), DOT_LIFETIME, 0.2)
    return t * min(dur, life) / cd


def dot_instances(name, ctx, dur, cd, targets, cap, kind):
    """稳态下同时挂着多少层 DoT —— 这就是 DoT 的 DPS 权重

        每次施放挂 targets 层，每层只活 min(持续时间, 目标存活时间) 秒
        （怪活得比持续时间短时，长出来的那截没跳完就没了）
        施放频率 1/冷却  ⇒  同时挂着 = targets × min(dur, life) / cd

    再按"能同时容纳多少层"封顶：
        点名型：每个目标都能挂满 cap 层 → targets × cap
        叠层型：只有 1 个目标 → cap
    """
    if kind == "direct":
        return 0.0
    want = dot_demand(name, ctx, dur, cd, targets, kind)
    if want <= 0:
        return 0.0
    if kind == "ground":
        room = max(1.0, targets) * 1.5
    elif dot_is_spread(name, targets, kind):
        room = max(1.0, targets) * cap
    else:
        room = cap
    return min(want, room)


def _dot_cd_gain(name, ctx, e):
    """DoT 的冷却收益：常态是「更快铺开」，只有叠层型叠满了才会归零"""
    dur, cd, targets, cap, kind = dot_params(name, ctx)
    if cd <= 0 or dur <= 0:
        return 1.0, "冷却无数据", False
    i0 = dot_instances(name, ctx, dur, cd, targets, cap, kind)
    if i0 <= 0:
        return 1.0, "DoT 无伤害", False
    if e.before is not None:
        b, a = e.before, e.after
        factor = (100.0 + a) / (100.0 + b)     # 加速池 → DPS 倍率
        cd_note = "冷却%.0f→%.0f" % (b, a)
    else:
        if 1.0 + e.delta / 100.0 <= 0.05:
            return 1.0, "冷却已接近0", False
        factor = 1.0 / (1.0 + e.delta / 100.0)  # 技能卡 → 冷却时间乘算
        cd_note = "冷却%+.0f%%" % e.delta
    i1 = dot_instances(name, ctx, dur, cd / max(1e-6, factor), targets, cap, kind)
    ratio = i1 / i0
    unit = ("覆盖%.1f→%.1f个" if dot_is_spread(name, targets, kind) else "层%.1f→%.1f") % (i0, i1)
    if ratio <= 1.001:
        return ratio, "DoT " + unit + " 已满 " + cd_note, False
    return ratio, "DoT " + unit + " " + cd_note, False



def _dot_duration_gain(name, ctx, d):
    """DoT 的持续时间收益：怪活不到那么久时，长出来的那截是白给的"""
    dur, cd, targets, cap, kind = dot_params(name, ctx)
    if dur <= 0 or cd <= 0:
        return 1.0, "持续无数据", False
    i0 = dot_instances(name, ctx, dur, cd, targets, cap, kind)
    i1 = dot_instances(name, ctx, dur * (1.0 + d / 100.0), cd, targets, cap, kind)
    spread_ratio = (i1 / i0) if i0 > 0 else 1.0

    if kind == "ground":
        # 地面持续：刷新为主，按半额估
        return spread_ratio, "地面持续+%g%%" % d, False

    c_share = consume_magic_ratio(name, ctx, 1.0 + d / 100.0)
    if c_share > 0:
        # 吞噬通道与持续时间同比，覆盖通道只看有没有被"怪寿命"截断
        ratio = (1.0 - c_share) * spread_ratio + c_share * (1.0 + d / 100.0)
        return ratio, "持续+%g%%(吞噬%.0f%%)" % (d, c_share * 100), False

    if spread_ratio <= 1.001:
        life = _f(ctx.settings.get("dot_lifetime"), DOT_LIFETIME, 0.2)
        return 1.0, "持续+%g%% 怪活不过%.0fs" % (d, min(dur, life)), False
    unit = ("覆盖%.1f→%.1f个" if dot_is_spread(name, targets, kind) else "层%.1f→%.1f") % (i0, i1)
    return spread_ratio, "持续+%g%% " % d + unit, spread_ratio > 1.001


def _stack_gain(ctx, d):
    """层数上限 +d：只有叠层型 DoT 真的叠满时才有收益"""
    cap = ctx.stack_cap
    # 找当前最吃紧的叠层型 DoT，用它代表 DoT 部分
    want = 0.0
    for r in ctx.meter:
        n = (r.get("name") or "").strip()
        if is_dot(n) or n in ctx.profile["dots"]:
            dur, cd, targets, _, kind = dot_params(n, ctx)
            if dot_is_spread(n, targets, kind):
                continue          # 点名型不吃叠层上限
            want = max(want, dot_demand(n, ctx, dur, cd, targets, kind))
    if want <= cap:
        return 1.0 + 0.0
    new_cap = cap + d
    return min(want, new_cap) / max(1e-6, min(want, cap))


def _global_target_gain(ctx, n):
    """全局 +n 目标：按每个技能的占比与有效目标数加权"""
    num = 0.0
    den = 0.0
    for r in ctx.meter:
        share = r.get("share") or 0.0
        if share <= 0:
            continue
        name = (r.get("name") or "").strip()
        dur, cd, base_t, cap, kind = dot_params(name, ctx)
        t = max(1.0, base_t + ctx.cnt)
        rr = ctx.extra_ratio.get(name, 1.0)
        eff = 1.0 + rr * (t - 1.0)
        eff2 = 1.0 + rr * (t + n - 1.0)
        num += share * (eff2 / eff - 1.0)
        den += share
    if den <= 0:
        return 1.0 + 0.04 * n
    return 1.0 + num / den


# ---------------------------------------------------------------- 卡牌整体
BOUNCE_RE = re.compile(r"跳跃到另外\s*(\d+(?:\.\d+)?|[一两二三四五六七八九十])\s*个敌人身上"
                       r"[，,]?\s*造成\s*(\d+(?:\.\d+)?)\s*%")
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def to_num(s):
    s = (s or "").strip()
    if s in CN_NUM:
        return float(CN_NUM[s])
    try:
        return float(s)
    except ValueError:
        return 1.0


def bounce_targets(text):
    """卡面里的「跳跃到另外 N 个敌人，造成 X%」→ 有效目标增量"""
    out = []
    for m in BOUNCE_RE.finditer(text or ""):
        out.append((to_num(m.group(1)), float(m.group(2)) / 100.0))
    return out


def subject_skill(name, text, abil_lookup):
    """卡牌作用的技能名：卡名「裂空之眼: 死亡缠绕」或效果行「死亡缠绕：+20% 伤害」"""
    for sep in (":", "："):
        if sep in (name or ""):
            cand = name.split(sep)[-1].strip()
            if abil_lookup(cand):
                return cand
    for line in (text or "").split("\n"):
        m = re.match(r"^([^：:]{2,10})[：:]", line.strip())
        if m and abil_lookup(m.group(1).strip()):
            return m.group(1).strip()
    return None


def _global_bounce_gain(ctx, bounces):
    """全局「你的命中会跳跃到另外 N 个敌人」：按各技能占比加权"""
    added = sum(n * p for n, p in bounces)
    num = 0.0
    den = 0.0
    for r in ctx.meter:
        share = r.get("share") or 0.0
        if share <= 0:
            continue
        name = (r.get("name") or "").strip()
        _, _, base_t, _, _ = dot_params(name, ctx)
        t = max(1.0, base_t + ctx.cnt)
        rr = ctx.extra_ratio.get(name, 1.0)
        eff = 1.0 + rr * (t - 1.0)
        num += share * (eff + added) / eff
        den += share
    if den <= 0:
        return 1.0 + added * 0.3
    return 1.0 + (num / den - 1.0)


def _gear_base(name):
    """「瘟疫坠饰: 腐蚀术」→「瘟疫坠饰」；不是装备卡则返回空串"""
    if ":" in name or "：" in name:
        return re.split(r"[:：]", name)[0].strip()
    return ""


def _lost_gear_share(ctx, name):
    """换掉身上另一件装备时，它自己打出来的那部分伤害会一起归零

    伤害榜里「瘟疫坠饰: 腐蚀术」这种带冒号的条目，是装备自己的输出（爆炸）。
    项链/戒指/饰品只能戴一件，新卡上阵 = 旧卡那条从榜上消失。

    只有「榜上恰好一件其他装备」时才扣，同时戴了项链和戒指就跳过 ——
    宁可不扣，也不要扣错。
    """
    base = _gear_base(name)
    if not base:
        return 0.0, ""
    others = []
    for r in ctx.meter:
        rn = (r.get("name") or "").strip()
        if not _gear_base(rn):
            continue
        if rn.startswith(base):
            continue          # 自己这张卡的升级版，不是"换掉别人"
        others.append(r)
    if len(others) != 1:
        return 0.0, ""
    r = others[0]
    return (r.get("share") or 0.0), (r.get("name") or "")


def _abil_key(name):
    return (abil(name) or {}).get("key") or ""


def _zh_of_key(key):
    for r in ABILITIES.values():
        if (r.get("key") or "") == key:
            return (r.get("name_zh") or r.get("name") or "").strip()
    return ""


def _gear_name_of(zh):
    """「腐蚀术」→「瘟疫坠饰: 腐蚀术」（等级表里那条）"""
    for cand in GEAR_RANKS:
        if zh and zh in cand:
            return cand
    return ""


def _base_targets(subj):
    tt = SPELL_TABLE.get((subj or "").strip()) or {}
    try:
        return max(1.0, float(tt.get("cnt") or 1))
    except (TypeError, ValueError):
        return 1.0


def _gear_effect_ratio(ctx, subj, old_dmg, new_dmg, old_t, new_t, solo=False):
    """装备给的「+N 目标 / +X% 伤害」变化，折算成本体技能的倍率

    solo=True（单体 BOSS 战）时目标数这一项恒为 1，只剩伤害那部分。
    """
    b = _base_targets(subj)
    r_t = 1.0 if solo else (b + new_t) / max(1e-6, b + old_t)
    pool = max(ctx.dmg, 1.0)
    r_d = (pool + new_dmg / 100.0) / max(1e-6, pool + old_dmg / 100.0)
    return r_t * r_d


def _swap_gain(ctx, choice, name, subj, share, solo=False):
    """换装净收益（项链/戒指/饰品只能戴一件）

    三种情况：
      1) 同名牌、等级不同：新卡面 - 身上那件的数值
      2) 不同名牌、同槽位：新卡收益 - 旧装备给它的本体加成 - 旧装备自己的输出
      3) 槽位空着：就是白送，按原逻辑

    solo=True 时按单体 BOSS 战算，装备上的「+N 目标」不再计入。
    返回 (pct, 说明) 或 None（交给原逻辑）。
    """
    slot = (choice.get("kind") or "").strip().lower()
    if slot not in ("amulet", "ring", "trinket"):
        return None
    tbl = GEAR_RANKS.get(name) or {}
    new_rank = choice.get("rank")
    try:
        new_rank = int(new_rank) if new_rank is not None else None
    except (TypeError, ValueError):
        new_rank = None

    cur = (ctx.gear or {}).get(slot)
    cur_key = cur_rank = None
    if cur:
        try:
            cur_key, cur_rank = cur[0], int(cur[1])
        except (TypeError, ValueError, IndexError):
            cur_key, cur_rank = None, None

    # ---- 1) 同名牌换等级
    if cur_key and cur_key == _abil_key(subj) and cur_rank and new_rank:
        if cur_rank == new_rank:
            return 0.0, "同名r%d 已佩戴" % cur_rank
        a = tbl.get(str(cur_rank)) or {}
        b = tbl.get(str(new_rank)) or {}
        if "dmg" in a and "dmg" in b:
            def _tg(rank, d):
                if "targets" in d:
                    return float(d["targets"])
                for step in (1, -1, 2, -2):
                    o = tbl.get(str(rank + step)) or {}
                    if "targets" in o:
                        return float(o["targets"])
                return 0.0
            t0, t1 = _tg(cur_rank, a), _tg(new_rank, b)
            if solo:
                # BOSS 战只有一个目标，装备多给的目标数全部作废
                t0 = t1 = 0.0
            r = _gear_effect_ratio(ctx, subj, a["dmg"], b["dmg"], t0, t1)
            pct = (r - 1.0) * 100.0 * share
            return pct, "同名r%d→r%d 目标%g→%g 伤害%+g%%" % (
                cur_rank, new_rank, _base_targets(subj) + t0,
                _base_targets(subj) + t1, b["dmg"] - a["dmg"])

    # ---- 2) 换掉另一个槽位上的装备
    if cur_key and cur_key != _abil_key(subj):
        old_zh = _zh_of_key(cur_key)
        old_gname = _gear_name_of(old_zh)
        rec = ((GEAR_RANKS.get(old_gname) or {}).get(str(cur_rank)) or {}) \
            if (old_gname and cur_rank) else {}
        if "dmg" in rec:
            old_t = float(rec.get("targets") or 0)
            # 新卡在这个槽位上给的 +目标和 +伤害（卡面直接给的）
            new_t = 0.0
            new_d = 0.0
            new_echo = 1.0
            for e in parse_card(choice.get("lines") or choice.get("text") or ""):
                if e.stat == "targets":
                    new_t = e.delta
                elif e.stat == "damage" and e.before is None:
                    new_d = e.delta
                elif e.stat == "echo_self":
                    new_echo *= 1.0 + e.delta / 100.0
            # 旧装备对「老技能」的加成没了 + 新装备只加「新技能」
            if old_zh and old_zh != subj:
                old_share = ctx.share(old_zh) or 0.0
                r_old = _gear_effect_ratio(ctx, old_zh, rec["dmg"], 0.0,
                                           old_t, 0.0, solo=solo)
                lost_body = (1.0 - r_old) * 100.0 * old_share
            else:
                lost_body = 0.0
            lost_self, _ln = _lost_gear_share(ctx, name)
            sh = share or 0.0
            b = _base_targets(subj)
            if solo:
                new_t = 0.0          # 单体 BOSS：+目标 不计
            r_new = (1.0 + new_d / 100.0 / max(ctx.dmg, 1.0)) * \
                    (1.0 + (b + new_t - b) / b * COVERAGE_SKILL) * new_echo
            pct = (r_new - 1.0) * 100.0 * sh - lost_body - lost_self * 100.0
            return pct, "换下%sr%d(-%g%%) 新装占%.0f%%×+%g目标+%g%%伤害" % (
                old_zh or "旧装", cur_rank,
                round(lost_body + lost_self * 100), sh * 100, new_t, new_d)
    return None


# 打 BOSS 只剩一个目标时，这些属性对输出没有任何贡献：
#   targets  +N 目标        —— 没第二个目标可打
#   area     +N% 范围       —— 打大怪不看范围
# 弹射/分裂同理（bounces 走单独通道，solo 时直接丢弃）。
SOLO_DEAD_STATS = ("targets", "area")


def evaluate_choice(choice, ctx, abil_lookup, solo=False):
    """评估一张卡。返回 (pct, note, confident, dmg_related)

    pct 是「总 DPS 提升百分比」；note 给悬浮层当副标题。
    算不出来时 pct=None，交给调用方走特殊表。

    solo=True 表示按「BOSS 战只有一个目标」重算：把 +目标 / +范围 /
    弹跳分裂这些只有多目标才吃得到的收益直接抹掉，剩下的（伤害/暴击/
    冷却/持续/回声）在单体战里照样生效。
    """
    name = (choice.get("name") or "").strip()
    text = choice.get("lines") or choice.get("text") or ""
    subj = subject_skill(name, text, abil_lookup)
    if not subj and abil_lookup(name):
        subj = name

    effects = parse_card(text)
    bounces = bounce_targets(text)
    solo_dead = False
    if solo:
        raw_n = len(effects) + len(bounces)
        effects = [e for e in effects if e.stat not in SOLO_DEAD_STATS]
        bounces = []
        solo_dead = raw_n > 0 and not effects and not bounces
    if not effects and not bounces:
        if solo_dead:
            # 这张卡给的全是「+目标 / +范围 / 弹射」—— 打单体 BOSS 就是 0
            return 0.0, "只加目标/范围，BOSS 战无收益", True, True
        return None, "", False, False

    share = None
    fb = False
    if subj:
        share = ctx.share(subj)
        if share is None:
            if is_damage_skill(subj):
                # 该技能这一把还没打出伤害：给个保守的「次要技能」占比。
                # 以前这里是 8%，结果「献祭 +50% 伤害」被算成 +4.0%，
                # 反过来盖过主力技能的「腐蚀术 +5%」（占 44% → +2.2%）。
                # 没打过伤害的技能就是没在贡献，宁可低估也不能抢了主技能的位置。
                share = 0.03
                fb = True
            else:
                share = 0.0       # 治疗/免疫类技能：加它也不会涨 DPS

    ratio, notes, conf, dmg_related = eval_effects(effects, ctx, subj, share)

    if bounces:
        conf = False
        if subj:
            _, _, base_t, _, _ = dot_params(subj, ctx)
            t = max(1.0, base_t + ctx.cnt)
            rr = ctx.extra_ratio.get(subj, 1.0)
            eff0 = 1.0 + rr * (t - 1.0)
            added = sum(n * p for n, p in bounces)
            ratio *= 1.0 + ((eff0 + added) / eff0 - 1.0) * COVERAGE_SKILL
            notes.append("跳跃%g×%.0f%%" % (sum(n for n, _ in bounces),
                                            max(p for _, p in bounces) * 100))
        else:
            ratio *= _global_bounce_gain(ctx, bounces)
            notes.append("全局跳跃%g" % sum(n for n, _ in bounces))

    note = " ".join(n for n in notes if n)
    if not dmg_related:
        return 0.0, "非伤害·" + note if note else "非伤害", False, False

    # 装备卡（项链/戒指/饰品）只能戴一件：先按「新的 - 身上旧的」算净账。
    # 少了这一步，「r7 换 r1」会被当成白送 +105%，「换另一条项链」也只算收益不算代价。
    swapped = _swap_gain(ctx, choice, name, subj, share, solo=solo) if subj else None
    if swapped is not None:
        return swapped[0], swapped[1], False, True

    if share is not None:
        pct = (ratio - 1.0) * 100.0 * share
        head = ("估%.0f%%×" % (share * 100)) if fb else ("占%.0f%%×" % (share * 100))
        lost, lost_name = _lost_gear_share(ctx, name)
        if lost > 0:
            # 换装卡只能戴一件：新卡上阵的代价是旧卡那条伤害整条消失
            pct -= lost * 100.0
            short = re.split(r"[:：]", lost_name)[-1].strip()
            head = "换下%s-%g%% " % (short, round(lost * 100)) + head
            conf = False
        return pct, head + note, conf, True
    return (ratio - 1.0) * 100.0, note, conf, True
