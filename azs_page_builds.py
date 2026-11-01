# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 流派页。

这一页回答一个问题：**我现在走的这条流派，还差什么？**

数据分两半：
  * 流派该有什么 —— builds.json（从客户端天赋树整理的，静态）
  * 我现在有什么 —— state.json 里的技能等级和身上首饰（brain 每 0.25 秒刷新）

两边一对，缺的标「缺少」，有的标等级，就得到一张待办清单。
右下角同时给「整体 DPS」和「对精英 DPS」两列：同一场战斗里，
打精英的 DPS 通常明显高于平均值，看这一列才知道自己打大怪到底行不行。
"""
import json

import dearpygui.dearpygui as dpg

import azs_builds as B
import azs_paths as P
import azs_theme as T

LEFT_W = 424
PAD = 18


def read_state():
    try:
        with open(P.app_path("state.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _pct(v, total):
    if not total:
        return "—"
    return "%.1f%%" % (v / total * 100.0)


class BuildsPage:
    def __init__(self, tk):
        self.tk = tk
        self.spec_id = None
        self._sig = None
        self._tabs = {}
        self._accent = None
        self._tab_spec = None
        self._ready = False
        self._dps_tags = {}

    # ------------------------------------------------------------- 构建
    def build(self, parent, tag="page_builds"):
        self.page_tag = tag
        with dpg.child_window(tag=tag, parent=parent, border=False,
                              width=-1, show=False):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=PAD)
                t = dpg.add_text("流派")
                T.set_font(t, "huge")
                dpg.add_spacer(width=14)
                s = dpg.add_text(
                    "选一条路，插件就知道该给你推什么。列表放不下时可以继续往下滚。",
                    color=T.C_TEXT_DIM)
                T.set_font(s, "small")
            dpg.add_spacer(height=6)

            # 流派切换按钮
            with dpg.group(horizontal=True, tag="build_tabs"):
                dpg.add_spacer(width=PAD)
            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_spacer(width=PAD)
                with dpg.child_window(width=LEFT_W, height=-1, border=False,
                                      no_scrollbar=False):
                    dpg.add_group(tag="build_left")
                dpg.add_spacer(width=14)
                with dpg.child_window(width=-1, height=-1, border=False):
                    dpg.add_group(tag="build_right")
        self._ready = True
        self.tick()
        return tag

    # ------------------------------------------------------------- 交互
    def _on_pick(self, sender, app_data, spec_id):
        self.spec_id = spec_id
        try:
            self.tk.cfg.data["overlay"]["spec"] = spec_id
            self.tk.cfg.save()
        except Exception:
            pass
        self._sig = None
        self.tick()
        self.tk._toast_msg("流派已切到「%s」" % self._spec_label(spec_id))

    def _spec_label(self, sid):
        for s in B.specs_for(self._cls()):
            if s.get("id") == sid:
                return s.get("name") or sid
        return sid or "未选"

    def _cls(self):
        st = read_state()
        cls = (st.get("class") or "").strip().lower()
        return cls or "warlock"

    def _saved_spec(self):
        try:
            return self.tk.cfg.data["overlay"].get("spec") or ""
        except Exception:
            return ""

    # ------------------------------------------------------------- 刷新
    def tick(self):
        if not self._ready:
            return
        st = read_state()
        cls = (st.get("class") or "").strip().lower() or "warlock"
        specs = B.specs_for(cls)
        if not specs:
            self._draw_empty(cls)
            return
        if not self.spec_id:
            self.spec_id = self._saved_spec() or B.default_spec(cls)
        if not B.get_spec(cls, self.spec_id):
            self.spec_id = B.default_spec(cls)

        self._draw_tabs(specs)
        sig = (cls, self.spec_id,
               tuple(sorted((st.get("spells") or {}).items())),
               tuple(sorted((st.get("gear") or {}).items())))
        if sig != self._sig:
            self._sig = sig
            self._draw_body(st, cls)
        self._draw_dps(st)

    def _draw_tabs(self, specs):
        # 只有换了流派才重建：每 0.6 秒重建一次会让按钮一直闪
        if getattr(self, "_tab_spec", None) == self.spec_id and self._tabs:
            return
        self._tab_spec = self.spec_id
        dpg.delete_item("build_tabs", children_only=True)
        dpg.add_spacer(width=PAD, parent="build_tabs")
        self._tabs = {}
        if self._accent is None:
            self._accent = T.accent_button_theme()
        for s in specs:
            sid = s.get("id")
            b = dpg.add_button(label="  %s  " % (s.get("name") or sid),
                               parent="build_tabs", height=34,
                               callback=self._on_pick, user_data=sid)
            if sid == self.spec_id:
                dpg.bind_item_theme(b, self._accent)
            self._tabs[sid] = b

    def _clear(self, tag):
        try:
            dpg.delete_item(tag, children_only=True)
        except Exception:
            pass

    def _draw_empty(self, cls):
        self._clear("build_left")
        self._clear("build_right")
        with dpg.group(parent="build_left"):
            dpg.add_spacer(height=10)
            t = dpg.add_text("还没有「%s」的流派数据" % cls, color=T.C_TEXT_DIM)
            T.set_font(t, "bold")
            dpg.add_spacer(height=6)
            s = dpg.add_text(
                "目前只整理了术士。进一局游戏、职业被识别出来之后，这里会自动出来。",
                color=T.C_TEXT_FAINT, wrap=LEFT_W - 20)
            T.set_font(s, "small")

    def _draw_body(self, st, cls):
        spec = B.get_spec(cls, self.spec_id) or {}
        skills = B.skill_rows(spec, st.get("spells") or {})
        gears = B.gear_rows(spec, st.get("gear") or {})
        done, total = B.completion(skills, gears)
        self._clear("build_left")
        self._clear("build_right")

        with dpg.group(parent="build_left"):
            with dpg.group(horizontal=True):
                t = dpg.add_text("%s · %s" % (B.class_name(cls), spec.get("name") or ""))
                T.set_font(t, "bold")
                dpg.add_spacer(width=10)
                if spec.get("tag"):
                    tagtxt = dpg.add_text(spec["tag"], color=T.C_GOLD)
                    T.set_font(tagtxt, "tiny")
                    if spec.get("intro"):
                        with dpg.tooltip(tagtxt):
                            dpg.add_text(spec["intro"], wrap=420)
                dpg.add_spacer(width=10)
                c = dpg.add_text("%d/%d 核心" % (done, total),
                                 color=T.C_GREEN if done >= total else T.C_AMBER)
                T.set_font(c, "small")
            dpg.add_spacer(height=8)
            if spec.get("caution"):
                cw = dpg.add_text("注意：" + spec["caution"], color=T.C_AMBER,
                                  wrap=LEFT_W - 20)
                T.set_font(cw, "tiny")
                dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                self._section("优先级")
                dpg.add_spacer(width=10)
                for i, sk in enumerate(skills[:4]):
                    if i:
                        a = dpg.add_text("→", color=T.C_TEXT_FAINT)
                        T.set_font(a, "small")
                    row = [r for r in skills if r["key"] == sk["key"]][0]
                    col = T.C_GREEN if row["state"] == "ok" else (
                        T.C_AMBER if row["state"] == "low" else T.C_TEXT_FAINT)
                    tag = dpg.add_text("%d %s" % (i + 1, row["zh"]), color=col)
                    T.set_font(tag, "small")
            dpg.add_spacer(height=7)

            self._section("法术", "%d/%d" % (
                sum(1 for r in skills if r["state"] != "missing"), len(skills)))
            for r in skills:
                self._skill_line(r)
            dpg.add_spacer(height=6)

            self._section("首饰", "%d/%d 栏位" % (
                sum(1 for r in gears if r["have"]), len(gears)))
            for r in gears:
                self._gear_line(r)

        with dpg.group(parent="build_right"):
            dpg.add_group(tag="build_dps_box")

    def _section(self, title, right=""):
        with dpg.group(horizontal=True):
            t = dpg.add_text(title, color=T.C_GOLD)
            T.set_font(t, "small")
            if right:
                dpg.add_spacer(width=8)
                r = dpg.add_text(right, color=T.C_TEXT_FAINT)
                T.set_font(r, "tiny")

    def _skill_line(self, r):
        col = {"ok": T.C_GREEN, "low": T.C_AMBER, "missing": T.C_TEXT_FAINT}[r["state"]]
        mark = "●" if r["state"] != "missing" else "○"
        with dpg.group(horizontal=True) as row:
            dpg.add_spacer(width=8)
            m = dpg.add_text(mark, color=col)
            T.set_font(m, "small")
            dpg.add_spacer(width=4)
            name = dpg.add_text(r["zh"], color=T.C_TEXT if r["state"] != "missing" else T.C_TEXT_DIM)
            T.set_font(name, "small")
            if r.get("must"):
                star = dpg.add_text("·核心", color=T.C_GOLD)
                T.set_font(star, "tiny")
            dpg.add_spacer(width=8)
            n = dpg.add_text(r["note"], color=col)
            T.set_font(n, "tiny")
        if r.get("why"):
            with dpg.tooltip(row):
                dpg.add_text(r["why"], wrap=380)
        dpg.add_spacer(height=1)

    def _gear_line(self, r):
        col = T.C_GREEN if r["have"] else T.C_TEXT_FAINT
        mark = "●" if r["have"] else "○"
        with dpg.group(horizontal=True) as row:
            dpg.add_spacer(width=8)
            m = dpg.add_text(mark, color=col)
            T.set_font(m, "small")
            dpg.add_spacer(width=4)
            name = dpg.add_text(r["name"], color=T.C_TEXT if r["have"] else T.C_TEXT_DIM)
            T.set_font(name, "small")
            if r.get("must"):
                star = dpg.add_text("·核心", color=T.C_GOLD)
                T.set_font(star, "tiny")
            dpg.add_spacer(width=8)
            n = dpg.add_text(r["bonus"] or "缺少", color=col)
            T.set_font(n, "tiny")
        if r.get("why"):
            with dpg.tooltip(row):
                dpg.add_text(r["why"], wrap=380)
        dpg.add_spacer(height=1)

    # ------------------------------------------------------------- DPS
    def _draw_dps(self, st):
        meter = st.get("meter") or []
        boss = st.get("boss_meter") or []
        total = sum(r.get("dps") or 0 for r in meter)
        boss_total = sum(r.get("dps") or 0 for r in boss)

        box = "build_dps_box"
        if not dpg.does_item_exist(box):
            return
        sig = (round(total, 1), round(boss_total, 1),
               tuple((r.get("name"), r.get("dmg")) for r in meter),
               tuple((r.get("name"), r.get("dmg")) for r in boss))
        if getattr(self, "_dps_sig", None) == sig:
            return
        self._dps_sig = sig
        self._clear(box)

        with dpg.group(parent=box):
            with dpg.group(horizontal=True):
                t = dpg.add_text("伤害统计")
                T.set_font(t, "bold")
            dpg.add_spacer(height=2)
            h = dpg.add_text("「对精英」只统计打大怪那段时间，通常比整体高，不是算错。",
                             color=T.C_TEXT_FAINT, wrap=430)
            T.set_font(h, "tiny")

        with dpg.group(parent=box):
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                self._metric("整体 DPS", "%.1f" % total, T.C_TEXT)
                dpg.add_spacer(width=12)
                self._metric("对精英 DPS", "%.1f" % boss_total, T.C_GOLD)
                dpg.add_spacer(width=12)
                self._metric("精英伤害占比", self._boss_share(meter, boss), T.C_BLUE)
            dpg.add_spacer(height=14)

            if not meter:
                s = dpg.add_text("还没进游戏，或者这一局还没造成伤害。",
                                 color=T.C_TEXT_FAINT)
                T.set_font(s, "small")
                return

            dpg.add_text("技能明细", color=T.C_GOLD)
            dpg.add_spacer(height=4)
            with dpg.table(header_row=True, policy=dpg.mvTable_SizingStretchProp,
                           borders_innerH=True, borders_outerH=False,
                           borders_innerV=False, borders_outerV=False,
                           row_background=True):
                dpg.add_table_column(label="技能", init_width_or_weight=2.2)
                dpg.add_table_column(label="整体伤害", init_width_or_weight=1.5)
                dpg.add_table_column(label="占比", init_width_or_weight=1.0)
                dpg.add_table_column(label="精英伤害", init_width_or_weight=1.5)
                dpg.add_table_column(label="占比", init_width_or_weight=1.0)
                bmap = {r.get("name"): r for r in boss}
                for r in meter:
                    nm = r.get("name") or ""
                    b = bmap.get(nm) or {}
                    with dpg.table_row():
                        dpg.add_text(nm, color=T.C_TEXT)
                        dpg.add_text("{:,}".format(int(r.get("dmg") or 0)),
                                     color=T.C_TEXT_DIM)
                        dpg.add_text(_pct(r.get("dmg") or 0, sum(x.get("dmg") or 0 for x in meter)),
                                     color=T.C_TEXT_DIM)
                        if b:
                            dpg.add_text("{:,}".format(int(b.get("dmg") or 0)),
                                         color=T.C_GOLD)
                            dpg.add_text(_pct(b.get("dmg") or 0,
                                              sum(x.get("dmg") or 0 for x in boss)),
                                         color=T.C_GOLD)
                        else:
                            dpg.add_text("—", color=T.C_TEXT_FAINT)
                            dpg.add_text("不在精英战", color=T.C_TEXT_FAINT)

    def _metric(self, label, value, color):
        with dpg.child_window(width=132, height=76, border=True, no_scrollbar=True):
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=10)
                l = dpg.add_text(label, color=T.C_TEXT_DIM)
                T.set_font(l, "tiny")
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=10)
                v = dpg.add_text(value, color=color)
                T.set_font(v, "bold")

    def _boss_share(self, meter, boss):
        bt = sum(r.get("dmg") or 0 for r in boss)
        mt = sum(r.get("dmg") or 0 for r in meter)
        if not mt:
            return "—"
        return "%.0f%%" % (bt / mt * 100.0)
