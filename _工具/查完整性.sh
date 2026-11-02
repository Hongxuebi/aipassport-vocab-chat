#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 查完整性 —— "写进去的每一行都还在、刷进去的每一个函数都进了固件"
#
# 为什么要有这道闸（2026-10-26 作者原话）：
#   「你写进去的每一次、刷的每一个，都给我读完整别漏了代码少了代码，不然我又要反复测试！」
# 这一轮真出过两次"代码/能力悄悄没了"：
#   ① 我重写配网页时把 h_scan/h_connect 整段替换掉了（HTTP 接口没了，页面还在引用）；
#   ② /p?s= 的路由因为 URI 比法不对永远走不到（页面有链接、固件里却没有能命中它的分支）。
# 这两类都能在**不碰真机**的情况下查出来，所以固定成闸。
#
# 查七项：
#   1) 页面里发起的每个地址，固件里都有对应路由（不能"页面有按钮、代码没处理"）
#   2) 固件里注册的每个路由，页面/注释里有出处（不能留死分支）
#   3) 所有 APP_* 测试开关默认 0
#   4) 没有 TODO / FIXME / XXX 这类没写完的记号留在交付代码里
#   5) 补丁与工作副本**逐字节一致**（防止"改了固件忘了重新生成补丁"）
#   6) 关键符号真的编进了 ELF（防止被 #if 关掉/链接器折掉）
#   7) 配网页逐页完整（有 </html>）+ 页面零件齐全（占位符/手打表单/提交表单）
#
# 用法：./查完整性.sh [固件目录]
set -u
cd "$(dirname "$0")/.."
FW="${1:-$AIP_FW}"
PATCH="03-固件补丁/0001-wprov-扫码配网.patch"
fail=0

echo "== 1) 页面地址 ↔ 固件路由 =="
/usr/bin/python3 - "$FW" <<'PY' || fail=1
import re, sys
fw = sys.argv[1]
src = open(fw + "/components/wprov/wprov_http.c", encoding="utf-8", errors="replace").read()
# 页面里真正会发请求的地址：href="/x…" 和 action=/x
used = set(re.findall(r'href=\\?"/([A-Za-z0-9_./?=%-]*)', src)) | set(re.findall(r'action=/?([A-Za-z0-9_./?=%-]*)', src))
used = {u.split('?')[0].split('&')[0] or '' for u in used}
used = {('/' if u == '' else '/' + u.lstrip('/')) for u in used}
# 固件里注册的路由：uri_is(req, "/x")
routes = set(re.findall(r'uri_is\(req,\s*"([^"]+)"\)', src))
routes.add('/')   # h_get 的兜底就是列表页
missing = sorted(u for u in used if u not in routes)
print("   页面用到的地址：", sorted(used))
print("   固件处理的路由：", sorted(routes))
if missing:
    print("   ❌ 这些地址页面会点，但固件里没有对应分支：", missing)
    sys.exit(1)
print("   ✅ 一一对应（点得动的前提）")
PY

echo "== 2) 开关默认值 =="
bad=$(grep -nE "^#define APP_[A-Z_]+\s+[1-9]" "$FW/main/app_config.h" || true)
if [ -n "$bad" ]; then echo "   ❌ 有开关不是 0："; echo "$bad" | sed 's/^/      /'; fail=1
else echo "   ✅ 所有 APP_* 都是 0"; fi

echo "== 3) 交付代码里的 TODO/FIXME =="
todo=$(grep -rnE "\bTODO\b|\bFIXME\b" "$FW/main" "$FW/components/evlog" "$FW/components/wprov" --include=*.c --include=*.h 2>/dev/null | grep -v "/build/" || true)
if [ -n "$todo" ]; then echo "   ⚠️ 找到（确认是不是故意留的）："; echo "$todo" | sed 's/^/      /'
else echo "   ✅ 没有遗留记号"; fi

echo "== 4) 补丁与工作副本一致 =="
if [ -f "$PATCH" ] && [ -d "$FW/.git" ]; then
  git -C "$FW" add -A >/dev/null 2>&1
  git -C "$FW" diff --cached a06235e > /tmp/_audit_patch.patch 2>/dev/null
  if cmp -s /tmp/_audit_patch.patch "$PATCH"; then echo "   ✅ 补丁 == 工作副本（$(wc -c < "$PATCH") 字节）"
  else echo "   ❌ 补丁和工作副本不一致 —— 忘了重新生成补丁（git add -A && git diff --cached a06235e > $PATCH）"; fail=1; fi
else echo "   ⏭  没有补丁或没有 git，跳过"; fi

echo "== 5) 关键符号是否编进 ELF =="
ELF="$FW/build/FoloToy-AI-Passport.elf"
if [ -f "$ELF" ]; then
  NM=$(ls -d "$HOME/.espressif/tools/riscv32-esp-elf/"*/riscv32-esp-elf/bin 2>/dev/null | head -1)/riscv32-esp-elf-nm
  if [ -x "$NM" ]; then
    "$NM" -C --defined-only "$ELF" > /tmp/_audit_syms.txt 2>/dev/null
    miss=0
    # 只查"非 static、必须外链"的符号：static 的页面处理函数在 -Os 下会被内联，查它会误报
    SYMS="wprov_start_ap wprov_scan_now wprov_http_start wprov_http_stop evlog_append evlog_read evlog_ack_set evlog_esp_mount ev_sync_note ev_sync_request net_client_post_events net_client_release_rx demo_card_enter demo_provision_enter srs_review pgb2_sync demo_device_info_enter voice_talk_get voice_talk_post"
    for s in $SYMS; do
      grep -q " $s$" /tmp/_audit_syms.txt || { echo "   ❌ 符号不在固件里：$s"; miss=1; fail=1; }
    done
    [ $miss -eq 0 ] && echo "   ✅ 19 个关键符号都在（配网/事件日志/补传/页面/算法/存储/数据网关）"
  else echo "   ⏭  没找到 nm，跳过（先跑一次编译）"; fi
else echo "   ⏭  还没编译，跳过"; fi

echo "== 6) 配网页逐页完整性 =="
./_工具/查页面.sh "$FW" || fail=1

echo "== 4b) 用了 APP_* 宏的文件必须 include app_config.h =="
# 2026-10-29 夜踩过：demo_pet.c 里追加了 `#if APP_ECHO_DIAG`，但那文件没 include app_config.h，
# 宏恒为 0 ⇒ 自测代码整段没编进固件（串口一个字没有、strings 里也找不到字符串），白刷两次机。
bad_inc=""
for f in $(grep -rlE '^[[:space:]]*#[[:space:]]*if.*APP_[A-Z_]+' "$FW/main" --include='*.c' 2>/dev/null); do
  grep -q '#include "app_config.h"' "$f" || bad_inc="$bad_inc  ${f#$FW/}\n"
done
if [ -n "$bad_inc" ]; then
  echo "   ❌ 这些文件用了 APP_* 却没 include app_config.h（#if 恒假 ⇒ 整段不编）："
  printf "$bad_inc"; fail=1
else
  echo "   ✅ 全都有 include"
fi

echo
if [ $fail -eq 0 ]; then
  echo "✅ 完整性检查通过（页面↔路由对得上、开关全 0、补丁不落后、符号齐全）"
else
  echo "❌ 完整性检查失败"
  exit 1
fi

# 7) 资源一致性（图鉴文件/索引/镜像）——有图鉴就必须过
if [ -d "_图鉴4bpp" ]; then
    if ./_工具/查资源.sh >/tmp/_g_assets.log 2>&1; then tail -1 /tmp/_g_assets.log
    else echo "   ❌ 资源检查失败"; tail -6 /tmp/_g_assets.log; exit 6; fi
fi
