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


def estimate_new_spell(choice):
    """新技能：用卡面伤害数值 + 技能表冷却，粗估 DPS 占比

    卡面伤害是该技能当前等级的值，成长节奏因人而异，所以结果只能当参考
    （界面显示 ≈），并封顶 30%，避免压过所有强化卡。
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
            stacks = min(d / max(0.2, cd), cap)
            dps = total / max(0.5, d) * tgt * stacks
            if tdps <= 1.0:
                return None, "尚无伤害统计", False
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

    targets = float(ab.get("targets") or 1)
    bonus = 0.0
    for mb in re.finditer(r"\+\s*(\d+(?:\.\d+)?)\s*%\s*伤害", text):
        bonus = max(bonus, float(mb.group(1)))

    cd = ab.get("cooldown") or 4.0
    cd_stat = ST.stats.get("cd", 1.0) or 1.0
    dmg = total if total else per_hit
    dps = dmg * targets / max(0.3, cd * cd_stat) * (1.0 + bonus / 100.0)

    if tdps <= 1.0:
        return None, "尚无伤害统计", False
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


def estimate_killproc(choice):
    """击杀/周期触发型技能（救赎、灵魂之火）：按触发频率估算，而不是冷却"""
    text = choice.get("text") or ""
    name = (choice.get("name") or "").strip()
    hits = re.findall(r"(?:造成|受到)\s*(\d+(?:\.\d+)?)\s*点", text)
    if not hits:
        return None, "特殊效果·无伤害数字", False
    per_hit = max(float(h) for h in hits)
    ab = ability_by_name(name) or {}
    targets = float(ab.get("targets") or 1)

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
        return None, "尚无伤害统计", False
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
        talents=talents,
        settings=SETTINGS,
        gear=ST.gear,
    )


def evaluate(choice):
    """返回 (pct, note, confident)"""
    name = (choice.get("name") or "").strip()
    lines = choice.get("lines") or ""
    text = choice.get("text") or lines
    kind = choice.get("kind") or ""

    # 0) 新技能：卡面上的 +X% 是它自己的强度，不能当成全局增益
    if kind == "new":
        if re.search(r"你击杀|击杀的敌人|击杀会|击中时|碎片|每秒最多\s*\d+", text):
            pct, note, conf = estimate_killproc(choice)
            if pct is not None:
                return pct, note, conf
        pct, note, conf = estimate_new_spell(choice)
        if pct is None:
            return model_special(name, text)
        return pct, note, conf

    pct, note, conf, dmg_related = MODEL.evaluate_choice(choice, build_ctx(), ability_by_name)

    if pct is None:
        # 「你击杀的敌人有12%几率爆发」这类击杀触发卡：按触发频率估
        if re.search(r"你击杀|击杀的敌人|击杀会|爆发", text):
            kp, kn, kc = estimate_killproc(choice)
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


def build_panel():
    """游戏内「流派面板」要显示的内容：这条流派要什么、我身上有什么。

    工具箱那一页和悬浮层共用同一份数据，只是画法不同。
    没选流派 / 没认出职业时返回空，悬浮层就整块不画。
    """
    spec_id = (SETTINGS.get("spec") or "").strip()
    cls = (getattr(ST, "cls", "") or "").strip().lower()
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
            results.append({
                "name": ch.get("name", ""),
                "rarity": ch.get("rarityName", ""),
                "kind": ch.get("kind", ""),
                "pct": None if pct is None else round(pct, 1),
                "note": note,
                "confident": conf,
            })
        known = [r["pct"] for r in results if r["pct"] is not None]
        best = max(known) if known else None
        # 流派点名要的技能优先：只要牌里有流派核心，★ 就只从它们里面出，
        # 数字再大的杂牌也抢不走这个标记
        for r in results:
            r["spec"] = spec_hit(r.get("name"))
        must = [r for r in results if r["spec"] == "must"]
        if must:
            cand = [r["pct"] for r in must if r["pct"] is not None]
            best = max(cand) if cand else None
        n = len(results)
        step = cfg.get("card_step", 0.1958)
        for i, r in enumerate(results):
            r["best"] = (best is not None and r["pct"] == best and r["pct"] > 0 and
                         (not must or r["spec"] == "must"))
            r["slot"] = i
            # 卡牌始终以屏幕中心对称排布，相邻中心间距固定
            r["x"] = 0.5 + (i - (n - 1) / 2.0) * step
            r["y"] = cfg["badge_y"]
            # 纯生存/收益卡（加血、减伤、金币、经验…）对 DPS 就是 0，
            # 用「—」而不是「0%」，一眼能和"有伤害但很小"区分开。
            r["util"] = (r["pct"] == 0.0) and (not r["confident"]) and (
                "非伤害" in r["note"] or "治疗/防御" in r["note"])
            if r["pct"] is None:
                r["main"] = "?"
            elif r["util"]:
                r["main"] = "—"
            elif r["pct"] <= 0.05:
                r["main"] = "0%"
            else:
                r["main"] = ("+%.1f%%" % r["pct"]) if r["confident"] else ("≈+%.1f%%" % r["pct"])
            if r["spec"] == "must" and r["pct"] is not None and not r["util"]:
                # 流派核心：标在数字前面，一眼能看出「这张是冲我来的」
                r["main"] = "★ 流派 " + r["main"]
            elif r["best"]:
                r["main"] = "★ " + r["main"]
            slots.append(r)

    return {
        "visible": visible,
        "status": "picker" if visible else ("waiting" if not ST.stats else "idle"),
        "level": (ST.offer or {}).get("level"),
        "total_dps": round(total_dps(), 1),
        "stats": {k: ST.stats.get(k) for k in ("dmg", "cd", "crit", "cm", "dots", "doth", "echo")},
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


def reset_state():
    ST.stats = {}
    ST.meter = []
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

    while True:
        try:
            # 日志被 run.cmd 重新创建（截断）时，回到文件头重新读
            try:
                if os.path.getsize(LOG) < fh.tell():
                    fh.close()
                    fh = open(LOG, "rb")
                    buf = ""
                    reset_state()
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
                if not ST.game_seen and game_running():
                    ST.game_seen = True
                if ST.game_seen and empty_polls > 40 and not game_running():
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
