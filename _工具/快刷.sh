#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 快刷：**只刷 app 段（0x10000）**，用于"只改了代码"的迭代
#   编译并刷.sh 会刷 4 段（bootloader/分区表/otadata/app）≈ 40–70 秒；
#   这个只刷 app 一段 ≈ 12–20 秒（1.7MB ÷ 16KB/块，逐块校验）。
# ⚠️ 改过分区表 / bootloader / otadata / assets / storage 时**不要**用它：
#   那种情况照 old 流程走 `_工具/编译并刷.sh`（或按段单独刷）。
set -u
cd "$(dirname "$0")/.."
BIN=$AIP_FW/build/FoloToy-AI-Passport.bin
[ -f "$BIN" ] || { echo "❌ 还没编译：先 ./_工具/编译并刷.sh 0 --no-flash"; exit 8; }
SZ=$(stat -f%z "$BIN")
[ "$SZ" -lt 2359296 ] || { echo "❌ app $SZ B 超过 2.25MB 槽，先看分区/裁剪"; exit 7; }
echo "== 快刷 app：$SZ 字节 → 0x10000 =="
# 2026-10-29 修：app 偏移是 **0x10000**（partitions.csv 的 factory），原来写的 0x20000 是旧分区表的位置，
# 用它快刷会把 app 写到 bootloader 后面的空档里 —— 表现就是"刷完还是旧版本"。
exec ./_工具/刷固件-分块.sh "0x10000:$BIN" 
