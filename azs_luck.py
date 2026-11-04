# -*- coding: utf-8 -*-
"""幸运值折算成 DPS：把「多抽到好卡」换成百分比增伤。

数据来源（2026-11-04，client.log 里 74 次选卡 / 222 张卡）：

  品质        幸运 0 附近    幸运 +66%   模型（K=1.2）
  普通          64.1%         27.1%        40.9%
  优秀          20.5%         29.2%        23.6%
  精良          10.3%         22.9%        18.3%
  史诗           5.1%         12.5%        12.7%
  传说           0.0%          8.3%         4.6%

  · 直接看「高幸运档更爱出传说」会高估：幸运是随着局内进程涨上去的，
    打得久、等级高本身就会推高品质。所以按等级分层再比，
    同一等级段里幸运高的那一半，史诗以上大概多 10~16 个百分点。
  · K 就是按这个分层结果定的：每 +100% 幸运，高一档品质的权重再乘 (1+K)。
  · 各品质「一张卡值多少增伤」，是用同一份日志里的卡跑本模型平均出来的
    （普通 2.0% / 优秀 3.2% / 精良 4.0% / 史诗 6.5% / 传说 16.4%）。
"""

# 幸运 0 时的品质概率。传说取 1% 而不是 0：222 张样本里低幸运档没抽到，
# 不等于概率是 0，留一点平滑项，免得除以零把结果放大。
BASE = (0.620, 0.220, 0.105, 0.045, 0.010)

# 每 +100% 幸运，高一档的权重乘 (1 + K)
K = 1.2

# 各品质一张卡的平均增伤（百分点）
CARD_VALUE = (2.0, 3.2, 4.0, 6.5, 16.4)

# 一局大概打到这个等级；用来算「这个幸运还能吃多少次选卡」
END_LEVEL = 42

# 一张幸运卡的收益不再往上加：后期幸运卡堆多了会离谱
CAP = 18.0


def odds(luck):
    """幸运值 → 各品质概率（和为 1）。luck 用小数，0.22 表示 +22%。"""
    f = 1.0 + K * max(0.0, float(luck or 0.0))
    w = [BASE[i] * (f ** i) for i in range(len(BASE))]
    s = sum(w) or 1.0
    return [x / s for x in w]


def per_pick(luck):
    """这个幸运水平下，一张卡平均值多少增伤（百分点）。"""
    return sum(p * v for p, v in zip(odds(luck), CARD_VALUE))


def picks_left(lvl):
    """还能选多少次卡：按一局打到 END_LEVEL 估，至少留 1 次。"""
    try:
        lv = int(float(lvl))
    except (TypeError, ValueError):
        lv = 1
    return max(1, END_LEVEL - lv)


def gain(luck, d_luck, lvl):
    """一张「+d_luck 幸运」的卡，折算成整局增伤百分比。"""
    cur = max(0.0, float(luck or 0.0))
    d = float(d_luck or 0.0) / 100.0 if abs(d_luck) > 1.0 else float(d_luck or 0.0)
    if d <= 0:
        return 0.0
    delta = per_pick(cur + d) - per_pick(cur)
    if delta <= 0:
        return 0.0
    return min(CAP, delta * picks_left(lvl))


def describe(luck, d_luck, lvl):
    """给徽标用的说明：幸运 22%·按剩 12 抽折算"""
    cur = max(0.0, float(luck or 0.0))
    d = float(d_luck or 0.0)
    if abs(d) <= 1.0:
        d *= 100.0
    return "幸运%.0f%%·按剩%d抽折算" % (d, picks_left(lvl))
