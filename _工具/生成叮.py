#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成叮.py —— 番茄到点那声「叮」：合成一段铃音 → 落成固件里的常驻数组。

为什么要自己合成（而不是塞一段现成 wav）：
  · 一段 0.45 s 的 16k PCM 只有 14,400 B，直接 `const` 常驻 app 分区，**零依赖、零延迟**
    （账见 `73-番茄叮-分区余量与方案-2026-10-29.md`）。
  · 铃音的"叮"= 非谐泛音 + 指数衰减。整数倍泛音会像电子琴，不像金属。参数都写在下面，改完重跑。

产物（都在固件工作副本里，别手改）：
  main/ding_pcm.c   数组本体（int16 LE，flash 直读，i2s 直接喂）
  main/ding_pcm.h   K_DING_SR / K_DING_PCM_N / k_ding_pcm
  ../../08-语音对话/叮-预览.wav   同一个波形的 wav（在 Mac 上试听用，不进固件）

用法：
  python3 _工具/生成叮.py            # 用默认参数
  python3 _工具/生成叮.py --f0 1318.5 --ms 450
"""
import argparse
import os
import struct
import sys
import wave

# ---------------- 音色参数（改这里，不改代码） ----------------
SR = 16000                 # 和语音页同格式：换格式要重开 codec（会"咔"）
F0 = 1318.5                # 基频 Hz（E6）：亮但不刺耳
MS = 450                   # 时长
PEAK = 0.55                # 峰值占满量程的比例（留 ~5 dB 余量，音量 95% 也不削顶）
ATTACK_MS = 3.0            # 起音（直接切会有"啪"）
RELEASE_MS = 30.0          # 收尾收零
# (倍频, 幅度, 衰减时间常数 ms) —— 泛音频率比故意不是整数：1 / 2.01 / 2.98 / 4.16
# 衰减常数按"450 ms 内自己死到 −30 dB"配的：τ 太长（>200）会在结尾被硬切断，听着像"截断"
PARTIALS = [
    (1.00, 0.50, 150.0),
    (2.01, 0.24, 95.0),
    (2.98, 0.13, 62.0),
    (4.16, 0.06, 38.0),
]

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.join(HERE, "..", "_参考仓库", "folotoy-ai-passport")
OUT_C = os.path.join(FW, "main", "ding_pcm.c")
OUT_H = os.path.join(FW, "main", "ding_pcm.h")
OUT_WAV = os.path.join(HERE, "..", "08-语音对话", "叮-预览.wav")


def synth(f0, ms):
    import math
    n = int(SR * ms / 1000)
    a_n = max(1, int(SR * ATTACK_MS / 1000))
    r_n = max(1, int(SR * RELEASE_MS / 1000))
    buf = [0.0] * n
    for ratio, amp, tau_ms in PARTIALS:
        w = 2.0 * math.pi * f0 * ratio / SR
        tau = tau_ms / 1000.0 * SR          # 采样数
        for i in range(n):
            env = amp * math.exp(-i / tau)
            buf[i] += env * math.sin(w * i)
    # 起音：升余弦；收尾：余弦收到 0（两端都安全）
    for i in range(min(a_n, n)):
        buf[i] *= 0.5 - 0.5 * math.cos(math.pi * i / a_n)
    for i in range(min(r_n, n)):
        k = n - 1 - i                       # i=0 是最后一个样本 ⇒ 系数必须是 0（贴 0 收尾）
        if k >= 0:
            buf[k] *= 0.5 - 0.5 * math.cos(math.pi * i / r_n)
    peak = max(abs(x) for x in buf) or 1.0
    scale = PEAK * 32767.0 / peak
    return [int(round(x * scale)) for x in buf]


def write_c(pcm):
    lines = []
    lines.append("// main/ding_pcm.c —— **由 _工具/生成叮.py 生成，别手改**")
    lines.append("//")
    lines.append("// 番茄到点那声「叮」的 PCM 本体：%d Hz / 16 bit / 单声道 / %d 采样（%.0f ms）"
                 % (SR, len(pcm), len(pcm) * 1000.0 / SR))
    lines.append("// 为什么常驻 flash 而不是塞 SPIFFS：见 `73-番茄叮-分区余量与方案-2026-10-29.md`。")
    lines.append("// 改音色改脚本里的 PARTIALS / F0 / MS，然后重跑脚本；不要直接编辑这个文件。")
    lines.append('#include "ding_pcm.h"')
    lines.append("")
    lines.append("const int16_t k_ding_pcm[K_DING_PCM_N] = {")
    for i in range(0, len(pcm), 16):
        chunk = ", ".join("%d" % v for v in pcm[i:i + 16])
        lines.append("    " + chunk + ",")
    lines.append("};")
    lines.append("")
    lines.append("const unsigned k_ding_pcm_n = K_DING_PCM_N;")
    lines.append("")
    open(OUT_C, "w", encoding="utf-8").write("\n".join(lines))

    hdr = """// main/ding_pcm.h —— **由 _工具/生成叮.py 生成，别手改**
//
// 番茄到点「叮」的波形：%d 采样 @ %d Hz（%.0f ms，%d B）。
// 播放口径见 main/app_ding.h —— 只能在独立任务里喂 I2S，绝不在 LVGL 上下文里写。
#pragma once

#include <stdint.h>

#define K_DING_SR     %d
#define K_DING_PCM_N  %d

extern const int16_t  k_ding_pcm[K_DING_PCM_N];
extern const unsigned k_ding_pcm_n;
""" % (len(pcm), SR, len(pcm) * 1000.0 / SR, len(pcm) * 2, SR, len(pcm))
    open(OUT_H, "w", encoding="utf-8").write(hdr)


def write_wav(pcm):
    os.makedirs(os.path.dirname(OUT_WAV), exist_ok=True)
    with wave.open(OUT_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", v) for v in pcm))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--f0", type=float, default=F0)
    ap.add_argument("--ms", type=float, default=MS)
    ap.add_argument("--no-wav", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.dirname(OUT_C)):
        sys.exit("❌ 没找到固件工作副本：%s（先跑过一次刷机脚本？）" % OUT_C)

    pcm = synth(a.f0, a.ms)
    write_c(pcm)
    if not a.no_wav:
        write_wav(pcm)

    n = len(pcm)
    peak = max(abs(v) for v in pcm)
    rms = (sum(v * v for v in pcm) / n) ** 0.5
    import math
    print("叮 → %s" % os.path.relpath(OUT_C, os.path.join(HERE, "..")))
    print("  基频 %.1f Hz ｜ %d 采样 ｜ %.0f ms ｜ %d B（%.1f KB）"
          % (a.f0, n, n * 1000.0 / SR, n * 2, n * 2 / 1024.0))
    print("  峰值 %d（%.2f FS）｜ RMS %.0f（%.1f dBFS）｜ 首样本 %d ｜ 末样本 %d"
          % (peak, peak / 32767.0, rms, 20 * math.log10(rms / 32767.0), pcm[0], pcm[-1]))
    if not a.no_wav:
        print("  试听 wav：%s" % os.path.relpath(OUT_WAV, os.path.join(HERE, "..")))
    # 自检：两端必须贴 0，否则就是爆音（§15 老坑）
    if abs(pcm[0]) > 200 or abs(pcm[-1]) > 200:
        print("  ⚠️ 首/末样本不为零 —— 会有「啪」一声，检查包络")
    if n * 2 > 20000:
        print("  ⚠️ 超过 20 KB，重新算分区余量（`73` 里的账）")


if __name__ == "__main__":
    main()
