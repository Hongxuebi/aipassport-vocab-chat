#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出默写单（T7）：20 词（本周新词 14 ＋ 错词 6）→ 可打印 HTML。

用法（在服务端机上跑；数据就在本地）：
  python3 出默写单.py                 # 出今天那张
  python3 出默写单.py --date 2026-11-08
  python3 出默写单.py --记 2026-11-08 95 teen,friend   # 录分数＋错词（进 默写.jsonl）

产物：~/aipassport/默写/默写-YYYY-MM-DD.html（看板「周默写」块可点开/打印）
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

DATA = os.path.expanduser(os.environ.get("AIP_DATA_DIR", "~/aipassport"))
STATE = os.path.join(DATA, "state.json")
EVENTS = os.path.join(DATA, "events.jsonl")
WORDS = os.path.join(DATA, "words.csv")
VERBS = os.path.join(DATA, "不规则动词.csv")      # B · 不规则动词表（2026-11-01）
AFFIX = os.path.join(DATA, "词缀.csv")            # C · 构词法词缀表（2026-11-01）
OUT_DIR = os.path.join(DATA, "默写")
DICT_LOG = os.path.join(DATA, "默写.jsonl")


def load_words():
    out = []
    with open(WORDS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.append({"word": r.get("词", ""), "ipa": r.get("音标", ""),
                        "pos": r.get("词性", ""), "cn": r.get("中文", ""),
                        "chunk": r.get("词块", "") or "",      # v0.124：词块（默写单上做"词块填词"）
                        "chunk2": r.get("词块2", "") or ""})    # A 第二层（2026-11-01）：答案页给第二条搭配
    return out


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def load_events():
    rows = []
    try:
        for line in open(EVENTS, encoding="utf-8"):
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


def pick(words, state, events):
    cards = state.get("cards") or {}
    cur_day = int(state.get("day") or 0)
    first_day = {}
    again = {}
    for e in events:
        try:
            wid = int(e.get("id") or 0)
            d = int(e.get("day") or 0)
            g = int(e.get("g") or 0)
        except Exception:
            continue
        if wid <= 0 or wid > len(words):
            continue
        if wid not in first_day or d < first_day[wid]:
            first_day[wid] = d
        if g == 1:
            again[wid] = again.get(wid, 0) + 1

    # ① 本周新词：第一次出现落在最近 7 个学习日内，取最新的 14 个
    week = [wid for wid, d in first_day.items() if cur_day - 6 <= d <= cur_day]
    week.sort(key=lambda w: (-first_day[w], w))
    picked = week[:14]
    if len(picked) < 14:
        # 不够就往前补（越新越前）
        rest = sorted([w for w in first_day if w not in picked], key=lambda w: (-first_day[w], w))
        picked.extend(rest[:14 - len(picked)])

    # ② 错词：lapses 优先，其次"忘记"次数
    laps = []
    for k, c in cards.items():
        try:
            wid = int(k)
            n = int(c.get("lapses") or 0)
        except Exception:
            continue
        if 0 <= wid < len(words) and n > 0:
            laps.append((n, again.get(wid, 0), wid))
    laps.sort(key=lambda x: (-x[0], -x[1], x[2]))
    wrong = [w for _, _, w in laps if w not in picked][:6]
    if len(wrong) < 6:
        extra = sorted([(n, w) for w, n in again.items() if w not in picked and w not in wrong],
                       key=lambda x: (-x[0], x[1]))
        wrong.extend([w for _, w in extra][:6 - len(wrong)])
    if len(wrong) < 6:
        # 还不够就补"最不稳"的卡（S 小）
        soft = []
        for k, c in cards.items():
            try:
                wid = int(k)
                s = float(c.get("S") or 0)
            except Exception:
                continue
            if 0 <= wid < len(words) and wid not in picked and wid not in wrong:
                soft.append((s, wid))
        soft.sort()
        wrong.extend([w for _, w in soft][:6 - len(wrong)])

    # 交错排：先新词后错词；同一天里保持稳定
    return picked, wrong


def _chunk_blank(chunk, word):
    """把词块里的原词挖成 ___（"a bit of" → "a ___ of"）。带 `->` 的词块对不挖（挖了等于送答案）。"""
    c = (chunk or "").strip()
    if not c or "->" in c or not word:
        return c
    pat = re.compile(r"\b%s\b" % re.escape(word), re.I)
    return pat.sub("___", c, count=1)


def render_html(date_s, picked, wrong, words):
    rows = []
    for i, wid in enumerate(picked + wrong, 1):
        w = words[wid]
        cn = w["cn"] or ""
        pos = (w["pos"] or "").strip()
        label = ("%s %s" % (pos, cn)).strip() if pos else cn
        _ck = _chunk_blank(w.get("chunk"), w.get("word"))          # v0.124：词块里填词
        if _ck and "___" in _ck:
            label = label + '<br><span class="ck">词块：%s</span>' % _ck
        rows.append((i, label))
    q = "\n".join(
        '<tr><td class="no">%d</td><td class="cn">%s</td><td class="line"></td></tr>' % (i, label)
        for i, label in rows)
    def _cks(w):
        """答案行：两条词块都列出来（A 第二层，2026-11-01）。第一条在前，缺哪条跳哪条。"""
        parts = []
        if w.get("chunk"):
            parts.append('<span class="ck">%s</span>' % w["chunk"])
        if w.get("chunk2") and w.get("chunk2") != w.get("chunk"):
            parts.append('<span class="ck">%s</span>' % w["chunk2"])
        return ("<br>" + "<br>".join(parts)) if parts else ""

    ans = "\n".join(
        '<tr><td class="no">%d</td><td class="en">%s</td><td class="cn2">%s%s</td></tr>'
        % (i, words[wid]["word"], (words[wid]["pos"] + " " + words[wid]["cn"]).strip(), _cks(words[wid]))
        for i, wid in enumerate(picked + wrong, 1))
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>英语默写 · %(date)s</title>
<style>
:root{--ink:#16202b;--line:#9aa7b4;--muted:#6b7a89}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#fff;color:var(--ink);
     font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:14px}
table{width:100%%;border-collapse:collapse;table-layout:fixed}
td{border-bottom:1px solid var(--line);padding:9px 4px;vertical-align:bottom}
.no{width:34px;color:var(--muted);font-size:12px}
.cn{width:46%%}
.line{width:54%%}
.en{font-weight:600;width:46%%}
.cn2{color:var(--muted);width:54%%;font-size:13px}
.ck{color:#7f8fa6;font-size:12px;font-family:ui-monospace,Consolas,monospace}
.note{color:var(--muted);font-size:12px;margin-top:14px}
@media print{
  body{padding:0}
  @page{size:A4;margin:14mm 12mm}
  .answers{page-break-before:always}
  .noprint{display:none}
}
</style></head>
<body>
<h1>英语默写 · %(date)s</h1>
<div class="sub">共 20 词：本周新词 14 ＋ 错词 6 ｜ 看中文写英文，写完翻第二页对答案</div>
<table>%(rows)s</table>
<div class="note noprint">打印：浏览器菜单 → 打印 → A4；手机可直接「打印」或存 PDF。</div>
<div class="answers">
  <h1>答案 · %(date)s</h1>
  <table>%(ans)s</table>
</div>
</body></html>
""" % {"date": date_s, "rows": q, "ans": ans}


_SHEET_CSS = """
:root{--ink:#16202b;--line:#9aa7b4;--muted:#6b7a89}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#fff;color:var(--ink);
     font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;table-layout:fixed}
td{border-bottom:1px solid var(--line);padding:9px 4px;vertical-align:bottom}
th{text-align:left;color:var(--muted);font-size:12px;font-weight:400;
   border-bottom:1px solid var(--line);padding:6px 4px}
.no{width:34px;color:var(--muted);font-size:12px}
.mono{font-family:ui-monospace,Consolas,monospace}
.muted{color:var(--muted);font-size:13px}
@media print{body{padding:0}@page{size:A4;margin:14mm 12mm}.answers{page-break-before:always}.noprint{display:none}}
"""


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def render_verbs_html(date_s):
    """B · 不规则动词：左页填过去式/过去分词，右页给答案＋例句（98 §8.2）。"""
    rows = _read_csv(VERBS)
    if not rows:
        return "<html><body><p>没有 不规则动词.csv</p></body></html>"
    q = "\n".join(
        '<tr><td class="no">%d</td><td>%s<br><span class="muted">%s</span></td>'
        '<td></td><td></td></tr>'
        % (i, w["原形"], w["中文"]) for i, w in enumerate(rows, 1))
    ans = "\n".join(
        '<tr><td class="no">%d</td><td>%s</td><td class="mono">%s</td><td class="mono">%s</td></tr>'
        '<tr><td></td><td colspan="3" class="muted">%s %s</td></tr>'
        % (i, w["原形"], w["过去式"], w["过去分词"], w["例句"], w.get("例句中文") or "")
        for i, w in enumerate(rows, 1))
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>不规则动词 · %(date)s</title><style>%(css)s</style></head><body>
<h1>不规则动词 · %(date)s</h1>
<div class="sub">共 %(n)d 个（只收词表里出现过的）｜ 写出过去式和过去分词，写完翻第二页对答案</div>
<table><tr><th class="no">#</th><th>原形 / 中文</th><th>过去式</th><th>过去分词</th></tr>%(q)s</table>
<div class="answers"><h1>答案 · %(date)s</h1>
<table><tr><th class="no">#</th><th>原形</th><th>过去式</th><th>过去分词</th></tr>%(ans)s</table></div>
<div class="note muted noprint">打印：浏览器菜单 → 打印 → A4。</div>
</body></html>
""" % {"date": date_s, "css": _SHEET_CSS, "q": q, "ans": ans, "n": len(rows)}


def render_affix_html(date_s):
    """C · 构词法词缀表：例词只从 1,693 词里取（98 §8.3）。"""
    rows = _read_csv(AFFIX)
    if not rows:
        return "<html><body><p>没有 词缀.csv</p></body></html>"
    body = []
    for w in rows:
        ex = [w.get("例词1"), w.get("例词2"), w.get("例词3"), w.get("例词4")]
        ex = " · ".join(x for x in ex if x)
        body.append('<tr><td class="mono">%s</td><td>%s</td><td class="mono">%s</td></tr>'
                    % (w["词缀"], w["含义"], ex))
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>构词法词缀 · %(date)s</title><style>%(css)s</style></head><body>
<h1>构词法词缀 · %(date)s</h1>
<div class="sub">共 %(n)d 条 ｜ 例词全部取自你的 1,693 词，全是背过的</div>
<table><tr><th>词缀</th><th>含义</th><th>例词</th></tr>%(body)s</table>
</body></html>
""" % {"date": date_s, "css": _SHEET_CSS, "body": "\n".join(body), "n": len(rows)}


def _xml_esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _cell(text, w, bold=False, underline=False, size=24):
    rpr = '<w:rPr>%s%s<w:sz w:val="%d"/></w:rPr>' % (
        "<w:b/>" if bold else "", "<w:u w:val=\"single\"/>" if underline else "", size)
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
            '<w:tcBorders><w:bottom w:val="single" w:sz="4" w:space="0" w:color="9AA7B4"/></w:tcBorders>'
            '</w:tcPr><w:p><w:pPr><w:spacing w:after="0" w:line="360" w:lineRule="auto"/></w:pPr>'
            '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p></w:tc>'
            % (w, rpr, _xml_esc(text)))


def _table(rows):
    out = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:jc w:val="center"/>'
           '<w:tblLayout w:type="fixed"/></w:tblPr>'
           '<w:tblGrid><w:gridCol w:w="700"/><w:gridCol w:w="3900"/><w:gridCol w:w="4700"/></w:tblGrid>']
    for i, (no, a, b) in enumerate(rows, 1):
        out.append('<w:tr>' + _cell(no, 700) + _cell(a, 3900) + _cell(b, 4700, underline=not b) + '</w:tr>')
    out.append('</w:tbl>')
    return "".join(out)


def _p(text, size=24, bold=False, center=False, after=120):
    return ('<w:p><w:pPr><w:spacing w:after="%d"/>%s</w:pPr><w:r><w:rPr>%s<w:sz w:val="%d"/></w:rPr>'
            '<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (after, '<w:jc w:val="center"/>' if center else "", "<w:b/>" if bold else "",
               size, _xml_esc(text)))


def build_docx(date_s, picked, wrong, words, path):
    """A4 的 .docx（Word/ WPS 直接打开就能打印）。零依赖手写 OOXML。"""
    qrows = []
    for i, wid in enumerate(picked + wrong, 1):
        w = words[wid]
        label = ("%s %s" % ((w["pos"] or "").strip(), w["cn"] or "")).strip()
        qrows.append((i, label, ""))
    arows = []
    for i, wid in enumerate(picked + wrong, 1):
        w = words[wid]
        # 答案格：中文 ＋ 词块（A 第二层起两条都列；docx 以前一条都不带，2026-11-01 补上）
        cks = [x for x in (w.get("chunk"), w.get("chunk2")) if x]
        if len(cks) == 2 and cks[0] == cks[1]:
            cks = cks[:1]
        cn = ("%s %s" % ((w["pos"] or "").strip(), w["cn"] or "")).strip()
        if cks:
            cn = cn + "　｜ 词块：" + " / ".join(cks)
        arows.append((i, w["word"], cn))

    body = [
        _p("英语默写 · %s" % date_s, size=36, bold=True, center=True, after=60),
        _p("共 20 词：本周新词 14 ＋ 错词 6 ｜ 看中文写英文，写完翻页对答案", size=20, center=True, after=200),
        _table(qrows),
        '<w:p><w:r><w:br w:type="page"/></w:r></w:p>',
        _p("答案 · %s" % date_s, size=32, bold=True, center=True, after=120),
        _table(arows),
        ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
         '<w:pgMar w:top="1021" w:right="1021" w:bottom="1021" w:left="1021" w:header="567" w:footer="567" w:gutter="0"/>'
         '</w:sectPr>'),
    ]
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % "".join(body))

    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)


def record(date_s, score, wrong_str):
    wrong = [x.strip() for x in re.split(r"[,，、\s]+", wrong_str or "") if x.strip()]
    line = {"date": date_s, "score": score, "wrong": wrong, "at": int(dt.datetime.now().timestamp())}
    with open(DICT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print("已记：%s ｜ %s 分 ｜ 错词 %s" % (date_s, score, ",".join(wrong) or "无"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--记", nargs=3, metavar=("DATE", "SCORE", "WRONG"), help="录分数：--记 2026-11-08 95 teen,friend")
    ap.add_argument("--动词变形", dest="verbs", action="store_true", help="只出「不规则动词」练习页（B）")
    ap.add_argument("--词缀", dest="affix", action="store_true", help="只出「构词法词缀」参考页（C）")
    a = ap.parse_args()
    if a.verbs or a.affix:
        os.makedirs(OUT_DIR, exist_ok=True)
        name = "动词变形" if a.verbs else "词缀"
        fn = render_verbs_html if a.verbs else render_affix_html
        out = os.path.join(OUT_DIR, "%s-%s.html" % (name, a.date))
        open(out, "w", encoding="utf-8").write(fn(a.date))
        print("已出：%s" % out)
        return
    if a.__dict__.get("记"):
        record(*a.__dict__["记"])
        return
    date_s = a.date
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_s):
        sys.exit("日期格式要 YYYY-MM-DD")
    words = load_words()
    state = load_state()
    events = load_events()
    picked, wrong = pick(words, state, events)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "默写-%s.html" % date_s)
    open(out, "w", encoding="utf-8").write(render_html(date_s, picked, wrong, words))
    docx = os.path.join(OUT_DIR, "默写-%s.docx" % date_s)
    build_docx(date_s, picked, wrong, words, docx)
    print("已出：%s" % out)
    print("已出：%s" % docx)
    print("新词 %d ｜ 错词 %d ｜ 词表 %d 条" % (len(picked), len(wrong), len(words)))


if __name__ == "__main__":
    main()
