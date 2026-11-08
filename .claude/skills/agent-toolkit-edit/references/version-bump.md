# バージョン更新の詳細手順

本書は配布プラグインのバージョン更新と生成物の同期手順をまとめた資料である。
対象は`.claude-plugin/marketplace.json`の`plugins[]`に載る各プラグイン（`agent-toolkit/`と`agent-basics/`）で、判定と更新はプラグインごとに行う。
本書の「定義元の2ファイル」は、対象プラグインの`<プラグインのディレクトリ>/.claude-plugin/plugin.json`と、`.claude-plugin/marketplace.json`の同名エントリを指す。
`.chezmoi-source/`配下のchezmoi配布物、`bin/`配下のCLIラッパー、`scripts/`配下のヘルパースクリプトのみを変更する場合は対象外とし、バージョン番号を据え置く。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 動作検証を実行する前 | `../../develop-dotfiles/references/verification-values.md`の「検証コマンドの実行」 | 判断 | `section:*/skills/develop-dotfiles/references/verification-values.md#「検証コマンドの実行」` |
| 全レーンの完了後にバージョンを更新する時点、および本書の更新手順へ入る前 | `agent-toolkit:process-wi/references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |
| Codex導入版変更後にrootを再解決する前 | `agent-toolkit:/share/rules/rules-common.codex.md#「Codexのplugin root解決」` | 判断 | `section:agent-toolkit:*/share/rules/rules-common.codex.md#「Codexのplugin root解決」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/publish.md#「版数と生成物」` | 判断 | `section:agent-toolkit:*/skills/commit/references/publish.md#「版数と生成物」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時 | `../../release-dotfiles/SKILL.md`の「developとmasterのリリース運用」内のstatusline採番規定 | 判断 | `section:*/skills/release-dotfiles/SKILL.md#「developとmasterのリリース運用」` |
| 公開前の統合差分に`rust/claude-statusline/`の変更があり、最終検証を選ぶ時 | `../../develop-dotfiles/references/publishing.md`の「公開前の検証」 | 判断 | `section:*/skills/develop-dotfiles/references/publishing.md#「公開前の検証」` |

## 判定基準

プラグインのディレクトリ配下に差分がある変更では、`*_test.py`だけの差分を除き、必ずバージョン番号を更新（バンプ）する。
コメント、docstring、文書、誤字、入出力の挙動を変えない内部整理の差分も更新の対象とする。Codexの導入処理は版数の不一致だけで再導入するため、版数を据え置いた差分は稼働環境へ届かず、導入済みの配布物が原本と食い違う。
この条件は`scripts/check_plugin_version.py`がCIの`plugin-version` jobとpyfltrの`plugin-version`で検査し、据え置いた差分を失敗にする。

- PATCH（`+0.0.1`）: 軽微な修正（不具合修正、フック処理の改善、検出処理の調整、文言の修正、既存見出し内における規範の補強など）
- MINOR（`+0.1.0`）: 機能の追加、検出範囲の大幅な拡大、descriptionの変更、節の新設など、規模の大きい変更
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がある場合に限定して適用する（詳細な規定は`agent-toolkit:commit`の`references/publish.md`「版数と生成物」を参照する）
- 現行のバージョンが`major.minor.patch`という3つの数値要素で表せない場合は、プロジェクト固有の対応表またはユーザーからの明示的な指定がある場合にのみ区分を適用する。指定がない場合はバージョン更新せず、判定不能として報告する

## 競合解決と統合後の確認

公開担当は統合差分の配布対象ごとに採番要否を確定し、レーンの引渡しと比較する。
`rust/claude-statusline/`の変更には`release-dotfiles`の採番規定を適用し、`develop-dotfiles`の`references/publishing.md`「公開前の検証」の`--enable=statusline-version`による最終検証へ接続する。
プラグインとstatuslineの採番は各定義元で判定する。

rebaseやmergeの際に`version`の競合が発生した場合は、`(major, minor, patch)`の数値タプルを比較して値が大きい方を採用する。採用した値は、競合したプラグインの定義元の2ファイルへ反映する。続けて`scripts/sync_codex_plugin_manifests.py`を実行し、派生マニフェストを同期する。バージョンの整合性は`scripts/sync_codex_plugin_manifests.py --check`（最新なら終了コード0、不整合なら1、引数の誤用なら2）を実行して確認し、同スクリプトによる判定結果を採用する。

rebaseやmergeを完了した後は、競合の有無にかかわらず、公開済みの統合先と定義元の`version`を比較する。未公開のコミットが「判定基準」で更新を要する差分を含み、そのプラグインのバージョン番号が同じであれば、統合後のバージョンを基準として再度更新する（別の作業が先に同一バージョンへ更新し、競合せずに統合された場合に更新が配信されなくなる事態を防ぐためである）。

## 未プッシュ範囲での統合

`scripts/agent_toolkit_bump.py`は、未プッシュのコミット範囲にすでに存在する更新以下の種別が指定された場合、処理を行わずスキップ（no-op）する。そのため、後続の編集ごとに毎回追加で更新を実行する必要はなく、更新種別の引き上げ（格上げ）が必要な場合にのみ上位の種別を指定して実行する。なお、`git push`を実行した後の追加コミットは、新たな未プッシュ範囲として扱う。

## Codex導入後のrootの再確認

Codexプラグインの導入バージョンが変更された場合は、保持しているplugin rootの情報を破棄して再解決する。
`agent-toolkit/share/rules/rules-common.codex.md`「Codexのplugin root解決」のmarketplace限定照会と抽出を用いる。
このプロジェクトのmarketplaceは`ak110-dotfiles`であり、`installed`から`pluginId == "agent-toolkit@ak110-dotfiles"`のエントリを一意に特定する。
識別子、marketplace、version、有効状態、rootの解決結果と診断のみをモデルへ返却する。
取得の失敗、対象エントリの不在、対象が一意に定まらない状態、無効化状態、実体パスの欠落をそれぞれ区別して診断し、全一覧をそのまま返すことは避ける。
plugin cache directory配下にある新バージョンのrootパスを解決し、そのroot内の`skills/`ディレクトリの存在、plugin manifestに記載された`version`と導入バージョンの一致、および実行対象スクリプトの実在を確認した上で使用する。
これらを確認できない場合は処理を未完了として扱い、古いrootへ戻す操作は行わない（`source.path`は配布元を示す値であり、cache rootの解決には新バージョンのrootを使用する）。
なお、起動時の通知に示された変更前のplugin rootは、導入バージョンを変更する前の処理に用いる。

## plan modeでの取り扱い

- 計画ファイルの`## 要件・外部仕様`に、変更のあるプラグインごとの更新種別（PATCH／MINOR／MAJORまたは`bump不要`）と、「判定基準」に基づく判断根拠を記述する。具体的なバージョン番号の決定や既存の更新コミットとの統合処理は、`scripts/agent_toolkit_bump.py`に委ねる。バージョン更新する実装では、検証を実行する前に`scripts/agent_toolkit_bump.py --plugin <プラグイン名> {種別}`をプラグインごとに実行する。
- バージョン更新を伴う計画では、差分の有無にかかわらず定義元の2ファイルを変更対象の説明に含める。ジェネレーターが整合性を保証する派生ファイル（派生マニフェストや、`agent-toolkit/share/rules/common/`から生成される`.chezmoi-source/dot_codex/AGENTS.md`など）は、変更対象の説明に列挙しない。
  これらは、正式な生成コマンドの実行と生成結果の一致確認手順を`## 検証`に記述する。派生マニフェストは、定義元の更新後に`scripts/sync_codex_plugin_manifests.py`を実行して反映し、`--check`オプションで整合性を確認する。
- バージョン更新の完了条件は、対象プラグインごとに、公開済みの基準（`git push`済みの最新のplugin manifest）に対して要求された種別以上の更新が含まれており、定義元の2ファイルと派生マニフェストの`version`が一致していることとする。既存の未プッシュの更新内容で条件を満たしており、`scripts/agent_toolkit_bump.py`がファイル変更なし・終了コード0で終了した場合も完了とみなす。

`agent-toolkit:process-wi`における特則:

- 各レーンは、プラグインごとの更新種別（`bump不要`を含む）とその根拠、ならびにMAJORの場合は認可の根拠を計画ファイルに記録し、レーン内ではバージョン更新コマンドを実行しない。
- AWI本文に記載されたバージョン区分は、起草時点でその要求単体を評価した判定である。メインエージェントは、同一の実行サイクルで統合された変更全体に対してプラグインごとに「判定基準」を適用し、より上位の区分が存在する場合はそれを採用する。公開担当へ渡す`bump種別`はプラグインごとの区分とする（例: agent-toolkitはminor、agent-basicsはpatch）。公開担当は、すべてのレーンのマージが完了した後に、変更のあるプラグインごとに`scripts/agent_toolkit_bump.py --plugin <プラグイン名> <種別>`を1回ずつ実行する。
- バージョン更新を完了条件に含むAWIは、`agent-toolkit/share/lane-integration.subagent.md`の延期`adopt`の手順に従い、公開担当がバージョン番号、派生マニフェスト、および公開結果を検収した後に`adopt`する。
- 統合ブランチをpushする前に上流ブランチの進捗を取り込むためrebaseを行った場合は、「競合解決と統合後の確認」の基準に基づいて再度比較し、必要に応じて公開担当が再度のバージョン更新とマニフェスト同期を実施する。

## 新規CLI公開時の疎通確認

配布用プラグインにおいて新しいCLI、コマンド、またはラッパースクリプトを公開する計画を立てる際は、エンドユーザー環境で正常に起動できるようになるまでの導入手順を`## 要件・外部仕様`に明記する。対象はインストールスクリプト、post-apply処理、PATHの通し方、bash補完の登録、Windows用ペアファイルの作成である。プラグイン単体を利用するエンドユーザーが、PATHの追加や環境変数の設定以外の特別な設定なしにコマンドを実行できる状態を目標基準とする。
プラグインの`bin/`ディレクトリの実配置先は、バージョン番号付きのキャッシュディレクトリとなる。dotfilesを利用するエンドユーザーは`.bashrc`でのPATH追加によって、またプラグイン単体を利用するエンドユーザーは`install-claude.sh`や`install-claude.ps1`が配置する動的解決ラッパーによって、このパス構造を意識せずにコマンドを実行できるようにする。

## バージョン更新

本節は`.claude-plugin/marketplace.json`の`plugins[]`に載る配布プラグイン（`agent-toolkit/`と`agent-basics/`）を対象とし、各プラグインの版数を同じ判定基準と同じスクリプトで更新する。詳細は本書の各節を参照する。
バージョン番号を更新しないままでは、`claude plugin update`を実行しても「最新です」と応答されて更新が配信されない。そのため、エンドユーザーに届く変更はプッシュ前に、変更のあるプラグインごとに必ずバージョンを更新する。

- バージョン番号は`scripts/agent_toolkit_bump.py --plugin <プラグイン名> {patch|minor|major}`で更新する。`--plugin`を省略するとagent-toolkitを対象とする。更新区分の判断は本書「判定基準」を、実行主体とタイミングは同文書「plan modeでの取り扱い」に従う。`agent-toolkit:process-wi`の各レーンでは計画にプラグインごとの区分を記録するのみとし、公開担当が全レーンのマージ後に変更のあるプラグインごとに1回ずつ実行する。rebaseやmergeの競合は同文書「競合解決と統合後の確認」に従って解決する。
- `version`と`description`は、プラグインごとに2箇所の定義元で常に一致させる（`description`は手動で同期する）。定義元は、`<プラグインのディレクトリ>/.claude-plugin/plugin.json`と、`.claude-plugin/marketplace.json`の`plugins[]`内にある同名のエントリである。agent-toolkitなら`agent-toolkit/.claude-plugin/plugin.json`と`name == "agent-toolkit"`のエントリを指す。この整合性は`manifest_ssot_invariant_test.py`によって検証される。
- Agent Plugins向けの`plugin.json`・`mcp.json`やCodex向けマニフェストは、上記2ファイルと`agent-toolkit/.mcp.json`をもとに`scripts/sync_codex_plugin_manifests.py`が生成する。Antigravity向けの`agent-toolkit-agy/`および`agent-basics-agy/`は`scripts/sync_antigravity_plugin.py`が生成し、`post_apply`が`~/.gemini/config/plugins.json`へ登録する。`--plugin agent-basics`を指定すると品質プラグインのみを生成できる。元の名前空間と品質本文を保持したまま、両配布物の長文スキルを同一の短いSKILL.md・全文資料生成処理へ渡す。これらの生成ファイルは手動で編集せず、生成元ファイルを編集した上で再生成する。
- 動作検証は、`develop-dotfiles`の`references/verification-values.md`「検証コマンドの実行」に示された特定ファイル限定の実行形式を用い、SSOTテストを含めて実施する。
