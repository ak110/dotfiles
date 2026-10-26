# Stop/SubagentStopフック

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-stop.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-stop.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-stop.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/claude-code-runtime.md#「実行時能力と通信scope」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/claude-code-runtime.md#「実行時能力と通信scope」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/termination-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 遮断の条件と再帰呼び出し対策

- 記述言語など本文から機械的に判定できる要件では、hookの通知内に検出箇所、判定基準、および誤検出時に本文を変更せずに終了させる操作方法を示す。ユーザー画面へ本文を表示するClaude Codeの最上位メインエージェントでは`…`のみを出力する。委譲元へ本文を渡す主体（Claude Codeのサブエージェント、agents_serverの委譲先、Codex）は、作業成果、判断内容、未達事項、ならびに指定された返却形式を含む完了本文の全文をそのまま再出力して停止する。これらの主体では最後の応答が返却本文となるためである。

## 入力

Claude Codeでは、assistantの`text`要素および`mcp__agent-toolkit__send_to_user`の文字列引数`message`を可視本文として扱う。

## Codexの差分

CodexのStopフックでは、`response_language_advisor`を用いて共通入力`last_assistant_message`の説明言語を判定する。終了工程の起動要否、ならびにメッセージ送達や残工程の点検は`agent-toolkit:completion-report`の文書で扱う。Claude Code固有のtranscript形式や非遮断通知を前提とする他のStop判定は、Codexの対象から除外する。

## 委譲先での発火

- `Stop`は、対象セッション自身のターン終了で発火する。`agents_server`が起動した委譲先かどうかは環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`で判定する。

- `SubagentStop`は、`Agent`ツールのサブエージェントのターン終了でのみ発火し、入力の`agent_id`で判別する。前述の環境変数は`agents_server`が起動したセッションにのみ付与される。`SubagentStop`に登録する処理は、サブエージェントが実行可能な処置に限定する。

区分の詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「実行時能力と通信scope」に従う。

## 終了工程の証拠

判定および証拠保持の責務は`agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が担う。設計の経緯と不採用とした案は`docs/development/design-hooks.md`「終了工程の証拠」に記録されている。
プロジェクト規範に基づく工程省略を記録するメインエージェントは、`agent-toolkit:completion-report`を起動せず、同スキルの`references/termination-evidence.md`を直接参照する。
省略の意味と優先順位はメインエージェントが判断する。記録処理は指定パスから規範全文を読み込んで保存し、配置場所、観測した作業対象、現在のメインエージェントと会話・入力の対応関係を確認する。
規範の変更、新たな判断、または新しい人間からの入力を受けて成果作業をする場合は、省略の適用可否を改めて判断する。省略後も他作業の起動漏れがないかを判定し、process-loopの会話が終了する際に`atk agents-exit-session`を実行する義務（`agent-toolkit:completion-report`の`references/termination-evidence.md`）を維持する。

- 供給と消費: PreToolUseとPostToolUse（Claude CodeではPostToolUseFailureを含む）が実際の呼び出しと応答を供給し、UserPromptSubmitが人間からの入力を供給する。Claude CodeのStopにおける`termination_order_advisor`は同一の証拠から起動漏れを通知する。判定は報告本文を除外した証拠に基づいて行う。
- 判定する範囲: 成果作業（成功した`agent-toolkit:process-wi`や`agent-toolkit:add-awi-by-user`の開始、リポジトリに対する直接編集、計画の保存、WIの保存、commitやpush）を実施した作業を対象とする。起動が必要な作業に対して`agent-toolkit:completion-report`の起動証拠が存在しない場合、共通noticeによって同一の原因・対象・対処の反復を抑制しつつ非遮断通知を返す。適用されるプロジェクト規範による有効な省略指示がある場合は、該当作業の起動不足判定のみを免除する。メッセージ送達や残工程の点検、報告内容・書式はcompletion-reportの規範が扱う。
- 起動証拠: Claude Codeでは成功したSkill呼び出し、CodexではBash向けに正規化された読取コマンドの入力が`skills/completion-report/SKILL.md`を指し、その結果に対象スキルのfrontmatterの`name`が含まれる読取操作を証拠とする。読取パス文字列のみの言及、起動を予告する発話、失敗または空の読取結果、検索の一致行、他の作業や委譲先の起動は証拠として扱わない。コマンド対象は`agent-toolkit/agent_toolkit/_common/shell_segments.py`の`extract_execution_segments`が抽出した実行位置で識別し、検索キーワードや引用文字列内のコマンド名は対象外とする。
- 待機の許可: 起動証拠が揃っていない作業であっても、該当作業が待機している非同期処理が稼働中であれば終了を許可する。待機対象は、作業自身が起動したagents_serverのセッション、および作業開始以降に起動が記録された非同期処理（バックグラウンドAgent、MCPのバックグラウンドタスク、未配送の完了通知、バックグラウンドBash）である。終了するバックグラウンドBashは完了通知によってターンが再開し、その時点で再判定される。実行位置から既知のサーバーやwatchプロセスの起動と判別できるバックグラウンドBashは待機免除から除外し、起動不足を判定する。実行時間に上限のある長時間コマンドや待機用CLIの免除は維持し、任意の自作コマンドが常駐するかどうかを静的に確定して免除することは避ける。作業開始前から稼働しているタスクは待機対象から除外する。作業との対応関係はStop側で判定し、他のhookが使用する`background_tasks.is_pending_async_work`の定義には影響を与えない。
- 作業の区切り: ユーザー入力、呼び出し単位、開始記録および判断記録によって作業を区切る。起動証拠を取得した作業の後に新たな人間からの入力があり、続いて成果作業が届いた場合は新規の作業として扱い、過去の起動証拠を流用しない。
- 判断の記録: 中止、置換、開始、再開、確認待ち、委譲先待機、技術的不成立などの判断は、メインエージェントが`atk run-script termination-evidence`で記録する。判断の意味はメインエージェントが決定し、記録処理は識別子から実体を読み取って、元入力の出所、対象の実在性、および実行主体のみを検証する。Stopの継続入力、`atk-auto`、構造上の生成標識を持つ入力、ならびに`source`が`user`以外の入力は、人間による入力の根拠から除外する。完了の自己申告は記録操作の対象外とする。現在の作業を中止・置換・技術的不成立として記録した場合でも、他の作業に残る起動不足は通常どおり判定する。
- 証拠を読めない場合: 状態データの破損、旧バージョンの未供給、期限切れによる回収後、またはtranscriptを読み取れず待機対象を確認できない場合は、完了とも未完了とも断定せず、Stop判定ログに診断情報を残した上で終了を許可する。
