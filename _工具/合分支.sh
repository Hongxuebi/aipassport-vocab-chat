#!/bin/bash
# 合分支.sh —— 把一条平行工作树合回主线，并清掉工作树
#
# 顺序（每一步都会挡）：
#   1 分支提交干净  2 主线干净  3 试合一次看冲突（有冲突自动 abort，主线一点不动）
#   4 真合（--no-ff，留合并点）  5 删工作树
#
# 用法：
#   ./_工具/合分支.sh 跟读                 # 合 feat/跟读
#   ./_工具/合分支.sh 语言跨重启 p2/lang    # 自定义分支名
#
# 冲突怎么办：见 89-并行开发约定 §五。五张账本页冲突 ⇒ 留主线那份，把分支新内容手工抄回。
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
BASE=main

TASK="${1:-}"
BRANCH="${2:-feat/$TASK}"
[ -n "$TASK" ] || { echo "用法：./_工具/合分支.sh <任务名> [分支名]"; exit 2; }

WT="../aipassport-$TASK"
[ -d "$WT" ] || { echo "❌ 没有工作树：$WT（先 ./_工具/开分支.sh $TASK）"; exit 1; }
git show-ref --verify --quiet "refs/heads/$BRANCH" || { echo "❌ 分支不存在：$BRANCH"; exit 1; }

# 1. 分支必须提交干净
if [ -n "$(git -C "$WT" status --porcelain)" ]; then
  echo "❌ 分支还有未提交改动 —— 先在工作树里 commit："; git -C "$WT" status --short; exit 1
fi

# 2. 主线必须干净
if [ -n "$(git status --porcelain)" ]; then
  echo "❌ 主线不干净 —— 先 commit/stash："; git status --short; exit 1
fi
CUR=$(git rev-parse --abbrev-ref HEAD)
[ "$CUR" = "$BASE" ] || { echo "❌ 当前不在 $BASE（在 $CUR），先 git checkout $BASE"; exit 1; }

# 3. 空分支：没东西可合，直接清掉
COMMITS=$(git log --oneline "$BASE..$BRANCH")
if [ -z "$COMMITS" ]; then
  echo "ℹ️ $BRANCH 没有新提交 —— 不合，直接清理"
  git worktree remove "$WT" --force && echo "🧹 工作树已删：$WT"
  git branch -d "$BRANCH" >/dev/null 2>&1 && echo "🧹 分支已删：$BRANCH"
  exit 0
fi
echo "待合入的提交（$BASE..$BRANCH）："
echo "$COMMITS" | sed 's/^/   /'

# 3. 试合：有冲突就回滚，主线一个字节都不动
if ! git merge --no-commit --no-ff "$BRANCH" >/dev/null 2>&1; then
  CONFLICT=$(git diff --name-only --diff-filter=U)
  git merge --abort 2>/dev/null
  echo "⚠️ 有冲突（主线已回滚，什么都没变）："
  echo "$CONFLICT" | sed 's/^/   /'
  echo
  echo "处理办法："
  echo "   cd $WT && git rebase $BASE      # 在分支上解，解完再跑一次本脚本"
  echo "  五张账本页（20-续接卡/进度表/版本与标签/能力表.csv/CHANGELOG）冲突 ⇒ 留主线那份，"
  echo "  把分支里的新内容手工抄回主线（89 §五）。"
  exit 2
fi

# 4. 提交合并
MSG="合并 $BRANCH：$(git log --oneline -1 "$BRANCH" | sed 's/^[0-9a-f]* //')"
git commit -m "$MSG" >/dev/null || { echo "❌ merge commit 失败"; exit 3; }
echo "✅ 已合入 $BASE：$(git log --oneline -1)"

# 5. 清工作树
git worktree remove "$WT" --force && echo "🧹 工作树已删：$WT"
echo
echo "主线收尾（必做）："
echo "  1. 把分支里的进展并进 20-续接卡 §0.xx / 进度表.md／_工具/能力表.csv / CHANGELOG.md"
echo "  2. python3 _工具/查口径.py && python3 _工具/进度表.py --check"
echo "  3. python3 _工具/查开工.py"
