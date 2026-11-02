#!/bin/bash
# 开分支.sh —— 一条命令开"平行开发工作树"（git worktree），给第二个会话用
#
# 为什么要有它（2026-10-31）：作者要像公司那样，一个会话干主线、另一个会话干别的，收工再合。
# 手敲 worktree 有三个坑：① 主线脏的时候开出来是 HEAD，脏改动白丢 ② 忘了把 660 MB 的
# _参考仓库 软链过来，分支里编不了固件 ③ 开完不知道新会话该填哪个目录。全收进来。
#
# 用法：
#   ./_工具/开分支.sh 跟读                 # → ../aipassport-跟读 ，分支 feat/跟读
#   ./_工具/开分支.sh 语言跨重启 p2/lang    # 自定义分支名
#
# 前置：主线工作区干净（脚本会挡）；目录 ../aipassport-<任务> 不存在
# 纪律：见 89-并行开发约定-2026-10-31.md（真机排他 / 五张账本页只在主线改 / 分支不发版）
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
BASE=main

TASK="${1:-}"
BRANCH="${2:-feat/$TASK}"
[ -n "$TASK" ] || { echo "用法：./_工具/开分支.sh <任务名> [分支名]"; exit 2; }

# 1. 主线必须干净：worktree 只从 HEAD 检出，脏改动不会跟过去
if [ -n "$(git status --porcelain)" ]; then
  echo "❌ 主线有未提交改动 —— 先收干净再开（工作树只从 HEAD 检出，这些改动不会跟过去）："
  git status --short
  exit 1
fi

WT="../aipassport-$TASK"
if [ -e "$WT" ]; then echo "❌ 目录已存在：$WT"; exit 1; fi
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then echo "❌ 分支已存在：$BRANCH"; exit 3; fi

git worktree add "$WT" -b "$BRANCH" "$BASE" || { echo "❌ worktree 建失败（上面是真因）"; exit 4; }

# 2. _参考仓库 不在 git 里（660 MB），软链给分支用；别在分支里改上游，改走 03-固件补丁/
if [ -d "$ROOT/_参考仓库" ] && [ ! -e "$WT/_参考仓库" ]; then
  ln -s "$ROOT/_参考仓库" "$WT/_参考仓库" && echo "🔗 _参考仓库 → 软链到主线（编译可用；改上游仍走 03-固件补丁/）"
fi

echo "✅ 工作树开好：$(cd "$WT" && pwd)"
echo "   分支：$BRANCH   基线：$BASE@$(git rev-parse --short "$BASE")"
echo
echo "👉 新会话的「工作目录」填这一行："
echo "   $(cd "$WT" && pwd)"
echo
echo "  分支会话开工先读：20-续接卡 §0 → 88-后续开发计划 → 89-并行开发约定"
echo
echo "四条纪律："
echo "  1. 真机/串口同一时刻只许一条分支；刷机与发版只在主线做"
echo "  2. 20-续接卡 / 进度表.md / 版本与标签.md / _工具/能力表.csv / CHANGELOG.md 只在主线改；"
echo "     分支的进展写本目录 _分支纪要.md，合并时主线统一收"
echo "  3. 分支不发版（标签全局唯一）"
echo "  4. 收工：git add -A && git commit -m \"...\"  →  回主线 ./_工具/合分支.sh $TASK"
