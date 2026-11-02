#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 交付闸 v2
#   默认：GATE-LITE（1..7 项，不需要设备）—— 只够"可以刷给自己试"，不作为发布依据
#   --device：额外跑真机烟测 → GATE-FULL（可打发布标签）
#   --strict：设备不在位直接失败（给"冻结期/发布前"用）
# 每次运行都会追加一条记录到 _工具/gate记录.md
set -e
cd "$(dirname "$0")/.."
MODE=lite; [ "${1:-}" = "--device" ] && MODE=full; [ "${1:-}" = "--strict" ] && MODE=strict
FW=$AIP_FW
PATCH="$(pwd)/03-固件补丁/0001-wprov-扫码配网.patch"
STAMP=$(date '+%Y-%m-%d %H:%M:%S')
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "?")

r_js=?; r_patch=?; r_host=?; r_layer=?; r_size=?; r_ui=?; r_integ=?; r_dev=SKIP
fail=0

echo "== 1) 页面 JS 语法 =="
if ./_工具/查页面.sh >/tmp/_g_js.log 2>&1; then r_js=PASS; tail -1 /tmp/_g_js.log
else r_js=FAIL; fail=1; tail -3 /tmp/_g_js.log; fi

echo "== 2) 补丁可应用到上游基线 =="
if git -C "$FW" worktree add -q /tmp/_gate_bt a06235e 2>/dev/null || true; then :; fi
if git -C /tmp/_gate_bt apply --check "$PATCH" 2>/dev/null; then r_patch=PASS; echo "  ✅ 可应用"
else r_patch=FAIL; fail=1; echo "  ❌ 无法应用（上游变了，先修补丁）"; fi
git -C "$FW" worktree remove --force /tmp/_gate_bt 2>/dev/null || true

echo "== 3) domain host 单测 =="
# 跑**所有**带 host 测试的组件（新增组件只要放 test/host_test.c 就自动进来，别再一条条手加）
HOST_OK=1
for D in "$FW"/components/*/; do
  D="${D%/}"
  C="$(basename "$D")"
  [ -f "$D/test/host_test.c" ] || continue
  if cc -O2 -Wall -Wextra -I"$D/include" "$D/test/host_test.c" $(ls "$D"/*.c 2>/dev/null) -lm -o "/tmp/_${C}_t" 2>/tmp/_g_host_cc.log; then
    if "/tmp/_${C}_t" >"/tmp/_g_host_$C.log" 2>&1; then echo "  [$C] $(tail -1 /tmp/_g_host_$C.log)"
    else HOST_OK=0; echo "  ❌ [$C] 单测失败"; tail -6 "/tmp/_g_host_$C.log"; fi
  else
    HOST_OK=0; echo "  ❌ [$C] 编译失败"; head -6 /tmp/_g_host_cc.log
  fi
done
if [ $HOST_OK -eq 1 ]; then r_host=PASS; else r_host=FAIL; fail=1; fi

echo "== 4) 分层检查 =="
if ./_工具/查分层.sh >/tmp/_g_layer.log 2>&1; then r_layer=PASS; tail -1 /tmp/_g_layer.log
else r_layer=FAIL; fail=1; tail -5 /tmp/_g_layer.log; fi

echo "== 5) 体量棘轮 =="
if ./_工具/查行数.sh >/tmp/_g_size.log 2>&1; then r_size=PASS; tail -1 /tmp/_g_size.log
else r_size=FAIL; fail=1; tail -5 /tmp/_g_size.log; fi

echo "== 6) 界面静态规则（中文×字体 / 按键回调增删对象 / 标签尺寸）=="
if ./_工具/查界面.sh >/tmp/_g_ui.log 2>&1; then r_ui=PASS; tail -1 /tmp/_g_ui.log
else r_ui=FAIL; fail=1; tail -6 /tmp/_g_ui.log; fi

echo "== 7) 完整性（页面↔路由 / 开关 / 补丁不落后 / 符号在固件里）=="
if ./_工具/查完整性.sh >/tmp/_g_integ.log 2>&1; then r_integ=PASS; tail -1 /tmp/_g_integ.log
else r_integ=FAIL; fail=1; echo "  ❌ 完整性检查失败"; tail -8 /tmp/_g_integ.log; fi

echo "== 8) 真机烟测 =="
if [ "$MODE" = lite ]; then
  r_dev=SKIPPED; echo "  ⏭  跳过（未加 --device）；这版**不能**当发布依据"
else
  if ./_工具/烟测.sh >/tmp/_g_dev.log 2>&1; then r_dev=PASS; tail -1 /tmp/_g_dev.log
  else
    r_dev=FAIL
    if grep -q "没找到串口" /tmp/_g_dev.log && [ "$MODE" = full ]; then
      r_dev=SKIPPED; echo "  ⏭  设备不在位（--device 下算跳过）"
    else fail=1; echo "  ❌ 烟测失败"; tail -8 /tmp/_g_dev.log; fi
  fi
fi

if [ "$MODE" = strict ] && [ "$r_dev" != PASS ]; then fail=1; echo "  ❌ --strict 要求烟测 PASS"; fi

if [ $fail -eq 0 ]; then
  [ "$r_dev" = PASS ] && VERDICT="GATE-FULL 通过（可打发布标签）" || VERDICT="GATE-LITE 通过（DEVICE=SKIPPED，不可作发布依据）"
else VERDICT="GATE 失败"; fi

echo
echo "=================== 裁决 ==================="
printf "  JS=%s  PATCH=%s  HOST=%s  LAYER=%s  SIZE=%s  UI=%s  INTEG=%s  DEVICE=%s\n" \
       "$r_js" "$r_patch" "$r_host" "$r_layer" "$r_size" "$r_ui" "$r_integ" "$r_dev"
echo "  → $VERDICT"
echo "============================================"

# 记录（可追溯：时间/提交/每项结果）
RECORD="_工具/gate记录.md"
[ -f "$RECORD" ] || echo -e "# GATE 运行记录\n\n| 时间 | 提交 | JS | 补丁 | host | 分层 | 体量 | 完整性 | 真机 | 裁决 |\n|---|---|---|---|---|---|---|---|---|---|" > "$RECORD"
echo "| $STAMP | $COMMIT | $r_js | $r_patch | $r_host | $r_layer | $r_size | $r_integ | $r_dev | $VERDICT |" >> "$RECORD"

[ $fail -eq 0 ] || exit 1
