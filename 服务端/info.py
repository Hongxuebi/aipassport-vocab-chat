#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信息台：她把「外面的事」查成一句人话（2026-11-01 立，`99` 方案）

只做三件事：**天气 / 简单新闻 / 网络梗**。取数在服务端机（有网、24h 开机），
设备端零改动 —— 她说完就走语音口，和自查同一条路（两段式：先说「我看看」再说答案）。

数据源（都免 key，2026-11-01 在服务端机上实测）：
  天气  api.open-meteo.com（按经纬度）＋ geocoding-api.open-meteo.com（认中文城市名）
  新闻  news-at.zhihu.com（知乎日报）／IT之家 RSS ／新浪 RSS
  热搜  top.baidu.com ／ api.bilibili.com（排行）
  检索  cn.bing.com（200）／ www.sogou.com（200）

纪律（和自查一样）：
  ① 查不到就明说「查不到」，绝不编；
  ② 任何网络错误都不让对话挂住（抛异常由调用方兜）；
  ③ 缓存 20 分钟，别每次都打人家接口；
  ④ 输出压成一句话（她的人格上限 cap=60 字、气泡 3 行、播放 45 s）。

用法：
  python3 info.py 天气                  # 默认本地、明天
  python3 info.py 天气 北京              # 问哪查哪
  python3 info.py 测试                  # 路由 + 取数自检（会联网）
"""
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

AP = os.path.expanduser("~/aipassport")
CACHE_DIR = os.path.join(AP, "cache")
CACHE_TTL = 20 * 60              # 20 分钟

# 默认城市（公开版：改成你所在的城市；原来是不问就报本地那座城）
DEFAULT_CITY = ("北京", 39.9042, 116.4074)   # 公开版：默认城市，改成你用得到的地方

# 🔴 2026-11-01 真机报「查不到天气」。真身是**地名库**：open-meteo 的 GeoNames 里
#    查不到我们那座小城（同名点落在别的市），周边几个区县也各错各缺。
#    ⇒ 常用地先查这张本地表，表里没有再走网络。
PLACES = {
    "北京": ("北京", 39.9042, 116.4074),
    "上海": ("上海", 31.2304, 121.4737),
    "广州": ("广州", 23.1291, 113.2644),
    "深圳": ("深圳", 22.5431, 114.0579),
    "杭州": ("杭州", 30.2741, 120.1551),
    "南京": ("南京", 32.0617, 118.7778),
    "成都": ("成都", 30.5728, 104.0668),
    "武汉": ("武汉", 30.5928, 114.3055),
    "西安": ("西安", 34.3416, 108.9398),
    "重庆": ("重庆", 29.5630, 106.5516),
}
# 公开版说明：原来这里是一张「本地地名表」（含真实经纬度），去隐私时整表替换成通用城市。
# 要加本地地名，按同样的 ("规范名", lat, lon) 往里加即可。
_PLACE_NORM = re.compile(r"[市县区镇]$")


def _place(name):
    """本地表优先；"本地市/本地县"这类带后缀的也认。"""
    n = (name or "").strip()
    if not n:
        return None
    for cand in (n, _PLACE_NORM.sub("", n)):
        if cand in PLACES:
            return PLACES[cand]
    return None

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"

# WMO weather code → (中文, 是否要带伞)
WMO = {
    0: ("晴", False), 1: ("晴间多云", False), 2: ("多云", False), 3: ("阴", False),
    45: ("有雾", False), 48: ("有雾", False),
    51: ("小毛毛雨", True), 53: ("毛毛雨", True), 55: ("毛毛雨", True),
    56: ("冻毛毛雨", True), 57: ("冻毛毛雨", True),
    61: ("小雨", True), 63: ("中雨", True), 65: ("大雨", True),
    66: ("冻雨", True), 67: ("冻雨", True),
    71: ("小雪", False), 73: ("中雪", False), 75: ("大雪", False), 77: ("雪粒", False),
    80: ("阵雨", True), 81: ("阵雨", True), 82: ("强阵雨", True),
    85: ("阵雪", False), 86: ("阵雪", False),
    95: ("雷阵雨", True), 96: ("雷阵雨伴冰雹", True), 99: ("雷阵雨伴冰雹", True),
}


# ---------------------------------------------------------------- 小工具
def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url, timeout=12):
    return json.loads(_get(url, timeout).decode("utf-8", "replace"))


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name + ".json")


def _cache_get(name):
    try:
        d = json.load(open(_cache_path(name), encoding="utf-8"))
        if time.time() - float(d.get("_at", 0)) < CACHE_TTL:
            return d.get("v")
    except Exception:
        pass
    return None


def _cache_put(name, v):
    try:
        json.dump({"_at": time.time(), "v": v},
                  open(_cache_path(name), "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------- 地理
def geocode(name):
    """中文/英文城市名 → (规范名, lat, lon)；查不到返回 None。**先查本地表**（见 PLACES 里的坑）。"""
    name = (name or "").strip()
    if not name:
        return None
    hit_place = _place(name)
    if hit_place:
        return hit_place
    key = "geo_" + re.sub(r"[^\w\u4e00-\u9fa5]", "", name)[:16]
    hit = _cache_get(key)
    if hit:
        return tuple(hit)
    url = ("https://geocoding-api.open-meteo.com/v1/search?name=%s&count=1&language=zh&format=json"
           % urllib.parse.quote(name))
    d = _json(url)
    res = (d or {}).get("results") or []
    if not res:
        return None
    r0 = res[0]
    out = (r0.get("name") or name, float(r0["latitude"]), float(r0["longitude"]))
    _cache_put(key, list(out))
    return out


# ---------------------------------------------------------------- 天气
def weather(city_name=None, when="明天"):
    """返回 dict：{ok, city, when, code, desc, tmin, tmax, rain, advice}
    失败返回 {ok: False, why: ...}"""
    try:
        geo = geocode(city_name) if city_name else DEFAULT_CITY
        if not geo:
            return {"ok": False, "why": "没认出来是哪儿"}
        city, lat, lon = geo
        key = "wx_%.2f_%.2f" % (lat, lon)
        d = _cache_get(key)
        if d is None:
            url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
                   "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                   "&timezone=Asia%%2FShanghai&forecast_days=3" % (lat, lon))
            d = _json(url)
            _cache_put(key, d)
        daily = (d or {}).get("daily") or {}
        codes = daily.get("weather_code") or []
        if not codes:
            alt = _wttr(lat, lon, when)          # open-meteo 没给数就换一家
            if alt:
                return alt
            return {"ok": False, "why": "天气接口没给数据"}
        idx = 0 if when == "今天" else 1
        idx = min(idx, len(codes) - 1)
        code = int(codes[idx])
        desc, rain = WMO.get(code, ("说不清", False))
        tmax = daily.get("temperature_2m_max") or [None] * 3
        tmin = daily.get("temperature_2m_min") or [None] * 3
        prob = (daily.get("precipitation_probability_max") or [None] * 3)[idx]
        tmax_v, tmin_v = tmax[idx], tmin[idx]
        advice = ""
        if rain or (prob or 0) >= 50:
            advice = "记得带伞"
        elif tmax_v is not None and tmax_v >= 30:
            advice = "热，注意别中暑"
        elif tmin_v is not None and tmin_v <= 5:
            advice = "冷，穿厚点"
        return {"ok": True, "city": city, "when": when, "code": code, "desc": desc,
                "tmin": tmin_v, "tmax": tmax_v, "rain": rain, "prob": prob, "advice": advice}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "why": "网太慢，超时了"}
    except Exception as e:
        return {"ok": False, "why": "接口出错了（%s）" % type(e).__name__}


def _wttr(lat, lon, when="明天"):
    """第二数据源 wttr.in（按坐标，不用 key）。只在一家出错/限流时兜底。
    2026-11-01 实测：坐标 31.81,121.66 → nearest_area=Huilong(本地县城)，能给出当日最高/最低。"""
    try:
        d = _json("https://wttr.in/%.3f,%.3f?format=j1" % (lat, lon), 12)
        days = d.get("weather") or []
        if not days:
            return None
        day = days[0] if when == "今天" else (days[1] if len(days) > 1 else days[0])
        hi, lo = day.get("maxtempC"), day.get("mintempC")
        desc_en = ""
        try:
            desc_en = (day["hourly"][4]["weatherDesc"][0]["value"] or "").lower()
        except Exception:
            pass
        table = [("thunder", "雷阵雨", True), ("snow", "雪", False), ("sleet", "雨夹雪", True),
                 ("heavy rain", "大雨", True), ("moderate rain", "中雨", True),
                 ("light rain", "小雨", True), ("rain", "小雨", True), ("drizzle", "毛毛雨", True),
                 ("fog", "有雾", False), ("mist", "有雾", False), ("overcast", "阴", False),
                 ("cloud", "多云", False), ("sunny", "晴", False), ("clear", "晴", False)]
        desc, rain = "说不清", False
        for k, zh, r in table:
            if k in desc_en:
                desc, rain = zh, r
                break
        advice = "记得带伞" if rain else ("热，注意别中暑" if hi and float(hi) >= 30 else
                                       ("冷，穿厚点" if lo and float(lo) <= 5 else ""))
        return {"ok": True, "city": "这里", "when": when, "code": -1, "desc": desc,
                "tmin": float(lo) if lo is not None else None,
                "tmax": float(hi) if hi is not None else None,
                "rain": rain, "prob": None, "advice": advice, "src": "wttr"}
    except Exception:
        return None


def weather_say(city_name=None, when="明天"):
    """一句话（≤40 字）：明天本地小雨，18 到 24 度，记得带伞。"""
    w = weather(city_name, when)
    if not w.get("ok"):
        return "天气我这儿没查到，%s。" % w.get("why", "过会儿再问我")
    bits = ["%s%s%s" % (w["when"], w["city"], w["desc"])]
    if w["tmin"] is not None and w["tmax"] is not None:
        bits.append("%d 到 %d 度" % (round(w["tmin"]), round(w["tmax"])))
    if w["prob"] is not None and w["prob"] >= 30:
        bits.append("降水概率 %d%%" % int(w["prob"]))
    if w["advice"]:
        bits.append(w["advice"])
    return "，".join(bits) + "。"


def weather_brief(city_name=None, when="明天"):
    """给模型看的事实块（不许它编数字）。"""
    w = weather(city_name, when)
    if not w.get("ok"):
        return "【天气】查不到（%s）—— 直接说查不到。" % w.get("why", "?")
    return ("【天气】%s %s：%s，%s 到 %s 度，降水概率 %s%%，建议：%s"
            "（open-meteo，%s 查）" % (
                w["city"], w["when"], w["desc"],
                "%.0f" % w["tmin"] if w["tmin"] is not None else "?",
                "%.0f" % w["tmax"] if w["tmax"] is not None else "?",
                int(w["prob"]) if w["prob"] is not None else "?",
                w["advice"] or "没什么特别的",
                time.strftime("%H:%M")))


# ---------------------------------------------------------------- 通用：抓页面/解 HTML
import html as _html

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(s):
    return _WS.sub(" ", _html.unescape(_TAG.sub("", s or ""))).strip()


# 内容闸（口径：只留两类硬拦）
#   ① 低俗/血腥/未成年相关 ② 明显广告。国内时政不在拦的范围（明确要国内新闻）。
_BLACK = re.compile(r"色情|裸|性侵|强奸|卖淫|嫖|吸毒|贩毒|自杀|跳楼|碎尸|惨死|尸体|血腥|虐童|拐卖|恐袭|枪击|空难|坠机|遇难|死伤|凶杀|血案|"
                    r"点击购买|限时优惠|扫码|广告|下载APP|领取红包")
_ADISH = re.compile(r"^(广告|推广|热卖|优惠)")


_DATE_HEAD = re.compile(r"^(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}天前|\d{1,2}小时前|\d{2}:\d{2}|\d{4}-\d{2}-\d{2})\s*[-－—]\s*")


def _clean(d):
    """摘要去掉开头的日期前缀（360 常写「2019年4月4日 - 2019年4月4日 今天…」，要连剥两层）、掉省略号。"""
    t = (d or "").strip()
    for _ in range(4):
        t2 = _DATE_HEAD.sub("", t).strip()
        t2 = re.sub(r"^(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}天前|\d{1,2}小时前|\d{2}:\d{2})\s*", "", t2)
        t2 = re.sub(r"^[\s\-－—|·]+", "", t2)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"\.{2,}|…+", "…", t)
    return t.strip()


def _ok(title, maxlen=30):
    t = (title or "").strip()
    if not t or len(t) > maxlen:
        return False
    return not (_BLACK.search(t) or _ADISH.search(t))


def _rss(url, n=8, timeout=10):
    """通用 RSS：返回 [(title, desc, pub)]"""
    try:
        h = _get(url, timeout).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for it in re.findall(r"<item[^>]*>(.*?)</item>", h, re.S)[:n]:
        m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        d = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", it, re.S)
        p = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        if m:
            out.append((_text(m.group(1)), _text(d.group(1)) if d else "", (p.group(1).strip() if p else "")))
    return out


# ---------------------------------------------------------------- 新闻（P1）
# 🔴 时间纪律（点名过）：**只报今天/昨天的**。RSS 一律看 pubDate，超 36 小时直接丢；
#    一条新的都没有就明说"今天没拉到"，绝不拿旧闻充今天。
# 源（2026-11-01 实测新鲜度）：IT之家 60 条/24h 内 60 条｜中新网国内 30/30｜中新网国际 30/30；
#    新浪 RSS 停在 2018、人民网停在 2025-06 ⇒ 一律不用。
NEWS_SRC = {
    "科技": [("IT之家", "https://www.ithome.com/rss/", "rss")],
    "国内": [("中新网", "https://www.chinanews.com.cn/rss/scroll-news.xml", "rss")],
    "国际": [("中新网", "https://www.chinanews.com.cn/rss/world.xml", "rss")],
    "好玩": [("知乎日报", "https://news-at.zhihu.com/api/4/news/latest", "zhihu"),
             ("中新网国际", "https://www.chinanews.com.cn/rss/world.xml", "rss")],
    "好玩国际": [("中新网国际", "https://www.chinanews.com.cn/rss/world.xml", "rss"),
              ("知乎日报", "https://news-at.zhihu.com/api/4/news/latest", "zhihu")],
}
NEWS_MAX_AGE_H = 36


def _zhihu_daily(n=8):
    try:
        d = _json("https://news-at.zhihu.com/api/4/news/latest")
        return [(s.get("title", ""), s.get("hint", ""), time.time()) for s in (d.get("stories") or [])[:n]]
    except Exception:
        return []


def _rss(url, n=10, max_age_h=NEWS_MAX_AGE_H):
    """RSS：返回 [(title, desc, ts)]；没有 pubDate 的按"今天"算（只有知乎日报那种 latest 接口）。"""
    try:
        h = _get(url, 10).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for it in re.findall(r"<item[^>]*>(.*?)</item>", h, re.S)[:n]:
        m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        d = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", it, re.S)
        pd = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        if not m:
            continue
        ts = None
        if pd:
            try:
                import email.utils
                ts = email.utils.parsedate_to_datetime(pd.group(1).strip()).timestamp()
            except Exception:
                ts = None
        if ts is not None and (time.time() - ts) > max_age_h * 3600:
            continue                      # 🔴 旧的直接丢，不给她念
        out.append((_text(m.group(1)), _text(d.group(1)) if d else "", ts))
    return out


def _when(ts):
    if not ts:
        return "今天"
    d = time.localtime(ts)
    today = time.strftime("%Y-%m-%d")
    if time.strftime("%Y-%m-%d", d) == today:
        return "今天 %02d:%02d" % (d.tm_hour, d.tm_min)
    return "%d月%d日" % (d.tm_mon, d.tm_mday)


# 「好玩的」那档要轻：硬的（战争/核/军/袭击/灾害）整条跳过
_HEAVY = re.compile(r"核|导弹|武器|军演|演习|军队|军方|战争|冲突|袭击|恐|制裁|枪击|空难|坠机|遇难|死伤|事故|踩踏|地震|洪水|台风|疫情|"
                    r"弹劾|大选|政变|抗议|罢工|罪|判刑|逮捕|起诉")


def _light(title):
    return not _HEAVY.search(title or "")


def news_items(kind="科技", n=5):
    """返回 [(标题, 摘要, 来源, 时间短语)]；按时间倒序，只要 NEW_MAX_AGE_H 内的。"""
    key = "news2_" + kind
    hit = _cache_get(key)
    if hit:
        return [tuple(x) for x in hit]
    light = kind in ("好玩", "好玩国际")
    if light:
        n = max(n, 12)
    raw = []
    for name, url, how in NEWS_SRC.get(kind, NEWS_SRC["科技"]):
        if how == "zhihu":
            for t, d, ts in _zhihu_daily(n):
                raw.append((t, d, name, ts))
        else:
            for t, d, ts in _rss(url, n):
                raw.append((t, d, name, ts))
        # 「好玩的」会过掉一大半硬新闻 ⇒ 不能早退，得把备用源也拉一遍
        if len(raw) >= n and not light:
            break
    items = []
    for t, d, src, ts in raw:
        if not _ok(t, 40):
            continue
        if light and not _light(t):
            continue                      # 「好玩的」别给战争/灾害
        items.append((t, d, src, _when(ts)))
        if len(items) >= n:
            break
    if items:
        _cache_put(key, items)
    return items


def _news_lines(items, kind="科技"):
    """把候选渲染成事实块（同时**记进会话状态**，"再来一条"才接得上）。"""
    _remember("news", kind, items)
    if not items:
        return "【新闻】%s 这块今天没拉到（就说「今天没拉到」）。" % kind
    lines = ["【新闻】%s 今天能念的（按时间新的在前，现在 %s）：" % (kind, time.strftime("%H:%M"))]
    for t, _d, src, when in items:
        lines.append("- %s ｜ %s（%s）" % (when, t, src))
    if kind in ("国际", "好玩国际"):
        lines.append("问的是**国际**的：优先念标着「中新网国际」那条；都太硬就挑最轻的一条。")
    lines.append("念的时候这样起头：「刚看到一条：……」；一条讲完就收，40 字以内。")
    lines.append("用户说「随便说一条」或「再换一条」，就接着念下一条（「还有一条，……」）。")
    lines.append("收尾用一句正常评论收住，比如「听着还挺有意思」。")
    return "\n".join(lines)


def news_brief(kind="科技", n=3):
    return _news_lines(news_items(kind, n), kind)


def news_say(kind="科技"):
    items = news_items(kind, 3)
    if not items:
        return "%s的新闻我今天没拉到。" % kind
    t, _d, _src, when = items[0]
    t = t[:36]
    return "刚看到一条：" + (t if t.endswith(("。", "？", "！", "?")) else t + "。")


# ---------------------------------------------------------------- 热梗 / 热搜（P2）
def hot_items(n=6):
    """热梗：优先**检索摘要**（网上文章里直接带解释），抓不到再退 B站排行／百度热搜。"""
    hit = _cache_get("hot")
    if hit:
        return [tuple(x) for x in hit]
    out = []
    try:
        for t, d, _src in search("最近流行的网络梗", 3):
            out.append((t, "检索"))
            if d and len(out) < n:
                out.append((_clean(d)[:60], "摘要"))
    except Exception:
        out = []
    try:
        d = _json("https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all")
        for v in ((d.get("data") or {}).get("list") or [])[:20]:
            t = _text(v.get("title") or "")
            if _ok(t, 28):
                out.append((t, "B站热门"))
            if len(out) >= n:
                break
    except Exception:
        pass
    if len(out) < 3:
        try:
            d = _json("https://top.baidu.com/api/board?platform=wise&tab=realtime")
            for c in ((d.get("data") or {}).get("cards") or [])[:1]:
                for grp in (c.get("content") or []):
                    for it in (grp.get("content") or []):
                        w = _text(it.get("word") or it.get("query") or "")
                        if _ok(w, 24):
                            out.append((w, "百度热搜"))
                        if len(out) >= n:
                            break
        except Exception:
            pass
    out = out[:n]
    if out:
        _cache_put("hot", out)
    return out


def hot_brief(n=3):
    items = hot_items(n)
    _remember("hot", None, items)
    if not items:
        return "【热梗】没拉到（老实说查不到）。"
    lines = ["【热梗】最近大家在看/在说的："]
    for t, src in items:
        lines.append("- %s（%s）" % (t, src))
    lines.append("挑**一条**说，35 字以内；不懂意思就直说「我也没太懂」。")
    lines.append("说「再换一条」，就接着给下一条。")
    return "\n".join(lines)


def hot_say():
    items = hot_items(4)
    if not items:
        return "最近流行什么我也没查到。"
    # 优先挑"在列梗名"的那条（顿号/括号多 ⇒ 真在举例），比标题党强
    best = max(items, key=lambda x: (x[0].count("、") + x[0].count("(") + x[0].count("（")))
    txt = best[0].strip()
    return txt[:44] + ("" if txt.endswith("。") else "。")


# ---------------------------------------------------------------- 检索（360 → 搜狗）
# 🔴 必应不要用：2026-11-01 实测，中文问句命中「词典/歌名」那种无关结果（"背单词的方法"→讲"背"字），
#    RSS 输出也基本空。360（so.com）能返回相关标题＋摘要，搜狗兜底。
def _so(q, n=3):
    """360 搜索。返回 [(title, snippet, 来源)]"""
    url = "https://www.so.com/s?q=%s" % urllib.parse.quote(q)
    h = _get(url, 12).decode("utf-8", "replace")
    out = []
    for b in re.findall(r'<li class="res-list.*?</li>', h, re.S):
        m = re.search(r"<h3[^>]*>(.*?)</h3>", b, re.S)
        if not m:
            continue
        t = _text(m.group(1))
        body = _text(b).replace(t, "", 1).strip()
        if t and len(t) <= 60 and _ok(t, 60):
            out.append((t, _clean(body)[:120], "so.com"))
        if len(out) >= n:
            break
    return out


def _sogou(q, n=3):
    url = "https://www.sogou.com/web?query=%s" % urllib.parse.quote(q)
    h = _get(url, 12).decode("utf-8", "replace")
    out = []
    for b in re.findall(r"<h3[^>]*>.*?</h3>", h, re.S)[:12]:
        t = _text(b)
        if t and 2 <= len(t) <= 60 and _ok(t, 60):
            out.append((t, "", "sogou.com"))
        if len(out) >= n:
            break
    return out


def search(q, n=3):
    """快的那条路（定的）：必应 → 搜狗；0 成本、不调模型。"""
    q = (q or "").strip().rstrip("？?。.!")
    if len(q) < 2:
        return []
    key = "se_" + re.sub(r"[^\w\u4e00-\u9fa5]", "", q)[:24]
    hit = _cache_get(key)
    if hit is not None:
        return [tuple(x) for x in hit]
    out = []
    for fn in (_so, _sogou):
        try:
            out = fn(q, n)
        except Exception:
            out = []
        if out:
            break
    _cache_put(key, out)
    return out


def search_brief(q, n=3):
    items = search(q, n)
    _remember("search", None, items)
    if not items:
        return "【检索】「%s」没搜到（直接说没搜到）。" % q
    lines = ["【检索】「%s」搜到的：" % q]
    for t, d, src in items:
        lines.append("- %s%s（%s）" % (t, ("：" + d[:60]) if d else "", src))
    lines.append("用**一两句话**回答用户的问题（≤40 字），别整段念；不确定就说不确定。")
    lines.append("用户要是说「随便说一条/再换一条」，就再从上面挑一条给使用者，**别问、别演拒绝**。")
    return "\n".join(lines)


def search_say(q):
    items = search(q, 2)
    if not items:
        return "我搜了，没搜到「%s」。" % q[:12]
    t, d, _src = items[0]
    return (d or t)[:40] + ("。" if not (d or t).endswith("。") else "")


# ---------------------------------------------------------------- 会话状态（2026-11-01 14:1x）
# 🔴 14:11–14:12 真机六轮抓到的最大问题：搜到/念过的候选**没存**，于是
#    「随便说一条」「再换一条」都掉回闲聊，她只能用人设编"算了，不说了"去拒绝（还重复了两遍）。
#    现在把上一批候选记在内存里（10 分钟过期），"再来一条"就真能接着换。
_LAST = {"at": 0.0, "kind": None, "topic": None, "items": [], "shown": []}
_LAST_TTL = 600


def _remember(kind, topic, items):
    """记下这一批候选。items 统一成 (正文, 补充, 来源)。"""
    norm = []
    for it in items:
        if len(it) >= 4:          # news: (t, d, src, when)
            norm.append((it[0], it[3], it[2]))
        elif len(it) == 3:        # search: (t, d, src)
            norm.append((it[0], it[1], it[2]))
        elif len(it) == 2:        # hot: (t, src)
            norm.append((it[0], "", it[1]))
    carry = []
    if _LAST.get("kind") == kind and _LAST.get("topic") == topic:
        texts = {it[0] for it in norm}
        carry = [x for x in (_LAST.get("shown") or []) if x in texts]   # 同一批接着念，别倒回第一条
    if norm and norm[0][0] not in carry:
        carry.append(norm[0][0])          # 事实块里排第一的那条她大概率已经念了 ⇒ 先记上，"再换一条"才不会重复
    _LAST.update(at=time.time(), kind=kind, topic=topic, items=norm, shown=carry)


def _last_alive():
    return bool(_LAST["kind"]) and (time.time() - _LAST["at"]) < _LAST_TTL


def _lcs(a, b):
    """最长公共子串长度（只用来判断"她念的到底是哪一条"）。"""
    a, b = (a or "")[:80], (b or "")[:120]
    best = 0
    for i in range(len(a)):
        for j in range(i + best + 1, len(a) + 1):
            if a[i:j] in b:
                best = j - i
            else:
                break
    return best


def note_spoken(reply):
    """她真正念出口之后调一次：把命中的那条记成"已念过"。
    🔴 2026-11-01 14:17 的坑：只按"事实块第一条"猜她会念哪条 ⇒ 她说第二条时，"再换一条"又给回同一条。
       LLM 挑哪条说不准，只能**回头看她说的话**去对。"""
    if not _last_alive() or not reply:
        return None
    for it in _LAST["items"]:
        if it[0] in _LAST["shown"]:
            continue
        if _lcs(it[0], reply) >= 6:
            _LAST["shown"].append(it[0])
            _LAST["at"] = time.time()
            return it[0]
    return None


def _next_one():
    """从上次候选里取下一条没念过的；全念完了就重拉一批同类。"""
    if not _last_alive():
        return None
    for it in _LAST["items"]:
        if it[0] not in _LAST["shown"]:
            _LAST["shown"].append(it[0])
            _LAST["at"] = time.time()
            return it
    kind, topic = _LAST["kind"], _LAST["topic"]
    try:
        if kind == "news":
            fresh = news_items(topic or "科技", 12)
            _remember("news", topic, fresh)
        elif kind == "hot":
            _remember("hot", None, hot_items(12))
        elif kind == "search":
            return None                      # 搜索不自动续，避免瞎编
        else:
            return None
    except Exception:
        return None
    return _next_one()


# ---------------------------------------------------------------- 路由（问句 → 查什么）
_CITY_STOP = re.compile(r"(天气|气温|温度|下雨|下雪|冷不冷|热不热|带伞|穿什么|怎么样|如何|明天|今天|后天|"
                        r"预报|会|要|需不需要|用不用|出门|外面|那边|这儿|这里|市区|的)+\s*$")
_NOT_CITY = {"我", "你", "他", "她", "它", "咱们", "我们", "你们", "他们", "这儿", "那儿", "这里", "那里",
             "外面", "家里", "明天", "今天", "后天", "现在", "最近", "什么", "怎么", "多少", "一下"}


def _find_city(text):
    """从问句里抠城市名：只认「X天气 / X下雨 / 在X」这类明确形状，抠不出来就返回 None（默认本地）。"""
    t = (text or "").strip()
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,8}?)(?:市|县|区)?(?:的)?(?:天气|下雨|下雪|气温|温度|冷不冷|热不热)", t):
        cand = _CITY_STOP.sub("", m.group(1))
        cand = re.sub(r"^(明天|今天|后天|在|去|到|问|查|看看|看一下|帮我|给我)", "", cand)
        if len(cand) >= 2 and cand not in _NOT_CITY:
            return cand
    m = re.search(r"在([\u4e00-\u9fa5]{2,8}?)(?:的)?(?:天气|下雨|怎么样)", t)
    if m:
        cand = _CITY_STOP.sub("", m.group(1))
        if len(cand) >= 2 and cand not in _NOT_CITY:
            return cand
    return None


_W_KEYS = [r"天气", r"气温", r"温度", r"下雨", r"下雪", r"冷不冷", r"热不热", r"带伞", r"穿(多少|什么)",
           r"天气预报", r"会不会(下雨|下雪)", r"外面(冷|热|下雨)"]
_W_RES = [re.compile(p) for p in _W_KEYS]
_NO_WEATHER = [re.compile(p) for p in (r"天气(好|不错|真好)",)]   # 闲聊别抢


_S_RES = re.compile(r"(搜一下|搜一搜|搜索|帮我搜|搜个|查一下|查查|检索|百度一下)(.*)")
_H_RES = re.compile(r"梗|流行语|热搜|大家都在说|最近(都在|流行)(看|说)")
_N_RES = re.compile(r"新闻|大事|发生了什么|今天发生|头条|国内(的)?事|(好玩|有趣|搞笑|奇葩|奇闻|新鲜)的?(事|新闻|消息)")
_SKIP_SEARCH = re.compile(r"天气|气温|下雨|下雪")          # 天气优先，别被"查一下天气"抢走


_NEXT_RES = re.compile(r"(换|再来|再给|再念|再说|再讲|还有)(一?条|一?个|点别的)|下一条|下一个|还有吗|换一个|"
                        r"(随便|任意)(说|来|讲|念|给)一?条")


def route(text):
    """认「天气 / 新闻 / 热梗 / 检索 / 再来一条」。返回 dict 或 None。

    🔴 顺序（2026-11-01 14:1x 改的，真机反馈）：**内容意图 > "搜"这个动词**。
       用户说"帮我搜搜国际上有什么好玩的新闻"——那个"搜搜"是口头语，真意图是**要新闻**；
       旧顺序里 search 抢在前面，于是只回一句"净是标题党"，等于没给内容。
    """
    t = (text or "").strip()
    if len(t) < 2:
        return None
    # 1) 天气最先（"查一下天气"也要走天气）
    if any(p.search(t) for p in _W_RES) and not any(p.search(t) for p in _NO_WEATHER):
        when = "今天" if "今天" in t else "明天"
        return {"kind": "weather", "city": _find_city(t), "when": when}
    # 2) "再来一条 / 换一条 / 随便说一条"：只有上一批还在才算
    if _NEXT_RES.search(t) and _last_alive() and not _H_RES.search(t):
        return {"kind": "next"}        # 带不带"新闻"二字都算：要的是"再来一条"
    # 3) 热梗
    if _H_RES.search(t) and not _N_RES.search(t):
        return {"kind": "hot"}
    # 4) 新闻（国际／国内／好玩／科技 分开走不同源；topic 可跨轮继承）
    if _N_RES.search(t):
        _intl = re.search(r"国际|国外|外国|海外|世界|全球", t)
        _fun = re.search(r"好玩|有趣|搞笑|逗|奇闻|新鲜事", t)
        if _intl and _fun:
            topic = "好玩国际"
        elif _fun:
            topic = "好玩"
        elif _intl:
            topic = "国际"
        elif re.search(r"国内|中国|国家大事", t):
            topic = "国内"
        elif _last_alive() and _LAST["kind"] == "news" and _LAST["topic"]:
            topic = _LAST["topic"]        # 只说"新闻"，沿用上一轮的国际/好玩
        else:
            topic = "科技"
        return {"kind": "news", "topic": topic}
    # 5) 最后才认"搜一下 X"
    m = _S_RES.search(t)
    if m and not _SKIP_SEARCH.search(t):
        q = m.group(2).strip(" 　，。？?！!")
        if len(q) >= 2:
            return {"kind": "search", "q": q[:40]}
    return None


def fact_block(text):
    """selfknow 用：一句问句 → 事实块（查不到也只会说查不到）。"""
    r = route(text)
    if not r:
        return ""
    if r["kind"] == "weather":
        return weather_brief(r.get("city"), r.get("when", "明天"))
    if r["kind"] == "news":
        return news_brief(r.get("topic", "科技"))
    if r["kind"] == "hot":
        return hot_brief()
    if r["kind"] == "search":
        return search_brief(r["q"])
    if r["kind"] == "next":
        it = _next_one()
        if not it:
            return "【再来一条】上一批已经念完/没了 —— 直说「这就没了」，别编、别演拒绝。"
        lines = ["【再来一条】直接念这条：%s%s（%s）" % (it[0], (" ｜ " + it[1]) if it[1] else "", it[2]),
                 "一句话念完，40 字以内。"]
        return "\n".join(lines)
    return ""


def short_answer(text):
    """selfknow 用：模型不可用时的兜底一句。"""
    r = route(text)
    if not r:
        return ""
    if r["kind"] == "weather":
        return weather_say(r.get("city"), r.get("when", "明天"))
    if r["kind"] == "news":
        return news_say(r.get("topic", "科技"))
    if r["kind"] == "hot":
        return hot_say()
    if r["kind"] == "search":
        return search_say(r["q"])
    if r["kind"] == "next":
        it = _next_one()
        if not it:
            return "这就没了。"
        t = it[0][:40]
        return t if t.endswith(("。", "？", "！", "?")) else t + "。"
    return ""


# ---------------------------------------------------------------- 自检
def _main(argv):
    if not argv:
        argv = ["天气"]
    if argv[0] in ("测试", "test"):
        cases = ["明天天气怎么样", "北京天气呢", "今天下雨吗", "我学了几个单词", "本地冷不冷",
                 "搜一下 背单词的方法", "最近流行什么梗", "今天有什么新闻", "国内新闻"]
        for c in cases:
            print("%-16s → %s" % (c, route(c)))
        print("-" * 50)
        print(weather_say(None, "明天"))
        print(weather_brief(None, "明天"))
        print("—— 新闻 ——")
        print(news_say("科技"))
        print("—— 热梗 ——")
        print(hot_say())
        print("—— 检索 ——")
        print(search_say("背单词的方法"))
        g = geocode("北京")
        print("北京 →", g)
        if g:
            print(weather_say("北京", "明天"))
        return 0
    if argv[0].startswith("天气"):
        city = argv[1] if len(argv) > 1 else None
        print(weather_say(city, "明天"))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
