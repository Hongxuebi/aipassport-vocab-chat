#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_v2.py —— 学习看板 v2（2026-11-01 19:5x 重设计）

起因：旧看板被嫌"太一般、看着眼花"，于是整页重排。
v1 的问题（截图逐条数的）：① 卡片一样重、没有主次 ② 本月日历占 1/3 屏且今天孤立在左上
③ 数字/文字一样小，12px 一排排堆着 ④ 勋章/错词/默写/评分全平铺，一屏十来个模块。
v2 的取舍：**首屏只回答三件事** —— 今天练够没、总共会了多少、下一步干什么；
其余（错词/默写/评分/今天练过）收进 <details>，想翻再翻。不引外部依赖、不开 JS，弱网也能看。

设计依据（网上找的，不是拍脑袋）：
  · Duolingo 的连续天数机制：**单一每日目标 + 一眼可见的 streak**，靠"不想断"驱动习惯
    https://blog.duolingo.com/how-duolingo-streak-builds-habit/
  · Duolingo 徽章/XP 拆解：里程碑要**少而明确**，下一个目标永远看得见
    https://apptitude.io/blog/how-duolingos-streak-mechanic-actually-works/
  · 学习类设计系统（Sanoma Learning）：面板分层——**先结论、后明细**，同级卡片才并排
    https://sanomalearning.design/categories/components/panel/usage/

接口：render_v2(now=None) → 整页 HTML（web.py 只调这一个）。
"""
import datetime as dt
import html
import os
import re

import web  # 复用 loaders / buckets / streak 等（导入不会启动服务）

ACCENT = "#35e0d0"
ACCENT2 = "#f2b544"

CSS = ("""
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#0a1018;--card:#111a24;--card2:#0e1620;--line:#1c2836;--txt:#e9eef5;
  --dim:#93a5b9;--faint:#5d6e82;--accent:@ACCENT@;--warn:@WARN@}
html{overflow-x:hidden}
body{margin:0 auto;width:100%;background:var(--bg);color:var(--txt);max-width:430px;overflow-x:hidden;
  font:15px/1.5 -apple-system,"PingFang SC","Hiragino Sans GB",system-ui,sans-serif;
  padding:14px 14px 30px;-webkit-font-smoothing:antialiased}
.top{display:flex;align-items:baseline;justify-content:space-between;margin:2px 2px 14px}
.top b{font-size:18px;font-weight:650;letter-spacing:.3px}
.top span{font-size:12px;color:var(--faint)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px;margin-bottom:11px}
.card.hero{background:linear-gradient(155deg,#12222b,#0e1720 62%);border-color:#1f3a3f}
.heroRow{display:flex;align-items:center;gap:12px}
.ringBox{position:relative;width:108px;height:108px;flex:0 0 auto}
.ringTxt{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.ringTxt b{font-size:28px;font-weight:700;line-height:1;letter-spacing:-1px}
.ringTxt i{font-style:normal;font-size:11px;color:var(--dim);margin-top:5px}
.heroR{flex:1;min-width:0}
.heroR .t{font-size:13px;color:var(--dim)}
.heroR .n{font-size:15px;margin-top:3px}
.heroR .n b{font-size:22px;font-weight:700;letter-spacing:-.5px}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}
.chip{font-size:12px;color:var(--dim);background:#0f1a24;border:1px solid var(--line);
  border-radius:9px;padding:4px 9px;white-space:nowrap}
.chip b{color:var(--txt);font-weight:600}
.chip.hot{color:var(--accent);border-color:#1d4a48;background:#0d2422}
.k{font-size:12px;color:var(--faint);letter-spacing:.4px;margin-bottom:8px;display:flex;
  justify-content:space-between;align-items:baseline}
.k em{font-style:normal;color:var(--dim);font-size:11px}
.big{font-size:34px;font-weight:700;letter-spacing:-1px;line-height:1}
.big small{font-size:13px;font-weight:400;color:var(--faint);letter-spacing:0;margin-left:5px}
.stack{display:flex;height:12px;border-radius:6px;overflow:hidden;background:#16202c;margin:13px 0 9px}
.stack i{display:block;height:100%}
.legend{display:flex;gap:14px;font-size:12px;color:var(--dim);flex-wrap:wrap}
.legend b{color:var(--txt);font-weight:600}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;vertical-align:1px}
.sub{font-size:12px;color:var(--faint);margin-top:10px;line-height:1.6}
.sub b{color:var(--dim)}
.bars{display:flex;align-items:flex-end;gap:6px;height:82px;margin-top:12px}
.col{flex:1 1 0;min-width:0;display:flex;flex-direction:column;justify-content:flex-end;height:100%;align-items:center}
.bar{width:100%;background:#1d3a52;border-radius:4px 4px 0 0;min-height:3px}
.col.on .bar{background:var(--accent)}
.dl{font-size:10px;color:var(--faint);margin-top:6px}
.col.on .dl{color:var(--accent)}
.bgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 8px;margin-top:4px}
.bd{text-align:center;padding:8px 4px 7px;border-radius:12px;border:1px solid transparent;min-width:0}
.bd.on{background:#0e2422;border-color:#1e4a46}
.bd.off{background:#0d151e;border-color:#18222e;opacity:.72}
.be{font-size:20px;line-height:1;filter:grayscale(1);opacity:.5}
.bd.on .be{filter:none;opacity:1}
.bn{font-size:11px;color:var(--faint);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bd.on .bn{color:var(--txt)}
.bp{height:3px;border-radius:2px;background:#16202c;margin:6px 6px 0;overflow:hidden}
.bp i{display:block;height:100%;background:var(--accent);border-radius:2px}
.next{font-size:12px;color:var(--dim);margin-top:12px;padding-top:11px;border-top:1px dashed var(--line)}
.next b{color:var(--accent)}
.road{display:flex;flex-direction:column;gap:9px;margin-top:2px}
.rd{display:flex;gap:9px;align-items:flex-start;font-size:13px;line-height:1.45}
.rd .ic{flex:0 0 16px;text-align:center;font-size:13px}
.rd .tx{flex:1;min-width:0;color:var(--dim)}
.rd .tx b{color:var(--txt);font-weight:600}
.st{flex:0 0 auto;font-size:10px;padding:2px 6px;border-radius:6px;white-space:nowrap;margin-top:1px}
.st.done{color:#7fe8dd;background:#0e2422;border:1px solid #1e4a46}
.st.now{color:#ffd27d;background:#241d0d;border:1px solid #4a3a14}
.st.todo{color:#7f8ea3;background:#0d151e;border:1px solid #1c2836}
details{margin-top:2px}
summary{cursor:pointer;font-size:13px;color:var(--dim);padding:11px 14px;background:var(--card2);
  border:1px solid var(--line);border-radius:12px;list-style:none}
summary::-webkit-details-marker{display:none}
summary:after{content:"＋";float:right;color:var(--faint)}
details[open] summary:after{content:"－"}
.dcard{background:var(--card2);border:1px solid var(--line);border-top:none;border-radius:0 0 12px 12px;
  padding:12px 14px;margin-top:-6px}
.dcard .k{margin-top:6px}
.tag{display:inline-block;font-size:12px;color:var(--dim);background:#0f1a24;border:1px solid var(--line);
  border-radius:7px;padding:2px 7px;margin:3px 4px 0 0}
.tag b{color:var(--txt)}
.foot{font-size:11px;color:#3f4e60;text-align:center;margin-top:14px;line-height:1.7}
""" + """

.qrow{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px dashed var(--line);font-size:13px}
.qrow:last-of-type{border-bottom:none}
.qrow .qi{width:18px;text-align:center}
.qrow .qt{flex:1;min-width:0;color:var(--dim)}
.qrow .qt b{color:var(--txt)}
.qrow .qv{color:var(--faint);font-size:12px}
.qrow.ok .qt{color:var(--txt)}
.trow{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-top:9px}
.tl{font-size:12px;color:var(--dim);white-space:nowrap}
.tt{display:flex;gap:4px}
.tt1{font-size:11px;color:#7f8ea3;background:#0d151e;border:1px solid #1c2836;border-radius:6px;padding:2px 7px;min-width:34px;text-align:center}
.tt1.on{color:#06231f;background:var(--accent);border-color:var(--accent);font-weight:700}
.tbar{height:5px;background:#16202c;border-radius:3px;margin:6px 0 0;overflow:hidden}
.tcap{font-size:10px;color:var(--faint);text-align:right;margin-top:2px}
.tbar i{display:block;height:100%;background:var(--accent);border-radius:3px}
.stars{margin-top:8px}
.starr{display:flex;align-items:center;gap:3px;margin-top:3px}
.starr .bk2{font-size:10px;color:var(--faint);width:24px;flex:0 0 24px}
.st{font-size:13px;color:#2b3a4a;line-height:1}
.st.on{color:var(--accent)}
.barwrap{height:8px;border-radius:5px;background:#16202c;margin-top:10px;overflow:hidden}
.barwrap i{display:block;height:100%;background:var(--accent);border-radius:5px}
.lnk{margin-top:8px}
.lnk a{font-size:12px;color:var(--accent);text-decoration:none;border:1px solid #1d4a48;background:#0d2422;border-radius:8px;padding:3px 9px}
.wchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.wchip{font-size:12px;color:#ffb3a7;background:#2a1512;border:1px solid #4a2a22;border-radius:8px;padding:3px 8px}
.wchip b{color:#ffd9d0;font-weight:600}
.fore{display:flex;gap:5px;margin-top:10px}
.fc{flex:1 1 0;min-width:0;text-align:center}
.fc .fb{height:46px;display:flex;align-items:flex-end;justify-content:center}
.fc .fb i{display:block;width:100%;background:#22405a;border-radius:3px 3px 0 0;min-height:2px}
.fc.today .fb i{background:var(--accent)}
.fc .fn{font-size:12px;color:var(--dim);margin-top:4px}
.fc .fd{font-size:10px;color:var(--faint)}
.ug{width:100%;border-collapse:collapse;margin-top:6px}
.ug td{padding:2px 1px;text-align:center;font-size:11px;color:var(--faint)}
.ug td.bk{text-align:left;padding-right:4px;color:var(--dim);font-size:11px;white-space:nowrap}
.ug td.c{width:11%;border-radius:5px}
.ug td.c em{font-style:normal;font-size:11px}
.tr{display:flex;align-items:flex-end;gap:3px;height:56px;margin-top:8px}
.tr .tc{flex:1 1 0;min-width:0;display:flex;flex-direction:column;justify-content:flex-end;height:100%}
.tr .tc i{display:block;width:100%;background:#2b4a63;border-radius:2px 2px 0 0;min-height:2px}
.tr .tc.on i{background:var(--accent)}
.trl{display:flex;justify-content:space-between;font-size:10px;color:var(--faint);margin-top:4px}
""").replace("@ACCENT@", ACCENT).replace("@WARN@", ACCENT2)


def esc(s):
    return html.escape(str(s))


def ring(pct, size=108, sw=9):
    """纯 SVG 进度环（不靠 JS）：pct 0..1"""
    pct = max(0.0, min(1.0, float(pct)))
    r = (size - sw) / 2.0
    c = 2 * 3.141592653589793 * r
    off = c * (1 - pct)
    return (
        '<svg width="%d" height="%d" viewBox="0 0 %d %d">'
        '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#1a2735" stroke-width="%d"/>'
        '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="%d" '
        'stroke-linecap="round" stroke-dasharray="%.1f" stroke-dashoffset="%.1f" '
        'transform="rotate(-90 %.1f %.1f)"/>'
        "</svg>"
        % (size, size, size, size, size / 2.0, size / 2.0, r, sw,
           size / 2.0, size / 2.0, r, ACCENT, sw, c, off, size / 2.0, size / 2.0)
    )


def badge(need, cur, unit="", kind="n"):
    """返回 (状态, 文字, 进度0..1)"""
    if need <= 0:
        return ("on", "已解锁", 1.0)
    p = min(1.0, cur / float(need))
    if cur >= need:
        return ("on", "已解锁", 1.0)
    return ("off", "%d/%d%s" % (cur, need, unit), p)


def render_v2(now=None):
    d = web.collect(now)
    now, today = d["now"], d["today"]
    tomatos, events, state, words = d["tomatos"], d["events"], d["state"], d["words"]
    cards, cur_day = d["cards"], d["cur_day"]
    tday, lday = d["tday"], d["lday"]
    has_tomato = bool(tomatos)
    prev_d = lambda x: x - dt.timedelta(days=1)   # noqa: E731

    # ---- 连续天数（同一口径：番茄日 ∪ 学习事件日 ∪ 日记日）----
    act_dates = {web._d(k) for k, v in tday.items() if v["n"] >= 1}
    s_tomato = web.streak(act_dates, today, prev_d) if act_dates else 0
    best_tomato = web.streak_max(act_dates, prev_d) if act_dates else 0
    act_ev = set()
    for e in events:
        try:
            act_ev.add(dt.datetime.fromtimestamp(float(e["at"])).date())
        except Exception:
            pass
    dset = set()
    try:
        import glob as _g
        for fp in _g.glob(os.path.expanduser("~/aipassport/mem/diary/*.json")):
            try:
                dset.add(web._d(os.path.basename(fp)[:-5]))
            except Exception:
                pass
    except Exception:
        pass
    act_all = act_ev | dset
    s_learn = web.streak_date(act_all, today) if act_all else 0
    streak_now = max(s_tomato, s_learn)
    streak_best = max(best_tomato, s_learn)

    # ---- 今天 ----
    t_today = tday.get(today.isoformat(), {"n": 0, "min": 0})
    _base0 = (d["state"].get("day_base") or {})
    _dn = (int(_base0.get("d0") or 0) + (today - __import__("datetime").date.fromisoformat(_base0["date"])).days) \
        if _base0.get("date") and _base0.get("d0") else 0
    rev_today = sum(1 for _e in events if int(_e.get("day") or 0) == _dn)
    GOAL_MIN = 15

    # ---- 词汇 ----
    hot, mid, cold = web.buckets(state)
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
    remaining = max(0, total_words - learned)

    # 近 7 个学习日的新词均值 → 预计学完
    first_day = {}
    for e in events:
        if e["id"] >= 0:
            dd = e["day"]
            if e["id"] not in first_day or dd < first_day[e["id"]]:
                first_day[e["id"]] = dd
    new_by_day = {}
    for wid, day in first_day.items():
        new_by_day[day] = new_by_day.get(day, 0) + 1
    last7 = [v for _k, v in sorted(new_by_day.items())[-7:]]
    pace = (sum(last7) / float(len(last7))) if last7 else 0.0
    eta = ""
    if pace > 0 and remaining > 0:
        eta = (today + dt.timedelta(days=int(remaining / pace))).isoformat()

    # ---- 本周柱 ----
    mon = today - dt.timedelta(days=today.weekday())
    week = []
    for i in range(7):
        dd = mon + dt.timedelta(days=i)
        v = tday.get(dd.isoformat(), {"n": 0, "min": 0})
        week.append((dd, v["min"]))
    wmax = max([m for _d0, m in week] + [1])
    wsum = sum(m for _d0, m in week)

    # ---- 勋章（12 枚，口径同 v1）----
    total_n = len(tomatos)
    total_min = sum(t["dur"] for t in tomatos)
    best_day = max([v["n"] for v in tday.values()] + [0])
    early = sum(1 for t in tomatos if t["hour"] < 8)
    late = sum(1 for t in tomatos if t["hour"] >= 22)
    week_tomato = 0
    for s in tday:
        d0 = web._d(s)
        m0 = d0 - dt.timedelta(days=d0.weekday())
        week_tomato = max(week_tomato, sum(1 for i in range(7)
                                           if (m0 + dt.timedelta(days=i)).isoformat() in tday))
    week_learn = 0
    for s in lday:
        week_learn = max(week_learn, sum(1 for i in range(7) if (s + i) in lday))
    best_week = max(week_tomato, week_learn)

    raw = [
        ("🍅", "第一次专注", badge(1, total_n)),
        ("🌱", "第一次上机", ("on", "已解锁", 1.0) if (lday or tday) else ("off", "还没开始", 0.0)),
        ("🌅", "早鸟", badge(1, early) if has_tomato else ("off", "待接入", 0.0)),
        ("🌙", "夜猫子", badge(1, late) if has_tomato else ("off", "待接入", 0.0)),
        ("⭐", "完美一天", badge(4, best_day, " 个") if has_tomato else ("off", "待接入", 0.0)),
        ("🗓", "完美一周", badge(5, best_week, " 天")),
        ("🔥", "连续七天", badge(7, streak_best, " 天")),
        ("💎", "连续三十天", badge(30, streak_best, " 天")),
        ("💯", "百番", badge(100, total_n) if has_tomato else ("off", "待接入", 0.0)),
        ("⏱", "十小时", badge(600, total_min, " 分") if has_tomato else ("off", "待接入", 0.0)),
        ("📚", "百词", badge(100, learned, " 词")),
        ("🏆", "五十掌握", badge(50, mastered, " 词")),
    ]
    unlocked = sum(1 for _e, _n, b in raw if b[0] == "on")
    locked = [(n, b) for _e, n, b in raw if b[0] != "on"]
    nxt = max(locked, key=lambda x: x[1][2]) if locked else None

    # ---- 详情（折叠）：错词 / 默写 / 评分 ----
    gcount = {1: 0, 2: 0, 3: 0, 4: 0}
    for e in events:
        if e["g"] in gcount:
            gcount[e["g"]] += 1
    gtotal = sum(gcount.values()) or 1
    gname = {1: "忘记", 2: "模糊", 3: "认识", 4: "熟知"}

    leech = []
    hist = {}
    for e in events:
        try:
            wid2 = int(e.get("id", -1))
            g2 = int(e.get("g") or 0)
        except Exception:
            continue
        if wid2 >= 0:
            hist.setdefault(wid2, []).append(g2)
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
            leech.append((laps2, wid2))
    leech.sort(key=lambda x: (-x[0], x[1]))
    leech = leech[:8]

    dict_rows = []
    try:
        dp = os.path.join(web.DATA, "默写.jsonl")
        if os.path.exists(dp):
            for line in open(dp, encoding="utf-8"):
                line = line.strip()
                if line:
                    dict_rows.append(line)
    except Exception:
        pass

    today_ids = lday.get(cur_day, {"ids": []})["ids"] if cur_day else []
    today_words = [words[i] for i in today_ids if 0 <= i < len(words)]


    # ---- 错词本 / 复习强度预测 / 每单元进度 / 14 天趋势（2026-11-02 要求的）----
    import csv as _csv
    import datetime as _dt
    import aipassport_server as _srv
    leeched = _srv.leech_list(state, events)
    day_now = _srv.day_now(state)
    cards_raw = state.get("cards", {})
    forecast = [0] * 7
    for _k, _c in cards_raw.items():
        try:
            _sv = float(_c.get("S") or 0)
            _last = int(_c.get("last_day") or 0)
        except Exception:
            continue
        _ivl = min(_srv.fsrs.interval_days(_sv, _srv.DESIRED), _srv.MAX_IVL)
        _off = (_last + _ivl) - day_now
        if _off < 0:
            _off = 0
        if _off < 7:
            forecast[_off] += 1
    learned_ids = card_ids | ev_ids
    try:
        _wrows = list(_csv.DictReader(open(os.path.join(web.DATA, "words.csv"), encoding="utf-8-sig")))
    except Exception:
        _wrows = []
    units = {}
    for _i, _r in enumerate(_wrows):
        _b = ((_r.get("备注") or "").split("｜")[0]) or "其他"
        _u = (_r.get("单元") or "").replace(" ", "")
        if not _u:
            continue
        _v = units.setdefault((_b, _u), [0, 0, 0])
        _v[0] += 1
        if _i in learned_ids:
            _v[1] += 1
            _cc = cards_raw.get(str(_i)) or {}
            try:
                if float(_cc.get("S") or 0) >= 21:
                    _v[2] += 1
            except Exception:
                pass
    by_day = {}
    _base = state.get("day_base") or {}
    _d0, _date0 = int(_base.get("d0") or 0), _base.get("date") or ""
    _base_date = None
    if _d0 and _date0:
        try:
            _base_date = _dt.date.fromisoformat(_date0)
        except Exception:
            _base_date = None
    for _e in events:
        try:
            if _base_date is not None:
                _dd = _base_date + _dt.timedelta(days=int(_e.get("day") or 0) - _d0)
            else:
                _dd = _dt.datetime.fromtimestamp(float(_e["at"])).date()
        except Exception:
            continue
        by_day[_dd] = by_day.get(_dd, 0) + 1
    trend = []
    for _i in range(13, -1, -1):
        _dd = today - _dt.timedelta(days=_i)
        trend.append((_dd, by_day.get(_dd, 0)))
    tmax = max([n for _d0, n in trend] + [1])


    # ---- 分层成就 / 收集 / 今日任务（2026-11-02 11:1x 要求"别满了就没动力"）----
    _today_new = sum(1 for _c in cards_raw.values() if int(_c.get("first_day") or 0) == _dn)
    _units_done = sum(1 for _v in units.values() if _v[0] and _v[1] >= _v[0])
    _total_rev = len(events)
    TIERS = [
        ("🔥", "连续", [7, 30, 100], streak_best, "天"),
        ("📚", "词量", [100, 500, 1000], learned, "词"),
        ("🏆", "熟练", [50, 200, 500], mastered, "词"),
        ("📈", "复习", [500, 2000, 10000], _total_rev, "张"),
        ("⭐", "完美日", [2, 10, 30], best_day, "个"),
        ("🗓", "完美周", [3, 10, 25], best_week, "周"),
        ("🧩", "单元", [1, 12, max(1, len(units))], _units_done, "个"),
    ]

    # ================= 拼页 =================
    p = []
    p.append('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">')
    p.append('<meta name="robots" content="noindex,nofollow">')
    p.append('<title>学习看板</title><style>' + CSS + "</style></head><body>")

    p.append('<div class="top"><b>学习陪伴机</b><span>%s</span></div>' % (
        ("学习第 %s 天" % esc(cur_day)) if cur_day else "学习看板"))

    # ①.6 今日任务（每天来都有三件事，清完给一句"收工"）
    _q_due = forecast[0]
    _q_new = _today_new
    _q_go = GOAL_MIN
    _q_min = t_today["min"]
    _q_done = sum([_q_due == 0, _q_new >= 20, rev_today >= 30])
    p.append('<div class="card"><div class="k">今日任务<em>%d / 3 完成</em></div>' % _q_done)
    p.append('<div class="qrow %s"><span class="qi">%s</span><span class="qt">复习完到期的 <b>%d</b> 张</span>'
             '<span class="qv">还剩 %d</span></div>'
             % ("ok" if _q_due == 0 else "", "✅" if _q_due == 0 else "⬜", _q_due, _q_due))
    p.append('<div class="qrow %s"><span class="qi">%s</span><span class="qt">学 <b>20</b> 个新词</span>'
             '<span class="qv">%d/20</span></div>'
             % ("ok" if _q_new >= 20 else "", "✅" if _q_new >= 20 else "⬜", _q_new))
    _q_cards = 30
    p.append('<div class="qrow %s"><span class="qi">%s</span><span class="qt">复习 <b>%d</b> 张</span>'
             '<span class="qv">%d/%d</span></div>'
             % ("ok" if rev_today >= _q_cards else "", "✅" if rev_today >= _q_cards else "⬜",
                _q_cards, rev_today, _q_cards))
    p.append('<div class="sub">%s</div>' % ("三件都清了 —— 今天到此为止也行" if _q_done == 3 else "任意一件清掉，今天就算没白来"))
    p.append("</div>")

    # ② 词汇量
    p.append('<div class="card"><div class="k">词汇量<em>词表 %d 词</em></div>' % total_words)
    rate = (100.0 * mastered / learned) if learned else 0.0
    p.append('<div class="big">%d<small>/ %d 词 · 掌握 %d（%.0f%%）</small></div>'
             % (learned, total_words, mastered, rate))
    p.append('<div class="stack">')
    for n, col in ((hot, ACCENT), (mid, "#3d8fd6"), (cold, "#5b6b7d")):
        if n:
            p.append('<i style="width:%.1f%%;background:%s"></i>' % (100.0 * n / total_words, col))
    p.append("</div>")
    p.append('<div class="legend"><span><i class="dot" style="background:%s"></i>熟 <b>%d</b></span>'
             '<span><i class="dot" style="background:#3d8fd6"></i>中 <b>%d</b></span>'
             '<span><i class="dot" style="background:#5b6b7d"></i>在学 <b>%d</b></span></div>'
             % (ACCENT, hot, mid, cold))
    if remaining > 0:
        if pace > 0:
            p.append('<div class="sub">剩 <b>%d</b> 词 · 日均 <b>%.0f</b> 词 · 预计 <b>%s</b> 学完</div>'
                     % (remaining, pace, esc(eta[5:] if eta else "—")))
        else:
            p.append('<div class="sub">剩 <b>%d</b> 词 · 还没攒够日均数据</div>' % remaining)
    else:
        p.append('<div class="sub">词表已过完一遍，接着是把「在学」熬成「熟」。</div>')
    p.append("</div>")

    # ③（原「本周专注」卡：2026-11-02 和 14 天趋势重复，已删）

    # ① 今天（紧凑条：不占大字环了 —— 反馈说主要背单词，环太大）
    _wn = sum(1 for _e in events if _dn and 0 <= (_dn - int(_e.get('day') or 0)) < 7)
    _GOAL_CARDS = 30
    _goalpct = min(100, int(round(100.0 * rev_today / float(_GOAL_CARDS)))) if _GOAL_CARDS else 0
    p.append('<div class="card"><div class="k">今天<em>目标 %d 张 · 已复习 %d 张</em></div>' % (_GOAL_CARDS, rev_today))
    p.append('<div class="barwrap"><i style="width:%d%%"></i></div>' % _goalpct)
    p.append('<div class="chips">')
    p.append('<span class="chip hot">🔥 连续 <b>%d</b> 天</span>' % streak_now)
    p.append('<span class="chip">复习 <b>%d</b> 张</span>' % rev_today)
    p.append('<span class="chip">本周 <b>%d</b> 张</span>' % _wn)
    if streak_best > streak_now:
        p.append('<span class="chip">最长 <b>%d</b> 天</span>' % streak_best)
    p.append("</div></div>")

    # ①.5 错词本（显眼入口 —— 反馈说「错词本入口不显眼，我都找不到」）
    p.append('<div class="card"><div class="k">错词本<em>%d 个 · 每天排在队列最前</em></div>'
             % len(leeched))
    p.append('<div class="lnk"><a href="错词本/">打印错词本（A4）</a></div>')
    if leeched:
        p.append('<div class="wchips">')
        for _wid in leeched[:10]:
            _w = words[_wid] if 0 <= _wid < len(words) else "#%d" % _wid
            _c = cards_raw.get(str(_wid)) or {}
            p.append('<span class="wchip">%s <b>×%d</b></span>' % (esc(_w), int(_c.get("lapses") or 0)))
        p.append("</div>")
        p.append('<div class="sub">连着两次答「认识」就出列；出不来就说明该换个记法</div>')
    else:
        p.append('<div class="sub">一个都没有 —— 没有反复忘的词，挺好的</div>')
    p.append("</div>")

    # ③.1 未来 7 天复习强度（防止某天突然爆量）
    WD7 = ["今天", "明天", "周三", "周四", "周五", "周六", "周日"]
    _wk = "一二三四五六日"
    _lab = ["今天", "明天"] + ["周%s" % _wk[(today + _dt.timedelta(days=i)).weekday()] for i in range(2, 7)]
    fmax = max(forecast + [1])
    p.append('<div class="card"><div class="k">接下来 7 天要复习多少<em>按 FSRS 间隔算</em></div>')
    p.append('<div class="fore">')
    for _i, _n in enumerate(forecast):
        _h = max(3, int(round(44.0 * _n / fmax))) if _n else 2
        p.append('<div class="fc%s"><div class="fb"><i style="height:%dpx"></i></div>'
                 '<div class="fn">%d</div><div class="fd">%s</div></div>'
                 % (" today" if _i == 0 else "", _h, _n, _lab[_i]))
    p.append("</div>")
    _peak = max(range(len(forecast)), key=lambda i: forecast[i])
    p.append('<div class="sub">最重的是 <b>%s</b>（%d 张）；今天到期 <b>%d</b> 张</div>'
             % (_lab[_peak], forecast[_peak], forecast[0]))
    p.append("</div>")

    # ③.2 每单元进度
    BOOKS_ORDER = ["八上", "八下", "九上", "九下", "七上", "七下"]
    p.append('<div class="card"><div class="k">每单元进度<em>格子里＝已学 / 底色越亮越熟</em></div>')
    p.append('<table class="ug">')
    _uhead = [u for (b, u) in units if b == BOOKS_ORDER[0]]
    p.append('<tr><td class="bk"></td>' + "".join('<td>%s</td>' % u.replace("Unit", "U") for u in sorted(_uhead, key=lambda x: int(re.sub(r"\D", "", x) or 0))) + "</tr>")
    for _b in BOOKS_ORDER:
        _us = sorted([u for (bb, u) in units if bb == _b], key=lambda x: int(re.sub(r"\D", "", x) or 0))
        if not _us:
            continue
        p.append('<tr><td class="bk">%s</td>' % _b)
        for _u in _us:
            _tot, _lrn, _mst = units[(_b, _u)]
            _pct = (_lrn / float(_tot)) if _tot else 0
            _alpha = 0.12 + 0.68 * _pct
            _col = "rgba(53,224,208,%.2f)" % _alpha if _pct > 0 else "rgba(255,255,255,0.04)"
            _ink = "#06231f" if _pct > 0.55 else "#cfe9e6"
            p.append('<td class="c" style="background:%s;color:%s"><em>%d</em></td>' % (_col, _ink, _lrn))
        p.append("</tr>")
    p.append("</table>")
    _lt = sum(v[1] for v in units.values())
    _tt = sum(v[0] for v in units.values())
    p.append('<div class="sub">六册共 <b>%d</b> 条，已学 <b>%d</b>（%.0f%%）；数字是该单元已学词数</div>'
             % (_tt, _lt, 100.0 * _lt / _tt if _tt else 0))
    p.append("</div>")

    # ③.3 最近 14 天练了多少
    p.append('<div class="card"><div class="k">最近 14 天<em>每天复习张数</em></div>')
    p.append('<div class="tr">')
    for _dd, _n in trend:
        _h = max(2, int(round(52.0 * _n / tmax))) if _n else 2
        p.append('<div class="tc%s"><i style="height:%dpx"></i></div>'
                 % (" on" if _dd == today else "", _h))
    p.append("</div>")
    p.append('<div class="trl"><span>%s</span><span>今天</span></div>'
             % trend[0][0].strftime("%m-%d"))
    p.append('<div class="sub">14 天共 <b>%d</b> 张 · 今天 <b>%d</b> 张</div>'
             % (sum(n for _d0, n in trend), trend[-1][1]))
    p.append("</div>")

    # ④ 成就：分档（每组长线，永远有下一档）＋ 单元星章收集
    _tier_total = sum(len(t[2]) for t in TIERS)
    _tier_got = sum(1 for _e, _n, needs, cur, _u in TIERS for _x in needs if cur >= _x)
    p.append('<div class="card"><div class="k">成就<em>%d / %d 档</em></div>' % (_tier_got, _tier_total))
    _closest = None
    for _e, _n, _needs, _cur, _u in TIERS:
        for _x in _needs:
            if _cur < _x:
                _gap = _x - _cur
                if _closest is None or _gap < _closest[0]:
                    _closest = (_gap, _n, _cur, _x, _u)
                break
    if _closest:
        p.append('<div class="next">最近的一档 · <b>%s</b> 还差 %d（%d/%d%s）</div>'
                 % (_closest[1], _closest[0], _closest[2], _closest[3], _closest[4]))
    for _e, _n, _needs, _cur, _u in TIERS:
        p.append('<div class="trow"><div class="tl">%s %s</div><div class="tt">' % (_e, _n))
        _next = None
        for _x in _needs:
            _done = _cur >= _x
            if not _done and _next is None:
                _next = _x
            p.append('<span class="tt1 %s">%s</span>' % ("on" if _done else "", _x if _x < 1000 else "%dk" % (_x // 1000)))
        p.append("</div></div>")
        if _next:
            _prev = max([_x for _x in _needs if _x < _next] + [0])
            _pct = 100.0 * (_cur - _prev) / float(_next - _prev) if _next > _prev else 0
            p.append('<div class="tbar"><i style="width:%.0f%%"></i></div>' % max(0.0, min(100.0, _pct)))
            p.append('<div class="tcap">%d / %d %s · 还差 %d</div>' % (_cur, _next, _u, max(0, _next - _cur)))
    p.append('<div class="sub">每档都是长线：到顶了就换下一档，不会"满了没事干"</div>')
    p.append("</div>")

    # ④.5 收集：单元星章
    p.append('<div class="card"><div class="k">收集<em>单元星章 %d / %d</em></div>' % (_units_done, sum(1 for v in units.values() if v[0])))
    p.append('<div class="stars">')
    for _b in ["八上", "八下", "九上", "九下", "七上", "七下"]:
        _us = sorted([u for (bb, u) in units if bb == _b], key=lambda x: int(re.sub(r"\D", "", x) or 0))
        if not _us:
            continue
        p.append('<div class="starr"><span class="bk2">%s</span>' % _b)
        for _u in _us:
            _tot, _lrn, _mst = units[(_b, _u)]
            _full = _tot and _lrn >= _tot
            p.append('<span class="st %s" title="%s %s">%s</span>'
                     % ("on" if _full else "", _b, _u.replace("Unit", "U"), "★" if _full else "☆"))
        p.append("</div>")
    p.append("</div>")
    p.append('<div class="sub">学完一个单元点亮一颗星；六册共 %d 颗，集齐是"通关"</div>' % len(units))
    p.append("</div>")

    # ⑤（原「接下来」路线卡：2026-11-02 09:5x 反馈说不要展示，已删；源码见 git 历史）

    # ⑤ 详情（折叠）
    p.append("<details><summary>更多数据（错词 / 默写 / 评分）</summary><div class='dcard'>")
    p.append('<div class="k">评分分布</div><div style="font-size:12px;color:var(--dim);line-height:1.9">')
    for g in (4, 3, 2, 1):
        n = gcount[g]
        p.append('%s %d 次（%.0f%%）<br>' % (gname[g], n, 100.0 * n / gtotal))
    p.append("</div>")
    if leech:
        p.append('<div class="k" style="margin-top:14px">反复忘的（Top %d）</div>' % len(leech))
        for laps, wid in leech:
            w = words[wid] if 0 <= wid < len(words) else "#%d" % wid
            p.append('<span class="tag">%s <b>%d</b></span>' % (esc(w), laps))
    if dict_rows:
        p.append('<div class="k" style="margin-top:14px">默写记录</div>')
        for line in dict_rows[-4:]:
            p.append('<div class="tag">%s</div>' % esc(line[:40]))
    p.append('<div class="k" style="margin-top:14px">今天练过（%d 个）</div>' % len(today_words))
    for w in today_words[:30]:
        p.append('<span class="tag">%s</span>' % esc(w))
    p.append("</div></details>")

    p.append('<div class="foot">%s 更新 · 只读页 · 数据：番茄流水 / 复习事件 / 词表<br>'
             '口径：熟＝稳定性≥21 天｜连续＝有学习或写日记的日子</div>'
             % now.strftime("%m-%d %H:%M"))
    p.append("</body></html>")
    return "".join(p)
