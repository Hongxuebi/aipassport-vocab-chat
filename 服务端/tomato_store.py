#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tomato_store.py —— 番茄流水（PRD §5.4，唯一契约）

设备每完成一个番茄，经 8802 语音口网关 `PST1 /tomato` 送来一条 JSON，
本模块**只追加**到 `~/aipassport/tomato.jsonl`（一行一条，七个字段）：

    {"dev":"a3b71565","ts":1793440000,"date":"2026-10-31","dur":25,"mode":1,"round":3,"at":1793440005}

字段口径（PRD §5.4，**别改名、别加字段**）：

    dev    设备 ID（token 前 8 位）      设备给；没给就用本机 TOKEN 前 8 位
    ts     完成时刻（UTC 秒；没对时=0）   设备给
    date   本地日期 YYYY-MM-DD            ts=0 ⇒ null（契约明文）；否则按 ts 换算成本地日期
    dur    本次分钟（15/25/45）          设备给
    mode   档位 0/1/2                    设备给
    round  当天第几个（设备计数）         设备给
    at     服务端机收到的时间戳              **本模块填**（设备无权决定）

三条硬规矩：
  ① **只追加**：不改历史行、不重排；看板只读它
  ② **失败要说话**：落盘失败回 ok=0（设备就不推进它的水位，下次重传），绝不静默丢
  ③ 写盘用行锁：语音口是多线程（每个设备连接一个线程）
"""
import json
import os
import threading
import time

PATH = os.path.expanduser(os.environ.get("AIP_TOMATO_PATH", "~/aipassport/tomato.jsonl"))
_LOCK = threading.Lock()

DURS = (15, 25, 45)


def _local_date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(int(ts)))


def _reply(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _count_lines():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def append(body, dev_hint=""):
    """设备送来一条番茄；body 是 JSON 正文（bytes）。

    回 b'{"ok":1,"n":<总行数>}' —— 设备只认 ok。
    """
    if not body:
        return _reply({"ok": 0, "err": "empty"})

    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:
        return _reply({"ok": 0, "err": "bad json: %s" % (e,)})
    if not isinstance(j, dict):
        return _reply({"ok": 0, "err": "not an object"})

    # ── 七个字段，逐个定性（不合契约就退回，让它下次重传）──
    try:
        ts = int(j.get("ts") or 0)
    except Exception:
        ts = 0
    try:
        dur = int(j.get("dur") or 0)
    except Exception:
        dur = 0
    if dur not in DURS:
        return _reply({"ok": 0, "err": "dur must be 15/25/45"})

    try:
        mode = int(j.get("mode") if j.get("mode") is not None else -1)
    except Exception:
        mode = -1
    if mode not in (0, 1, 2):
        return _reply({"ok": 0, "err": "mode must be 0/1/2"})
    if DURS[mode] != dur:
        return _reply({"ok": 0, "err": "dur/mode mismatch"})

    try:
        rnd = int(j.get("round") or 0)
    except Exception:
        rnd = 0
    rnd = max(0, min(999, rnd))

    dev = str(j.get("dev") or dev_hint or "")[:16]
    at = int(time.time())
    date = _local_date(ts) if ts > 0 else None     # 契约：ts=0 ⇒ null，不拿 at 冒充

    row = {"dev": dev, "ts": ts, "date": date, "dur": dur, "mode": mode, "round": rnd, "at": at}

    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _LOCK:
        # 幂等（2026-10-31 拍板）：补传时 ACK 丢了设备会重发同一条 —— 同 dev+ts 已落过就只回 ok，
        # 不再追加。ts=0（没对时）没法判定，照旧追加。
        if ts > 0:
            try:
                with open(PATH, "r", encoding="utf-8") as f:
                    for old in f:
                        old = old.strip()
                        if not old:
                            continue
                        try:
                            o = json.loads(old)
                        except Exception:
                            continue
                        if o.get("dev") == dev and int(o.get("ts") or 0) == ts:
                            return _reply({"ok": 1, "dup": 1, "n": _count_lines()})
            except FileNotFoundError:
                pass
        try:
            d = os.path.dirname(PATH)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            return _reply({"ok": 0, "err": "write failed: %s" % (e,)})
        n = _count_lines()

    return _reply({"ok": 1, "n": n, "date": date, "dur": dur})


# ── 自测（离线，不碰网络）：python3 tomato_store.py selftest ──────────────
def _selftest():
    import tempfile
    global PATH
    old = PATH
    fd, tmp = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    os.unlink(tmp)
    PATH = tmp
    ok = 0
    bad = 0
    cases = [
        (b'{"ts":1793440000,"dur":25,"mode":1,"round":3}', 1),
        (b'{"ts":1793440000,"dur":15,"mode":1,"round":1}', 0),   # dur/mode 不一致
        (b'{"ts":0,"dur":45,"mode":2,"round":9}', 1),
        (b'not json', 0),
        (b'{"ts":1793440000,"dur":30,"mode":1,"round":1}', 0),   # 档位非法
    ]
    for body, want in cases:
        r = json.loads(append(body, "a3b71565").decode())
        got = r.get("ok")
        if got == want:
            ok += 1
        else:
            bad += 1
            print("FAIL", body[:40], "want", want, "got", r)
    # 幂等：同一条重发（补传丢 ACK 的场景）不许再落一行
    dup = json.loads(append(b'{"ts":1793440000,"dur":25,"mode":1,"round":3}', "a3b71565").decode())
    if dup.get("ok") == 1 and dup.get("dup") == 1:
        ok += 1
    else:
        bad += 1
        print("FAIL 幂等：重发应回 ok=1/dup=1，实际", dup)
    # 落盘格式：一行一条、七个键、date 与 ts=0 的 null
    rows = [json.loads(x) for x in open(tmp, encoding="utf-8") if x.strip()]
    keys = set()
    for r in rows:
        keys |= set(r.keys())
    assert keys == {"dev", "ts", "date", "dur", "mode", "round", "at"}, keys
    assert rows[0]["date"] is not None and rows[0]["dev"] == "a3b71565"
    assert rows[1]["date"] is None and rows[1]["ts"] == 0
    assert all(r["at"] > 0 for r in rows)
    os.unlink(tmp)
    PATH = old
    print("selftest %d/%d 通过，落盘 %d 行" % (ok, ok + bad, len(rows)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(_selftest())
    print(__doc__)
