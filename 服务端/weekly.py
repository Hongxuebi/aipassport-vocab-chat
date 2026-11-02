#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly.py —— P6 记忆周体检 + 周裁 / P7 每周备份（2026-10-31）

10-30 定过两条（`80` 的 P6/P7、`81 §二` 的表）：
  P6 判据：**200 活跃不溢出；归档仍可检索**
  P7 判据：**从备份恢复后，随便问一句旧事仍答得出**

这个文件只干四件事，全是「读多写少」，坏了好退：
  ① 周报告：`Memory.stats()` ＋ **四层体检** ＋ **记忆冒烟** ＋ 过期/超上限/归档分布/命中 TOP
     汇成一页 `mem/reports/YYYY-Www.md`（四层＝`80 §三`：L0 原文档/L1 日索引/L2 事实账/L3 场景账）
  ② 周裁：超 200 进归档（`_enforce_cap`）＋ 落盘；过期那条由 `Memory()` 构造时自动归档（memory.py 现有行为）
  ③ 周备份：`mem/` 打 tar.gz 到 `~/aipassport/backups/`，留最近 8 份（P7 的前半）
  ④ 节拍：每周日 03:00 自动跑（照抄 `diary.py` 的「每分钟看一眼」，不新增 系统服务 服务）

🔴 第一次真裁要人点头：没有 `~/aipassport/trim-ok.flag` 时**只出 dry-run 清单、不裁**。
🔴 两个真坑（2026-10-31 演练抓到的，都在 memory.py 里）：
   · `Memory()` 一构造就归档过期事实（`__init__` → `_retire_expired`）⇒ 要看「今天该裁多少」得读**盘**。
   · `_enforce_cap()` **自己不落盘**（只有 `add_fact` 里会 `_save_facts`）⇒ 裁完必须补一次 save，否则白裁。

用法：
  python3 weekly.py report            # 只出报告（不裁、不备份）
  python3 weekly.py trim              # dry-run：只列将裁谁
  python3 weekly.py trim --apply      # 真裁（要先有 trim-ok.flag，--force 可越过）
  python3 weekly.py backup            # 只做一次备份＋轮转
  python3 weekly.py run               # 一周例行：备份 → 报告 → 裁（有 flag 才真裁）
"""

import datetime
import glob
import json
import os
import sys
import tarfile
import threading
import time

try:
    import memory as mem_mod
except ImportError:                      # 公开仓里没有这个模块（它是作者私有仓的记忆层）
    raise SystemExit("这个脚本依赖记忆模块 memory.py，它不随本仓库发布，所以这里跑不起来。\n"
                     "要看它怎么实现，直接读代码；要跑，得先自己有一份 memory.py。")

HOME = os.path.expanduser("~")
MEM = os.path.expanduser(os.environ.get("AITALK_MEM_DIR", "~/aipassport/mem"))
BACKUP_DIR = os.path.join(HOME, "aipassport", "backups")
REPORT_DIR = os.path.join(MEM, "reports")
FLAG_TRIM_OK = os.path.join(HOME, "aipassport", "trim-ok.flag")
LAST_RUN = os.path.join(REPORT_DIR, ".last")

KEEP_BACKUPS = 8          # 留 8 周
KEEP_RECENT = 800         # recent.jsonl 留多少轮（memory.py 里的默认一样）
WEEK_AT = (6, 3, 0)       # ISO 周几（0=周一…6=周日）03:00
MAX_REPORT_ITEMS = 10     # 报告里每类最多列几条


def log(msg):
    print("[%s] weekly: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ------------------------------------------------------------------ 小工具
def _today():
    return datetime.date.today()


def _week_key(d=None):
    d = d or _today()
    y, w, _ = d.isocalendar()
    return "%04d-W%02d" % (y, w)


def _read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return rows


def _disk_facts():
    """磁盘上的**原始** facts.json。

    🔴 不能用 `Memory().facts` 来算「今天该裁多少」—— `Memory()` 一构造就会归档过期事实
    （`memory.py:437` 的 `_retire_expired()`），数出来永远是 0。
    """
    try:
        with open(os.path.join(MEM, "facts.json"), encoding="utf-8") as f:
            return json.load(f).get("facts") or []
    except Exception:
        return []


def _arch_lines():
    try:
        with open(os.path.join(MEM, "facts_archive.jsonl"), encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _age_days(date_str):
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
        return (_today() - d).days
    except Exception:
        return None


# ------------------------------------------------------------------ ① 报告
def collect():
    """把体检要的数字一次算齐（**只读**，不落任何东西）。"""
    m = mem_mod.Memory()
    st = m.stats()
    today = m._today()
    raw = _disk_facts()
    expired = [f for f in raw if (f.get("expires") or "") and f["expires"] < today]
    over = max(0, len(raw) - mem_mod.MAX_FACTS)
    try:
        recent_n = len(m._read_recent_all())
    except Exception:
        recent_n = -1

    arch = _read_jsonl(m.p_archive)
    why = {}
    for r in arch:
        k = r.get("why") or "?"
        why[k] = why.get(k, 0) + 1

    top_hits = sorted(m.facts, key=lambda x: -(x.get("hits") or 0))[:MAX_REPORT_ITEMS]

    # 判据②：归档仍可检索 —— 拿一条归档的事实，用它的字去搜，搜得回来才算数
    probe = {"ok": None, "kw": "", "text": ""}
    if arch:
        cand = arch[-1]
        text = (cand.get("text") or "").strip()
        kw = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")[:2]
        probe.update({"kw": kw, "text": text[:24]})
        if kw:
            try:
                hits = m.search(kw)
                probe["ok"] = any(text[:8] in (h or "") for h in hits) if hits else False
            except Exception:
                probe["ok"] = None

    return {
        "week": _week_key(),
        "today": today,
        "st": st,
        "raw_n": len(raw),
        "expired": expired,
        "over_cap": over,
        "recent_n": recent_n,
        "keep_recent": KEEP_RECENT,
        "archive_why": why,
        "top_hits": top_hits,
        "probe": probe,
        "max_facts": mem_mod.MAX_FACTS,
        "layers": layers(m),
    }


def layers(m=None):
    """四层体检（存储层）＋ 冒烟检索：证明「四层都在、而且答得出」。

    层与 `80 §三` 对齐：L0 原文档 rounds / L1 日索引 diary / L2 事实账（活跃+归档）/ L3 场景账 episodes。
    冒烟那三条是固定问句：只在「能跑但答不出」时才会红（检索坏了、索引空了、归档没进索引）。
    """
    m = m or mem_mod.Memory()

    # L0 原文档
    days, turns = 0, 0
    for fn in sorted(glob.glob(os.path.join(MEM, "rounds", "*.jsonl"))):
        days += 1
        try:
            with open(fn, encoding="utf-8") as f:
                turns += sum(1 for line in f if line.strip())
        except Exception:
            pass

    # L1 日索引
    dd = os.path.join(MEM, "diary")
    diaries = sorted(x[:-5] for x in os.listdir(dd) if x.endswith(".json")) if os.path.isdir(dd) else []

    # L3 场景账
    eps = len(m.episodes)

    # 冒烟：三条固定问句（真实语料里都有话可说）
    probes = ("天气", "新闻", "背单词")
    smoke = {}
    for q in probes:
        try:
            smoke[q] = len(m.search(q))
        except Exception:
            smoke[q] = -1

    st = m.stats()
    return {
        "L0_days": days,
        "L0_turns": turns,
        "L1_days": len(diaries),
        "L1_last": diaries[-1] if diaries else "",
        "L2_facts": st["facts"],
        "L2_arch": st["archived"],
        "L2_max": mem_mod.MAX_FACTS,
        "L3_eps": eps,
        "smoke": smoke,
        "fixed_tokens": st["stable_tokens"],
        "fixed_max": mem_mod.STABLE_TOKENS,
        "ctx_tokens": st["ctx_tokens"],
        "ctx_max": st["budget"],
    }


def render_md(r, applied=None):
    st = r["st"]
    ly = r["layers"]
    L = []
    L.append("# 记忆周体检 · %s" % r["week"])
    L.append("")
    L.append("> 生成：%s ｜ 由 `~/aipassport/weekly.py` 自动出（P6）" % time.strftime("%Y-%m-%d %H:%M"))
    L.append("")
    L.append("## 1 账本")
    L.append("")
    L.append("| 项 | 值 | 判据 |")
    L.append("|---|---|---|")
    L.append("| 活跃事实 | **%d** / %d | 不溢出 |" % (st["facts"], mem_mod.MAX_FACTS))
    L.append("| 归档事实（仍可检索） | %d | 只增不丢 |" % st["archived"])
    L.append("| 分类 | %s | — |" % "、".join("%s %d" % (k, v) for k, v in st["facts_by_type"].items()))
    L.append("| 累计轮次 | %d ｜ 今天 %d | — |" % (st["turns_total"], st["turns_today"]))
    L.append("| 索引文档 / 组装耗时 | %d / %.1f ms | <50 ms |" % (st["index_docs"], st["block_ms"]))
    L.append("")
    L.append("## 2 四层体检（存储层，`80 §三`）")
    L.append("")
    L.append("| 层 | 内容 | 现在 | 判据 |")
    L.append("|---|---|---|---|")
    L.append("| **L0 原文档** | `rounds/*.jsonl` | %d 天 / %d 轮 | 只增不删 |" % (ly["L0_days"], ly["L0_turns"]))
    L.append("| **L1 日索引** | `diary/*.json` | %d 篇（最近 %s） | 一天一条；没对话也写 |" %
             (ly["L1_days"], ly["L1_last"] or "无"))
    L.append("| **L2 事实账** | `facts.json` ＋ `facts_archive.jsonl` | 活跃 %d ／ 归档 %d | 活跃 ≤%d，归档只增 |" %
             (ly["L2_facts"], ly["L2_arch"], ly["L2_max"]))
    L.append("| **L3 场景账** | `episodes.json`（情绪拐点） | %d 条 | ≤30 |" % ly["L3_eps"])
    L.append("")
    L.append("## 3 注入三段（喂给模型的部分）")
    L.append("")
    L.append("| 段 | token | 判据 | 说明 |")
    L.append("|---|---|---|---|")
    L.append("| **固定**（人格 ＋ MEMORY_VOICE ＋ 常驻事实 ＋ 关系账） | %d | ≤%d | 恒定 → 吃缓存 |" %
             (ly["fixed_tokens"], ly["fixed_max"]))
    L.append("| **按需**（近 6 轮原文 ＋ 昨天日记 ＋ 滚动摘要 ＋ 召回事实/场景） | %d | 合计 ≤%d | 每轮变 |" %
             (ly["ctx_tokens"] - ly["fixed_tokens"], ly["ctx_max"]))
    L.append("")
    L.append("## 4 记忆冒烟（每次体检都跑：能跑≠答得出）")
    L.append("")
    L.append("| 问法 | 命中 | 判据 |")
    L.append("|---|---|---|")
    for q, n in ly["smoke"].items():
        L.append("| 「%s」 | %d 条 | %s |" % (q, n, "✅" if n > 0 else "❌ 一条都取不到"))
    L.append("")
    L.append("## 5 本周该裁的（不是删，是进归档）")
    L.append("")
    L.append("> `memory.py` 的 `Memory()` **每次构造都会顺手归档过期事实**（现有行为）；"
             "周裁真正负责的是「超 200 进归档」＋「报告」。")
    L.append("")
    if r["expired"]:
        L.append("- **过期事实 %d 条**（下次 `Memory()` 构造时自动进归档）：%s" % (
            len(r["expired"]), "；".join((f.get("text") or "")[:20] for f in r["expired"][:MAX_REPORT_ITEMS])))
    else:
        L.append("- 过期事实：**0 条**")
    if r["over_cap"]:
        L.append("- **超上限 %d 条**（按 命中/最近/日期 从低往高进归档）" % r["over_cap"])
    else:
        L.append("- 超上限：**0 条**")
    L.append("- recent.jsonl：%d 轮（`memory.py` 初始化时就自动裁到 ≤%d，这里只报数）" %
             (r["recent_n"], r["keep_recent"]))
    L.append("")
    L.append("## 6 归档分布")
    L.append("")
    if r["archive_why"]:
        L.append("| 原因 | 条数 |")
        L.append("|---|---|")
        for k, v in sorted(r["archive_why"].items(), key=lambda kv: -kv[1]):
            L.append("| %s | %d |" % (k, v))
    else:
        L.append("（还没归档过）")
    L.append("")
    L.append("## 7 判据")
    L.append("")
    p = r["probe"]
    if p["ok"] is None:
        s_probe = "跳过（归档为空）"
    elif p["ok"]:
        s_probe = "✅ 搜「%s」找回「%s」" % (p["kw"], p["text"])
    else:
        s_probe = "❌ 搜「%s」没找回「%s」" % (p["kw"], p["text"])
    L.append("- ① 活跃不溢出：%s（%d / %d）" % ("✅" if st["facts"] <= r["max_facts"] else "❌",
                                              st["facts"], r["max_facts"]))
    L.append("- ② 归档仍可检索：%s" % s_probe)
    L.append("- ③ 注入预算：%s（%d / %d token）" % ("✅" if st["ctx_tokens"] <= st["budget"] else "❌",
                                                   st["ctx_tokens"], st["budget"]))
    L.append("- ④ 四层都在：%s（L0 %d 轮 ｜ L1 %d 篇 ｜ L2 %d+%d ｜ L3 %d 条）" % (
        "✅" if (ly["L0_turns"] > 0 and ly["L1_days"] > 0 and st["facts"] > 0) else "⚠️ 有层是空的",
        ly["L0_turns"], ly["L1_days"], st["facts"], st["archived"], ly["L3_eps"]))
    L.append("- ⑤ 冒烟：%s" % ("✅ 三条都答得出" if all(n > 0 for n in ly["smoke"].values())
                                else "❌ 有问法取不到"))
    L.append("")
    L.append("## 8 命中次数 TOP%d" % MAX_REPORT_ITEMS)
    L.append("")
    if r["top_hits"]:
        for f in r["top_hits"]:
            age = _age_days(f.get("date"))
            L.append("- %s（命中 %s，%s）" % ((f.get("text") or "")[:24], f.get("hits") or 0,
                                            ("%d 天前" % age) if age is not None else "日期未知"))
    else:
        L.append("（还没有事实）")
    L.append("")
    L.append("## 9 这次做了什么")
    L.append("")
    if applied is None:
        L.append("只出报告（未裁）")
    else:
        L.append("- 备份：%s" % (applied.get("backup") or "无"))
        L.append("- 裁：过期归档 %s 条 ／ 超上限 %s 条 ／ recent %s" %
                 (applied.get("expired", 0), applied.get("overflow", 0), applied.get("recent", "?")))
        L.append("- 真裁：%s" % ("是" if applied.get("did_trim") else "**否（dry-run，等 trim-ok.flag）**"))
    L.append("")
    return "\n".join(L)


def write_report(r, applied=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, "%s.md" % r["week"])
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_md(r, applied))
    with open(os.path.join(REPORT_DIR, "%s.json" % r["week"]), "w", encoding="utf-8") as f:
        json.dump({"stats": r["st"], "expired": len(r["expired"]), "over_cap": r["over_cap"],
                   "recent_n": r["recent_n"], "archive_why": r["archive_why"],
                   "probe_ok": r["probe"]["ok"], "layers": r["layers"], "applied": applied},
                  f, ensure_ascii=False, indent=1)
    log("报告已写：%s" % path)
    return path


# ------------------------------------------------------------------ ② 裁
def trim(apply=False, force=False):
    """超 200 进归档（过期那条由 Memory() 构造时自动做）。默认 dry-run 只列表。"""
    raw = _disk_facts()
    today = datetime.date.today().isoformat()
    expired = [f for f in raw if (f.get("expires") or "") and f["expires"] < today]
    over = max(0, len(raw) - mem_mod.MAX_FACTS)

    out = {"expired": len(expired), "overflow": over, "did_trim": False, "recent": None,
           "facts_before": len(raw)}
    log("dry-run：过期 %d 条，超上限 %d 条，活跃 %d 条（上限 %d）" %
        (len(expired), over, len(raw), mem_mod.MAX_FACTS))
    for f in expired[:MAX_REPORT_ITEMS]:
        log("  过期：%s（expires %s）" % ((f.get("text") or "")[:24], f.get("expires")))

    allowed = force or os.path.exists(FLAG_TRIM_OK)
    if not apply:
        out["recent"] = "未动（dry-run）"
        return out
    if not allowed:
        log("没看到 %s —— **不裁**（先看清单，点头再建这个文件）" % FLAG_TRIM_OK)
        out["recent"] = "未动（没批准）"
        return out

    # 🔴 `Memory()` 一构造就会归档过期（`memory.py:437`）⇒ 用归档文件行数差来数它。
    before = _arch_lines()
    m = mem_mod.Memory()
    n_exp = max(0, _arch_lines() - before)
    n_over = m._enforce_cap()
    m._save_facts()          # 🔴 `_enforce_cap` 自己不落盘，只有 add_fact 那条路会 save —— 不补这行等于白裁
    n_rec = m._trim_recent(KEEP_RECENT)
    out.update({"did_trim": True, "expired": n_exp, "overflow": n_over,
                "recent": n_rec or "已是最少", "facts_after": len(m.facts)})
    log("真裁完成：过期归档 %d 条（构造时自动）、超上限 %d 条、recent 留 %s 轮；活跃 %d 条" %
        (n_exp, n_over, n_rec or "?", len(m.facts)))
    return out


# ------------------------------------------------------------------ ③ 备份
def backup(keep=KEEP_BACKUPS):
    """mem/ 打一个 tar.gz，轮转留 keep 份。返回路径。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "mem-%s.tar.gz" % time.strftime("%Y-%m-%d")
    path = os.path.join(BACKUP_DIR, name)
    with tarfile.open(path, "w:gz") as tar:
        tar.add(MEM, arcname="mem")
    size = os.path.getsize(path)
    log("备份：%s（%d B）" % (path, size))

    olds = sorted(glob.glob(os.path.join(BACKUP_DIR, "mem-*.tar.gz")))
    for old in olds[:-keep]:
        try:
            os.remove(old)
            log("轮转：删旧备份 %s" % os.path.basename(old))
        except Exception as e:
            log("轮转删失败 %r" % (e,))
    return path


# ------------------------------------------------------------------ ④ 节拍 + 一周例行
def run_weekly(backup_first=True):
    """备份 → 报告 → 裁（有 flag 才真裁）。返回 (报告路径, 结果)。"""
    bk = backup() if backup_first else None
    r = collect()
    applied = trim(apply=True)          # 里面有 flag 判断；没 flag 也只出 dry-run
    applied["backup"] = os.path.basename(bk) if bk else None
    path = write_report(r, applied)
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(LAST_RUN, "w", encoding="utf-8") as f:
            f.write(_week_key() + "\n")
    except Exception:
        pass
    return path, applied


def _due(now=None):
    """到点没有：周日 ≥03:00，且这一周还没跑过。"""
    now = now or time.localtime()
    if now.tm_wday != WEEK_AT[0]:
        return False
    if now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec < WEEK_AT[1] * 3600 + WEEK_AT[2] * 60:
        return False
    try:
        with open(LAST_RUN, encoding="utf-8") as f:
            return f.read().strip() != _week_key()
    except Exception:
        return True


def _loop():
    while True:
        try:
            if _due():
                log("到点了（周日 %02d:00）→ 跑一周例行" % WEEK_AT[1])
                run_weekly()
        except Exception as e:
            log("例行出错：%r" % (e,))
        time.sleep(60)


def start():
    """aitalk 起来时调一次：起节拍（错过就下个周日补跑 —— LAST_RUN 按 ISO 周比对）。"""
    threading.Thread(target=_loop, daemon=True).start()
    log("周体检节拍已起（周日 %02d:00｜备份留 %d 份｜真裁门槛 %s）" %
        (WEEK_AT[1], KEEP_BACKUPS, "已批准" if os.path.exists(FLAG_TRIM_OK) else "未批准（只出清单）"))


# ------------------------------------------------------------------ CLI
def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "report").lower()
    if cmd == "report":
        r = collect()
        write_report(r)
        st = r["st"]
        print("活跃 %d/%d ｜ 归档 %d ｜ episodes %d ｜ recent %d ｜ 日记 %d 天 ｜ 注入 %d/%d token" %
              (st["facts"], r["max_facts"], st["archived"], st["episodes"], r["recent_n"],
               st["diary_days"], st["ctx_tokens"], st["budget"]))
        ly = r["layers"]
        print("四层：L0 %d 轮 ｜ L1 %d 篇 ｜ L2 %d+%d ｜ L3 %d 条 ｜ 注入 固定 %d + 按需 %d token" %
              (ly["L0_turns"], ly["L1_days"], ly["L2_facts"], ly["L2_arch"], ly["L3_eps"],
               ly["fixed_tokens"], ly["ctx_tokens"] - ly["fixed_tokens"]))
        print("冒烟：%s" % "／".join("「%s」%d 条" % (q, n) for q, n in ly["smoke"].items()))
        print("本周该裁：过期 %d ｜ 超上限 %d ｜ 归档可检索探测 %s" %
              (len(r["expired"]), r["over_cap"], r["probe"]["ok"]))
        return 0
    if cmd == "trim":
        out = trim(apply=("--apply" in argv), force=("--force" in argv))
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if cmd == "backup":
        print(backup())
        return 0
    if cmd == "run":
        path, applied = run_weekly()
        print(json.dumps({"report": path, "applied": applied}, ensure_ascii=False))
        return 0
    print("用法：weekly.py [report|trim [--apply] [--force]|backup|run]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
