#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.121 协议联调：**照固件的读法**把回包走一遍，验「AE2 中途帧」这一条。

为什么要它：设备现在连不上网（换了网络），真机那一句验不了；
但这个协议是"服务端发、固件按块首认"—— 把客户端写成和固件一样的读法，
就能在没有设备的情况下确认：① AE2 确实在**一个 recv 的块首**；② crc/长度对；
③ 帧后面紧跟的就是 PCM（不会把音频吃掉）；④ 两段之间确实是"设计停顿"。

用法：
  python3 _工具/验中途帧.py                    # 默认公网 8000，问「我最近学得怎么样」
  python3 _工具/验中途帧.py --text "今天几个番茄了"
"""
import argparse
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, os.environ.get("AIP_FW", "_参考仓库/folotoy-ai-passport"), "main/secrets.h")


def device_token():
    try:
        for line in open(SECRETS, encoding="utf-8", errors="replace"):
            if "AIP_DEV_TOKEN" in line:
                return line.split('"')[1]
    except OSError:
        pass
    return os.environ.get("AIP_DEV_TOKEN", "")


def speech_pcm(text, sr=16000):
    d = tempfile.mkdtemp()
    aiff, wav = os.path.join(d, "q.aiff"), os.path.join(d, "q.wav")
    subprocess.run(["say", "-v", "Tingting", "-o", aiff, text], check=True, capture_output=True, timeout=30)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@%d" % sr, "-c", "1", aiff, wav],
                   check=True, capture_output=True, timeout=30)
    b = open(wav, "rb").read()
    i = 12
    while i + 8 <= len(b):
        cid, sz = b[i:i + 4], struct.unpack_from("<I", b, i + 4)[0]
        if cid == b"data":
            return b[i + 8:i + 8 + sz]
        i += 8 + sz + (sz & 1)
    return b""


def crc8(data):
    c = 0
    for b in data:
        c ^= b & 0xFF
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else (c << 1) & 0xFF
    return c


def parse_frames(sock):
    """照 voice_talk.c 的读法：先循环读 8 字节头，magic 不对就把那 8 字节当 PCM。"""
    got = {"echo": None, "her": None, "pcm": b""}
    hd = b""
    while len(hd) < 8:
        d = sock.recv(8 - len(hd))
        if not d:
            return got, "对端直接关了"
        hd += d
    while True:
        if hd[:3] != b"AE1":
            got["pcm"] += hd
            break
        tag, tl, flags = hd[3], hd[4] | (hd[5] << 8), hd[6]
        body = b""
        while len(body) < tl:
            d = sock.recv(tl - len(body))
            if not d:
                return got, "帧文本没读全"
            body += d
        txt = body.decode("utf-8", "replace")
        if flags & 1:
            got["her"] = (tag, txt)
        else:
            got["echo"] = (tag, txt)
        hd = b""
        while len(hd) < 8:
            d = sock.recv(8 - len(hd))
            if not d:
                return got, "帧后对端关了"
            hd += d
    return got, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="GATEWAY_HOST")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--text", default="我最近学得怎么样")
    ap.add_argument("--no-clean", action="store_true",
                    help="别清服务端 dev_stats.json（默认清：这个工具发的是**伪造电量**，留着会让她拿假数糊弄人）")
    a = ap.parse_args()

    pcm = speech_pcm(a.text)
    s = socket.create_connection((a.host, a.port), 8)
    # 模拟 v0.121 固件：鉴权头里带电量/音量
    s.sendall(("AIPV2 %s soc=76 vol=70 mv=3980 test=1\n" % device_token()).encode())
    for i in range(0, len(pcm), 1024):
        c = pcm[i:i + 1024]
        s.sendall(struct.pack(">H", len(c)) + c)
    s.sendall(struct.pack(">H", 0))

    t0 = time.time()
    got, err = parse_frames(s)
    print("问句：%s（%.2fs 音频）" % (a.text, len(pcm) / 32000.0))
    if err:
        print("❌ %s" % err)
        return 1
    print("① 回显帧：%r" % (got["echo"],))
    print("② 她说帧（第一段）：%r" % (got["her"],))

    # ---- 进 PCM 之后，照固件那样：每个 recv 的块首认一次 'AE2'
    hits, played, first_pcm_at = [], 0, None
    while True:
        try:
            d = s.recv(1024)
        except socket.timeout:
            break
        if not d:
            break
        if first_pcm_at is None:
            first_pcm_at = time.time()
        if len(d) >= 8 and d[:3] == b"AE2":
            ok = crc8(d[:7]) == d[7]
            tl = d[4] | (d[5] << 8)
            if ok and tl <= 127:
                body = d[8:8 + tl]
                while len(body) < tl:
                    more = s.recv(tl - len(body))
                    if not more:
                        break
                    body += more
                hits.append({"tag": d[3], "text": body.decode("utf-8", "replace"),
                             "gap_ms": int((time.time() - first_pcm_at) * 1000),
                             "left_bytes": len(d) - 8 - tl})
                played += len(d) - 8 - tl
                continue
            else:
                print("   ⚠️ 块首像 AE2 但 crc/长度不对（crc_ok=%s, tl=%d）⇒ 会当 PCM 播" % (ok, tl))
        played += len(d)
    s.close()
    print("③ 中途帧：%d 个" % len(hits))
    for h in hits:
        print("   tag=%d 文本=%r ｜ 正片开始后 %d ms ｜ 同包剩音频 %d B"
              % (h["tag"], h["text"], h["gap_ms"], h["left_bytes"]))
    print("④ 实际当 PCM 播的字节：%d（%.2fs）" % (played, played / 32000.0))
    ok = len(hits) == 1 and hits[0]["left_bytes"] >= 0 and hits[0]["text"]
    print("\n%s" % ("✅ 中途帧协议通过：块首命中、crc 对、帧后音频没被吃" if ok
                    else "❌ 不符合预期（服务端 MID_FRAME 没开？还是块首没对齐）"))
    if not a.no_clean:
        # 我发的 soc=76/vol=70 是**假数**：留着的话她会拿它当"设备刚报的"糊弄作者（2026-11-01 差点犯）
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "server",
                            "rm -f ~/aipassport/dev_stats.json"], capture_output=True)
        print("🧹 已清掉服务端 dev_stats.json（本次是伪造电量%s）"
              % ("，清理失败，手动删一下" if r.returncode else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
