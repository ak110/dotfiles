# 配布への反映

`agent-toolkit/`と`agent-basics/`の配布物を変えてから、版数を上げ、生成物をそろえ、公開担当へ渡すまでの手順書である。
対象は`.claude-plugin/marketplace.json`の`plugins[]`に載る各プラグインで、判定と更新はプラグインごとに行う。
本書の「定義元の2ファイル」は、`<プラグインのディレクトリ>/.claude-plugin/plugin.json`と、`.claude-plugin/marketplace.json`の同名のエントリを指す。

方針は次のとおりである。
版数を据え置くと、`claude plugin update`は更新を配信せず「最新です」と応答し、Codexの導入処理も版数の不一致だけで再導入するため、導入済みの配布物が原本と一致しなくなる。
そのため配布物の差分は版数の更新と組にし、公開の前に版数と生成物の一致を確かめる（原則4。`agent-basics:writing-standards/references/quality-principles-constraints.md`）。
生成物は生成元を直して再生成し、手で直さない。

## 判定基準

dotfilesの配布プラグインは、リリースworkflowの有無にかかわらず、版数を提案する時も確認する時も `operate-ak110-projects` の個人プロジェクト共通方針に従う。
同スキルの `references/release-operations.md`「実施」2を読み、互換性を壊すという理由だけならPATCHかMINORを選ぶ。

プラグインのディレクトリ配下に差分がある変更では、`*_test.py`だけの差分を除き、公開前に版数を上げる。
`implement` のマージ用ブランチでは版数を上げず、更新種別と根拠をAWIに書く。
公開担当が統合後の未公開差分を基準に `bump種別` を決め、版数を上げる。
コメント、docstring、文書、誤字、入出力を変えない内部整理の差分も対象とする。
`.chezmoi-source/`のchezmoi配布物、`bin/`のCLIラッパー、`scripts/`の補助スクリプトだけの変更は対象外とし、版数を据え置く。
この条件は`scripts/check_plugin_version.py`がCIの`plugin-version` jobとpyfltrの`plugin-version`で検査し、据え置いた差分を失敗にする。

- PATCH（`+0.0.1`）: 不具合修正、hookの改善、検出の調整、文言の修正、既存の見出しの中での規範の補強などの軽微な変更。
- MINOR（`+0.1.0`）: 機能の追加、検出範囲の大幅な拡大、`description`の変更、節の新設などの規模の大きい変更。
- MAJOR（`+1.0.0`）: ユーザーが明示的に指示した場合だけ当てる（`agent-toolkit:atk-mode/playbooks/publish.md`「版数と生成物」）。
- 現行の版数が`major.minor.patch`の3つの数で表せない場合は、プロジェクト固有の対応表かユーザーの明示の指定がある時だけ区分を当てる。
  どちらも無ければ版数を上げず、判定できないと報告する。

## plan modeでの取り扱い

計画を起草する時は、次を書く。

1. `## 反映内容と反映先`に、変更のあるプラグインごとの更新種別（PATCH、MINOR、MAJOR、`bump不要`）と、本書「判定基準」による根拠を書く。
   MAJORでは認可の根拠も書く。
   具体的な版数と、既存の更新commitとの統合は`scripts/agent_toolkit_bump.py`に任せる。
2. 版数を上げる計画では、差分の有無によらず定義元の2ファイルを変更対象の説明に含める。
   生成器が整合を保証する派生物（派生マニフェスト、`myprojects-common.md`から生成する`.chezmoi-source/dot_codex/AGENTS.md`など）は変更対象に並べず、正式な生成コマンドと生成結果の一致の確認を引き継ぎ記録に書く。
3. 版数の更新の完了条件は、プラグインごとに、公開済みの基準（push済みの最新のplugin manifest）に対して要求した種別以上の更新を含み、定義元の2ファイルと派生マニフェストの`version`が一致していることとする。
   既存の未pushの更新で条件を満たし、`scripts/agent_toolkit_bump.py`がファイルを変えずに終了コード0で終わった場合も完了とする。
4. 新しいCLI、コマンド、ラッパースクリプトを公開する計画では、本書「新規CLI公開時の疎通確認」の導入手順を書く。
5. `agent-toolkit:atk-mode`の作業`process-wi`では、公開担当へ渡す`bump種別`をプラグインごとの区分にする（例: agent-toolkitはminor、agent-basicsはpatch）。
   公開担当は全レーンのマージの後に、変更のあるプラグインごとに本書「バージョン更新」を1回ずつ行う。

## 新規CLI公開時の疎通確認

配布するプラグインで新しいCLI、コマンド、ラッパースクリプトを公開する時は、エンドユーザーの環境で起動できるまでの導入手順を計画の`## 反映内容と反映先`に書く。
対象は、インストールスクリプト、post-applyの処理、PATHの通し方、bash補完の登録、Windows用のペアファイルの作成である。
プラグイン単体のエンドユーザーが、PATHの追加と環境変数の設定のほかに特別な設定をせずにコマンドを実行できる状態を目標にする。
プラグインの`bin/`の実際の配置先は版数付きのキャッシュディレクトリになる。
dotfilesのエンドユーザーは`.bashrc`のPATHの追加で、プラグイン単体のエンドユーザーは`install-claude.sh`・`install-claude.ps1`が置く動的に解決するラッパーで、このパスの構造を意識せずに実行できるようにする。

## worktreeでの編集時の注意

作業ツリーで編集した配布物は、配布の処理と再起動の後に初めて、稼働中のhookと自動チェックへ反映される。
hookが新たに操作を止めた時と、修正の後も同じ事象が出る時は、修正が無効と判断する前に次を確かめる。

1. 稼働中の版を`~/.claude/plugins/installed_plugins.json`の`installPath`で確かめる（`~/.claude/plugins/data/`は展開先ではない）。
   保持していたplugin rootが無効になった時も、このファイルから解決し直す。
2. 作業ツリーのファイルと稼働中の版のファイルを比べてから、対応を決める。
3. 常駐のMCPサーバー（`agents_server_mcp.py`など）は起動時に読んだモジュールを保持し続けるため、`agent-toolkit/agent_toolkit/`の変更はプロセスの再起動の後に反映される。
   `ps -eo pid,cmd`で起動スクリプトの絶対パスが対象の配布先と一致することを確かめ、`stat -c %Y /proc/<PID>`の起動時刻と対象ファイルの更新時刻を比べる。
   ファイルの方が新しければ、プロセスは修正前の版を保持している。
   別の作業ツリーやplugin rootから起動したプロセスは、修正の後に起動していても修正前の内容を読んでいることがあるため、パスと時刻の両方で判定する。
   `ps -o lstart=`はロケールで形式が変わるため、比較に使わない。
4. プロセスが終了済みで実行パスと起動時刻を確かめられない場合は、版の違いを原因と断定せず、現行の配布先で事象が再現するかだけを結論にする。

## バージョン更新

変更のあるプラグインごとに、pushの前に次の順で進める。

1. 本書「判定基準」で種別を決める。
   `agent-toolkit:atk-mode`の作業`process-wi`の公開担当は、委譲元が確定した種別を使う。
2. 検証の前に`scripts/agent_toolkit_bump.py --plugin <プラグイン名> {patch|minor|major}`を実行する。
   `--plugin`を省くとagent-toolkitが対象になる。
3. 定義元の2ファイルの`version`と`description`をそろえる（`description`は手で同期する）。
   agent-toolkitでは`agent-toolkit/.claude-plugin/plugin.json`とmarketplaceの`name == "agent-toolkit"`が対応し、`manifest_ssot_invariant_test.py`が検証する。
4. 生成元を直して再生成する。
   - Agent Pluginsの`plugin.json`・`mcp.json`とCodexのマニフェストは、定義元の2ファイルと`agent-toolkit/.mcp.json`から`scripts/sync_codex_plugin_manifests.py`が生成する。
     整合は`--check`の終了コード（最新0、不整合1、引数誤用2）で判定する。
   - Antigravityの`agent-toolkit-agy/`・`agent-basics-agy/`は`scripts/sync_antigravity_plugin.py`が生成し、`post_apply`が`~/.gemini/config/plugins.json`へ登録する。
     `--plugin agent-basics`で品質プラグインだけを生成できる。
     元の名前空間と品質本文を保ち、両配布物の長文スキルを同じ短いSKILL.md・全文資料の生成処理へ通す。
   - 生成物の一括同期は`uv run python scripts/sync_generated_files.py`で行う（`python`の明示が必要である。詳細は`docs/development/operations.md`）。
5. 本書「plan modeでの取り扱い」の手順3の完了条件と、生成結果の一致を確かめる。
   続けて`.claude/skills/develop-dotfiles/references/verification.md`「検証コマンドの実行」の特定ファイルに限る形式で、SSOTテストを含む動作検証を行う。

### 未プッシュ範囲での統合

`scripts/agent_toolkit_bump.py`は、未pushのcommitの範囲に指定と同じか上位の種別の更新が既にあれば、何もせずに終わる。
後続の編集ごとに更新し直す必要は無く、種別を上げる時だけ上位の種別で実行する。
`git push`の後のcommitは、新しい未pushの範囲として扱う。

## 競合解決と統合後の確認

rebaseやmergeの後は、競合の有無によらず次の順で確かめる。

1. 公開担当は、統合した差分ごとに採番の要否を各定義元で確定し、レーンの引き渡しと比べる。
   `rust/claude-statusline/`の差分は`.claude/skills/release-dotfiles/SKILL.md`の採番の規定で判定し、`.claude/skills/develop-dotfiles/references/verification.md`「公開前の検証」の`--enable=statusline-version`で最終検証する。
2. `version`が競合したら、`(major, minor, patch)`の数の組で大きい方を定義元の2ファイルへ反映する。
   `scripts/sync_codex_plugin_manifests.py`で派生マニフェストを同期し、`--check`の終了コードで整合を判定する。
3. 公開済みの統合先と定義元の`version`を比べる。
   未公開のcommitが本書「判定基準」の対象を含み、版数が同じなら、統合後の版数を基準に更新し直す。
   別の作業が先に同じ版数へ上げ、競合せずに統合された場合もこれに当たる。

## marketplace管理

ローカルの環境への反映は次の順で行う（版数の更新は不要である）。

1. `chezmoi apply`（または`update-dotfiles`）を実行する。
   事後処理は次を行う。
   - `pytools/_internal/install_claude_plugins.py`でClaude Code向けのagent-toolkitを導入・更新する。
   - `setup_codex_links`で、Codex向けのdotfiles固有のスキルと`docs`へのシンボリックリンクを作る。
   - Codexのmarketplaceを登録し、プラグインの導入と更新を終える。
2. Claude Codeを再起動するか、`/reload-plugins`を実行する。
   対話のメインは、`mcp__plugin_agent-toolkit_agents_server__apply_runtime_changes`の`targets`へ`plugins`、`agents_server`、`pyfltr`から必要な対象を指定して反映を予約できる。
   予約した後は今のターンを終え、プラグインの再読み込みとMCPの再接続の結果を確かめる。
   再起動と反映の受付を完了と扱わず、確かめていない操作を自動で繰り返さない。

marketplaceは、初回の導入（bootstrap）で`install-claude.sh`・`install-claude.ps1`がGitHub参照型として登録する。
以後は`chezmoi apply`の事後処理がローカルディレクトリ参照型（絶対パス）に保ち、GitHub型が残っていても移行する。
Codex向けの生成物`agent-toolkit/.codex-plugin/plugin.json`と`.agents/plugins/marketplace.json`は、prekから動くpyfltrの`sync-generated-files`が毎回再生成する。
Codexのhook定義は、`pytools/_internal/codex_plugin_manifests.py`がイベント名、matcher、入力契約を検証した許可リストから生成する（手動では`scripts/sync_codex_plugin_manifests.py`を使う）。

### Codex導入後のrootの再確認

Codexプラグインの導入版を変えた後は、導入版の混在を防ぐため、次の処理で使う資源を次の順で確定する。

1. 保持していたrootの情報を捨て、`agent-toolkit:atk-mode/playbooks/host-codex.md`「Codexのplugin root解決」のmarketplaceを限った照会と抽出で解決し直す。
   marketplaceは`ak110-dotfiles`とし、`installed`から`pluginId == "agent-toolkit@ak110-dotfiles"`を1つに特定する。
   起動通知の変更前のrootは、導入版を変える前の処理だけに使う。
2. 新版のplugin cache directoryの中のroot、`skills/`、実行するスクリプトの実在と、manifestの`version`と導入版の一致を確かめてから使う。
   確かめられなければ未完了とし、古いrootへ戻さない。
   `source.path`は配布元を示し、cache rootには新版のrootを使う。
3. モデルへ返すのは、識別子、marketplace、version、有効状態、解決結果と診断だけにする。
   取得失敗、エントリ不在、1つに定まらない状態、無効化、実体パスの欠落を区別し、全一覧は返さない。

## 返す報告に含める項目

プラグインごとの更新種別と根拠、更新後の版数、`--check`と動作検証のコマンドと終了コード、競合後の再比較の結果、反映の予約と確かめた結果を返す。
