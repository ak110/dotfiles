# フックごとのホスト対応範囲の設計記録

[設計記録の索引](design.md)から参照する、フックごとのホスト対応範囲の記録である。

## フックごとの処理とCodexの対応範囲

`docs/guide/claude-code-guide.md`「常時有効な仕組み」はエンドユーザーが観測できる主な働きだけを示し、本節は各フックの判定条件とCodexでの対応範囲を保持する。

agent-toolkitは以下のフックを常時有効化する。
識別子はイベント名と処理名の組で示し、`plugin`はagent-toolkitプラグインの配布分、
`個人設定`はdotfilesユーザーの`~/.claude/settings.json`へ配布される分を指す。
Codex欄の「対応」「部分対応」「非対応」は、Codex 0.154.0の実機検証で確認した範囲を示す。

| フック識別子 | 処理概要 | Claude対応状況 | Codex対応状況 |
| --- | --- | --- | --- |
| plugin `PreToolUse/pretooluse` | 所有を確認できない終了・追送、能動heredoc置換、pattern kill、公開commitの形式・帰属、定型委譲の必須入力を保護する。本文・改行・生成物、shellのオプション・出力接続・rev-parse、背景出力は警告で支援する。資料不足は初回だけ案内し、再書込を許可する。通常読取・診断・結果回収・質問・既存復旧は保つ | 対応 | 部分対応。共通の保護と資料選択を使う。Codexの大量読取は予算確認を警告して標準読取を継続する。PowerShell改行・AskUserQuestionなどホスト固有入力の判定は除く |
| plugin `PostToolUse/posttooluse` | 成功したツール実行の観測結果を記録する。TaskStopは登録から成功対象の完了記録だけを消費し、別対象と失敗・拒否の記録を保持する。計画ファイル・スキル起動・バックグラウンドタスク・`agents_server` sessionの状態を記録し、計画ファイルの書き込み後に計画構造の自動チェックを案内し、そのセッションが投入したUWIへの回答を通知する。`AskUserQuestion`への回答が選択肢と一致しない自由記述を含む場合は、UserPromptSubmitと同じ、発話の内容を現物で確かめる手順を示す注記を返す。ホストの上限を超えて退避したBashとPowerShellの結果は、抜粋を保存先・元の出力のバイト数・保存先を読む次の操作を示す本文へ置き換える | 対応 | 部分対応。成功した編集による計画ファイルの記録と計画構造の自動チェックの案内、`agents_server` sessionの状態記録、Bashで実行した振り返りの準備結果の記録に対応する。Codexはシェル出力を退避せず切り詰めて返すため、退避した結果の置き換えは対象外 |
| plugin `SessionStart/rules_context` | セッションの開始、再開、`/clear`および圧縮の後に、メインエージェントだけに適用する条文（`share/rules/rules-main.md`とClaude Code向けの`share/rules/rules-main.claude-code.md`）を文脈へ追加する。`agents_server`が起動した委譲先では追加しない。圧縮の後は`QUALITY_CHECKPOINT_NOTICE`も併せて追加する。Claude Codeのメインへは入力の会話IDとtranscriptの絶対パスも通知する | 対応 | 対応。`rules_context_codex`として射影し、Claude Code固有の条文を除いて追加する。handlerの`additionalContextLimit`は0とし、切り詰めない |
| plugin `SubagentStart/rules_context` | サブエージェントの起動時に、サブエージェントと委譲先だけに適用する条文（`share/rules/rules-subagent.md`）を文脈へ追加する。親の一時領域がある場合は、agent_idごとの専用領域を通知する | 対応 | 対応。handlerの`additionalContextLimit`は0とし、切り詰めない |
| plugin `SubagentStop/subagent_stop_advisor` | 空の完了報告での終了をブロックする | 対応 | 対応。空の完了報告をブロックし、内部返却の言語は判定しない |
| plugin `SessionEnd/session_end_cleanup` | 期限を過ぎたセッション状態を回収する。会話を破棄する時だけ、そのセッションの状態を削除する | 対応 | 対応。終了理由が`other`固定のため、期限切れ状態の回収だけを実行する |
| plugin `Stop/stop` | 言語保護、常駐ループの終了保証、所有計画・未観測委譲先・可視本文・report-completion起動不足の非遮断通知、未配送通知のホスト配送を集約する。送達・残工程・質問の意味は文書へ委ねる。実在する待機を除き、同じ原因・対象・対処の反復を共通noticeで抑止する | 対応 | 部分対応。`response_language_advisor`で説明言語を、`submission_report_advisor`で投入作業の報告用要約の有無を判定し、`decision`と`reason`だけを返す。非遮断の終了支援は関連操作と文書で扱う |
| plugin `UserPromptSubmit/user_prompt_submit` | 実入力・手動スキル起動を記録し、開始・clear・圧縮後の最初の実入力へ一度だけ起動を案内する。固定手順の時間再注入と時刻追跡は持たない。全角記号と例示・調査語の関連案内は既存共通noticeで反復を抑止する。機械注入は実入力向けの案内を消費しない | 対応 | 対応。文脈ごとの一度の案内と入力観測を同じ条件で行い、sessionTitleは付けない |
| plugin `PermissionRequest/permissionrequest` | 全ツールの確認ダイアログを自動許可し、許可した要求をJSON Lines形式のログへ記録する。記録には要求元セッションの識別子と、委譲の起点となった最上位セッションの識別子を残す | 対応 | 非対応。`approvalPolicy: "never"`では危険と判定するコマンドを承認なしに拒否し、PermissionRequest hookを呼ばない。Codex委譲先の標準手順は承認を要しないコマンドで組む |
| plugin `PostToolUseFailure/posttooluse` | Bashの背景実行が失敗した応答にもタスク識別子が含まれる場合は、バックグラウンドタスクの所有記録へ保存する。失敗を成功済み検証として記録しない | 対応 | 非対応。対応するイベントが存在しない |
| plugin `PermissionDenied/posttooluse` | 許可拒否時に状態を変更せず終了する | 対応 | 非対応。対応するイベントが存在しない |
| plugin `StopFailure/stopfailure_notifier` | メインの利用上限によるターン終了時に、再開要求を既存modへ渡す | 対応 | 非対応。対応するイベントが存在しない |
| 個人設定 `PreToolUse/pretooluse` | dotfilesの配布元ファイルと個人の命名規約に基づく編集前のチェック | 対応 | 非対応。dotfiles固有の配布構成を前提とするため、プラグインへ移さない |

Codexはplugin同梱フックの定義が変わると、ユーザーが変更後の定義を再び信頼するまでそのフックを実行しない。
対象はagent-toolkitの9イベント（`SessionStart`、`SubagentStart`、`PreToolUse`、`PostToolUse`、`PermissionRequest`、`UserPromptSubmit`、`Stop`、`SubagentStop`、`SessionEnd`）である。
`pytools/_internal/install_codex_plugins.py`の`_hook_trust_notice_required`は、app-serverの`hooks/list`に次の条件を全て満たすhookが1件以上あれば、`/hooks`で信頼する案内を出す。
条件は、`pluginId`が対象pluginと一致し、`source`が`plugin`、`enabled`が`true`、`isManaged`が`false`、`trustStatus`が`untrusted`または`modified`であることとする。
全9イベントの登録は案内の条件に含めない。
他のhookにエラーや承認済みの状態があっても、この条件を満たすhookがあれば案内する。
再信頼の操作だけではSessionStartの規範注入を検収できないため、案内は信頼後に新しいセッションを開始して注入を確かめることも求める。

### セッション種別が分かる短い名前

PostToolUseのSkill観測と手動起動のUserPromptSubmitは、process-wi・process-wi single-lane・add-awi-by-userの種別を通常session stateへ保存する。
UserPromptSubmitのコマンドhookは`hookSpecificOutput.sessionTitle`を返す。
先頭は`process-wi`・`single-lane`・`add-awi`とし、計画があればstemの日時接頭辞を除いた名称を続ける。
複数リポジトリを統括する会話（`process-wi all-repos`の明示起動と、process-loopの環境印と会話IDが一致する常駐統括）は、計画名を連結せず`process-wi all-repos`とする。
統括は複数の個別計画を順に扱うため、最後の計画名を連結すると会話全体の処理範囲を誤認させる。
常駐統括の中で単一処理の種別が記録されても統括名を保ち、子セッションは会話IDの照合で区別して通常の命名に従う。
その他は計画名だけにし、計画が無ければホストの名前を保つ。

入力の現在名`session_title`が空か前回自動設定した名前と一致する場合だけ更新する。
後から判明した種別・計画を名前へ反映し、前回設定名は既存の通常状態で保持する。
手動の名前を保ち、Codexは命名しない。

Function hooksを命名に必要とする案は、組織向け設定で省略された場合に命名できないため採用しない。
セッション開始イベントだけに集約する案はclear後を取りこぼし、一度限りの専用記録は後から判明した種別への更新を妨げる。
