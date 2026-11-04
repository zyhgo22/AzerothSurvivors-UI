# -*- coding: utf-8 -*-
"""AZS 选卡伤害收益助手 —— 数据大脑

读取游戏客户端 stdout（RUST_LOG 打开 addon chat 调试输出），从中解析：
  * rogue: {"type":"choices",...}   —— 本次升级四选一的完整数据（名称/描述/稀有度）
  * AZS\t...                        —— 当前乘区：dmg/cd/crit/cm/area/cnt/echo ...
  * AZSMETER\t...                   —— 每个技能的伤害、DPS、占比
  * AZSSPELLS\t...                  —— 已学技能的冷却

据此算出每张卡能带来多少总 DPS 提升，写入 state.json 给 overlay.ps1 显示。
"""
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse

try:
    import azs_model as MODEL
except Exception:          # 模型文件缺失时不让后台进程直接死掉
    MODEL = None

try:
    import azs_strength as STR
except Exception:          # 静态强度表缺失时退回旧行为
    STR = None

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "client.log")
STATE = os.path.join(HERE, "state.json")
ABILITY_FILE = os.path.join(HERE, "abilities.json")
SETTINGS_FILE = os.path.join(HERE, "settings.json")

DEFAULT_SETTINGS = {
    # 卡牌横向间距（占窗口宽度的比例）：实测 1920 宽下相邻卡中心相距 376px
    "card_step": 0.1958,
    # 徽标纵向位置（占窗口高度的比例）
    "badge_y": 0.222,
    # 徽标尺寸
    "badge_width": 220,
    "badge_height": 44,
    # 心跳静默多久算"模拟已暂停"（秒）。选卡界面打开时客户端会暂停模拟，
    # AZSRUN 每 0.2s 一次的心跳随之停止 —— 这是判断界面是否开着最可靠的信号。
    "pause_gap": 0.8,
    # 一张卡最多显示多久（秒），防止异常情况下徽标常驻
    "max_picker_seconds": 600,
    # 天赋开关：插件读不到天赋树，只能由你在这里声明。
    #   consume_magic    术士地狱犬「吞噬魔法」：持续时间多一条结算通道
    #   echo_cascade     奥法终局「回声连锁」：回声变成 1+E+E²+E³
    #   dot_stack_bonus  额外 DoT 层数（叠层天赋）
    "talents": {
        "consume_magic": False,
        "echo_cascade": False,
        "dot_stack_bonus": 0,
    },
    # 按职业覆盖上面的天赋（只写要覆盖的键）
    "class_talents": {},
    # 吞噬魔法多久触发一次（秒），影响持续时间卡的收益大小
    "consume_interval": 6.0,
    # 怪平均能活多久（秒）：DoT 跳不完怪就死了，超出的持续时间等于白给。
    # 调大 → 「+持续时间」更有价值、减 CD 相对变弱；调小 → 反过来。
    "dot_lifetime": 4.0,
    # 额外目标的伤害折扣（按技能名覆盖内置表）
    "extra_target_ratio": {},
    # 工具箱里选中的流派 id（对应 builds.json），空表示不启用
    "spec": "",
}


def load_settings():
    cfg = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


SETTINGS = load_settings()

# settings.json 是工具箱写的，本进程只在启动时读一次的话，用户在界面上改
# 「流派 / 字号 / 位置」就得手动点「重启后台」才生效——这是最容易踩的坑。
# 所以按修改时间热重载：注意只能就地改 SETTINGS，不能重新赋值，
# 因为 build_state / evaluate 里全是直接引用这个 dict。
_settings_mtime = 0.0


def reload_settings():
    global _settings_mtime
    try:
        m = os.path.getmtime(SETTINGS_FILE)
    except OSError:
        return
    if m <= _settings_mtime:
        return
    _settings_mtime = m
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            new = json.load(fh)
        SETTINGS.clear()
        SETTINGS.update(DEFAULT_SETTINGS)
        SETTINGS.update(new)
    except Exception:
        pass

# ---------------------------------------------------------------- abilities
try:
    with open(ABILITY_FILE, "r", encoding="utf-8") as fh:
        ABILITIES = json.load(fh)
except Exception:
    ABILITIES = {}

BY_ZH = {}
for _rec in ABILITIES.values():
    for _k in (_rec.get("name_zh"), _rec.get("name")):
        if _k:
            BY_ZH.setdefault(_k.strip(), _rec)


def ability_by_name(name):
    if not name:
        return None
    return BY_ZH.get(name.strip())


# ---------------------------------------------------------------- 运行时状态
class State(object):
    def __init__(self):
        self.stats = {}
        self.meter = []
        self.boss_meter = []       # 只算打精英/首领的那部分，格式同 meter
        self.boss_ts = 0.0
        self.spells = {}
        self.gear = {}             # {"amulet": {"id": "fire_lord", "affix": None, "rank": 1}}
        self.offer = None          # dict or None
        self.offer_ts = 0.0
        self.resolved = True       # 当前 offer 是否已被选掉（收到 resume）
        self.meter_ts = 0.0
        self.game_seen = False
        self.phase = None         # AZSRUN 的 ph 字段：playing / choosing / dead ...
        self.phase_ts = 0.0
        self.last_ts = 0.0        # 最近一条带时间戳的日志行
        self.run = {}
        self.cls = ""             # AZSZONE 里的 class=paladin / warlock / priest


ST = State()


def parse_pairs(payload):
    out = {}
    for part in payload.split(";"):
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def parse_meter(payload):
    rows = []
    for part in payload.split(";"):
        part = part.strip()
        if not part:
            continue
        f = part.replace(",", "\u0001").rsplit("\u0001", 4)
        if len(f) != 5:
            continue
        name = f[0].strip()
        try:
            rows.append({
                "name": name,
                "dmg": float(f[1]),
                "dps": float(f[2]),
                "share": float(f[3]),
                "proc": int(float(f[4])),
            })
        except ValueError:
            continue
    return rows


RARITY = ("common", "uncommon", "rare", "epic", "legendary", "poor", "artifact")


def parse_gear(payload):
    """从 AZSM 里捞出身上穿的项链 / 戒指 / 饰品。

    装备在内存里是 URL 编码的，而且有两种写法：
      R,<id>,12,d,<id>,amulet%3Afire_lord%3Ar1,...     固定装备：炎魔之王项圈 r1
      R,<id>,47,d,<id>,amulet%3Alegendary%3Acorruption%3Ar7,...  词缀装备：腐蚀术 r7
    两者的差别是中间多不多一个稀有度词，所以按段数分开认。
    """
    out = {}
    for seg in (payload or "").split(";"):
        if not seg.startswith("R,"):
            continue
        parts = seg.split(",")
        if len(parts) < 6:
            continue
        raw = parts[5]
        if "%3A" in raw.upper():
            raw = urllib.parse.unquote(raw)
        m = re.match(r"(amulet|ring|trinket):(.+):r(\d+)$", raw)
        if not m:
            continue
        slot, mid, rank = m.group(1), m.group(2), int(m.group(3))
        bits = mid.split(":")
        if len(bits) >= 2 and bits[0].lower() in RARITY:
            # 词缀装备：稀有度 + 词缀
            out[slot] = {"id": None, "affix": bits[-1], "rank": rank,
                         "rarity": bits[0].lower()}
        else:
            out[slot] = {"id": mid, "affix": None, "rank": rank, "rarity": None}
    return out


def parse_spells(payload):
    out = {}
    for part in payload.split(";"):
        f = part.split(",")
        if len(f) < 5:
            continue
        try:
            out[f[0].strip()] = {
                "spell_id": int(f[1]),
                "rank": int(float(f[2])),
                "cd": float(f[3]),
                "cdmax": float(f[4]),
            }
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- 收益模型
def total_dps():
    return sum(r["dps"] for r in ST.meter) or 0.0


def share_of(name):
    """技能在总伤害中的占比（0..1）。名字做宽松匹配。"""
    if not name or not ST.meter:
        return None
    key = name.strip()
    for r in ST.meter:
        if r["name"].strip() == key:
            return r["share"]
    for r in ST.meter:
        n = r["name"].strip()
        if key and (key in n or n in key):
            return r["share"]
    return None


def dps_of(name):
    key = (name or "").strip()
    for r in ST.meter:
        if r["name"].strip() == key:
            return r["dps"]
    return None


def crit_factor():
    crit = ST.stats.get("crit", 0.0)
    cm = ST.stats.get("cm", 2.0)
    return 1.0 + crit * (cm - 1.0)


def pct_from(ratio):
    return (ratio - 1.0) * 100.0


# 特殊机制卡：没有可用公式时的兜底（值：pct / 说明 / 是否精确）
# 注意：能解析出数值的卡会先走 azs_model，不会落到这里。
SPECIAL_CARDS = {
    "炎魔之王项圈": (7.0, "击杀爆炸·按怪血量", False),
    "酋长的号角": (0.0, "非伤害·增益强度", False),
    "耶克里克的乳白护符": (0.0, "非伤害·吸血/击杀回复", False),
    "吞噬者之戒": (0.0, "非伤害·每秒回复", False),
    "饮血者指环": (0.0, "非伤害·吸血", False),
    "奥妮克希亚之血护符": (0.0, "非伤害·击杀回复", False),
    "极效灵魂石": (0.0, "非伤害·复活次数", False),
    "恶魔增效": (0.0, "非伤害·需有恶魔", False),
    "原始的灵魂之石": (0.0, "非伤害·复活次数", False),
}


def model_special(name, text):
    """特殊机制卡：能算的给保守估算，纯收益类给 0 并标记为「非伤害」"""
    hit = SPECIAL_CARDS.get(name)
    if hit:
        return hit[0], hit[1], hit[2]
    if re.search(r"分散|分裂|弹射|跳跃", text):
        return 3.0, "分散目标·无具体数值", False
    if re.search(r"治疗|回复|恢复|吸血|复活|免疫|护盾|吸收", text):
        return 0.0, "非伤害·生存/回复", False
    if re.search(r"金币|经验|幸运|拾取|移动速度|生命值|减伤", text):
        return 0.0, "非伤害·收益类", False
    return None, "特殊机制·无公式", False


# 「在15秒内分15跳造成490点」——新法术卡自带真实持续/跳数，
# 顺手记进模型，这样 DoT 的持续时间以后就不用猜了
DOT_TEXT_RE = re.compile(
    r"在\s*(\d+(?:\.\d+)?)\s*秒内分\s*(\d+(?:\.\d+)?)\s*跳造成\s*(\d+(?:\.\d+)?)\s*点")


def known_spell_names():
    """身上已经学到的技能名（用来给新技能卡算相对强度）"""
    return [n for n in (ST.spells or {}) if (n or "").strip()]


def static_share(name, solo=False):
    """没有伤害统计时，用客户端技能表估一个「预计占总伤害」的比例

    以前这里直接返回「尚无伤害统计」，结果刚开一局 / 换了职业还没打过怪时，
    所有新技能卡都显示「无法计算」。现在改成按技能表横向比一次。

    solo=True 按单体口径算（打 BOSS），否则按群体口径（刷小怪）。
    """
    if STR is None:
        return None
    try:
        return STR.new_spell_share(name, known_spell_names(), aoe_mode=not solo)
    except Exception:
        return None


# 卡面文字里出现这些词，说明这技能对首领/精英有特化。
# 为什么不靠 abilities.json 的 desc：那份表 152 条里只有 24 条填了描述，
# 多重射击的 desc 直接是 None，「优先攻击首领和精英」白写在卡面上却读不到。
BOSS_TEXT_HINTS = (
    ("优先攻击首领和精英", "首领优先"),
    ("优先攻击首领", "首领优先"),
    ("对精英和首领", "对精英加伤"),
    ("对生命值低于30%", "斩杀低血"),
)


def boss_tags_of(name, text=""):
    """打 BOSS 的特化标记：静态表 + 卡面文字双路取，谁有算谁"""
    out = []
    if STR is not None:
        try:
            out.extend(STR.boss_tags(name))
        except Exception:
            pass
    blob = text or ""
    for word, tag in BOSS_TEXT_HINTS:
        if word in blob and tag not in out:
            out.append(tag)
    return out[:2]


def targets_of(name, text=""):
    """一次打几个目标。

    abilities.json 大半没填 targets，spell_table 里的计数字段叫 cnt，
    两边都拿不到时只能从卡面文字读（「最近的3个敌人」「范围内的所有敌人」）。
    这个数是「小怪战」的口径；打 BOSS 时会强制成 1。
    """
    t = 0.0
    ab = ability_by_name(name) or {}
    for v in (ab.get("targets"), (STR.row_of(name) or {}).get("cnt") if STR else None):
        try:
            t = max(t, float(v or 0))
        except (TypeError, ValueError):
            pass
    if t > 0:
        return t
    m = re.search(r"最近的?\s*(\d+)\s*个敌人", text) or \
        re.search(r"(\d+)\s*个敌人", text)
    if m:
        return float(m.group(1))
    if re.search(r"所有敌人|内所有|范围内的敌人", text):
        return 3.0          # 范围技能：小怪聚堆时按 3 个估
    return 1.0


def _soft_pct(share):
    """把「预计占比」压进 0~30% 的曲线

    以前直接 min(30, share*100)：占比超过 30% 的卡全被截成同一个数，
    三张牌又变得分不出高下。改成平滑饱和，永远逼近但不超过 30，
    而且占比越大数值越大。
    """
    try:
        sh = max(0.0, float(share))
    except (TypeError, ValueError):
        return 0.0
    return 30.0 * (1.0 - math.exp(-3.0 * sh))


def estimate_new_spell(choice, solo=False):
    """新技能：用卡面伤害数值 + 技能表冷却，粗估 DPS 占比

    卡面伤害是该技能当前等级的值，成长节奏因人而异，所以结果只能当参考
    （界面显示 ≈），并封顶 30%，避免压过所有强化卡。

    solo=True 时按「BOSS 战只有一个目标」算：目标数强制为 1，
    所以带溅射/多目标的技能在 BOSS 战里数字会明显低于小怪战。
    """
    text = choice.get("text") or choice.get("lines") or ""
    # 卡名带「新法术：」前缀，查技能表前必须去掉
    name = re.sub(r"^新(?:法术|技能)[：:]\s*", "", (choice.get("name") or "").strip())
    tdps = total_dps()

    # DoT：卡面自带「X秒内分Y跳造成Z点」，可以直接算出真实 DPS
    m = DOT_TEXT_RE.search(text)
    if m:
        try:
            dur, _ticks, total = (float(x) for x in m.groups())
        except ValueError:
            dur = total = 0.0
        if dur > 0 and total > 0:
            MODEL.TOOLTIP_DOT[name] = {"dur": dur, "ticks": float(m.group(2))}
            ctx = build_ctx()
            d, cd, tgt, cap, _kind = MODEL.dot_params(name, ctx)
            if solo:
                tgt = 1.0
            stacks = min(d / max(0.2, cd), cap)
            dps = total / max(0.5, d) * tgt * stacks
            if tdps <= 1.0:
                sh = static_share(name, solo=solo)
                if sh is None:
                    return None, "尚无伤害统计", False
                return (_soft_pct(sh),
                        "新DoT %.0f伤/%.0fs×%g目标 按技能表估"
                        % (total, d, tgt), False)
            pct = min(30.0, dps / tdps * 100.0)
            return pct, "新DoT %.0f伤/%.0fs×%g目标×%.1f层" % (
                total, d, tgt, stacks), False

    # 「造成X点」「受到X点」都要认
    hits = re.findall(r"(?:各)?(?:造成|受到)\s*(\d+(?:\.\d+)?)\s*点", text)
    if not hits:
        if re.search(r"恢复|免疫|护盾|吸收|治疗|减免", text):
            return 0.0, "治疗/防御，无直接伤害", False
        return None, "特殊效果·无伤害数字", False

    ab = ability_by_name(name) or {}
    per_hit = max(float(h) for h in hits)
    total = None
    mt = re.search(r"共计\s*(\d+(?:\.\d+)?)\s*点", text)
    if mt:
        total = float(mt.group(1))

    targets = targets_of(name, text)
    if solo:
        targets = 1.0            # BOSS 战只剩一个目标
    bonus = 0.0
    for mb in re.finditer(r"\+\s*(\d+(?:\.\d+)?)\s*%\s*伤害", text):
        bonus = max(bonus, float(mb.group(1)))

    cd = ab.get("cooldown") or 4.0
    cd_stat = ST.stats.get("cd", 1.0) or 1.0
    dmg = total if total else per_hit
    dps = dmg * targets / max(0.3, cd * cd_stat) * (1.0 + bonus / 100.0)

    if tdps <= 1.0:
        sh = static_share(name, solo=solo)
        if sh is None:
            return None, "尚无伤害统计", False
        return (_soft_pct(sh),
                "新技能 %g×%g目标/%.1fs 按技能表估"
                % (dmg, targets, cd * cd_stat), False)
    pct = dps / tdps * 100.0
    capped = pct > 30.0
    pct = min(30.0, pct)
    note = "新技能 %g×%g目标/%.1fs%s" % (dmg, targets, cd * cd_stat,
                                          ("+%.0f%%" % bonus) if bonus else "")
    if capped:
        note += " 封顶"
    return pct, note, False


CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
NUM_RE = r"(\d+(?:\.\d+)?|[一两二三四五六七八九十])"


def to_num(s):
    s = (s or "").strip()
    if s in CN_NUM:
        return float(CN_NUM[s])
    try:
        return float(s)
    except ValueError:
        return 1.0


def estimate_killproc(choice, solo=False):
    """击杀/周期触发型技能（救赎、灵魂之火）：按触发频率估算，而不是冷却"""
    text = choice.get("text") or ""
    name = (choice.get("name") or "").strip()
    hits = re.findall(r"(?:造成|受到)\s*(\d+(?:\.\d+)?)\s*点", text)
    if not hits:
        return None, "特殊效果·无伤害数字", False
    per_hit = max(float(h) for h in hits)
    ab = ability_by_name(name) or {}
    targets = targets_of(name, text)
    if solo:
        targets = 1.0

    freq = None
    m = re.search(r"每秒最多\s*" + NUM_RE + r"\s*次", text)
    if m:
        freq = to_num(m.group(1)) * 0.35
    if freq is None:
        m = re.search(r"每\s*" + NUM_RE + r"\s*秒(?:最多)?(?:一次|\s*1\s*个)", text)
        if m:
            freq = 1.0 / max(0.2, to_num(m.group(1)))
    if freq is None:
        freq = 1.0

    tdps = total_dps()
    if tdps <= 1.0:
        sh = static_share(name, solo=solo)
        if sh is None:
            return None, "尚无伤害统计", False
        return (_soft_pct(sh),
                "击杀触发 %g伤×%.1f/s 按技能表估"
                % (per_hit, freq), False)
    pct = min(30.0, per_hit * targets * freq / tdps * 100.0)
    return pct, "击杀触发·%g伤害×%.1f/s" % (per_hit, freq), False


def build_ctx():
    """把当前 build 打包给模型：属性/等级/职业/伤害统计/天赋开关"""
    lvl = 1
    try:
        lvl = int(float(ST.run.get("lvl", 1) or 1))
    except (TypeError, ValueError):
        pass
    # 天赋：先取通用默认，再用 class_talents[职业] 覆盖
    talents = dict(SETTINGS.get("talents") or {})
    per_cls = (SETTINGS.get("class_talents") or {}).get(getattr(ST, "cls", ""))
    if isinstance(per_cls, dict):
        talents.update(per_cls)
    return MODEL.Ctx(
        stats=ST.stats,
        lvl=lvl,
        cls=getattr(ST, "cls", ""),
        meter=ST.meter,
        share_fn=share_of,
        # BOSS 口径单独一份榜：打首领只剩一个目标，占比和小怪战差很远
        boss_meter=ST.boss_meter,
        talents=talents,
        settings=SETTINGS,
        gear=ST.gear,
        # 技能面板：伤害榜还没这个技能时，用它算「大概占多少」
        panel=ST.spells,
    )


def evaluate(choice, solo=False):
    """返回 (pct, note, confident)

    solo=True：按「BOSS 战只有一个目标」重算（+目标/+范围/弹射都不算数）。
    """
    name = (choice.get("name") or "").strip()
    lines = choice.get("lines") or ""
    text = choice.get("text") or lines
    kind = choice.get("kind") or ""

    # 0) 新技能：卡面上的 +X% 是它自己的强度，不能当成全局增益
    if kind == "new":
        if re.search(r"你击杀|击杀的敌人|击杀会|击中时|碎片|每秒最多\s*\d+", text):
            pct, note, conf = estimate_killproc(choice, solo=solo)
            if pct is not None:
                return pct, note, conf
        pct, note, conf = estimate_new_spell(choice, solo=solo)
        if pct is None:
            return model_special(name, text)
        return pct, note, conf

    pct, note, conf, dmg_related = MODEL.evaluate_choice(
        choice, build_ctx(), ability_by_name, solo=solo)

    if pct is None:
        # 「你击杀的敌人有12%几率爆发」这类击杀触发卡：按触发频率估
        if re.search(r"你击杀|击杀的敌人|击杀会|爆发", text):
            kp, kn, kc = estimate_killproc(choice, solo=solo)
            if kp is not None:
                return kp, kn, kc
        # 模型解析不出数值：走特殊机制表
        return model_special(name, text)
    if not dmg_related:
        return 0.0, note or "非伤害", False

    # 饰品/戒指：worn=1 表示会替换已佩戴的那件，真实增益是「新值-旧值」，
    # 日志里拿不到旧值，只能把卡面值当上限，并降级为估算（显示 ≈）。
    if choice.get("worn"):
        conf = False
        note = "换装·" + note
    return pct, note, conf


def fit_note(note, max_px=262):
    """按显示宽度截断副标题

    徽标固定宽度，中文约 10.7px/字、数字符号约 5.6px/字，按字符数截断会把
    「弹跳2×30%」这种关键信息切掉，所以这里按估算宽度算。
    """
    w = 0.0
    for i, c in enumerate(note):
        w += 10.7 if ord(c) > 0x2000 else 5.6
        if w > max_px:
            return note[:i] + "…"
    return note


BOSS_TAG_CN = {
    "首领优先": "首领优先",
    "对精英加伤": "对精英加伤",
    "斩杀": "斩杀低血",
    "碎片爆发": "碎片爆发",
}


def boss_line(r):
    """徽标第三行：这张卡换到「单体 BOSS 战」还值多少

    BOSS 战只剩一个目标，所以两张卡在小怪战里都是 +30%，打大怪可能一个
    是 +30%、一个是 0%。这一行就是为了把这个差距摆到眼前。

    返回 (文案, 类型)，类型给悬浮层挑颜色：
      tag  —— 有首领特化（优先打首领 / 对精英加伤 / 斩杀），荧光绿
      weak —— 群体卡，打单体掉价明显，橙色警告
      ''   —— 普通卡，灰色
    """
    name = r.get("name") or ""
    tags = [BOSS_TAG_CN.get(t, t) for t in boss_tags_of(name, r.get("text") or "")]

    bp = r.get("boss_pct")
    gp = r.get("pct")
    if bp is None:
        txt = "?"
        kind = ""
    elif bp <= 0.05:
        # 「只吃多目标」只能给真的靠目标数吃饭的卡。
        # 幸运、金币这类卡打 BOSS 当然也是 0，但那样写会被误读成
        # 「这卡打 BOSS 没用」，其实它本来就不是伤害卡。
        if "只加目标" in (r.get("note") or ""):
            txt = "0%·只吃多目标"
            kind = "weak"
        else:
            txt = "0%"
            kind = ""
    else:
        txt = ("+%.1f%%" if r.get("confident") else "≈+%.1f%%") % bp
        # 比小怪战低 3 个点以上 = 群体卡，进 BOSS 战明显掉价
        kind = "weak" if (gp is not None and bp < gp - 3.0) else ""

    if tags:
        txt += "·" + "/".join(tags)
        kind = "tag"
    return fit_note(txt, max_px=196), kind


def grade_of(pct, conf, util):
    """第一行「综合评价」：把百分比翻译成人话

    门槛是按实测调的：爆炸陷阱 ≈14%、多重射击 ≈23% 属于「看到就拿」，
    常见的 +2~3% 小强化落在「可以考虑」，纯生存卡单独走「非输出向」。
    confident 留着给以后的分档用（现在只影响百分比前面的 ≈）。
    """
    if pct is None:
        return "无法评估", "unknown"
    if util:
        return "非输出向", "util"
    if pct >= 15.0:
        return "强烈推荐", "s"
    if pct >= 8.0:
        return "值得一拿", "a"
    if pct >= 3.0:
        return "可以考虑", "b"
    if pct > 0.05:
        return "聊胜于无", "c"
    return "不建议", "d"


def spec_line_of(spec, has_pre, basis):
    """第四行：这张卡跟「当前流派」的关系

    spec 来自 spec_hit / preset_rank：must = 流派核心，opt = 可选项。
    没设流派时不能写「非本流派」——那会让人以为漏配了，直接说未设流派。
    """
    if spec == "must":
        return ("预设核心" if has_pre else "本流派 · 核心"), "must"
    if spec == "opt":
        return ("预设可选" if has_pre else "本流派 · 可选"), "opt"
    if not SETTINGS.get("spec") and not has_pre:
        return "未设流派", ""
    if has_pre:
        return "不在预设里", ""
    if basis == "pet":
        return "非本流派 · 宠物线", ""
    return "非本流派", ""


def note_unknown(choice, note):
    """把算不出来的卡记到 logs/unknown_cards.json，方便事后补进模型表

    客户端不会主动告诉我们卡牌的内部 key（choices 事件里只有 name/icon/kind/rank），
    所以这里用「卡名+图标+效果文本」当稳定标识，同一张卡只记一次。
    """
    try:
        path = os.path.join(HERE, "logs", "unknown_cards.json")
        db = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                db = json.load(fh)
        key = "%s|%s" % (choice.get("name", ""), choice.get("icon", ""))
        if key in db:
            return
        db[key] = {
            "name": choice.get("name"),
            "icon": choice.get("icon"),
            "kind": choice.get("kind"),
            "rank": choice.get("rank"),
            "lines": choice.get("lines"),
            "note": note,
            "seen": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(db, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def preset_lists():
    """工具箱里自己填的优先级清单（越靠前越想要）

    settings.json 里长这样：
      "preset": {"enabled": true, "skills": ["腐蚀术", "痛苦诅咒"],
                 "gear": ["哈卡之眼"]}
    也容忍一个字符串（用换行/逗号分隔），方便手改。
    """
    p = SETTINGS.get("preset") or {}

    def _clean(v):
        if isinstance(v, str):
            parts = re.split(r"[\n,，;；]+", v)
        elif isinstance(v, list):
            parts = v
        else:
            return []
        return [str(x).strip() for x in parts if str(x).strip()]

    if p.get("enabled") is False:
        return [], []
    return _clean(p.get("skills")), _clean(p.get("gear"))


def _bare(name):
    return re.sub(r"^新(?:法术|技能)[：:]\s*", "", (name or "").strip())


def preset_rank(name):
    """命中预设时返回优先级（1 最高），没命中返回 0

    匹配用「互相包含」：预设里写「腐蚀术」，卡面写「新法术：腐蚀术」也算命中。
    短名字（<2 字）不做包含匹配，避免「火」把「火焰冲击」也算进来。
    """
    n = _bare(name)
    if not n:
        return 0
    skills, gears = preset_lists()
    for i, s in enumerate(skills):
        if s == n or (len(s) >= 2 and (s in n or n in s)):
            return i + 1
    for i, g in enumerate(gears):
        if g == n or (len(g) >= 2 and (g in n or n in g)):
            return i + 1
    return 0


def build_preset_panel():
    """把预设清单画成悬浮层右侧那块面板（结构和流派面板一致）"""
    skills, gears = preset_lists()
    if not skills and not gears:
        return {}
    learned = {str(k).strip(): v for k, v in (ST.spells or {}).items()}
    rows = []
    done = 0
    for i, s in enumerate(skills[:8]):
        rank = None
        for k, v in learned.items():
            if k == s or (len(s) >= 2 and (s in k or k in s)):
                rank = v.get("rank") if isinstance(v, dict) else v
                break
        if rank:
            done += 1
            rows.append({"zh": s, "state": "ok", "note": "R%s" % rank, "must": i < 2})
        else:
            rows.append({"zh": s, "state": "missing", "note": "缺少", "must": i < 2})
    grows = []
    for i, g in enumerate(gears[:6]):
        worn = False
        try:
            for slot, info in (ST.gear or {}).items():
                gid = (info or {}).get("id") or (info or {}).get("affix") or ""
                if gid and (gid in g or g in (info or {}).get("name", "")):
                    worn = True
                    break
        except Exception:
            pass
        grows.append({"name": g, "state": "ok" if worn else "missing",
                      "bonus": "", "must": i < 2})
    total = len(rows) + len(grows)
    return {
        "id": "preset",
        "class": "预设",
        "name": "我的优先级",
        "title": "预设 · 我的优先级",
        "done": done,
        "total": total,
        "skills": rows,
        "gear": grows,
    }


def build_panel():
    """游戏内「流派面板」要显示的内容：这条流派要什么、我身上有什么。

    工具箱那一页和悬浮层共用同一份数据，只是画法不同。
    没选流派 / 没认出职业时返回空，悬浮层就整块不画。
    """
    spec_id = SETTINGS.get("spec") or ""
    cls = (getattr(ST, "cls", "") or "").strip().lower()
    # 预设优先：自己填过清单就画预设，没填才退回流派面板
    pre = build_preset_panel()
    if pre:
        return pre
    if not spec_id or not cls:
        return {}
    try:
        import azs_builds as B
        spec = B.get_spec(cls, spec_id)
        if not spec:
            return {}
        skills = B.skill_rows(spec, ST.spells)
        gears = B.gear_rows(spec, ST.gear)
        done, total = B.completion(skills, gears)
        cls_name = B.class_name(cls)
    except Exception:
        return {}

    def _cut(s, n):
        s = (s or "").replace("\n", " ").strip()
        return s if len(s) <= n else s[:n] + "…"

    return {
        "id": spec_id,
        "class": cls_name,
        "name": spec.get("name") or spec_id,
        # 悬浮层只有一行空间的标题：职业 + 流派
        "title": "%s · %s" % (cls_name, spec.get("name") or spec_id),
        "done": done,
        "total": total,
        "skills": [{
            "zh": r["zh"],
            "state": r["state"],
            "note": _cut(r.get("note"), 8),
            "must": bool(r.get("must")),
        } for r in skills],
        "gear": [{
            # 「命定时刻护符: 灵魂之火」这种是「装备: 词缀」的写法，
            # 浮窗一行放不下，只留装备名，词缀已经在 bonus 里说明了
            "name": _cut((r["name"] or "").split(":")[0].strip(), 9),
            "state": r["state"],
            "bonus": _cut(r.get("bonus"), 18),
            "must": bool(r.get("must")),
        } for r in gears],
    }


def spec_hit(name):
    """这张卡对应的技能，是不是当前流派点名要的。

    返回 "must"（核心）/ "opt"（可选）/ ""（跟流派无关）。

    为什么需要这个：新法术卡经常全被「封顶 30%」拉平，三张牌看着一模一样，
    光看数字根本分不出该拿哪张。这时候流派才是判据 —— 痛苦流要腐蚀术，
    火焰流要灵魂之火，拿错了数字再高也用不上。
    """
    if not name:
        return ""
    try:
        import azs_builds as B
        spec = B.get_spec(getattr(ST, "cls", "") or "", SETTINGS.get("spec") or "")
    except Exception:
        return ""
    if not spec:
        return ""
    key = (name or "").strip()
    if not key:
        return ""
    for k in spec.get("skills") or []:
        zh = (k.get("zh") or k.get("name") or "").strip()
        if not zh:
            continue
        if zh == key or (len(zh) >= 2 and (zh in key or key in zh)):
            return "must" if k.get("must") else "opt"
    return ""


def build_state():
    cfg = SETTINGS
    now = time.time()
    # 判据一：客户端在选卡界面会暂停模拟 -> AZSRUN 心跳停止
    hb_age = (now - ST.phase_ts) if ST.phase_ts else 1e9
    sim_paused = hb_age > cfg.get("pause_gap", 0.8)
    # 判据二：这张卡是刚才发出来的，不是启动时读到的陈年旧数据
    fresh = (now - ST.offer_ts) <= cfg.get("max_picker_seconds", 300)
    visible = (ST.offer is not None) and (not ST.resolved) and ST.game_seen and sim_paused and fresh
    slots = []
    if visible:
        choices = ST.offer.get("choices", [])
        results = []
        for ch in choices:
            try:
                pct, note, conf = evaluate(ch)
            except Exception as exc:  # 单张卡出错不影响其他卡
                pct, note, conf = None, "计算失败: %s" % exc, False
            note = fit_note(note)
            if pct is None:
                note_unknown(ch, note)
            # 同一张卡再按「BOSS 战只有一个目标」算一遍：
            # 小怪战里 +目标/+范围/弹射 值钱，打大怪时这些全部作废，
            # 两套数字差得多的通常是纯群体卡。
            try:
                bpct, _bnote, _bconf = evaluate(ch, solo=True)
            except Exception:
                bpct = None
            results.append({
                "name": ch.get("name", ""),
                "rarity": ch.get("rarityName", ""),
                "kind": ch.get("kind", ""),
                "text": ch.get("text") or ch.get("lines") or "",
                "pct": None if pct is None else round(pct, 1),
                "boss_pct": None if bpct is None else round(bpct, 1),
                "note": note,
                "confident": conf,
                "basis": MODEL.card_basis(ch, ability_by_name),
            })
        # 综合评价按「这张卡最能发挥的那一种战斗」算：
        # 单体特化的卡（瞄准射击）在小怪战里占比很低，但打 BOSS 很值，
        # 只看全局口径会把它判成「聊胜于无」，和下面第三行的 BOSS 数字打架。
        for r in results:
            r["eff"] = max(r["pct"] or 0.0, r["boss_pct"] or 0.0)
        known = [r["eff"] for r in results if r["pct"] is not None]
        best = max(known) if known else None
        # 流派点名要的技能优先：只要牌里有流派核心，★ 就只从它们里面出，
        # 数字再大的杂牌也抢不走这个标记
        # 预设点名要的技能优先：只要牌里有预设项，★ 就只从它们里面出
        _sk, _gr = preset_lists()
        has_pre = bool(_sk or _gr)
        for r in results:
            pr = preset_rank(r.get("name"))
            r["preset"] = pr
            if has_pre:
                r["spec"] = "must" if (pr and pr <= 2) else ("opt" if pr else "")
            else:
                r["spec"] = spec_hit(r.get("name"))
        must = [r for r in results if r["spec"] == "must"]
        # ★ 只表示「这三张里收益最高」，不再被流派核心垄断：
        # 流派归属已经由第四行（◆ / 本流派·核心）说清楚了，
        # 以前让核心卡强行拿 ★，会出现「★ 聊胜于无」这种自相矛盾的徽标。
        _ = must
        n = len(results)
        step = cfg.get("card_step", 0.1958)
        for i, r in enumerate(results):
            r["best"] = (best is not None and r["eff"] == best and r["eff"] > 0 and
                         (not must or r["spec"] == "must"))
            r["slot"] = i
            # 卡牌始终以屏幕中心对称排布，相邻中心间距固定
            r["x"] = 0.5 + (i - (n - 1) / 2.0) * step
            r["y"] = cfg["badge_y"]
            # 纯生存/收益卡（加血、减伤、金币、经验…）对 DPS 就是 0，
            # 用「—」而不是「0%」，一眼能和"有伤害但很小"区分开。
            r["util"] = (r["pct"] == 0.0) and (not r["confident"]) and (
                "非伤害" in r["note"] or "治疗/防御" in r["note"])
            # ---- 第一行：综合评价（字最大，一眼决定拿不拿）----
            r["grade"], r["grade_key"] = grade_of(r["eff"], r["confident"], r["util"])
            if r["spec"] == "must":
                r["grade"] = "◆ " + r["grade"]
            elif r["best"] and r["eff"] >= 8.0:
                r["grade"] = "★ " + r["grade"]
            # ---- 第二行：全局增伤（这把牌对总输出的提升）----
            if r["pct"] is None:
                r["pct_text"] = "?"
            elif r["util"]:
                r["pct_text"] = "—"
            elif r["pct"] <= 0.05:
                r["pct_text"] = "0%"
            else:
                r["pct_text"] = ("+%.1f%%" % r["pct"]) if r["confident"] \
                    else ("≈+%.1f%%" % r["pct"])
            # 第二行只给数值，行首的「全局」由悬浮层画（不然会出现「全局 全局 +9%」）
            r["global_line"] = r["pct_text"]
            # ---- 第三行：BOSS 增伤 ----
            r["boss"], r["boss_kind"] = boss_line(r)
            # ---- 第四行：流派归属 ----
            r["spec_line"], r["spec_kind"] = spec_line_of(
                r["spec"], bool(has_pre), r.get("basis") or "")
            r["main"] = r["grade"]      # 旧字段别名（replay_test 还在读）
            slots.append(r)

    # 为什么没显示：出问题时看一眼 brain.err 就知道卡在哪一步，
    # 不用再靠「重启试试」猜。
    if visible:
        why = ""
    elif ST.offer is None:
        why = "还没收到选卡事件"
    elif ST.resolved:
        why = "这张卡已经选完"
    elif not ST.game_seen:
        why = "没检测到游戏进程"
    elif not sim_paused:
        why = "模拟还在跑(心跳未停)"
    elif not fresh:
        why = "这张卡超过%.0f秒了" % cfg.get("max_picker_seconds", 600)
    else:
        why = "?"

    return {
        "visible": visible,
        "why": why,
        "status": "picker" if visible else ("waiting" if not ST.stats else "idle"),
        "level": (ST.offer or {}).get("level"),
        "total_dps": round(total_dps(), 1),
        "stats": {k: ST.stats.get(k) for k in
                  ("dmg", "cd", "crit", "cm", "dots", "doth", "echo", "pet")},
        "class": ST.cls,
        "run_level": ST.run.get("lvl"),
        "slots": slots,
        # ---- 流派面板要用的原始数据
        "spec": SETTINGS.get("spec") or "",
        "gear": ST.gear,
        "spells": dict((k, v.get("rank")) for k, v in (ST.spells or {}).items()),
        "meter": _meter_out(ST.meter),
        "boss_meter": _meter_out(ST.boss_meter),
        "boss_dps": round(sum(r["dps"] for r in ST.boss_meter), 1),
        # 游戏内流派面板：技能清单、首饰清单、完成度。
        # 只有选了流派且认得出职业才有内容，悬浮层据此决定画不画。
        "build": build_panel(),
        "panel_on": bool(cfg.get("panel", True)),
        "updated": now,
        "game_alive": ST.game_seen,
    }


def _meter_out(rows):
    """伤害条精简后写进 state.json，只留界面要显示的字段。"""
    return [{
        "name": r["name"],
        "dmg": round(r["dmg"]),
        "dps": round(r["dps"], 1),
        "share": round(r["share"], 4),
    } for r in (rows or [])]


# ---------------------------------------------------------------- 日志解析
EVENT_RE = re.compile(r"^rogue: (\{.*\})\s*$")
ADDON_RE = re.compile(r'addon chat \[0x06\][^"]*"((?:[^"\\]|\\.)*)"')
# 日志行自带 UTC 时间戳，用它算"新旧"，重启脚本时才不会把陈年数据当成刚发生
# 注意：行首有 ANSI 颜色码（\x1b[2m2026-…），不先剥掉的话 ^ 永远匹配不上，
#       结果所有事件都退回"读到的当下时间"，离线回放就没法定位了。
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z\s")


def line_ts(line):
    m = TS_RE.match(ANSI_RE.sub("", line))
    if not m:
        return None
    try:
        dt = datetime.datetime.fromisoformat(m.group(1))
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return None


def _maybe_new_run(run):
    """开局 / 重开时把上一局的战斗统计清掉。

    以前只清总榜，AZSBOSSDMG 那份一直留着：上一局打了 5 个 BOSS 的
    技能占比，新局一开局就顶在那儿，直到第一次打 BOSS 才被覆盖。
    现在两条判据，任一条成立就当作新的一局：

      · 本局计时 t 明显回退（上一局 900 秒，这一局从 0 秒开始）
      · 阶段从结算界面（victory / dead）回到 ready / playing
    """
    def _f(key):
        try:
            return float(run.get(key))
        except (TypeError, ValueError):
            return None

    new_t = _f("t")
    try:
        old_t = float(ST.run.get("t"))
    except (TypeError, ValueError):
        old_t = None
    ph = (run.get("ph") or "").strip()
    old_ph = (ST.run.get("ph") or "").strip()

    fresh = False
    if new_t is not None and old_t is not None and new_t + 5.0 < old_t:
        fresh = True            # 计时回退 = 新开一局
    if ph in ("ready", "playing") and old_ph in ("victory", "dead"):
        fresh = True            # 从结算界面回到战斗
    if not fresh:
        return False

    ST.meter = []
    ST.boss_meter = []
    ST.meter_ts = 0.0
    ST.boss_ts = 0.0
    return True


def handle_line(line):
    line = line.rstrip("\r\n")
    if not line:
        return False

    ts = line_ts(line)
    if ts is not None:
        ST.last_ts = ts
    else:
        ts = ST.last_ts

    m = EVENT_RE.match(line)
    if m:
        try:
            ev = json.loads(m.group(1))
        except Exception:
            return False
        typ = ev.get("type")
        if typ == "choices":
            ST.offer = ev
            ST.offer_ts = ts or time.time()
            ST.resolved = False
        elif typ == "resume":
            # resume 紧跟 choices 出现（同一帧），它表示"世界继续"，不是"玩家已选完"，
            # 因此不能用它来隐藏徽标。真正选完的标志是 picked 或心跳恢复。
            pass
        elif typ in ("picked", "start", "roster", "ready", "end"):
            ST.offer = None
            ST.resolved = True
        return True

    m = ADDON_RE.search(line)
    if m:
        try:
            payload = json.loads('"' + m.group(1) + '"')
        except Exception:
            payload = m.group(1).replace("\\t", "\t")
        if "\t" not in payload:
            return False
        tag, body = payload.split("\t", 1)
        if tag == "AZS":
            ST.stats = parse_pairs(body)
            return True
        if tag == "AZSMETER":
            rows = parse_meter(body)
            if rows:
                ST.meter = rows
                ST.meter_ts = ts or time.time()
            return True
        if tag == "AZSBOSSDMG":
            rows = parse_meter(body)
            if rows:
                ST.boss_meter = rows
                ST.boss_ts = ts or time.time()
            return True
        if tag == "AZSSPELLS":
            sp = parse_spells(body)
            if sp:
                ST.spells = sp
            return True
        if tag == "AZSM":
            low = body.upper()
            if "AMULET%3A" in low or "RING%3A" in low or "TRINKET%3A" in low:
                g = parse_gear(body)
                if g:
                    ST.gear = g
            return True
        if tag in ("AZSZONE", "AZSEND"):
            info = parse_pairs(body)
            cls = info.get("class")
            if isinstance(cls, str) and cls.strip():
                ST.cls = cls.strip().lower()
            return True
        if tag == "AZSRUN":
            run = parse_pairs(body)
            if run:
                _maybe_new_run(run)
                ST.run = run
                ph = run.get("ph")
                if ph:
                    ST.phase = ph
                ST.phase_ts = ts or time.time()
            return True
    return False


def game_running():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq AzerothSurvivors.exe", "/NH"],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return b"AzerothSurvivors.exe" in out
    except Exception:
        return True


_ALIVE = {"ts": 0.0, "v": True, "no": 0}


def game_alive():
    """带缓存的进程检测

    tasklist 一次几十毫秒，以前每 0.25 秒调四次；系统一忙就可能卡住整个读取循环，
    而读取循环一卡，日志就可能被新文件顶掉（见 main 里的文件身份检查）。
    现在 3 秒查一次，而且要连续两次查不到才算「游戏真的退了」，
    避免一次抖动就把状态清空、提示再也不出现。
    """
    now = time.time()
    if now - _ALIVE["ts"] < 3.0:
        return _ALIVE["v"]
    _ALIVE["ts"] = now
    v = game_running()
    if v:
        _ALIVE["v"] = True
        _ALIVE["no"] = 0
    else:
        _ALIVE["no"] += 1
        if _ALIVE["no"] >= 2:
            _ALIVE["v"] = False
        else:
            return True     # 第一次查不到，先当作还活着
    return _ALIVE["v"]


def reset_state():
    ST.stats = {}
    ST.meter = []
    # BOSS 那份也要清：只清总榜的话，上一局打 BOSS 的占比会一直挂在统计框里
    ST.boss_meter = []
    ST.boss_ts = 0.0
    ST.spells = {}
    ST.offer = None
    ST.offer_ts = 0.0
    ST.resolved = True
    ST.meter_ts = 0.0
    ST.phase = None
    ST.phase_ts = 0.0
    ST.last_ts = 0.0
    ST.run = {}
    ST.cls = ""


def write_state(state):
    """state.json 被悬浮层每 200ms 读一次，os.replace 撞上正在读的文件会 WinError 5，
    必须容忍失败，否则整个进程会静默退出。"""
    text = json.dumps(state, ensure_ascii=False, indent=1)
    tmp = STATE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        for _ in range(5):
            try:
                os.replace(tmp, STATE)
                return
            except OSError:
                time.sleep(0.02)
    except OSError:
        pass
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass


def log_line(msg):
    """普通诊断日志：只在关键时刻写一行，用来复盘「提示为什么不见了」"""
    try:
        with open(os.path.join(HERE, "logs", "brain.err"), "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("[%Y-%m-%d %H:%M:%S]"), msg))
    except Exception:
        pass


def _file_ident(path):
    """文件身份：换了文件（重新创建）时这个值会变，单纯变大变小抓不到"""
    try:
        st = os.stat(path)
        return (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0))
    except Exception:
        return None


def log_error():
    import traceback
    try:
        with open(os.path.join(HERE, "logs", "brain.err"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("[%Y-%m-%d %H:%M:%S] "))
            traceback.print_exc(file=fh)
            fh.write("\n")
    except Exception:
        pass


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if not os.path.exists(LOG):
        open(LOG, "wb").close()
    # 写 pid，方便 restart-tools.cmd 精确停止上一轮
    try:
        with open(os.path.join(HERE, "logs", "brain.pid"), "w") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass

    fh = open(LOG, "rb")
    size = os.path.getsize(LOG)
    if size > 4 * 1024 * 1024:
        fh.seek(size - 4 * 1024 * 1024)
    buf = ""
    last_flush = 0.0
    empty_polls = 0
    # 文件身份（设备号 + 文件索引）。run.cmd 每次启动游戏都会把 client.log
    # 重新创建一遍；如果新文件长得比旧读取位置还快，只看「大小变小了」是抓不到的，
    # 之后就会一直从错误偏移读 → 再也收不到 choices 事件 → 提示永久消失。
    ident = _file_ident(LOG)

    while True:
        try:
            # 日志被 run.cmd 重新创建（截断 / 换了个新文件）时，回到文件头重新读
            try:
                now_ident = _file_ident(LOG)
                cur = fh.tell()
                if now_ident != ident or os.path.getsize(LOG) < cur:
                    fh.close()
                    fh = open(LOG, "rb")
                    buf = ""
                    ident = now_ident
                    empty_polls = 0
                    reset_state()
                    log_line("log rotated -> reopen")
            except Exception:
                pass

            chunk = fh.read(262144)
            if chunk:
                empty_polls = 0
                buf += chunk.decode("utf-8", "replace")
                if "\n" in buf:
                    *lines, buf = buf.split("\n")
                    for ln in lines:
                        try:
                            handle_line(ln)
                        except Exception:
                            log_error()
            else:
                empty_polls += 1

            now = time.time()
            if now - last_flush >= 0.25:
                last_flush = now
                reload_settings()
                alive = game_alive()
                if not ST.game_seen and alive:
                    ST.game_seen = True
                if ST.game_seen and empty_polls > 120 and not alive:
                    # 游戏退出 -> 清空并待命，下一次启动不用重启本脚本
                    ST.game_seen = False
                    reset_state()
                    write_state({"visible": False, "status": "closed", "slots": [],
                                 "level": None, "total_dps": 0.0, "stats": {},
                                 "updated": now, "game_alive": False})
                else:
                    write_state(build_state())
        except Exception:
            # 任何意外都只记日志，绝不让后台进程静默退出
            log_error()
            time.sleep(0.5)

        time.sleep(0.08)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        log_error()
