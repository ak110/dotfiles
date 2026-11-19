# 監査記録: プロジェクトの実行環境

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

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
