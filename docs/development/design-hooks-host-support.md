# フックごとのホスト対応範囲の設計記録

[設計記録の索引](design.md)

## フックごとの処理とCodexの対応範囲

`docs/guide/claude-code-guide.md`「常時有効な仕組み」はエンドユーザーが観測できる主な働きだけを示し、本節は各フックの判定条件とCodexでの対応範囲を保持する。

agent-toolkitは以下のフックを常時有効化する。
識別子はイベント名と処理名の組で示し、`plugin`はagent-toolkitプラグインの配布分、
`個人設定`はdotfilesユーザーの`~/.claude/settings.json`へ配布される分を指す。
Codex欄の「対応」「部分対応」「非対応」は、Codex 0.154.0の実機検証で確認した範囲を示す。

| フック識別子 | 処理概要 | Claude対応状況 | Codex対応状況 |
| --- | --- | --- | --- |
| plugin `PreToolUse/pretooluse` | 所有を確認できない終了・追送、heredoc置換、pattern kill、公開commitの形式・帰属、定型委譲の必須入力を保護し、他は警告で支援する。資料不足は初回だけ案内し、再書込を許可する | 対応 | 部分対応。共通の保護と資料選択を使う。Codexの大量読取は予算確認を警告して標準読取を継続する。PowerShell改行・AskUserQuestionなどホスト固有入力の判定は除く |
| plugin `PostToolUse/posttooluse` | 成功したツール実行の観測結果を記録し、計画構造の自動チェックの案内、投入したUWIへの回答の通知、退避したBash・PowerShell結果の抜粋の置き換えを行う | 対応 | 部分対応。成功した編集による計画ファイルの記録と計画構造の自動チェックの案内、`agents_server` sessionの状態記録、Bashで実行した振り返りの準備結果の記録に対応する。Codexはシェル出力を退避せず切り詰めて返すため、退避した結果の置き換えは対象外 |
| plugin `SessionStart/rules_context` | 文脈を引き継がずに始まる会話（`source`が`startup`・`clear`・`compact`）のメインエージェントへ、入口のスキル`agent-toolkit:atk-mode`の起動を求める1行を追加する。文脈を引き継ぐ`resume`・`fork`では案内しない。あわせて、その場で決まる値（セッション識別子・managed-tempなど）を文脈へ追加する。規範の本文は載せない。`agents_server`が起動した委譲先ではメインの案内を追加しない | 対応 | 対応。`rules_context_codex`として射影し、Claude Code固有の値を除いて追加する。handlerの`additionalContextLimit`は0とし、切り詰めない |
| plugin `SubagentStart/rules_context` | サブエージェントの起動時に、委譲先の入口文書`playbooks/subagent.md`の所在とCLIの所在を文脈へ追加する。規範の本文は載せない | 対応 | 対応。handlerの`additionalContextLimit`は0とし、切り詰めない |
| plugin `SubagentStop/subagent_stop_advisor` | 空の完了報告での終了をブロックする | 対応 | 対応。空の完了報告をブロックし、内部返却の言語は判定しない |
| plugin `SessionEnd/session_end_cleanup` | 期限を過ぎたセッション状態を回収する。会話を破棄する時だけ、そのセッションの状態を削除する | 対応 | 対応。終了理由が`other`固定のため、期限切れ状態の回収だけを実行する |
| plugin `Stop/stop` | 言語保護、常駐ループの終了保証、所有計画・未観測委譲先・可視本文・report-completion起動不足の非遮断通知、未配送通知のホスト配送を集約する | 対応 | 部分対応。`response_language_advisor`で説明言語を判定し、`decision`と`reason`だけを返す。非遮断の終了支援は関連操作と文書で扱う |
| plugin `UserPromptSubmit/user_prompt_submit` | 実入力・手動スキル起動を記録し、例示・調査語の注記の反復を抑える | 対応 | 対応。sessionTitleは付けない |
| plugin `PermissionRequest/permissionrequest` | 全ツールの確認ダイアログを自動許可し、許可した要求をJSON Lines形式のログへ記録する | 対応 | 非対応。`approvalPolicy: "never"`では危険と判定するコマンドを承認なしに拒否し、PermissionRequest hookを呼ばない。Codex委譲先の標準手順は承認を要しないコマンドで組む |
| plugin `PostToolUseFailure/posttooluse` | Bashのバックグラウンド実行が失敗した応答にもタスク識別子が含まれる場合は、バックグラウンドタスクの所有記録へ保存する。失敗を成功済み検証として記録しない | 対応 | 非対応。対応するイベントが存在しない |
| plugin `PermissionDenied/posttooluse` | 許可拒否時に状態を変更せず終了する | 対応 | 非対応。対応するイベントが存在しない |
| plugin `StopFailure/stopfailure_notifier` | メインの利用上限によるターン終了時に、再開要求を既存modへ渡す | 対応 | 非対応。対応するイベントが存在しない |
| 個人設定 `PreToolUse/pretooluse` | dotfilesの配布元ファイルと個人の命名規約に基づく編集前のチェック | 対応 | 非対応。dotfiles固有の配布構成を前提とするため、プラグインへ移さない |

Codexはplugin同梱フックの定義が変わると、ユーザーが変更後の定義を再び信頼するまでそのフックを実行しない。
条件は、`pluginId`が対象pluginと一致し、`source`が`plugin`、`enabled`が`true`、`isManaged`が`false`、`trustStatus`が`untrusted`または`modified`であることとする。
全9イベントの登録は案内の条件に含めない。
他のhookにエラーや承認済みの状態があっても、この条件を満たすhookがあれば案内する。
再信頼の操作だけではSessionStartの通知を検収できないため、案内は信頼後に新しいセッションを開始して、`atk-mode`の起動を求める通知が届くことを確かめることも求める。

### セッション種別が分かる短い名前

複数リポジトリを統括する会話（`process-wi all-repos`の明示起動と、process-loopの環境印と会話IDが一致する常駐統括）は、計画名を連結せず`process-wi all-repos`とする。
統括は複数の個別計画を順に扱うため、最後の計画名を連結すると会話全体の処理範囲を誤認させる。
常駐統括の中で単一処理の種別が記録されても統括名を保ち、子セッションは会話IDの照合で区別して通常の命名に従う。
その他は計画名だけにし、計画が無ければホストの名前を保つ。
`add-awi-by-user`には種別だけの名前を付けず、依頼の内容を区別できるClaude Codeの自動題名に任せる。

入力の現在名`session_title`が空か前回自動設定した名前と一致する場合だけ更新する。
手動の名前を保ち、Codexは命名しない。

Function hooksを命名に必要とする案は、組織向け設定で省略された場合に命名できないため採用しない。
セッション開始イベントだけに集約する案はclear後を取りこぼし、一度限りの専用記録は後から判明した種別への更新を妨げる。
