# 監査記録: プロジェクトの実行環境

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## .claude/skills/agent-toolkit-edit/references/agents-server-shared-state-backends.md：バックエンド固有の接続とAPI状態：2026年10月18日

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

## .claude/skills/agent-toolkit-edit/references/session-state-and-flags.md：自己編集の取得継承：2026年11月2日

- 観測日: 2026年11月2日。
- 観測時の版: Claude Code 2.1.316の実行ファイル内にあるEdit・Writeの出力スキーマとPostToolUse呼出しを確認した。
  `originalFile`は編集前本文かnullであり、`staged`・`userModified`も出力に含まれる。
  MultiEditの実ホスト応答型とCRLF正規化時の継承は未確認。
- 再検証手段: `develop-dotfiles`「ホスト本体のバイナリの検索」に従い、対象版の`originalFile`出力型・data生成と`hook_event_name:"PostToolUse"`の`tool_response`への引渡しを確認する。
  同居する`read_delivery_acquisition_test.py`の自己編集成功・未取得編集の試験で、編集前本文・入力・現本文の一致と成功・主体の境界を検証する。

## .claude/skills/merge-pr/SKILL.md：マージ前の確認事項：2026年10月5日

2026年10月5日、GitHub CLI 2.101.0でPR #138のcheck登録順を確認した。
run `37327087407`は同じPR headの`push`起点CIであり、run `37329129743`は`pull_request`起点CIであった。
後者の`statusline-version`は先行するcheckの観測後に非同期で登録された。
同じheadとcheck名だけでは起点を識別できず、PR起点checkの登録前に必須checkの集合を完全と判定できない。

再検証では`gh version`を取得し、PR番号とその`headRefOid`を保持する。
`gh pr checks <PR番号> --json event,workflow,name`でcheckを取得し、`event=pull_request`、`workflow=CI`、`name=statusline-version`の組合せを探す。
run `37327087407`と`37329129743`を個別に取得し、event、head SHA、workflowとjob名を比較する。
次に同じPRの必須check終了後の`headRefOid`と`mergeStateStatus`を取得し、headが不変でGitHubの評価が`CLEAN`になることを確認する。
対象runが保持期限で取得できない場合は、新しいrelease PRで同じJSON項目を使って登録順を観測する。

## develop-dotfiles：回収予定の作業場所の環境準備：2026年10月1日

2026年10月1日、uv 0.12.21を使い、専用worktreeのrootで`env --unset=UV_FROZEN uv sync --locked --all-groups --all-extras`を実行した。
終了コードは0で、標準エラーには135パッケージの解決と133パッケージの確認が記録された。
続く`uv run --frozen python`でpytools、agent_toolkit、pytestのimportが成功した。
同期の前後で、`uv tool dir`配下の導入記録と共有ツールのPythonから取得したeditable導入元を比較し、変更が無いことを確認した。
Git共通dirのhookの内容・実行権限・リンク先と、`git config --local --get commit.template`の値も変わらなかった。
アクセス時刻は比較対象へ含めていない。
共有ツールの導入記録とeditable導入元、Git共通dirのhook、commit.templateを保存した後、回収予定の作業場所で上記の同期を1回実行して再検証する。
importの成功と登録内容の一致を確認する。
恒久作業ツリーの初期導入を検証用に再実行する必要は無い。

## develop-dotfiles：ホスト本体のバイナリの検索：2026年10月4日

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
再検証は実体を同じ解決形で取得し、上記の語をdevelop-dotfilesの一致行数と文脈取得の各コマンドへ渡す。
ホスト版、検索ツール版、ロケール、所要時間と一致行数を保持し、文字列表より後ろにある文脈も使って判断する。
