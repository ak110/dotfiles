# 監査記録

本ファイルは、`agent-toolkit`の規範文書とタスク文書が持つ条文のうち、対象の挙動を現物で観測して確かめたものについて、確認した日付、観測した版数及び再検証の手段を保持する。
条文の側には観測事象だけを置き、本ファイルのH2見出しで索引する。
条文が前提とする挙動と異なる観測を得た場合は、当該条文が指すH2見出しを読み、記載された手段で再検証してから条文の失効を判定する。
H2見出しは索引元の条文が指す文字列と一致させる。索引元の条文を移設し、又は索引元の節名を改める改訂では、同じ改訂で本ファイルのH2見出しと索引元の参照を併せて改める。
本ファイルは`agent-toolkit`の配布物に含まれない。索引を辿れるのは、本リポジトリの作業ツリーを持つ主体に限る。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：背景ジョブ完了通知と実プロセスの終了順：2026年9月21日

2026-09-21に、Claude Codeの背景ジョブ完了通知が届いた後も、同じ`atk agents wait`の実プロセスが稼働している事例を観測した。
通知後に`ps -eo pid,etimes,args`を実行し、PID 3566782、3566794及び3566974の同じ待機が経過時間155秒で残っていることを確認した。
旧実装ではこの状態で後続の`atk agents wait`を発行すると、待機所有権が残っているため終了コード8になり得た。
同じ処理回の背景タスク`bjhq8zib4`では、警告1行だけを持つ1回目の`completed`通知後に後続の待機が終了コード8を返した。
呼び出し元が待機と照会を発行せずターンを終えた後、同じ背景タスクが結果本文を伴う2回目の`completed`通知を送った。
再検証は背景タスクの通知と`ps -eo pid,etimes,args`の結果を比較し、同じ待機プロセスの生存と後続の`atk agents wait`の終了コードを確認する。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：2026年8月

Claude Code 2.1.251で検証した。
委譲の調整を担うサブエージェントが`SendMessage`で子へ継続指示を送って待機表明で終えたとき、その子の完了通知は送信元のtranscriptへ現れなかった。
同じ調整役が`Agent`ツールで起動した子の完了通知は現れた。
再検証は調整役のtranscriptに現れる`task-notification`の件数を起動手段別に数える。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：同一セッション内の再開：2026年8月

Claude Code 2.1.241で、同一セッション内の起動元が保持した機械可読識別子を再開対象へ用いた構成では、再開後の完了報告を起動元が受け取れることを実際に動かして確かめた。
再確認では`claude --version`で前提版数を記録し、完了通知に含まれる識別子と`SendMessage`の終了状態を比べる。
Claude Code 2.1.241で検証した設定は、対象キーを含まない一時JSONを`--settings`へ指定した。
`--setting-sources project,local`でユーザー設定を読み込まず、プロセス側で`env -u CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`を前置した。
初期化イベントで`SendMessage`と`ListAgents`の公開を確認し、両toolの実呼び出しが成功した。

## プロジェクト指示のAGENTS.md対応：2026年9月20日

2026年9月20日、Claude Code v2.1.277の公式リリースノート<https://github.com/anthropics/claude-code/releases/tag/v2.1.277>で、`CLAUDE.md`が無いプロジェクトで`AGENTS.md`を読む機能が追加されたことを確認した。再検証は同バージョンのリリースノートを読み、`AGENTS.md`対応の記載を確認する。

## docs/development/design.md：Claude CodeとCodexの規範配置：2026年9月13日

2026年9月13日、Codex CLI 0.154.0でローカルmarketplaceを隔離`CODEX_HOME`へ導入して検証した。`agent-toolkit/`直下にAgent Plugins用`plugin.json`がある構成では、`.codex-plugin/plugin.json`のhook定義よりroot manifestが優先され、app-serverの`hooks/list`は0件を返した。root manifestを除いたwrapperから相対シンボリックリンクでhook・skill・実行資源へ接続した構成では、公式CLIのsnapshotに`.codex-plugin`だけが残り、リンク先は含まれなかった。全資源を通常ファイルとして含む`agent-toolkit-codex/`では、`hooks/list`が8イベントを返した。対象は`sessionStart`、`subagentStart`、`preToolUse`、`postToolUse`、`permissionRequest`、`userPromptSubmit`、`subagentStop`、`sessionEnd`である。project trustの有無で登録集合は変わらなかった。再検証ではCodex CLI 0.154.0で`scripts/sync_codex_plugin_manifests_test.py::test_codex_0154_registers_all_hooks_independent_of_project_trust`を実行する。隔離した2つの`CODEX_HOME`における登録集合、SessionStartの管理一時領域生成、SessionEndの回収を確認する。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：コンパクション計測通知：2026年9月10日

2026年9月10日、Codex CLI 0.153.4の`codex app-server generate-json-schema --out <管理対象一時領域の絶対パス>`が終了コード0で生成したJSON Schemaを検証した。`v2/ItemStartedNotification.json`は`item`・`startedAtMs`・`threadId`・`turnId`を必須とし、`startedAtMs`をitem lifecycle開始時のUnix時刻（ミリ秒）と定める。`v2/ItemCompletedNotification.json`は`completedAtMs`・`item`・`threadId`・`turnId`を必須とし、`completedAtMs`をitem lifecycle完了時のUnix時刻（ミリ秒）と定める。`ThreadItem`は`id`と`type`を必須とし、`type`が`contextCompaction`である`ContextCompactionThreadItem`を変種に持つ。再検証は同じコマンドで現行版のJSON Schemaを生成し、当該2通知の必須項目と時刻の説明及び`ContextCompactionThreadItem`の必須項目を確認する。

## agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日

2026年9月11日、`~/.claude/projects`配下の記録（Claude Code 2.1.239から2.1.268）で、`agents_server`のstart系ツールの呼び出し489件を集計した。
このうち起動された子sessionの記録を対応付けられた233件について、呼び出しのtool_useの時刻と当該子sessionの記録の先頭エントリの時刻の差を求めた。
中位値は1.34秒、90%点は7.12秒、99%点は36.76秒であり、232件が47.65秒以内に収まった。残る1件は604.22秒であった。
同じ489件のうち7件は、tool_useから応答までの経過が1800.4秒から1800.5秒であり、ホストがMCPツール呼び出しを打ち切った回であった。
ホスト側の上限は、Claude Codeが`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`の既定として1800秒を課し、
Codexが<https://learn.chatgpt.com/docs/extend/mcp?surface=cli>の`tool_timeout_sec`の既定として60秒を課す。
再検証は同じ記録へ同じ集計を適用し、tool_useの時刻と子sessionの記録の先頭エントリの時刻の差の分布と、応答の打ち切りに達した件数を対比する。
上限値はこの分布に加えて、ホストがMCPツール呼び出しを背景タスクへ移す閾値を制約に持つ。当該閾値の測定記録は「agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月」にある。

## agent-toolkit/rules/01-agent.md：行動指針：2026年9月8日

2026年9月8日、Codexのrollout記録`01a07e75-0c9a-7263-a52e-0e24c6aacc62`を確かめた。生存14,745秒に対し`custom_tool_call`が445回であり、当該呼び出しから出力までの間隔の中央値は0.1秒であった。`reasoning`と`token_count`を起点とする間隔の合計は約10,186秒であり、ツール呼び出し1サイクルあたり約23秒に当たる。再検証は同じ集計を任意のrollout記録へ適用し、`custom_tool_call`の件数と間隔の合計を対比する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月5日

2026年9月5日、GNU bash 5.2.21で、外側のbashの二重引用符へ置いたドル記号付きの変数は外側で展開され、単一引用符で囲んだ値は`sh -c`を2段重ねた実行で引用符を失うことを実際に動かして確かめた。再検証は外側のbashの二重引用符の内側へドル記号と単一引用符を含む文字列を置き、`sh -c`を2段重ねて出力を確認する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月8日（1）

2026年9月8日、ripgrep 15.2.0で、隠しディレクトリ配下の追跡ファイルだけが含む文字列に対し、リポジトリrootを対象とする`rg -n --fixed-strings`が一致0件を返し、同じ文字列を対象とする`git grep -nF`が当該ファイルを返すことを実際に動かして確かめた。再検証は隠しディレクトリ配下の追跡ファイルだけが含む文字列を1件選び、両コマンドの一致件数を比べる。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月8日（2）

2026年9月8日、ripgrep 15.2.0で、隠しディレクトリ配下のファイルに存在する文字列が、`--hidden`の無い実行で一致0件となり、付けた実行で一致することを実際に動かして確かめた。再検証は隠しディレクトリ配下のファイルへ一意な文字列を1件置き、`--hidden`の有無で一致件数を比べる。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月14日

2026年9月14日、Git 2.43.0で実際に動かして確かめた。公式`git-rev-parse`文書は`--short=<length>`を、少なくとも指定長を持つ一意な接頭辞と定める。`core.abbrev`を設定していない対象HEADでは`git rev-parse --short HEAD`が9文字、`git rev-parse --short=7 HEAD`が7文字を返した。`grep.lineNumber=true`を指定した`git grep -h -m 1 -F -e <固定文字列> -- AGENTS.md`は`3:<本文>`を返し、同じ検索へ`--no-line-number`を指定すると`<本文>`だけを返した。再検証は`git config --get core.abbrev`の設定有無を記録し、同じHEADに対する`git rev-parse --short HEAD`と`git rev-parse --short=7 HEAD`の文字数を比較し、後者が7文字以上で一意に解決できることを確認する。続けて`git -c grep.lineNumber=true grep -h -m 1 -F -e <固定文字列> -- <追跡ファイル>`と、`-h`を`--no-line-number`へ置き換えた検索の出力を比較する。

## agent-toolkit/rules/02-agent-operations.md：testの終了状態の表示：2026年9月24日

2026年9月24日、Claude Code 2.1.280のBashツールでは、実在するパスと存在しないパスへの`test -e`がともに`(Bash completed with no output)`を返した。同日のAWI `20260924-032304-001`が両呼び出しを記録する。bash 5.2.15で`test -e /dev/null; echo "test_e_rc=$?"`は標準出力へ`test_e_rc=0`を返し、存在しない`/dev/__agent_toolkit_audit_absent__`では`test_e_rc=1`を返した。再検証はClaude CodeのBashツールで同じ2種類のパスへ`test -e`と表示付きの起動形をそれぞれ渡し、ツール表示と標準出力の値を対比する。

## agent-toolkit/rules/02-agent-operations.md：規範の全文取得：2026年9月25日

2026年9月25日、Claude Code 2.1.282で`agent-toolkit/skills/wi-standards/SKILL.md`の309行・52,347バイトを`Read`の`offset=1, limit=309`で取得すると、末尾まで届いた。同じファイルを`cat <絶対パス> | cat`で取得すると、Bashは表示上限を超えた全量をセッション内のファイルへ保存した。保存物は309行・52,347バイトで原本と一致した。Claude Codeの[tools仕様](https://code.claude.com/docs/en/tools.md)の「Read tool behavior」は、上限超過時に`PARTIAL view`と先頭ページを返し、`offset`と`limit`で続きを取得する方式を説明する。同仕様の「Bash」は、表示上限を超えた結果をセッション内のファイルへ保存してパスを返す。2026年9月21日のCodexでは、複数文書の全文取得を1つの実行セルへ集約した結果、15,105トークンで出力が切り詰められ、個別再取得を要した。この観測時のCodexのホスト版番号は記録されていない。再検証では両ホストへ同一の大容量エージェント向け文書を与えて全文読取とBashの単純全文読取を実行する。hook通知、ホストの部分取得通知、保存物の末尾及び容量を比較する。

## agent-toolkit/skills/commit/references/git-identifier.md：revision件数とshell引用：2026年9月20日

2026年9月20日、Git 2.43.0で`git rev-parse --short=7 HEAD HEAD~1`が標準エラーへ`fatal: Needed a single revision`を書いて終了コード128となることを確認した。PowerShell 7.6.0では、未引用の`git rev-parse --verify HEAD^{commit}`が同じエラーと終了コード128を返し、単一引用符で囲んだ`git rev-parse --verify 'HEAD^{commit}'`が完全OIDと終了コード0を返した。再検証は同じrepositoryで1件と2件のrevisionを渡した`--short=7`の終了状態を比較し、PowerShellでpeel式の引用有無によるGitの受理結果を比較する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機対象未登録：2026年9月20日

2026年9月20日、`atk agents wait`が`agents_server`の状態投影に対象を持たない状態を終了コード10で報告し、実行ホストの組み込み委譲は同じ状態投影へ登録されないことを確認した。再検証は`agents_server` sessionと組み込み委譲をそれぞれ起動し、`atk agents list`への登録有無、`atk agents wait`の終了コード及びホストの委譲一覧が返すstatusを比較する。

## agent-toolkit/rules/99-claude-code.md：ツールAPIと権限：2026年9月1日

2026年9月1日、ツール呼び出しだけで地の文を持たない応答に対して`Your previous response had no visible output`が返ることを確認した。再検証は地の文を持たない応答を1回発行し、当該要求の有無を確認する。

## agent-toolkit/rules/99-claude-code.md：役割上の区分と実行環境上の区分：2026年9月4日

2026年9月4日、agent-toolkit 2.94.0で通常起動と軽量起動の設定読込先およびStop系フックの動作を検証した。再検証は`agents_server`の通常起動と軽量起動を比較する。

## agent-toolkit/skills/process-wi/references/run-lanes.md「統合とAWI終端」：所有資源の回収：2026年9月3日

2026年9月3日にgit version 2.43.0で、upstreamを設定した専用branchをローカルの`develop`へ統合した直後に`branch -d`が未統合として拒否されることを確認した。再検証は`git branch -vv`で追跡先を確認して同じ状態の`branch -d`の終了コードを観測する。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年8月31日

2026年8月31日から9月2日までの5セッションの観測で、出力トークン量はレーン数へほぼ比例し、レーン数を増やしたセッションで所要時間が短くなる傾向は観測されなかった。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年9月2日

2026年9月2日のセッションで、担当7件のレーンは同じセッションの担当4件の3レーンを1件あたりのトークン量と所要時間の双方で下回った。再検証は同一条件のレーンを比較する。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年9月3日

2026年9月3日のセッションでは、担当7件のレーンの実装担当の直列時間が8479秒に達し、セッション全体の所要時間が目標を64.1分超過した。再検証はレーンごとの担当件数と実装担当の直列時間を比較する。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント`https://code.claude.com/docs/en/agent-sdk/user-input`の`Question format`節、`Response format`節及び`Limitations`節で確認した。再検証は同じ3節を読む。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年8月31日

2026年8月31日、Claude Code v2.1.251・Fable 5で、ツール結果と次のツール呼び出しの間へ置いた地の文が`· summarized`付きの短縮文へ置換されることを確認した。当該回ではターン冒頭と`AskUserQuestion`直前の地の文が原文どおり表示された。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月3日

2026年9月3日、Claude Code v2.1.258・Opus 5では、複数のツール呼び出しの間へ置いた地の文の同じ置換をユーザーが端末表示で確認できなかった。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月5日

2026年9月5日、Fable 5.1で`AskUserQuestion`直前の地の文へ選択肢の前提を置いた回では、ユーザーが当該地の文を読めず判断できないと回答し、確認の再発行を要した。当該回のClaude Codeの版数は記録していない。再検証はモデルと版ごとに、ターン冒頭、ツール呼び出しの間及び`AskUserQuestion`直前の3箇所へ地の文を置き、表示を確認する。

## agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月4日

```text
2026年9月4日、Claude Code 2.1.260で、Bash背景ジョブの稼働中はデバッグログへ`[goal] evaluation deferred — background work still running`が出て評価が発動せず、背景ジョブを起動しない指示では評価が発動することを実測した。再検証は`claude --debug-file <path> --print --session-id <uuid> --permission-mode auto --allowedTools Bash -- '/goal <条件>。まずBashツールをrun_in_background=trueで使い sleep 60 を実行し、その後は追加作業をせずターンを終えること'`を実行して当該行の有無を確認し、背景ジョブを起動しない指示でも同じコマンドを実行し、評価の発動を対にして確認する。
```

## agent-toolkit/skills/delegation/references/runtime-routing.md：modelとreasoning effort：2026年8月

本項は軽量モデルへreasoning effort `max`を割り当てた大きな作業で自動コンパクションが10回発生し、所要時間が大幅に伸びた観測に基づく（2026年8月、ユーザー報告）。再検証は同じ組合せで同規模の作業を1件実行し、自動コンパクションの発生回数を観測することによる。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月

2026年9月、Claude Codeで`agents_server`の`wait`が発行から120秒で背景タスクへ移り、完了時に当該タスクの通知として結果本文が届くことを確認した。再検証は`wait`を発行し、背景移行の通知を受領した後に完了通知の到達を確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月3日

2026年9月3日から9月4日にかけて、`agents_server`の`start`で起動した委譲先が外部コマンドの完了待ちを表明して`status: completed`で終端し、当該コマンドの終了後も再開しない事象を観測した。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月4日

2026年9月4日には、同じ方法で起動した委譲先が自ら起動した委譲先の終端を待たずに待機表明で終端し、呼び出し元が保持した`session_id`へ`send_message`を送って再開させた事象を観測した。再検証は`agents_server`で委譲先を起動し、待機表明で終端させたうえで、完了通知の到達を確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月7日

2026年9月7日から9月8日にかけて、Codexで動く主体が発行した`wait`が`timed out awaiting tools/call after 300s`で失敗する事象を確認した。再検証は当該上限より長く稼働する委譲先へ`timeout`を省略した`wait`を発行し、当該失敗の有無を確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月14日

2026年9月14日、`atk agents wait`が通知だけを返した後に状態投影を削除し、同じコマンドを再発行してから終端結果を公開すると、書込主体別の待機対象登録簿から同じsessionを復元して終端結果を回収することをテストで確認した。結果を保持しない`stop`、session登録簿の`missing`、破損した待機対象登録の各ケースで、回収不能と確定した待機対象を解放することもテストで確認した。

再検証では`agents_wait_test.py`の登録簿のテスト3件と、`agents_server_mcp_test.py`の`test_stop_releases_wait_target_before_waiting_for_new_result`を実行する。通知後の再発行で同じ`session_id`の終端結果が返ることを確認する。明示破棄又は喪失確定後は、旧sessionが待機対象に残らないことも確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月4日

2026年9月4日、Claude Code 2.1.260で次の2点を確認した。終了コード7で終わる`run_in_background=true`のBashについて、起動結果が返した出力ファイルは出力の全量と`[exited with code 7]`を保持した。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月2日

2026年9月2日に別のセッションが起動した背景ジョブの実行識別子は`TaskOutput`が`No task found with ID`で拒否し、当該ジョブの出力ファイルは絶対パスのまま全出力と`[exited with code 0]`を保持していた。再検証は同じ形で背景実行して当該ファイルを読み、終了済みセッションが残した出力ファイルの絶対パスの読み取りと、同じ実行識別子が解決しないことを対にして確認する。

## agent-toolkit/skills/plan-mode/references/plan-file-standards.md：実装資料と完了条件：2026年9月7日

2026年9月7日、git 2.43.0で`assert expanded_common_job_count + len(statusline_jobs) == 7`を検索する`git grep -n`が一致0件を返し、同じ文字列を検索する`git grep -nF`が1件返すことを確認した。再検証は正規表現のメタ文字を含む行を対象リポジトリの追跡ファイルから1件選び、`git grep -n`と`git grep -nF`の一致件数を比べる。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節と`SubagentStop`節で確認した。再検証は同2節を読む。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日

2026年9月6日、Claude Code 2.1.263で次を確認した。地の文を1文書いた直後に同じ応答で`Bash`を1回呼ぶ指示を与え、当該呼び出しの`PreToolUse`が受領した`transcript_path`の内容を捕捉した。発火時点の当該JSONLはアシスタントのエントリを1件も持たなかった。実行後の同じJSONLには、同一の`message.id`を持つ思考ブロック、テキストブロック及びツール呼び出しの3エントリが並んでいた。再検証は同じ指示を与えて発火時点のJSONLの内容と実行後の内容を比較する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日

2026年9月8日、Claude Code 2.1.263で次を確認した。存在しないリポジトリを指す`git -C /tmp log --oneline -1`は終了コード128で終わり、当該セッションの状態ファイルの`git_log_checked`は未設定のままだった。続けて実在するworktreeを指す同じ形の`git log`を実行すると、当該cwdのキーが真になった。再検証はこの2つのコマンドを単独で順に実行し、`{tempdir}/claude-agent-toolkit-<session_id>.json`の`git_log_checked`を前後で比較する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルへ埋め込まれたパターンマッチ関数が、値が空文字列と`"*"`のいずれかのときに正規表現へ変換せず一致を返すことと、同ドキュメントが同じ3分類を記載することを確認した。再検証は当該ドキュメントの`Matcher patterns`節を取得し、`strings`で抽出した当該関数が空値と`"*"`を短絡することを確認する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：出力フィールドの使い分け：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルと公式のHooksリファレンスで確認した。再検証は同じ2つの資料で当該文字列の出所を確認する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：Stop/SubagentStopフックの再帰呼び出し対策：2026年9月4日

2026年9月4日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節、`Stop`節及び`SubagentStop`節で前段の入力仕様を確認した。同日、Claude Code 2.1.260のStopフックへ渡る入力を捕捉した。`run_in_background`で起動したBashジョブが、`type`を`shell`、`status`を`running`とする要素として`background_tasks`へ現れた。再検証は同3節を読み、Stopフックへ渡る入力を捕捉して`background_tasks`の有無と要素の構造を確認する。

## agent-toolkit/skills/writing-standards/references/python.md：実行環境：2026年8月17日

2026年8月17日、Linux・uv 0.12.3で、依存メタデータが同一でパスだけが異なるPEP 723スクリプトを`uv run --script`で実行すると、スクリプトごとにvenvを再構築することを確認した。パッケージと解決結果のキャッシュは共有され、ウォーム状態での再構築は1秒未満だった。再検証は同じ`# /// script`ブロックを持つスクリプトを2つのパスへ置き、`uv --version`を記録したうえで順に実行し、uvのキャッシュディレクトリ配下の環境の数と所要時間を比べる。

## agent-toolkit/skills/writing-standards/references/sub-agents.md：frontmatter：2026年8月19日

2026年8月19日、`skills`で宣言したスキルの本文がサブエージェントへ注入される際に、所在ディレクトリの絶対パスの表示が付随することをサブエージェントの記録で確認した。公式ドキュメントはこの表示を記載していない。再検証は`skills`を宣言した定義から起動したサブエージェントの記録を読み、注入されたスキル本文の直前又は直後に所在の絶対パスが現れるかを確認する。

## agent-toolkit/skills/writing-standards/references/sub-agents.md：コンテキスト境界：2026年9月13日

2026年9月13日時点で、`main`と名前付きエージェントを列挙した名簿はClaude Code v2.1.206以降の版で付くことを確認した。再検証は`tools`に`SendMessage`を含む定義を、ほかの名前付きエージェントが稼働するセッションから起動し、サブエージェントの記録に名簿が現れるかと`claude --version`の版数を確認する。

## agent-toolkit/hooks/hooks.json：SessionEndの非同期化：2026年9月24日

Claude Code 2.1.281を`--plugin-dir`で作業ツリーのプラグインから2回起動した。debugログはどちらも`SessionEnd:other`を非同期hookとして登録し、予算を`600000ms`と記録した。hookの完了状態は2回とも0で、事前に各セッションIDへ登録した管理対象一時領域は終了後に実在しなかった。標準エラーは空で、debugログにも`Hook cancelled`は現れなかった。CLI本体は2回とも終了コード143で、経過時間は11.60秒と16.45秒だったため、全プラグイン構成での通常応答の完了は未確認である。非同期hookの実行時間もdebugログからは分離できない。

同日、`hooks.json`のSessionEnd登録と同じコマンド・`async: true`だけを一時設定へ写し、`--setting-sources ''`と`--settings`で指定した`claude -p`を実行した。CLIは終了コード0で、結果JSONは`terminal_reason: completed`、`result: OK`だった。標準エラーは空で、debugログに`Hook cancelled`はなく、`SessionEnd:other`を非同期hookとして登録した記録、`600000ms`の予算及び完了状態0があった。同一`session_id`で事前登録した管理対象一時領域はCLI終了後に実在しなかった。この検収はSessionEnd単独構成の正常終了を示す。全プラグイン構成を用いた同日の追加試行も終了コード143だったため、その正常終了は引き続き未確認である。再検証では`claude -p`へ同じSessionEnd設定、専用の`--session-id`、`--debug-file`を指定し、CLIの終了コードと結果JSON、標準エラー、debugログの登録・完了状態・予算・`Hook cancelled`の有無を確認する。2026年9月26日以降のSessionEnd hookはセッション単位の領域を削除しないため、同IDで作成した領域の終了後の不在はhookの完了の観測に使わない。

2026年9月24日、Claude Code 2.1.281を作業ツリーのagent-toolkitを`--plugin-dir`で読み込む全プラグイン構成で再検証した。`claude -p`に専用の`--session-id`、`--debug-file`、`--output-format json`を指定し、標準入力を`/dev/null`へ接続した。外側に時間制限は設けなかった。

先の終了コード143は、検証元から継承した`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`と`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`による。再現試行の`strace`はagent-toolkitのStop hookの子プロセスからCLI本体への`SIGTERM`送信を記録した。debugログにも無進捗判定と常駐ループへの中断要求があり、SessionEnd hook自体は完了状態0だった。

常駐ループ用の環境変数を外し、同じ全プラグイン構成でCLIを起動すると、7秒で終了コード0となった。結果JSONは`terminal_reason: completed`、`result: OK`、`is_error: false`を返した。標準エラーは空で、debugログは`SessionEnd:other`を非同期hookとして登録し、予算`600000ms`と完了状態0を記録した。`Hook cancelled`はdebugログと標準エラーの双方に無かった。同じ`session_id`で事前登録した管理対象一時領域は終了後に実在せず、`atk managed-temp list --prefix sessionend-probe`も該当0件を返した。再検証では常駐ループ用の環境変数を検証用CLIへ継承させない。

## agent-toolkit/skills/writing-standards/references/dependency-management.md：バージョン指定と更新：2026年9月16日

2026年9月16日、公開直後の新バージョンを待つ目安1日の典拠を確認した。`.chezmoi-source/dot_config/uv/uv.toml`は`exclude-newer = "1 day"`を持ち、同ファイルのコメントが公開後24時間未満のパッケージを除外する目的を示す。再検証は同ファイルの当該キーの値を取得する。再検証の契機は当該設定値の変更とする。

## agent-toolkit/skills/writing-standards/references/dependency-management.md：pnpm：2026年9月16日

2026年9月16日、pnpm 11.25.0とnpm 11.19.0で検証した。
環境変数`NPM_CONFIG_REGISTRY`へ`https://example.invalid/`を与えて`pnpm config get registry`を実行すると`https://registry.npmjs.org/`を返した。
同じ環境変数で`npm config get registry`を実行すると`https://example.invalid/`を返した。
再検証は両ツールの当該版へ同じ環境変数を与えて実効のレジストリー設定を取得し、反映の有無を比べる。
再検証の契機はpnpm又はnpmの更改とする。

## agent-toolkit/skills/writing-standards/references/drizzle.md：H1直下：2026年9月16日

2026年9月16日、参考実利用バージョンの取得元を確認した。`~/glatasks/package.json`は`drizzle-orm`を`^0.45.2`、`drizzle-kit`を`^0.31.10`で指定する。再検証は同ファイルの当該2つの依存の版指定を取得する。再検証の契機は当該依存の更新とする。

## agent-toolkit/skills/writing-standards/references/llm-characteristics.md：知識の想起：2026年9月16日

2026年9月16日、本リポジトリのHEAD `2ee94027`でコーパス分析とA/B実験を実施した。

コーパス分析の対象は、`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`、`.chezmoi-source/dot_claude/`、`.claude/skills/`配下のMarkdownと`AGENTS.md`とする。
現行本文137ファイル・9850文のうち、否定形で終わる文は1481文（15.0%）、「当該」は1393回（1000文あたり141回）現れた。
由来の区分は`git log --format='%H%x09%an%x09%(trailers:key=Co-Authored-By,valueonly)'`で全3578commitのトレーラーを取って行った。
`Codex`を含むものをCodex由来（107件、いずれも2026年8月）、`Claude`を含むものをClaude由来とした。
Codexが委譲先として書いた変更はClaude名義で記録されるため、Codex由来の件数は下限である。
追加文の抽出は各commitの`git show --format= --unified=0 -- '*.md'`の追加行から行い、コードブロック・表・見出しを除いて「」で分割した。
追加文1000文あたりの推移は、Codex利用前のClaude由来、2026年8月のCodex由来、2026年9月のClaude由来の順に次のとおりであった。
「`当該`」23→55→163、「`主体`」0.6→7.6→17.5、「`正本`」0.4→10.4→35.4、「`契約`」0.2→8.3→11.0。
「`終端`」0.5→2.5→36.7、「`厳守規定`」0.8→5.6→18.2、「〜を根拠にしない」型2.9→7.8→10.2。
「〜だけを…しない」型0.7→6.8→6.3、否定形終端文の割合5.0〜8.0%→10.9%→15.7%。

A/B実験では、`agent-toolkit/rules/01-agent.md`の「判断指針」と「完遂と先送り」（112行）を条件Aの抜粋とし、同じ内容を平易な肯定形へ書き換えた99行を条件Bの抜粋とし、抜粋を読ませない条件Cを対照とした。
抜粋の否定形終端率はA19.5%・B12.1%、「当該」はA34回・B0回、「厳守規定」はA7回・B0回である。
課題はスキルの1節の起草（10〜20文）、バグ修正方針の指示文（300〜600字）、ユーザーへの完了報告（200〜400字）の3件とし、各条件3回ずつClaude Opusの委譲先へ与え、条件を隠した匿名のファイル名で別の委譲先へ採点させた。
修正方針の課題では、範囲外の追加要素がA4.67件・B2.00件・C1.67件、過剰設計度がA3.00点・B2.00点・C2.33点であった。
成果物の否定形終端率は、スキル起草の課題でA17.9%・B9.0%・C22.9%、修正方針の課題でA30.5%・B16.5%・C27.6%であった。
反復は3回であり、統計的な検定は行っていない。完了報告の課題では条件間の差は現れなかった。

再検証は`uv run --frozen python scripts/check_agent_doc_tone.py --report <対象ファイル>`で現行本文の指標を取得し、上記の値と比べる。
再検証の契機は、規範文書の一括改訂と、文体の閾値の見直しとする。

## agent-toolkit/skills/writing-standards/references/notation-rules.md：逐語引用の検出範囲：2026年9月5日

本表は2026年9月5日に実際に動かして確かめた結果である。次の1文を地の文、引用ブロック、フェンス付きコードブロックへ置いた3つのサンプルファイルを作成し、pyfltr 3.17.8の`textlint`・`colloquial-check`にかけた。

```text
警告を出すと思う。
```

地の文では口語表現チェックとtextlintの弱い表現がいずれも検出され、引用ブロックではtextlintの弱い表現だけが検出され、フェンス付きコードブロックではいずれも検出されなかった。em-dash（U+2014）を含む同じ形のサンプルを`scripts/check_dash.py`にかけたところ、引用ブロックでは検出され、フェンス付きコードブロックでは検出されなかった。再検証は同じ3つのサンプルファイルを再度作成し、同じ手順で検出の有無を対にして確認する。

## agent-toolkit/skills/writing-standards/references/session-records.md：H1直下：2026年9月2日

2026年9月2日に`~/.claude/projects`配下と`~/.codex/sessions`配下の記録で確認した。再検証は同じ2箇所を当該キーで検索する。

## agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日

本節の記述は2026年9月3日に`agent-toolkit/skills/session-review/scripts/session_review_evidence.py`の`_latest_claude_usages`と`_stats_summary_data`を読んで確認した。再検証は同じ2つの関数を読む。

## agent-toolkit/skills/writing-standards/references/sqlalchemy.md：autoflushと問い合わせ順序：2026年9月14日

2026年9月14日、SQLAlchemy 2.0.52の公式文書で、既定構成の`Session`がORM対応の問い合わせ前に保留変更をflushすることと、`Session.flush()`で明示的にflushできることを確認した。
2026年9月13日のAWIは、SQLAlchemy 2.0.51を使うアプリケーションで、保留中のUPDATEが一意制約へ違反するケースと、保留中のINSERTが`NOT NULL`制約へ違反するケースを確認した記録を持つ。
同記録ではautoflushの無効化が同じ処理単位で追加した設定を読むテストを失敗させ、入力検証前の無条件な問い合わせが`Session`未開始のテストを失敗させた。
再検証ではSQLAlchemy 2.0系の公式文書にある`Session Basics`の`Flushing`節と`Session.flush()`のAPI説明を確認する。
あわせて、保留中のUPDATEとINSERTの後にORMへ問い合わせるテストで、問い合わせ前に各制約違反が送出されることを確認する。
問い合わせを属性代入前かつ入力検証後へ移し、保留変更の反映が必要な箇所だけ明示的にflushした状態で、入力エラーと同じ処理単位の読み取りを対にして確認する。

## agent-toolkit/skills/writing-standards/references/textlint-violations.md：文体と箇条書き：2026年9月10日

2026年9月10日、pyfltr 3.17.9のtextlintと本リポジトリの`.textlintrc.yaml`（`preset-jtf-style`の`1.1.3.箇条書き`を`shouldUsePoint: false`で運用する設定）で確認した。
全ての項目が句点で終わる順序付きリストは同ルールへ一致0件であった。
同じリストの1項目へ空行で区切った入れ子の箇条書きと段落を追加すると、句点で終わる他の4項目へ箇条書きの文末から句点を外す指摘が返った。
入れ子を独立した節へ移して各項目を1行に戻すと、再び一致0件であった。
再検証は同じ順序付きリストについて入れ子を含む写しと含まない写しを作成し、`--commands=textlint`を指定した同じコマンドで一致件数を比べる。

## agent-toolkit/skills/writing-standards/references/session-records.md：スキル起動の判定：2026年9月10日

2026年9月10日に確認した。`~/.claude/projects`配下でClaude Code 2.1.252と2.1.267の記録を読んだ。`Skill`ツールの`tool_use`要素が`input.skill`にスキル名を持つことを確認した。対応する`tool_result`要素の`content`は`Launching skill: <スキル名>`と一致した。
同日、`~/.codex/sessions`配下のrollout記録2169件のうち`agent-toolkit:exit-session`を含む1527件を対象に、先頭120件のレコード種別を集計した。当該文字列は`world_state`、`developer`ロールの`message`、`compacted`、`function_call_output`及び`custom_tool_call_output`だけへ現れた。
同日、codex-cli 0.154.0の同じ2169件に対し、`atk wi process-loop`がCodexへ渡す起動プロンプトの完全一致を数えた。一致は0件であった。いずれの記録も最初のuser役レコードの本文は実行環境が挿入する前置きであった。前置きは``# AGENTS.md instructions``又は``<recommended_plugins>``で始まる。`agent-toolkit:process-wi`をuser役の本文へ含む記録は304件であった。当該304件の`session_meta`の`originator`は`agent-toolkit-codex-app-server`が303件、`codex-tui`が1件であった。
再検証はClaude Codeの記録から`Launching skill:`を含む行を1件取得して`tool_result`の構造を確認し、Codexの記録から同じスキル名を含む行を取得してレコード種別を確認する。あわせてCodexの記録から当該起動プロンプトの完全一致と包含の件数を数え、user役レコードの`text`の先頭が前置きであることを確認する。

## agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py：UserPromptSubmitの出所欄：2026年9月17日

2026年9月17日に確認した。Claude Code 2.1.274の実行ファイルは、UserPromptSubmitの入力スキーマへ`source`を宣言し、値を`user`、`sdk`、`system`、`loop_wakeup`、`schedule_wakeup`、`poll_event`の6種とする。
同スキーマの説明は`schedule_wakeup`を`scheduled-task fire (CronCreate/routine)`と定める。
`system`は`other machine-injected turns (peer/channel messages, task notifications, auto-continuation)`と定める。
説明の末尾へは`Payloads may omit it while the field rolls out.`と記す。
一方、同じ実行ファイルでUserPromptSubmitの入力を組み立てる2箇所は、いずれも`prompt`の直後に`...!1`を持ち、当該位置の代入が畳み込まれている。同じ実行ファイルのSessionStartの入力組み立ては`source:n`を持つため、当該位置の`...!1`は出所欄の欠落を示すと判定した。2.1.272と2.1.273も同じ形である。
確認の範囲は、この3版が出所欄を配送しないこととする。配送を開始する版と時期は確認していない。
この観測により、出所欄だけを判定入力とする案は現行版で成立しないため、機械注入ターンの判定を4系統で構成した。
再検証は対象版の実行ファイルに対し`hook_event_name:"UserPromptSubmit"`の前後の文字列を取得し、当該位置に`source`の代入が現れるかを`hook_event_name:"SessionStart"`の同じ箇所と対にして確認する。

## agent-toolkit/skills/writing-standards/references/claude-hooks.md：Bash失敗の分類：2026年9月14日

2026年9月14日、Claude Code 2.1.270のPostToolUseFailure入力で、失敗ツール名を`tool_name`、中断状態を`is_interrupt`、エラー本文を`error`として取得できることを確認した。Bashの非ゼロ終了では`error`の先頭行が`Exit code N`となる。再検証は同版以降で終了コードを変えたBash失敗と中断を発生させ、PostToolUseFailureへ渡る3項目と先頭行を記録して確認する。

## agent-toolkit/agent_toolkit/_atk/config.py：Antigravity CLIのモデル指定：2026年9月18日

2026年9月18日、Antigravity CLI 1.2.5（`/home/aki/.local/bin/agy`）で確認した。
`agy models`は終了コード0で14件を返し、各行は推論の深さを含む完全スラッグと表示名をタブで区切る。完全スラッグは`gemini-3.8-flash-high`、`gemini-3.8-flash-medium`、`gemini-3.8-flash-low`のように、ベース名へ`-high`・`-medium`・`-low`を付けた形である。`gemini-3.1-pro`は`-high`と`-low`だけを持ち、`claude-sonnet-4-6`・`claude-opus-4-6-thinking`・`gpt-oss-120b-medium`は当該接尾辞の分岐を持たない。
`--model`は推論の深さを含まないベース名も受理する。
`agy -p 'reply with OK only' --model gemini-3.8-flash`は終了コード1で終わった。
標準エラーの本文は次のとおりである。

```text
error: invalid model selection (--model "gemini-3.8-flash" --effort ""): --model gemini-3.8-flash requires --effort (available: low, medium, high)
```

同じ呼び出しへ`--effort low`を加えると終了コード0で終わり、標準出力へ`OK`だけを書いた。
この検証により、`--model`へベース名を渡し`--effort`を別に渡す`agent_toolkit/_agents_server/antigravity.py`の`build_command`の形が、現行版で成立することを確認した。
再検証は`agy models`の出力から完全スラッグの接尾辞の有無を確認し、`agy -p 'reply with OK only' --model <ベース名>`を`--effort`の有無で1回ずつ実行して終了コードと標準エラーを比べる。

2026年9月23日、Antigravity CLI 1.2.9で非対話実行の時間指定を確認した。
`agy --help`の`--print-timeout`は既定値を`0s`と示す。`--print-timeout 3600`は単位不足として拒否された。
次のコマンドは終了コード0となり、`init`、`step_update`、`result`のイベントを返した。

```sh
agy -p 'reply with OK only' --model gemini-3.8-flash --effort medium --output-format stream-json --print-timeout 3600s
```

2026年9月24日の同版の出力では、`result`イベントの`status`と`response`は内側の`result`オブジェクトにあり、状態値は`SUCCESS`、本文は`OK`であった。作業ツリーの`start_custom`で同じ候補を起動した結果もagyのsessionが`completed`となり、待機応答の本文に`OK`を受け取った。
同日、同版へ「検証完了」とだけ答える指示を与えた。`--output-format stream-json --print-timeout 120s --model gemini-3.8-flash --effort low --mode plan --disable-slash-commands`で実行すると、終了コード0で5行のJSONLを返した。イベントは`init`が1行、`step_update`が3行、`result`が1行で、最後の`result.response`は`検証完了`だった。`step_update`の2行には`text_delta`があり、公開ストリームに表示用の本文が含まれることを確認した。標準エラーは1行で、slash command expansionを無効にした状態では`--mode plan`が適用されない旨を示した。
再検証は`agy --version`で版数を記録し、同じ指示で`--print-timeout`を`3600`と`3600s`へ変えて終了コード、標準エラー及びイベント種別を確認する。

## agent-toolkit/skills/delegation/references/codex-runtime.md：Codexネイティブ委譲の入力境界：2026年9月21日

2026年9月21日、Codex CLI 0.155.1で確認した。`spawn_agent`は`fork_turns`で会話履歴の引継ぎ範囲を選ぶ。起動した委譲先は対象worktreeの`AGENTS.md`と`SubagentStart` hookの追加本文を別入力として受け取る。`send_message`は稼働中の主体への追加配送、`followup_task`は同じ主体の継続、`wait_agent`は終端待機、`interrupt_agent`は中断を担う。agent-toolkitのCodex hookを実起動したテストでは共通の`rules-subagent.md`が追加され、Claude Code固有の`rules-subagent.claude-code.md`は追加されない。agents_serverの通常委譲も共通規範を持つ一方、軽量な探索・書込・shell promptは持たない。
再検証では`codex --version`で対象版を記録する。`rules_context_codex.main`へ`SubagentStart`を入力するテストと、`_agents_server/state.py`の通常・軽量promptのテストを実行する。生成したCodex hook manifestの`SubagentStart`起動コマンドも実行し、共通規範、ホスト固有規範及び軽量委譲の境界を確認する。

## agent-toolkit/rules/01-agent.md：自動挿入本文の配送境界：2026年9月25日

2026年9月25日、Claude Code 2.1.281で新しいセッション`0b228cab-e106-4b5e-805b-f5e88320691c`を起動し、`--include-hook-events --output-format stream-json`でhook応答を保存した。SessionStartの`additionalContext`は`<agent-toolkit-auto-inserted source="agent-toolkit/rules_context" kind="notice">`で始まった。存在しないパスを指定したBash検索に対するPreToolUseの`additionalContext`は`<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">`で始まった。両応答の`exit_code`は0だった。検証用セッションはhook応答を得た後に中断したため、セッション全体の完了結果はこの観測の根拠に含めない。
再検証するときは、同版以降で`--include-hook-events`を付けて新しいセッションを起動する。SessionStartと、`uv.lock`を`Write`で書き込むPreToolUseの`hook_response.output`を読む（存在しないパスへの警告は2026年9月26日に撤去した）。各応答の外側境界にある`source`と`kind`が上記と一致するか確かめる。
