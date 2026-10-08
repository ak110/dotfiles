# レーン統合の失敗

本書は`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「マージありの統合」で、rebaseが競合で停止した場合と、rebase後の変更範囲の検証が失敗した場合の扱いを定める。

## rebaseの競合

- 停止時: `git rebase --abort`と`git rebase --continue`のいずれも自ら実行せず、rebaseを進行中のまま保持して続行できない理由を返す。`続行できない理由:`へ、競合したファイルのリポジトリ相対パス、専用worktreeの絶対パス、およびそのworktreeでrebaseが進行中であることを書く。
- メインから競合の解消指示を受領した場合: 競合を解消して解消したパスだけをstageする。続けて`agent-toolkit:commit`の`references/history-rewrite.md`「履歴確認の起動形」の`git log`を単独で実行し、`git rebase --continue`でrebaseを完了させる。競合箇所、解消方針、変更内容、影響範囲を`atk review-table add`で実行レビューのレビュー指摘管理表へ1行登録し、同じ行へ`atk review-table respond`で解消内容を応答として記録する。その後、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」が定める返却値を返す。
- 統合指示を再び受領した場合: 同書「マージありの統合」の、専用worktreeのclean状態を確認する冒頭から実施する。

## 統合後の変更範囲の検証の失敗

- 失敗がレビュー済みHEADでは再現せず、統合先との組合せだけで生じた場合: 失敗した検証が確かめる契約の送信側、受信側、実装およびテストを列挙したうえで、専用worktreeへ是正commitを記録する。変更範囲の検証を再実行して成功と阻害に当たる警告が無いことを確認し、commitと検証の証拠（警告の判定の根拠を含む）を続行できない理由としてメインへ返す。メインの差分確認と再指示後に同書「マージありの統合」の、プロジェクト規範に統合後にだけ成立する検証があるかを確認する段落へ進む。
- レビュー済みHEADでも再現する失敗と、認可範囲外の変更を要する失敗: コマンド、出力および続行できない事情を`続行できない理由:`へ書いて返す。
