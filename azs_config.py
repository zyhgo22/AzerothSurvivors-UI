# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 配置读写。

设计上的两条硬规矩：

1. **config.json 是唯一事实来源。**
   以前所有旋钮都塞在 settings.json 里，界面上改一项、手改一项，很容易互相覆盖。
   现在界面只读写 config.json；settings.json 由本模块「派生」出来给 brain.py 用，
   人不用再手改它（但手改也不会崩，下次保存会被覆盖）。

2. **任何一次保存都要能回滚。**
   先写 .tmp 再原子替换，且旧文件留一份 .bak。
   用户把界面点到奇怪状态时，把 .bak 改回 .json 就能救回来。
"""
import copy
import json
import os
import time

import azs_paths as P

CONFIG_PATH = P.app_path(P.CONFIG_NAME)
SETTINGS_PATH = P.app_path(P.SETTINGS_NAME)
# 工具箱 → 悬浮层 的控制通道。
# 为什么不让 toolbox 直接改 state.json：那份文件由 brain.py 每 0.25 秒整份覆盖，
# 写进去的开关活不过一个心跳。所以另开一个 control.json，两边各写各的。
CONTROL_PATH = P.app_path("control.json")

SCHEMA = 3

# 职业 → 中文名（界面上给用户看）
CLASSES = [
    ("paladin", "圣骑士"),
    ("warlock", "术士"),
    ("priest", "牧师"),
    ("warrior", "战士"),
    ("hunter", "猎人"),
    ("rogue", "潜行者"),
    ("shaman", "萨满"),
    ("mage", "法师"),
    ("druid", "德鲁伊"),
    ("deathknight", "死亡骑士"),
]
CLASS_CN = dict(CLASSES)

# 天赋开关：键 → (界面标题, 说明)
TALENT_ITEMS = [
    ("consume_magic", "吞噬魔法（术士宠物）",
     "点了之后，DoT 持续时间卡才有收益：宠物会按剩余时间直接结算伤害。"),
    ("echo_cascade", "回声连锁（奥法终局）",
     "回声从 1+E 变成 1+E+E²+E³，回声卡的收益会明显变高。"),
]

DEFAULT_PROFILE = {
    "class": "paladin",
    "talents": {
        "consume_magic": False,
        "echo_cascade": False,
        "dot_stack_bonus": 0,
    },
    "class_talents": {},
    "extra_target_ratio": {},
    "dot_lifetime": 4.0,
    "consume_interval": 6.0,
}

DEFAULTS = {
    "schema": SCHEMA,
    "installed_version": P.VERSION,
    "game": {
        "dir": "",              # 空 = 自动探测
        "auto_launch": True,    # 点「开始」时顺带把游戏拉起来
    },
    "overlay": {
        "enabled": True,
        "visible": True,
        "font_scale": 1.0,
        "badge_width": 220,
        "badge_height": 44,
        "badge_y": 0.222,
        "card_step": 0.1958,
        "opacity": 0.95,
        # 游戏内「流派面板」：右侧的小黑板，只在选卡时冒出来
        "panel": True,
        # 游戏内「伤害统计」：左侧常驻，每个技能一行，伤害 + 占比
        "dps": True,
        # 当前选中的流派（builds.json 里的 id），空字符串表示不启用
        "spec": "",
    },
    "hotkey": {
        "enabled": True,
        "toggle_overlay": "F8",
        "toggle_panel": "F9",
    },
    "profile": {
        "active": "默认方案",
        "items": {"默认方案": copy.deepcopy(DEFAULT_PROFILE)},
    },
    "update": {
        "manifest_url": "",
        "auto_check": True,
        "last_check": 0.0,
        "skipped_version": "",
        "channel": "github",
    },
    "window": {
        "width": 1200,
        "height": 780,
    },
    "first_run_done": False,
}


# --------------------------------------------------------------------- 基础 IO
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_json(path, data, keep_backup=True):
    """原子写：.tmp → replace，成功后再留一份 .bak。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except Exception:
            pass
    if keep_backup and os.path.isfile(path):
        try:
            bak = path + ".bak"
            if os.path.isfile(bak):
                os.remove(bak)
            os.replace(path, bak)
        except Exception:
            pass
    os.replace(tmp, path)


def deep_merge(base, override):
    """把 override 合进 base 的副本：字典递归合并，其它类型直接覆盖。"""
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# --------------------------------------------------------------------- 应用类
class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self.loaded_from = "默认值"
        self.load()

    # ------------------------------------------------------------- 读
    def load(self):
        raw = _read_json(CONFIG_PATH)
        if isinstance(raw, dict):
            self.data = deep_merge(DEFAULTS, raw)
            self.loaded_from = CONFIG_PATH
        else:
            # 第一次跑：如果旁边有老版本的 settings.json，尽量把能认的字段搬过来
            self.data = self._migrate_legacy(copy.deepcopy(DEFAULTS))
            self.loaded_from = "新建"
        self._normalise()

    def _migrate_legacy(self, cfg):
        old = _read_json(SETTINGS_PATH)
        if not isinstance(old, dict):
            return cfg
        prof = cfg["profile"]["items"]["默认方案"]
        for key in ("talents", "class_talents", "extra_target_ratio",
                    "dot_lifetime", "consume_interval"):
            if key in old:
                prof[key] = copy.deepcopy(old[key])
        for key in ("badge_y", "badge_width", "badge_height", "card_step"):
            if key in old:
                cfg["overlay"][key] = old[key]
        cfg["_migrated_from_settings"] = True
        return cfg

    def _normalise(self):
        """把明显不合理的值夹回可用范围，避免界面被写坏后打不开。"""
        d = self.data
        ov = d.setdefault("overlay", {})
        ov["font_scale"] = _clamp(_num(ov.get("font_scale"), 1.0), 0.6, 2.0)
        ov["badge_width"] = _clamp(_num(ov.get("badge_width"), 220), 120, 520)
        ov["badge_height"] = _clamp(_num(ov.get("badge_height"), 44), 24, 120)
        ov["badge_y"] = _clamp(_num(ov.get("badge_y"), 0.222), 0.02, 0.9)
        ov["card_step"] = _clamp(_num(ov.get("card_step"), 0.1958), 0.05, 0.6)
        ov["opacity"] = _clamp(_num(ov.get("opacity"), 0.95), 0.25, 1.0)

        prof_items = d.setdefault("profile", {}).setdefault("items", {})
        if not isinstance(prof_items, dict) or not prof_items:
            prof_items = {"默认方案": copy.deepcopy(DEFAULT_PROFILE)}
            d["profile"]["items"] = prof_items
        for name, prof in list(prof_items.items()):
            if not isinstance(prof, dict):
                prof_items[name] = copy.deepcopy(DEFAULT_PROFILE)
                prof = prof_items[name]
            merged = deep_merge(DEFAULT_PROFILE, prof)
            if merged.get("class") not in CLASS_CN:
                merged["class"] = "paladin"
            merged["dot_lifetime"] = _clamp(_num(merged.get("dot_lifetime"), 4.0), 0.5, 60.0)
            merged["consume_interval"] = _clamp(
                _num(merged.get("consume_interval"), 6.0), 0.5, 60.0)
            if not isinstance(merged.get("extra_target_ratio"), dict):
                merged["extra_target_ratio"] = {}
            prof_items[name] = merged
        active = d["profile"].get("active")
        if active not in prof_items:
            d["profile"]["active"] = sorted(prof_items)[0]

    # ------------------------------------------------------------- 写
    def save(self, sync_settings=True):
        self.data["schema"] = SCHEMA
        self.data["installed_version"] = self.data.get("installed_version") or P.VERSION
        _write_json(CONFIG_PATH, self.data)
        if sync_settings:
            self.sync_settings()

    def sync_settings(self):
        """把 config 里跟插件相关的部分派生成 brain.py 认得的 settings.json。"""
        d = self.data
        ov = d["overlay"]
        prof = self.profile()
        payload = {
            "card_step": ov["card_step"],
            "badge_y": ov["badge_y"],
            "badge_width": int(ov["badge_width"]),
            "badge_height": int(ov["badge_height"]),
            "pause_gap": 0.8,
            "max_picker_seconds": 600,
            "talents": prof["talents"],
            "class_talents": prof["class_talents"],
            "consume_interval": prof["consume_interval"],
            "dot_lifetime": prof["dot_lifetime"],
            "extra_target_ratio": prof["extra_target_ratio"],
            "overlay_opacity": ov["opacity"],
            "overlay_font_scale": ov["font_scale"],
            "overlay_visible": bool(ov["visible"]),
            # 游戏内流派面板的开关（悬浮层右侧那块）
            "panel": bool(ov.get("panel", True)),
            # 当前选中的流派（builds.json 里的 id），空字符串表示不启用
            "spec": ov.get("spec") or "",
        }
        try:
            _write_json(SETTINGS_PATH, payload, keep_backup=False)
        except Exception:
            pass
        return payload

    # ------------------------------------------------------------- 便捷访问
    def profile(self, name=None):
        name = name or self.data["profile"]["active"]
        items = self.data["profile"]["items"]
        if name not in items:
            name = sorted(items)[0]
        return items[name]

    def profile_names(self):
        return sorted(self.data["profile"]["items"].keys())

    def set_active_profile(self, name):
        if name in self.data["profile"]["items"]:
            self.data["profile"]["active"] = name
            return True
        return False

    def add_profile(self, name, copy_from=None):
        name = (name or "").strip()
        if not name:
            return False, "名字不能为空"
        if name in self.data["profile"]["items"]:
            return False, "已经有同名方案了"
        base = self.profile(copy_from) if copy_from else DEFAULT_PROFILE
        self.data["profile"]["items"][name] = copy.deepcopy(base)
        self.data["profile"]["active"] = name
        return True, ""

    def rename_profile(self, old, new):
        new = (new or "").strip()
        items = self.data["profile"]["items"]
        if old not in items:
            return False, "找不到这个方案"
        if not new:
            return False, "名字不能为空"
        if new in items and new != old:
            return False, "已经有同名方案了"
        items[new] = items.pop(old)
        if self.data["profile"]["active"] == old:
            self.data["profile"]["active"] = new
        return True, ""

    def delete_profile(self, name):
        items = self.data["profile"]["items"]
        if name not in items:
            return False, "找不到这个方案"
        if len(items) <= 1:
            return False, "至少要留一个方案"
        items.pop(name)
        if self.data["profile"]["active"] == name:
            self.data["profile"]["active"] = sorted(items)[0]
        return True, ""

    # ------------------------------------------------------------- 游戏目录
    def game_dir(self):
        d = self.data.get("game", {}).get("dir") or ""
        if d and os.path.isfile(os.path.join(d, P.GAME_EXE_NAME)):
            return d
        return P.detect_game_dir() or ""

    def set_game_dir(self, path):
        self.data["game"]["dir"] = os.path.abspath(path) if path else ""

    # ------------------------------------------------------------- 控制通道
    def write_control(self, visible=None, extra=None):
        """把悬浮层的实时开关写进 control.json（overlay.ps1 每 200ms 读一次）。

        颜色/字号这类纯外观项一起带上，改完立刻见效，不用重启悬浮层。
        """
        ov = self.data["overlay"]
        if visible is not None:
            ov["visible"] = bool(visible)
        payload = {
            "visible": bool(ov.get("visible", True)),
            "enabled": bool(ov.get("enabled", True)),
            "font_scale": float(ov.get("font_scale", 1.0)),
            "badge_width": int(ov.get("badge_width", 220)),
            "badge_height": int(ov.get("badge_height", 44)),
            "opacity": float(ov.get("opacity", 0.95)),
            "panel": bool(ov.get("panel", True)),
            "dps": bool(ov.get("dps", True)),
            "updated": time.time(),
        }
        if isinstance(extra, dict):
            payload.update(extra)
        try:
            _write_json(CONTROL_PATH, payload, keep_backup=False)
        except Exception:
            pass
        return payload

    def overlay_visible(self):
        return bool(self.data["overlay"].get("visible", True))

    @property
    def overlay(self):
        return self.data["overlay"]

    @property
    def hotkey(self):
        return self.data["hotkey"]

    @property
    def update_cfg(self):
        return self.data["update"]

    def touch_update_check(self):
        self.data["update"]["last_check"] = time.time()


# --------------------------------------------------------------------- 小工具
def _num(v, default):
    try:
        return float(v)
    except Exception:
        return float(default)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


_CFG = None


def get():
    """进程内单例，界面各处共用一份，避免互相覆盖。"""
    global _CFG
    if _CFG is None:
        _CFG = Config()
    return _CFG


def reload():
    global _CFG
    _CFG = Config()
    return _CFG
