#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web.py —— 手机学习看板（只读统计页，PRD §F11）

链路（对外只有一条 URL，与「在不在家」无关）：

    手机 → https://<域名>:GATE_PORT/s/<32位随机串>/
         → 统一入口独立场景（控制机内部口 TUNNEL_PORT）
         → 服务端机本服务 0.0.0.0:8803（同一路径原样转发）
         → 这里再校验一次随机串（串不对回 404，连"有这东西"都不透露）

三条硬规矩：
  ① **只读**：只认 GET/HEAD，其余 405；页面上没有任何操作设备的按钮
  ② **不绑网络位置**：监听 0.0.0.0:8803，安全靠「门白名单 + 路径随机串 + 只读」，**不靠局域网**
     （2026-10-31 点名的污染：文档里不许写「绑 LAN / 只绑某个 IP」）
  ③ 弱网也要能看主干：单页 HTML、无外部依赖、**数据全部服务端渲染**（不开 JS 也能看）

数据源（服务端机 `~/aipassport/`）：

    tomato.jsonl  番茄流水（PRD §5.4 七字段）—— **有真实日期**，是本周/本月/连续天数的正本
    events.jsonl  复习流水（130 条/14 个学习日）—— 只有「学习日」编号（day），**没有可靠的真实日期**：
                  它的 `at` 是服务端机**收到**的时刻（历史补传一次来好几十条），拿它归日会全堆到同一天
    state.json    FSRS 快照（掌握三档只看这里的 S；day = 学习第几天）
    words.csv     453 词表（事件里的数字 id 是它的 1-based 下标）

所以本页有两种模式，**页面自己会切**：

    番茄已接入：本周/本月/连续天数 全按**真实日期**（周一起算、一格一天、streak 给 1 天宽限）
    番茄未接入：这几块退到**学习日**口径（最近 14 个学习日），并在页上写明「番茄还没接入」——
               **不显示 0**（PRD §5.4 明文）

掌握三档（锚 Anki 手册：复习间隔 ≥21 天为 mature，**不另造指标**）：
    熟 S≥21 ｜ 会 7≤S<21 ｜ 在学 S<7 ｜ 已掌握 = 熟 + 会
"""
import calendar
import datetime as dt
import html
import json
import os
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.path.expanduser(os.environ.get("AIP_DATA_DIR", "~/aipassport"))
TOMATO = os.path.join(DATA, "tomato.jsonl")
EVENTS = os.path.join(DATA, "events.jsonl")
STATE = os.path.join(DATA, "state.json")
WORDS = os.path.join(DATA, "words.csv")
TOKEN_FILE = os.path.join(DATA, "board_token.txt")

PORT = int(os.environ.get("AIP_BOARD_PORT", "8803"))
HOST = os.environ.get("AIP_BOARD_HOST", "0.0.0.0")     # 不绑死某一张网卡（见文件头 ②）

WEEK_CN = ("一", "二", "三", "四", "五", "六", "日")


# ────────────────────────────────────────────────────────── 令牌
def token():
    """32 位随机串：只存在文件里，**不进日志**（照门里 /q/ 那条的做法）。"""
    try:
        t = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(t) >= 16:
            return t
    except Exception:
        pass
    t = secrets.token_hex(16)                       # 32 个十六进制字符
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(t + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    return t


# ────────────────────────────────────────────────────────── 读数据
def _jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return out


def load_tomato():
    rows = []
    for r in _jsonl(TOMATO):
        try:
            dur = int(r.get("dur") or 0)
            at = int(r.get("at") or 0)
            ts = int(r.get("ts") or 0)
        except Exception:
            continue
        if dur <= 0:
            continue
        d = r.get("date")
        if not d:                                   # 契约允许 null（ts=0）；用「收到时刻」归日，只当兜底
            if at > 0:
                d = dt.datetime.fromtimestamp(at).strftime("%Y-%m-%d")
            else:
                continue
        try:
            hour = dt.datetime.fromtimestamp(ts or at).hour
        except Exception:
            hour = 12
        rows.append({"date": d, "dur": dur, "at": at, "ts": ts, "hour": hour})
    return rows


def load_events():
    rows = []
    for r in _jsonl(EVENTS):
        try:
            at = int(r.get("at") or 0)
            wid = int(r.get("id") or 0)
            g = int(r.get("g") or 0)
            day = int(r.get("day") or 0)
        except Exception:
            continue
        rows.append({"day": day, "id": wid, "g": g, "at": at,
                     "rt": int(r.get("rt") or 0), "flip": int(r.get("flip") or 0)})
    return rows


def load_state():
    try:
        j = json.load(open(STATE, encoding="utf-8"))
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def load_words():
    """words.csv：第一列 id（w0001），第二列词。事件里的数字 id 即 1-based 下标。"""
    out = []
    try:
        with open(WORDS, encoding="utf-8-sig") as f:
            for i, line in enumerate(f):
                if i == 0 and ("词" in line or "id" in line):
                    continue
                parts = line.rstrip("\n").split(",")
                if len(parts) >= 2 and parts[1].strip():
                    out.append(parts[1].strip())
    except Exception:
        pass
    return out


# ────────────────────────────────────────────────────────── 统计
def _d(s):
    return dt.date.fromisoformat(s)


def streak(active, cur, prev, grace=7):
    """从 cur 往回数连续活跃数；**grace 天里白给 1 天**（拍板「可以给宽限」）。

    `cur` 今天没活跃不算断（从上一个起算），否则一早打开就被清零，反而泄气。
    active / prev 由调用方给（可以是日期，也可以是学习日编号）。
    """
    d = cur
    if d not in active:
        d = prev(d)
    n = 0
    missed = []
    for _ in range(4000):
        if d in active:
            n += 1
        else:
            if len([x for x in missed if _gap(prev, x, d) < grace]) >= 1:
                break
            missed.append(d)
        d = prev(d)
    return n


def _gap(prev, newer, older):
    """两个"日子"隔多少天——两种口径都按 1 步 = 1 天算。"""
    try:
        return (newer - older).days
    except Exception:
        return int(newer) - int(older)


def streak_date(active, today, grace_skips=1):
    """日期版连续（与 `~/aipassport/selfknow.py` 的 `_streak_date` 逐字同口径）。"""
    d = today
    if d not in active:
        d = d - dt.timedelta(days=1)
    n, missed = 0, 0
    while n < 4000:
        if d in active:
            n += 1
        else:
            if missed >= grace_skips:
                break
            missed += 1
        d = d - dt.timedelta(days=1)
    return n


def streak_max(active, prev):
    if not active:
        return 0
    return max(streak(active, x, prev) for x in sorted(active))


def buckets(state):
    """掌握三档：只看 FSRS 稳定性 S（不另造指标）。"""
    cards = state.get("cards") or {}
    hot = mid = cold = 0
    for c in cards.values():
        try:
            s = float(c.get("S") or 0)
        except Exception:
            s = 0.0
        if s >= 21:
            hot += 1
        elif s >= 7:
            mid += 1
        else:
            cold += 1
    return hot, mid, cold


def collect(now=None):
    now = now or dt.datetime.now()
    today = now.date()
    tomatos = load_tomato()
    events = load_events()
    state = load_state()
    words = load_words()
    cards = state.get("cards") or {}
    cur_day = state.get("day")

    # 番茄：按真实日期
    tday = {}
    for t in tomatos:
        v = tday.setdefault(t["date"], {"n": 0, "min": 0})
        v["n"] += 1
        v["min"] += t["dur"]

    # 复习：按学习日
    lday = {}
    for e in events:
        v = lday.setdefault(e["day"], {"n": 0, "ids": []})
        v["n"] += 1
        if e["id"] not in v["ids"]:
            v["ids"].append(e["id"])

    return {"now": now, "today": today, "tomatos": tomatos, "events": events,
            "state": state, "words": words, "cards": cards, "cur_day": cur_day,
            "tday": tday, "lday": lday}


# ────────────────────────────────────────────────────────── 页面
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{overflow-x:hidden;background:#0a1018;color:#e8eef7;font:16px/1.5 -apple-system,"PingFang SC",system-ui,sans-serif;
  padding:18px 14px 40px;max-width:460px;margin:0 auto;-webkit-font-smoothing:antialiased}
.hdr{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:16px}
.hdr h1{font-size:19px;font-weight:600;letter-spacing:.5px}
.hdr .sub{font-size:12px;color:#5f7189;white-space:nowrap}
.card{background:linear-gradient(160deg,#111c2b,#0d1520);border:1px solid #1b2a3d;border-radius:18px;padding:15px;margin-bottom:12px}
.k{font-size:12px;color:#5f7189;letter-spacing:.5px;margin-bottom:6px}
.big{font-size:32px;font-weight:700;letter-spacing:-.5px}
.big small{font-size:13px;font-weight:400;color:#7c8ea6;margin-left:4px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.chip{font-size:12px;color:#9fb0c6;background:#13202f;border:1px solid #1f2f44;border-radius:10px;padding:4px 9px}
.chip b{color:#e8eef7}
.chart{display:flex;align-items:flex-end;gap:4px;height:104px;margin-top:12px}
.col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%}
.bar{width:100%;background:linear-gradient(180deg,#2d6aa8,#1d3f66);border-radius:4px 4px 0 0;position:relative;min-height:2px}
.bar span{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:10px;color:#5f7189}
.col.hot .bar{background:linear-gradient(180deg,#35e0d0,#1d7f77)}
.col.hot .bar span{color:#35e0d0}
.dl{font-size:10px;color:#3f5168;margin-top:5px}
.col.hot .dl{color:#7fe8dd}
.stack{display:flex;height:14px;border-radius:7px;overflow:hidden;background:#16202e;margin-top:12px}
.stack i{display:block}
.lg{display:flex;gap:13px;margin-top:10px;font-size:12px;color:#7c8ea6;flex-wrap:wrap}
.lg b{color:#e8eef7;font-weight:600}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:1px}
.grow{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:13px}
.gn{width:30px;color:#7c8ea6;font-size:12px}
.gbar{flex:1;height:8px;background:#16202e;border-radius:4px;overflow:hidden}
.gbar i{display:block;height:100%;background:#4a9dff;border-radius:4px}
.gv{width:28px;text-align:right;color:#7c8ea6;font-size:12px}
.note{font-size:12px;color:#42546b;line-height:1.6;margin-top:12px;word-break:break-word}
.note b{color:#c9a227}
.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-top:12px}
.cal .wd{font-size:10px;color:#3f5168;text-align:center}
.cell{aspect-ratio:1;border-radius:4px;background:#131e2c;display:flex;align-items:center;justify-content:center;font-size:9px;color:#3a4a5e}
.cell.l1{background:#16463f;color:#8fe7dc}
.cell.l2{background:#1c7c72;color:#d6fbf6}
.cell.l3{background:#28bfb1;color:#06231f}
.cell.l4{background:#5ff2e4;color:#04211d;font-weight:700}
.cell.today{outline:1.5px solid #35e0d0;outline-offset:1px}
.cell.off{background:transparent}
.bgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px 8px;margin-top:12px}
.bd{text-align:center}
.bi{width:52px;height:52px;line-height:52px;margin:0 auto 6px;border-radius:50%;font-size:22px;
  background:#18222f;border:1px solid #27374c;opacity:.55}
.bd.on .bi{background:linear-gradient(160deg,#1d6b62,#0d3733);border-color:#35e0d0;opacity:1;
  box-shadow:0 0 0 3px rgba(53,224,208,.09),0 0 16px rgba(53,224,208,.16)}
.bd.todo .bi{border-style:dashed;opacity:.26}
.bn{font-size:11px;color:#7c8ea6;line-height:1.25}
.bd.on .bn{color:#e8eef7}
.bs{font-size:9px;color:#3f5168;margin-top:2px;letter-spacing:.3px}
.bd.on .bs{color:#35e0d0}
.bd.todo .bs{color:#5a4a1e}
.bp{height:3px;background:#16202e;border-radius:2px;margin:5px 10px 0;overflow:hidden}
.bp i{display:block;height:100%;background:linear-gradient(90deg,#2d6aa8,#4a9dff);border-radius:2px}
.blegend{font-size:10px;color:#3f5168;margin-top:12px;text-align:center;letter-spacing:.2px}
.foot{font-size:12px;color:#3a4a5e;line-height:1.7;padding:0 4px;margin-top:6px}
.wrap{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.wtag{font-size:12px;color:#9fb0c6;background:#13202f;border:1px solid #1f2f44;border-radius:8px;padding:2px 8px}
"""


def esc(s):
    return html.escape(str(s))


def leech_page(now=None):
    """错词本打印页（A4）。数据和设备队列同一份规则（aipassport_server.leech_list）。"""
    import csv as _csv
    import datetime as _dt
    import html as _h
    try:
        import aipassport_server as _S
        stp = _S.load_state()
        ids = _S.leech_list(stp, _S.load_all_events())
    except Exception:
        ids = []
    try:
        wrows = list(_csv.DictReader(open(os.path.join(DATA, "words.csv"), encoding="utf-8-sig")))
    except Exception:
        wrows = []
    today = (now if isinstance(now, _dt.date) else _dt.date.today()).isoformat()
    rows = []
    for i, wid in enumerate(ids, 1):
        r = wrows[wid] if 0 <= wid < len(wrows) else {}
        rows.append("<tr><td>%d</td><td class='w'>%s</td><td class='ip'>%s</td><td>%s</td><td class='ch'>%s</td></tr>"
                    % (i, _h.escape(r.get("词", "#%d" % wid)), _h.escape(r.get("音标", "")),
                       _h.escape(r.get("中文", "")), _h.escape(r.get("词块", ""))))
    if not rows:
        body = "<p class='empty'>今天没有错词 —— 没有反复忘的词。</p>"
    else:
        body = ("<table><thead><tr><th>#</th><th>单词 / 词组</th><th>音标</th><th>中文</th><th>搭配</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>错词本 %s</title><style>
*{box-sizing:border-box}
body{margin:0 auto;max-width:760px;padding:26px 22px;color:#111;background:#fff;
  font:15px/1.5 -apple-system,"PingFang SC",system-ui,sans-serif}
h1{font-size:20px;margin:0 0 2px}
.meta{font-size:12px;color:#666;margin-bottom:16px}
table{width:100%%;border-collapse:collapse}
th,td{border-bottom:1px solid #e3e3e3;padding:7px 6px;text-align:left;vertical-align:top;font-size:14px}
th{font-size:12px;color:#666;border-bottom:1.5px solid #999}
td.w{font-weight:600} td.ip{color:#777} td.ch{color:#555}
.empty{color:#666;padding:30px 0}
.foot{margin-top:18px;font-size:11px;color:#999}
@media print{body{padding:0}h1{font-size:18px}}
</style></head><body>
<h1>错词本</h1>
<div class="meta">%s ｜ 共 %d 个 ｜ 打印自学习看板</div>
%s
<div class="foot">连着两次答「认识」就出列；一直出不去，说明该换个记法（拆词块 / 造例句 / 隔天再试）。</div>
</body></html>""" % (today, today, len(ids), body)


def render(now=None):
    d = collect(now)
    now, today = d["now"], d["today"]
    tomatos, events, state, words = d["tomatos"], d["events"], d["state"], d["words"]
    cards, cur_day = d["cards"], d["cur_day"]
    tday, lday = d["tday"], d["lday"]
    has_tomato = bool(tomatos)

    # ── 连续天数：图表按真实日期；**「连续／完美」两族取「学习日 vs 番茄」的高**
    #    （2026-10-31 拍板：过渡期别让练了 14 天的战绩因为接上番茄而清零）
    if has_tomato:
        act_dates = {_d(k) for k, v in tday.items() if v["n"] >= 1}
        prev_d = lambda x: x - dt.timedelta(days=1)                 # noqa: E731
        streak_tomato = streak(act_dates, today, prev_d)
        best_tomato = streak_max(act_dates, prev_d)
    else:
        streak_tomato = best_tomato = 0
    # 连续（2026-11-01 15:1x 定）：**有学习事件 或 写过日记的日子**算来过，白给 1 天；
    # 原来用"学习日编号"，和日记日混不起来，而且口径与 selfknow 不一致（同一天会一个说 3 一个说 6）。
    act_dates_ev = set()
    for e in events:
        try:
            act_dates_ev.add(dt.datetime.fromtimestamp(float(e["at"])).date())
        except Exception:
            pass
    dset = set()
    try:
        import glob as _glob2
        for fp in _glob2.glob(os.path.expanduser("~/aipassport/mem/diary/*.json")):
            try:
                dset.add(_d(os.path.basename(fp)[:-5]))
            except Exception:
                pass
    except Exception:
        pass
    act_dates_all = act_dates_ev | dset
    streak_learn = streak_date(act_dates_all, today) if act_dates_all else 0
    best_learn = streak_learn
    streak_now = max(streak_tomato, streak_learn)
    streak_best = max(best_tomato, best_learn)
    streak_label = ""
    streak_hybrid = bool(has_tomato) and streak_learn > streak_tomato

    # ── 今天 ──
    t_today = tday.get(today.isoformat(), {"n": 0, "min": 0})
    rev_today = (lday.get(cur_day, {"n": 0})["n"] if cur_day else 0)

    # ── 本周（周一）──
    mon = today - dt.timedelta(days=today.weekday())
    week = [(mon + dt.timedelta(days=i), tday.get((mon + dt.timedelta(days=i)).isoformat(), {"n": 0, "min": 0}))
            for i in range(7)]
    wmin = sum(v["min"] for _, v in week)
    wtom = sum(v["n"] for _, v in week)
    lmin = sum(tday.get((mon - dt.timedelta(days=7) + dt.timedelta(days=i)).isoformat(), {"min": 0})["min"]
               for i in range(7))
    wmax = max([v["min"] for _, v in week] + [1])

    # ── 本月 ──
    mdays = calendar.monthrange(today.year, today.month)[1]
    mmin = mtom = mactive = 0
    for i in range(1, mdays + 1):
        v = tday.get(dt.date(today.year, today.month, i).isoformat(), {"n": 0, "min": 0})
        mmin += v["min"]
        mtom += v["n"]
        if v["n"] >= 1:
            mactive += 1

    # ── 词语 ──
    hot, mid, cold = buckets(state)
    ev_ids = {e["id"] for e in events if e["id"] > 0}
    card_ids = set()
    for k in cards:
        try:
            card_ids.add(int(k))
        except Exception:
            pass
    learned = len(card_ids | ev_ids)
    mastered = hot + mid
    total_words = len(words) or 453
    today_ids = lday.get(cur_day, {"ids": []})["ids"] if cur_day else []
    today_words = [words[i] for i in today_ids if 0 <= i < len(words)]

    # ── 新词进度 / 预计完成日（T5，2026-11-01）──
    remaining = max(0, total_words - learned)
    # 近 7 个"有数据的学习日"的新词均值：每张卡第一次出现的 day
    first_day = {}
    for e in events:
        if e["id"] >= 0:                     # 词表 id 是 0 基（id 0 = 第一个词）
            d = e["day"]
            if e["id"] not in first_day or d < first_day[e["id"]]:
                first_day[e["id"]] = d
    new_by_day = {}
    for d in first_day.values():
        new_by_day[d] = new_by_day.get(d, 0) + 1
    recent_days = sorted(new_by_day)[-7:]
    avg_new = (sum(new_by_day[d] for d in recent_days) / float(len(recent_days))) if recent_days else 0.0
    eta = None
    if remaining > 0 and avg_new > 0.05:
        eta = today + dt.timedelta(days=int(round(remaining / avg_new + 0.4999)))

    # ── 错词 Top20（lapses 排序）──
    leech = []
    for k, c in cards.items():
        try:
            wid = int(k)
        except Exception:
            continue
        laps = int(c.get("lapses") or 0)
        if laps > 0 and 1 <= wid <= len(words):
            leech.append((laps, wid, float(c.get("S") or 0)))
    leech.sort(key=lambda x: (-x[0], x[1]))
    leech_top = leech[:20]

    # ── 错词本（T6，规则与卡片服务一致）──
    hist = {}
    for e in events:
        try:
            wid2 = int(e.get("id") or 0)
            g2 = int(e.get("g") or 0)
        except Exception:
            continue
        if wid2 >= 0:
            hist.setdefault(wid2, []).append(g2)
    leech_cur = []
    for k, c in cards.items():
        try:
            wid2 = int(k)
            laps2 = int(c.get("lapses") or 0)
        except Exception:
            continue
        h = hist.get(wid2, [])
        if len(h) >= 2 and h[-1] >= 3 and h[-2] >= 3:
            continue
        bad2 = sum(1 for g in h[-5:] if g in (1, 2))
        if laps2 >= 3 or bad2 >= 3:
            leech_cur.append((laps2, bad2, wid2))
    leech_cur.sort(key=lambda x: (-x[0], -x[1], x[2]))
    leech_cur = leech_cur[:10]

    # ── 周默写记录（T7 还没出单时显示"还没有"）──
    dict_rows = []
    try:
        dp = os.path.join(DATA, "默写.jsonl")
        if os.path.exists(dp):
            for line in open(dp, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    dict_rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        dict_rows = []

    # ── 评分分布 ──
    gcount = {1: 0, 2: 0, 3: 0, 4: 0}
    for e in events:
        if e["g"] in gcount:
            gcount[e["g"]] += 1
    gtotal = sum(gcount.values()) or 1
    gname = {1: "忘记", 2: "模糊", 3: "认识", 4: "熟知"}

    # ── 勋章（PRD §F11 首版 12 枚）──
    total_n = len(tomatos)
    total_min = sum(t["dur"] for t in tomatos)
    best_day = max([v["n"] for v in tday.values()] + [0])
    early = sum(1 for t in tomatos if t["hour"] < 8)
    late = sum(1 for t in tomatos if t["hour"] >= 22)
    first_tomato = min((t["date"] for t in tomatos), default=None)
    first_learn = (min(lday) if lday else None)
    # 完美一周：任一「周一起算的 7 天」里 ≥5 天有练（番茄按真实日期；学习日按连续 7 个编号）
    #   两族取高，理由同连续天数（2026-10-31）
    week_tomato = 0
    for s in tday:
        d0 = _d(s)
        m0 = d0 - dt.timedelta(days=d0.weekday())
        week_tomato = max(week_tomato, sum(1 for i in range(7)
                                           if (m0 + dt.timedelta(days=i)).isoformat() in tday))
    week_learn = 0
    for s in lday:
        week_learn = max(week_learn, sum(1 for i in range(7) if (s + i) in lday))
    best_week_days = max(week_tomato, week_learn)

    def t_badge(need, cur, unit="", when=None):
        """番茄类：一条数据都没有 ⇒「待接入」（**不显示 0**，PRD §5.4 明文）。"""
        if not has_tomato:
            return {"st": "todo", "txt": "待接入", "pct": None, "when": None}
        if cur >= need:
            return {"st": "on", "txt": "已解锁", "pct": 1.0, "when": when or first_tomato}
        return {"st": "", "txt": "%d/%d%s" % (cur, need, unit), "pct": min(1.0, cur / float(need)), "when": None}

    def n_badge(need, cur, unit=""):
        if cur >= need:
            return {"st": "on", "txt": "已解锁", "pct": 1.0, "when": None}
        return {"st": "", "txt": "%d/%d%s" % (cur, need, unit), "pct": min(1.0, cur / float(need)), "when": None}

    badges = [
        ("🍅", "第一次专注", t_badge(1, total_n)),
        ("🌱", "第一次上机", {"st": "on" if (lday or tday) else "", "txt": "已解锁" if (lday or tday) else "还没开始",
                          "pct": 1.0 if (lday or tday) else None,
                          "when": ("第 %d 天" % first_learn) if first_learn else None}),
        ("🌅", "早鸟", t_badge(1, early)),
        ("🌙", "夜猫子", t_badge(1, late)),
        ("⭐", "完美一天", t_badge(4, best_day, " 个")),
        ("🗓", "完美一周", n_badge(5, best_week_days, " 天")),
        ("🔥", "连续七天", n_badge(7, streak_best, " 天")),
        ("💎", "连续三十天", n_badge(30, streak_best, " 天")),
        ("💯", "百番", t_badge(100, total_n)),
        ("⏱", "十小时", t_badge(600, total_min, " 分")),
        ("📚", "百词", n_badge(100, learned, " 词")),
        ("🏆", "五十掌握", n_badge(50, mastered, " 词")),
    ]
    unlocked = sum(1 for _, _, b in badges if b["st"] == "on")

    # ── 拼页 ──────────────────────────────────────────────
    h = []
    h.append('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">')
    h.append('<meta name="robots" content="noindex,nofollow">')
    h.append('<title>学习看板</title><style>%s</style></head><body>' % CSS)

    h.append('<div class="hdr"><h1>学习陪伴机</h1><span class="sub">%s</span></div>' %
             (("学习第 %s 天" % esc(cur_day)) if cur_day else "学习看板"))

    # ① 今天
    h.append('<div class="card"><div class="k">今天专注</div>')
    if has_tomato:
        h.append('<div class="big">%d<small>个番茄 · %d 分钟</small></div>' % (t_today["n"], t_today["min"]))
    else:
        h.append('<div class="big">—<small>番茄还没接入</small></div>')
    h.append('<div class="chips">')
    h.append('<span class="chip">🔥 连续 <b>%d</b> 天</span>' % streak_now)
    if has_tomato:
        h.append('<span class="chip">⭐ 本周 <b>%d</b>/7 天</span>' % sum(1 for _, v in week if v["n"] >= 1))
    h.append('<span class="chip">今天复习 <b>%d</b> 次</span>' % rev_today)
    h.append('</div>')
    if not has_tomato:
        h.append('<div class="note">番茄上报是 <b>2026-10-31</b> 才接上的；之前的番茄只存在设备里，'
                 '没有记录 —— 这里显示「未接入」，不是 0。<br>连续天数先按<b>学习日</b>算（番茄接入后按真实日期）。</div>')
    elif streak_hybrid:
        h.append('<div class="note">连续天数<b>取高</b>：学习日 <b>%d</b> 天 ／ 番茄 <b>%d</b> 天'
                 '（图表按真实日期；番茄追上后自动只按番茄算）。</div>' % (streak_learn, streak_tomato))
    h.append('</div>')

    # ② 本周
    h.append('<div class="card"><div class="k">%s</div>' %
             ("本周（周一起算）" if has_tomato else "最近 14 个学习日"))
    if has_tomato:
        h.append('<div class="chart">')
        for dt_, v in week:
            pct = int(round(v["min"] / float(wmax) * 100))
            cls = "col hot" if dt_ == today else "col"
            h.append('<div class="%s"><div class="bar" style="height:%d%%"><span>%s</span></div>'
                     '<div class="dl">%s</div></div>'
                     % (cls, max(pct, 2 if v["min"] else 1), ("%d" % v["min"]) if v["min"] else "",
                        WEEK_CN[dt_.weekday()]))
        h.append('</div>')
        delta = wmin - lmin
        h.append('<div class="note">本周 <b>%d</b> 个 · %d 分钟 · 比上周 %s%d 分钟</div>'
                 % (wtom, wmin, "+" if delta >= 0 else "", delta))
    else:
        # 退到学习日口径：最近 14 个学习日的复习量（番茄接入后自动换成上面的分钟柱状）
        h.append('<div class="chart">')
        lo = max(1, (cur_day or 1) - 13)
        vals = [lday.get(i, {"n": 0})["n"] for i in range(lo, (cur_day or 1) + 1)]
        mx = max(vals + [1])
        for i in range(lo, (cur_day or 1) + 1):
            n = lday.get(i, {"n": 0})["n"]
            cls = "col hot" if i == cur_day else "col"
            h.append('<div class="%s"><div class="bar" style="height:%d%%"><span>%s</span></div>'
                     '<div class="dl">%d</div></div>'
                     % (cls, max(int(round(n / float(mx) * 100)), 2 if n else 1), n if n else "", i))
        h.append('</div>')
        h.append('<div class="note">按<b>学习日</b>看（最近 14 天）。番茄接入后，这一块自动换成'
                 '按真实日期、按分钟的周柱状。</div>')
    h.append('</div>')

    # ③ 本月
    h.append('<div class="card"><div class="k">本月 · %s</div>' % now.strftime("%Y-%m"))
    h.append('<div class="cal">')
    for w in WEEK_CN:
        h.append('<div class="wd">%s</div>' % w)
    for wk in calendar.Calendar(firstweekday=0).monthdatescalendar(today.year, today.month):
        for dd in wk:
            if dd.month != today.month:
                h.append('<div class="cell off"></div>')
                continue
            v = tday.get(dd.isoformat(), {"n": 0, "min": 0})
            score = v["min"]
            lv = "" if score == 0 else ("l1" if score < 30 else ("l2" if score < 60 else ("l3" if score < 120 else "l4")))
            h.append('<div class="cell %s%s">%d</div>'
                     % (lv, " today" if dd == today else "", dd.day))
    h.append('</div>')
    if has_tomato:
        h.append('<div class="note">本月 <b>%d</b> 个 · %d 分钟 · %d 天有练</div>' % (mtom, mmin, mactive))
    else:
        h.append('<div class="note">格子按<b>番茄分钟</b>加深；番茄还没接入，所以本月是空的。'
                 '复习流水只有「学习日」编号、没有可靠日期，<b>不拿收到时刻冒充</b>。</div>')
    h.append('</div>')

    # ④ 词语
    h.append('<div class="card"><div class="k">词语</div>')
    h.append('<div class="big">%d<small>/ %d 学过 · 掌握 %d</small></div>' % (learned, total_words, mastered))
    denom = float(max(1, hot + mid + cold))
    h.append('<div class="stack">')
    h.append('<i style="width:%.1f%%;background:#35e0d0"></i>' % (hot / denom * 100))
    h.append('<i style="width:%.1f%%;background:#4a9dff"></i>' % (mid / denom * 100))
    h.append('<i style="width:%.1f%%;background:#8a7bff"></i>' % (cold / denom * 100))
    h.append('</div><div class="lg">')
    h.append('<span><i class="dot" style="background:#35e0d0"></i>熟 <b>%d</b></span>' % hot)
    h.append('<span><i class="dot" style="background:#4a9dff"></i>会 <b>%d</b></span>' % mid)
    h.append('<span><i class="dot" style="background:#8a7bff"></i>在学 <b>%d</b></span>' % cold)
    h.append('</div>')
    h.append('<div class="note">熟 = 稳定性 ≥21 天（Anki 的 mature 口径）· 会 7–21 天 · 在学 &lt;7 天</div>')
    # 反应时（2026-10-31）：先只看相对变化；样本 <200 条只报数，不下结论
    rt_ok = [e for e in events if e.get("rt", 0) > 0 and e["g"] >= 3]
    if rt_ok:
        vals = sorted(e["rt"] for e in rt_ok)
        med = vals[len(vals) // 2]
        fast = sum(1 for v in vals if v < 1000)
        h.append('<div class="k" style="margin-top:14px">反应时</div>')
        if len(vals) < 200:
            h.append('<div class="note">样本还在收（<b>%d</b> 条），先不下结论：认识/熟知中位 <b>%.1f s</b>，秒过（&lt;1 s）<b>%d%%</b>。</div>'
                     % (len(vals), med / 1000.0, int(100.0 * fast / len(vals))))
        else:
            h.append('<div class="note">认识/熟知中位 <b>%.1f s</b> · 秒过（&lt;1 s）<b>%d%%</b> · 样本 %d</div>'
                     % (med / 1000.0, int(100.0 * fast / len(vals)), len(vals)))
        fl = sorted(e["flip"] for e in events if e.get("flip", 0) > 0)
        if fl:
            medf = fl[len(fl) // 2]
            h.append('<div class="note">回忆中位 <b>%.1f s</b>（卡片出现 → 第一次翻面，%d 条）</div>'
                     % (medf / 1000.0, len(fl)))
        hes = {}
        for e in rt_ok:
            if e["rt"] >= 8000:
                hes[e["id"]] = hes.get(e["id"], 0) + 1
        if hes:
            top = sorted(hes.items(), key=lambda kv: -kv[1])[:6]
            names = []
            for wid, n in top:
                nm = words[wid] if 0 <= wid < len(words) else "#%d" % wid
                names.append("%s（%d 次）" % (esc(nm), n))
            h.append('<div class="note">迟疑词（&gt;8 s 还想起来了）：%s</div>' % "、".join(names))
    # 新词进度（T5）
    h.append('<div class="k" style="margin-top:14px">新词进度</div>')
    h.append('<div class="big">剩 %d<small> 词 · 共 %d</small></div>' % (remaining, total_words))
    pct = (100.0 * learned / total_words) if total_words else 0.0
    h.append('<div class="stack"><i style="width:%.1f%%;background:#4a9dff"></i></div>' % pct)
    if eta:
        days_left = (eta - today).days
        h.append('<div class="note">按近 7 日新词均值 <b>%.1f</b> 词/天 → 预计 <b>%s</b> 学完（还剩 %d 天）</div>'
                 % (avg_new, eta.isoformat(), days_left))
    else:
        h.append('<div class="note">新词均值还不够（最近没有新词记录），先积累几天再算完成日。</div>')
    # 错词 Top20
    if leech_top:
        h.append('<div class="k" style="margin-top:14px">错词 Top20（忘记次数）</div><div class="wrap">')
        for laps, wid, sv in leech_top:
            nm = esc(words[wid])
            h.append('<span class="wtag">%s ×%d</span>' % (nm, laps))
        h.append('</div>')
        h.append('<div class="note">按 FSRS 的 lapses（忘记次数）排；同分按词表顺序。</div>')
    # 错词本（T6）
    if leech_cur:
        h.append('<div class="k" style="margin-top:14px">错词本（今天 ≤10 个，插在复习最前）</div><div class="wrap">')
        for laps2, bad2, wid2 in leech_cur:
            nm = words[wid2] if 0 <= wid2 < len(words) else "#%d" % wid2
            h.append('<span class="wtag">%s</span>' % esc(nm))
        h.append('</div>')
        h.append('<div class="note">规则：忘记次数 ≥3 或最近 5 次里 ≥3 次忘记/模糊；最近两次都认识就出列。</div>')
    # 周默写记录
    h.append('<div class="k" style="margin-top:14px">周默写</div>')
    sheets = []
    try:
        sd = os.path.join(DATA, "默写")
        if os.path.isdir(sd):
            sheets = sorted([n for n in os.listdir(sd) if n.startswith("默写-") and n.endswith(".html")])[::-1][:4]
    except Exception:
        sheets = []
    if sheets:
        h.append('<div class="wrap">')
        for n in sheets:
            stem = n[3:-5]
            h.append('<a class="wtag" href="默写/%s" target="_blank" rel="noopener">%s</a>'
                     % (esc(n), esc(stem)))
            h.append('<a class="wtag" href="默写/默写-%s.docx">Word</a>' % esc(stem))
        h.append('</div>')
        h.append('<div class="note">点日期在线看/打印（A4，第二页答案）；点 <b>Word</b> 下载 .docx（A4 页面，Word/WPS 直接打印）。</div>')
    if dict_rows:
        rows_show = dict_rows[-8:][::-1]
        h.append('<div class="wrap">')
        for r in rows_show:
            d = esc(str(r.get("date", "?")))
            sc = r.get("score", "?")
            h.append('<span class="wtag">%s ｜ %s 分</span>' % (d, esc(str(sc))))
        h.append('</div>')
    elif sheets:
        h.append('<div class="note">还没有分数记录 —— 默写完把分数和错词告诉我，我记进 <code>默写.jsonl</code>。</div>')
    else:
        h.append('<div class="note">还没有默写单 —— 到周日跑 <code>出默写单.py</code> 生成第一张。</div>')
    if today_words:
        h.append('<div class="k" style="margin-top:14px">今天练过</div><div class="wrap">')
        for w in today_words[:24]:
            h.append('<span class="wtag">%s</span>' % esc(w))
        h.append('</div>')
    else:
        h.append('<div class="note">今天还没有复习记录。</div>')
    h.append('</div>')

    # ⑤ 勋章墙
    h.append('<div class="card"><div class="k">勋章墙 · 已解锁 %d / %d</div><div class="bgrid">'
             % (unlocked, len(badges)))
    for ico, name, b in badges:
        cls = "bd" + (" on" if b["st"] == "on" else (" todo" if b["st"] == "todo" else ""))
        txt = b["txt"]
        if b["st"] == "on" and b["when"]:
            txt = "%s · %s" % (txt, b["when"])
        h.append('<div class="%s"><div class="bi">%s</div><div class="bn">%s</div><div class="bs">%s</div>'
                 % (cls, ico, esc(name), esc(txt)))
        if b["st"] != "on" and b["pct"] is not None and b["pct"] < 1.0:
            h.append('<div class="bp"><i style="width:%d%%"></i></div>' % int(round(b["pct"] * 100)))
        h.append('</div>')
    h.append('</div><div class="blegend">亮 = 已解锁 · 灰 = 未解锁（下面带进度）· 虚线 = 等番茄数据</div>')
    cand = [(b["pct"], ico, name, b["txt"]) for ico, name, b in badges
            if b["st"] not in ("on", "todo") and b["pct"] is not None]
    if cand:
        pct, ico, name, txt = max(cand)
        h.append('<div class="note">下一个：<b>%s %s</b> —— %s（%d%%）</div>'
                 % (ico, esc(name), esc(txt), int(round(pct * 100))))
    h.append('</div>')

    # ⑥ 评分分布
    h.append('<div class="card"><div class="k">按起来的样子（评分分布，共 %d 次）</div>' % sum(gcount.values()))
    for g in (1, 2, 3, 4):
        h.append('<div class="grow"><span class="gn">%s</span><div class="gbar"><i style="width:%d%%"></i></div>'
                 '<span class="gv">%d</span></div>'
                 % (gname[g], int(round(gcount[g] / float(gtotal) * 100)), gcount[g]))
    h.append('<div class="note">忘记/模糊占 <b>%d%%</b> —— 这份词表对你偏难还是偏易，一眼能看出来</div>'
             % int(round((gcount[1] + gcount[2]) / float(gtotal) * 100)))
    h.append('</div>')

    h.append('<div class="foot">只读页面 · 刷新看最新 · 最后更新 %s<br>'
             '番茄 %d 条 · 复习 %d 条 · 快照 %d 张卡</div>'
             % (now.strftime("%m-%d %H:%M"), len(tomatos), len(events), len(cards)))
    h.append('</body></html>')
    return "".join(h)


# ────────────────────────────────────────────────────────── 服务
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "aipboard/1.0"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        p = (self.path or "/").split("?")[0]
        if self.command not in ("GET", "HEAD"):
            return self._send(405, "只读页面", "text/plain; charset=utf-8", {"Allow": "GET, HEAD"})
        if p == "/healthz":            # 本机自检用；门只转发 /s/<串>/，它露不出去
            return self._send(200, json.dumps({"ok": True, "tomato": len(load_tomato()),
                                               "events": len(load_events())}), "application/json")
        t = token()
        if not (p == "/s/" + t or p == "/s/" + t + "/" or p.startswith("/s/" + t + "/")):
            return self._send(404, '<!doctype html><meta charset="utf-8"><body style="background:#0a1018;'
                                   'color:#42546b;font:15px -apple-system,sans-serif;padding:40px 20px">'
                                   '404 Not Found</body>')
        # 默写单下载/打印：只放行 默写/默写-YYYY-MM-DD.html 这一种名字（防目录穿越）
        import urllib.parse as _up
        pre = "/s/" + t + "/默写/"
        p_dec = _up.unquote(p)                      # 中文目录名会以 %E9... 进来
        if p_dec.startswith(pre):
            name = p_dec[len(pre):]
            m = re.match(r"^默写-(\d{4}-\d{2}-\d{2})\.(html|docx)$", name)
            if m:
                fp = os.path.join(DATA, "默写", name)
                if os.path.isfile(fp):
                    if m.group(2) == "docx":
                        ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        cd = "attachment; filename=\"dictation-%s.docx\"" % m.group(1)
                        with open(fp, "rb") as f:
                            return self._send(200, f.read(), ctype, {"Content-Disposition": cd})
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
            return self._send(404, "没有这张默写单", "text/plain; charset=utf-8")
        try:
            # 2026-11-02：错词本打印页（看板上的「打印错词本」入口）
            if p_dec.startswith("/s/" + t + "/错词本/"):
                return self._send(200, leech_page(), "text/html; charset=utf-8")
            # 2026-11-01 19:5x 看板重设计（v1 曾被嫌眼花）：v2 在 board_v2.py，出错自动退回 v1
            try:
                import board_v2
                body = board_v2.render_v2()
            except Exception as _e2:
                import logging as _lg
                _lg.getLogger("board").warning("board_v2 失败，退回 v1：%r", _e2)
                body = render()
        except Exception as e:
            return self._send(500, '<pre style="color:#e8eef7;background:#0a1018">看板渲染失败：%s</pre>' % esc(e))
        return self._send(200, body)

    do_GET = _route
    do_HEAD = _route

    def do_POST(self):
        self._send(405, "只读页面", "text/plain; charset=utf-8", {"Allow": "GET, HEAD"})


def main():
    t = token()
    # 令牌**不进日志**：只打在用户终端不上——这里只报端口与令牌文件位置
    print("学习看板：监听 %s:%d （路径 /s/<串>/，串见 %s；只读；数据目录 %s）"
          % (HOST, PORT, TOKEN_FILE, DATA), flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--render":
        sys.stdout.write(render())
    elif arg == "--token":
        print(token())
    elif arg == "--stats":
        d = collect()
        hot, mid, cold = buckets(d["state"])
        print(json.dumps({"tomato_rows": len(d["tomatos"]),
                          "tomato_min": sum(t["dur"] for t in d["tomatos"]),
                          "events": len(d["events"]), "learn_days": len(d["lday"]),
                          "cur_day": d["cur_day"], "today": str(d["today"]),
                          "hot": hot, "mid": mid, "cold": cold}, ensure_ascii=False, indent=2))
    else:
        main()
