# dotfilesの検証で使う値

本書は`.claude/skills/develop-dotfiles/SKILL.md`の読込表から参照する資料である。変更範囲の検証において本リポジトリで指定する具体的な値、push前に実行すべきチェックとCIのみが実行するチェックの一覧、および`agent-doc-tone`の指摘への対処手順をまとめている。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/verification-operations.md#「範囲の決め方」` | 判断 | `section:agent-toolkit:*/share/verification-operations.md#「範囲の決め方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `testing.md#「テスト配置」` | 判断 | `section:*/skills/develop-dotfiles/references/testing.md#「テスト配置」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| テストとCIの所要基準を使う前 | `agent-basics:writing-standards/references/testing.md`の「速度」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「速度」` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics.md`の「読む出力」 | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「読む出力」` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations.md`の「統合実行ツール経由の起動」 | 判断 | `section:agent-toolkit:*/share/check-execution-operations.md#「統合実行ツール経由の起動」` |

## 変更範囲の検証の値

変更範囲の検証対象は、`agent-basics:check-execution`の類型に基づいて選定する。
個別の値を選ぶ前に、変更したパス・ファイル名・モジュール名・契約の識別子（定数、文書の節名、公開項目など）を固定文字列で検索して参照するコードを抽出する。
抽出したコードを利用するテストまで、中間の参照を順にたどる。
文書を定数経由で読み込む処理、間接import、共有ヘルパーを含め、変更ファイル名やテストへの直接一致だけで選定を終えない。
消費側を列挙できない場合は、検証単位全体（下記のserveモジュール全体やagent-toolkit全体など）を選ぶ。
変更した契約、消費側テスト、採否理由、実行結果の対応は、既存の計画ファイルや検証記録へ残す。
列挙と全体実行の判断は`agent-basics:check-execution`の`references/verification-scope.md`「範囲の決め方」に従い、下記の各項はその選定の具体例である。
本リポジトリで指定する具体的な値は次のとおりである。
役割の新設では役割一覧を走査する`scripts/sync_generated_files_test.py`、`start`の公開入力の受理条件変更では`agent-toolkit/agent_toolkit/_agents_server`全体も対象にする。
会話・委譲の環境変数入力を追加するときは、`_testing/isolation.py`の共通隔離との対応を`agent-toolkit/session_environment_invariant_test.py`で確認する。

- 横断テスト: `testing.md`「テスト配置」に記載された`*_invariant_test.py`を指す。変更範囲の検証には`uv run --frozen pyfltr fast --commands=pytest`（個別ファイルを渡さず、リポジトリルートで1回実行）を含める。この1回の実行により、ルートリポジトリとサブプロジェクト`agent-toolkit/`の両方がまとめてテストされる。保存した出力結果から`# subproject: .`と`# subproject: agent-toolkit`それぞれのpytestの終了状態を確認し、双方のプロジェクトにまたがる横断検証の結果として採用する。ルートと`agent-toolkit/`の`pytest-always-targets`設定により、pytestを実行する他のpyfltr操作（対象ファイルを指定した`run`やMCP経由の`run`、`commands`未指定の実行など）でも、同じ横断テストが自動的に追加されて実行される。変更範囲の検証にそうした実行が含まれている場合は、その実行結果からサブプロジェクトごとの終了状態を確認して横断検証の結果に充てることができ、同じ内容に対してfastを重ねて実行する必要はない。コミット時にprekが実行するfastも、対象集合・入力・依存条件と保存出力を確認でき、フック後の最終内容へ適用できる場合は同じ横断検証の結果に充てる。フックで不足する検証や、結果に影響する更新があった範囲だけを追加し、コミットフックの実行自体は省略しない。前提条件と再検証の手順は`docs/development/audit-records.md`「.claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年10月4日」に記載されている。
- コミットフックの実行範囲: `pyfltr fast`は`<ツール名>-fast`がtrueのツールだけを実行する。
  本リポジトリではpylint・mypy・pyrightなどを含まないため、計画のlint（`uv run --frozen pyfltr run --disable=pytest <変更パス>`）全体をフックの成功で置き換えられない。
  実効値は`uv run --frozen pyfltr config list --all`の`*-fast`、実行済みツールは`uv run --frozen pyfltr list-runs`の`commands`で確かめる。
  確認記録と再検証は`docs/development/audit-records.md`「.claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年11月9日」にある。
  前項の横断テストの再利用は、pytestの対象集合と実行条件を確かめる別の条件付きの扱いである。
- WI画面のアセットと擬似DOM: `agent-toolkit/agent_toolkit/_testing/serve_support.py`内の`_run_node_ui`が擬似DOMの構築と実行時に読み込むアセットが対象となる。
  具体的には、`agent-toolkit/agent_toolkit/_atk/serve/static/`にある`common.js`、`wi.js`、`index.html`、および`serve_support.py`自身の変更が該当する。
  `static/`配下にあるその他のアセットの変更には本項を適用せず、他の検証項目や`agent-basics:check-execution`の類型に沿って対象を選ぶ。
  検証には`uv run --frozen pytest -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/serve`を実行する。
  共有ヘルパーを利用するテストは`app_test.py`、`assets_test.py`、`cli_test.py`、`config_test.py`、`state_test.py`に分かれており、変更したファイル名だけから利用側のテストを特定することが難しいため、serveモジュール全体を検証対象とする。
- 期待値を保持するテスト: `agent-toolkit/agent_toolkit/_hooks/`にあるエンドユーザー向け通知メッセージを変更した場合は、各hookに対応する`<hook名>_test.py`が期待値を持っているため、対象のテストを実行する。
  serveのAPIの辞書へ項目を足す変更では、既存の兄弟キーを固定文字列で検索し、辞書全体を完全一致で比べる`agent-toolkit/agent_toolkit/_atk/serve/assets_test.py`などを含める。hooks設定を新たに読む処理を加える変更では、その処理を実行するテストのfixture（`agent-toolkit/agent_toolkit/_agents_server/mcp_projection_test.py`の設定fixtureなど）を含める。目的文や説明文を変える変更では、変更前の文言で検索し、それを期待値に持つテスト（`agent-toolkit/agent_toolkit/_atk/agy_launch_test.py`など）を含める。
- 別の構文で定義を読み込むテスト（「横断テスト」の項と異なり、`pyfltr fast`の対象に含まれないため個別に指定する）: `.github/workflows/`内のジョブ名・ステップ名やpytestの対象指定を変更した場合は、リポジトリ直下の`ci_workflow_test.py`を含める。
  `agent-toolkit/hooks/hooks.json`や`hooks.codex.json`の登録内容を変更した場合、および`agent-toolkit/agent_toolkit/hook.py`や各イベント処理が読み込むモジュールを変更した場合は、`agent-toolkit/agent_toolkit/hook_test.py`を含める。
  いずれも変更されたファイル名からは自動選定されないため注意する。
- 共有契約を変更した場合: `uv run --frozen pytest -v -p no:cacheprovider agent-toolkit`を実行する。対象となるのは、`_common/`配下、`_hooks/`の通知生成元で使われる`source`や`kind`、`atk.py`のサブコマンド登録、および2つ以上の`atk`サブコマンドから呼ばれる共通処理や出力契約の変更である。また、`agents_server`のMCPツールや`atk agents wait`における公開レスポンスの項目（内容、形式、条件、項目の有無）を変更した場合も含まれる。同一レスポンスの各項目を、MCP層・CLI待機処理・自動再開後処理の各テストが別ファイルで検証しているためである。スキル用スクリプトとagent-toolkit直下のスクリプトも`agent_toolkit`を直接importするため、CIと同じagent-toolkit全体を対象にする。なお、コメントや空白のみの変更は対象外とする。
- 配布パッケージのバージョン指定: 次のいずれかを変更した場合は、`uv run --frozen pytest -v -p no:cacheprovider install_sh_test.py`を含める。このテストは隔離されたHOME環境で実際のuvを動作させ、post-apply処理における`uv tool install`とpyfltr MCPのウォームアップ処理を検証する。`pytools/_internal/warmup_pyfltr_mcp_test.py`は`uvx`をモックで置き換えるため、実際のバージョン解決までは確認できない。
  - `agent-toolkit/.mcp.json`のバージョン指定（自動生成される`agent-toolkit/mcp.json`および`agent-toolkit/.mcp.codex.json`を含む）
  - リポジトリ直下および`agent-toolkit/`の`pyproject.toml`にある`dependencies`
  - `.chezmoi-source/`のインストール・更新処理がパッケージマネージャーへ渡すバージョン指定
- パッケージ外からの呼び出し元: 変更した共有契約の消費側を配置にかかわらず、本節冒頭の参照追跡で選定する。
  既存の配置には`agent-toolkit/skills/*/scripts/`、`agent-toolkit/`直下のスクリプト・テスト、`pytools/`、`scripts/`、`libexec/`とリポジトリ直下の結合テストがある。
  agent-toolkit内の利用側は前項のagent-toolkit全体の実行で覆う。
  生成資料の読取契約では`agent_toolkit_read_trigger_agy_integration_test.py`、共有Git処理では`libexec/update_dotfiles.py`・`update_dotfiles_stash.py`とその関連テストも追跡する。
  これらの配置と実行例だけで探索を終了せず、将来追加される別配置の消費側も同じ基準で含める。
  agent-toolkit外の試験の実行例は`uv run --frozen pytest -v -p no:cacheprovider pytools scripts libexec agent_toolkit_read_trigger_agy_integration_test.py`とする。
- Function hooksモジュール: `agent-toolkit/hooks/`配下の`*.ts`および`*.tsx`（テストを含む）の変更は、既存の`make test`とコミットフックの`pyfltr fast`から`function-hooks-types`を起動し、全モジュールを一括型検査する。
  - 型チェック: TypeScript 5.9.3を使用する。Claude Codeの`plugin-authoring`で生成した`claude-code.d.ts`を環境変数`FUNCTION_HOOKS_TYPES`へ絶対パスで指定する。未指定時はworktree直下または`agent-toolkit/`の`.claude-plugin/types/`と`types/claude-code.d.ts`を参照する。型定義不足は非0終了する。一時設定と生成型定義はコミットしない。CIはClaude Code生成型を用意しないため、このコマンドを明示無効化する。
  - 未指定かつworktree内に生成型がない場合は、OS一時領域（`TMPDIR`などを反映）の直下および`claude-*`ディレクトリ内の`bundled-skills/<版>/*/plugin-authoring/types/claude-code.d.ts`を探索する。PATH上の`claude --version`とディレクトリ版・先頭行の`// Written by Claude Code <版>.`が一致する型を1件使う。不足時は対象版を含む診断と終了2を返す。`--types`は環境変数より優先し、両者の明示指定時は自動探索しない。チェックはモデルを呼び出さない。
  - モジュールテスト: 型の自動探索時と同じPATH上の実行ファイルで`claude plugin test <worktree>/agent-toolkit`を実行する。
    引数にはプラグインのルートパスを渡す。
    `agent-toolkit/hooks`を渡すと`hooks/hooks.json`が見つからずエラーとなる。
- 名前の削除や改名に伴う全体静的チェック: `uv run --frozen pyfltr run --commands=ty`を実行する。対象ファイルを絞らずリポジトリ全体を対象として実行し、Pythonファイルを変更するレーンでは計画書の「変更範囲の検証」欄に必ず含める。
- CIのコンテナージョブにおいて`uv sync`が読み込む入力設定を変更した場合: `make ci-deps-check`を実行する。対象となるのは、Pythonバージョンの指定（`.python-version`、`.github/workflows/ci.yaml`のmatrixにおけるPythonバージョン）や、同ファイル内の「ネイティブ依存インストール」ステップである。また、リポジトリ直下の`uv.lock`や、同じ場所の`pyproject.toml`内の依存関係宣言・`[tool.uv]`設定の変更も含む。lockファイルのみの依存関係追加・削除・更新や、`agent-toolkit/pyproject.toml`の依存変更に伴う直下のlock更新も対象となる。`make update`では、lock更新の後にコンテナー依存のインストール確認を常に実施する。CIと同一のコンテナーイメージ内でmatrixの各Pythonバージョンにおける依存関係の導入と`uv sync`を再現することで、ローカル環境ではビルドできてもCIで失敗するようなsdistパッケージの問題などをpush前に検知できる。使用するコンテナーイメージや導入パッケージ、バージョン情報は`Makefile`内の変数で管理されており、`ci_workflow_test.py`によってCI定義との整合性が検証される。なお、このチェックにはDockerが必要であり、1バージョンあたり約1分を要する。
  再検証の記録は`docs/development/audit-records.md`「.claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年10月18日」にある。
- 統合後の検証: rebase後のブランチの組み合わせはコミット時には検証できないため、fast-forwardマージを行う前に専用worktree上で`make sync`を実行し、続けて以下を一括で1回実行する。
  - 共有契約およびパッケージ外の呼び出し元に関するpytest
  - `uv run --frozen pyfltr fast --commands=pytest`
  - `ty`
  - `uv run --frozen pyfltr run --commands=arid`
  - 統合後・公開前に追加した是正（統合時の検証失敗の是正、CI失敗の修正、案内文や構成の追随など）では、変更した文言・ジョブ名・設定キーの変更前の値を固定文字列で探す。それを期待する試験（構成を読む`ci_workflow_test.py`、hookの期待値を持つ`<hook名>_test.py`など）を是正後のHEADで実行してから公開する。実Windowsの導入・API変換などCIだけで観測する条件はLinuxの成功で代替せず、公開CIで確かめる

## テストとCIの所要基準

執筆者は追加・変更するテストを次の段階へ分類し、レビュー担当はその段階の必要性と削減余地、結果確認担当は実測を確認する。
共通の評価方法は`agent-basics:writing-standards/references/testing.md`「速度」と`agent-basics:check-execution/references/diagnostics.md`「読む出力」に従う。

| 必要な仕事 | テスト本体の改善判断値 |
| --- | --- |
| 同一プロセス内の通常処理 | 0.1秒 |
| 契約上不可避の実時間待機を含む処理 | 0.5秒 |
| 実プロセス・実Gitなどの境界を使う処理 | 2秒 |
| 外部接続・ブラウザー・大規模結合など | 固有の検出契約、最小構成、起動・待機回数に基づく個別の所要見込み |

通常処理と実境界の値は、2026年10月31日の選択標本88ケースで本体87ケースが0.1秒以下、実プロセス1ケースが0.73秒だった実績を出発点とする。
待機の値は必要待機量0.08秒の共有ロック試験が本体0.11秒だった実測に余裕を持たせた値である。
これらは選択標本に基づく改善判断値とし、全テストの分布から推定する上限と区別する。
setupと後始末も別に読み、全体への寄与を評価する。

0.1秒を超える構成は必要最小限とし、上位段階には、その層でしか検出できない契約違反と代表シナリオを残す。
待機の段階は契約上必要な待機量から選び、固定sleepの長さだけによる段階の引き上げは禁止する。
基準超過時には、軽い層への移動、不変準備の共有、データ量と起動・待機回数の削減を検討する。
OS・負荷・準備状態に由来する差と必要仕事による超過理由、最小化の判断を既存の検証記録に残す。
固定秒数だけでテストを失敗させる機械チェックの追加は禁止する。

CI workflowの全体基準は、対象HEADの実行開始から必須ジョブ終了までの実時間10分とする。
以前の同workflowの成功実績8〜9分へ約1〜2分の余裕を持たせた改善判断値であり、キュー待ち・人間の承認待ちは分ける。
公開担当とPR検収担当は成功時も比較し、律速ジョブの準備・本体・終了処理を内訳として既存の検証記録へ残す。
10分超なら原因を調べ、現在の範囲と認可で成立する改善を同じ作業で行う。
外部前提や別計画が必要な残作業だけ、原因、具体策、解除条件を付けて引き継ぐ。
公開工程と必要な検証は、所要超過時も継続する。
同条件比較では必須テスト集合、並列度、依存版、コンテナーと環境隔離の一致を確かめる。
この固有数値の適用範囲はdotfilesに限る。

## push前のチェックとCIだけが実行するチェック

公開前の全体検証は、`agent-toolkit:commit`が公開工程で参照する資料「検証とCI」の定めに従い、CIでは実行されないチェックや、変更ファイル単体では検知できない複数ファイル間の問題をリポジトリ全体で確認する。本リポジトリでは以下の手順を実行する。

- 最初に環境を同期する: `make sync`を実行する。仮想環境を`uv.lock`および`.python-version`の内容に合わせ、CIのクリーンな実行環境と同様の条件で後続のチェックを動かす
- 他の全体検証より先に、生成型を前提とする`function-hooks-types`を単独で実行する: `uv run --frozen pyfltr run --commands=function-hooks-types`
  - 型が未展開で終了2になった場合は、「変更範囲の検証の値」のFunction hooksモジュールの項に従ってplugin-authoringで型を準備し、`FUNCTION_HOOKS_TYPES`で明示してから同じコマンドを再実行する
  - 成功した結果は同じ型・同じ対象の検査として後段でも再利用し、重ねて実行しない
- CIのpyfltrで無効化されている残りのチェックを実行する。
  `uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version,plugin-version --enable=statusline-version,plugin-version`
- 複数レーンにまたがる重複実装を検査する: `uv run --frozen pyfltr run --commands=arid`
- 型定義と静的参照の整合性を検証する: `uv run --frozen pyfltr run --commands=ty`
- 複数ファイル間にまたがる循環importを検査する: `uv run --frozen pyfltr run --no-fix --commands=pylint --pylint-args='--disable=all --enable=cyclic-import'`

CIの`python-lint (3.15)`は、Pylintとその他のlintの両jobの成功を集約する。
Pylintは`pyfltr ci --commands=pylint`で実行する。
その他は`pyfltr ci --disable=pylint,pytest,claude-plugin-validate,statusline-version,plugin-version,function-hooks-types`で実行する。
Python 3.15のpytestは直下の`pytest (3.15)`と、toolkitを3分割した`pytest-toolkit (3.15, 1/3)`〜`(3.15, 3/3)`が独立して担当する。
必須checkの`pytest-toolkit (3.15)`が全分割の成功を集約する。
以下のジョブはローカルの`make test`では実行されない。

- `test-windows`ジョブ: Windows実機環境でのchezmoi適用、Windows固有のテスト、および公開ランチャーの動作確認。Windows固有のテストは`windows` markerを付けた試験が選ばれるため、Windows実機で確かめる試験を加えるときはCI定義ではなく試験側へmarkerを付ける
- `update-dotfiles-upgrade (windows)`ジョブ: Windows環境における旧バージョンからのupdate-dotfilesによるアップデート検証
- `test-linux`ジョブ: `install.sh`およびchezmoiの実際の適用確認
- `pytest (3.15)`・`pytest-toolkit (3.15, N/3)`ジョブ: Python 3.15・最低Git 2.34.1で直下とtoolkitのテストを各1回実行し、skills配下と横断不変条件も含める。toolkitの試験は`--test-shard`で3分割の各jobへちょうど1回ずつ割り当てる
- `rust-lint`ジョブ: `rust/claude-statusline/`のcargoによる検証
- `plugin-version`ジョブ: `develop`へのpushで、push前の公開済みcommitとagent-toolkit・agent-basicsの配布物の差分と版数を比べる検証
- `browser-e2e`ジョブ: 実際のブラウザーを用いたE2Eテスト（ローカル環境では`make test-browser`で実行可能）

Linux上のOS判定値の模擬は、`agent-basics:writing-standards/references/testing.md`「プラットフォーム分岐の検証」に従って実Windowsの保証と分ける。
共有モジュールを変更するfixtureの影響、ランチャーの引数・待機・終了状態の受け渡し、パスAPIの戻り値と実体同一性、起動時の依存解決・環境作成は、ローカルでも先に前提を反証できる。
実WindowsのAPIによる変換や権限・ファイル共有の副作用は実OSの観測と対応付け、準備済み開発環境の成功で配布環境の初回起動を保証しない。
現行CIでは候補branchへのpushによるWindows検証も選べる。必要性、公開操作の認可、既存結果との重複費用を確認し、追加CIの要否を変更ごとに判定する。
未観測の境界は検証記録へ残し、タイムアウトの内部段階が未確定なら特定原因へ帰属させない。

通常branchのpushではdevelop、PRではPRのbaseとの共通祖先から変更領域を判定する。
`common-ci-owner`の選択結果と理由を基に、Rust・fixtureは`rust-lint`、Python・serveは`browser-e2e`を実行する。
文書だけの変更では両検査を省略できる。
共有設定・未知の変更・比較不能・初回pushとmaster/develop pushは両方を実行する。
選択しない成功と判定の失敗・出力欠落を区別し、必須check名と失敗伝播を保つ。
Pythonのpytest・lint、完全導入と更新検証には領域選択を適用しない。
CIを変更した担当は、同条件の保存結果から実時間とrunner実行量を比較し、公開前に観測できない実CIの比較は公開担当へ渡す。

## agent-doc-tone

- `make test`およびコミット時のpre-commit（ステージされた対象ファイル向け）によって`agent-doc-tone`が実行される。単独で実行する場合は`uv run --frozen pyfltr run --commands=agent-doc-tone`を使用する。
- チェックの対象は、エージェントが実行時に参照するMarkdownファイル（`AGENTS.md`、`agent-toolkit/`配下のrules・skills・share、`.chezmoi-source/dot_claude/`、`.claude/skills/`）である。なお`agent-toolkit/`配下では、コード内のコメント、docstring、表示メッセージ、プロンプト注入文も語彙判定の対象に含まれる。
- 文体の密度が規定の閾値を超えたファイルは、測定指標とともに出力される（指標と閾値の詳細は`scripts/check_agent_doc_tone.py`のdocstringに記載されている）。同スクリプトの`_DENIED_PATTERNS`に定義された禁止パターンに説明文が一致した場合は、該当ファイル名と行番号を表示して終了コード1以上で停止する（pre-commitではコミットが中断される）。
- 文脈によって対象や動作が曖昧になりやすい表現（同スクリプトの`_CAUTION_PATTERNS`）については、警告メッセージのみを出力して終了コード0で終了する。これは文脈上意味が正しく伝わるかを確認するための補助機能であり、正確な専門用語や合意済みの正式名称はそのまま維持する。自身で作成した適切な用例や説明は通常のチェックへ通し、引用した原文、検出用の定義データ、保存形式の名称などは用途を区別してそのまま保持する。
- 密度が閾値を超過した場合、診断結果の1行目に超過した各指標の比率、該当件数、および閾値以下へ抑えるために必要な最小書き換え件数が表示される。
- 続く詳細診断では、本文中から検出された否定形で終わる文や「当該」の使用箇所が、行番号および該当箇所の抜粋とともにすべて列挙される。引用ブロック内の段落も本文としてカウントされるが、コードブロックや検出用データは密度の計算対象（分子・分母）から除外される。否定形終端率は文の数を基準とし、「当該」の出現率は単語の出現回数を基準としてカウントするため、同一の文に複数回出現した場合は出現ごとに書き換える。
- 列挙された検出箇所の中から必要な件数を選び、否定形による宣言文は肯定形の表現へ、法令調の指示語は指示対象が具体的に伝わる表現へ書き直す。語彙の再利用を避ける際は、`agent-basics:writing-standards`におけるtextlint指摘の修正方針に準拠し、文全体の構成を見直して書き直す。

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/verification-scope.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md` |

## 検証コマンドの実行

1. 消費側を漏らさず検証するため、「変更範囲の検証の値」で対象を確定する。
   `.venv`を再作成し得るチェック（`uv run --python`や依存更新など）は同じ仮想環境パスで直列に実行し、並行する場合は別の仮想環境パスを指定する。
2. 差分を保護するため、実行目的に合う修正条件を選ぶ。
   自動修正には`make format`（`uv run --frozen pyfltr fast`）、調査・計測には`--no-fix`を使う。
   フォーマッター段（`ruff-format`、`uv-sort`、`shfmt`、`prek`、`sync-generated-files`）は`--no-fix`でも書き換えて終了0となるため、読取専用ではこれらの段を除く。
   分岐の効果を確かめる差分は`git diff --no-ext-diff --src-prefix=a/ --dst-prefix=b/`で保存し、`git apply`・`git apply -R`で適用・復元する。
   `diff.noprefix=true`では既定の`git apply`が接頭辞なしのパスを解決できないため、この起動形を使い、既存差分とindexは別々に保護する。
3. 起動条件を保つため、直接pytestと共有実行枠の条件を確認する。
   - デバッガー・最小再現・環境切り分けでは直接pytestを使える。
     `-o`・`-p`はpytest固有で、`pyfltr run`へ渡すと終了2となる。
     `-o addopts=''`で設定全体を外す場合は`-p no:cacheprovider`も指定する。
     並列化だけを止める場合は`-n 0`を使う。
     `-p no:xdist`では既存の`-n 4 --dist=worksteal`が未認識となり収集前に終了4となる。
   - 共有実行枠の対象は`make test`、`uv run --frozen pyfltr run`、`git commit`内の`pyfltr fast`、共通pytestプラグインを読むpytestである。
     両プロジェクトの`addopts`は同プラグインを`-p`で読み、`make test-browser`など`-o addopts=''`で解除するものは枠の対象外となる。
     シェルからの起動は`agent-toolkit:/share/check-execution-operations.md`「統合実行ツール経由の起動」の形式と3分類に従う。
   - pytestは子処理を回収した試験の区切りで待機者へ譲り、再取得後に残りを続ける。
     `pytest-slot-ticket`を設定したpyfltr経由と`atk lane integrate`の検証でも、参加券を受け渡して最上位の所有者だけが解放する。
     参加しないツールやworkerが稼働する間は譲らず、長い全体pytestの区切りでも別worktreeの限定検証やcommit hookを実行できる。
4. 確定した対象へ全チェックを適用するため、初回はMCPの`run`へパスを渡すか、`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を`commands`指定なしで実行する。
   修正後の再実行だけ失敗したチェックへ限定し、同じ内容の2回目以降の静的チェックでは`commands`（CLIは`--commands`）からpytestを除く。
   失敗テストだけの再確認は`--only-failed`、単一試験の確認は対象ファイルと必要なら`-k`を渡した直接pytestを使い、収集件数と想定の一致を確かめる。
   pytestを含む実行には両プロジェクトの`pytest-always-targets`が加わるため、本書の横断テストの項で重複実行を避ける。
5. 意図した版と結果を確かめてからコミット対象を確定するため、`git status`で整形による差分を確認する。
   実行時間は`uv run --frozen pyfltr list-runs`で得た実行IDを使い、`uv run --frozen pyfltr show-run <run_id>`で確認する。
