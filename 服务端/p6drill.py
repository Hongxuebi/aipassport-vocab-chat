#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""p6drill.py —— P6 周裁演练：**在 /tmp 的副本上**跑一遍「过期 + 超上限」，
证明 `80` 的那条判据「归档仍可检索（不是删）」真的成立。

真数据一个字不碰：`AITALK_MEM_DIR` 指向临时副本，演练完删掉。
用法（在服务端机上）：python3 p6drill.py
退出码：0 = 两条判据都过；1 = 有一条没过。
"""

import json
import os
import shutil
import sys
import tempfile

SRC = os.path.expanduser("~/aipassport/mem")
HOME_DIR = os.path.expanduser("~/aipassport")


def main():
    tmp = tempfile.mkdtemp(prefix="p6drill-")
    dst = os.path.join(tmp, "mem")
    shutil.copytree(SRC, dst)
    os.environ["AITALK_MEM_DIR"] = dst
    sys.path.insert(0, HOME_DIR)

    try:
        import memory        # noqa: E402  （必须在上面的环境变量之后）
    except ImportError:
        raise SystemExit("这个脚本依赖记忆模块 memory.py，它不随本仓库发布。")
    import weekly            # noqa: E402

    p = os.path.join(dst, "facts.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    facts = d.get("facts") or []
    n0 = len(facts)
    print("· 副本起点：活跃 %d 条（上限 %d）" % (n0, memory.MAX_FACTS))

    def mk(fid, text, date, expires, hits, kind):
        return {"id": fid, "date": date, "text": text, "scene": text, "kw": [],
                "type": kind, "expires": expires, "hits": hits, "last": date, "src": "drill"}

    for i in range(3):                      # 3 条过期
        facts.append(mk("x%03d" % i, "演练过期事实%d号老槐树" % i, "2026-10-01", "2026-10-05", 0, "event"))
    need = memory.MAX_FACTS + 15 - len(facts)   # 再塞到「超上限 15 条」
    for i in range(max(need, 0)):                # 超上限的：hits/date 都低 → 会被先挑走
        facts.append(mk("o%03d" % i, "演练溢出事实%d号旧棉花糖" % i, "2026-10-02", "", 1, "event"))

    with open(p, "w", encoding="utf-8") as f:
        json.dump({"facts": facts}, f, ensure_ascii=False, indent=1)
    print("· 造好：活跃 %d 条（其中 3 条已过期）" % len(facts))

    out = weekly.trim(apply=True, force=True)
    print("· 裁的结果：过期 %s ／ 超上限 %s ／ recent %s" %
          (out.get("expired"), out.get("overflow"), out.get("recent")))

    m = memory.Memory()
    ok = []
    ok.append(("活跃不溢出", len(m.facts) <= memory.MAX_FACTS, "%d / %d" % (len(m.facts), memory.MAX_FACTS)))
    ok.append(("归档有东西", len(m.arch) > 0, "%d 条" % len(m.arch)))

    for kw, what in (("老槐树", "过期那条"), ("旧棉花糖", "溢出那条")):
        hits = m.search(kw)
        found = any(kw in (h or "") for h in hits)
        ok.append(("归档仍可检索（%s）" % what, found, "搜「%s」→ %d 条" % (kw, len(hits))))

    bad = 0
    for name, passed, detail in ok:
        print("· %s %s（%s）" % ("✅" if passed else "❌", name, detail))
        if not passed:
            bad += 1

    rc = 0 if bad == 0 else 1
    print("→ %s（临时目录 %s）" % ("P6 演练通过" if rc == 0 else "P6 演练有 %d 条没过" % bad, tmp))
    if rc == 0:
        shutil.rmtree(tmp, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
