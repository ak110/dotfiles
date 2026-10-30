# レーン統合の失敗

本書は、`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「マージありの統合」において、rebaseが競合によって中断した場合、およびrebase後の変更範囲の検証が失敗した場合の対処手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文の参照基準を適用する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/lane-integration.subagent.md#「マージありの統合」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「マージありの統合」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/history-rewrite.md#「WI実装commitの対応の継承」` | 判断 | `section:agent-toolkit:*/skills/commit/references/history-rewrite.md#「WI実装commitの対応の継承」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/history-rewrite.md#「履歴確認の起動形」` | 判断 | `section:agent-toolkit:*/skills/commit/references/history-rewrite.md#「履歴確認の起動形」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.subagent.md#「レビュー修正の履歴統合」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「レビュー修正の履歴統合」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md#「類似見直し」「類似見直しと再発防止の完了」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」「類似見直しと再発防止の完了」` |

## rebaseの競合

競合解消の指示を伝達する際は、rebase開始前に記録した旧系列および統合先のbaseとheadの各OIDを引き継ぐ。競合を解消してrebaseを完了した後は、それらのOIDを指定して`history-compare --operation rebase`を実行し、内容を変更した旧コミットに対して`--resolved-conflict`を繰り返し指定する。
検収済みの対応表を生成した上で、後述の返却値とともに委譲元へ渡す。対応表の生成と引き継ぎは、`agent-toolkit:commit`の`references/history-rewrite.md`「WI実装commitの対応の継承」に従う。

- 停止時の対応: `git -c submodule.recurse=false rebase --abort`や`--continue`は実行せず、rebaseが進行中の状態を保ったまま、続行できない理由を委譲元へ返す。`続行できない理由:`には、競合が発生したファイルのリポジトリ相対パス、専用worktreeの絶対パス、および該当worktreeでrebaseが進行中であることを記載する。
- メインから解消指示を受領した場合: 競合を解消し、解消したパスのみをステージングする。`agent-toolkit:commit`の`references/history-rewrite.md`「履歴確認の起動形」に記載された`git log`を単独で実行する。続いて`git -c submodule.recurse=false rebase --continue`を実行してrebaseを完了させる。競合箇所、解消方針、変更内容、影響範囲を`atk review-table add`でレビュー指摘管理表に1行追加し、同じ行に対して`atk review-table respond`で解消内容を記録する。その上で、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」に定められた返却値を返す。
- 統合指示を再受領した場合: 同書「マージありの統合」の冒頭手順（専用worktreeがクリーンな状態であることの確認）から再開する。

## 統合後の変更範囲の検証の失敗

- レビュー済みHEADでは再現せず、統合先との組み合わせによってのみ発生した失敗: 失敗した検証が確認対象とする契約の送信側、受信側、実装、テストを整理し、専用worktreeに是正コミットを記録する。変更範囲の検証を再実行して、成功することおよび処理を妨げる警告が存在しないことを確認した上で、コミットと検証の証拠（警告の判定根拠を含む）を「続行できない理由」としてメインエージェントへ返す。メインエージェントから再指示を受けた後は、同書「マージありの統合」における「統合後にのみ成立する検証の有無を確認する手順」へ進む。
- 是正対象の失敗がレーンの変更内容と同一の根本原因を持つ場合: 是正の範囲および完了判定は、`agent-basics:bugfix`の`references/root-cause-remediation.md`「類似見直し」「類似見直しと再発防止の完了」に従って行う。認可範囲外の対象が含まれる場合は次項の手順に従って報告する。
- レビュー済みHEADでも再現する失敗、および認可範囲外の変更が必要な失敗: 実行したコマンド、出力結果、作業を続行できない理由を`続行できない理由:`に明記して返す。
