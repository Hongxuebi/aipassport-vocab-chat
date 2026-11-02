# -*- coding: utf-8 -*-
"""FSRS-6 精简实现（与固件 components/srs/srs.c **逐行对应**）

为什么在服务端机重写一份：调度上移到服务端机后，服务端机要能自己算间隔；
但两份实现绝不能各算各的 —— 所以有 crosscheck 脚本逐值比对（误差 < 1e-4）。

改动这份文件的前提：先改 srs.c，再同步这里，再跑 crosscheck。
"""
import math

# py-fsrs DEFAULT_PARAMETERS（w1..w19，w20=DECAY）—— 与 srs.c 的 s_w[21] 完全一致
W = [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194,
     0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614, 0.2629,
     1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542]

S_MIN, S_MAX = 0.001, 36500.0
D_MIN, D_MAX = 1.0, 10.0

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


def _decay():
    return -W[20]


def _factor():
    return 0.9 ** (1.0 / _decay()) - 1.0


FACTOR = _factor()


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def retrievability(stability, elapsed_days):
    if stability <= 0:
        return 0.0
    if elapsed_days < 0:
        elapsed_days = 0
    return (1.0 + FACTOR * elapsed_days / stability) ** _decay()


def interval_days(stability, desired_retention):
    ivl = (stability / FACTOR) * (desired_retention ** (1.0 / _decay()) - 1.0)
    d = int(ivl + 0.5)
    return max(1, min(d, int(S_MAX)))


def init(grade):
    """首次见到这个词 → (S, D)"""
    grade = max(AGAIN, min(EASY, grade))
    s = clamp(W[grade - 1], S_MIN, S_MAX)
    d = clamp(W[4] - math.exp(W[5] * (grade - 1)) + 1.0, D_MIN, D_MAX)
    return s, d


def review(s, d, grade, t):
    """复习后更新 → (S, D)"""
    grade = max(AGAIN, min(EASY, grade))
    t = max(0.0, t)
    r = retrievability(s, t)
    if grade == AGAIN:
        long_term = W[11] * (d ** -W[12]) * (((s + 1.0) ** W[13]) - 1.0) * math.exp((1.0 - r) * W[14])
        short_term = s / math.exp(W[17] * W[18])
        ns = min(long_term, short_term)
    else:
        hard_penalty = W[15] if grade == HARD else 1.0
        easy_bonus = W[16] if grade == EASY else 1.0
        ns = s * (1.0 + math.exp(W[8]) * (11.0 - d) * (s ** -W[9])
                  * (math.exp((1.0 - r) * W[10]) - 1.0) * hard_penalty * easy_bonus)
    nd = d - W[6] * (float(grade) - 3.0)
    return clamp(ns, S_MIN, S_MAX), clamp(nd, D_MIN, D_MAX)
