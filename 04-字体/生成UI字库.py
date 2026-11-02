#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成设备屏用的中文字库（fusion_ui12 / fusion_ui16）

字符集 = 现有字库的字（防止老页面丢字） + 八上词表全部中文释义 + 卡片页界面文案。
为什么要这一步：LVGL 的 Montserrat 没有中文字形；字库是**子集**，少一个字就是方块。
每次词表/界面文案变了都要重跑本脚本。

用法：
  python3 04-字体/生成UI字库.py          # 生成并覆盖 main/fonts/
  python3 04-字体/生成UI字库.py --dry    # 只算字数，不生成
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.expanduser("~/dev/ai-passport")
FONT_DIR = os.path.join(ROOT, "04-字体")
REPO = os.path.join(ROOT, "_参考仓库/folotoy-ai-passport")
OUT_DIR = os.path.join(REPO, "main/fonts")
CSV = os.path.join(ROOT, "01-词库/初中六册-词库.csv")   # 2026-10-31 扩到六册：释义里的新字也得进 12/24px
TTF = os.path.join(FONT_DIR, "fusion-pixel-12px-proportional-zh_hans.ttf")        # 中文：点阵
TTF_LATIN = os.path.join(FONT_DIR, "DejaVuSans.ttf")                                 # 英文/音标：矢量轮廓

# 卡片页等新界面要用到的字（写死，忘了加就会显示方块）
UI_TEXT = (
    "背单词今天要背完忘记模糊认识熟知点错了下次再见本单元剩余已学张继续退出复习新词"
    "正确率第共个全部开始结束进度时间分钟上一下左右按键确定返回主页设置"
    "任释义反复重来"        # 两段式提示："按任意键看释义""翻面"
    "离线只读待传新复"      # 卡片页底栏 D11 新 在线 待传11（2026-10-31 缺「传」→ 方块）
    # 2026-10-28 主页时钟 / 番茄 / 设置（新增文案，漏一个字就是方块）
    "月日周一二三四五六七八九十"
    "番茄专注休息开始暂停继续重置未对时校准"
    "设置音量亮度关于版本实验室返回"
    # 2026-10-29 夜：桌宠页标题改成指定的名字（12/24px 也要有，否则标题一换就方块）
    "大肥鱼"
)

NODE_BIN = "/usr/local/bin"
CACHE = os.path.join(FONT_DIR, ".cache")      # 字底没变就跳过 lv_font_conv（一次 1~3 分钟）


def charset_of(path):
    """从已生成的字库 .c 里读出它当时用了哪些字（Opts 注释里那句 --symbols）。"""
    if not os.path.exists(path):
        return set()
    # 2026-10-29：扩字后 `--symbols` 那行有 3900+ 字，读 4096 会**截掉尾巴** ⇒ 多读一点
    s = open(path, encoding="utf-8", errors="replace").read(65536)
    m = re.search(r"--symbols\s+(\S+)", s)
    # ⚠️ 用 U+3000（全角空格）当过正则的断点：`\S+` 会在它那里停 ⇒ 只捞到前 97 字。
    #    2026-10-29 踩过：扩字后 charset_of 只读回 ASCII，下次重生成会**丢掉整个中文底**。
    return {ch for ch in m.group(1) if ch.strip()} if m else set()


def gb2312_common():
    """GB2312 一级汉字（3755）＋ A1 标点 / A3 全角区 —— 「常用 3500」，覆盖日常文本 99%+。

    为什么用它当"屏上任意识别文本"的字底（2026-10-29）：
      屏上要回显**识别出来的任意一句话**（后面还有气泡、日记），924 字的子集必然蹦方块。
      一级字表是国标里"最常用"的那 3755 个，正好是"随手一句话基本不缺字"的那一档；
      再往上（二级 3008 个）就是人名地名冷字，字库翻倍、屏上一年也用不到几个。
    """
    chars = set()
    for hi in range(0xB0, 0xD8):                    # 一级汉字：0xB0A1 .. 0xD7F9
        for lo in range(0xA1, 0xFF):
            if hi == 0xD7 and lo > 0xF9:             # D7FA 起是二级字，别混进来
                break
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    for hi in (0xA1, 0xA3):                          # A1 标点　A3 全角字母数字
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def cjk_from_csv(path):
    """词表里所有需要上屏的非 ASCII 字符：中文释义 + **音标**。
    音标里 ˈ ˌ ʃ ə ɡ 这些都不是 ASCII，不塞进子集屏上就是空白（踩过：Montserrat 没中文）。"""
    import csv
    chars = set()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            for col in ("中文", "音标"):
                for ch in (r.get(col) or ""):
                    if ord(ch) > 0x7E:
                        chars.add(ch)
    return chars


def build_symbols():
    """界面文案用的字底（12 / 24 / 36px 用这一份）。

    🔴 **只从 12px 的老字库续字，不要从 16px 续**（2026-10-29 扩字时差点踩）：
       16px 现在是"常用 3500"的大字库，把它当种子会让 12/24px 一起涨到 3900 字，
       flash 直接多吃 200 KB+。12px 是界面文案的正本，16px 是"任意文本"，两份要分开。
    """
    s = set()
    s |= charset_of(os.path.join(OUT_DIR, "fusion_ui12.c"))
    s |= cjk_from_csv(CSV)
    s |= {ch for ch in UI_TEXT if ord(ch) > 0x7F}
    # 音标常用符号（音标列已覆盖绝大多数，这里补全角标点等边界情况）
    s |= set("ˈˌːəɪʊʌɔæɑɒɜɞθðʃʒŋɡɚɝɐ")
    s |= set("●▲▼•·")          # 按键提示里的圆点/三角（不塞进去就是方块）
    s |= set("，。？！、；：「」『』（）《》…—～·“”‘’")   # 台词气泡/回显要用的中文标点
    # 去掉不可见/空白，排序只为稳定可复现
    s = {ch for ch in s if ch.strip()}
    return "".join(sorted(s))


# 说话的"口头字"：GB2312 **一级字表里没有**，但识别文本/聊天里天天出现（2026-10-29 抽检发现「嗯」缺）
TALK_EXTRA = (
    "嗯呃唔噢喔耶呗嘛咯唉哎嘿嗨咱咋甭掰搞俺您啥唷啦呐呗瞅嘚瑟"
    "嘚怼囧趴蹭愣懵"
)


# 名字/绰号里的字：GB2312 一级**没有**（徐是二级、煜/崴是二级甚至更偏），但页面标题要写死
# 顶栏标题「大肥鱼」。2026-10-29 夜实测：标题字必须进子集，否则标题蹦方块。**只进 16px**。
NAME_EXTRA = ""          # 顶栏标题用到的额外字（换了标题就往这儿加）


def build_echo_symbols(base):
    """**屏上任意识别文本**用的字底（16px 专用）：界面字底 ∪ 常用 3500。

    ⚠️ 必须把空白滤掉（GB2312 的 A1 区里有 U+3000 全角空格）：它会让 lv_font_conv
      写在 `--symbols` 里的那一行**从中间断开**，`charset_of()` 只能读回前半截。
    """
    s = {ch for ch in (set(base) | gb2312_common() | set(TALK_EXTRA) | set(NAME_EXTRA))
         if ch.strip()}
    return "".join(sorted(s))


def gen(size, symbols, out_path, ttf=None, bpp=1):
    """生成一个字号。**字底没变就直接跳过**（2026-10-29 夜：一晚上重跑 3 次，每次 1–3 分钟）。"""
    path = ttf or TTF
    try:
        st = os.stat(path)
        ttf_id = "%s@%d.%d" % (path, st.st_size, int(st.st_mtime))
    except OSError:
        ttf_id = path
    key = hashlib.sha1(("%d|%d|%s|%s" % (size, bpp, ttf_id, symbols)).encode("utf-8")).hexdigest()[:16]
    os.makedirs(CACHE, exist_ok=True)
    stamp = os.path.join(CACHE, "%dpx_%dbpp_%s.ok" % (size, bpp, key))
    if os.path.exists(stamp) and os.path.exists(out_path):
        print(f"  ⏭  {size}px 字底没变（缓存命中 {key[:8]}）→ 不重跑 lv_font_conv")
        return

    env = dict(os.environ)
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    cmd = [
        "npx", "--yes", "lv_font_conv@1.5.2",
        "--size", str(size), "--bpp", str(bpp), "--format", "lvgl",
        "--font", ttf or TTF,
        "-r", "0x20-0x7E",
        "--symbols", symbols,
        "--no-kerning",
        # 🔴 必须 --no-compress：lv_font_conv 默认会把多 bit 的字体压成 RLE（bitmap_format=1），
        #    而本固件没开 LV_USE_FONT_COMPRESSED → LVGL 取位图直接返回 NULL，屏上英文一片空白。
        #    2026-10-25 踩过：4bpp 英文"不见了"，中文 1bpp 正常（它本来就是未压缩）。
        "--no-compress",
        "-o", out_path,
    ]
    print(f"  → {size}px 生成中（{len(symbols)} 字）…")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-3000:])
        sys.exit(f"❌ lv_font_conv 失败（{size}px）")

    # lv_font_conv 生成的 include 路径是 lvgl/lvgl.h，本仓库要的是 lvgl.h
    s = open(out_path, encoding="utf-8").read()
    s2 = s.replace('#include "lvgl/lvgl.h"', '#include "lvgl.h"')
    if s2 != s:
        open(out_path, "w", encoding="utf-8").write(s2)
    print(f"  ✅ {os.path.basename(out_path)}  {len(s2)/1024:.1f} KB")
    try:
        open(stamp, "w", encoding="utf-8").write(key)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(TTF):
        sys.exit(f"❌ 缺字体文件：{TTF}（先跑 04-字体/拉取字体.sh）")

    old12 = charset_of(os.path.join(OUT_DIR, "fusion_ui12.c"))
    words = cjk_from_csv(CSV)
    symbols = build_symbols()
    echo_symbols = build_echo_symbols(symbols)
    print(f"界面字底 {len(symbols)} 字（12/24px）｜ 回显大字底 {len(echo_symbols)} 字（16px）")
    print(f"现有 12px 字库 {len(old12)} 字 ｜ 词表释义 {len(words)} 字")

    missing_in_old = sorted(words - old12)
    print(f"词表里有、老字库没有的字：{len(missing_in_old)} 个")

    if a.dry:
        print("（--dry：不生成）")
        return

    for f in ("fusion_ui12.c", "fusion_ui16.c"):
        p = os.path.join(OUT_DIR, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(FONT_DIR, "_raw", f + ".bak"))
    print(f"旧字库已备份到 04-字体/_raw/*.bak")

    gen(12, symbols, os.path.join(FONT_DIR, "fusion_ui12.c"))
    # 16px 是「屏上任意识别文本」那一档（回显 / 气泡 / 日记都用它）⇒ 字底扩到常用 3500。
    # ⚠️ 只有它扩：12/24px 涨到 3900 字要多花 200 KB+ flash，而它们只显示**固定文案**。
    gen(16, echo_symbols, os.path.join(FONT_DIR, "fusion_ui16.c"))
    # 24px 是 12px 的**整数 2 倍**，点阵字体放大后仍是硬边（不出毛边）——
    # 卡片页的释义要大字，用它（2026-10-25 作者："汉字有点小"）
    gen(24, symbols, os.path.join(FONT_DIR, "fusion_ui24.c"))
    # 36px 只给"预览版面"用（作者 2026-10-25 定：上机维持 24px）。
    # 写到 04-字体/_预览/，**不进固件**（省 Flash，也别把它混进补丁）。
    gen(36, symbols, os.path.join(FONT_DIR, "_预览", "fusion_ui36.c"))

    # ---------------------------------------------------------------- 英文/音标
    # 为什么要另做一套：中文点阵 1bpp 够用且省内存，但英文放大到 28px 会出毛边（"糊"）。
    # 英文改用 DejaVu Sans 矢量轮廓 + **4bpp 抗锯齿**，同样字数只占几十 KB。
    if not os.path.exists(TTF_LATIN):
        sys.exit(f"❌ 缺拉丁字体：{TTF_LATIN}（04-字体/拉取拉丁字体.sh）")
    ipa = {ch for ch in cjk_from_csv(CSV) if ord(ch) < 0x4E00}   # 音标符号（去掉汉字）
    latin_syms = "".join(sorted(ipa | set("●▲▼•·ˈˌː")))
    for sz in (28, 20, 16):
        gen(sz, latin_syms, os.path.join(FONT_DIR, f"latin{sz}.c"),
            ttf=TTF_LATIN, bpp=4)

    for f in ("fusion_ui12.c", "fusion_ui16.c", "fusion_ui24.c",
              "latin28.c", "latin20.c", "latin16.c"):
        shutil.copy2(os.path.join(FONT_DIR, f), os.path.join(OUT_DIR, f))
    print("✅ 已覆盖 main/fonts/（下次编译生效）")


if __name__ == "__main__":
    main()
