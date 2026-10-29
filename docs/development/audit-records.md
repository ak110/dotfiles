# 監査記録

本ファイルの収録対象は、エージェント向け文書（`agent-toolkit`の規範文書・タスク文書と本リポジトリのプロジェクトスキルなど）の条文、それを実装するコード（コメントとdocstring）および設計記録が根拠とする観測である。
そのうち対象の挙動を現物で観測して確かめたものについて、確認した日付、観測した版数および再検証の手段を保持する。
条文の側には観測事象だけを置き、本ファイルのH2見出しで索引する。
条文が前提とする挙動と異なる観測を得た場合は、その条文が指すH2見出しを読む。記載された手段で再検証してから、条文の失効を判定する。
H2見出しは索引元の条文が指す文字列と一致させる。索引元の条文を移設し、または索引元の節名を改める改訂では、同じ改訂で本ファイルのH2見出しと索引元の参照を併せて改める。
本ファイルは`agent-toolkit`の配布物に含まれない。索引を辿れるのは、本リポジトリの作業ツリーを持つ主体に限る。

H2見出しは`<索引元のパス>：<節名>：<日付>`の形で書き、索引元のパスを持たない記録は`<題名>：<日付>`とする。同じ見出しが重なる場合は日付の後に「（1）」「（2）」を付ける。
H2は索引元のパスの文字コード順に並べ、同じパスでは日付の昇順とする。パスを持たない記録はパスを持つ記録の後に題名の文字コード順で置き、同じ題名では日付の昇順とする。日を持たない日付（`2026年8月`など）はその月の最初に置く。記録を追加するときは、この並び順の位置へ置く。

## .claude/skills/agent-toolkit-edit/references/agents-server-shared-state.md：実行主体：2026年10月18日

会話contextの継承に関する2026年10月16日の調査記録と、2026年10月18日の実stdio MCPでの変更前後の観測を分けて示す。
元の調査の一次記録と今回の観測JSON・標準エラー・実行スクリプトは、リポジトリ外に保持する。

2026年10月16日の調査記録は、Claude Code 2.1.301のstdio MCP起動処理が自身の環境と`CLAUDE_CODE_SESSION_ID`を渡すことを本体で確認している。
同記録によると、Claude Agent SDK 0.2.165は`os.environ`から`CLAUDECODE`だけを除き、`options.env`を上書きで合成する。
クラウド実行環境の委譲先でremote変数の継承と識別子の重複が観測され、最小環境の対照実験でもremote sessionを表す変数群を加えた2回の起動が同じ識別子になった。
この調査では`CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2`との単独の切り分けを実施していない。
2026年10月18日の観測は実MCPへ環境を明示して起動しており、ホスト自身によるstdio環境の注入は直接再観測していない。

今回の実MCP観測はClaude Code 2.1.302、Claude Agent SDK 0.2.165で行った。
`agents_server_mcp.py`をstdioで起動し、初期化とツール一覧取得の後、Claude engineの`start`を2件並行して呼び出した。
変更前後で専用の状態名前空間を分離し、次の変数を同じ条件で与えた。

- `CLAUDE_CODE_SESSION_ID`: 起動ごとの任意のUUID
- `CLAUDE_CODE_REMOTE`: `true`
- `CLAUDE_CODE_REMOTE_SESSION_ID`: `cse_`で始まる検証用の値
- `CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2`: `true`

変更前は2件の起動応答が同じ識別子を返し、`atk agents wait --root-session-id <応答のroot_session_id>`は終了1件を回収して終了コード0だった。
変更後は2件の識別子が互いと親の識別子から分かれ、回収前の`show`は両方で`result_available=true`を返した。
同じCLIで終了2件を回収し、終了コード0を得た。
両観測ともstdio接続を閉じてMCPの終了を確認した。

再検証では専用の状態ディレクトリを使い、上記の変数を与えた実MCPへ2件の並行`start`を送る。
起動応答の識別子を互いと親について比較し、終了時の`show`とCLIによる回収件数・終了コードを記録する。
変更前の版でも同じ条件で実行し、識別子の重複と回収1件を対照にする。
環境除去の単体検証は`_common/mcp_environment_test.py`と`_atk/orchestrator_test.py`を使う。
全backendの登録前の重複拒否は`_agents_server/manager_test.py`の`test_start_rejects_duplicate_session_id`で再検証する。

## .claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月4日

pyfltr 3.20.0の`pytest-fast-targets`へ`*_invariant_test.py`を指定した。指定した時点では横断テストをpytestのマーカー`repo_invariant`でも識別しており、マーカーでの収集と専用ファイルだけの収集は、ファイル名を除いた各nodeの多重集合が一致し、両側とも130件だった。クラス内の字下げされたマーカー1件も比較で検出して分離した。その後、fastがマーカーを選択に使わないため、マーカーを撤去してファイル名だけで識別する形にした。撤去後の確認では、fastのpytestが受け取ったファイルは33件で、`git ls-files`が返す`*_invariant_test.py`の33件と一致した。

再検証はrootで次を個別に実行する。

```sh
git ls-files -- '*_invariant_test.py'
uv run --frozen pyfltr fast --commands=pytest
uv run --frozen pyfltr fast --commands=pytest --pytest-args='--collect-only -q' --output-format=jsonl
uv run --frozen pyfltr show-run <run_id> --commands pytest --output --output-format=json
uv run --frozen prek run pyfltr --files docs/development/design-packages.md
```

3行目の出力の1行目（`"kind":"header"`）が示す`run_id`を4行目へ渡す。4行目が返すJSONの`output`に、pyfltrのsubprojectごとの`# subproject: <ディレクトリ>`の行と、収集したテストの`<ファイル>::<テスト名>`の行が並ぶ。各行のファイルへsubprojectのディレクトリ（`.`はroot）を前置した集合が、fastのpytestが受け取ったファイルの集合である。
この集合が`git ls-files`の一覧と一致し、通常の動作テストのファイルを含まないことを確認する。件数だけを比べると、対象のファイルが別のファイルへ置き換わった場合を見逃すため、集合を比べる。Markdownだけを渡したprekのhookからpytestが起動し、不変条件の失敗が同じhookの失敗へ届くことも確かめる。rebaseで組合せが変わった場合は、commit時の成功だけで判定せず、同じfastのpytestを統合前に再実行する。

## .claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月18日

Python 3.15、CIと同じimageのdigest `sha256:b13645f08610527422a741872c2457ee7b0e2ce96880acee22be2f1984477600`で、lockだけの変更によるsdistビルドの差を観測した。
基準commit `a151f22a2a3447cfc4925e2a708d279138de46f4`のarchiveへ、`uv.lock`のzstandard依存2行から`python_full_version < '3.14'`のmarkerだけを除いた入力を作成した。
両入力を同じcontainerの`poppler-utils libarchive13 zstd`導入後に`UV_FROZEN=1 UV_PYTHON=3.15 uv sync --all-groups --all-extras`へ渡した。
marker保持側は終了0、除去側はzstandard 0.25.0のビルドに到達して`FileNotFoundError: No such file or directory: 'cc'`で終了1となった。
現行commit `3c7276065cc081b9759f2702b7d149d3aaa06c7c`のlockにはzstandardが含まれないため、故障対照は基準版の入力として保持する。
同じ現行commitのarchiveも上記と同じ操作へ渡し、135パッケージの依存導入と終了0を確認した。

再検証では同じ基準版のarchiveから上記2入力を作成し、同じimage・ネイティブパッケージ・Python版・uvの同期引数を使う。
実行結果は基準版の正常導入とlock変更によるビルド失敗を区別して読む。
`ci_workflow_test.py::test_update_checks_container_dependencies_after_lock_updates`で依存更新の手順を確かめる。
`update`から`$(MAKE) ci-deps-check`を除いたMakefileでは、同じ試験が失敗することを確かめる。
`make -n update`では両lockの更新後に`ci-deps-check`の`docker run`が出力された。

## .claude/skills/merge-pr/SKILL.md：マージ前の確認事項：2026年10月5日

2026年10月5日、GitHub CLI 2.101.0でPR #138のcheck登録順を確認した。run `37327087407`は同じPR headの`push`起点CIであり、run `37329129743`は`pull_request`起点CIであった。後者の`statusline-version`は先行するcheckの観測後に非同期で登録された。同じheadとcheck名だけでは起点を識別できず、PR起点checkの登録前に必須checkの集合を完全と判定できない。

再検証では`gh version`を取得し、PR番号とその`headRefOid`を保持する。`gh pr checks <PR番号> --json event,workflow,name`でcheckを取得し、`event=pull_request`、`workflow=CI`、`name=statusline-version`の組合せを探す。run `37327087407`と`37329129743`を個別に取得し、event、head SHA、workflowとjob名を比較する。次に同じPRの必須check終了後の`headRefOid`と`mergeStateStatus`を取得し、headが不変でGitHubの評価が`CLEAN`になることを確認する。対象runが保持期限で取得できない場合は、新しいrelease PRで同じJSON項目を使って登録順を観測する。

## agent-toolkit/agent_toolkit/_agents_server/claude.py：結果の保留とturn状態の報告：2026年10月4日

2026年10月4日、Claude Code 2.1.289とClaude Agent SDK 0.2.163で、turn状態の報告を受け取れることを確かめた。
`ClaudeAgentOptions(env={"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"})`を与えたsessionでは、`ClaudeSDKClient.receive_messages()`へ`SystemMessage`が届く。その`subtype`は`session_state_changed`、`data["state"]`は`running`・`idle`・`requires_action`のいずれかである。この変数が無い場合、SDKは`CLAUDE_CODE_SDK_READS_SESSION_STATE=1`を設定して報告を呼び出し側から除く。
haikuの委譲先に`sleep 2`を背景実行させて直ちにturnを終えさせ、Stop hookの待機時間を変えて到着順を比べた。
待機0秒では次の順に届いた。`ResultMessage`、`idle`、`task_notification`、`running`、再開turnの`ResultMessage`（`origin.kind`が`task-notification`）、`idle`である。待機5秒では`task_notification`（Stop hookの実行中）、`ResultMessage`、再開turnの`ResultMessage`、`idle`の順に届き、最初の結果の後に`idle`は届かなかった。どちらも再開turnが実行された。CLIのスキーマ記述は`idle`を、保留した結果の送出と背景エージェントの待機の後に発行するturnの終了の信号と説明する。
同じ版の組で上記の環境変数を与えた委譲先へ背景実行の`sleep 2`と即時のturn終了を指示して再検証する。
Stop hookに0秒と5秒の待機を入れた2条件で、`receive_messages()`の全メッセージの種別、`subtype`、`state`、`origin`を到着順に保存して比べる。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：コンパクション計測通知：2026年9月10日

2026年9月10日、Codex CLI 0.153.4の`codex app-server generate-json-schema --out <管理対象一時領域の絶対パス>`が終了コード0で生成したJSON Schemaを検証した。`v2/ItemStartedNotification.json`は`item`・`startedAtMs`・`threadId`・`turnId`を必須とし、`startedAtMs`をitem lifecycle開始時のUnix時刻（ミリ秒）と定める。`v2/ItemCompletedNotification.json`は`completedAtMs`・`item`・`threadId`・`turnId`を必須とし、`completedAtMs`をitem lifecycle完了時のUnix時刻（ミリ秒）と定める。`ThreadItem`は`id`と`type`を必須とし、`type`が`contextCompaction`である`ContextCompactionThreadItem`を変種に持つ。再検証は同じコマンドで現行版のJSON Schemaを生成する。続いて、2つの通知の必須項目と時刻の説明、`ContextCompactionThreadItem`の必須項目を確認する。

## agent-toolkit/agent_toolkit/_agents_server/codex.py：孫sessionの待機表明と自動再開：2026年10月1日

2026年10月1日、codex-cli 0.159.3とClaude Code 2.1.286を同じホストで使い、変更後の作業ツリーで`agents_server_live_test.py::test_live_grandchild_wait_resumes_same_session`を実行した。委譲先は`agents_server`の起動ツールで`sleep 5`を実行する孫sessionを起動し、回収前に`待機中: <孫のsession_id>`を出力してターンを終える指示を受けた。
Codexの委譲先（`codex:sol/medium`）とClaudeの委譲先（`claude:sonnet[1m]/medium`）のいずれも、待機表明の結果は呼び出し元へ配送されず、孫の終了後に同じsessionが手動の指示なしに再開した。再開したターンは`atk agents wait`で孫の結果を回収し、呼び出し元は`AUTO_RESUME_COMPLETED`と`turn_seq`2の完了結果を受け取った。2件とも成功し、所要時間は141秒だった。
変更前のCodex backendは、同じ待機表明を`completed`の結果として公開し、孫の識別子を`error.unobservedSessions`へ記録していた（同日の対照観測）。
再検証は`make test-agents-live`と同じpytest引数（`-m live_agents`）を指定し、呼び出し元の会話と状態ディレクトリを分けるため別の`CLAUDE_CODE_SESSION_ID`を与えて同じテストを実行する。

## agent-toolkit/agent_toolkit/_agents_server/codex_resume_state.py：会話状態の生成先：2026年10月23日

Codex担当がCLI 0.164.0の同日rolloutで、先頭`session_meta.model_provider`、providerを持たない`turn_context`、`response_item`のreasoning内`encrypted_content`を観測した。対象は`rollout-2026-10-23T16-42-16-01a167b5-e496-7fd2-846b-2965d3c94ea0.jsonl`である。値を再掲せず、フィールドの所在と型を確認した。互換性検体はこの形を架空のIDと暗号文へ置換している。再確認時は当該版の実会話で生成したrolloutの`session_meta`と不透明状態を持つ応答のキーを読み、接続先選択と生成先の記録を分けて照合する。元障害の別ホストの原記録を再現したという証拠には用いない。

## agent-toolkit/agent_toolkit/_agents_server/launch_prompts.py：委譲先のベース指示：2026年10月23日

codex-cli 0.163.0の`codex app-server generate-json-schema --out <保存先>`でschemaを生成した。
生成された`v2/ThreadStartParams.json`と`v2/ThreadResumeParams.json`は、どちらも`baseInstructions`と`developerInstructions`を受理する。ベース指示の差替えと既存のdeveloper指示の配送を併用できる。
再検証は導入版を`codex --version`で確認し、同じ公開コマンドが生成する両schemaで行う。

Claude Agent SDK 0.2.170を使った`claude_test.py`では、通常委譲の`system_prompt`が
独自ベース本文・委譲先規範・自動再開通知・CLI情報を含む文字列であることと、軽量区分の設定の保持を確認した。
`codex_test.py`では全起動区分の開始・通常再開・復元再開に同じベース本文が入ることを確認した。
これらと`launch_prompts_test.py`、`dot_codex_config_test.py`の合計247件は成功した。
この観測は公開リクエストとSDK引数の構築を検証するもので、外部モデルによる回答内容の実測は含まない。

## agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日

2026年9月11日、`~/.claude/projects`配下の記録（Claude Code 2.1.239から2.1.268）で、`agents_server`のstart系ツールの呼び出し489件を集計した。
このうち233件は起動された子sessionの記録と対応付けられた。各件について、呼び出しのtool_useの時刻から子sessionの記録の先頭エントリの時刻までの差を求めた。
中位値は1.34秒、90%点は7.12秒、99%点は36.76秒であり、232件が47.65秒以内に収まった。残る1件は604.22秒であった。
同じ489件のうち7件は、tool_useから応答までの経過が1800.4秒から1800.5秒であり、ホストがMCPツール呼び出しを打ち切った回であった。
ホスト側の上限は、Claude Codeが`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`が未設定の場合に1800秒を課し、
Codexが<https://learn.chatgpt.com/docs/extend/mcp?surface=cli>の`tool_timeout_sec`が未設定の場合に60秒を課す。
再検証は同じ記録へ同じ集計を適用し、tool_useの時刻と子sessionの記録の先頭エントリの時刻の差の分布と、応答の打ち切りに達した件数を対比する。
上限値はこの分布に加えて、ホストがMCPツール呼び出しを背景タスクへ移す閾値を制約に持つ。
2026年9月、Claude Codeで`agents_server`の`wait`が発行から120秒で背景タスクへ移り、完了すると結果本文がそのタスクの通知として届くことを確認した。
背景移行の閾値の再検証は`wait`を発行し、背景移行の通知を受領した後に完了通知の到達を確認する。

## agent-toolkit/agent_toolkit/_atk/config.py：Antigravity CLIのモデル指定：2026年9月18日

2026年9月18日、Antigravity CLI 1.2.5（`/home/aki/.local/bin/agy`）で確認した。
`agy models`は終了コード0で14件を返し、各行は推論の深さを含む完全スラッグと表示名をタブで区切る。完全スラッグは`gemini-3.8-flash-high`、`gemini-3.8-flash-medium`、`gemini-3.8-flash-low`のように、ベース名へ`-high`・`-medium`・`-low`を付けた形である。`gemini-3.1-pro`は`-high`と`-low`だけを持つ。一方、`claude-sonnet-4-6`・`claude-opus-4-6-thinking`・`gpt-oss-120b-medium`には推論の深さごとに接尾辞を変える分岐が無い。
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
`agy --help`の`--print-timeout`は指定しない場合の値を`0s`と示す。`--print-timeout 3600`は単位不足として拒否された。
次のコマンドは終了コード0となり、`init`、`step_update`、`result`のイベントを返した。

```sh
agy -p 'reply with OK only' --model gemini-3.8-flash --effort medium --output-format stream-json --print-timeout 3600s
```

2026年9月24日の同版の出力では、`result`イベントの`status`と`response`は内側の`result`オブジェクトにあり、状態値は`SUCCESS`、本文は`OK`であった。作業ツリーの`start_custom`で同じ候補を起動した結果もagyのsessionが`completed`となり、待機応答の本文に`OK`を受け取った。
同日、同版へ「検証完了」とだけ答える指示を与えた。`--output-format stream-json --print-timeout 120s --model gemini-3.8-flash --effort low --mode plan --disable-slash-commands`で実行すると、終了コード0で5行のJSONLを返した。イベントは`init`が1行、`step_update`が3行、`result`が1行で、最後の`result.response`は`検証完了`だった。`step_update`の2行には`text_delta`があり、公開ストリームに表示用の本文が含まれることを確認した。標準エラーには、slash command expansionを無効にすると`--mode plan`が適用されないという説明が1行出た。
再検証は`agy --version`で版数を記録し、同じ指示で`--print-timeout`を`3600`と`3600s`へ変えて終了コード、標準エラーおよびイベント種別を確認する。

## agent-toolkit/agent_toolkit/_hooks/pretooluse/large_reads.py：全文取得の閾値：2026年9月29日

2026年9月29日、tiktoken 0.14.0のo200k_baseで`agent-toolkit/skills/`・`share/`・`rules/`配下のMarkdownを数えた。分割前の最大の文書`agent-toolkit/skills/wi-standards/SKILL.md`（60,005バイト）は16,945トークンだった。分割後の137件では、最大が13,176トークン（`skills/plan-mode/references/plan-file-standards.md`、45,749バイト）だった。1トークンあたりバイト数の最小は3.10（`skills/plan-mode/references/legacy-plan-file-standards.md`）だった。配布設定`share/codex_config.toml`（当時は`scripts/codex_config.toml`）の`tool_output_token_limit`は20000である。20,000トークンと3.10バイトの積は約62,000バイトであり、実行セルが本文へ付加する分の余裕を取って閾値を48KiB（49,152バイト）とした。48KiBの文書は最悪の比率でも約15,900トークンで、上限に対して約4,000トークンの余裕が残る。
再検証では同じ集合をo200k_baseで数えて1トークンあたりバイト数の最小値を測り直し、配布設定の`tool_output_token_limit`との積が閾値と付加分の余裕を上回るか確かめる。

## agent-toolkit/agent_toolkit/_hooks/stop.py：未配送通知を次ターンへ返す：2026年10月10日

Claude Code 2.1.296の非対話CLIで、goalなし・ありの専用sessionを1件ずつ起動した。
対象版は基準commit `fb5cbb72380af87aa26c302816b0c87efdb17004`へ本変更の未commit差分を加えた作業ツリーである。
配布するStopの起動コマンドを一時settingsへ登録し、実際のstdin・応答・終了コードを保存した。
各sessionはBashで固定文字列を1回だけ背景出力し、Read前に待機を表明した。
両方でStopは終了0の`continue: false`を返した後、同じsessionがtask-notificationによる次ターンで再開した。
Readは背景の出力ファイルの全量と`[exited with code 0]`を取得し、最終結果は`terminal_reason: completed`だった。
goalありでは実際の`Goal set`、待機時の未充足評価、結果受領後の正常終了を保存した。
追加のgoal観測では`--debug-file`を指定した。
同じ条件の評価は待機時に`ok: false`、通知からの再開後に`ok: true`となり、その後にsessionのStop評価hookが除去された。
この追加観測の実行記録は`atk-command-t1dania1/record.json`、詳細索引は`session-4bef2h87/goal-evaluation-observation.json`にある。
背景コマンドの再発行と追加のユーザー入力は無かった。

証拠索引は管理対象一時領域の`session-4bef2h87/delivery-observations.json`で、各条件のargv・session・両出力・StopログとCLI終了を保持する。
外側の実行記録は`atk-command-pez6xwtn/record.json`へ保存した。
再検証は`claude --version`と`--help`を確認し、専用sessionで同じBash・Readの順序を指示する。
起動には次の公開引数を使う。

```sh
claude -p --session-id <専用UUID> --setting-sources '' --strict-mcp-config \
  --settings <Stop登録JSON> --tools Bash,Read --allowedTools 'Bash(printf *)' Read \
  --output-format stream-json --include-hook-events --verbose <指示本文>
```

goalありは公開された`/goal`を先頭に置き、設定・評価・同一sessionの再開を併せて確認する。
Stopの応答だけやホストのtask_notificationだけを、モデルによる結果受領の代用にしない。
他のblockとの合成は`stop_test.py`が扱い、本実機観測はblockなしのBash結果に限る。
上流の修正やIssue投稿は行わない。
上流改善後に同条件で警告による継続が通知配送を妨げなくなったことを確認して、回避の撤去を判断する。

## agent-toolkit/agent_toolkit/_hooks/termination_evidence.py：終了工程の証拠のStop判定：2026年10月3日

Stopで報告の不足を判定する変更（2026年10月4日「fix: 残る報告工程と未発話の報告で停止しないようStopで検出する」、`4862700e1`）の要求を起草した時点で、両ホストの公式Hooks仕様のStopとPostToolUseを確認した。資料はCodexが<https://learn.chatgpt.com/docs/hooks>、Claude Codeが<https://code.claude.com/docs/en/hooks>である。両ホストのStopは`last_assistant_message`を供給し、`decision: "block"`と`reason`で同じターンを継続する。CodexのStopは`hookSpecificOutput`を受理せず、CodexのPostToolUseのBashの`tool_response`は終了コードを含まない出力文字列である（`claude-hooks.md`の既存記録と同じ）。
実装時の作業ホストはcodex-cli 0.160.0とClaude Code 2.1.288である。確認した範囲は判定器の契約テストまでである。対象は`termination_evidence_test.py`と`completion_report_delivery_advisor_test.py`の判定である。加えて`output_contract_test.py`がCodex Stopの出力を、`sync_codex_plugin_manifests_test.py`が生成を確かめた。ホスト本体のStopの発火と継続、Codexの未信頼設定や無効化されたhookでの挙動は実機で試験していない。
再検証は両ホストの公式Hooks仕様のStop・PostToolUseの入力と出力を取得し、`last_assistant_message`、Codexの`tool_response`の形とStopの出力契約を比べる。変わった場合は`termination_evidence.py`の可視本文と応答の読取、`output_contract.py`のCodex Stopの契約を改める。

## agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py：UserPromptSubmitの出所欄：2026年9月17日

2026年9月17日に確認した。Claude Code 2.1.274の実行ファイルは、UserPromptSubmitの入力スキーマへ`source`を宣言し、値を`user`、`sdk`、`system`、`loop_wakeup`、`schedule_wakeup`、`poll_event`の6種とする。
同スキーマの説明は`schedule_wakeup`を`scheduled-task fire (CronCreate/routine)`と定める。
`system`は`other machine-injected turns (peer/channel messages, task notifications, auto-continuation)`と定める。
説明の末尾へは`Payloads may omit it while the field rolls out.`と記す。
一方、同じ実行ファイルでUserPromptSubmitの入力を組み立てる2箇所は、いずれも`prompt`の直後に`...!1`を持つ。この部分では代入が畳み込まれている。同じ実行ファイルのSessionStartの入力組み立てには`source:n`があるため、UserPromptSubmitの`prompt`直後にある`...!1`は出所欄の欠落を示すと判定した。2.1.272と2.1.273も同じ形である。
確認の範囲は、この3版が出所欄を配送しないこととする。配送を開始する版と時期は確認していない。
この観測により、出所欄だけを判定入力とする案は現行版で成立しないため、機械注入ターンの判定を4系統で構成した。
再検証は対象版の実行ファイルから`hook_event_name:"UserPromptSubmit"`の前後の文字列を取得する。`prompt`直後に`source`の代入が現れるか、`hook_event_name:"SessionStart"`の同じ箇所と対にして確認する。

## agent-toolkit/hooks/hooks.json：SessionEndの非同期化：2026年9月24日

Claude Code 2.1.281を`--plugin-dir`で作業ツリーのプラグインから2回起動した。debugログはどちらも`SessionEnd:other`を非同期hookとして登録し、予算を`600000ms`と記録した。hookの完了状態は2回とも0で、事前に各セッションIDへ登録した管理対象一時領域は終了後に実在しなかった。標準エラーは空で、debugログにも`Hook cancelled`は現れなかった。CLI本体は2回とも終了コード143で、経過時間は11.60秒と16.45秒だったため、全プラグイン構成での通常応答の完了は未確認である。非同期hookの実行時間もdebugログからは分離できない。

同日、`hooks.json`のSessionEnd登録と同じコマンド・`async: true`だけを一時設定へ写し、`--setting-sources ''`と`--settings`で指定した`claude -p`を実行した。CLIは終了コード0で、結果JSONは`terminal_reason: completed`、`result: OK`だった。標準エラーは空で、debugログに`Hook cancelled`はなく、`SessionEnd:other`を非同期hookとして登録した記録、`600000ms`の予算および完了状態0があった。同一`session_id`で事前登録した管理対象一時領域はCLI終了後に実在しなかった。この検収はSessionEnd単独構成の正常終了を示す。全プラグイン構成を用いた同日の追加試行も終了コード143だったため、その正常終了は引き続き未確認である。再検証では`claude -p`へ同じSessionEnd設定、専用の`--session-id`、`--debug-file`を指定し、CLIの終了コードと結果JSON、標準エラー、debugログの登録・完了状態・予算・`Hook cancelled`の有無を確認する。2026年9月26日以降のSessionEnd hookはセッション単位の領域を削除しないため、同IDで作成した領域の終了後の不在はhookの完了の観測に使わない。

2026年9月24日、Claude Code 2.1.281を作業ツリーのagent-toolkitを`--plugin-dir`で読み込む全プラグイン構成で再検証した。`claude -p`に専用の`--session-id`、`--debug-file`、`--output-format json`を指定し、標準入力を`/dev/null`へ接続した。外側に時間制限は設けなかった。

先の終了コード143は、検証元から継承した`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`と`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`による。再現試行の`strace`はagent-toolkitのStop hookの子プロセスからCLI本体への`SIGTERM`送信を記録した。debugログにも無進捗判定と常駐ループへの中断要求があり、SessionEnd hook自体は完了状態0だった。

常駐ループ用の環境変数を外し、同じ全プラグイン構成でCLIを起動すると、7秒で終了コード0となった。結果JSONは`terminal_reason: completed`、`result: OK`、`is_error: false`を返した。標準エラーは空で、debugログは`SessionEnd:other`を非同期hookとして登録し、予算`600000ms`と完了状態0を記録した。`Hook cancelled`はdebugログと標準エラーの双方に無かった。同じ`session_id`で事前登録した管理対象一時領域は終了後に実在せず、`atk managed-temp list --prefix sessionend-probe`も該当0件を返した。再検証では常駐ループ用の環境変数を検証用CLIへ継承させない。

## agent-toolkit/rules/01-agent.md：方針が衝突する場合の優先順位：2026年9月25日

2026年9月25日、Claude Code 2.1.281で新しいセッション`0b228cab-e106-4b5e-805b-f5e88320691c`を起動し、`--include-hook-events --output-format stream-json`でhook応答を保存した。SessionStartの`additionalContext`は`<agent-toolkit-auto-inserted source="agent-toolkit/rules_context" kind="notice">`で始まった。存在しないパスを指定したBash検索に対するPreToolUseの`additionalContext`は`<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">`で始まった。両応答の`exit_code`は0だった。検証用セッションはhook応答を得た後に中断したため、セッション全体の完了結果はこの観測の根拠に含めない。
再検証するときは、同版以降で`--include-hook-events`を付けて新しいセッションを起動する。SessionStartと、`uv.lock`を`Write`で書き込むPreToolUseの`hook_response.output`を読む（存在しないパスへの警告は2026年9月26日に撤去した）。各応答の外側境界にある`source`と`kind`が上記と一致するか確かめる。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月5日

2026年9月5日、GNU bash 5.2.21で、外側のbashの二重引用符へ置いたドル記号付きの変数は外側で展開され、単一引用符で囲んだ値は`sh -c`を2段重ねた実行で引用符を失うことを実際に動かして確かめた。再検証は外側のbashの二重引用符の内側へドル記号と単一引用符を含む文字列を置き、`sh -c`を2段重ねて出力を確認する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月24日

2026年9月24日、Claude Code 2.1.280のBashツールでは、実在するパスと存在しないパスへの`test -e`がともに`(Bash completed with no output)`を返した。両呼び出しの一次記録はリポジトリ外のセッション記録にあり、本節は観測内容と再検証手段を保持する。bash 5.2.15で`test -e /dev/null; echo "test_e_rc=$?"`は標準出力へ`test_e_rc=0`を返し、存在しない`/dev/__agent_toolkit_audit_absent__`では`test_e_rc=1`を返した。再検証はClaude CodeのBashツールで同じ2種類のパスへ`test -e`と表示付きの起動形をそれぞれ渡し、ツール表示と標準出力の値を対比する。

## agent-toolkit/rules/02-agent-operations.md：ツール・コマンド運用：2026年9月25日

2026年9月25日、Claude Code 2.1.282で`agent-toolkit/skills/wi-standards/SKILL.md`の309行・52,347バイトを`Read`の`offset=1, limit=309`で取得すると、末尾まで届いた。同じファイルを`cat <絶対パス> | cat`で取得すると、Bashは表示上限を超えた全量をセッション内のファイルへ保存した。保存物は309行・52,347バイトで原本と一致した。Claude Codeの[tools仕様](https://code.claude.com/docs/en/tools.md)の「Read tool behavior」は、上限超過時に`PARTIAL view`と先頭ページを返し、`offset`と`limit`で続きを取得する方式を説明する。同仕様の「Bash」は、表示上限を超えた結果をセッション内のファイルへ保存してパスを返す。2026年9月21日のCodexでは、複数文書の全文取得を1つの実行セルへ集約した結果、15,105トークンで出力が切り詰められ、個別再取得を要した。この観測時のCodexのホスト版番号は記録されていない。再検証では両ホストへ同一の大容量エージェント向け文書を与えて全文読取とBashの単純全文読取を実行する。hook通知、ホストの部分取得通知、保存物の末尾および容量を比較する。

## agent-toolkit/rules/02-agent-operations.md：出力の保持と観測：2026年10月12日

2026年10月12日、`claude --version`は`2.1.296 (Claude Code)`を返し、同版の本体の実行ファイルは`allow_large`の文字列を含んでいた。Claude Codeの[tools仕様](https://code.claude.com/docs/en/tools)の「Large files」は次を説明する。`Read`の1回の返却は通常25,000トークン（`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`で変更可能）までで、256KBを超えるファイルの全文取得は拒否される。v2.1.296以降は`allow_large: true`を指定すると、ファイル全体か長い行範囲をセッションのコンテキストの空きに応じた量で1回に読める。画像、PDF、notebookは従来の上限を保つ。上限を超えた場合、全文取得は先頭ページと`PARTIAL view`の通知を返し、256KB超の全文取得と上限超過の範囲指定はエラーを返す。どちらも`offset`と`limit`での部分取得を案内する。
同日のセッションで、通常上限内のファイルへの`allow_large: true`付きの`Read`は全文を返した。通常上限を超えるファイルでの返却は観測していない。
再検証では`claude --version`で版を確かめ、tools仕様の「Large files」を読む。通常上限を超える追跡ファイル（`uv.lock`など）を`allow_large: true`付きと無しの`Read`でそれぞれ取得し、全文の到達、`PARTIAL view`の通知、エラーの有無を比べる。

## agent-toolkit/share/add-wi.parent.md：渡す入力：2026年10月7日

Claude Code 2.1.292のセッション`6ff8ab5b-235b-4bfc-b765-115d1486cd60`で、要求本文を`extra_params`へ埋め込んだ`agents_server`の`start`が、ホストのJSON解析で拒否された。記録の184行目の`__unparsedToolInput.raw`には文字列中の未エスケープの改行と閉じていない末尾があり、185行目に`InputValidationError`と`could not be parsed as JSON`が残る。長文一般や容量上限による拒否を示す観測ではない。

同記録236行目では`投入する要求`へ要求ファイル`r1.md`の絶対パスと、同書が共通入力`common.md`を参照する旨を渡し、246行目にsession識別子と`running`が返った。これはファイル参照入力による起動の受理を示し、起動結果だけで後続の保存成功まで主張しない。

再検証では同記録の上記4行の入力・ツール結果・`version`・`timestamp`を比較する。JSON構文の条件は184行目のrawを標準JSONパーサーへ渡して確認し、本文を正しくエスケープした場合と比較できる。不正入力をホストへ再送する必要はない。現行版でのファイル入力は、公開された`start`のスキーマと`add-wi.parent.md`の入力契約を確認し、要求・共通入力をmanaged-tempへ保存して絶対パスを渡し、起動の終了状態と担当が取得した内容を原文と比べる。

## agent-toolkit/share/pick-wi.subagent.md：調査とレーン分け：2026年8月31日

2026年8月31日から9月2日までの5セッションの観測で、出力トークン量はレーン数へほぼ比例し、レーン数を増やしたセッションで所要時間が短くなる傾向は観測されなかった。

## agent-toolkit/share/read-trigger-bootstrap.md：操作を契機とする共有資料：2026年10月27日

本節は、読込表の宣言と表外の資料参照を分類し、取得を案内する経路と対応付けた記録である。
比較基準は2026年10月26日の`c31bea2b6`で、対象は`agent-toolkit/skills/`、`agent-toolkit/share/`、`.claude/skills/`、`agent-basics/skills/`の373 Markdownだった。
原本の保存先はprivate-notesの`plans/2026/10/27-0550_process-wi_レーン05.read-delivery/`で、次の表のファイル名を使う。
初回保存版はprivate-notesのcommit `0168b0a8e`にある。
初回集計は、原因分析資料の宣言を読込表の先頭側へ動かした配送順の版に対応し、後続のdotfilesの版との一致は示さない。
初回原本には取得時のdotfiles HEADの記録が無いため、各行の`現行sourceのSHA256`で取得対象の版を特定する。

| 本節の呼称 | 原本ファイル | 主な列 |
| --- | --- | --- |
| 全宣言表 | `read-delivery-20261027-routes.tsv` | `種別`、`照合状態`、変更属性、`宣言位置`、`基準位置`、`現行sourceのSHA256` |
| 表外指示の探索表 | `read-delivery-20261027-search.tsv` | `検索語`、`位置`、`形態分類` |
| 参照経路表 | `read-delivery-20261027-references.tsv` | `検索語`、`位置`、`参照先`、`結果` |

| 契機 | 宣言 | 行全文一致 | 一対一差分 | 条件変更 | 対象変更 | 契機・配送操作変更 | 行位置変化 | 意味上の移設 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 操作 | 34 | 34 | 0 | 0 | 0 | 0 | 5 | 0 |
| 起動時 | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 0 |
| 判断 | 1732 | 1709 | 23 | 3 | 20 | 19 | 2 | 1 |
| 合計 | 1781 | 1758 | 23 | 3 | 20 | 19 | 7 | 1 |

宣言数は全宣言表の`種別`別の行数である。
「行全文一致」「一対一差分」「手動対応」「新規」は、`照合状態`の`行全文一致`、`差分の一対一行`、`差分の行番号対応（手動確認）`、`新規追加（基準行なし）`を数える。
「条件変更」「対象変更」「契機・配送操作変更」は、順に原本の`条件変更`、`対象変更`、`契機変更`が`変更あり`の行数である。
「行位置変化」は`配置変更`が`行移動あり`の行数で、新規追加を含めない。
属性は重複するため横方向に合計しない。
一対一差分23行はすべて判断の行で、配送操作と対象がともに変わった19行、対象だけが変わった1行、条件だけが変わった3行である。
契機・配送操作変更列は原本の両セルを比較した値で、この19行の契機の種別はいずれも判断のままである。
行位置変化7行は前後の挿入・削除を含む。
意味上の移設1行は、`agent-toolkit/share/read-trigger-bootstrap.md`の読込表で原因分析資料`agent-basics/skills/bugfix/references/root-cause-analysis.md`の宣言順を変えた手動比較の結果である。
この移動は`read-trigger-bootstrap.md`の16行目から10行目への変更を示す。
この旧配送順の変更は後述の撤回対象に含まれる。

表外指示の探索表は、4対象根に対する固定文字列検索の1615一致行を保持する。
検索語は`全文読`、`に従う`、`が定める`、`を参照`で、順に320、813、93、389行だった。
取得時は語ごとに`git grep -n -F -e '<検索語>' -- agent-toolkit/skills/ agent-toolkit/share/ .claude/skills/ agent-basics/skills/`を実行し、4回とも終了0だった。
参照経路表の1325行は、宣言経路731、同一資料5、名指し参照の短節22、常時規範18、所在参照9、正式資料への移譲10、コードフェンス内3、プログラム内文言37、実在資料の名指しなし490に分類された。経路照合不可は0行だった。
この分類は取得時の静的な先行経路の照合であり、モデルが判断で本文を利用したことの実機観測ではない。

再取得では、4対象根のMarkdownを`agent_toolkit._common.read_triggers.parse_tables`で解析し、sourceと行番号で重複を除く。
宣言の契機・条件・対象・配送操作を同じsourceの比較基準版と対応付け、各属性を別々に数える。
上記4語を各々の固定文字列検索で取得し、表の行・本文の規定参照・説明・コードフェンス・所在参照を分ける。
本文の名指し資料は`validate_reference_routes`で、本文内とsidecarの宣言、常時規範、名指し参照の短節と対応させる。
`read_triggers_invariant_test.py`で宣言・参照先・見出し・先行経路を確認する。

廃止前の代表実機はClaude Code 2.1.311の新規会話で、Pythonの可変デフォルト引数の不具合調査と原因分析節を持つ短い報告書の新規作成を自然な入力で求めた。
資料取得やhookの案内を探す指示は含めなかった。
WriteのPreToolUseは原因分析資料の全文32,697文字を返し、ホストは追加文脈をtool-resultsの保存ファイルへ移した。保存本文は末尾改行を除きhook出力と一致した。
順序はWrite入力の生成、hookの本文出力、Write成功、最終応答で、後続Readは観測されなかった。モデルが判断前に本文を利用したかは未検証である。
この観測は廃止前の方式に限り、新方式の達成根拠にはしない。

本文をhookで代替配送する方式は同月19日の既存判断に反した。宣言型読込表の導入時に再導入された本文配送と、その後の宣言順変更は、後続の全文取得方式で撤去した。
40,000文字の内部配送予算とホスト保存先のRead分割取得案内を撤去した。
案内方式へ戻した作業途中の版で、同じ自然入力を使った新規会話を観測した。この途中版は公開履歴の版として再取得する根拠に使わない。
Claude Code 2.1.311では、`agent-toolkit:bugfix`のSkill起動をhookが取得済みと記録した。
詳細資料へのRead案内も届いたが、会話全体にそのReadはなく、原因分析を含むWrite入力がWriteのhook案内より先に生成された。
全11hookとプロセスの終了コードは0だったが、詳細資料の取得と判断前の利用は確認できなかった。
この各1回の観測から、別会話での取得や全対象の判断前利用を保証しない。

2回目の集計は、作業途中の案内方式の版で取得した原本に対応する。原本は保存先のGit履歴には保存されておらず、以下の件数とSHA256は比較用の参考値である。
同じ4対象根と比較基準を使った。
宣言1,783行の内訳は基準行との全文一致1,755、一対一差分21、旧新の表・条件・順序を比較した手動対応5、新規追加2で、未確定は0行だった。
新規追加は`lane-planning.md`とレビュー担当の読込表からAWI本文形式の冒頭を読む宣言である。

| 契機 | 宣言 | 行全文一致 | 一対一差分 | 手動対応 | 新規 | 条件変更 | 対象変更 | 契機・配送操作変更 | 行位置変化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 操作 | 34 | 34 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| 起動時 | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 判断 | 1734 | 1706 | 21 | 5 | 2 | 5 | 22 | 20 | 21 |
| 合計 | 1783 | 1755 | 21 | 5 | 2 | 5 | 22 | 20 | 22 |

変更属性は重複し、新規行は各変更属性の母集団から除く。契機・配送操作変更は原本の両セルを比較した値であり、契機の種別だけの変更数ではない。
4検索語の一致は順に320、817、93、389行、合計1,619行で、各検索は終了0だった。
参照経路表の1,332行は、初回と同じ分類名で宣言経路735、実在資料の名指しなし493、同一資料5、名指し参照の短節22、常時規範18、所在参照9、プログラム内文言37、正式資料への移譲10、コードフェンス内3だった。経路照合不可は0行だった。
この参照分類は4検索語の候補範囲に限る。候補外の参照を含む宣言経路は、別途`read_triggers_invariant_test.py`のstrict検査で確認する。
2回目の原本のSHA256は次のとおりである。

| 原本ファイルの種別 | SHA256 |
| --- | --- |
| routes | `9652a6620d29dfcdddd184c42211d0ebdebb2ad80319f2b1c8309264182fc9d0` |
| search | `6d40957f52c0f8c220ff4d875b269786a913e6d4ecdc5fda2d01b90e7ff0afc3` |
| references | `5fc95ff9a197c87c1e3afd407859bc4d89dfd430712f5364ae1a922b9d5df988` |

初回と2回目はそれぞれの取得版の履歴であり、次の集計とは分けて扱う。
保存先のGit履歴にある初回原本と、後述の全文取得方式の最終原本は各版のSHA256で集計に対応付ける。2回目にはこの選択方法を適用できない。

以下の1,790宣言の集計は同じ4対象根と比較基準を使った取得時版である。この中間原本は保存先のGit履歴に無く、数値とSHA256は参考値とする。
373 Markdownのうち、宣言を含む249 sourceのSHA256を取得後の本文と比べ、不一致は0だった。
全宣言表の1,790行は、判断1,741、操作34、起動時15である。
`照合状態`は`行全文一致`が1,755、`差分の一対一行`が21、`差分の行番号対応（手動確認）`が5、`新規追加（基準行なし）`が9で、未確定は0だった。
前回の1,783行から7宣言を追加した。
`条件変更`が`変更あり`の行は5、`対象変更`は22、`契機変更`は20、`配置変更`が`行移動あり`の行は122である。
`契機変更`は契機種別と配送操作の比較を含み、各属性は重複する。
新規9行は変更属性の比較対象外とする。

意味上の移設はこの取得時の集計では0行である。
初回に基準16行から10行へ動いた原因分析の宣言は、その後16行へ戻り、この取得時には基準と同じ位置となった。
移設は原本の`配置変更`列で比較し、条件・対象・契機と配送操作は別の列で比較する。

同じ4語の検索は順に320、818、93、389行、計1,620行だった。
参照分類1,333行は、宣言経路736、実在資料の名指しなし493、同一資料5、名指し参照の短節22、常時規範18、所在参照9、プログラム内文言37、正式資料への移譲10、コードフェンス内3である。
照合不可は0行だった。
2回目との差は検索・参照とも差引1行増で、行番号の移動も含む。
この分類は4語の候補に限り、候補外を含むstrict検査とは区別する。

中間取得時の3原本のSHA256は次の参考値である。中間版は保存されておらず、その版の再取得はできない。

| 原本ファイル | 取得時のSHA256 |
| --- | --- |
| `read-delivery-20261027-routes.tsv` | `2673c834b16d897f008dba21f057f9694c3e138409d3d56bfd403fe0a7166901` |
| `read-delivery-20261027-search.tsv` | `5e3cd922ec012cb74fa7fcd20eb5520bbf29f20111c1639899df99d86c5d2536` |
| `read-delivery-20261027-references.tsv` | `5d21c313e6d0228524995601ac242de3e01582e0a6912a52e0504563a53fa9b8` |

全文取得方式に合わせて再取得した分類は、同じ4対象根の373 Markdownを対象とする。
全宣言1,790行の内訳は判断1,741、操作34、起動時15である。
基準版との`照合状態`は行全文一致1,755、一対一差分21、手動対応5、新規追加9で、未確定は0だった。
宣言を含む249 sourceのSHA256を分類取得時の作業ツリー本文と照合し、不一致は0だった。
前述の中間取得時の1,790行から、`agents-server-explore.md`冒頭を指定した宣言1行が削除された。
その後、`agent-toolkit/skills/writing-standards/references/writing.md`に宣言1行が追加された。
追加行の参照先は`agent-basics/skills/writing-standards/references/textlint-violations.md`の「textlint指摘への修正方針」である。

4語の検索結果は順に359、815、93、349行の計1,616行である。
参照分類1,313行の内訳は宣言経路736、実在資料の名指しなし473、同一資料5、名指し参照の短節22、常時規範18、所在参照9である。
残りはプログラム内文言37、正式資料への移譲10、コードフェンス内3で、照合不可は0だった。
指定節は注目箇所であり、取得済みの判定単位はファイル全文とする。
原本の節指定列は宣言の内容を表し、取得済み状態や実機での成功を示さない。

全文取得方式の分類原本のSHA256を次に示す。保存先と版は前述のprivate-notesのディレクトリとGit履歴で確認する。
再取得は比較基準`c31bea2b6`と、保存されたroutesのsource SHAが一致する公開履歴の版を選び、前述の4語の独立検索と宣言・参照の対応付けを実行する。
3原本の行末はLFである。次のSHA256は取得時の値で、保存の有無は上記ディレクトリのGit履歴で確認する。

| 原本ファイル | 全文取得方式の取得時SHA256 |
| --- | --- |
| `read-delivery-20261027-routes.tsv` | `42fbc98017b58b4a2db9965ba46714e23562803c9fba8d4a6d64b95a8723b7d6` |
| `read-delivery-20261027-search.tsv` | `40c41efeadf4e63e72b1bb901306feb4724695b0fd2571787a4a1cfcc2992cde` |
| `read-delivery-20261027-references.tsv` | `96a36680f1a74e5cad6656e7ae059cc0084f25ffa31ab65cc3e3deec7e0bbbc5` |

旧節単位方式の代表実機は作業途中のクリーン版で行い、自然入力とReadを明示した制御は別会話である。途中版のcommitは公開履歴での再取得に使わない。
自然会話では初回Writeの後に案内が届き、その後にSkill・Read・再Writeを観測したが、180秒上限で終了し最終応答はなかった。
basics側原因分析資料の全文Readは出力省略があり、全文の取得済み状態は観測していない。
制御会話ではtoolkit側の原因分析記録資料のRead、PostToolUse入力と取得済み状態の対応を確認し、終了0だった。
この観測は当時の実装に限る。後述する分割Readの是正後の制御観測とは区別する。
旧節単位方式の代表対象は`agent-toolkit/share/read-trigger-bootstrap.md`の原因分析に関する操作と判断の2宣言である。全宣言の個別実機成功やモデル内部の利用は確認していない。

旧節単位方式の同日の後続観測ではClaude Code 2.1.311の自然入力で原因分析のWrite後に資料取得を案内し、Read後の再WriteとEditを観測した。
180秒上限で最終応答はなく、モデル内部の利用は確認できない。
非連続の選択節を分割Readした別会話の生PostToolUse入力を旧判定へ再生すると、前半は部分範囲の末尾LF省略を拒否して、取得範囲が本文と一致した証拠を記録できず、後半だけが記録された。
LFを補った対照でも全文座標のままでは選択節の被覆を合成できなかった。
当時の是正では本文・metadata・主体・hashと失敗・出力省略の検査を維持し、部分範囲の正常な末尾LF省略を扱った。Readとsedの取得範囲を、指定された非連続節を順に連結した文字列上の座標へ変換した。

旧節単位方式の是正後の制御会話では、Python資料の「言語スタイル」「テストコード（pytest）」「Python新構文と導入バージョン」を2回のReadで取得した。
対象は`agent-basics/skills/writing-standards/references/python.md`だった。
本文SHA256は`05a0ae8503778ae9db2de4bcedf5c231360604ae0b5512e10a02d927345d3e78`である。
Readの指定は前半がoffset 14・limit 63、後半がoffset 90・limit 40だった。
非連続の3節を順に連結した文字列上の範囲は0:5195と5195:8183だった。
前半だけでは指定節の取得済み状態がなく、後半の取得後に同じ主体とhashの指定節の取得済み状態を確認した。
別の制御会話で全文末尾LFが1個と2個の2検体をReadし、どちらも全文が一致した証拠を得た。
いずれも制御入力であり、自然会話の内部利用や他のホスト版全般を保証しない。
これらは節ごとに取得済みを判定していた旧方式の観測である。後続の全文取得への方針変更では、節指定を注目箇所として保持し、全文被覆を判定する。
旧方式の実装版は公開履歴から再現できないため、その観測の再実行はできない。現行方式の再検証は、後述の現行3実装sourceのSHAが一致する公開版を選ぶ。`read_delivery.py`と`read_delivery_test.py`で正常・省略・失敗・誤metadata・分割取得の全文被覆を比較する。

全文取得方式の静的分類は宣言1790・検索候補1616・参照候補1313を扱い、これらの集合は重複し得る。
代表自然観測は`agent-toolkit/share/read-trigger-bootstrap.md`の原因分析の操作と判断の2宣言に対応し、原因分析資料の取得を観測した。
代表制御観測は`agent-basics/skills/writing-standards/SKILL-delivery-5.md`のPython資料を読む宣言に対応し、同資料の分割取得を観測した。
これら3宣言以外の個別の取得順と、検索・参照候補の個別実機は観測していない。
全文取得方式の代表実機はClaude Code 2.1.311で、自然入力と制御入力を別会話で観測した。実装はb28807ac95b2e7b2059bb6a670f4daf1ce098f7fに未commit変更を加えた版で、次の3 sourceのSHA256で取得時版を識別する。この取得時版の公開履歴からの再現は確認できず、観測は歴史的な参考として扱う。

| 実装source | SHA256 |
| --- | --- |
| `agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery.py` | `0d3d86305b9e30d63782fa5ec120ea1e290e7ee884373ab144e591f38a407f8a` |
| `agent-toolkit/agent_toolkit/_common/read_triggers.py` | `e89e28d67bde492d15516738cc347b174dd57440450627ab19cbd23a84cd0969` |
| `agent-toolkit/agent_toolkit/_hooks/rules_context.py` | `9b00eba032e19e2c8d3575f41fb6e91dcfcadf9fe3bf205666d98408291e6b74` |

自然入力はPythonの可変デフォルト引数によるリスト共有の原因と対策を短い報告書にまとめる依頼だった。Python資料は`agent-basics/skills/writing-standards/references/python.md`である。
自然会話ではWrite後の全文取得案内、Read、再WriteとEditを観測したが、180秒上限で終了し最終応答はなかった。
basics側原因分析資料は前半のReadが出力省略され、後半を取得しても全文取得済みの状態は記録されなかった。
toolkit側原因分析資料とPython資料は、それぞれ全文Readと同じ主体・hashの全文取得済みの記録を確認した。
自然会話では他にも未取得の資料が残り、モデル内部の利用は確認できない。
別の制御会話では、同じPython資料の1〜50行、52行以降、51行を順にReadした。
途中の未取得範囲がある状態では全文取得済みの記録がなく、51行の取得後に全文取得済みの記録となり、会話は終了0だった。
節だけの取得済み記録を確認した旧方式の観測は、この全文取得済みの記録の証拠に使わない。
公開イベントのないモデル内部の判断や利用順は観測対象から除く。

全文取得方式の後続自然観測もClaude Code 2.1.311で、同じPythonの不具合調査と報告書作成を依頼した。会話は終了0で最終応答を得た。Write後の案内、資料のRead、再WriteとEditを観測した。basics側原因分析資料の前半は出力が省略され、後半だけが取得範囲に記録されたため全文取得済みにはならなかった。toolkit側原因分析資料は全文取得済みとなった。未取得の資料が残り、モデル内部の判断や利用は観測できない。
全文取得方式の後続自然観測では、Claude Code 2.1.311に`/home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-toolkit`の2.228.0だけを`--plugin-dir`で指定した。インストール済み`agent-basics@ak110-dotfiles` 1.4.0は`/home/aki/dotfiles/agent-basics`に登録され、そのSkillを読み込んだ。Readは`/home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-basics`を対象とした。当時の登録元とRead先の本文の同一性は確認していない。

後続自然観測時の`read_delivery.py` SHA256は`5401948b0b85a5e35f71c36506835e46a3985944cf064f35be6dda5fb08b92d6`である。`read_triggers.py`は`e89e28d67bde492d15516738cc347b174dd57440450627ab19cbd23a84cd0969`である。`rules_context.py`は`d57dfbd19f0e01e7a3d390f0b775ca0e2e22aebd4d7b191389f1e1fbd58c0364`である。観測後に例外案内と不在資料向け案内を変更したため、後述の現行版と完全同一ではない。正常資料の案内と全文被覆は変更せず、不在分岐は公開hook入力を用いた別の試験で確認した。

全文取得方式の別の制御観測では、1つのworktree内の2 pluginを明示し、許可ツールをReadとSkill、上限を120秒としてClaude Code 2.1.311で実行した。終了0・result successだった。登録は同じworktreeのagent-toolkit 2.228.0とagent-basics 1.4.0である。`agent-basics:bugfix`のSkillと`agent-basics/skills/bugfix/SKILL.md`のReadの1資料を照合した。Readは121行中121行で省略なし、本文SHA256は`301587105fd4b30d287e954ef3d263007c7787d7cf78d44c383c3cffcc10d494`だった。Skill本文とRead本文は同じworktreeの資料と一致した。制御観測の3実装sourceは後述の現行SHAと一致した。自然会話や、同名pluginが複数登録された他の環境での優先順位は、この制御結果の対象外である。

現行方式の再観測では、対象worktreeのcommit OIDと未commit差分、agent-toolkitとagent-basicsのmanifestにあるversion、3実装sourceのSHA256を起動前に記録する。後述の3 SHAと実装が一致することを確認する。後続自然観測時の3 SHAは上記の値であり、再観測で選ぶ現行版とは異なる。
自然入力で取得を案内される資料は実行時に決まる。起動前にagent-toolkitとagent-basics配下の資料ファイルの絶対パスとSHA256を記録し、実際にReadした各資料について、同じ絶対パスの事前SHAと取得時のファイルSHAを比較する。Read本文は取得範囲を同じファイルの対応箇所と比較する。事前記録に無い資料は版の一致を確認できない。
全文取得方式の代表自然観測と後続自然観測で対象となった資料の例を次に示す。
事前に記録する集合はこの3資料に限らず、2 plugin配下を対象とする。

- `agent-basics/skills/bugfix/references/root-cause-analysis.md`
- `agent-toolkit/skills/bugfix/references/root-cause-analysis.md`
- `agent-basics/skills/writing-standards/references/python.md`

次は対象worktreeを`/home/aki/dotfiles/.claude/worktrees/process-wi-lane-05`とする例である。
Pythonの可変デフォルト引数の不具合を調査し、原因分析節を持つ短い報告書を新規作成するよう依頼する。出力先は取得前に存在しない作業用パスを使う。`<入力>`は依頼本文に置き換える。

```sh
timeout 180 claude -p --no-session-persistence --plugin-dir /home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-toolkit --plugin-dir /home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-basics --allowedTools Write,Read --output-format stream-json --verbose --include-hook-events '<入力>'
```

streamのplugin登録path・version、SkillのBase directory、Readのpath・本文・hashが対象worktreeと一致することを確認する。初回Write入力、PreToolUseの全文取得・見直し案内、Write成功、SkillとRead、再Write・Editと各結果をイベントの順序で確認する。PostToolUseのRead結果では本文の範囲・省略の有無と、同じ主体・資料hashに対応する取得済み状態を確認する。未取得範囲、最終応答および終了状態も記録する。公開イベントからモデル内部の利用を推論しない。

現行の再検証で選ぶ3 sourceのSHA256は次のとおりである。公開後に各ファイルのSHAが一致する版を確認する。

| 現行実装source | SHA256 |
| --- | --- |
| `agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery.py` | `541dfa9f284eed60737344a2c860ceab721b898fd287f77efd4c7578d9a81153` |
| `agent-toolkit/agent_toolkit/_common/read_triggers.py` | `e89e28d67bde492d15516738cc347b174dd57440450627ab19cbd23a84cd0969` |
| `agent-toolkit/agent_toolkit/_hooks/rules_context.py` | `50361034a30c66546ad75331ec5b9667923147d634f7f55fee22678f6bedb72c` |

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年8月31日

2026年8月31日、Claude Code v2.1.251・Fable 5で、ツール結果と次のツール呼び出しの間へ置いた地の文が`· summarized`付きの短縮文へ置換されることを確認した。この観測では、ターン冒頭と`AskUserQuestion`直前の地の文は原文どおり表示された。

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント`https://code.claude.com/docs/en/agent-sdk/user-input`の`Question format`節、`Response format`節および`Limitations`節で確認した。再検証は同じ3節を読む。

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年9月3日

2026年9月3日、Claude Code v2.1.258・Opus 5では、複数のツール呼び出しの間へ置いた地の文の同じ置換をユーザーが端末表示で確認できなかった。

## agent-toolkit/share/rules-main.claude-code.md：ターンの終え方：2026年9月4日

```text
2026年9月4日、Claude Code 2.1.260で、Bash背景ジョブの稼働中はデバッグログへ`[goal] evaluation deferred — background work still running`が出て評価が発動せず、背景ジョブを起動しない指示では評価が発動することを実測した。再検証は`claude --debug-file <path> --print --session-id <uuid> --permission-mode auto --allowedTools Bash -- '/goal <条件>。まずBashツールをrun_in_background=trueで使い sleep 60 を実行し、その後は追加作業をせずターンを終えること'`を実行してそのログ行の有無を確認し、背景ジョブを起動しない指示でも同じコマンドを実行し、評価の発動を対にして確認する。
```

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年9月5日

2026年9月5日、Fable 5.1で`AskUserQuestion`直前の地の文へ選択肢の前提を置いた。ユーザーはその説明を読めず判断できないと回答し、確認の再発行を要した。この観測で使ったClaude Codeの版数は記録していない。再検証はモデルと版ごとに、ターン冒頭、ツール呼び出しの間および`AskUserQuestion`直前の3箇所へ地の文を置き、表示を確認する。

## agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年9月28日

2026年9月28日、Claude Code 2.1.283をtmux内で起動して、複数行の可視本文、Read呼び出し、最後の1行を順に出力するよう指示した。tmux画面とtranscriptの双方へ、4行の本文、ツール呼び出し、最後の本文が同じ順序で現れた。一方、同日の別セッションで実行主体がツール前へ書いたつもりの回答と完了報告は、画面にもtranscriptの`text`にも現れなかった。再検証は新しいClaude Codeセッションへ同じ順序の出力を指示し、画面表示とtranscriptの`text`、`thinking`、`tool_use`を対応付ける。ツール前の複数行本文が可視となることと、拡張思考へ置いた文が可視本文として配送されないことを分けて確認する。

## agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年10月3日

拡張思考の表示に関する変更は、2026年10月4日「fix: 拡張思考の表示を断定せず発話本文への出力を求める」、`31a598fd5`である。
要求を起草した時点の観測環境はClaude Code 2.1.288、モデル`claude-opus-5-5`、`showThinkingSummaries=false`だった。transcriptの`thinking`ブロックに保存された文がユーザーの画面へ
通常の応答文と同じ見た目で表示された。一次記録（当時のセッション記録とユーザーが貼った画面表示）はリポジトリ外にあり、
本節は観測内容だけを保持する。続くStopはtext本文の欠落を遮断したが、思考の非表示を述べる理由は画面と一致しなかった。
2026年9月28日の記録（Claude Code 2.1.283）では思考の文が表示されなかった。当時の記録は保持する。
表示の有無はエージェントが応答時に確かめられないため、規範と通知は表示されないものとして発話本文へ書くよう求める。
再検証ではホスト版、モデルと同設定を保持し、ユーザーの画面表示をtranscriptの同一messageのtext・thinkingへ対応付ける。
transcriptだけの取得を画面表示の観測として扱わず、画面へ届いた内容とhookが述べる理由を比べる。

## agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年10月6日

同じ応答でツール呼び出しより前に置いた地の文の一部が、APIの応答の時点で要約へ置き換わる根拠を記す。観測はいずれも`thinking`ブロックの署名を開かずに行った。

公式文書「Thinking」（<https://platform.claude.com/docs/en/build-with-claude/thinking>、2026年10月7日取得）の節「Progress updates between tool calls」を確かめた。
同節はFable 5.1、Mythos 5.1、Opus 5.5、Sonnet 5.5、Fable 5ではモデルがツール呼び出しの間にprogress updateを書き、直後の`tool_use`の前に独立した`thinking`ブロックとして返ると記す。
返り方は`display`で決まる。`"omitted"`（これらのモデルの既定）では文面が空で、`"updates"`（beta）では要約文になる。`"summarized"`では推論のブロックと区別できない要約になる。
`"updates"`については「any `thinking` block with non-empty text is a progress update」「The text you receive is a summary of the progress update」と記す。どの`display`でも文面が空で返ることがあるとも記す。

Claude Code 2.1.291の本体では、出力の項目`narration_block_indexes`の説明が該当ブロックを「server summaries of the prose between tool calls, not the model's own reasoning」と記す。
項目`narration_hint`の説明はそのブロックの末尾へ`· summarized`を描く規則を端末の表示と同じとする。
本体が要求へ`display: "updates"`とbeta`thinking-display-updates-2026-08-18`を加える条件は次の3つが揃うときである。環境変数`CLAUDE_CODE_THINKING_DISPLAY_UPDATES`が未設定であること、思考の表示の指定が`omitted`でないこと、設定`showThinkingSummaries`が`true`でないことである。
観測した環境は`showThinkingSummaries`が`false`で、同環境変数を設定していなかった。

判別では`display: "updates"`を要求している記録で、文面を持つ`thinking`ブロックをprogress updateの要約（ナレーション要約）として数える方法とした。
`showThinkingSummaries`が`true`の環境と前掲の環境変数で無効にした環境は、この判別の前提を満たさない。

集計の母集団は、2026年9月1日以降に更新されたClaude Codeのメイン記録のうち2026年10月6日19時13分6秒（日本時間）以前に始まった1466件で、同時刻以前の`isSidechain`でない応答のブロック列を数えた。数えた層はAPIの応答を保存したtranscriptである。

| モデル | 文面を持つ`thinking` | 後続にツール呼び出しがある`text` | 後続にツール呼び出しが無い`text` |
| --- | --- | --- | --- |
| Opus 5.5 | 3663件 | 12900件 | 4602件 |
| Fable 5.1 | 98件 | 131件 | 47件 |
| Sonnet 5.5 | 12件 | 252件 | 334件 |
| Opus 5、Sonnet 5、Haiku 4.5 | 0件 | 集計対象外 | 集計対象外 |

ツール呼び出しより前の地の文の多くは`text`のまま届いており、順番は要約への置換の必要条件だが十分条件ではない。
文面を持つ`thinking`のうち後続にツール呼び出しが無い4件は、いずれも直後にユーザーの中断があった。
画面の表示は2026年8月31日と2026年10月3日の節が持ち、いずれも要約が表示された点で一致する。

既知の報告としてIssue `anthropics/claude-code#95764`（2026年9月21日作成）が同じ事象を報告し、2026年10月7日時点でOPENで、Anthropicの回答は無かった。

対策の`send_to_user`は2026年10月6日にClaude Code 2.1.291で試作のFunction hooks moduleを使って確かめた。
`claude plugin validate`と`claude plugin test`（`ToolUse`の行を端末と`desktop`で描くテスト）が合格した。
`claude -p --plugin-dir <試作> --model claude-opus-5-5`へ公式ガイドの誘導文を渡し、答えを先に伝えてからファイルを読むよう指示した。モデルはこのツールを呼び、入力は原文のまま記録された。
tmux内の対話のClaude Code（Opus 5.5）では、ツールで送った見出しと箇条書きが端末の画面へその形で表示された。

再検証は次の2つに限る。1つ目は本体のバイナリを`rg -a -F 'server summaries of the prose between tool calls'`で検索して`narration_block_indexes`の説明を確かめることである。検索の手順は`.claude/skills/dotfiles-development/SKILL.md`「ホスト本体のバイナリの検索」に従う。
2つ目は`display: "updates"`を要求する環境の記録で、`thinking`ブロックを文面の有無で数えることである。

## agent-toolkit/share/rules-main.claude-code.md：ターンの終え方：2026年10月7日

2026年10月7日、Claude Codeのセッション記録を`Your previous response had no visible output`の固定文字列で検索した。
ホストが挿入したこの表示は3件（Claude Code 2.1.281・2.1.282・2.1.283、2026年9月24日から26日）で、いずれも直前のアシスタント応答がthinkingだけを持ち、地の文もツール呼び出しも持たなかった。
同じ文字列を含む残りの記録2件は、ツール結果に同じ文字列が含まれただけだった。
ツール呼び出しだけで地の文を持たない応答は記録に多数あるが、その直後にこの表示は現れなかった。
再検証では同じ文字列を検索し、表示の直前のアシスタント応答が持つ要素の種類を確かめる。

## agent-toolkit/skills/bugfix/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月1日

2026年10月1日、LinuxのGitHub CLI 2.101.0で`ak110/dotfiles`を対象に確認した。
run `36791008397`が進行中でjob `110143643856`が`completed/success`のとき、job logs APIを`--allow-escape-sequences`付きで取得すると終了コード0で454行の全jobログを保存できた。
保存直後もrunは`in_progress`で、同runとjobを指定した`gh run view --log-failed`は終了コード1となり、進行中のため取得できない旨を返した。
終了済みの失敗run `36786321272`とjob `110128462391`への`gh run view --log-failed`は終了コード0で保存できた。
確認したAPIの成立範囲は進行中runの完了済みjobであり、進行中jobの取得可否は未確認である。

再検証では`gh version`と両コマンドのヘルプを取得する。
この節の再検証コマンドでは`<OWNER>/<REPO>`を`ak110/dotfiles`とする。
進行中runに完了jobがある場合はrunとjobの状態をAPIで保存する。
`gh api repos/<OWNER>/<REPO>/actions/jobs/<job ID>/logs --allow-escape-sequences`で全jobログを取得する。
同じ対象へ`gh run view <run ID> --repo <OWNER>/<REPO> --job <job ID> --log-failed`を実行し、出力と終了コードを比較する。
取得後もrunが進行中であることを確認し、終了済みの失敗runとjobにも後者を適用する。
APIは全jobログ、`--log-failed`は失敗ステップを返すため、出力件数の一致は求めない。
進行中runが無い場合はその条件を未観測とし、新しいCIの起動や事象の発生待ちは行わない。

## agent-toolkit/skills/bugfix/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月24日

2026年10月24日、LinuxのGitHub CLI 2.102.0で`ak110/dotfiles`の終了済み失敗run `39987263957`とjob `120529624126`（pytest (3.15)）を対象に確認した。
job logs APIを`--allow-escape-sequences`付きで取得すると終了コード0で29773行・6090534bytesを保存でき、最長行は600188bytes、外側の時間上限による終了の表示を含んだ。
同じrunとjobへの`gh run view --log-failed`も終了コード0・標準エラー0bytesだったが、17833行・3430008bytesで、試験の進捗44%の行で終わり、時間上限の表示を含まなかった。
APIの原本では、その次の行が600KB前後の試験名を持つ行だった。
GitHub CLIの[該当source](https://github.com/cli/cli/blob/v2.102.0/pkg/cmd/run/view/view.go#L549)は既定の`bufio.Scanner`で行を読み、走査後の`Err()`を返していない。[`bufio.Scanner`の既定の上限](https://pkg.go.dev/bufio#MaxScanTokenSize)は64KiBである。

再検証では`gh version`を取得し、64KiBを超える行を含む失敗jobへ両コマンドを適用して、終了コード・行数・bytes・末尾の行を比較する。
該当する失敗jobが無い場合は条件を未観測とし、新しいCIの起動や事象の発生待ちは行わない。

## agent-basics/skills/bugfix/references/ci-failure-handling.md：分類判断のための詳細検討：2026年10月7日

2026年10月7日、GitLabのリポジトリ（Runnerはgitlab-runner 18.2.1のDocker executor）のAWI処理回で、許容された終了コード4で終わるはずのjobがjobとして終了コード1を報告した事象を再現した。
手段は次のとおりである。GitLabのjob取得とjob更新の要求に応える最小のHTTPサーバー（偽のjob API）を、コンテナーから到達できるアドレスで起動した。`gitlab/gitlab-runner:v18.2.1`の`run-single --executor docker`（helper `x86_64-v18.2.1`）でjobを1件ずつ実行し、Runnerがjob APIへ送った`exit_code`を報告値として読んだ。
bashのイメージにはCIのjobと同じdigestのPythonイメージ（bash 5.2.37）、shのイメージには`docker:27-cli`を使った。
bashのイメージで外部コマンドが4で終わると、`after_script`の有無にかかわらず1を報告した。組み込みの`exit 4`は4を報告した。shのイメージでは外部コマンドの4を4と報告した。
変数`FF_USE_NEW_BASH_EVAL_STRATEGY=true`を与えると、bashのイメージでも外部コマンドの4と5をそのまま報告した。
生成形の典拠はgitlab-runner v18.2.1の`shells/bash.go`の`BashWriter.Finish`で、同フラグが偽なら`: | eval <script>`、真なら`: | (eval <script>)`を生成する。
スクリプトとuvの層だけを含むローカル再現ではこの報告値の差は現れず、Runnerと実行器を含めた再現で初めて直接的原因を対照で確定できた。

再検証では同じ手段で、bashのイメージで`python -c 'import sys; sys.exit(4)'`だけをscriptに持つjobを、同フラグの指定なしと`true`の2条件で実行し、前者が1、後者が4を報告することを確かめる。
Runnerの版を変えた場合は、その版の`shells/bash.go`で生成形を確かめてから同じ2条件を実行する。

## agent-basics/skills/check-execution/references/diagnostics.md：読む出力：2026年10月3日

2026年10月3日、pyfltr 3.19.9でJSONLの要約と診断の粒度を確認した。
`uv run --frozen pyfltr run --commands=textlint --no-fix agent-toolkit/share/add-wi.parent.md`は終了コード0だった。
診断レコードはseverity=warningのメッセージを1件持ち、コマンド結果はsucceeded・diagnostics=1、要約の`commands_summary.needs_action.warning`は0、`diagnostics`は1だった。
導入済みパッケージの`pyfltr.output.jsonl._build_summary_record`も、各コマンドのstatusを数える処理と診断を集計する処理を分けていた。

再検証では導入版を取得し、warningの診断を持つMarkdownへ同じtextlintの起動形を適用する。
JSONLのdiagnosticのmessages、commandのstatus・diagnosticsとsummaryの`commands_summary.needs_action.warning`を比較する。
出力の集計は同じ保存ファイルを使い、件数0だけから診断の不在を判断しない。
導入済みパッケージの同関数も読み、要約が数える値と診断の生成を確かめる。

## agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月14日

2026年9月14日、Git 2.43.0で実際に動かして確かめた。公式`git-rev-parse`文書は`--short=<length>`を、少なくとも指定長を持つ一意な接頭辞と定める。`core.abbrev`を設定していない対象HEADでは`git rev-parse --short HEAD`が9文字、`git rev-parse --short=7 HEAD`が7文字を返した。再検証は`git config --get core.abbrev`の設定有無を記録し、同じHEADに対する`git rev-parse --short HEAD`と`git rev-parse --short=7 HEAD`の文字数を比較し、後者が7文字以上で一意に解決できることを確認する。

## agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月20日

2026年9月20日、Git 2.43.0で`git rev-parse --short=7 HEAD HEAD~1`が標準エラーへ`fatal: Needed a single revision`を書いて終了コード128となることを確認した。PowerShell 7.6.0では、未引用の`git rev-parse --verify HEAD^{commit}`が同じエラーと終了コード128を返し、単一引用符で囲んだ`git rev-parse --verify 'HEAD^{commit}'`が完全OIDと終了コード0を返した。再検証は同じrepositoryで1件と2件のrevisionを渡した`--short=7`の終了状態を比較し、PowerShellでpeel式の引用有無によるGitの受理結果を比較する。

## agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月8日

Git 2.47.3で、`commit.verbose=true`を設定した隔離リポジトリ2つへ同じ変更をstageし、`#`で始まる行だけを取り除く同じ`GIT_EDITOR`で`--fixup=amend:<sha>`を実行した。`--no-verbose`付きでは`git log -1 --format=%B`の本文に`diff --git`が無く、外した対照では差分が本文へ入った。両方とも終了コード0、作業ツリーはcleanだった。

再検証では新規の隔離リポジトリで初期commitを作成し、`git config commit.verbose true`を設定する。ファイルを変更してstageし、編集バッファの各行を読み、`#`で始まる行だけを除いて保存するスクリプトを`GIT_EDITOR`へ指定する。`git commit --no-verbose --fixup=amend:<初期commit>`と、別の同一条件のリポジトリで`--no-verbose`を除いた起動を比較し、前記の本文の差を確認する。

## agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月18日

Git 2.34.1と2.47.3で、`compare_history_test.py`の隔離履歴に対する20件のテストが成功した。
Git 2.34.1はCIと同じUbuntuの`git_2.34.1-1ubuntu1_amd64.deb`を一時領域へ展開して使用し、SHA-256は`0a0bd5c85eafb688025d68e69f49919591b22abf8b7d608d7e64307714d361ca`だった。
`rebase.updateRefs=true`と別branchを置いた。
文書と同じ`GIT_SEQUENCE_EDITOR=:`、`-c rebase.updateRefs=false rebase -i --autosquash`でfixupを統合した。
最終treeと別branchのOIDが保持され、制御件名が消失した。
Git 2.47.3で局所設定のfalseだけを除いた対照は、別branchのOID保持の試験で失敗した。
履歴検収は空白・path・mode・type・binary・曖昧な対応を区別し、統合後の対応表を公開記録操作へ渡した。

再検証では上記debを展開した`usr/bin`をPATHの先頭、`usr/lib/git-core`を`GIT_EXEC_PATH`へ設定し、`git --version`を確認して同じ20件を実行する。
新しいGitでは通常のPATHで同じテストを実行し、参照更新機能が有効な環境でも局所指定が保護することを確認する。
サブモジュールの作業状態を保持する局所設定は同じautosquashへ併用でき、履歴比較はGit 2.38以降だけの`patch-id --verbatim`を呼び出さない。

## agent-toolkit/skills/commit/references/history-rewrite.md：親だけのGit操作とサブモジュール：2026年10月18日

Git 2.34.1での検証担当の記録によると、`agent-toolkit/agent_toolkit/_atk/submodule_state_test.py`の15件が成功した。
GitはCIと同じUbuntuのdebを一時領域へ展開して使用した。
親の履歴内でgitlinkが異なる隔離fixtureへ再帰trueとfalseを与え、autosquash・通常rebase・keep reset・switch・checkout・restoreを比較した。
再帰trueでは子の作業状態が変わり、呼び出し限りの`-c submodule.recurse=false`では子のHEAD・branch・作業内容を保持した。
比較した値はHEAD・symbolic-ref・porcelain・追跡ファイルの内容・未追跡のレビュー内容である。
autosquashは基点への切替を確実にするため`--force-rebase`を併用した。
局所falseを指定した後も、永続設定の`submodule.recurse=true`を保持した。
同じ実Gitの試験で、private-notes同期のrebaseとkeep reset、laneの準備時のdetached worktreeからbranchへの接続も、子の作業状態を保持した。

再検証ではGit 2.34.1を展開した`usr/bin`をPATHの先頭、`usr/lib/git-core`を`GIT_EXEC_PATH`へ設定し、`git --version`で版数を確認して上記のpytestを実行する。
再帰設定だけを変えた対照の結果と、子の5種類の観測値を確認する。

## agent-toolkit/skills/commit/references/publish.md：検証とCI：2026年10月11日

uv 0.13.0で、`UV_PROJECT_ENVIRONMENT`で隔離した仮想環境を使い、dotfilesのリポジトリ直下と`agent-toolkit/`のプロジェクトを`UV_FROZEN=1`のもとで同期した。
リポジトリ直下をPython 3.15で`uv sync --all-groups --all-extras`した環境へ、ロックファイルに無い`toolz`を`uv pip install`で加えた。
この環境では`uv run`の後も`import toolz`が成功し、続く`uv sync --all-groups --all-extras`の後は`ModuleNotFoundError`になった。
`agent-toolkit/`をPython 3.14で構築した環境は、`--python`を付けない`uv sync`と`uv run`の後も3.14のままで、`uv sync --python 3.15`の後は3.15になった。
`agent-toolkit/`は`.python-version`を持たないため、`--python`を渡さない同期は既存の仮想環境をリポジトリ直下の基準版で構築し直さない。

その後`agent-toolkit/pyproject.toml`の`requires-python`が`>=3.15`となり、`agent-toolkit/`を3.14で同期する手順は解決の不一致で終了コード2となって成立しなくなった。
2026年10月15日、uv 0.13.1で同じ観測を取り直した。
余分なパッケージの残存と除去はリポジトリ直下で上記と同じ結果になった。
版の保持と再構築は、`requires-python`が`>=3.14`で依存を持たない一時プロジェクトを`.python-version`（`3.15`）を置いたディレクトリの直下に作成し、`agent-toolkit/`と同じ配置で再現した。
3.14で構築した環境は`--python`を付けない`uv sync`と`uv run`の後も`(3, 14)`のままで、`uv sync --python 3.15`の後に`(3, 15)`となった。

再検証の手順を次に示す。
`WORKTREE`にはdotfilesの作業ツリーの絶対パスを設定する。
`TMP_DIR`には作業ツリーの外に用意した空の一時ディレクトリの絶対パスを設定する。
`UV_FROZEN=1`を設定したシェルで、コードブロックの行を順に実行する。
`ROOT_ENV`はリポジトリ直下のプロジェクト、`SUB_ENV`は一時プロジェクトの仮想環境で、各`uv`の呼び出しへ`UV_PROJECT_ENVIRONMENT`で渡す。
`uv run`で実行した`import toolz`が`toolz present`を出力し、続く`uv sync`の後に`$ROOT_ENV/bin/python`で実行した`import toolz`が`ModuleNotFoundError`で失敗すれば、余分なパッケージの残存と除去を再現できている。
一時プロジェクトの版が`--python`を付けない同期と`uv run`の後に`(3, 14)`のままで、`--python 3.15`の同期の後に`(3, 15)`となれば、版の保持と再構築を再現できている。

```sh
ROOT_ENV="$TMP_DIR/root-env"
SUB_ENV="$TMP_DIR/sub-env"
SUB_PROJECT="$TMP_DIR/parent/sub"
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv sync --project "$WORKTREE" --all-groups --all-extras --python 3.15
uv pip install --python "$ROOT_ENV/bin/python" toolz
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv run --project "$WORKTREE" python -c 'import toolz; print("toolz present")'
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv sync --project "$WORKTREE" --all-groups --all-extras --python 3.15
"$ROOT_ENV/bin/python" -c 'import toolz'
mkdir -p "$SUB_PROJECT"
printf '3.15\n' > "$TMP_DIR/parent/.python-version"
printf '[project]\nname = "sub"\nversion = "0.1.0"\nrequires-python = ">=3.14"\ndependencies = []\n' > "$SUB_PROJECT/pyproject.toml"
UV_FROZEN=0 uv lock --project "$SUB_PROJECT"
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv sync --project "$SUB_PROJECT" --python 3.14
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv sync --project "$SUB_PROJECT"
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv run --project "$SUB_PROJECT" python -c 'import sys; print(sys.version_info[:2])'
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv sync --project "$SUB_PROJECT" --python 3.15
"$SUB_ENV/bin/python" -c 'import sys; print(sys.version_info[:2])'
```

## agent-toolkit/skills/commit/references/publish.md：検証とCI：2026年10月19日

前節の観測は`.python-version`を持たないプロジェクトの条件であり、`.python-version`を持つプロジェクトでは`uv run`の自動同期が既存の仮想環境を再作成する。
2026年10月19日にuv 0.13.1で、依存を持たず`requires-python`が`>=3.14`の一時プロジェクトへ`.python-version`（`3.15`）と`uv.lock`を置き、3.14の仮想環境から始めて次を観測した。

- 版指定なし: `uv run --frozen`の後に3.15で再作成された
- `UV_PYTHON=3.14`: `uv run --frozen`と`--python`なしの`uv sync --frozen`の後も3.14のままだった
- `UV_PYTHON=3.14`のまま`uv sync --frozen --python "$(cat .python-version)"`: 3.15で再作成された
- 対照として`.python-version`を除くと、版指定なしの`uv run --frozen`の後も3.14のままだった
- `uv pip install six`で加えたパッケージは`uv run --frozen`の後も残り、`uv sync --frozen`で除かれた

再検証は次を順に実行する。
最初の`cd`で作業ツリーの外に空の一時ディレクトリを作成して移動し、以降のファイル作成と`uv`の呼び出しはそのディレクトリだけを対象にする。
`uv run`の後の版が3.15、`UV_PYTHON=3.14`の2操作の後が3.14、`--python "$(cat .python-version)"`の後が3.15なら、記述どおりである。
処理時点のuvで版が異なれば、その版の観測へ記述を合わせる。

```sh
cd "$(mktemp -d)"
unset UV_PYTHON VIRTUAL_ENV
printf '[project]\nname = "observe"\nversion = "0.0.0"\nrequires-python = ">=3.14"\ndependencies = []\n' > pyproject.toml
echo 3.15 > .python-version
uv lock
uv venv --python 3.14 .venv
uv run --frozen python -c 'import sys; print(sys.version_info[:2])'
rm -rf .venv && uv venv --python 3.14 .venv
UV_PYTHON=3.14 uv run --frozen python -c 'import sys; print(sys.version_info[:2])'
UV_PYTHON=3.14 uv sync --frozen && .venv/bin/python -c 'import sys; print(sys.version_info[:2])'
UV_PYTHON=3.14 uv sync --frozen --python "$(cat .python-version)" && .venv/bin/python -c 'import sys; print(sys.version_info[:2])'
```

## agent-toolkit/skills/delegation/references/base-contract.md：基本委譲契約：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>は通常のサブエージェントを独立文脈、forkを親の会話履歴の継承として区別する。Codex CLI 0.157.1の`spawn_agent`スキーマも`fork_turns`で渡す履歴を選べる。再検証は各ホストの通常起動と全履歴forkの入力契約を比較する。

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

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：実行時能力と通信scope：2026年9月4日

2026年9月4日、agent-toolkit 2.94.0で通常起動と軽量起動の設定読込先およびStop系フックの動作を検証した。再検証は`agents_server`の通常起動と軽量起動を比較する。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：背景ジョブ完了通知と実プロセスの終了順：2026年9月21日

2026-09-21に、Claude Codeの背景ジョブ完了通知が届いた後も、同じ`atk agents wait`の実プロセスが稼働している事例を観測した。
通知後に`ps -eo pid,etimes,args`を実行し、PID 3566782、3566794および3566974の同じ待機が経過時間155秒で残っていることを確認した。
旧実装ではこの状態で後続の`atk agents wait`を発行すると、待機所有権が残っているため終了コード8になり得た。
同じ処理回の背景タスク`bjhq8zib4`では、警告1行だけを持つ1回目の`completed`通知後に後続の待機が終了コード8を返した。
呼び出し元が待機と照会を発行せずターンを終えた後、同じ背景タスクが結果本文を伴う2回目の`completed`通知を送った。
再検証は背景タスクの通知と`ps -eo pid,etimes,args`の結果を比較し、同じ待機プロセスの生存と後続の`atk agents wait`の終了コードを確認する。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：起動パラメーター：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>で、forkの背景実行、完了通知、forkからの再委譲制限とfork modeを設定しない場合の値を確認した。同版の対話セッションで`Agent`を`subagent_type: "fork"`として起動した観測では、`SubagentStart`が委譲先規範を追加した。再検証は対話と`-p`のそれぞれでfork modeを確認し、forkの起動結果、規範の配送および完了通知を比べる。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：待機中の定期再確認と背景転換：2026年10月4日

HEAD 2026年10月4日「fix: 不変条件のテストを通常のcommitで自動実行する」、`80ce39d82`、agent-toolkit 2.188.0の公開CLIで`atk watch --help`を確認した。
保持記録から解決した値を`atk watch --worktree "$worktree_path"`と
`atk watch --file "$artifact_path"`へ個別に渡し、両方の終了コード0を確認した。
前者は作業ツリーのdirty件数とHEAD、後者はファイルのlinesとageを返した。
Cronの発火や委譲sessionの終了はこの観測の対象へ含めず、状態と完了通知を用いる既存契約を保つ。
再検証は同じ公開ヘルプを取得し、その回の読み取り可能な作業ツリーと通常ファイルを保持記録から解決して、
上記の各コマンドへ渡す。cwd、コマンド、標準出力、標準エラーと終了コードを保存し、項目と受理形式を比べる。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：一時領域と成果物の観測：2026年10月9日

2026年10月9日10:49～10:50 JST、Claude Code 2.1.295で、メインが`Agent`を使って背景の委譲先1件を起動した。委譲先の作業は`sleep 5; echo`を4回実行するだけとした。稼働中の`output_file`へ`stat`と`stat -L`を対にして6秒間隔で3回実行した。リンク自体はサイズ153バイト・更新時刻10:49:55.606955323で固定だった。リンク先は通常ファイルで、サイズ231295、334100、337929バイト、更新時刻10:49:58、10:50:05、10:50:13へ進んだ。

観測は対象worktreeのcleanなHEAD `6c062a92ac0be19953900a48b14337992fdda7ca`で`atk run-command`を使って保存され、子終了コード0、標準エラー0バイトだった。再検証ではClaude Codeの版を記録し、同じ背景作業を`Agent`で起動する。起動結果の`output_file`へ`stat -c '%s %y %F'`と`stat -L -c '%s %y %F'`を同じ間隔で対にして実行し、リンク自体とリンク先の値を比較する。補助観測の更新は終了判定へ代用しない。

## agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月21日

2026年9月21日、Codex CLI 0.155.1で確認した。`spawn_agent`は`fork_turns`で会話履歴の引継ぎ範囲を選ぶ。起動した委譲先は対象worktreeの`AGENTS.md`と`SubagentStart` hookの追加本文を別入力として受け取る。`send_message`は稼働中の主体への追加配送、`followup_task`は同じ主体の継続、`wait_agent`は終了待機、`interrupt_agent`は中断を担う。agent-toolkitのCodex hookを実起動したテストでは共通の`rules-subagent.md`が追加され、Claude Code固有の`rules-subagent.claude-code.md`は追加されない。agents_serverの通常委譲も共通規範を持つ一方、軽量な探索・書込・shell promptは持たない。
再検証では`codex --version`で対象版を記録する。`rules_context_codex.main`へ`SubagentStart`を入力するテストと、`_agents_server/launch_prompts.py`の通常・軽量promptのテストを実行する。生成したCodex hook manifestの`SubagentStart`起動コマンドも実行し、共通規範、ホスト固有規範および軽量委譲の境界を確認する。

## agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月27日

2026-09-27、Codex CLI 0.157.1の`spawn_agent`公開スキーマで`fork_turns`の`none`、`all`、正の整数文字列と、全履歴forkのモデル・reasoning effort継承を確認した。再検証は現行ホストのツールスキーマを取得し、`fork_turns`と上書き引数の相互制約を確かめる。

## agent-toolkit/skills/delegation/references/routing.md：会話を引き継ぐ委譲：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>「Fork the current conversation」で、forkが親の会話履歴、モデルおよびツールを引き継ぎ、背景の再説明を減らす場面と同じ起点から複数案を試す場面に適することを確認した。Codex CLI 0.157.1の`spawn_agent`公開スキーマでは`fork_turns="all"`が全履歴を渡し、モデルとeffortの上書きを受け付けない。再検証は両ホストの公開スキーマと公式資料を読み、通常起動と全履歴forkの入力・モデルの差を比較する。

## agent-toolkit/skills/delegation/references/runtime-routing.md：modelとreasoning effort：2026年8月

本項は軽量モデルへreasoning effort `max`を割り当てた大きな作業で自動コンパクションが10回発生し、所要時間が大幅に伸びた観測に基づく（2026年8月、ユーザーからの報告）。再検証は同じ組合せで同規模の作業を1件実行し、自動コンパクションの発生回数を観測することによる。

## agent-toolkit/skills/delegation/references/runtime-routing.md：実行手段：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>で`Agent`の`fork`型を確認した。Codex CLI 0.157.1の`spawn_agent`公開スキーマは`fork_turns`省略または`"all"`の全履歴forkを示す。再検証は両ホストの起動スキーマを取得し、親のモデルとeffortの継承条件を確かめる。

## agent-toolkit/skills/delegation/references/wait-anomalies.md：終了コードと呼び出しの失敗：2026年9月20日

2026年9月20日、`atk agents wait`が`agents_server`の状態投影に対象を持たない状態を終了コード10で報告し、実行ホストの組み込み委譲は同じ状態投影へ登録されないことを確認した。再検証は`agents_server` sessionと組み込み委譲をそれぞれ起動し、`atk agents list`への登録有無、`atk agents wait`の終了コードおよびホストの委譲一覧が返すstatusを比較する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月2日

2026年9月2日に別のセッションが起動した背景ジョブの実行識別子を`TaskOutput`へ渡すと、`No task found with ID`で拒否された。一方、そのジョブの出力ファイルには絶対パスで到達でき、全出力と`[exited with code 0]`が残っていた。再検証は同じ形で背景実行し、終了済みセッションが残した出力ファイルを絶対パスで読めることと、同じ実行識別子を`TaskOutput`が解決できないことを対にして確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月3日

2026年9月3日から9月4日にかけて、`agents_server`の`start`で起動した委譲先が外部コマンドの完了待ちを表明し、`status: completed`で終了した。その後、待機対象のコマンドが終了しても委譲先は再開しなかった。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月4日

2026年9月4日、Claude Code 2.1.260で次の2点を確認した。終了コード7で終わる`run_in_background=true`のBashについて、起動結果が返した出力ファイルは出力の全量と`[exited with code 7]`を保持した。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：進捗照会と結果回収：2026年10月10日

Claude Code公式資料「Keep Claude working toward a goal」の「Background work defers evaluation」（<https://code.claude.com/docs/en/goal>、2026年10月10日取得）を読解した。背景のエージェントやシェルが稼働している間は評価を保留し、進捗を確認する通知と、完了結果による新しいターンを区別する仕様である。本改訂時にGoalを実機で起動した観測ではない。

2026年10月9日、Claude Code 2.1.295のセッションで次の順序を観測した。一次記録はリポジトリ外のセッション記録にある。Goal check-inの後、未完了の`atk agents wait`出力を`cat`で読み、同じ出力の2回目の読取がblockされて終了コード2となった。同じ応答で発行した`agents_server`の`list`は遮断の後に活動情報を返した。遮断後に新しく照会した因果にはしない。

再検証は同じ公式節を取得する。hookの判定は`background_task_outputs_test.py`で、明示背景化・実行上限・手動移行の未完了出力を同一sessionで2回読む入力、完了通知を加えた入力、別パスを読む入力を比較する。2回とも警告でblockが空、完了後は警告も空であることを確認する。Goalの自然発生を待たず、進捗の照会を終了時の結果へ代用しない。

## agent-toolkit/skills/process-wi/references/run-lanes.md「統合とAWI終了」：所有資源の回収：2026年9月3日

2026年9月3日にgit version 2.43.0で、upstreamを設定した専用branchをローカルの`develop`へ統合した直後に`branch -d`が未統合として拒否されることを確認した。再検証は`git branch -vv`で追跡先を確認して同じ状態の`branch -d`の終了コードを観測する。

## agent-toolkit/skills/review-standards/SKILL.md：レビュー指摘管理表の共通操作：2026年10月8日

agent-toolkit 2.199.0の基準commitは2026年10月8日「Merge pull request #147 from ak110/develop」、`3b3932b5`で、review-table実装の差分は無い。公開CLIで、通常名と`ci-3b3932b.exec-review.tsv`の表をそれぞれ`atk review-table init <パス>`で作成した。両方とも終了コード0で、`wc -c <パス>`は0バイトを示した。`atk review-table validate <パス>`も両方が終了コード0で、応答欄を含めた検証結果は0件だった。ヘッダーと仮の行は追加していない。

再検証ではmanaged-tempへ異なる通常名とCI名のパスを用意し、前記のinit、バイト数確認、validateを順に実行する。列の順序と旧形式の読み取り互換は`atk review-table --help`で確認する。

## agent-toolkit/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月7日

2026年9月7日、git 2.43.0で`assert expanded_common_job_count + len(statusline_jobs) == 7`を検索する`git grep -n`が一致0件を返し、同じ文字列を検索する`git grep -nF`が1件返すことを確認した。再検証は正規表現のメタ文字を含む行を対象リポジトリの追跡ファイルから1件選び、`git grep -n`と`git grep -nF`の一致件数を比べる。

## agent-toolkit/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（1）

2026年9月8日、ripgrep 15.2.0で実際に動かして確かめた。隠しディレクトリ配下の追跡ファイルだけが含む文字列に対し、リポジトリrootを対象とする`rg -n --fixed-strings`は一致0件を返した。同じ文字列を対象とする`git grep -nF`はその文字列を含む追跡ファイルを返した。再検証は隠しディレクトリ配下の追跡ファイルだけが含む文字列を1件選び、両コマンドの一致件数を比べる。

## agent-toolkit/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（2）

2026年9月8日、ripgrep 15.2.0で、隠しディレクトリ配下のファイルに存在する文字列が、`--hidden`の無い実行で一致0件となり、付けた実行で一致することを実際に動かして確かめた。再検証は隠しディレクトリ配下のファイルへ一意な文字列を1件置き、`--hidden`の有無で一致件数を比べる。

## agent-toolkit/skills/search/SKILL.md：引数とシェルの扱い：2026年9月14日

2026年9月14日、Git 2.43.0で実際に動かして確かめた。`grep.lineNumber=true`を指定した`git grep -h -m 1 -F -e <固定文字列> -- AGENTS.md`は`3:<本文>`を返し、同じ検索へ`--no-line-number`を指定すると`<本文>`だけを返した。再検証は`git -c grep.lineNumber=true grep -h -m 1 -F -e <固定文字列> -- <追跡ファイル>`と、`-h`を`--no-line-number`へ置き換えた検索の出力を比較する。

## agent-toolkit/skills/search/SKILL.md：引数とシェルの扱い：2026年10月9日

2026年10月9日、Claude Code 2.1.295のBashで`type grep`がシェル関数を返し、ugrep 7.8.4へ転送することを確認した記録に基づく。GNU grepは3.11だった。ignore・binaryの除外で一致する内容を持つ対象でも標準出力が空・終了コード1となった。`.{0,160}a.{0,160}`はugrepで複雑度エラーを標準エラーへ出力し、標準出力は空・終了コード2だった。上限を小さくした`.{0,30}a.{0,30}`も終了コード2で、繰り返しを片側だけにした`a.{0,400}`は終了コード0だった。同じテキストへの`command grep -o -E '.{0,160}a.{0,160}'`は`a`を出力して終了コード0だった。この節のClaude関数の観測は入力WIが保持する対照記録に由来する。

再検証ではClaude Bashで`type grep`と`grep --version`、`command grep --version`を取得する。専用領域に`a`を含む通常テキスト、ignore対象とbinary対象を用意し、関数の`grep`と`command grep`へ同じ対象・パターンを渡す。各標準出力・標準エラー・終了コードを保存して比較し、無出力の理由とパイプの後段で使う起動実体を区別する。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：語調と現行規則の表現：2026年9月16日

条文は2026年10月6日まで`llm-characteristics.md`「知識の想起」にあり、同ファイルの撤去に伴って`agent-documents-basics.md`「語調と現行規則の表現」の理由へ移った。

2026年9月16日、本リポジトリのHEAD `2ee94027`でコーパス分析とA/B実験を実施した。

コーパス分析の対象は、`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`、`.chezmoi-source/dot_claude/`、`.claude/skills/`配下のMarkdownと`AGENTS.md`とする。
現行本文137ファイル・9850文では、否定形で終わる文は1481文（15.0%）だった。`scripts/check_agent_doc_tone.py`の`_SUBJECT_WORD`が表す指示語は1393回（1000文あたり141回）現れた。
由来の区分は`git log --format='%H%x09%an%x09%(trailers:key=Co-Authored-By,valueonly)'`で全3578commitのトレーラーを取って行った。
`Codex`を含むものをCodex由来（107件、いずれも2026年8月）、`Claude`を含むものをClaude由来とした。
Codexが委譲先として書いた変更はClaude名義で記録されるため、Codex由来の件数は下限である。
追加文の抽出は各commitの`git show --format= --unified=0 -- '*.md'`の追加行から行い、コードブロック・表・見出しを除いて「」で分割した。
追加文1000文あたりの推移は、Codex利用前のClaude由来、2026年8月のCodex由来、2026年9月のClaude由来の順に次のとおりであった。
`_SUBJECT_WORD`の対象語23→55→163、「`主体`」0.6→7.6→17.5、「`正本`」0.4→10.4→35.4、「`契約`」0.2→8.3→11.0。
「`終端`」0.5→2.5→36.7、「`厳守規定`」0.8→5.6→18.2、「〜を根拠にしない」型2.9→7.8→10.2。
「〜だけを…しない」型0.7→6.8→6.3、否定形終端文の割合5.0〜8.0%→10.9%→15.7%。

A/B実験では、`agent-toolkit/rules/01-agent.md`の「判断指針」と「完遂と先送り」（112行）を条件Aの抜粋とし、同じ内容を平易な肯定形へ書き換えた99行を条件Bの抜粋とし、抜粋を読ませない条件Cを対照とした。
抜粋の否定形終端率はA19.5%・B12.1%、`_SUBJECT_WORD`の対象語はA34回・B0回、「厳守規定」はA7回・B0回である。
課題はスキルの1節の起草（10〜20文）、バグ修正方針の指示文（300〜600字）、ユーザーへの完了報告（200〜400字）の3件とし、各条件3回ずつClaude Opusの委譲先へ与え、条件を隠した匿名のファイル名で別の委譲先へ採点させた。
修正方針の課題では、範囲外の追加要素がA4.67件・B2.00件・C1.67件、過剰設計度がA3.00点・B2.00点・C2.33点であった。
成果物の否定形終端率は、スキル起草の課題でA17.9%・B9.0%・C22.9%、修正方針の課題でA30.5%・B16.5%・C27.6%であった。
反復は3回であり、統計的な検定は行っていない。完了報告の課題では条件間の差は現れなかった。

再検証は`uv run --frozen python scripts/check_agent_doc_tone.py --report <対象ファイル>`で現行本文の指標を取得し、上記の値と比べる。
再検証の契機は、規範文書の一括改訂と、文体の閾値の見直しとする。

2026年10月14日、`scripts/check_agent_doc_tone.py`の文境界を、句点とMarkdown構文解析が返す本文ブロックの末尾へ変更した。同じブロックの折返しは連結し、文末の物理行と抜粋を保持する。frontmatter、HTMLコメント、フェンス付きコード、見出しと表の除外、インラインコードの置換語、8%・3%・20文の判定条件は維持した。字下げコードも保存データとして分子・分母の共通対象から除外し、引用内の本文段落は引き続き数える。
入力WIの基準期間は次の方法で再測定した。最初のCodex由来commit 2026年8月2日「fix(agent-toolkit): プロジェクト規範を実態より優先してコミット帰属を統一する」、`4cef410f8`より前のClaude由来787commitについて、Markdown追加行をhunkごとに数えた。同じ集合で、句点だけの数え方は1400/16182文（8.65%）、ブロック末も数える方法は1879/22925文（8.20%）だった。この再測定値は入力WIの観測に由来し、2026年9月16日の区分ごとの記録と集計単位が異なるため、数え方による差だけを閾値維持の根拠とする。
再検証では前掲の`--report`を使い、段落、句点の無い箇条の項目、引用内の段落を個別に数え、同じブロックの折返しを1文として数えることを確かめる。規範の一括改訂後は`uv run --frozen pyfltr run-for-agent --commands=agent-doc-tone`をパス指定なしで実行し、全対象への到達と密度超過の診断の不在を確認する。上記の旧値と比べる場合は数え方と集計単位をそろえる。

## agent-toolkit/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日

配送範囲表のAntigravityの2列、作業ディレクトリのプロジェクト規範の行、agent-toolkitのスキルの行、Codexの列の`~/.codex/AGENTS.md`の配置条件の根拠である。

2026年10月6日、`agents_server`の`start`でツールを使わせない委譲先を起動し、初期コンテキストにある文書を答えさせて、各見出しの直後の1文の逐語引用を現物と比べた。
版はClaude Code 2.1.291（claude-agent-sdk 0.2.163）、codex-cli 0.160.1、agy 1.3.0である。
Claudeの`explore`は`rules/`配下だけが届き、`rules-subagent.md`、作業ディレクトリの`AGENTS.md`、スキル（`skills=[]`）は届かなかった。
Claudeの`delegate`は`rules/`配下、`rules-subagent.md`と`rules-subagent.claude-code.md`、作業ディレクトリの`AGENTS.md`、スキルが届いた。
Codexの`explore`は`~/.codex/AGENTS.md`経由で`rules/`配下と`rules-main.codex.md`、およびスキルが届き、`rules-subagent.md`と作業ディレクトリの`AGENTS.md`は届かなかった。`delegate`はこれらに加えて`rules-subagent.md`と作業ディレクトリの`AGENTS.md`が届いた。
Antigravityの`explore`と`delegate`はどちらも作業ディレクトリの`AGENTS.md`が届き、`rules/`配下は届かなかった。`rules-subagent.md`は`delegate`だけに届き、`delegate`のスキル一覧は作業ディレクトリの`.agents/skills`配下などだけでagent-toolkitのスキルを含まなかった。`explore`のスキルは確かめていない。
実装の根拠は次の3つである。`_agents_server/claude.py`の`_build_options`は軽量起動で`setting_sources=["user"]`と`skills=[]`を渡す。
`codex.py`の`_base_thread_config`は軽量起動で`project_doc_max_bytes=0`を渡す。
`antigravity.py`の`_system_prompt`は起動区分ごとの指示を本文の先頭へ置くだけで、読込範囲を変えない。
`~/.codex/AGENTS.md`は`scripts/sync_codex_agents.py`が生成し、Codex単体のインストーラーは配置しない（`docs/guide/codex-guide.md`冒頭）。
`Agent`ツールのサブエージェントとCodexの組み込み委譲先のスキルの列は観測していないため「未確認」とした。

再検証では`start`の`model_type`へ`codex:<model>`、`agy:<model>`、`claude:<model>`を指定し、ツールを使わない指示とともに同じ問いを渡す。
問いは初期コンテキストの文書一覧と、次の各見出しの直後の1文である。
見出しは`01-agent.md`「QCDと3段判定」、`02-agent-operations.md`「ツール・コマンド運用」、`rules-subagent.md`「確認事項の即時通知」である。
ほかに`rules-main.codex.md`「メインエージェント向け規範」と作業ディレクトリの`AGENTS.md`「詳細な参照先」を使う。

## agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルと公式のHooksリファレンスで確認した。再検証ではこの2つの資料を読み、対象の文字列がどちらに由来するか確認する。

## agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力上限：2026年10月20日

- 観測日: 2026年10月20日に[公式Hooksリファレンス](https://code.claude.com/docs/en/hooks#json-output)を再取得した。10,000文字の制限は各JSON文字列と通常の標準出力へ個別に適用され、超過時はセッション内の保存ファイルと最大2,000文字のプレビューへ置き換わる。標準エラー44,224文字が接頭辞込み44,346文字で全文届いた結果は、2026年10月19日05:33 UTCの既存調査記録に由来し、今回は再現していない
- 観測した版数: 標準エラー配送の既存観測はClaude Code 2.1.303。公式ページの再取得は2026年10月20日時点の公開内容
- 再検証手段: 固定したBash操作のPreToolUseへ、連番本文と末尾の`END-MARK-QZX`を標準エラーへ出力して終了2を返すhookを装着する。stream-jsonのツール結果との全文一致とモデルが回答した末尾を比べる。JSON各値と通常の標準出力は10,000文字の前後で保存先とプレビューを確認する。標準エラーの単一条件の成功から、配送量が無制限とは判断しない

## agent-basics/skills/writing-standards/references/claude-hooks-output.md：組織向け設定：2026年10月20日

- 観測日: 2026年10月20日の起草時調査記録にある、本体の読解と組織設定のユーザー表示の観測による。今回の文書追随では本体の読解を再実行していない
- 観測した版数: Claude Code 2.1.304。既存記録は2.1.302と2.1.303で該当通知が0件だった比較を含む
- 再検証手段: 対象版の`PASSED_OVER_EVENTS`・`PASSED_OVER_DOORS`・`passedOverPatterns`を公開仕様と比べ、全Function hooks登録へ同じ判定を適用する不変条件テストを実行する。組織設定下でセッション名の自動設定、手動名の保持と`rate_limit`後の再開予約を確認する

## agent-basics/skills/writing-standards/references/claude-hooks-stop.md：入力：2026年9月4日

2026年9月4日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節、`Stop`節および`SubagentStop`節で前段の入力仕様を確認した。同日、Claude Code 2.1.260のStopフックへ渡る入力を捕捉した。`run_in_background`で起動したBashジョブが、`type`を`shell`、`status`を`running`とする要素として`background_tasks`へ現れた。再検証は同3節を読み、Stopフックへ渡る入力を捕捉して`background_tasks`の有無と要素の構造を確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節と`SubagentStop`節で確認した。再検証は同2節を読む。

## agent-basics/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルへ埋め込まれたパターンマッチ関数を確認した。この関数は値が空文字列と`"*"`のいずれかのときに正規表現へ変換せず一致を返す。同ドキュメントにも同じ3分類が記載されていた。再検証は同ドキュメントの`Matcher patterns`節を取得し、`strings`で抽出した関数が空値と`"*"`を短絡することを確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日

2026年9月6日、Claude Code 2.1.263で次を確認した。地の文を1文書いた直後に同じ応答で`Bash`を1回呼ぶ指示を与え、その呼び出しの`PreToolUse`が受領した`transcript_path`の内容を捕捉した。フックの発火時点では、JSONLにアシスタントのエントリが1件も無かった。実行後の同じJSONLには、同一の`message.id`を持つ思考ブロック、テキストブロックおよびツール呼び出しの3エントリが並んでいた。再検証は同じ指示を与えて発火時点のJSONLの内容と実行後の内容を比較する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日

2026年9月8日、Claude Code 2.1.263で次を確認した。
存在しないリポジトリを指す`git -C /tmp log --oneline -1`は終了コード128で終わり、セッションの状態ファイルの`git_log_checked`は未設定のままだった。
続けて実在するworktreeを指す同じ形の`git log`を実行すると、そのcwdのキーが真になった。
再検証はこの2つのコマンドを単独で順に実行し、`session_state.state_path`が返す現行JSONの`git_log_checked`を前後で比較する。
当時の保存先は`{tempdir}/claude-agent-toolkit-<session_id>.json`だった。
現行の再検証では`state_dir()/hook-sessions`のJSONと固定の`.state.lock`を使い、clear・期限回収後もlockが同じパスへ残ることを確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年10月7日

2026年10月7日、Claude Code 2.1.292の環境で公式ドキュメント<https://code.claude.com/docs/en/hooks.md>を取得し、`PostToolUseFailure`節と`PermissionDenied`節を確認した。前者は実行を始めたツールの失敗で発火し、`additionalContext`を受理する。未知のツール名、入力検証の失敗と権限拒否では発火しない。後者はauto modeの拒否だけで発火し、手動の拒否、`PreToolUse`の遮断とdeny規則の一致では発火しない。出力は`hookSpecificOutput.retry`だけを受理する。再検証は同ドキュメントの両節を読み、発火条件と受理する出力を確かめる。

## agent-basics/skills/writing-standards/references/dependency-management.md：公開待機設定：2026年9月16日

2026年9月16日、公開直後の新バージョンを待つ目安1日の典拠を確認した。`.chezmoi-source/dot_config/uv/uv.toml`は`exclude-newer = "1 day"`を持ち、同ファイルのコメントが公開後24時間未満のパッケージを除外する目的を示す。再検証は同ファイルから`exclude-newer`の値を取得する。この設定値が変わったときに再検証する。

## agent-basics/skills/writing-standards/references/dependency-management.md：pnpm：2026年9月16日

2026年9月16日、pnpm 11.25.0とnpm 11.19.0で検証した。
環境変数`NPM_CONFIG_REGISTRY`へ`https://example.invalid/`を与えて`pnpm config get registry`を実行すると`https://registry.npmjs.org/`を返した。
同じ環境変数で`npm config get registry`を実行すると`https://example.invalid/`を返した。
再検証はpnpm 11.25.0とnpm 11.19.0に同じ環境変数を与える。両ツールから実効のレジストリー設定を取得し、環境変数が反映されるか比べる。
再検証の契機はpnpmまたはnpmの更改とする。

## agent-basics/skills/writing-standards/references/drizzle.md：Drizzle ORM／Drizzle Kit記述スタイル：2026年9月16日

2026年9月16日、参考実利用バージョンの取得元を確認した。`~/glatasks/package.json`は`drizzle-orm`を`^0.45.2`、`drizzle-kit`を`^0.31.10`で指定する。再検証は同ファイルから、この2つの依存の版指定を取得する。いずれかの依存が更新されたときに再検証する。

## agent-basics/skills/writing-standards/references/file-editing.md：ファイル更新時の編集ツールの前提：2026年10月7日

確認日は2026年10月7日、観測した版はClaude Code 2.1.292である。
`agents_server`で起動したClaudeの委譲先（auto mode）で、管理対象一時領域の1行のファイルを使って取得手段ごとにEditとWriteを発行した。
拒否されたのは次の4条件で、文面はいずれも`File has not been read yet. Read it first before writing to it.`だった。

- `printf ... >`で作成したファイルへのEdit
- `cp`で作成した複製へのEdit（複製元はReadで読んだ）
- `cat "$M/..."; sed -n 1p "$M/..."`の1回の連結で表示した2ファイルへのEdit
- 未読の既存ファイルへのWrite

同じファイルをReadで読んだ後の同じEditは成功した。
単独の`cat <絶対パス>`で表示したファイルへのEditは成功し、新しい主セッション（`claude -p`、既定の権限モード）でも同じ条件で成功した。

2026年9月30日にClaude Code 2.1.285で行った再検証では、新しい主セッションと`Agent`ツールで前景起動したサブエージェントで、単純な`cat`、forループの`cat -n`、1201行のファイルを読むforループのいずれでも、Readを経ない初回Editが成功した。
単独の`cat`の成功は2.1.292でも同じであり、拒否はBashでの作成、変数を含むパスや複数コマンドの連結、別パスのReadという取得手段の違いで生じる。
どのBashの読取をRead状態として扱うかは公開されていないため、条文はBashでの表示を前提として扱わない。

Claude Code本体（`~/.local/share/claude/versions/2.1.292`）を`rg -a`で検索すると、Editの検証は読取状態の記録（`readFileState`）にパスが無い場合に前記の文面を返す。
Editのツール説明は、編集の前に同じ会話でファイルを読むことを求め、読まずに編集すると失敗すると述べる。
Writeのツール説明は「Overwriting an existing file you haven't Read will fail.」を含む。

2026年9月7日以降に更新されたClaude Codeのセッション記録1736件（`~/.claude/projects`配下の`*.jsonl`、サブエージェントの記録を含む）では、Read不足で拒否されたツール結果が31件（Edit 26件、Write 5件）あり、17件の記録に分布した。
数えた層は`is_error`を持つ`tool_result`のうち前記の文面を含み、対応する`tool_use`のIDへ結び付いたものである。同じ期間のEditの呼び出しは1920件、Writeは2464件である。

再検証では管理対象一時領域に1行のファイルを準備し、主セッション、`Agent`ツールのサブエージェントおよび`agents_server`のClaudeの委譲先のそれぞれで発行する。
Readを経ない取得手段（Bashのリダイレクトでの作成、`cp`での複製、変数を含むパスと複数コマンドの連結での表示、単独の`cat <絶対パス>`での表示、同じ内容の別パスのRead）ごとにEditを発行し、未読の既存ファイルへWriteを発行する。
条件ごとに拒否の有無と文面、Readで読んだ後の同じ操作の成否を保存して比べる。版数は`claude --version`で取得する。

## agent-basics/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月9日

2026年10月9日、Codex CLI 0.162.0の公式ソース（タグ`rust-v0.162.0`）を読解した記録に基づく。`codex-rs/exec/src/cli.rs`の`--ephemeral`と`codex-rs/exec/src/lib.rs`の適用はセッションの永続化を抑止する。認証処理は`codex-rs/login/src/auth/manager.rs`と`codex-rs/login/src/auth/storage.rs`にあり、ChatGPTトークンの期限が5分以内か、最後のrefreshから8日を超える条件で更新し、認証ストレージへ保存する。`--ephemeral`はこの更新・保存を抑止しない。実機でrefreshを新たに起こす観測は行っていない。

再検証では同じ公式タグのCLIオプション、execへの適用、認証更新条件と保存処理を対応付けて読む。実機で測定する場合は公開された状態隔離手段と通常認証の成立を先に確認し、同じ実体のmtimeなどを前後で比較する。外部認証情報を隔離先へ複製しない。通常利用と既存のテスト隔離へ一律の隔離を追加する根拠にはしない。

## agent-basics/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月18日（1）

観測した日付は2026年10月18日、版数はClaude Code 2.1.302である。
再検証では再開・分岐を伴わない新規の`claude -p`を空のmanaged-tempで起動する。
まず`--allowedTools Read`だけを指定して実行する。
次に`--tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'`も指定して実行する。
両実行のstream-jsonの`system`/`init`にある`tools`と`mcp_servers`を比較する。

## agent-toolkit/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月18日（2）

観測した日付は2026年10月18日、版数はClaude Code 2.1.302とcodex-cli 0.163.0である。
`atk wi process-loop`と`atk run-skill`の可用性確認の固定プロンプトで、変更前の起動形（本作業の候補のモデルとeffortの通常起動）と変更後の軽量起動を1回ずつ比べた。
Claudeの変更前は`opus[1m]`・`medium`で、入力・キャッシュ作成・キャッシュ読取の合計が41,574トークン、`~/.claude/projects`の記録が1件増えた。
変更後は`haiku`・`low`に`--no-session-persistence --setting-sources "" --tools ""`を付けた。
さらに`--strict-mcp-config --disable-slash-commands --system-prompt <文> -- <プロンプト>`を付けて起動した。
合計は630トークン、記録の増加は0件で、`modelUsage`は`claude-haiku-5-5`へ解決された。
Codexの変更前は`gpt-6.1-sol`・`medium`で`turn.completed`の`input_tokens`が30,044、変更後は`gpt-6-luna`・`low`に`--ignore-user-config --ignore-rules`を付けて27,130だった。
`codex exec --model luna`はChatGPT認証で`The 'luna' model is not supported when using Codex with a ChatGPT account.`を返して終了コード1となった。
Codexは信頼されていないGit外のディレクトリでは`--skip-git-repo-check`なしの起動を拒否するため、Codexの計測は空のGitリポジトリをcwdにした。

再検証では空のmanaged-temp（Codexは空のGitリポジトリ）をcwdにして、変更前と変更後の可用性確認の起動形を1回ずつ実行する。
Claudeは`result`イベントの`usage`の入力・キャッシュ作成・キャッシュ読取と`~/.claude/projects`の記録ファイルの有無を、Codexは`turn.completed`の`usage`と`CODEX_HOME`のrolloutの有無を比べる。

## agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月2日

2026年10月2日、同書の典拠表の各資料を取得した。Claude Code公式資料（Claude Code 2.1.286の時点）の「For MCP server authors」は、ツール説明とserver instructionsを各2,048文字に切り詰めると記す。この値は`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`で変更できる。「MCP output limits and warnings」は1万トークンの警告と2万5千トークンの上限を記し、上限を変える`MAX_MCP_OUTPUT_TOKENS`と`_meta["anthropic/maxResultSizeChars"]`を示す。MCP 2025-11-25のSchema Referenceは`InitializeResult.instructions`をsystem promptへ加えてもよいヒントとし、`ToolAnnotations`の全項目をヒントとする。
再検証は各資料の該当節を取得し、切り詰めの文字数、出力上限、instructionsの扱いを比べる。値が変わった場合は同書の「ホスト固有の条件」と、後述の説明長のテストを改める。

既存の2サーバーへ同書の観点を適用した。判定は「適用済み」「任意の改善候補」「仕様上の是正」に分ける。任意の推奨との相違だけでは不良と判定しない。

agents_server（`agent-toolkit/agent_toolkit/agents_server_mcp.py`、MCP Python SDK 1.30.0）の公開ツールは`start`、`send_message`、`kill`、`stop`、`list`、`show`の6件である。`FastMCP.list_tools()`の取得結果と、各ツールの直接の応答生成（`_public_start_response`、`AgentsServerManager`の各公開メソッド、`_actionable_tool`）を読んだ。

- 適用済み: 起動操作は`start`の`mode`へ統合し、modeを列挙型でスキーマへ示し、modeごとの必須・禁止の入力を説明と`_validate_start_inputs`の双方に置く。応答は`session_id`と次の操作を返し、`list`は除いた件数を`omitted`へ、`show`は`verbose`で詳細を選ぶ。例外は`_actionable_tool`が`next_action`付きの`ToolError`へ変え、SDKがツール実行エラーとして返す。全ツールが構造化応答を返し、`outputSchema`は任意の項目を許すobjectである。stdio transportのログは標準エラーとファイルへ出力する
- 是正済み（同書の「単体での利用」とホスト固有の条件による）: 適用前の`start`の説明は2,932文字で、Claude Codeの設定を変えない状態では後半の結果受領・起動前の準備・応答の説明が切り詰められていた。結果受領を先頭へ移し、modeの選び方を`mode`の引数説明へ移して1,962文字とした。`send_message`・`kill`・`stop`・`show`の`session_id`、`send_message`の`prompt`、`list`の`include_terminated`、`show`の`verbose`は引数説明を持たなかったため説明を加えた。`_agents_server/tool_descriptions_test.py::test_tool_descriptions_fit_claude_code_truncation_and_describe_every_argument`が説明長と全引数の説明を確かめる。instructionsは690文字である
- 任意の改善候補: `outputSchema`は応答の項目を列挙していない。応答は`mode`や状態で項目が変わり、列挙の保守負担が大きいため、2026年10月29日の見直しでも列挙しない
- 適用済み（2026年10月29日）: 全ツールへ日本語の`title`と注釈を付けた。`list`と`show`は`readOnlyHint`、`start`と`send_message`は`destructiveHint: false`、`kill`と`stop`は`destructiveHint: true`である。`list`が内部で行う保持期限到来sessionの失効は、保持期限のタイマーが同じ時刻に行う失効を先に反映するだけで、呼び出しの有無と回数で観測できる状態が変わらないため、`readOnlyHint`を付けた。`_agents_server/tool_descriptions_test.py::test_tool_list_publishes_title_and_side_effect_annotations`が`tools/list`の応答の値を確かめる
- 仕様上の是正: 該当なし

pyfltrの公開ツールは`build_server`が登録する11件である。対象は`/home/aki/pyfltr/pyfltr/cli/mcp_server.py`で、対応する変更は2026年10月1日「fix: 置換失敗時の復旧とMCPの応答性を改善し、公開前の動作確認を追加する」、`c5aa7e2`である。観測環境のMCP Python SDKは2.2.0だった。読み取り専用で`list_tools()`の結果と各ツールの直接の応答生成を読んだ。

- 適用済み: 全ツールの`inputSchema`はobjectで、構造化結果は戻り値の型で検証してから返す。想定内の誤りは`ToolError`からツール実行エラーとして返り、`show_run`などは有効な値を得るツールを案内する。`grep`は件数上限、要約の選択、省略件数と`guidance`を返す。MCPのstdoutへは書かず、ログを標準エラーへ向ける
- 任意の改善候補: 公開される説明は`description=`の文字列だけで、引数の意味、組合せ条件、省略時の動作はクライアントへ届かないdocstringにある（例: `grep`の`max_count`の0の意味、`replace`の`within`と文脈行数の組合せ、`config`の`action`ごとの必須引数）。全ツールの引数に説明が無い。`run`の`mode`、`replace_history`と`config`の`action`は文字列型で列挙値がスキーマに無い。`show_run_output`は出力ログの全文、`show_run_diagnostics`は指定コマンドの診断の全件を返し、範囲・件数の指定と省略の通知を持たない。annotationsは全ツールで未設定で、書込を伴う`replace_undo`と`config`の説明は書き換えを明示しない。説明の言語が日本語と英語で混在する。instructionsは未設定である
- 仕様上の是正: 確定した該当なし。未確定として、workerプロセスからの結果JSONの読取に例外処理が無く、外部ツールがファイル記述子1へ直接書いた場合は汎用のエラー本文になる可能性がある（プロトコル違反ではない）。子プロセスの標準出力が常に取り込まれるかは確認していない

## agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月13日

2026年10月13日、Claude Codeの公式リリースノートを`gh release view <タグ> -R anthropics/claude-code`で取得した。2つの版の変更項目は次のとおりである。

```text
v2.1.295: Changed MCP tool descriptions the model loads through tool search to be cut at 16,384 characters instead of 2,048
v2.1.296: Changed the default limit on MCP tool descriptions sent up front and on MCP server instructions from 2,048 to 4,096 characters
```

取得元は[v2.1.295](https://github.com/anthropics/claude-code/releases/tag/v2.1.295)と[v2.1.296](https://github.com/anthropics/claude-code/releases/tag/v2.1.296)である。
前掲の2026年10月2日の記録にある2,048文字は、Claude Code 2.1.286の時点の公式資料の観測であり、本記録はその値を置き換えずに新版の仕様を記録する。2026年10月11日の時点の公式MCPガイドには2,048文字の記述が残っていたため、版を特定したリリースノートを根拠とする。上限を変える`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`は2026年10月2日の公式資料の記述であり、新版での名称は再確認していない。引数の説明への適用は両リリースノートとも記していない。

agents_serverは全説明とinstructionsを初期送信の4,096文字以内に保つ。検索後の16,384文字も同時に満たすため、検索の有無で説明を変えない。
説明長とinstructions長は`agent-toolkit/agent_toolkit/_agents_server/tool_descriptions_test.py`の次の2件が確かめる。

- `test_tool_descriptions_fit_claude_code_truncation_and_describe_every_argument`
- `test_lightweight_selection_reaches_instructions_and_mode_schema`

説明の先頭4,096文字に`EXPLORE_CAPABILITIES`が届くことは次の2件が確かめる。

- `launch_prompts_test.py::test_explore_capabilities_are_shared_with_public_schema`
- `mcp_tools_test.py::test_public_start_schema_delivers_explore_capabilities`

再検証は同じコマンドで後続版のリリースノートからMCPの説明とinstructionsの上限の変更項目を探し、公式MCPガイドの「For MCP server authors」と比べる。値が変わった場合は同書の「ホスト固有の条件」、`tool_descriptions.py`の`START_DESCRIPTION`直前のコメントと前記のテストを改める。

## agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月29日

2026年10月29日、公式マーケットプレイス`anthropics/claude-plugins-official`の複製`~/.claude/plugins/marketplaces/claude-plugins-official/`から`mcp-server-dev`の資料を読んだ。
複製の版は、同ディレクトリの`.gcs-sha`が`f8c5e7a1dcb71b10cb74586224b5401f3de5db28`、`~/.claude/plugins/known_marketplaces.json`の`lastUpdated`が`2026-10-29T07:27:00.345Z`である。
`build-mcp-server/references/tool-design.md`の「Tool annotations」は、`readOnlyHint`・`destructiveHint`・`idempotentHint`・`openWorldHint`の意味とホストの扱いを表で示す。
同節は、全読取ツールへ`readOnlyHint: true`を付けるよう勧める。
同書冒頭の「Anthropic Directory hard requirements」は、Directoryへ提出するサーバーの合否条件として、全ツールの`readOnlyHint`・`destructiveHint`・`title`、読み書きのツール分割、説明文での行動指示の禁止などを挙げる。
`server-capabilities.md`は`instructions`、sampling、roots、logging、progress、cancellation、completionを扱う。

再検証は同じ複製かGitHubの同じパスから各資料を取得し、注釈の推奨とDirectoryの条件を同書の「ツール注釈」と典拠表の注記と比べる。推奨が変わった場合は同書を改める。

## agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月29日

2026年10月29日、Claude Code 2.1.312の本体（`~/.local/share/claude/versions/2.1.312`）を`rg -a -o`で検索した。
MCPツールの呼び出しで`mayHaveRun`を持つ接続断の例外が起きた場合、本体は実行前の失敗か、ツールの`readOnlyHint===!0`のときに再試行する分岐を持つ。
別の再試行経路は、実行前の失敗、`isConcurrencySafe`、`annotations?.idempotentHint===!0`のいずれかで再試行する。
MCPツールの`isConcurrencySafe`は`annotations?.readOnlyHint??!1`を返すため、`readOnlyHint: true`のツールは並行呼び出しの対象にもなる。
権限の自動許可への注釈の作用は確認していない。

再検証は後続版の本体を`dotfiles-development`「ホスト本体のバイナリの検索」の手順で`mayHaveRun`と`isConcurrencySafe(){return`を検索し、再試行と並行実行の条件を比べる。条件が変わった場合は同書の「ホスト固有の条件」を改める。

## agent-basics/skills/writing-standards/references/notation-rules.md：逐語引用の検出範囲：2026年9月5日

本表は2026年9月5日に実際に動かして確かめた結果である。次の1文を地の文、引用ブロック、フェンス付きコードブロックへ置いた3つのサンプルファイルを作成し、pyfltr 3.17.8の`textlint`・`colloquial-check`にかけた。

```text
警告を出すと思う。
```

地の文では口語表現チェックとtextlintの弱い表現がいずれも検出され、引用ブロックではtextlintの弱い表現だけが検出され、フェンス付きコードブロックではいずれも検出されなかった。em-dash（U+2014）を含む同じ形のサンプルを`scripts/check_dash.py`にかけたところ、引用ブロックでは検出され、フェンス付きコードブロックでは検出されなかった。再検証は同じ3つのサンプルファイルを再度作成し、同じ手順で検出の有無を対にして確認する。

## agent-basics/skills/writing-standards/references/python.md：実行環境：2026年8月17日

2026年8月17日、Linux・uv 0.12.3で、依存メタデータが同一でパスだけが異なるPEP 723スクリプトを`uv run --script`で実行すると、スクリプトごとにvenvを再構築することを確認した。パッケージと解決結果のキャッシュは共有され、ウォーム状態での再構築は1秒未満だった。再検証は同じ`# /// script`ブロックを持つスクリプトを2つのパスへ置き、`uv --version`を記録したうえで順に実行し、uvのキャッシュディレクトリ配下の環境の数と所要時間を比べる。

## agent-basics/skills/writing-standards/references/python.md：静的解析の誤検出と抑制：2026年10月20日

- 観測日: 既存調査記録を2026年10月20日に確認した。元の観測日は未確認。同記録はLinuxで`sys.platform`による`msvcrt`と`fcntl`の分岐importを抑止なしで解析し、`msvcrt`だけに診断1件が出たと報告している。今回の文書追随では同じ解析を再実行していない
- 観測した版数: pylint 4.1.3、astroid 4.3.4
- 再検証手段: 同じ分岐importを一時Pythonファイルへ保存し、対象プロジェクトの`uv run --frozen pyfltr run --commands=pylint --no-fix <絶対パス>`で解析する。Windowsでは`fcntl`側の診断を対照取得する。pylintの`checkers/imports.py`にある`_ignore_import_failure`がOS分岐を除外しないことも確認する

## agent-basics/skills/writing-standards/references/python.md：テストコード（pytest）：2026年10月20日

- 観測日: 既存調査記録を2026年10月20日に確認した。元の観測日は未確認。同記録はpyfltrのWindows CI実行39222530624で`Popen.pid`と子の自己申告PIDの不一致を観測したと報告している。修正commit `60161d7`後の実行39227766095は成功とされる。今回の文書追随ではWindows上で再現していない
- 観測した版数: uvが作成したWindows runnerの仮想環境。観測したPythonの正確な版は未確認。Python公式`Doc/library/venv.rst`の3.7.2の変更記録はredirectorの仕様の根拠であり、観測版とは区別する
- 再検証手段: Windows仮想環境の`sys.executable`で子を起動し、`Popen.pid`と子の`os.getpid()`の標準出力を比べる。子の自己申告PIDとの一致を求める試験と、`Popen.pid`をプロセスの木の根として停止・生存確認に使う試験を分けて確認する

## agent-toolkit/skills/writing-standards/references/session-records.md：H1直下：2026年9月2日

2026年9月2日に`~/.claude/projects`配下と`~/.codex/sessions`配下の記録で確認した。再検証は両ディレクトリの記録から対象のキーを検索する。

## agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日

本節の記述は2026年9月3日に`agent-toolkit/skills/session-review/scripts/session_review_evidence.py`の`_latest_claude_usages`と`_stats_summary_data`を読んで確認した。再検証は同じ2つの関数を読む。

## agent-toolkit/skills/writing-standards/references/session-records.md：スキル起動の判定：2026年9月10日

2026年9月10日に確認した。`~/.claude/projects`配下でClaude Code 2.1.252と2.1.267の記録を読んだ。`Skill`ツールの`tool_use`要素が`input.skill`にスキル名を持つことを確認した。対応する`tool_result`要素の`content`は`Launching skill: <スキル名>`と一致した。
同日、`~/.codex/sessions`配下のrollout記録2169件のうち`agent-toolkit:exit-session`を含む1527件を対象に、先頭120件のレコード種別を集計した。このスキル名が現れたのは、`world_state`、`developer`ロールの`message`、`compacted`、`function_call_output`および`custom_tool_call_output`だけであった。
同日、codex-cli 0.154.0の同じ2169件に対し、`atk wi process-loop`がCodexへ渡す起動プロンプトの完全一致を数えた。一致は0件であった。いずれの記録も最初のuser役レコードの本文は実行環境が挿入する前置きであった。前置きは``# AGENTS.md instructions``または``<recommended_plugins>``で始まる。`agent-toolkit:process-wi`をuser役の本文へ含む記録は304件であった。この304件の`session_meta`を確認すると、`originator`は`agent-toolkit-codex-app-server`が303件、`codex-tui`が1件であった。
再検証はClaude Codeの記録から`Launching skill:`を含む行を1件取得して`tool_result`の構造を確認し、Codexの記録から同じスキル名を含む行を取得してレコード種別を確認する。あわせてCodexの記録から`atk wi process-loop`が渡す起動プロンプトの完全一致と包含の件数を数える。user役レコードの`text`の先頭が前置きであることも確認する。

## agent-toolkit/skills/writing-standards/references/session-records.md：Claude Codeの記録：2026年10月6日

2026年10月6日にClaude Code 2.1.291の本体（`~/.local/share/claude/versions/2.1.291`）を確かめた。出力の項目`narration_block_indexes`の説明は該当する`thinking`ブロックを「server summaries of the prose between tool calls, not the model's own reasoning」と記す。
本体のバイナリを`rg -a -F 'server summaries of the prose between tool calls'`で検索して再検証する。検索の手順は`.claude/skills/dotfiles-development/SKILL.md`「ホスト本体のバイナリの検索」に従う。

## agent-toolkit/skills/writing-standards/references/session-records.md：暗号化された値：2026年10月7日

2026年10月7日に公式文書「Thinking」（<https://platform.claude.com/docs/en/build-with-claude/thinking>、同日取得）を確かめた。節「Thinking encryption」は`signature`を「The `signature` field is opaque: don't interpret or parse it.」と定め、`redacted_thinking`の`data`を「opaque and encrypted」と記す。
同じ文書の節「Progress updates between tool calls」は、`display`が`"updates"`のとき「any `thinking` block with non-empty text is a progress update」と記す。
同日、codex-cli 0.160.1の記録（`~/.codex/sessions/2026/10/06/rollout-*.jsonl`）で、`encrypted_content`を持つレコードの`type`が`response_item`、`payload.type`が`reasoning`であることを確かめた。観測はいずれも値を開かずに行った。
公式文書の2つの節を取得して前掲の記述を確かめ、Codexの記録で`encrypted_content`を持つレコードの`payload.type`を数えて再検証する。

## セッション記録のAPI要求の写し：2026年10月18日

既存調査はClaude Code 2.1.301の記録`7542c54d-c7b2-4bca-9e29-c9199e63c7a4.jsonl`の1022行と1031行に、同じtool_use IDを持つ`assistant`と`api-request-blob`があると報告した。写しは`message`に会話と同じ形のcontentを持ち、timestampとuuidを持たない。公開仕様での形式の保証は未確認である。
今回の検証は、報告された構造を最小化した検体へツール呼出・失敗結果・完了通知の写しを追加し、通常抽出・呼出照会・警告・統計・ユーザー発話の結果が追加前と一致することを確かめる。生の行のdetail・grepは写しも表示する。再検証は`session_evidence_record_boundary_test.py`の`test_request_copies_do_not_create_conversation_events`を実行する。

## agent-basics/skills/writing-standards/references/sqlalchemy.md：問い合わせと属性代入の順序：2026年9月14日

2026年9月14日、SQLAlchemy 2.0.52の公式文書で、`autoflush`を無効にしていない`Session`がORM対応の問い合わせ前に保留変更をflushすることと、`Session.flush()`で明示的にflushできることを確認した。
2026年9月13日のAWIは、SQLAlchemy 2.0.51を使うアプリケーションで、保留中のUPDATEが一意制約へ違反するケースと、保留中のINSERTが`NOT NULL`制約へ違反するケースを確認した記録を持つ。
同記録ではautoflushの無効化が同じ処理単位で追加した設定を読むテストを失敗させ、入力検証前の無条件な問い合わせが`Session`未開始のテストを失敗させた。
再検証ではSQLAlchemy 2.0系の公式文書にある`Session Basics`の`Flushing`節と`Session.flush()`のAPI説明を確認する。
あわせて、保留中のUPDATEとINSERTの後にORMへ問い合わせるテストで、問い合わせ前に各制約違反が送出されることを確認する。
問い合わせを属性代入前かつ入力検証後へ移し、保留変更の反映が必要な箇所だけ明示的にflushした状態で、入力エラーと同じ処理単位の読み取りを対にして確認する。

## agent-basics/skills/writing-standards/references/sub-agents.md：Claude Codeのfrontmatter：2026年8月19日

2026年8月19日、`skills`で宣言したスキルの本文がサブエージェントへ注入される際に、所在ディレクトリの絶対パスの表示が付随することをサブエージェントの記録で確認した。公式ドキュメントはこの表示を記載していない。再検証は`skills`を宣言した定義から起動したサブエージェントの記録を読み、注入されたスキル本文の直前または直後に所在の絶対パスが現れるかを確認する。

## agent-basics/skills/writing-standards/references/sub-agents.md：コンテキスト境界：2026年9月13日

2026年9月13日時点で、`main`と名前付きエージェントを列挙した名簿はClaude Code v2.1.206以降の版で付くことを確認した。再検証は`tools`に`SendMessage`を含む定義を、ほかの名前付きエージェントが稼働するセッションから起動し、サブエージェントの記録に名簿が現れるかと`claude --version`の版数を確認する。

## agent-toolkit/skills/writing-standards/references/sub-agents.md：コンテキスト境界：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>とCodex CLI 0.157.1の`spawn_agent`公開スキーマで、通常起動とforkの履歴入力が異なることを確認した。再検証は同じ起動文で通常起動と全履歴forkを行い、委譲先に渡る会話履歴の有無を比較する。

## agent-basics/skills/writing-standards/references/textlint-violations.md：文体と箇条書き：2026年9月10日

2026年9月10日、pyfltr 3.17.9のtextlintと本リポジトリの`.textlintrc.yaml`（`preset-jtf-style`の`1.1.3.箇条書き`を`shouldUsePoint: false`で運用する設定）で確認した。
全ての項目が句点で終わる順序付きリストは同ルールへ一致0件であった。
同じリストの1項目へ空行で区切った入れ子の箇条書きと段落を追加すると、句点で終わる他の4項目へ箇条書きの文末から句点を外す指摘が返った。
入れ子を独立した節へ移して各項目を1行に戻すと、再び一致0件であった。
再検証は同じ順序付きリストについて入れ子を含む写しと含まない写しを作成し、`--commands=textlint`を指定した同じコマンドで一致件数を比べる。

## agent-basics/skills/writing-standards/references/x-posts.md：投稿の同定と本文の取得：2026年10月10日

2026年10月10日、FxEmbedの公開原本を読解した。API版とURLは[API introduction](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)、v1の処理は[src/realms/api/router.ts](https://github.com/FxEmbed/FxEmbed/blob/main/src/realms/api/router.ts)、応答の`code`・`message`・`tweet`は[src/embed/status.ts](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)、本文・日時・写真の属性は[api-status.ts](https://github.com/FxEmbed/FxEmbed/blob/main/packages/atmosphere/src/types/api-status.ts)と[api-schemas.ts](https://github.com/FxEmbed/FxEmbed/blob/main/packages/atmosphere/src/types/api-schemas.ts)へ対応付けた。v1は`/:handle/status/:id`、v2は`/2/`付きであり、資料のv1手順をv2の応答構造へ読み替えない。

元ホストの取得条件はリポジトリ外の作業記録に由来する。X直接取得のHTTP 402、APIのHTTP 307、pbs画像の取得成功とWindows curlの失効確認エラーはその環境の記録であり、本改訂時に同じ通信を再現した結果でも全ホスト共通の仕様でもない。

同日の追加読解では、API introductionのResponse FormatがHTTP状態とは別に`code`を確認する必要を説明し、v1の応答実装が数値の`code`、200の`OK`、401・404・503・500の失敗を分岐し、API応答のHTTP状態にも`code`を設定することを確認した。最終HTTP 200かつ数値のcode 200という採用条件を手順へ明示した。これは公式実装の読解の記録である。通信失敗や写真なしの実通信は新たに再現していない。

再検証では前記原本のv1の処理と応答生成を取得し、属性と版を比較する。通信を試す場合は公開投稿のURL・最終URL・HTTP状態・応答本文を保存し、最終HTTP 200と数値の`code` 200の両方、必要な`tweet`の属性の成立を確認してから画像URLをmanaged-tempへ取得する。HTTPだけの成功、codeの不一致・欠落・型の違い、通信とJSONの失敗、必要属性の欠落を未取得として区別する。成功応答で写真の一覧が空か写真媒体がない場合の「写真なし」と、写真の取得失敗も区別する。画像を実際に読んだホストと保存先を記録し、通信失敗の本文を画像や投稿の内容として扱わない。

## docs/development/design-hooks.md：通知の整形と反復検出：2026年10月13日

2026年10月13日、Claude Code 2.1.296の環境で公式の[Hooks reference](https://code.claude.com/docs/en/hooks)を取得した。「Common input fields」は`session_id`を現在のセッション識別子、`transcript_path`を会話のJSONへのパスとし、transcriptは非同期に書かれるため現在のturnの最新メッセージを含まない場合があると記す。「SessionStart」はmatcherを`startup`・`resume`（`--resume`、`--continue`、`/resume`）・`clear`・`compact`・`fork`とし、v2.1.214より前はforkしたセッションが`resume`を報告したと記す。
`rules_context`はこの入力の`session_id`と`transcript_path`をClaude Codeのメインへの通知に使う。`agent-toolkit/agent_toolkit/_hooks/rules_context_test.py`が、会話ごと・切替ごとの入力と出力の対応と、managed-tempの作成失敗時の通知を確かめる。

再検証は同じ資料の2節を取得して項目名とmatcherの値を比べ、制御したSessionStart入力をhook handlerへ渡して`additionalContext`の値を確かめる。項目名か値が変わった場合は`rules_context.py`の通知、`agent-toolkit/share/rules-main.claude-code.md`「自身の会話の記録」と同テストを改める。

## docs/development/design-hooks.md：通知の整形と反復検出：2026年10月20日

2026年10月20日、Claude Code 2.1.304で、開始時と別のディレクトリで再開すると、SessionStart入力の`transcript_path`が再開時のディレクトリに対応する実在しないパスを示した。同じ会話のPreToolUse入力63件は、開始時のworktree側にある実在する記録を示した。
今回の文書追随では元ホストで再現していない。再検証では専用会話を開始時と別のディレクトリで再開し、SessionStartと同じ会話のPreToolUseの`transcript_path`およびファイルの実在を比べる。通知は同じsession IDの完全一致で1件へ解決し、0件では新規会話の未作成を許容して入力値を保ち、複数件と探索失敗ではID指定による取得を案内する。一次記録はリポジトリ外の作業記録にある。

## docs/development/design-agents-runtime.md：MCP接続失敗の再接続：2026年10月20日

2026年10月20日、LinuxのPython 3.15でMCP再接続の契約を検証した。
対象は`agent-toolkit/agent_toolkit/_agents_server/claude_connections_test.py`の`test_mcp_reconnect_is_once_per_new_and_resumed_process`である。managerの公開start/showとSDKクライアントのテストダブルを組み合わせ、接続失敗のキャッシュによる省略と`CONNECT_TIMEOUT`、再接続の回復成功と失敗の4条件で、新規起動と再開の各プロセスの再接続が1回であることを確認した。
未回復のサーバー名と初期エラーはstart/showのwarningsと`claude_mcp_recovery_failed`のロガー出力へ残り、回復成功時はwarningsが消える。同じ4条件で再接続をno-opへ差し替えた対照は4件とも失敗し、正常な処理では4件とも成功した。
再検証は同テストと`test_slow_mcp_recovery_keeps_start_budget_and_updates_show`をpytestで実行し、新規・再開の呼出回数、未回復warnings、回復後のshowとログを比較する。ログの保存先は`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`と`mcp_tools_test.py`の起動・終了診断の試験で確かめる。この観測はテストダブルによる契約検証であり、実Claude CLIと外部MCPサーバーの接続障害を再現したものではない。

## agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日

2026年10月19日、Codex CLI 0.163.0で、戻り値の一部だけを表示すると識別子を失い、待機を重ねた。戻り値全体を表示した対照では、`write_stdin`で1つの待機を回収した。今回の文書追随では保存済みの観測記録を比較しており、実機では再現していない。
再検証では、長時間コマンドのyield結果が`session_id`を持ち`exit_code`を持たないこと、`text(r)`で両欄と本文が保持されること、同じ識別子で終了を回収できることを比べる。一次記録はリポジトリ外の作業記録にある。

## docs/development/design-hosts.md：Claude CodeとCodexの規範配置：2026年9月13日

2026年9月13日、Codex CLI 0.154.0でローカルmarketplaceを隔離`CODEX_HOME`へ導入して検証した。`agent-toolkit/`直下にAgent Plugins用`plugin.json`がある構成では、`.codex-plugin/plugin.json`のhook定義よりroot manifestが優先され、app-serverの`hooks/list`は0件を返した。root manifestを除いたwrapperから相対シンボリックリンクでhook・skill・実行資源へ接続した構成では、公式CLIのsnapshotに`.codex-plugin`だけが残り、リンク先は含まれなかった。全資源を通常ファイルとして含む`agent-toolkit-codex/`では、`hooks/list`が8イベントを返した。対象は`sessionStart`、`subagentStart`、`preToolUse`、`postToolUse`、`permissionRequest`、`userPromptSubmit`、`subagentStop`、`sessionEnd`である。project trustの有無で登録集合は変わらなかった。再検証ではCodex CLI 0.154.0で`scripts/sync_codex_plugin_manifests_test.py::test_codex_0154_registers_all_hooks_independent_of_project_trust`を実行する。隔離した2つの`CODEX_HOME`における登録集合、SessionStartの管理一時領域生成、SessionEndの回収を確認する。

## dotfiles-development：回収予定の作業場所の環境準備：2026年10月1日

2026年10月1日、uv 0.12.21を使い、専用worktreeのrootで`env --unset=UV_FROZEN uv sync --locked --all-groups --all-extras`を実行した。終了コードは0で、標準エラーには135パッケージの解決と133パッケージの確認が記録された。続く`uv run --frozen python`でpytools、agent_toolkit、pytestのimportが成功した。
同期の前後で、`uv tool dir`配下の導入記録と共有ツールのPythonから取得したeditable導入元を比較し、変更が無いことを確認した。Git共通dirのhookの内容・実行権限・リンク先と、`git config --local --get commit.template`の値も変わらなかった。アクセス時刻は比較対象へ含めていない。
共有ツールの導入記録とeditable導入元、Git共通dirのhook、commit.templateを保存した後、回収予定の作業場所で上記の同期を1回実行して再検証する。importの成功と登録内容の一致を確認する。恒久作業ツリーの初期導入を検証用に再実行する必要は無い。

## dotfiles-development：ホスト本体のバイナリの検索：2026年10月4日

ホスト本体の検索手順に関する変更は、2026年10月4日「fix: ホスト本体の検索手順をdotfilesの開発スキルへ集める」、`a896a6d06`である。
要求を起草した時点の測定環境はClaude Code 2.1.288、codex-cli 0.160.0、ripgrep 15.2.0、GNU grep 3.11、Claude Code組込みugrep 7.8.4で、UTF-8ロケールだった。
Claude CodeのBashツールでgrepはugrepを呼ぶシェル関数として観測された。

| 対象とコマンド | 起草時の測定 |
| --- | --- |
| Claude本体、`rg -a -c -F showThinkingSummaries` | 7行、0.11〜0.22秒 |
| 同本体、`rg -a -o '.{0,80}showThinkingSummaries.{0,80}'` | 11件、0.04秒、JSソースの前後80文字まで取得 |
| 同本体、`rg -a -o -m 2 '.{0,200}showThinkingSummaries.{0,200}'` | 文字列表の2件、74バイト。JSソースを含まない |
| 同本体、`rg -a -o '.{0,300}showThinkingSummaries.{0,300}'` | 9件、0.11秒 |
| 同本体、`rg -a -o '.{300}showThinkingSummaries.{300}'` | 7件、0.12秒 |
| 同本体、UTF-8のugrep・GNU grepで`-a -o -E '.{0,300}showThinkingSummaries.{0,300}'` | 30秒で打切り、終了124。GNU grepの`LC_ALL=C`では1.33秒 |
| 同本体、`grep -a -o '.\{300\}showThinkingSummaries.\{300\}'` | 7件、1.4〜2.7秒。120秒を超えた回もあり、差を生む条件は未特定 |
| 同本体、`strings -n 6`をファイルへ保存 | 1.72秒、55311970バイト |
| Codex本体、`rg -a -c -F fork_turns`と`rg -a -o '.{0,80}fork_turns.{0,80}'` | それぞれ0.73秒と0.12秒 |

実装時は`command -v`と`readlink -f`でClaude Code 2.1.289とcodex-cli 0.160.0の実体を解決した。
同じripgrep 15.2.0で、60秒上限の`-a -c -F`はClaudeが7行・0.04秒、Codexが9行・0.05秒だった。
`-a -o`の前後0〜200文字の文脈はそれぞれ0.04秒・0.05秒で、全て終了0・stderr空。
ClaudeではJSの設定説明、Codexではfork_turnsのヘルプ文を含む全出力をファイルへ保存した。
再検証は実体を同じ解決形で取得し、上記の語をdotfiles-developmentの一致行数と文脈取得の各コマンドへ渡す。
ホスト版、検索ツール版、ロケール、所要時間と一致行数を保持し、文字列表より後ろにある文脈も使って判断する。

## completion-report：最終報告送達後のターン終了：2026年10月9日

出所はリポジトリ外に保存した対照の記録である。本記録の作成時にモデルは再実行していない。Claude Codeメインがモデル`claude-opus-5-5`へ、同じ短い最終報告の送達入力と、送達後の地の文を省略記号だけで閉じる指示を加えた入力を各3回渡した。起動形は`claude -p --model claude-opus-5-5 --allowedTools mcp__agent-toolkit__send_to_user --output-format json`で、各JSONの`result`（送達後の最終地の文）を比較した。

| 入力 | 追加説明 | 省略記号 | 終了状態 |
| --- | --- | --- | --- |
| 終了の具体化なし、3回 | 1回 | 2回 | 全件success、num_turns=2 |
| 終了の具体化あり、3回 | 0回 | 3回 | 全件success、num_turns=2 |

再検証は同じ報告本文を固定し、後段の終了指示の有無だけを変えて上記起動形で各群を実行し、JSONのresult・success・num_turnsを保存する。モデルとホスト版も取得時に記録する。これは具体化が作用した限定条件の観測であり、元の長い文脈の内的判断、一般発生率、将来の出力保証を示さない。元事象のセッション記録（リポジトリ外）では、固定文付きの送達、表示の成功、追加の要約がこの順に並び、工程10の追加作業を限定する条文は同じ記録の送達より前に読み込まれていた。条文の不在を原因とはしない。

## dotfiles-development：Git動作テストの状態準備：2026年10月9日

出所はリポジトリ外に保存した対照結果と、導入commit `b20e6f20514a381648c730bbf828e2f8ac9e3522`、修正commit `f071d6348670a39e797e9dc54f6276e1e9dad8f6`の実装である。対象は`agent-toolkit/agent_toolkit/_hooks/pretooluse/shell_checks_test.py`の`TestBashGitRevParseShortMultiple.test_individual_revision_fix_succeeds`。入力は`git rev-parse`のoption `--short`、`--short=12`、`--verify`、`--verify --quiet`とrevision `HEAD`、`HEAD~1`の8組である。

| 履歴条件 | HEADの4入力 | HEAD~1の4入力 |
| --- | --- | --- |
| 通常履歴 | 全件終了0 | 全件終了0 |
| HEADを浅い境界とする深さ1相当 | 全件終了0 | optionの上記順に128・128・128・1 |

再検証は一時repoに2commitを作成し、通常履歴と同じHEADを浅い境界とするrepoで上記8入力をそれぞれ実行する。対象repoを明示し、入力・履歴条件・終了コード・両出力を保存する。修正後のテストは`init_repository`と`commit_all`で必要な2commitを用意し、必須repo引数を`git -C`へ渡す`run_git`で検証する。`_testing/isolation.py:isolate_development_state`は設定と環境を隔離するが、cwdの変更やcommit生成は行わない。この違いを設定隔離の成功だけから履歴準備の成立へ読み替えない。
CI run `37887505483`の失敗と`37889681072`の成功はリポジトリ外の確認記録に由来し、本記録の作成時にCIは再照会していない。

## プロジェクト指示のAGENTS.md対応：2026年9月20日

2026年9月20日、Claude Code v2.1.277の公式リリースノート<https://github.com/anthropics/claude-code/releases/tag/v2.1.277>で、`CLAUDE.md`が無いプロジェクトで`AGENTS.md`を読む機能が追加されたことを確認した。再検証は同バージョンのリリースノートを読み、`AGENTS.md`対応の記載を確認する。

## プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日

2026年9月26日、Claude Codeの公式文書<https://code.claude.com/docs/en/memory.md>で、設定を変えていない状態では`CLAUDE.md`・`.claude/CLAUDE.md`・`CLAUDE.local.md`のいずれかがあると`AGENTS.md`を読まないことを確認した。
該当する記載は次のとおりである。

```text
Because `CLAUDE.local.md` counts, adding one to keep your own uncommitted instructions in a project that relies on `AGENTS.md` stops Claude from reading `AGENTS.md` for you.
```

同日、Claude Code 2.1.283の`claude -p --model haiku`で観測した。
合言葉を書いた`AGENTS.md`と`CLAUDE.local.md`だけを置いたディレクトリでは、合言葉を答えなかった。
`claudize`が置く`# CLAUDE.md`と`@AGENTS.md`の2行のアダプターを加えると、合言葉を答えた。
再検証は同じ構成の一時ディレクトリで、アダプターの有無ごとに`claude -p`へ合言葉を尋ねる。

## agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月9日

Claude Code 2.1.295で、基準commit `4573be59230a11bdc9d717a1a1e03d994abf284f`に本機能の未commit差分を加えたプラグインを検証した。
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`を設定し、`--plugin-dir`に作業ツリーのagent-toolkitを指定した。

対話UIでは、モデルが`compact_conversation`へ任意指示を渡した後に現在ターンを終えた。
PTYに予約の受付と、その後の`Conversation compacted`・`Compacted`表示が保存された。
人による`/compact`入力は無く、session `f616210b-3a9d-4a2e-a067-30a004973d93`は正常終了した。

非対話では`claude -p --verbose --output-format stream-json --include-hook-events`を使用した。
モデルがBashのバックグラウンドタスクで65秒待機を起動し、予約後にターンを終えた。
同じsession `b276a7e9-9b4d-435c-b7fb-85010d6cd575`でPreCompact・PostCompactを観測した。
その後にmanual compact境界とホストの圧縮成功結果が現れた。
待機終了のtask-notificationから同じ会話が再開し、圧縮指示に指定した応答`COMPACT_BACKGROUND_RESUMED`を返して正常終了した。

故障の試行は隔離プラグインの`command.run`呼び出し直前に一度だけ例外を注入した。
実ホスト自身のコマンド拒否を観測した試行とは区別する。
session `1e19cda7-eaf3-49dd-b96c-916c43f077d1`へ失敗原因と再予約可能の通知が届いた。
モデルは再予約し、同じ会話でmanual compact境界と圧縮成功を観測して正常終了した。
未完了予約の重複排除と成功後の再予約は、ツール登録から時計のコールバックまでを動かす結合試験でも確認した。

以上はinstructionsが存在した当時の観測であり、任意指示と指定応答も当時の事実として保持する。現行契約は引数なしの予約と注入された要約と既存記録からの再開である。

再検証では隔離ディレクトリで作業ツリーのプラグインを読み込み、対話と非対話の両方でモデルへ引数なしの予約を指示する。圧縮のための事前保存は行わない。
受付応答、ターン終了、ホストの圧縮成功を順に記録する。
非対話はBashのバックグラウンドタスクと組み合わせ、圧縮後に注入された要約を起点として同じ工程を再開できるか確認する。要約だけでは足りない情報は既存の担当別記録・保存出力・状態照会から補い、同じsessionと担当・監視対象が続行することも確かめる。任意指示で応答文字列を渡さない。
故障と再予約は隔離した検証用プラグインだけへ例外を注入して試し、製品のプラグインを改変しない。

## agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月23日

Claude Code 2.1.309の`claude plugin test agent-toolkit`で、公開modの登録・時計・turn・背景タスク・圧縮APIを使う結合試験を実行した。
`session.compact()`はturn間に呼び、成功のmessages、見送りのskip、例外を別の結果として扱う。
5秒未満、利用率49%、状態未保存、委譲先とサブエージェントは自動圧縮しない。
50%の境界、5秒後、turn完了への持越し、完了通知による取消し、失敗後の解放と自動再試行の抑止、閾値未満を経た再開を確認した。
Python側の公開`wait-compaction-context`も、所有・mode・待機登録・保存時刻の12ケースで確認した。
modの全136件は成功した。外部モデルによる実際の要約生成はこの試験へ含まない。
再検証は同じ公開testコマンドと`wait_compaction_context_test.py`を使い、実際の要約生成を調べる場合は同月9日の隔離セッション手順で、予約・ホストの結果・待機の再開を分けて観測する。

## agent-toolkit/skills/user-confirmation-and-report/references/usage-limit.md：利用上限後の再開：2026年10月14日

- 観測日: 2026年10月14日（起草時の観測報告に基づく記録）
- 観測した版数: Claude Code 2.1.299
- 再検証手段: [Interactive mode](https://code.claude.com/docs/en/interactive-mode#wait-for-a-usage-limit-to-reset)でRemote Controlと`-p`の対象範囲を確認する。手元に保存したRemote Control接続のメイン記録を、下記の公開操作で照会する。通知冒頭、解除予定時刻と解除後の新turnの有無を対応付ける。起草時の報告は猶予通知後にユーザーの発話で再開した経過を持つ。元のsession記録はリポジトリ外の私有記録であり、同梱していない。自然発生する上限の実機再現は今回の文書同期の対象外とした

`<対象session ID>`を対象のClaude session IDに置き換えて、猶予通知の記録位置を取得する。
IDは対象の会話で実行するシェルの`CLAUDE_CODE_SESSION_ID`から確認できる。
そのsessionのtranscriptを保存しているOSアカウントで次のコマンドを実行する。

```sh
atk run-script session-review-evidence -- --claude-session-id '<対象session ID>' --fixed-string 'Usage limit reached; a short grace allowance remains'
```

`<record>:<line>`を返却された`record`と`line`の組に置き換えて、同じsession IDで全文を取得する。
拒否と解除後のturnの時系列は、照会オプションを付けずに同じsessionを取得して確認する。

```sh
atk run-script session-review-evidence -- --claude-session-id '<対象session ID>' --detail '<record>:<line>'
atk run-script session-review-evidence -- --claude-session-id '<対象session ID>'
```

元のtranscriptの絶対パスが分かる場合は、`--claude-session-id`の指定を`--transcript '<transcriptの絶対パス>'`へ置き換えられる。
引数の定義と記録が見つからない場合の診断は`atk run-script session-review-evidence -- --help`で確認する。

## docs/development/design-hooks.md：利用上限後のメインの再開予約：2026年10月14日

- 観測日: 2026年10月14日（起草時の観測報告。公式資料は2026年10月16日に再確認）
- 観測した版数: Claude Code 2.1.299
- 再検証手段: [mods API](https://code.claude.com/docs/en/plugins/mods/api)と[reference](https://code.claude.com/docs/en/plugins/mods/reference)でusageのrateLimits、clock.afterとprompt.submitを確認し、機能別modのテストで偽の使用率・時計・turnを与える。StopFailureのrate_limit、猶予後のturn完了、予定時刻不明、既再開、委譲先除外と表示回収を分ける。実上限時のpercentUsed、猶予後の打切り時のStopFailure発火は起草時も未観測であり、予約の永続化は検証範囲に含めない

## docs/development/design-agents-runtime.md：Codexの利用上限記録と再開：2026年10月14日

- 観測日: 2026年10月14日（起草時の観測報告に基づく記録）
- 観測した版数: Codex CLI 0.162.1、公式ソースのタグrust-v0.162.1
- 再検証手段: ロールアウト記録のtask_complete.error.codex_error_infoがusage_limit_exceededであるturnと、直前token_count.rate_limitsのused_percent・resets_atを対応付ける。[turn.rs](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/core/src/session/turn.rs)の失敗時の終了とStop hookを呼ぶ条件、[goalのextension](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/ext/goal/src/extension.rs)の上限停止を確認する。TUIのthread_goal_actions.rsでは再開確認を調べる。偽のrollout・codex・時計でrun-skillとprocess-loopの再開を検証し、最上位TUIは解除後の実機観測へ分ける。実上限到達からの再開と、即時拒否turnのtoken_countの有無は今回再現していない

## docs/development/design-agents-runtime.md：最上位のCodex TUIの解除後の再開：2026年10月24日

- 観測日: 2026年10月24日
- 観測した版数: Codex CLI 0.164.0（共有app-server daemon 0.164.0）、agent-toolkit 2.225.1
- 観測の結果: 引数なしの`codex`を専用ソケットのtmuxで起動し、1回のturnの後に`codex queue --thread <thread-id> --message <指示>`を実行すると、終了0で指示がturnを始め、TUIの画面に発話と応答が表示された。TUIのagents_serverのMCPプロセスの親はdaemonで、環境に`CODEX_THREAD_ID`は無かった。`/quit`でTUIを終了した5秒後と65秒後に同じMCPプロセスが残り、終了後に積んだ指示はTUIの無いままturnを開始・完了した
- 再検証手段: 隔離した作業ディレクトリで`tmux -L <専用名> new-session -d -c <作業ディレクトリ> codex`を起動し、`tmux capture-pane`で画面を取得する。turnの後に`ps -o ppid`と`/proc/<pid>/environ`の変数名でMCPプロセスの親と`CODEX_THREAD_ID`の有無を確かめ、TUIの前後で`codex queue`を実行してrolloutの`task_started`・`task_complete`を比べる。購読者の判別手段は`codex app-server generate-json-schema --out <ディレクトリ>`のClientRequestで確認する。実上限からの解除時刻での再開は観測していない

## agent-basics/skills/writing-standards/references/dependency-management.md：Python対応版の追加：2026年10月10日

2026年10月10日の入力WIが保持する観測では、[pytilpackのCI run 38024857168・job 114133431911](https://github.com/ak110/pytilpack/actions/runs/38024857168/job/114133431911)が依存同期の終了1を記録した。
停止した条件はcommit `1de178d2e7fb7e7cbf12135db690ae4a4c31b797`、CPython 3.15.0rc3、pydantic-core 2.46.5のsdistビルドとリンカー`cc`不在である。
使用イメージは`ghcr.io/ak110/pyfltr`のdigest `sha256:224003f281050887c053eb24f14137970df5225b1b2c99af61b960fab43d8103`である。
PyYAML 6.0.3のビルドは成功しており、ビルド発生だけを失敗と判定できない。
同入力の更新commit `80d4be7`は2026年10月10日の以下の件名を持つ。

> fix: 3.15用wheelのあるpydantic-coreへ依存を更新し、CIの3.15でソースビルドが走らないようにする

同commitではpydantic-core 2.50.0にcp315 wheelがある。
この過去のCIの観測は入力WIに由来し、本レーンのローカル成功とは区別する。

[uvのビルド失敗の公式説明](https://docs.astral.sh/uv/reference/troubleshooting/build-failures/#why-does-uv-build-a-package)では、導入環境に合うwheelが無ければsdistをビルドする。[CIキャッシュの説明](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration)では、ソースからビルドしたwheelもキャッシュする。ローカルでの成功は、別のCI環境のビルド用ツールとキャッシュの成立を保証しない。

再検証では該当jobの全ログとcommitのlockfileを取得し、Python版、OS・アーキテクチャ、イメージdigest、ビルド開始と終了状態を対応付ける。同じイメージとPython版の隔離環境で、既存wheelキャッシュを使わずにプロジェクトの同期コマンドを実行し、ビルド要件を確認する。互換wheelを持つ版への更新とCIで必要なビルド用ツールを用意する案を比較し、純Pythonのビルドと環境マーカーにより導入されない依存を区別する。

## agent-basics/skills/writing-standards/references/host-official-references.md：子Claudeへ環境値を渡す：2026年10月22日

Claude Codeの[公式環境変数資料](https://code.claude.com/docs/en/env-vars)と[settings資料](https://code.claude.com/docs/en/settings)を参照する。
settingsのenvは変数を削除できず、空の値はBash・MCPサーバー・hookへ継承される。
SDK 0.2.168の起動環境はos.environへoptions.envを重ねるため、agents_serverの起動時に隔離対象を取り除く。
2026年10月21日の観測では、設定に接続変数が無い環境でも、委譲先のBashへ26変数が空の値で渡った。
再検証はAPI環境ファイルを設定し、user・project・local設定のenvへ同じキーを持つ場合と持たない場合で、新規・再開した子の変数名と空値の件数だけを比較する。
SDK・process-loop・可用性確認の起動環境と合成settingsを合わせた結果は、claude_connection_test.pyとruntime_isolation_test.pyの回帰検証で確認する。
本記録の過去の実機観測はWI原本20261021-140153-001.mdに記された結果であり、今回の原稿作成担当による認証を伴う実機再測定は未実施である。

## agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月22日

WI原本の2026年10月22日の観測（Codex CLI 0.163.0）では、起動直後の`ALL_TOOLS`へstdio MCPがまだ登録されず、20秒後の別の`exec`ではagents_serverの6ツールとpyfltrが現れた。2026年10月20日の比較記録では`tools.mcp__agents_server__start`の呼び出しが成功している。本変更の担当も2026年10月22日の`functions.exec`で`ALL_TOOLS`から同じ6ツールの名前と公開説明を取得した。

再検証は同じ設定のCodex CLIで、起動直後と20秒後の別の`exec`から`text(ALL_TOOLS.map(x => x.name).filter(n => n.startsWith("mcp__")))`を実行する。引数は該当要素の`description`のexec tool declarationから読み、同じ`exec`から`tools.mcp__agents_server__list({})`を呼ぶ。原稿担当は配布先の変更を認可されていないため、生成物配布後の通常委譲の試行は統合担当が行う。
