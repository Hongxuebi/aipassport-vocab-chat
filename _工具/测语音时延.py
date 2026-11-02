#!/usr/bin/env python3
"""测语音时延：不用设备，模拟一次"按住 ● 说完"（AIPV2 帧 + 0 长度帧），量**收完 → 首字节**。
判据（2026-10-28 起）：正常问句 **< 1500 ms** 算达标；> 2500 ms 就是退步。

用法：
  python3 _工具/测语音时延.py                      # 服务端机 LAN_HOST:8802，默认问句
  python3 _工具/测语音时延.py --host GATEWAY_HOST --port 8000
  python3 _工具/测语音时延.py --text "薯条的英文叫什么？" --mode noend   # 故意不发结束帧
  python3 _工具/测语音时延.py --times 3            # 连测三次

判读（同时看服务端机日志 `tail -f ~/aipassport/系统服务.out.log`）：
  LLM 首句 0.xxs → '…'  →  首块 xxxxms（收完→可播：ASR+LLM+首句 TTS）
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


def speech_pcm(text, sr=16000, voice="Tingting"):
    """本机 say 造一句人话 → 16k/16bit/mono 裸 PCM"""
    d = tempfile.mkdtemp()
    aiff, wav = os.path.join(d, "q.aiff"), os.path.join(d, "q.wav")
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True,
                   capture_output=True, timeout=30)
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


def one_round(host, port, pcm, token, mode="end", sr=16000, live=False):
    t0 = time.time()
    s = socket.create_connection((host, port), 8)
    # test=1：这一轮不进记忆/日记（K47）；--live 才写成 test=0（验多轮上下文时用，会真的进记忆）
    s.sendall(("AIPV2 %s test=%d\n" % (token, 0 if live else 1)).encode())
    sent = 0
    for i in range(0, len(pcm), 1024):
        chunk = pcm[i:i + 1024]
        s.sendall(bytes([len(chunk) >> 8, len(chunk) & 0xFF]) + chunk)
        sent += len(chunk)
        slack = sent / 2.0 / sr - (time.time() - t0)      # 按设备节奏实时发
        if slack > 0:
            time.sleep(slack)
    if mode == "end":
        s.sendall(b"\x00\x00")
    t_rel = time.time()

    s.settimeout(30)
    first, total = None, 0
    buf = b""                      # 前若干字节留下解元数据帧（AE1）
    while True:
        d = s.recv(4096)
        if not d:
            break
        if first is None:
            first = time.time() - t_rel
        total += len(d)
        if len(buf) < 512:
            buf += d[:512 - len(buf)]
    return (first or -1) * 1000, total, (time.time() - t_rel) * 1000, parse_frames(buf)


def parse_frames(buf):
    """把下行开头连续的 'AE1' 元数据帧解出来：[(flags, tag, text)]。
    和固件 voice_talk.c 的读法一致 —— magic 不匹配就停（那段是 PCM）。"""
    out = []
    i = 0
    while i + 8 <= len(buf):
        if buf[i:i + 3] != b"AE1":
            break
        tag = buf[i + 3]
        tl = buf[i + 4] | (buf[i + 5] << 8)
        flags = buf[i + 6]
        body = buf[i + 8:i + 8 + tl]
        out.append((flags, tag, body.decode("utf-8", "replace")))
        i += 8 + tl
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="LAN_HOST")
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--text", default="我今天想吃一个包子，用英语怎么说？")
    ap.add_argument("--mode", default="end", choices=["end", "noend"])
    ap.add_argument("--times", type=int, default=1)
    ap.add_argument("--voice", default="Tingting",
                    help="合成问句用的嗓音（验英文用 Samantha，别拿中文嗓音念英文）")
    ap.add_argument("--live", action="store_true",
                    help="这一轮真的进记忆/上下文（test=0）；验多轮连贯时必须用，验完记得回滚 mem/")
    ap.add_argument("--dump-meta", action="store_true",
                    help="把下行开头的元数据帧（ASR / 她说的）解出来打印")
    a = ap.parse_args()

    tok = device_token()
    if not tok:
        print("❌ 找不到设备令牌（main/secrets.h 的 AIP_DEV_TOKEN）")
        return 2
    pcm = speech_pcm(a.text, voice=a.voice)
    print("问句「%s」→ %.2fs / %d B ｜ %s:%d ｜ %s" %
          (a.text, len(pcm) / 2.0 / 16000, len(pcm), a.host, a.port, a.mode))
    worst = 0
    for k in range(a.times):
        ttfb, total, done, frames = one_round(a.host, a.port, pcm, tok, a.mode, live=a.live)
        if a.dump_meta:
            if not frames:
                print("  ⚠️ 没解析到 AE1 帧（老服务端？）")
            for flags, tag, text in frames:
                who = "她说" if (flags & 1) else "我说"
                print("  帧[%s] flags=%d 标签=%d「%s」" % (who, flags, tag, text))
        worst = max(worst, ttfb)
        print("  第%d次：首字节 %6.0f ms ｜ 回包 %6d B（%.2fs）｜ 收完 %5.0f ms" %
              (k + 1, ttfb, total, total / 2.0 / 16000, done))
        time.sleep(0.8)
    verdict = "✅ 达标" if worst < 1500 else ("🟡 偏高" if worst < 2500 else "❌ 退步")
    print("结论：最差 %.0f ms → %s（判据：正常问句 <1500 ms）" % (worst, verdict))
    return 0 if worst < 2500 else 1


if __name__ == "__main__":
    sys.exit(main())
