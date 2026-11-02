#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 体量检查：我们自己的文件 ≤300 行；函数 ≤60 行（用 ctags 不可靠，先只查文件+最长函数估算）
set -e
FW="${1:-$AIP_FW}"
bad=0
EXEMPT="$PWD/_工具/第三方清单.txt"
BASE="$PWD/_工具/体量基线.txt"
is_exempt() { [ -f "$EXEMPT" ] && grep -qxF "${1#$FW/}" "$EXEMPT"; }
is_baseline() { [ -f "$BASE" ] && grep -qxF "${1#$FW/}" "$BASE"; }
echo "== 文件行数（红线 300）=="
for f in $(find "$FW/components/srs" "$FW/components/wprov" "$FW/main/app" "$FW/main/ui" \
                "$FW/main/demo_provision.c" "$FW/main/demo_zh.c" -name '*.c' 2>/dev/null); do
  is_exempt "$f" && continue
  n=$(wc -l < "$f" | tr -d ' ')
  if [ "$n" -gt 300 ]; then
    if is_baseline "$f"; then echo "  ⚠️  $n 行（已知债，见技术债台账）: ${f#$FW/}"
    else echo "  ❌ $n 行（新超标！）: ${f#$FW/}"; bad=1; fi
  fi
done
if [ $bad -eq 0 ]; then
  if grep -qvE '^\s*$|^#' "$BASE" 2>/dev/null; then echo "  ⚠️  新文件全部达标（存量债见上）"
  else echo "  ✅ 全部 ≤300 行"; fi
fi

echo "== 最长函数（红线 60 行，粗算：从行首 '{' 到匹配 '}' 的跨度）=="
/usr/bin/python3 - "$FW" "$EXEMPT" <<'PY'
import sys, re, os
fw=sys.argv[1]; exempt_path=sys.argv[2]
paths=[]
for root in ("components/srs","components/wprov","main/app","main/ui"):
    for dp,_,fs in os.walk(os.path.join(fw,root)):
        paths += [os.path.join(dp,f) for f in fs if f.endswith(".c")]
for extra in ("main/demo_provision.c","main/demo_zh.c"):
    p=os.path.join(fw,extra)
    if os.path.exists(p): paths.append(p)
worst=[]
EXEMPT=set()
if os.path.exists(exempt_path):
    for l in open(exempt_path,encoding="utf-8"):
        l=l.strip()
        if l and not l.startswith("#"): EXEMPT.add(l)
for p in paths:
    if os.path.relpath(p,fw) in EXEMPT: continue
    lines=open(p,encoding='utf-8',errors='ignore').read().splitlines()
    depth=0; start=None; name=""
    for i,l in enumerate(lines,1):
        if depth==0 and re.search(r'\)\s*\{\s*$', l) and not l.strip().startswith(('if','for','while','switch')):
            start=i; name=l.strip()[:50]; depth=1; continue
        if start:
            depth += l.count('{') - l.count('}')
            if depth<=0:
                worst.append((i-start+1, os.path.relpath(p,fw), name)); start=None; depth=0
worst.sort(reverse=True)
for n,p,name in worst[:5]:
    mark = "❌" if n>60 else "✅"
    print(f"  {mark} {n:3d} 行  {p}  ::  {name}")
PY
