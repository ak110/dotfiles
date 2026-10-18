# レーン統合の失敗

本書は`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「マージありの統合」で、rebaseが競合で停止した場合と、rebase後の変更範囲の検証が失敗した場合の扱いを定める。

## rebaseの競合

- 停止時: `git -c submodule.recurse=false rebase --abort`も`git -c submodule.recurse=false rebase --continue`も実行せず、rebaseを進行中のまま保って続行できない理由を返す。`続行できない理由:`へ、競合したファイルのリポジトリ相対パス、専用worktreeの絶対パス、そのworktreeでrebaseが進行中であることを書く。
- メインから解消の指示を受領した場合: 競合を解消して解消したパスだけをstageする。`agent-toolkit:commit`の`references/history-rewrite.md`「履歴確認の起動形」の`git log`を単独で実行する。その後、`git -c submodule.recurse=false rebase --continue`で完了させる。競合箇所、解消方針、変更内容、影響範囲を`atk review-table add`でレビュー指摘管理表へ1行登録する。同じ行へ`atk review-table respond`で解消内容を記録する。そのうえで`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」の返却値を返す。
- 統合指示を再び受領した場合: 同書「マージありの統合」の冒頭（専用worktreeのclean状態の確認）から行う。

## 統合後の変更範囲の検証の失敗

- レビュー済みHEADでは再現せず、統合先との組合せだけで生じた失敗: 失敗した検証が確かめる契約の送信側、受信側、実装、テストを列挙して専用worktreeへ是正commitを記録する。変更範囲の検証を再実行して成功と阻害する警告の不在を確かめ、commitと検証の証拠（警告の判定の根拠を含む）を続行できない理由としてメインへ返す。メインの再指示後は同書「マージありの統合」の、統合後にだけ成立する検証の有無を確かめる段落へ進む。
- 是正する失敗がレーンの変更が扱った問題と根本原因を同じくする場合は、是正の範囲と完了を`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「類似見直し」「類似見直しと再発防止の完了」で判定し、認可範囲外の対象は次項のとおり返す。
- レビュー済みHEADでも再現する失敗と、認可範囲外の変更を要する失敗: コマンド、出力、続行できない事情を`続行できない理由:`へ書いて返す。
