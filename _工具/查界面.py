#!/usr/bin/env python3
"""界面静态规则（2026-10-26 三次真机翻车换来的）

① 中文不许用 montserrat（它没有中文字形）——踩过：卡死在文本测量（技能 N11）
② 按键回调（xxx_key）里不许 create/delete LVGL 对象——踩过：关详情后黑屏/回不去（N13）
③ 提醒：中文标签建议给固定 w/h（`ui_pixel_label` 后要有 `lv_obj_set_size`）

影子块那条（N14）静态判不准，留在技能与 32 §22 里，靠"看屏"验。
"""
import os
import re
import sys
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "_参考仓库", "folotoy-ai-passport", "main")

CJK = re.compile(r"[\u4e00-\u9fff]")
BAD_IN_KEY = (
    "lv_obj_delete(",
    "lv_obj_create(",
    "ui_pixel_panel_create(",
    "lv_label_create(",
    "lv_image_create(",
)


def key_body(src, start):
    """从函数体 { 开始，按大括号配平取到结尾"""
    i = start
    depth = 0
    j = i
    while j < len(src):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:j]
        j += 1
    return src[i:]


def main():
    bad = 0
    files = sorted(glob.glob(os.path.join(FW, "*.c")))

    print("== ① 中文 × montserrat ==")
    hit1 = 0
    for f in files:
        src = open(f, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"ui_pixel_label\(([^;]{0,300}?)\)\s*;", src, re.S):
            arg = m.group(1)
            if "lv_font_montserrat" in arg and CJK.search(arg):
                print("  ❌ %s：ui_pixel_label 用 montserrat 渲染中文（会卡死在文本测量）" % os.path.basename(f))
                bad = 1
                hit1 = 1
    if not hit1:
        print("  ✅ 没有 montserrat 渲染中文")

    print("== ② 按键回调里 create/delete ==")
    hit2 = 0
    for f in files:
        src = open(f, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"\b(\w+_key)\s*\([^)]*\)\s*\{", src):
            body = key_body(src, m.end() - 1)
            for b in BAD_IN_KEY:
                if b in body:
                    print("  ❌ %s：%s() 里出现 %s（页面要“建一次+显隐”，别在按键回调里增删对象）"
                          % (os.path.basename(f), m.group(1), b))
                    bad = 1
                    hit2 = 1
    if not hit2:
        print("  ✅ 按键回调里没有 create/delete")

    print("== ③ 中文标签是否给固定尺寸（提醒，不判失败）==")
    warn = 0
    for f in files:
        src = open(f, encoding="utf-8", errors="replace").read()
        n_lab = len(re.findall(r"ui_pixel_label\(", src))
        n_size = len(re.findall(r"lv_obj_set_size\(", src))
        if n_lab > 0 and n_size < n_lab:
            print("  ⚠️  %s：有 %d 个 ui_pixel_label，但只有 %d 个 lv_obj_set_size（中文标签建议固定 w/h）"
                  % (os.path.basename(f), n_lab, n_size))
            warn += 1
    if warn == 0:
        print("  ✅ 中文标签都有固定尺寸")

    print("== ④ lv_label_create 后有没有设中文字体（提醒）==")
    warn2 = 0
    for f in files:
        src = open(f, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"(\w+)\s*=\s*lv_label_create\(", src):
            var = m.group(1)
            tail = src[m.end():m.end() + 700]
            if ("lv_obj_set_style_text_font(" + var) not in tail:
                print("  ⚠️  %s：%s 没设 text_font（默认 montserrat，中文会变方块）"
                      % (os.path.basename(f), var))
                warn2 += 1
    if warn2 == 0:
        print("  ✅ 都有设字体")

    print()
    if bad:
        print("❌ 界面静态检查失败")
        return 1
    print("✅ 界面静态检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
