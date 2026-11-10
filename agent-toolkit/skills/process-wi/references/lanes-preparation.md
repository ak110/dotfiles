# AWIのレーン処理：直前の同期結果の検分

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 選定前、対象がGitHub上にある場合 | `review-audit-launch.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/review-audit-launch.md` | |
| `agent-toolkit:process-wi all-repos`の統括から、リポジトリ担当として取得済み候補を受領した時 | `all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` | |
| メインが対象選定を開始する前（実行順2）と、処理対象WIを追加する前 | `selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` | |
| 選定YAMLを生成または消費する前 | `selection-format.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format.md` | |
| 専用worktreeとレーンを作成する前（実行順3） | `run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` | |
| レーン担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` | |
| 不良またはユーザーの是正要求を受け取った時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` | |
| 公開工程を開始する前（実行順7） | `finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` | |
| 公開担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.parent.md` | |

## 直前の同期結果の検分

リポジトリ間の同期が失敗した状態のまま着手すると、AWIの処理が途中で成立しなくなる恐れがある。
そのため選定を始める前に、`atk config show`で表示される`state_dir`直下の`sync-report.json`を確認する。

- ファイルが存在しないか、`status`が`succeeded`であれば、そのまま次の工程へ進む
- `status`が`failed`、あるいは`post_apply`の`failed_steps`が1件以上ある場合は、その失敗内容を踏まえてこのセッションで処理を最後まで進められるか判断する。
  問題なく処理できると判断した場合は、その根拠を報告して作業を進める。
  処理の完遂が難しい場合は作業に着手せず、`atk wi process-loop abort`でprocess-loopへ中断を要請し、失敗したステップと理由を報告してセッションを終了する

## 自動コードレビュー監査

対象リポジトリがGitHub上にある場合は、選定を始める前に読込表の監査契約に従って未判定（pending）の有無を確認し、必要な監査を起動する。
監査の終了と追加WI投入の完了は、独立した既存候補の選定・レーン起動と並行して待つ。
同じprocess-wiの実行へ追加するWIは、保存結果の正式ファイル名を受領した直後に`selection-procedure-addition.md`「処理対象WIの追加」へ渡す。
監査を省略する条件、候補0件時の受領、公開前に確定する処置は監査契約に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| メインによるWI選定とレーン分け：処理対象WIの追加の基準を適用する前 | `selection-procedure-addition.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure-addition.md` |
