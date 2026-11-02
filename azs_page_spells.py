# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 技能数值页。

把 spellpanel.py 那套读写能力搬进工具箱，但做了两点改造：

1. **读取放到后台线程。** 第一次读要定位技能表（扫 exe 里的特征），可能花几秒；
   放在界面线程里会整窗假死，用户以为崩了。现在点刷新立刻有"正在读取…"的反馈。
2. **区分「当前值」和「游戏原值」。** 原值来自这次读取的快照，改了多少一目了然；
   应用后快照会刷新，所以"原值"始终指游戏本来的数值，不是上一次的改动。

所有写入都走 spellcore.do_apply，它会自动备份，随时能一键还原。
"""
import threading
import time

import dearpygui.dearpygui as dpg

import azs_theme as T
from azs_pages import _cell_theme

FIELD_ORDER = ["dmg", "cd", "range", "radius", "cnt", "dur"]
FIELD_CN = {"dmg": "伤害", "cd": "冷却", "range": "施法距离",
            "radius": "范围半径", "dur": "持续", "cnt": "弹道"}
FIELD_HINT = {
    "dmg": "每次命中 / 每跳的基础伤害",
    "cd": "基础冷却，实战还要乘一次冷却缩减",
    "range": "最远释放距离；自身周围技保持 0",
    "radius": "命中范围大小",
    "cnt": "多目标数（多重射击 3、奥术飞弹 4）",
    "dur": "地面 / DoT 持续秒数；瞬发技为 0",
}
LIST_W = 320
PANEL_H = 316


class SpellsPage:
    def __init__(self, tk):
        self.tk = tk
        self.records = []
        self.base = {}          # (off, field) -> 游戏原值
        self.view = []          # 列表行 -> records 下标
        self.row_tags = []
        self.cur = None
        self.target = "mem"
        self.loading = False
        self._result = None
        self._thread = None
        self._msg = ""
        self._msg_color = T.C_TEXT_DIM
        self._ready = False

    # ------------------------------------------------------------- 构建
    def build(self, parent, tag="page_spells"):
        self.page_tag = tag
        with dpg.child_window(tag=tag, parent=parent, border=False,
                              width=-1, show=False):
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=26)
                with dpg.group():
                    t = dpg.add_text("技能数值")
                    T.set_font(t, "bold")
                    dpg.add_spacer(height=2)
                    s = dpg.add_text(
                        "改的是游戏本体的技能表，改完立刻生效（内存）或下次启动生效（文件）。",
                        color=T.C_TEXT_DIM, wrap=820)
                    T.set_font(s, "small")
            dpg.add_spacer(height=10)

            # ---- 工具栏 ----
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=26)
                dpg.add_text("读取自", color=T.C_TEXT_DIM)
                dpg.add_radio_button(items=["游戏内存", "exe 文件"],
                                     tag="sp_tgt", horizontal=True,
                                     default_value="游戏内存",
                                     callback=self.on_target)
                dpg.add_spacer(width=10)
                b = dpg.add_button(label="   刷 新 读 取   ", height=30,
                                   callback=self.on_refresh)
                dpg.bind_item_theme(b, T.accent_button_theme())
                dpg.add_spacer(width=10)
                dpg.add_button(label="  还原全部改动  ", height=30,
                               callback=self.on_restore)
                dpg.add_spacer(width=16)
                dpg.add_text("", tag="sp_status", color=T.C_TEXT_DIM)
            dpg.add_spacer(height=10)

            # ---- 主体 ----
            # 两栏走表格：直接放 group(horizontal=True) 里的话，
            # 右边 width=-1 会按整个父宽算，卡片被顶到窗口外面去。
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           width=-1, pad_outerX=True,
                           borders_innerV=False, borders_innerH=False,
                           borders_outerH=False, borders_outerV=False,
                           scrollX=False, scrollY=False) as tb:
                dpg.bind_item_theme(tb, _cell_theme(20))
                dpg.add_table_column(init_width_or_weight=0.86)
                dpg.add_table_column(init_width_or_weight=1.0)
                with dpg.table_row():
                    with dpg.table_cell():
                        with dpg.child_window(width=-1, height=PANEL_H,
                                              border=True):
                            dpg.add_spacer(height=6)
                            with dpg.group(horizontal=True):
                                dpg.add_spacer(width=10)
                                dpg.add_input_text(tag="sp_search",
                                                   width=270,
                                                   hint="搜索 中文 / 英文 / spellid",
                                                   callback=self.rebuild_list)
                            dpg.add_spacer(height=6)
                            with dpg.child_window(tag="sp_list", width=-1,
                                                  height=-1, border=False):
                                dpg.add_text("      还没读取",
                                             color=T.C_TEXT_FAINT,
                                             tag="sp_list_empty")
                    with dpg.table_cell():
                        with dpg.child_window(width=-1, height=PANEL_H,
                                              border=True):
                            self._build_editor()

            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=26)
                dpg.add_text("批量套用", color=T.C_TEXT_DIM)
                dpg.add_spacer(width=10)
                dpg.add_text("伤害 ×", color=T.C_TEXT_DIM)
                dpg.add_input_float(tag="sp_mul_dmg", width=70,
                                    default_value=1.0, step=0, format="%.2f")
                dpg.add_spacer(width=6)
                dpg.add_text("冷却 ×", color=T.C_TEXT_DIM)
                dpg.add_input_float(tag="sp_mul_cd", width=70,
                                    default_value=1.0, step=0, format="%.2f")
                dpg.add_spacer(width=6)
                dpg.add_text("弹道 +", color=T.C_TEXT_DIM)
                dpg.add_input_int(tag="sp_add_cnt", width=70,
                                  default_value=0, step=0)
                dpg.add_spacer(width=10)
                dpg.add_button(label="  套 用 到 全 部  ", height=30,
                               callback=self.on_global)
        self.refresh()
        return tag

    def _build_editor(self):
        # 按钮放最上面：卡片高度受窗口约束，放底部会被裁掉看不见
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            n = dpg.add_text("未选中", tag="sp_name", color=T.C_GOLD)
            T.set_font(n, "bold")
            dpg.add_spacer(width=10)
            b = dpg.add_button(label="  应 用  ", height=28,
                               callback=self.on_apply_one)
            dpg.bind_item_theme(b, T.accent_button_theme())
            dpg.add_spacer(width=6)
            dpg.add_button(label="  恢复  ", height=28,
                           callback=self.on_revert_one)
        with dpg.group(horizontal=True):
            sub = dpg.add_text("", tag="sp_sub", color=T.C_TEXT_FAINT)
            T.set_font(sub, "small")
        dpg.add_spacer(height=6)

        with dpg.table(header_row=True, policy=dpg.mvTable_SizingStretchProp,
                       borders_innerV=False, row_background=False,
                       width=-1, pad_outerX=True):
            dpg.add_table_column(label="字段", width_fixed=True,
                                 init_width_or_weight=88)
            dpg.add_table_column(label="当前值", init_width_or_weight=1.0)
            dpg.add_table_column(label="游戏原值", width_fixed=True,
                                 init_width_or_weight=86)
            cols = {"dmg": "伤害", "cd": "冷却", "range": "施法距离",
                    "radius": "范围半径", "cnt": "弹道", "dur": "持续"}
            order = ["dmg", "cd", "range", "radius", "cnt", "dur"]
            for f in order:
                with dpg.table_row():
                    dpg.add_text(cols[f], color=T.C_TEXT_DIM)
                    if f == "cnt":
                        dpg.add_input_int(tag="sp_in_" + f, width=-1,
                                          step=0, default_value=1)
                    else:
                        dpg.add_input_float(tag="sp_in_" + f, width=-1,
                                            step=0, format="%.3f",
                                            default_value=0.0)
                    dpg.add_text("", tag="sp_old_" + f, color=T.C_TEXT_FAINT)

        with dpg.group(horizontal=True):
            dpg.add_spacer(width=14)
            dpg.add_text("", tag="sp_dirty", color=T.C_AMBER, wrap=520)


    # ------------------------------------------------------------- 读取
    def on_target(self, sender=None, app_data=None, user_data=None):
        self.target = "mem" if dpg.get_value("sp_tgt") == "游戏内存" else "file"
        self.refresh()

    def on_refresh(self, sender=None, app_data=None, user_data=None):
        self.refresh()

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        self._result = None
        dpg.set_value("sp_status", "正在读取…")
        dpg.configure_item("sp_status", color=T.C_AMBER)
        want = self.target

        def work():
            try:
                import spellcore as core
                res = core.do_read(want)
                if not res.get("ok") and want == "mem":
                    res = core.do_read("file")
                    res["_fell_back"] = True
            except Exception as e:
                res = {"ok": False, "reason": "读取出错：%s" % e}
            self._result = res

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def poll(self):
        """主循环每帧调一次，把后台读到的结果搬到界面上。"""
        if not self.loading or self._result is None:
            return
        self.loading = False
        res = self._result
        self._result = None
        if not res.get("ok"):
            dpg.set_value("sp_status", "读取失败：%s" % res.get("reason", "?"))
            dpg.configure_item("sp_status", color=T.C_RED)
            return
        if res.get("_fell_back"):
            dpg.set_value("sp_tgt", "exe 文件")
            self.target = "file"
        self.records = res["records"]
        self.base = {}
        for r in self.records:
            for f in FIELD_ORDER:
                self.base[(r["off"], f)] = float(r[f])
        where = ("游戏内存 pid %s" % res.get("pid")) if res.get("target") == "mem" \
            else "exe 文件"
        dpg.set_value("sp_status", "已读取 %d 个技能 ← %s"
                      % (len(self.records), where))
        dpg.configure_item("sp_status", color=T.C_GREEN)
        self._ready = True
        self.cur = None
        self.rebuild_list()

    # ------------------------------------------------------------- 列表
    def rebuild_list(self, sender=None, app_data=None, user_data=None):
        if not self._ready:
            return
        kw = (dpg.get_value("sp_search") or "").strip().lower()
        view = []
        for i, r in enumerate(self.records):
            hay = "%s %s %s" % (r.get("zh") or "", r.get("key") or "", r.get("sid"))
            if kw and kw not in hay.lower():
                continue
            view.append(i)
        self.view = view
        dpg.delete_item("sp_list", children_only=True)
        self.row_tags = []
        if not view:
            dpg.add_text("      没有匹配的技能", parent="sp_list",
                         color=T.C_TEXT_FAINT)
            return
        for i in view:
            r = self.records[i]
            tag = "sp_row_%d" % i
            self.row_tags.append((tag, i))
            dpg.add_selectable(
                label="%03d  %s" % (i + 1, r.get("zh") or r.get("key") or "未命名"),
                tag=tag, parent="sp_list", default_value=False,
                callback=self.on_row, user_data=i)
        self.select_index(view[0])
        dpg.set_y_scroll("sp_list", 0)

    def on_row(self, sender=None, app_data=None, user_data=None):
        if user_data is None:
            return
        self.select_index(int(user_data))

    def _mark(self, idx):
        for tag, i in self.row_tags:
            try:
                dpg.set_value(tag, i == idx)
            except Exception:
                pass

    # ------------------------------------------------------------- 选中
    def select_index(self, idx):
        if idx < 0 or idx >= len(self.records):
            return
        r = self.records[idx]
        self.cur = r
        dpg.set_value("sp_name", r.get("zh") or r.get("key") or "未命名")
        dpg.set_value("sp_sub", "%s   ·   spellid %d   ·   偏移 0x%X"
                      % (r.get("key") or "?", r.get("sid", 0), r.get("off", 0)))
        for f in FIELD_ORDER:
            dpg.set_value("sp_in_" + f, float(r[f]))
        for f in FIELD_ORDER:
            b = self.base.get((r["off"], f))
            dpg.set_value("sp_old_" + f, self._fmt(b) if b is not None else "—")
        self._mark(idx)
        self.show_dirty()

    @staticmethod
    def _fmt(v):
        if v is None:
            return "—"
        s = ("%.3f" % float(v)).rstrip("0").rstrip(".")
        return s or "0"

    def show_dirty(self):
        if not self.cur:
            return
        r = self.cur
        diffs = []
        for f in FIELD_ORDER:
            new = float(dpg.get_value("sp_in_" + f))
            old = self.base.get((r["off"], f), new)
            if abs(new - old) > 5e-4:
                diffs.append("%s %s → %s" % (FIELD_CN[f], self._fmt(old),
                                             self._fmt(new)))
        if diffs:
            dpg.set_value("sp_dirty", "待应用：" + "，".join(diffs))
        else:
            dpg.set_value("sp_dirty", "")

    # ------------------------------------------------------------- 写入
    def _collect_one(self):
        if not self.cur:
            return []
        r = self.cur
        out = []
        for f in FIELD_ORDER:
            new = float(dpg.get_value("sp_in_" + f))
            old = self.base.get((r["off"], f))
            if old is None:
                continue
            if abs(new - old) > 5e-4:
                out.append((r["off"], f, new))
        return out

    def _apply(self, changes, note=""):
        if not changes:
            self._log("没有改动", T.C_AMBER)
            return False
        try:
            import spellcore as core
            res = core.do_apply(self.target, changes)
        except Exception as e:
            self._log("写入失败：%s" % e, T.C_RED)
            return False
        if res.get("ok"):
            where = "内存" if res.get("target") == "mem" else "exe 文件"
            self._log("%s写入 %d 项 → %s（已备份 %d 项）"
                      % (note + "：" if note else "", res.get("applied", 0),
                         where, res.get("backup", 0)), T.C_GREEN)
            self.refresh()
            return True
        self._log("失败：%s" % (res.get("reason") or "；".join(res.get("errors") or [])),
                  T.C_RED)
        return False

    def on_apply_one(self, sender=None, app_data=None, user_data=None):
        ch = self._collect_one()
        if not ch:
            self._log("这个技能还没改动", T.C_AMBER)
            return
        name = self.cur.get("zh") or self.cur.get("key") or "技能"
        self._apply(ch, name)

    def on_revert_one(self, sender=None, app_data=None, user_data=None):
        if not self.cur:
            return
        try:
            import spellcore as core
            res = core.do_revert(self.target, [self.cur["off"]])
        except Exception as e:
            self._log("还原失败：%s" % e, T.C_RED)
            return
        rev = res.get("reverted") or []
        if not rev:
            self._log("这个技能没有改过的记录", T.C_AMBER)
            return
        txt = "，".join("%s→%s" % (FIELD_CN.get(f, f), self._fmt(v)) for f, v in rev)
        self._log("已还原：" + txt, T.C_GREEN)
        self.refresh()

    def on_global(self, sender=None, app_data=None, user_data=None):
        if not self._ready:
            self._log("先点刷新读一次", T.C_AMBER)
            return
        dm = float(dpg.get_value("sp_mul_dmg"))
        cm = float(dpg.get_value("sp_mul_cd"))
        ca = int(dpg.get_value("sp_add_cnt"))
        if dm == 1.0 and cm == 1.0 and ca == 0:
            self._log("倍率都是 1 / 0，没有可套用的", T.C_AMBER)
            return
        changes = []
        for r in self.records:
            off = r["off"]
            if dm != 1.0:
                changes.append((off, "dmg", self.base[(off, "dmg")] * dm))
            if cm != 1.0:
                changes.append((off, "cd", self.base[(off, "cd")] * cm))
            if ca != 0:
                changes.append((off, "cnt", self.base[(off, "cnt")] + ca))
        self._log("套用：伤害×%s 冷却×%s 弹道+%d（%d 项）"
                  % (dm, cm, ca, len(changes)), T.C_TEXT)
        self._apply(changes, "全局倍率")

    def on_restore(self, sender=None, app_data=None, user_data=None):
        try:
            import spellcore as core
            res = core.do_restore(self.target)
        except Exception as e:
            self._log("还原失败：%s" % e, T.C_RED)
            return
        if res.get("ok"):
            self._log("已还原 %d 个字段" % res.get("restored", 0), T.C_GREEN)
            self.refresh()
        else:
            self._log("还原失败：%s" % res.get("reason", ""), T.C_RED)

    def _log(self, msg, color=None):
        """结果统一显示在顶栏状态条，页面里不再占一行。"""
        try:
            self.tk._toast_msg(msg)
        except Exception:
            pass
