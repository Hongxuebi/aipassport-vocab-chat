#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 生成设备访问令牌并写入固件源（main/secrets.h）—— 作者 2026-10-26 定：令牌烧进固件
#
# 规矩：
#   ① `main/secrets.h` **不进 git、不进补丁**（用 .git/info/exclude 挡），否则复制仓库就泄了
#   ② 换令牌 = 重刷固件（这是"烧进去"的代价，已认）
#   ③ 令牌同时要贴到服务端机的服务配置里，两边一致
#
# 用法：
#   ./生成设备令牌.sh            # 没有就生成新的；已有就打印（不覆盖）
#   ./生成设备令牌.sh --force    # 强制重新生成（会作废旧令牌 → 必须重刷所有设备）
set -e
cd "$(dirname "$0")/.."
FW=$AIP_FW
HDR="$FW/main/secrets.h"
EXCL="$FW/.git/info/exclude"

# 挡在补丁之外（这份 exclude 不跟踪，也不会被 git add -A 带进补丁之外的路径）
# 注意：worktree / tarball / 直接下载的源码里 .git 可能不存在或是个文件 —— 那种情况跳过，不影响生成
if [ -d "$FW/.git" ]; then
  mkdir -p "$(dirname "$EXCL")"
  grep -qxF "main/secrets.h" "$EXCL" 2>/dev/null || echo "main/secrets.h" >> "$EXCL"
else
  echo "（$FW 不是 git 仓库根：跳过 .git/info/exclude；令牌文件请自己别提交）"
fi

if [ -f "$HDR" ] && [ "${1:-}" != "--force" ]; then
  echo "已有令牌头文件（不覆盖）：$HDR"
  echo "  令牌前 8 位：$(grep -oE 'AIP_DEV_TOKEN +"[0-9a-f]+' "$HDR" | grep -oE '[0-9a-f]{8}$')…"
  echo "  （要换：$0 --force，然后重刷固件 + 同步改服务端机配置）"
  exit 0
fi

TOKEN=$(openssl rand -hex 32)
cat > "$HDR" <<HDR_EOF
// main/secrets.h —— 本机构建专用，**不进 git、不进补丁**
// 由 _工具/生成设备令牌.sh 生成；换令牌必须重刷固件，并同步服务端机配置。
#pragma once
#define AIP_DEV_TOKEN "$TOKEN"
HDR_EOF
chmod 600 "$HDR"
echo "✅ 已生成令牌（600 权限，不进 git）：$HDR"
echo
echo "设备令牌（贴到服务端配置 /config 里，两边必须一致）："
echo "  $TOKEN"
echo
echo "提醒：这是**设备门禁卡**，不是大模型 API Key。"
