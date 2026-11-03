#!/usr/bin/env bash
set -euo pipefail
for required in "$HOME/.bashrc" "$HOME/.vimrc" "$HOME/.claude/rules/agent-toolkit/01-agent.md" "$HOME/.ssh/config"; do
    if [ ! -f "$required" ]; then
        printf '::error::必須ファイルがありません: %s。install.shとchezmoi適用のログで原因を確認し、修正後にRe-run failed jobsで再実行してください。\n' "$required"
        exit 1
    fi
done
# shellcheck disable=SC2016 # .bashrc内に格納される$HOMEを展開せず文字列リテラルとして検索する
grep -qF '$HOME/dotfiles/bin' ~/.bashrc || {
    echo '::error::.bashrcに$HOME/dotfiles/binのPATH設定がありません。配布元dot_bashrcとchezmoi適用ログを確認し、修正後にRe-run failed jobsで再実行してください。'
    exit 1
}
