# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 预设页（替代原来那套流派）。

为什么换成「自己填」：
  流派是事先写死在 builds.json 里的，一个职业五条线，改一个技能就得改数据文件，
  而且经常和实际玩法对不上。现在改成：你自己按想要的顺序列一遍技能和首饰，
  选卡时命中的那张会在游戏里发光提示 —— 规则透明，改起来就是改几行字。
"""
import dearpygui.dearpygui as dpg

import azs_theme as T

# 卡片高度按「默认窗口 601 高」倒推：标题区占掉约 130，底下要留一口气，
# 所以 400 会顶到窗口边被切掉半行，374 刚好露全。
CARD_H = 390
TABLE_H = 254


def _preset(tk):
    p = tk.cfg.data.get("preset")
    if not isinstance(p, dict):
        p = {"enabled": True, "skills": [], "gear": []}
        tk.cfg.data["preset"] = p
    for k, d in (("skills", []), ("gear", [])):
        if not isinstance(p.get(k), list):
            p[k] = list(d)
    if "enabled" not in p:
        p["enabled"] = True
    return p


def _lines(v):
    return [x.strip() for x in str(v or "").replace("，", "\n").replace(",", "\n").split("\n")
            if x.strip()]


def cb_save(sender, app_data, user_data):
    tk = user_data
    p = _preset(tk)
    p["skills"] = _lines(dpg.get_value("preset_skills"))
    p["gear"] = _lines(dpg.get_value("preset_gear"))
    p["enabled"] = bool(dpg.get_value("preset_on"))
    tk.cfg.save()
    n = len(p["skills"]) + len(p["gear"])
    msg = "已保存 %d 项，下次选卡立即生效" % n if n else "已保存（清单是空的，不会高亮）"
    try:
        dpg.set_value("preset_state", "  " + msg)
    except Exception:
        pass
    try:
        tk._toast_msg(msg)
    except Exception:
        pass


def _board_rows():
    """BOSS 强度榜：只依赖客户端技能表，不需要打怪数据"""
    try:
        import azs_strength as S
        return S.board(limit=14)
    except Exception:
        return []


def _layout_tools():
    """分栏只借首页那套单元格间距；左右留白自己加，
    免得把父容器的 WindowPadding / ChildBorderSize 一起继承给卡片。"""
    from azs_pages import _cell_theme
    return _cell_theme


def build_preset(tk, parent):
    tag = "page_preset"
    p = _preset(tk)

    with dpg.child_window(tag=tag, parent=parent, border=False, width=-1, show=False):
        dpg.add_spacer(height=12)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=26)
            with dpg.group():
                t = dpg.add_text("预设")
                T.set_font(t, "huge")
                dpg.add_spacer(height=2)
                s = dpg.add_text(
                    "自己列一遍想要的技能和首饰，越靠上越优先。选卡时命中的那张会在游戏里发光。",
                    color=T.C_TEXT_DIM, wrap=980)
                T.set_font(s, "small")
        dpg.add_spacer(height=16)

        _cell_theme = _layout_tools()
        # 两栏按 1 : 1.16 分。分栏表格自己 pad_outerX=False（左右白由外层给），
        # 列宽用小数权重，窗口再窄也不会把右表顶出卡片。
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=22)
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           width=-1, pad_outerX=False,
                           borders_innerV=False, borders_innerH=False,
                           borders_outerH=False, borders_outerV=False,
                           scrollX=False, scrollY=False) as tb:
                dpg.bind_item_theme(tb, _cell_theme(9))
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(init_width_or_weight=1.16)
                with dpg.table_row():
                    _editor(tk, p)
                    _board()
            dpg.add_spacer(width=12)

    return tag


def _editor(tk, p):
    """左：自己的清单"""
    from azs_pages import card, card_head, rule, _cell_theme

    with card(CARD_H):
        card_head("我的清单", "前两项高亮+光边，第三项起只高亮")
        dpg.add_spacer(height=2)
        rule()
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="启用预设高亮", tag="preset_on",
                             default_value=bool(p.get("enabled", True)))
            dpg.add_spacer(width=14)
            b = dpg.add_button(label="  保 存  ", height=30,
                               callback=cb_save, user_data=tk)
            dpg.bind_item_theme(b, T.accent_button_theme())
            dpg.add_spacer(width=10)
            dpg.add_text("", tag="preset_state", color=T.C_GREEN)
            T.set_font(dpg.last_item(), "small")
        dpg.add_spacer(height=10)

        # 两栏并排，省一行高度 —— 竖着排的话 390 高的卡片放不下两个多行框
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       width=-1, pad_outerX=False,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as tb:
            dpg.bind_item_theme(tb, _cell_theme(10))
            dpg.add_table_column(init_width_or_weight=1.35)
            dpg.add_table_column(init_width_or_weight=1.0)
            with dpg.table_row():
                with dpg.table_cell():
                    h1 = dpg.add_text("技能（每行一个）", color=T.C_GREEN)
                    T.set_font(h1, "small")
                    dpg.add_spacer(height=5)
                    dpg.add_input_text(tag="preset_skills", multiline=True,
                                       width=-1, height=228,
                                       default_value="\n".join(p.get("skills") or []),
                                       hint="例：\n腐蚀术\n痛苦诅咒\n灵魂之火")
                with dpg.table_cell():
                    h2 = dpg.add_text("首饰（每行一个）", color=T.C_GREEN)
                    T.set_font(h2, "small")
                    dpg.add_spacer(height=5)
                    dpg.add_input_text(tag="preset_gear", multiline=True,
                                       width=-1, height=228,
                                       default_value="\n".join(p.get("gear") or []),
                                       hint="例：\n哈卡之眼")


def _board():
    """右：BOSS 强度榜（只看技能表，不看战斗记录）"""
    from azs_pages import card, card_head, rule

    with card(CARD_H):
        card_head("BOSS 强度榜", "以榜首 = 100 归一")
        dpg.add_spacer(height=2)
        rule()
        h = dpg.add_text(
            "只看技能本身的单体输出，不看你打了多少。",
            color=T.C_TEXT_FAINT, wrap=-1)
        T.set_font(h, "tiny")
        dpg.add_spacer(height=4)
        rows = _board_rows()
        if not rows:
            dpg.add_text("技能表未加载", color=T.C_TEXT_FAINT)
            return
        top = rows[0]["solo"] or 1.0
        with dpg.table(header_row=True, row_background=False,
                       borders_innerH=True, borders_innerV=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollY=True, height=TABLE_H, width=-1,
                       policy=dpg.mvTable_SizingStretchProp,
                       tag="preset_board"):
            dpg.add_table_column(label="技能", init_width_or_weight=1.7)
            dpg.add_table_column(label="单体", init_width_or_weight=0.6)
            dpg.add_table_column(label="群体", init_width_or_weight=0.6)
            dpg.add_table_column(label="打BOSS", init_width_or_weight=1.2)
            for r in rows:
                with dpg.table_row():
                    col = T.C_GOLD if r["tags"] else T.C_TEXT
                    nm = dpg.add_text(r["name"], color=col)
                    T.set_font(nm, "small")
                    v = dpg.add_text("%.0f" % (r["solo"] / max(1e-6, top) * 100),
                                     color=T.C_TEXT)
                    T.set_font(v, "small")
                    v2 = dpg.add_text("%.0f" % (r["aoe"] / max(1e-6, top) * 100),
                                      color=T.C_TEXT_DIM)
                    T.set_font(v2, "small")
                    tg = "/".join(r["tags"]) if r["tags"] else "—"
                    t2 = dpg.add_text(tg, color=T.C_AMBER if r["tags"] else T.C_TEXT_FAINT)
                    T.set_font(t2, "tiny")
