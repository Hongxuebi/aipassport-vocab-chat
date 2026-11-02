#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽六册词表 → 统一词库 CSV（背单词 v2 · 2026-10-31）

源：~/桌面/教材/ 六册（译林版，只收单词；八上沿用已入库的 453 条）
顺序：八上（原样）→ 八下 → 九上 → 九下 → 七上 → 七下（D28）
输出：01-词库/初中六册-词库.csv（10 列）＋ 01-词库/词表统计.md

用法：
  python3 _工具/抽六册词表.py --dry     # 只打印各源条数，不写文件
  python3 _工具/抽六册词表.py           # 生成 CSV + 统计
"""
import csv
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
SRC = os.path.join(HOME, "桌面/教材")
ROOT = os.path.join(HOME, "dev/ai-passport")
OUT_CSV = os.path.join(ROOT, "01-词库/初中六册-词库.csv")
OUT_STAT = os.path.join(ROOT, "01-词库/词表统计.md")
BASE_CSV = os.path.join(ROOT, "01-词库/八上-词库.csv")

FIELDS = ["id", "词", "音标", "词性", "中文", "例句", "例句中文", "单元", "难度", "备注"]
UNIT_RE = re.compile(r"^U(\d+)$")


def txt(docx_path):
    """docx → 纯文本（macOS textutil，不装库）"""
    r = subprocess.run(["textutil", "-convert", "txt", "-stdout", docx_path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return r.stdout.decode("utf-8", "replace")


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


# ---------------------------------------------------------------- 八上（基线，原样）
def load_bashang():
    rows = []
    with open(BASE_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({"词": clean(r["词"]), "音标": clean(r["音标"]), "词性": clean(r["词性"]),
                         "中文": clean(r["中文"]), "单元": clean(r["单元"]), "册": "八上"})
    return rows


# ---------------------------------------------------------------- xlsx（七上 / 七下）
def read_xlsx(path):
    import xml.etree.ElementTree as ET
    import zipfile
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", ns):
            ss.append("".join(t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    sheets = sorted(n for n in z.namelist() if re.search(r"worksheets/sheet\d+\.xml$", n))
    out = []
    for name in sheets:
        root = ET.fromstring(z.read(name))
        for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            vals = []
            for c in row.findall("m:c", ns):
                v = c.find("m:v", ns)
                t = c.get("t")
                if v is None or v.text is None:
                    vals.append("")
                elif t == "s":
                    vals.append(ss[int(v.text)] if int(v.text) < len(ss) else "")
                else:
                    vals.append(v.text)
            out.append(vals)
    return out


def load_qishang():
    rows = []
    for r in read_xlsx(os.path.join(SRC, "七上/单词专项/牛津译林版七年级英语上册单词表.xlsx")):
        if len(r) < 6 or not r[2]:
            continue
        unit = clean(r[1])
        if not re.match(r"^Unit\s*\d+", unit, re.I):
            continue
        rows.append({"词": clean(r[2]), "音标": clean(r[3]), "词性": clean(r[4]),
                     "中文": clean(r[5]), "单元": unit, "册": "七上"})
    return rows


def load_qixia():
    rows = []
    for i, r in enumerate(read_xlsx(os.path.join(SRC, "七下/单词专项/Unit1-Unit8单词背诵默写表.xlsx"))):
        pass
    # 每张 sheet 一个单元，顺序 = sheet 序
    import zipfile
    z = zipfile.ZipFile(os.path.join(SRC, "七下/单词专项/Unit1-Unit8单词背诵默写表.xlsx"))
    n_sheet = len([n for n in z.namelist() if re.search(r"worksheets/sheet\d+\.xml$", n)])
    allrows = read_xlsx(os.path.join(SRC, "七下/单词专项/Unit1-Unit8单词背诵默写表.xlsx"))
    # 用“单元标题行”切分：'新译林（2024版）七下Unit1单词表'
    cur = None
    for r in allrows:
        head = clean(r[0]) if r else ""
        m = re.search(r"七下\s*Unit\s*(\d+)", head, re.I)
        if m:
            cur = "Unit " + m.group(1)
            continue
        if not cur or len(r) < 4:
            continue
        w = clean(r[0])
        if not w or w == "单词":
            continue
        rows.append({"词": w, "音标": clean(r[1]), "词性": clean(r[2]),
                     "中文": clean(r[3]), "单元": cur, "册": "七下"})
    return rows


# ---------------------------------------------------------------- docx：八下（五列，一行一条）
def load_baxia():
    rows = []
    lines = [clean(x) for x in txt(os.path.join(SRC, "八下/03 单词表（word，全册）/2026春译林8下单词表.docx")).splitlines()]
    i = 0
    while i < len(lines) - 4:
        m = re.match(r"^(?:U|Unit)\s*(\d+)$", lines[i], re.I)
        if m:
            unit = "Unit " + m.group(1)
            w, ipa, cn = lines[i + 1], lines[i + 2], lines[i + 3]
            if re.match(r"^[A-Za-z*][A-Za-z*' -]*$", w or "") and cn and not cn.isdigit():
                star = w.startswith("*")
                mpos = re.match(r"^((?:n|v|vt|vi|adj|adv|pron|prep|conj|num|abbr|int)\.?(?:&(?:n|v|vi|vt|adj))?\.?)\s*(.*)$", cn)
                pos2, cn2 = (mpos.group(1), clean(mpos.group(2))) if mpos else ("", cn)
                star = star or cn.startswith("*")
                rows.append({"词": clean(w.lstrip("*")), "音标": ipa, "词性": pos2,
                             "中文": clean(cn2.lstrip("*")), "单元": unit, "册": "八下",
                             "备注": "*只认不写" if star else ""})
                i += 5
                continue
        i += 1
    return rows


# ---------------------------------------------------------------- docx：九上（默写清单，纯英文行）
STOP = {"unit", "units", "page", "九上", "九下"}


def load_jiushang():
    """九上：`Unit 1-8 单词.docx` —— 每条 `N.中文[词性] english`，中英粘在一起"""
    rows, cur = [], None
    POS = (r"(?:n|v|vt|vi|adj|adv|pron|prep|conj|num|abbr|int|modal v"
           r"|vt&vi|vt & vi|v & n|vt&n|adj&n|n&v)")
    TAIL_RE = re.compile(r"^\s*((?:" + POS + r")\.?(?=\s|$))?\s*([A-Za-z][A-Za-z'’\-/ ]*)$")
    CJK = re.compile(r"[\u4e00-\u9fff]")
    for line in txt(os.path.join(SRC, "九上/单词专项/Unit 1-8 单词.docx")).splitlines():
        s2 = clean(line)
        m = re.match(r"^U(\d+)\b", s2)
        if m:
            cur = "Unit " + m.group(1)
            s2 = s2[m.end():].strip()
            continue
        if not cur or not s2:
            continue
        for part in re.split(r"(?<!\d)(?=\d{1,2}\.)", s2):
            mm = re.match(r"^\d{1,2}\.(.*)$", clean(part))
            if not mm:
                continue
            body = clean(mm.group(1))
            last = None
            for ch in CJK.finditer(body):
                last = ch
            if not last:
                continue
            tail = body[last.end():]
            cn = clean(body[:last.end()].replace("(词组)", ""))
            mt = TAIL_RE.match(tail)
            if not mt or not cn:
                continue
            pos = clean(mt.group(1) or "")
            en = clean(mt.group(2))
            star = cn.startswith("*")
            cn = clean(cn.lstrip("*"))
            if not en:
                continue
            rows.append({"词": en, "音标": "", "词性": pos, "中文": cn,
                         "单元": cur, "册": "九上"})
    return rows


# ---------------------------------------------------------------- docx：九下（教师版，中文+词性+英文）
def load_jiuxia():
    rows = []
    path = os.path.join(SRC, "九下/单词专项/苏教译林版九年级下册整册书单词默写表-教师版.docx")
    cur = None
    for ln in txt(path).splitlines():
        s = clean(ln)
        m = re.match(r"^Unit\s*(\d+)$", s, re.I)
        if m:
            cur = "Unit " + m.group(1)
            continue
        if not cur or not s:
            continue
        # 例：12旗帜	n. flag / 3中国结	.Chinese knot / 1使人疲劳的，累人的 adj. tiring
        m = re.match(r"^\d+\s*(.*?)\s*((?:n|v|vt|vi|adj|adv|pron|prep|conj|num|abbr|int|modal v)\.?)?\s*([A-Za-z][A-Za-z' -]*)$", s)
        if not m:
            continue
        cn, pos, en = clean(m.group(1)), clean(m.group(2) or ""), clean(m.group(3))
        if not en:
            continue
        rows.append({"词": en, "音标": "", "词性": pos, "中文": cn, "单元": cur, "册": "九下"})
    return rows


# ---------------------------------------------------------------- 合并
def norm(w):
    return clean(w).lower().strip(".,;:!?()（）[]【】")


def build():
    stats = []
    books = [("八上", load_bashang()), ("八下", load_baxia()),
             ("九上", load_jiushang()), ("九下", load_jiuxia()),
             ("七上", load_qishang()), ("七下", load_qixia())]
    out, seen = [], {}
    for book, rows in books:
        kept = dup = phr = nocn = 0
        for r in rows:
            w = clean(r["词"]).lstrip("*").strip()
            if not w:
                continue
            if " " in w and book != "八上":      # 只收单词；八上存量例外（56 条短语）
                phr += 1
                continue
            if not clean(r["中文"]) and book != "八上":
                nocn += 1
                continue
            k = norm(w)
            if k in seen:
                dup += 1
                out[seen[k]]["备注"] = (out[seen[k]]["备注"] + "｜复现:" + book).strip("｜")
                continue
            seen[k] = len(out)
            note = book
            if r.get("备注"):
                note += "｜" + r["备注"]
            out.append({"id": "w%04d" % (len(out) + 1), "词": w, "音标": r["音标"], "词性": r["词性"],
                        "中文": r["中文"], "例句": "", "例句中文": "", "单元": r["单元"],
                        "难度": "", "备注": note})
            kept += 1
        stats.append((book, len(rows), kept, dup, phr, nocn))
    return out, stats


def main():
    out, stats = build()
    print("== 各册 ==")
    for book, raw, kept, dup, phr, nocn in stats:
        print("  %-3s 原始 %4d ｜ 收 %4d ｜ 重复 %3d ｜ 短语 %3d ｜ 无中文 %3d" % (book, raw, kept, dup, phr, nocn))
    print("  **合计 %d 词**" % len(out))
    if "--dry" in sys.argv:
        return 0
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    lines = ["# 六册词表统计（自动生成 · %s）" % __import__("datetime").date.today(), "",
             "| 册 | 原始条数 | 收入 | 跨册重复 | 短语丢弃 | 无中文丢弃 |", "|---|---|---|---|---|---|"]
    for book, raw, kept, dup, phr, nocn in stats:
        lines.append("| %s | %d | %d | %d | %d | %d |" % (book, raw, kept, dup, phr, nocn))
    lines += ["", "**合计 %d 词**（八上含 56 条存量短语）" % len(out)]
    open(OUT_STAT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("已写：", OUT_CSV)
    print("已写：", OUT_STAT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
