# 監査記録

本ファイルは、`agent-toolkit`の規範文書とタスク文書が持つ条文のうち、対象の挙動を実測して確定したものについて、実測した日付、観測した版数及び再検証の手段を保持する。
条文の側には観測事象だけを置き、本ファイルのH2見出しで索引する。
条文が前提とする挙動と異なる観測を得た場合は、当該条文が指すH2見出しを読み、記載された手段で再検証してから条文の失効を判定する。
H2見出しは索引元の条文が指す文字列と一致させる。索引元の条文を移設し、又は索引元の節名を改める改訂では、同じ改訂で本ファイルのH2見出しと索引元の参照を併せて改める。
本ファイルは`agent-toolkit`の配布物に含まれない。索引を辿れるのは、本リポジトリの作業ツリーを持つ主体に限る。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：コンパクション計測通知：2026年9月10日

2026年9月10日、Codex CLI 0.153.4の`codex app-server generate-json-schema --out <管理対象一時領域の絶対パス>`が終了コード0で生成したJSON Schemaを実測した。`v2/ItemStartedNotification.json`は`item`・`startedAtMs`・`threadId`・`turnId`を必須とし、`startedAtMs`をitem lifecycle開始時のUnix時刻（ミリ秒）と定める。`v2/ItemCompletedNotification.json`は`completedAtMs`・`item`・`threadId`・`turnId`を必須とし、`completedAtMs`をitem lifecycle完了時のUnix時刻（ミリ秒）と定める。`ThreadItem`は、`id`と`type`を必須とし、`type`が`contextCompaction`である`ContextCompactionThreadItem`を変種に持つ。再検証は、同じコマンドで現行版のJSON Schemaを生成し、当該2通知の必須項目と時刻の説明及び`ContextCompactionThreadItem`の必須項目を確認する。

## agent-toolkit/rules/01-agent.md：行動指針：2026年9月8日

2026年9月8日、Codexのrollout記録`01a07e75-0c9a-7263-a52e-0e24c6aacc62`を実測した。生存14,745秒に対し`custom_tool_call`が445回であり、当該呼び出しから出力までの間隔の中央値は0.1秒であった。`reasoning`と`token_count`を起点とする間隔の合計は約10,186秒であり、ツール呼び出し1サイクルあたり約23秒に当たる。再検証は、同じ集計を任意のrollout記録へ適用し、`custom_tool_call`の件数と間隔の合計を対比する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月5日

2026年9月5日、GNU bash 5.2.21で、外側のbashの二重引用符へ置いたドル記号付きの変数は外側で展開され、単一引用符で囲んだ値は`sh -c`を2段重ねた実行で引用符を失うことを実測した。再検証は、外側のbashの二重引用符の内側へドル記号と単一引用符を含む文字列を置き、`sh -c`を2段重ねて出力を確認する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月8日（1）

2026年9月8日、ripgrep 15.2.0で、隠しディレクトリ配下の追跡ファイルだけが含む文字列に対し、リポジトリrootを対象とする`rg -n --fixed-strings`が一致0件を返し、同じ文字列を対象とする`git grep -nF`が当該ファイルを返すことを実測した。再検証は、隠しディレクトリ配下の追跡ファイルだけが含む文字列を1件選び、両コマンドの一致件数を比べる。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月8日（2）

2026年9月8日、ripgrep 15.2.0で、隠しディレクトリ配下のファイルに存在する文字列が、`--hidden`の無い実行で一致0件となり、付けた実行で一致することを実測した。再検証は、隠しディレクトリ配下のファイルへ一意な文字列を1件置き、`--hidden`の有無で一致件数を比べる。

## agent-toolkit/rules/99-claude-code.md：ツールAPIと権限：2026年9月1日

2026年9月1日、ツール呼び出しだけで地の文を持たない応答に対して`Your previous response had no visible output`が返ることを実測した。再検証は地の文を持たない応答を1回発行し、当該要求の有無を確認する。

## agent-toolkit/rules/99-claude-code.md：役割上の区分と実行環境上の区分：2026年9月4日

2026年9月4日、agent-toolkit 2.94.0で通常起動と軽量起動の設定読込先およびStop系フックの動作を実測した。再検証は`agents_server`の通常起動と軽量起動を比較する。

## agent-toolkit/share/lane-integration.parent.md：所有資源の回収：2026年9月3日

2026年9月3日にgit version 2.43.0で、upstreamを設定した専用branchをローカルの`develop`へ統合した直後に`branch -d`が未統合として拒否されることを実測した。再検証は`git branch -vv`で追跡先を確認して同じ状態の`branch -d`の終了コードを観測する。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年8月31日

2026年8月31日から9月2日までの5セッションの実測で、出力トークン量はレーン数へほぼ比例し、レーン数を増やしたセッションで所要時間が短くなる傾向は観測されなかった。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年9月2日

2026年9月2日のセッションで、担当7件のレーンは同じセッションの担当4件の3レーンを1件あたりのトークン量と所要時間の双方で下回った。再検証は同一条件のレーンを比較する。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年9月3日

2026年9月3日のセッションでは、担当7件のレーンの実装担当の直列時間が8479秒に達し、セッション全体の所要時間が目標を64.1分超過した。再検証はレーンごとの担当件数と実装担当の直列時間を突き合わせる。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント`https://code.claude.com/docs/en/agent-sdk/user-input`の`Question format`節、`Response format`節及び`Limitations`節で確認した。再検証は同じ3節を読む。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年8月31日

2026年8月31日、Claude Code v2.1.251・Fable 5で、ツール結果と次のツール呼び出しの間へ置いた地の文が`· summarized`付きの短縮文へ置換されることを実測した。当該回では、ターン冒頭と`AskUserQuestion`直前の地の文が原文どおり表示された。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月3日

2026年9月3日、Claude Code v2.1.258・Opus 5では、複数のツール呼び出しの間へ置いた地の文の同じ置換をユーザーが端末表示で確認できなかった。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月5日

2026年9月5日、Fable 5.1で`AskUserQuestion`直前の地の文へ選択肢の前提を置いた回では、ユーザーが当該地の文を読めず判断できないと回答し、確認の再発行を要した。当該回のClaude Codeの版数は記録していない。再検証はモデルと版ごとに、ターン冒頭、ツール呼び出しの間及び`AskUserQuestion`直前の3箇所へ地の文を置き、表示を確認する。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月4日

```text
2026年9月4日、Claude Code 2.1.260で、Bash背景ジョブの稼働中はデバッグログへ`[goal] evaluation deferred — background work still running`が出て評価が発動せず、背景ジョブを起動しない指示では評価が発動することを実測した。再検証は`claude --debug-file <path> --print --session-id <uuid> --permission-mode auto --allowedTools Bash -- '/goal <条件>。まずBashツールをrun_in_background=trueで使い sleep 60 を実行し、その後は追加作業をせずターンを終えること'`を実行して当該行の有無を確認し、背景ジョブを起動しない指示でも同じコマンドを実行し、評価の発動を対にして確認する。
```

## agent-toolkit/skills/delegation/references/runtime-routing.md：modelとreasoning effort：2026年8月

本項は、軽量モデルへreasoning effort `max`を割り当てた大きな作業で自動コンパクションが10回発生し、所要時間が大幅に伸びた観測に基づく（2026年8月、ユーザー報告）。再検証は、同じ組合せで同規模の作業を1件実行し、自動コンパクションの発生回数を観測することによる。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月

2026年9月、Claude Codeで`agents_server`の`wait`が発行から120秒で背景タスクへ移り、完了時に当該タスクの通知として結果本文が届くことを実測した。再検証は`wait`を発行し、背景移行の通知を受領した後に完了通知の到達を確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月3日

2026年9月3日から9月4日にかけて、`agents_server`の`start`で起動した委譲先が外部コマンドの完了待ちを表明して`status: completed`で終端し、当該コマンドの終了後も再開しない事象を実測した。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月4日

2026年9月4日には、同じ経路で起動した委譲先が自ら起動した委譲先の終端を待たずに待機表明で終端し、呼び出し元が保持した`session_id`へ`send_message`を送って再開させた事象を実測した。再検証は`agents_server`で委譲先を起動し、待機表明で終端させたうえで、完了通知の到達を確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月7日

2026年9月7日から9月8日にかけて、Codexで動く主体が発行した`wait`が`timed out awaiting tools/call after 300s`で失敗する事象を実測した。再検証は、当該上限より長く稼働する委譲先へ`timeout`を省略した`wait`を発行し、当該失敗の有無を確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月4日

2026年9月4日、Claude Code 2.1.260で次の2点を実測した。終了コード7で終わる`run_in_background=true`のBashについて、起動結果が返した出力ファイルは出力の全量と`[exited with code 7]`を保持した。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月2日

2026年9月2日に別のセッションが起動した背景ジョブの実行識別子は`TaskOutput`が`No task found with ID`で拒否し、当該ジョブの出力ファイルは絶対パスのまま全出力と`[exited with code 0]`を保持していた。再検証は同じ形で背景実行して当該ファイルを読み、終了済みセッションが残した出力ファイルの絶対パスの読み取りと、同じ実行識別子が解決しないことを対にして確認する。

## agent-toolkit/skills/plan-mode/references/plan-file-standards.md：実装資料と完了条件：2026年9月7日

2026年9月7日、git 2.43.0で`assert expanded_common_job_count + len(statusline_jobs) == 7`を検査する`git grep -n`が一致0件を返し、同じ文字列を検査する`git grep -nF`が1件返すことを実測した。再検証は、正規表現のメタ文字を含む行を対象リポジトリの追跡ファイルから1件選び、`git grep -n`と`git grep -nF`の一致件数を比べる。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節と`SubagentStop`節で確認した。再検証は同2節を読む。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日

2026年9月6日、Claude Code 2.1.263で次を実測した。地の文を1文書いた直後に同じ応答で`Bash`を1回呼ぶ指示を与え、当該呼び出しの`PreToolUse`が受領した`transcript_path`の内容を捕捉した。発火時点の当該JSONLはアシスタントのエントリを1件も持たなかった。実行後の同じJSONLには、同一の`message.id`を持つ思考ブロック、テキストブロック及びツール呼び出しの3エントリが並んでいた。再検証は、同じ指示を与えて発火時点のJSONLの内容と実行後の内容を比較する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日

2026年9月8日、Claude Code 2.1.263で次を実測した。存在しないリポジトリを指す`git -C /tmp log --oneline -1`は終了コード128で終わり、当該セッションの状態ファイルの`git_log_checked`は未設定のままだった。続けて実在するworktreeを指す同じ形の`git log`を実行すると、当該cwdのキーが真になった。再検証は、この2つのコマンドを単独で順に実行し、`{tempdir}/claude-agent-toolkit-<session_id>.json`の`git_log_checked`を前後で比較する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルへ埋め込まれた照合関数が、値が空文字列と`"*"`のいずれかのときに正規表現へ変換せず一致を返すことと、同ドキュメントが同じ3分類を記載することを確認した。再検証は、当該ドキュメントの`Matcher patterns`節を取得し、`strings`で抽出した当該関数が空値と`"*"`を短絡することを確認する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：出力フィールドの使い分け：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルと公式のHooksリファレンスで確認した。再検証は同じ2つの資料で当該文字列の出所を確認する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：Stop/SubagentStopフックの再帰呼び出し対策：2026年9月4日

2026年9月4日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節、`Stop`節及び`SubagentStop`節で前段の入力仕様を確認した。同日、Claude Code 2.1.260のStopフックへ渡る入力を捕捉した。`run_in_background`で起動したBashジョブが、`type`を`shell`、`status`を`running`とする要素として`background_tasks`へ現れた。再検証は同3節を読み、Stopフックへ渡る入力を捕捉して`background_tasks`の有無と要素の構造を確認する。

## agent-toolkit/skills/writing-standards/references/notation-rules.md：逐語引用の検出範囲：2026年9月5日

本表は2026年9月5日に実測した。次の1文を地の文、引用ブロック、フェンス付きコードブロックへ置いた3つの検体を作成し、pyfltr 3.17.8の`textlint`・`colloquial-check`で検査した。

```text
警告を出すと思う。
```

地の文では口語表現チェックとtextlintの弱い表現がいずれも検出され、引用ブロックではtextlintの弱い表現だけが検出され、フェンス付きコードブロックではいずれも検出されなかった。em-dash（U+2014）を含む同じ形の検体を`scripts/check_dash.py`で検査したところ、引用ブロックでは検出され、フェンス付きコードブロックでは検出されなかった。再検証は同じ3つの検体を再度作成し、同じ検査で検出の有無を対にして確認する。

## agent-toolkit/skills/writing-standards/references/session-records.md：H1直下：2026年9月2日

2026年9月2日に`~/.claude/projects`配下と`~/.codex/sessions`配下の記録で実測した。再検証は同じ2箇所を当該キーで検索する。

## agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日

本節の記述は2026年9月3日に`agent-toolkit/skills/session-review/scripts/session_review_evidence.py`の`_latest_claude_usages`と`_stats_summary_data`を読んで確認した。再検証は同じ2つの関数を読む。
