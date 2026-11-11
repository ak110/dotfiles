# AWIのレーン処理：終了

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 基準資料を選択・取得する時 | `../../user-confirmation-and-report/SKILL.md` |
| 選定前、対象がGitHub上にある場合 | `review-audit-launch.md` |
| 基準資料を選択・取得する時 | `../../report-completion/SKILL.md` |
| `agent-toolkit:process-wi all-repos`の統括から、リポジトリ担当として取得済み候補を受領した時 | `all-repos-orchestration.md` |
| メインが対象選定を開始する前（実行順2）と、処理対象WIを追加する前 | `selection-procedure.md` |
| 選定YAMLを生成または消費する前 | `selection-format.md` |
| 専用worktreeとレーンを作成する前（実行順3） | `run-lanes.md` |
| レーン担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md` |
| 不良またはユーザーの是正要求を受け取った時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `immediate-fix.md` |
| 公開工程を開始する前（実行順7） | `finish-session.md` |
| 公開担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md` |

## 終了

工程の区切りそのものを確認の理由にしない。
確認要否の判定に基づいて、ユーザーの判断が真に必要な事項だけを確認待ちとし、その判断を待たずに進められる工程はそのまま継続する。
回答がすぐに得られない確認事項は、`agent-toolkit:user-confirmation-and-report`の手順に従ってUWIへ退避させる。
`processing`状態の元項目も同じ手順に従い、そのUWIへの依存を保存して`inbox`へ戻す。
作業完了の報告は`agent-toolkit:report-completion`に従って行い、本スキルの工程で作成したコミットを公開した上で報告する。

### 公開工程の承認スコープ

環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`が`1`として起動された場合は自動常駐起動（`atk wi process-loop`が子セッション向けに設定）とみなし、それ以外を手動起動とする。
この判別は選定工程で1回だけ行う。

手動起動の場合、確定したAWIの完成条件に明記されているrelease、tag、push、PRまたはMRの作成、配布といった操作について、記載された操作内容、対象、影響範囲に限り承認スコープとして保持する。
一方、自動常駐起動ではこれらを保持しない。
すでに承認されている範囲の操作はそのまま実行し、新たに具体化したことで範囲が変わる部分だけをユーザーへ確認する。
なお、破壊的な操作をする前の事前説明は維持する。
