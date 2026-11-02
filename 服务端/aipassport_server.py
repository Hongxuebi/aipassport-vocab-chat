#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Passport · 服务端机侧服务（C2-a）

职责（作者定的架构：设备挂靠服务端机算力）：
  · GET  /healthz            健康检查（不鉴权）
  · GET  /time               服务器时间（设备对时用）
  · GET  /queue?day=N        今日队列：到期复习 + 20 个新词（**词表在服务端机，换书不用刷固件**）
  · POST /state              设备把本次改动的卡片状态传回来
  · POST /events             设备补传复习事件（C2-b）：**按 seq 幂等**，重复传不重复计
  · GET  /events?limit=N     最近 N 条事件 + 水位（给验收/周报看，不参与调度）
  · GET  /manifest           固件/资源清单（给 OTA 用，C3 才真正下发）

鉴权：所有 /queue /state /events /manifest 都要 `X-Dev-Token`，值与固件里烧的一致。
状态：~/aipassport/state.json（原子写：先写 .tmp 再 rename）
事件：~/aipassport/events.jsonl（只追加，一行一条，**这是原始事件流，别改/别删**）
水位：~/aipassport/events_ack.json（每台设备一个 ack，按令牌前 8 位分）
日志：~/aipassport/server.log

启动：
  AIP_TOKEN=<令牌> python3 aipassport_server.py            # 前台
  AIP_TOKEN=<令牌> nohup python3 aipassport_server.py &    # 后台

规矩：**别把令牌写进代码**；只从环境变量或 ~/aipassport/config.json 读。
"""
import json
import hashlib
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fsrs

HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, "aipassport")
STATE = os.path.join(DATA, "state.json")
WORDS_CSV = os.path.join(DATA, "words.csv")
EVENTS = os.path.join(DATA, "events.jsonl")      # 只追加的事件流（C2-b）
FW_DIR = os.path.join(DATA, "fw")                # OTA：固件包 + manifest.json（C3）
ACKS = os.path.join(DATA, "events_ack.json")     # 每台设备的幂等水位
VERSION = "0.2-c2b"

NEW_PER_DAY = 20
# 2026-11-02 10:1x 需求：当天计划做完后，再进来给「加练」——
# 一些不熟的老词 + 少量新词；答完这批才算「今天完成」。
EXTRA_WEAK_MAX = 10      # 加练：不熟老词最多几张
EXTRA_NEW_MAX = 3        # 加练：额外新词最多几个
WEAK_S = 21.0            # 稳定性低于这个算「不熟」（天）：还没到"熟＝21天"那档
CARD_CAP = 90
DESIRED = 0.95
MAX_IVL = 14


# ---------------------------------------------------------------- 词表
def load_words(path):
    """读八上词表（列：id,词,音标,词性,中文,...）→ [{id, word, ipa, pos, cn, unit}]"""
    import csv
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            out.append({
                "id": i,
                "word": r.get("词", ""),
                "ipa": r.get("音标", ""),
                "pos": r.get("词性", ""),
                "cn": r.get("中文", ""),
                "unit": r.get("单元", ""),
                "chunk": r.get("词块", "") or "",      # v0.124：词块/搭配（卡片答案面第二行）
                "chunk2": r.get("词块2", "") or "",    # A 第二层（2026-11-01）：高频词的第二条搭配；卡面不发（RX 省字节）
            })
    return out


# ---------------------------------------------------------------- 状态
def day_now(st):
    """学习日 = **服务器日历**（2026-11-01 抓到的坑）。

    设备自己数的那套（`nvs_day_load`：开机时若“上次开过机”就 +1）把**刷机/重启**当成新的一天 ——
    实测 10-31 23:35 → 11-01 11:00 的 12 小时里走了 13→14→15→16 四天，而且 10-31 那次还从 27 退到 13。
    后果：`/queue?day=17` 拿 17 去算 `over = day - last_day - ivl` ⇒ 旧的 61 张卡永远不到期，
    只拿到新词、复习一张都不出（看着还挺正常）。

    现在以服务器日历为准：`day_base = {d0: 起始日号, date: 起始日期}`，之后每过一天 +1；只增不减。
    """
    import datetime as _dt
    base = st.get("day_base") or {}
    if not base.get("date"):
        base = {"d0": int(st.get("day") or 1), "date": _dt.date.today().isoformat()}
        st["day_base"] = base
    try:
        d0 = int(base["d0"]); s0 = _dt.date.fromisoformat(str(base["date"]))
    except Exception:
        d0, s0 = 1, _dt.date.today()
        st["day_base"] = {"d0": d0, "date": s0.isoformat()}
    n = d0 + (_dt.date.today() - s0).days
    if n > int(st.get("day") or 0):
        st["day"] = n
    return int(st["day"])


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[warn] state.json 坏了，按空状态起：{e}")
    return {"day": 0, "cards": {}, "updated": 0}


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    st["updated"] = int(time.time())
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, STATE)


# ---------------------------------------------------------------- 事件（C2-b）
def load_acks():
    if os.path.exists(ACKS):
        try:
            with open(ACKS, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[warn] events_ack.json 坏了，按空水位起：{e}")
    return {}


def save_acks(a):
    os.makedirs(DATA, exist_ok=True)
    tmp = ACKS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ACKS)


def append_events(dev, rows):
    """只追加：一行一条。**幂等由调用方按水位筛过**，这里只管落盘。"""
    if not rows:
        return 0
    os.makedirs(DATA, exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return len(rows)


def recent_events(limit):
    """读最后 limit 条（文件可能很长，用环形缓冲，不整读进内存）"""
    from collections import deque
    if limit <= 0 or not os.path.exists(EVENTS):
        return []
    dq = deque(maxlen=limit)
    with open(EVENTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                dq.append(line)
    out = []
    for line in dq:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 队列
LEECH_MAX = 10          # 错词本每天最多 10 个（T6）


def load_all_events():
    rows = []
    try:
        with open(EVENTS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return rows


def leech_list(st, events, limit=LEECH_MAX):
    """错词本（T6）：lapses ≥3 或最近 5 次里 ≥3 次「忘记/模糊」⇒ 进；
    最近两次都是「认识/熟知」⇒ 出列。最多 limit 个。"""
    cards = st.get("cards", {})
    hist = {}
    for e in events:
        try:
            wid = int(e.get("id") or 0)
            g = int(e.get("g") or 0)
        except Exception:
            continue
        if wid >= 0:
            hist.setdefault(wid, []).append(g)
    out = []
    for k, c in cards.items():
        try:
            wid = int(k)
            laps = int(c.get("lapses") or 0)
        except Exception:
            continue
        h = hist.get(wid, [])
        if len(h) >= 2 and h[-1] >= 3 and h[-2] >= 3:
            continue                                  # 连续 2 次认识 → 出列
        bad = sum(1 for g in h[-5:] if g in (1, 2))
        if laps >= 3 or bad >= 3:
            out.append((laps, bad, wid))
    out.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [w for _, _, w in out[:limit]]


def first_day_map():
    """每个词**第一次**出现的日子（用来算"今天学了几个新词"）。
    🔴 2026-11-02 09:5x 问过「为什么有时进去没词、有时一堆」——查出来的根因：
       build_queue 原来用 `last_day == 今天` 当"今天的新词"，但**复习也会写 last_day**
       ⇒ 复习做得越多，"新词额度"被越快吃光，重进页面就只剩几张、甚至一张没有。
       新词额度必须按"第一次学"算。"""
    out = {}
    try:
        for e in load_all_events():
            wid = e.get("id", -1)
            day = e.get("day", 0)
            if wid is None or wid < 0:
                continue
            if wid not in out or day < out[wid]:
                out[wid] = day
    except Exception:
        pass
    return out


def build_queue(st, words, day):
    """错词本（≤10，最前）+ 到期复习（逾期最久先）+ 新词（按词表顺序补到 NEW_PER_DAY）"""
    cards = st.get("cards", {})
    fmap = first_day_map()
    due = []
    new_today = 0
    for wid in range(len(words)):
        c = cards.get(str(wid))
        if not c:
            continue
        if not c.get("first_day"):
            c["first_day"] = int(fmap.get(wid, c.get("last_day", 0)) or 0)
        if int(c.get("first_day", 0)) == day:
            new_today += 1
        ivl = min(fsrs.interval_days(c["S"], DESIRED), MAX_IVL)
        over = (day - int(c["last_day"])) - ivl
        if over > 0:
            due.append((over, wid))
    due.sort(reverse=True)

    leech = leech_list(st, load_all_events())
    q = [{"id": w, "kind": "leech"} for w in leech]
    q += [{"id": w, "kind": "review"} for _, w in due[:CARD_CAP] if w not in set(leech)]
    room_new = max(0, NEW_PER_DAY - new_today)
    for wid in range(len(words)):
        if room_new <= 0 or len(q) >= CARD_CAP:
            break
        if str(wid) in cards or wid in set(leech):
            continue
        q.append({"id": wid, "kind": "new"})
        room_new -= 1
    q = q[:CARD_CAP]
    if q:
        return q, len(due)

    # ---- 主计划做完了：给一组「加练」 ----
    # 规则：① 不熟的老词（没到期、不是错词、今天没答过、稳定性 < WEAK_S），S 最低的先来
    #       ② 少量新词（总数不超过 NEW_PER_DAY + EXTRA_NEW_MAX）
    # 自限：答过的卡 last_day 会变成今天 ⇒ 下次自动排除；答完这批才回到「今天完成」。
    leeched = set(leech)
    extra = []
    weak = []
    for wid in range(len(words)):
        c = cards.get(str(wid))
        if not c:
            continue
        if wid in leeched:
            continue
        if int(c.get("last_day", 0)) == day:          # 今天已经答过
            continue
        ivl = min(fsrs.interval_days(c["S"], DESIRED), MAX_IVL)
        if (day - int(c["last_day"])) - ivl > 0:      # 到期的已经排进主队列了
            continue
        weak.append((float(c.get("S", 0)), wid))
    weak.sort()
    # 先取"还没到熟线"的；不够 5 张再放宽到 S 最低的几张（总有东西给）
    pick = [x for x in weak if x[0] < WEAK_S]   # 严格只给"还没到熟线"的：有上限、不会无限榨
    for _sv, wid in pick[:EXTRA_WEAK_MAX]:
        extra.append({"id": wid, "kind": "review"})    # 用 review：设备不用认新类型

    extra_room = (NEW_PER_DAY + EXTRA_NEW_MAX) - new_today
    if extra_room > 0 and len(extra) < CARD_CAP:
        for wid in range(len(words)):
            if extra_room <= 0 or len(extra) >= CARD_CAP:
                break
            if str(wid) in cards or wid in leeched:
                continue
            extra.append({"id": wid, "kind": "new"})
            extra_room -= 1
    return extra, len(due)


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = "AIPassport/" + VERSION
    token = None
    words = []

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        got = self.headers.get("X-Dev-Token", "")
        if self.token and got == self.token:
            return True
        self._send(401, {"error": "bad token"})
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        st = load_state()

        if u.path == "/healthz":
            return self._send(200, {"ok": True, "version": VERSION, "time": int(time.time()),
                                    "cards": len(st.get("cards", {})), "words": len(self.words)})
        if u.path == "/time":
            return self._send(200, {"epoch": int(time.time()), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        if not self._auth():
            return
        if u.path == "/queue":
            day = day_now(st)                    # 🔴 以服务器日历为准，不信设备自己数的那个（见 day_now）
            save_state(st)
            q, ndue = build_queue(st, self.words, day)
            items = []
            for it in q:
                w = self.words[it["id"]]
                # 🔴 紧凑格式（v0.124.1，2026-11-01 11:2x）：每条压成一个字符串
                #    "id|n/r|word|ipa|cn|chunk"。
                #    为什么：cJSON 每个字段一个节点，90 张 × 6 字段 = 540 个节点 ≈ 35 KB，
                #    设备进页面时只余 35 KB ⇒ **cJSON 解析直接失败**（真机日志：队列 JSON 解析失败（10180 B），
                #    整页退成离线、复习事件全记成"离线"）。压成字符串后只有 90 个节点 ≈ 16 KB。
                items.append("|".join([str(it["id"]), "n" if it["kind"] == "new" else "r",
                                       w["word"], w["ipa"], w["cn"], w.get("chunk") or ""]))
            return self._send(200, {"day": day, "count": len(items), "due": ndue, "items": items})
        if u.path == "/events":
            limit = min(int(qs.get("limit", ["20"])[0]), 500)
            dev = (self.headers.get("X-Dev-Token", "") or "unknown")[:8]
            a = load_acks().get(dev, {})
            rows = recent_events(limit)
            by_grade = {}
            for r in rows:
                by_grade[str(r.get("g"))] = by_grade.get(str(r.get("g")), 0) + 1
            return self._send(200, {"dev": dev, "ack": int(a.get("ack", 0)),
                                    "count": int(a.get("count", 0)), "gaps": int(a.get("gaps", 0)),
                                    "by_grade": by_grade, "last": rows})
        if u.path == "/manifest":
            mf = os.path.join(FW_DIR, "manifest.json")
            fw = None
            if os.path.exists(mf):
                try:
                    with open(mf, encoding="utf-8") as f:
                        fw = json.load(f).get("fw")
                except Exception as e:
                    print(f"[warn] manifest.json 读不了：{e}")
            return self._send(200, {"version": VERSION, "fw": fw, "assets": []})

        if u.path == "/assets/pick":
            # 设备：这只本地没有，给我它的地址/大小/sha
            q = parse_qs(u.query); did = (q.get("id", [""])[0] or "").strip()
            path = os.path.join(DATA, "assets", did + ".bin")
            if not did or not os.path.exists(path):
                return self._send(404, {"error": "no such asset", "id": did})
            data = open(path, "rb").read()
            return self._send(200, {"id": did, "size": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest(),
                                    "url": "/assets/" + did + ".bin"})

        if u.path.startswith("/assets/"):
            name = os.path.basename(u.path[len("/assets/"):])
            path = os.path.join(DATA, "assets", name)
            if not os.path.exists(path):
                return self._send(404, {"error": "no such asset"})
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            print(f"[assets] 下发 {name}（{len(data)} B）", flush=True)
            return

        if u.path.startswith("/fw/"):
            name = os.path.basename(u.path[len("/fw/"):])
            path = os.path.join(FW_DIR, name)
            if not os.path.exists(path):
                return self._send(404, {"error": "no such firmware"})
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    b = f.read(65536)
                    if not b:
                        break
                    try:
                        self.wfile.write(b)
                    except Exception:
                        break
            print(f"[fw] 下发 {name}（{size} B）")
            return
        return self._send(404, {"error": "no such path"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._auth():
            return
        if u.path == "/state":
            body = self._body()
            st = load_state()
            cards = st.setdefault("cards", {})
            n = 0
            fmap = first_day_map()
            for c in body.get("cards", []):
                wid = str(int(c["id"]))
                old = cards.get(wid) or {}
                fd = old.get("first_day") or fmap.get(int(wid)) or int(c["last_day"])
                cards[wid] = {"S": float(c["S"]), "D": float(c["D"]),
                              "reps": int(c.get("reps", 0)), "lapses": int(c.get("lapses", 0)),
                              "last_day": int(c["last_day"]), "first_day": int(fd)}
                n += 1
            day_now(st)                          # 学习日只认日历；body 里的 day 不再采信（2026-11-01）
            save_state(st)
            return self._send(200, {"ok": True, "merged": n, "total": len(cards)})

        if u.path == "/events":
            body = self._body()
            evs = body.get("events") or []
            if not isinstance(evs, list):
                return self._send(400, {"error": "events 必须是数组"})
            dev = (self.headers.get("X-Dev-Token", "") or "unknown")[:8]
            acks = load_acks()
            d = acks.setdefault(dev, {"ack": 0, "count": 0, "gaps": 0})
            ack = int(d.get("ack", 0))

            # 幂等：**只收 seq > 水位的**；同一批里重复/倒序也挡掉
            accepted, dups, gaps = [], 0, 0
            for e in sorted(evs, key=lambda x: int(x.get("seq", 0) or 0)):
                try:
                    seq = int(e["seq"])
                    if seq <= ack:
                        dups += 1
                        continue
                    if seq != ack + 1:
                        gaps += 1          # 中间断了（设备被环形覆盖/换 flash），如实记账
                    row = {"dev": dev, "seq": seq, "id": int(e["id"]), "g": int(e["g"]),
                           "f": int(e.get("f", 0)), "rt": int(e.get("rt", 0)),
                           "flip": int(e.get("flip", 0)),
                           "day": day_now(load_state()), "t": int(e.get("t", 0)),   # 设备那只钟不可信，按服务器日盖戳
                           "at": int(time.time())}
                    ack = seq
                    accepted.append(row)
                except Exception as ex:
                    print(f"[warn] 丢掉一条坏事件：{ex} {e}")
            if accepted:
                append_events(dev, accepted)
            d["ack"] = ack
            d["count"] = int(d.get("count", 0)) + len(accepted)
            d["gaps"] = int(d.get("gaps", 0)) + gaps
            d["updated"] = int(time.time())
            acks[dev] = d
            save_acks(acks)
            if accepted or dups or gaps:
                print(f"[events] dev={dev} 收 {len(accepted)} 条 / 重复 {dups} / 断档 {gaps} → 水位 {ack}")
            return self._send(200, {"ok": True, "ack": ack, "accepted": len(accepted),
                                    "dups": dups, "gaps": gaps})
        return self._send(404, {"error": "no such path"})


def main():
    cfg = {}
    cfg_path = os.path.join(DATA, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    token = os.environ.get("AIP_TOKEN") or cfg.get("token")
    port = int(os.environ.get("AIP_PORT") or cfg.get("port") or 8791)

    if not token:
        sys.exit("❌ 没给令牌：设 AIP_TOKEN 环境变量，或写 ~/aipassport/config.json {\"token\":\"...\"}")
    if len(token) < 32:
        sys.exit(f"❌ 令牌太短（{len(token)}），应为 64 位 hex")

    os.makedirs(DATA, exist_ok=True)
    H.token = token
    H.words = load_words(WORDS_CSV)
    if not H.words:
        print(f"[warn] 没找到词表 {WORDS_CSV}，/queue 会返回空队列")

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"AI Passport 服务端 {VERSION} 起在 :{port}｜词表 {len(H.words)} 条｜令牌前8位 {token[:8]}…")
    print(f"状态文件：{STATE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
