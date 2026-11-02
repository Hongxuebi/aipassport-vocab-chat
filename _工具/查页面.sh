#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 从固件里把配网页抠出来做体检。两个坑都真踩过，手机上表现都是"点了没反应"，串口上看不出：
#   2026-10-25：JS 语法错 → 页面永远停在"正在扫描…"
#   2026-10-26：页面被截断 → 列表出来了，但「选它」怎么点都不动
# 检查三件事：
#   ① 每页都能在 4KB 内找到 </html>（截断就报错）；列表页的头段例外，里面带 @@ROWS@@ 占位符
#   ② 列表页拼装需要的东西都在：占位符、手打名字表单（action=/p）
#   ③ 有 JS 的话必须能过 node --check
# 用法：./查页面.sh [工程目录]
set -e
PROJ="${1:-$AIP_FW}"
BIN=$(ls "$PROJ"/build/*.bin 2>/dev/null | head -1)
[ -n "$BIN" ] || { echo "❌ 没找到 build/*.bin"; exit 8; }
/usr/bin/python3 - "$BIN" <<'PY'
import sys
b = open(sys.argv[1], 'rb').read()
LIMIT = 4096

pages, heads, k = [], [], 0
pos = 0
while True:
    s = b.find(b'<!doctype html>', pos)
    if s < 0:
        break
    e = b.find(b'</html>', s, s + LIMIT)
    k += 1
    if e < 0:
        seg = b[s:s + LIMIT]
        if b'@@ROWS@@' in seg:           # 列表页头段：列表行由设备端插进来，本段没有 </html> 是对的
            heads.append(seg)
            print("第%d段: 列表页头段（%d 字节内，带 @@ROWS@@ 占位符）" % (k, LIMIT))
            pos = s + LIMIT
            continue
        print("❌ 第%d页在 %dB 内没有 </html>，HTML 可能被截断" % (k, LIMIT))
        sys.exit(9)
    pages.append(b[s:e + 7])
    print("第%d页: %d 字节 | %s | 占位符%s" % (
        k, e + 7 - s,
        '有 JS' if b'<script>' in b[s:e + 7] else '无 JS（表单式）',
        '有' if b'@@' in b[s:e + 7] else '无'))
    pos = e + 7

if not pages and not heads:
    print("（固件里没有配网页，跳过）")
    sys.exit(0)

# 列表页要能拼起来：占位符 + 手打名字的表单 + 连 WiFi 的提交口
# 组装一遍"设备真正会发出去的页面"（头 + 一行样例 + 尾巴），检查后半段有没有丢
tpl = b[i0:b.find(b'\x00', i0)] if False else None
need = {b'@@ROWS@@': '列表占位符', b'action=/c': '提交连接表单',
        b'name=p type=password': '密码框', b'name=s': 'WiFi 列表（select）'}
miss = [name for tok, name in need.items() if tok not in b]
if miss:
    print("❌ 配网页缺零件：" + "、".join(miss))
    sys.exit(9)
print("✅ 列表页零件齐（占位符 / 手打表单 / 提交表单）")

alljs = []
for p in pages:
    h = p.decode('utf-8', 'replace')
    if '<script>' in h:
        alljs.append(h.split('<script>', 1)[1].split('</script>', 1)[0])
# ── 组装检查（2026-10-26 12:5x 真机事故）：页面是"头 + 若干行 + 尾巴"分三段发的，
#    光看字符串在不在没用 —— 那次尾巴根本没发出去，手机上只看到下拉框、没有密码框。
if b'@@ROWS@@' in b:
    m0 = b.find(b'@@ROWS@@')
    i0 = b.rfind(b'<!doctype html>', 0, m0)
    j0 = b.find(b'\x00', m0)
    tpl = b[i0:j0].decode('utf-8', 'replace')
    head, tail = tpl.split('@@ROWS@@', 1)
    asm = head + '<option value="X">X 1dBm</option>' + tail
    miss = [x for x in ('type=password', '</select>', '</form>', '</html>') if x not in asm]
    if miss:
        print("❌ 组装后的页面缺零件（手机上会看不到/点不到）：" + "、".join(miss))
        sys.exit(9)
    print("✅ 组装后整页 %d 字节（下拉框/密码框/提交/闭口 都在）" % len(asm.encode()))

if not alljs:
    print("✅ 这一版页面不需要 JS（链接/表单式）")
    sys.exit(0)
open('/tmp/_page_check.js', 'w').write("\n;\n".join(alljs))
print("JS: %d 字节" % sum(len(x) for x in alljs))
PY
[ -f /tmp/_page_check.js ] || exit 0
/usr/local/bin/node --check /tmp/_page_check.js && echo "✅ 页面 JS 语法 OK" || { echo "❌ 页面 JS 语法错（手机端会静默卡住）"; exit 9; }
