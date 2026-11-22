# dotfilesの検証

dotfilesの変更を、試験の配置から公開前の検証まで確かめる手順書である。
検証の一般の規則（対象の選び方、成功の再利用、再実行、結果と所要の判定）は`agent-basics:check-execution`と`agent-toolkit:atk-mode/playbooks/verification.md`にある。
本書はdotfilesに固有の値（起動の形、実行枠の数、CIのジョブ、対象の試験、所要の基準値）を持つ。
本リポジトリでは全体の試験と静的チェックをCIが担うため、ローカルでは変更ファイルと特定した利用側の試験・lint、CIが実行しないチェックを実行する。
合否は終了コードに加えて個別の試験の結果で判定する（`agent-basics:check-execution/references/diagnostics.md`「合否の判定」）。

## テスト配置

試験の要否、期待値の出所、検証単位の選び方は`agent-basics:writing-standards/references/testing.md`に従い、本節は配置と隔離の決まりを持つ。

- 試験は対象の近くに置く。
  Pythonモジュールの単体試験は同じディレクトリの`<name>_test.py`とし、文書・設定・スクリプトの実物を読む試験は、そのファイルと同じディレクトリか、対象群を包含する最も近い上位ディレクトリに置く。
  fixtureで入力を組み立てて実装を呼ぶ試験も実装の近くに置き、離れた場所に置く場合は置ける最も近い場所を選んで理由が配置から分からない場合はcommit本文に残す。
- 複数の領域にまたがる既存成果物の不変条件を検証する試験（横断テスト）は`*_invariant_test.py`と名付け、通常の動作試験と別のファイルにする。
  リポジトリ直下の`pyproject.toml`はこの名前を`pytest-fast-targets`（`pyfltr fast`の対象）と`pytest-always-targets`（pytestを含む全ての`pyfltr`の実行へ加える対象）に指定している。
- 共通ヘルパーは、`pytools/`配下では`pytools/_internal/_test_helpers.py`、リポジトリ直下では`_test_helpers.py`、`scripts/`配下では`scripts/_scripts_test_helpers.py`に置く。
  リポジトリ直下と`scripts/`はどちらも`sys.path`に入るため、モジュール名を重ねない。
  `agent-toolkit/`の試験は配布物の独立のため`pytools/_internal/`を参照せず、共通処理は`.claude/skills/agent-toolkit-edit/references/implementation.md`「agent_toolkitパッケージの配置と層」の試験専用パッケージに置く。
- 各試験は`agent-toolkit/agent_toolkit/_testing/isolation.py`の隔離の下で動き、ホームと設定ディレクトリは試験ごとの一時ディレクトリを指す。
  実際のGit操作を伴う試験は、履歴、タグ、ブランチ、作業ツリーの状態を試験自身の一時リポジトリに作り、全てのGitコマンドで対象リポジトリを明示する（`_testing/git_repository.py`の`init_repository`、`commit_all`、`run_git`を使える）。
  実行元リポジトリの不変条件を検証する試験があるため、cwdは実行元リポジトリのルートのまま保つ。
  再検証手順は`docs/development/audit-records-project-verification.md`「develop-dotfiles：Git動作テストの状態準備：2026年10月9日」を参照する。
- pytestの外の単発スクリプトで公開コマンドを呼ぶ検証（hook入力の再生など）は、同モジュールの`isolated_development_state(root)`の区間内で呼ぶ。
  保存先を持つモジュールを個別に差し替えると、差し替えていないモジュールが実運用の保存先へ書き込む。
- 外部ランタイムやCLIを子プロセスで起動する試験は、同モジュールの`host_environ()`で子プロセスの`env`を作る。
  子プロセスへ環境変数を渡せない形態では、`restore_host_environment(monkeypatch)`でHOME・設定ディレクトリ・PATHを隔離前へ戻し、試験対象の設定と状態は別の一時ディレクトリへ退避する。
  検証では`.github/workflows/ci.yaml`の対象ジョブと同じランチャー経由でも実行し、修正前の失敗と比べて環境変数の設定の不足を検出できることを確かめる。
  `uv run --frozen python scripts/run_mise_runtime_tests.py node -- agent-toolkit/agent_toolkit/_hooks/message_format_test.py`のようにruntime名と`--`以降のpytest引数を渡すと、miseのshimを先頭に置いてランタイムの解決を確かめてからpytestを起動する。
  `-v -n 0 -p no:cacheprovider`を自動で付けてpytestの終了コードを返し、miseの照会やshimの解決に失敗すると終了コード2で止まる。
- パッケージを取得して実行する外部ツール（pnpmの`dlx`、corepackなど）の試験は、同モジュールの`share_package_caches`で取得物のキャッシュの保存先だけをホストと共有する。
- `scripts/`配下はpytestのprependモードで`sys.path`に入り、試験から直接importできる。
  import対象のスクリプトはアンダースコア区切りで名付け、shebangを持つスクリプトには実行権限を付ける。
  別ディレクトリのスクリプトを裸のモジュール名でimportする形は`test_import_resolution_invariant_test.py`が検出し、直し方は診断文にある。

## 変更範囲の検証の値

対象は`agent-basics:check-execution/references/verification-scope.md`の「直接影響範囲の類型」で選び、ローカルで実行する範囲の上限は同じ資料の「ローカルとCIの分担」に従う。
本リポジトリで選ぶ対象の値は次のとおりで、変更ファイル名から自動で選ばれない試験も多いため、特定した試験ファイルを引数へ明記する。

- 横断テスト: 全量を確認する`pyfltr`の実行には`pytest-always-targets`で横断テストが加わり、`agent-toolkit/`を含むリポジトリ全体を直下の仮想環境で1回に実行する。
  保存した出力のpytestの終了状態と収集件数を確かめて、横断検証の結果に充てる。
  横断テストだけを実行する形は`uv run --frozen pyfltr fast --commands=pytest`（対象を渡さず、リポジトリルートで1回）である。
  レーンの限定実行は、[検証コマンドの実行](verification-execution.md#検証コマンドの実行)の条件に従い、変更が直接影響する横断テストを指定する。
  直接pytestで切り分ける場合も、同じ影響範囲の試験を指定する。
  前提条件と再検証の手順は`docs/development/audit-records-project-verification.md`に記載されている。
  該当節は「.claude/skills/develop-dotfiles/references/verification.md：変更範囲の検証の値：2026年10月4日」である。
- コミットフックの実行範囲: `pyfltr fast`は`<ツール名>-fast`がtrueのツールだけを実行し、本リポジトリではpylint・mypy・pyrightなどを含まない。
  そのため、計画のlint（`uv run --frozen pyfltr run --disable=pytest <変更パス>`）全体をフックの成功で置き換えられない。
  実効値は`uv run --frozen pyfltr config list --all`の`*-fast`、実行済みのツールは`uv run --frozen pyfltr list-runs`の`commands`で確かめる。
  確認記録と再検証は`docs/development/audit-records-project-verification.md`にある。
  該当節は「.claude/skills/develop-dotfiles/references/verification.md：変更範囲の検証の値：2026年11月9日」である。
- 役割の新設では役割一覧を走査する`scripts/sync_generated_files_test.py`、`start`の公開入力の受理条件の変更では`agent-toolkit/agent_toolkit/_agents_server`全体を対象にする。
- 会話・委譲の環境変数入力を足す時は、`_testing/isolation.py`の共通隔離との対応を`agent-toolkit/session_environment_invariant_test.py`で確かめる。
- WI画面のアセットと擬似DOM: `_atk/serve/static/`の`common.js`、`wi.js`、`wi_*.js`、`index.html`と`_testing/serve_support.py`を変えたら、serveモジュール全体を実行する。
  形は`uv run --frozen pytest -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/serve`である（`static/`の他のアセットは通常の類型で選ぶ）。
- 期待値を持つ試験: `agent-toolkit/agent_toolkit/_hooks/`のエンドユーザー向け通知を変えたら、各hookの`<hook名>_test.py`を実行し、通知や復旧案内の撤去では生成関数名と旧文言の両方で別ファイルのmockと期待値も探す。
  serveのAPIの辞書へ項目を足す変更では、辞書全体を完全一致で比べる試験（`agent-toolkit/agent_toolkit/_atk/serve/assets_test.py`、`assets_detail_test.py`、`cli_detail_test.py`など）を含める。
  hooks設定を新たに読む処理では、その処理を実行する試験のfixture（`agent-toolkit/agent_toolkit/_agents_server/mcp_projection_test.py`の設定fixtureなど）を含める。
  目的文や説明文の変更では、旧文言を期待値に持つ試験（`agent-toolkit/agent_toolkit/_atk/agy_launch_test.py`など）を含める。
  参照コメントの撤去では、そのコメントだけが監査見出しを現行の契約へ結び付けていないかを調べ、監査の限定試験を加える。
- 別の構文で定義を読む試験は`pyfltr fast`の対象外のため個別に指定する。
  `.github/workflows/`のジョブ名・ステップ名やpytestの対象指定の変更では`ci_workflow_test.py`、`ci_workflow_ownership_test.py`、`ci_workflow_windows_test.py`を含める。
  `agent-toolkit/hooks/hooks.json`・`hooks.codex.json`の登録内容、`agent-toolkit/agent_toolkit/hook.py`や各イベント処理が読むモジュールを変えたら`agent-toolkit/agent_toolkit/hook_test.py`を含める。
- 共有契約の対象は`_common/`配下、`_hooks/`の通知の生成元で使う`source`や`kind`、`atk.py`のサブコマンド登録、2つ以上の`atk`サブコマンドが呼ぶ共通処理や出力契約である。
  `agents_server`のMCPツールと`atk agents wait`の公開レスポンスの項目も含み、コメントと空白だけの変更は除く。
  公開レスポンスの変更では、同じレスポンスを読むMCP層・CLIの待機処理・自動再開の後処理の各試験を配置によらず追跡する。
- パッケージ外の呼び出し元: 既存の配置は`agent-toolkit/skills/*/scripts/`、`agent-toolkit/`直下のスクリプトと試験、`pytools/`、`scripts/`、`libexec/`、リポジトリ直下の結合試験であり、将来の別の配置の利用側も同じ基準で含める。
  共有のGit処理では`libexec/update_dotfiles.py`・`update_dotfiles_stash.py`とその試験も追跡する。
- 配布パッケージの版の指定を変えたら`uv run --frozen pytest -v -p no:cacheprovider install_sh_test.py`を含める。
  対象は`agent-toolkit/.mcp.json`（生成される`mcp.json`と`.mcp.codex.json`を含む）、依存の宣言、`.chezmoi-source/`の導入・更新処理が渡す版の指定である。
  依存の宣言は、リポジトリ直下の`pyproject.toml`と`agent-toolkit/scripts/launch.py`のインラインメタデータの`dependencies`を指す。
  この試験は隔離したHOMEで実際のuvを動かし、post-applyの`uv tool install`とpyfltr MCPのウォームアップを確かめる（`warmup_pyfltr_mcp_test.py`は`uvx`をモックにするため版の解決まで届かない）。
- Function hooksモジュール（`agent-toolkit/hooks/`の`*.ts`・`*.tsx`）: 変更パスを渡した`pyfltr`とコミットフックが`function-hooks-types`を起動し、CIが用意しない生成型を使う。
  Claude Codeの`plugin-authoring`で生成した`claude-code.d.ts`を環境変数`FUNCTION_HOOKS_TYPES`へ絶対パスで渡し、一時設定と生成型はcommitしない。
  モジュールの試験は、同じPATH上の`claude`で`claude plugin test <worktree>/agent-toolkit`を実行する（引数はプラグインのルートで、`agent-toolkit/hooks`を渡すと`hooks/hooks.json`が見つからずエラーになる）。
- CIのコンテナージョブで`uv sync`が読む入力を変えたら`make ci-deps-check`を実行する。
  入力は`.python-version`、`ci.yaml`のmatrixのPythonの版と「ネイティブ依存インストール」ステップ、リポジトリ直下の`uv.lock`と`pyproject.toml`の依存の宣言・`[tool.uv]`である。
  lockだけの更新と、`agent-toolkit/scripts/launch.py`の依存の変更に伴う直下のlockの更新も含む。
  CIと同じイメージ内でmatrixの各版の依存導入と`uv sync`を再現し、手元では作れてもCIで失敗するsdistの問題などをpush前に検知する。
  Dockerが要り、1版あたり約1分かかる。
  再検証の記録は`docs/development/audit-records-project-verification.md`にある。
  該当節は「.claude/skills/develop-dotfiles/references/verification.md：変更範囲の検証の値：2026年10月18日」である。
- Windowsの実装・fixtureを変え、Linuxで同じ境界を観測できない場合は、本書「push前のチェックとCIだけが実行するチェック」の手順4へ進む。

## 検証コマンドの実行

[検証コマンドの実行](verification-execution.md#検証コマンドの実行)へ進む。

## 公開前の検証

1. 公開する担当は、pushの前に実行する検証を`agent-toolkit:atk-mode/playbooks/publish.md`「検証とCI」で選ぶ。
2. 統合差分の配布対象ごとに採番の要否を確定し、レーンからの引渡しと比べる。
   `rust/claude-statusline/`に変更があれば、`.claude/skills/release-dotfiles/SKILL.md`「developとmasterのリリース運用」の手順1の採番をpushの前に適用し、最終の検証のpyfltrへ`--enable=statusline-version`を指定する。
   同チェックは通常は無効のまま保ち、他の全体検証の成功と同チェックの成功を分けて扱う。
3. 本書「push前のチェックとCIだけが実行するチェック」の手順を実行する。
4. ローカルの全体検証が要ると判定した場合は、`make sync`の後に`uv run --frozen pyfltr run --no-fix --pytest-args='--maxfail=10'`を実行する（打ち切りの件数は本書「検証コマンドの実行」の手順8）。

## push前のチェックとCIだけが実行するチェック

push前には、変更箇所と関連の試験・lint、CIが実行しないチェックを確かめる（一般の規則は`agent-toolkit:atk-mode/playbooks/publish.md`「検証とCI」）。

1. `make sync`で仮想環境を`uv.lock`と`.python-version`へ合わせる。
2. 生成型を前提とする`function-hooks-types`を単独で先に実行する（`uv run --frozen pyfltr run --commands=function-hooks-types`）。
   型が未展開で終了2になったら、本書「変更範囲の検証の値」のFunction hooksモジュールの項のとおり型を用意して`FUNCTION_HOOKS_TYPES`で渡し、同じコマンドを再実行する。
3. CIのpyfltrで無効にしている残りのチェックを実行する。
   形は`uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version,plugin-version --enable=statusline-version,plugin-version`である。
4. Windowsの分岐の実装、公開ランチャー、試験の入力・配置・共有fixtureを変え、同じ結果を手元で観測できない場合は、認可済みの候補branchの既存CIで、公開branchへ反映する前にWindows側を確かめる。
   Linuxで模した判定値と実Windowsの保証の分け方は`agent-basics:writing-standards/references/testing.md`「プラットフォーム分岐の検証」に従い、Linuxで完全に観測できる変更はローカルの検証と通常のCIで終える。

CIの`python-lint (3.15)`はPylintとその他のlintの両jobの成功を集約する。
Pylintは`pyfltr ci --commands=pylint`で実行する。
その他は`pyfltr ci --disable=pylint,pytest,claude-plugin-validate,statusline-version,plugin-version,function-hooks-types`で実行する。
Python 3.15のpytestは`pytest (3.15)`（直下）と、toolkitを`--test-shard`で3分割した`pytest-toolkit (3.15, 1/3)`〜`(3.15, 3/3)`が担う。
必須checkの`pytest-toolkit (3.15)`が全分割の成功を集約する。
これらは最低Git 2.34.1で、skills配下と横断不変条件も含めて各試験を1回実行する。
これらと次のジョブはローカルの`make test`の実行の外にある。

- `test-windows (tests)`: Windows実機でのWindows固有の試験、Codex統合、公開ランチャーの動作確認。
  Windows固有の試験は`windows` markerで選ばれるため、Windows実機で確かめる試験はCI定義でなく試験側へmarkerを付ける。
- `test-windows (install)`: Windows実機での`install.ps1`による初回導入、既存設定のバックアップ、主要ファイルの確認。
  必須checkの`test-windows`がこの2つのjobの成功を集約する。
- `update-dotfiles-upgrade (windows)`: Windowsでの旧版からのupdate-dotfilesによる更新の検証。
- `test-linux`: `install.sh`とchezmoiの実際の適用の確認。
- `rust-lint`: `rust/claude-statusline/`のcargoによる検証。
- `plugin-version`: `develop`へのpushで、push前の公開済みcommitとagent-toolkit・agent-basicsの配布物の差分と版数を比べる。
- `browser-e2e`: 実際のブラウザーによるE2Eテスト（ローカルでは`make test-browser`）。

通常branchのpushは`develop`、PRはbaseとの共通祖先からの変更領域で、`common-ci-owner`が選ぶ。
Rustとfixtureの変更は`rust-lint`、Pythonとserveの変更は`browser-e2e`を実行し、文書だけの変更では両方を省く。
共有設定、未知の変更、比較不能、初回push、`master`・`develop`へのpushは両方を実行する。
選ばずに成功とした結果と判定の失敗・出力の欠落を分け、必須check名と失敗の伝播を保つ。
Pythonのpytest・lint、完全導入と更新の検証には領域の選択を当てない。

## テストとCIの所要基準

[テストとCIの所要基準](verification-execution.md#テストとciの所要基準)で判定する。

### toolkitの所要記録更新

[toolkitの所要記録更新](verification-execution.md#toolkitの所要記録更新)に従う。

## 返す報告に含める項目

実行したコマンドとrun_id・終了コード、選んだ対象と利用側の試験、verify-runと公開前のチェックの結果、未実施・未確認の範囲とその理由、基準を超えた所要とその判断を返す。
