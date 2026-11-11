# dotfilesの検証で使う値：変更範囲の検証の値

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| テストとCIの所要基準を使う前 | `../../../../agent-basics/skills/writing-standards/references/testing-speed.md` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics-results.md` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` |

## 変更範囲の検証の値

変更範囲の検証対象は、`agent-basics:check-execution`の類型に基づいて選定する。
個別の値を選ぶ前に、変更したパス・ファイル名・モジュール名・契約の識別子（定数、文書の節名、公開項目など）を固定文字列で検索して参照するコードを抽出する。
抽出したコードを利用するテストまで、中間の参照を順にたどる。
文書を定数経由で読み込む処理、間接import、共有ヘルパーを含め、変更ファイル名やテストへの直接一致だけで選定を終えない。
ファイル単位で消費側を特定しにくい場合は、関連する機能のモジュール（下記のserveなど）までを選ぶ。
変更した契約、消費側テスト、採否理由、実行結果の対応は、既存の計画ファイルや検証記録へ残す。
CIが全体を担う本リポジトリでは、列挙しきれなかった利用側を記録してCIへ委ねる。
ローカルの上限は`agent-basics:check-execution/references/verification-scope.md`、参照追跡は同じディレクトリの`verification-scope-selection.md`に従い、下記は具体例である。
本リポジトリで指定する具体的な値は次のとおりである。
役割の新設では役割一覧を走査する`scripts/sync_generated_files_test.py`、`start`の公開入力の受理条件変更では`agent-toolkit/agent_toolkit/_agents_server`全体も対象にする。
会話・委譲の環境変数入力を追加するときは、`_testing/isolation.py`の共通隔離との対応を`agent-toolkit/session_environment_invariant_test.py`で確認する。

- 横断テスト: `testing.md`「テスト配置」に記載された`*_invariant_test.py`を指す。
  変更範囲の検証には`uv run --frozen pyfltr fast --commands=pytest`（個別ファイルを渡さず、リポジトリルートで1回実行）を含める。
  この1回の実行により、ルートリポジトリとサブプロジェクト`agent-toolkit/`の両方がまとめてテストされる。
  保存した出力結果から`# subproject: .`と`# subproject: agent-toolkit`それぞれのpytestの終了状態を確認し、双方のプロジェクトにまたがる横断検証の結果として採用する。
  ルートと`agent-toolkit/`の`pytest-always-targets`設定により、pytestを実行する他のpyfltr操作（対象ファイルを指定した`run`やMCP経由の`run`、`commands`未指定の実行など）でも、同じ横断テストが自動的に追加されて実行される。
  変更範囲の検証にそうした実行が含まれている場合は、その実行結果からサブプロジェクトごとの終了状態を確認して横断検証の結果に充てることができ、同じ内容に対してfastを重ねて実行する必要はない。
  直接pytestや手で選んだテストファイルの一覧など、`pytest-always-targets`が加わらない起動形で最初の検証を組む場合は、追跡済みの`*_invariant_test.py`全件（`git ls-files -- '*_invariant_test.py'`）を同じ実行の対象へ加える。
  commitフックの`pyfltr fast`はこれらを実行するため、最初の検証から漏れるとcommitの時点で初めて失敗する。
  コミット時にprekが実行するfastも、対象集合・入力・依存条件と保存出力を確認でき、フック後の最終内容へ適用できる場合は同じ横断検証の結果に充てる。
  フックで不足する検証や、結果に影響する更新があった範囲だけを追加し、コミットフックの実行自体は省略しない。
  前提条件と再検証の手順は`docs/development/audit-records-project-verification.md`に記載されている。
  該当節は「.claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年10月4日」である。
- コミットフックの実行範囲: `pyfltr fast`は`<ツール名>-fast`がtrueのツールだけを実行する。
  本リポジトリではpylint・mypy・pyrightなどを含まないため、計画のlint（`uv run --frozen pyfltr run --disable=pytest <変更パス>`）全体をフックの成功で置き換えられない。
  実効値は`uv run --frozen pyfltr config list --all`の`*-fast`、実行済みツールは`uv run --frozen pyfltr list-runs`の`commands`で確かめる。
  確認記録と再検証は`docs/development/audit-records-project-verification.md`にある。
  該当節は「.claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年11月9日」である。
  前項の横断テストの再利用は、pytestの対象集合と実行条件を確かめる別の条件付きの扱いである。
- WI画面のアセットと擬似DOM: `agent-toolkit/agent_toolkit/_testing/serve_support.py`内の`_run_node_ui`が擬似DOMの構築と実行時に読み込むアセットが対象となる。
  具体的には、`agent-toolkit/agent_toolkit/_atk/serve/static/`にある`common.js`、`wi.js`、`wi_*.js`、`index.html`、および`serve_support.py`自身の変更が該当する。
  `static/`配下にあるその他のアセットの変更には本項を適用せず、他の検証項目や`agent-basics:check-execution`の類型に沿って対象を選ぶ。
  検証には`uv run --frozen pytest -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/serve`を実行する。
  共有ヘルパーを利用するテストは`app*_test.py`、`assets*_test.py`、`cli*_test.py`、`config*_test.py`、`state*_test.py`に分かれており、変更したファイル名だけから利用側のテストを特定することが難しいため、serveモジュール全体を検証対象とする。
- 期待値を保持するテスト: `agent-toolkit/agent_toolkit/_hooks/`にあるエンドユーザー向け通知メッセージを変更した場合は、各hookに対応する`<hook名>_test.py`が期待値を持っているため、対象のテストを実行する。
  serveのAPIの辞書へ項目を足す変更では、既存の兄弟キーを固定文字列で検索し、辞書全体を完全一致で比べるテスト関数を特定して含める。
  比べる試験は`agent-toolkit/agent_toolkit/_atk/serve/assets_test.py`、`assets_detail_test.py`、`cli_detail_test.py`などにある。
  hooks設定を新たに読む処理を加える変更では、その処理を実行するテストのfixture（`agent-toolkit/agent_toolkit/_agents_server/mcp_projection_test.py`の設定fixtureなど）を含める。
  目的文や説明文を変える変更では、変更前の文言で検索し、それを期待値に持つテスト（`agent-toolkit/agent_toolkit/_atk/agy_launch_test.py`など）を含める。
- 別の構文で定義を読み込むテスト（「横断テスト」の項と異なり、`pyfltr fast`の対象に含まれないため個別に指定する）: `.github/workflows/`内のジョブ名・ステップ名やpytestの対象指定を変更した場合は、リポジトリ直下のCI定義の試験を含める。
  対象は`ci_workflow_test.py`、`ci_workflow_ownership_test.py`、`ci_workflow_windows_test.py`である。
  `agent-toolkit/hooks/hooks.json`や`hooks.codex.json`の登録内容を変更した場合、および`agent-toolkit/agent_toolkit/hook.py`や各イベント処理が読み込むモジュールを変更した場合は、`agent-toolkit/agent_toolkit/hook_test.py`を含める。
  いずれも変更されたファイル名からは自動選定されないため注意する。
- 共有契約を変更した場合は、その契約の参照元から特定した利用側の試験を実行する。
  対象となるのは、`_common/`配下、`_hooks/`の通知生成元で使われる`source`や`kind`、`atk.py`のサブコマンド登録、および2つ以上の`atk`サブコマンドから呼ばれる共通処理や出力契約の変更である。
  また、`agents_server`のMCPツールや`atk agents wait`における公開レスポンスの項目（内容、形式、条件、項目の有無）を変更した場合も含まれる。
  同一レスポンスの各項目を読むMCP層・CLI待機処理・自動再開後処理の各テストを、配置にかかわらず追跡する。
  スキル用スクリプトとagent-toolkit直下のスクリプトも直接のimportと呼出元をたどる。
  列挙が難しい場合は関連モジュールまでを選び、パッケージ全体の試験はCIへ委ねる。
  なお、コメントや空白のみの変更は対象外とする。
- 配布パッケージのバージョン指定: 次のいずれかを変更した場合は、`uv run --frozen pytest -v -p no:cacheprovider install_sh_test.py`を含める。
  このテストは隔離されたHOME環境で実際のuvを動作させ、post-apply処理における`uv tool install`とpyfltr MCPのウォームアップ処理を検証する。
  `pytools/_internal/warmup_pyfltr_mcp_test.py`は`uvx`をモックで置き換えるため、実際のバージョン解決までは確認できない。
  - `agent-toolkit/.mcp.json`のバージョン指定（自動生成される`agent-toolkit/mcp.json`および`agent-toolkit/.mcp.codex.json`を含む）
  - リポジトリ直下および`agent-toolkit/`の`pyproject.toml`にある`dependencies`
  - `.chezmoi-source/`のインストール・更新処理がパッケージマネージャーへ渡すバージョン指定
- パッケージ外からの呼び出し元: 変更した共有契約の消費側を配置にかかわらず、本節冒頭の参照追跡で選定する。
  既存の配置には`agent-toolkit/skills/*/scripts/`、`agent-toolkit/`直下のスクリプト・テスト、`pytools/`、`scripts/`、`libexec/`とリポジトリ直下の結合テストがある。
  agent-toolkit内の利用側も前項の参照追跡で選定する。
  生成資料の読取契約では`agent_toolkit_read_trigger_agy_integration_test.py`、共有Git処理では`libexec/update_dotfiles.py`・`update_dotfiles_stash.py`とその関連テストも追跡する。
  これらの配置と実行例だけで探索を終了せず、将来追加される別配置の消費側も同じ基準で含める。
  特定したテストファイルを引数へ明記してpytestを実行する。
- Function hooksモジュール: `agent-toolkit/hooks/`配下の`*.ts`および`*.tsx`（テストを含む）の変更では、変更パス指定のpyfltrとコミットフックから`function-hooks-types`を起動し、CIが実行しない生成型を検査する。
  - 型チェック: TypeScript 5.9.3を使用する。
    Claude Codeの`plugin-authoring`で生成した`claude-code.d.ts`を環境変数`FUNCTION_HOOKS_TYPES`へ絶対パスで指定する。
    未指定時はworktree直下または`agent-toolkit/`の`.claude-plugin/types/`と`types/claude-code.d.ts`を参照する。
    型定義不足は非0終了する。
    一時設定と生成型定義はコミットしない。
    CIはClaude Code生成型を用意しないため、このコマンドを明示無効化する。
  - 未指定かつworktree内に生成型がない場合は、OS一時領域（`TMPDIR`などを反映）の直下および`claude-*`ディレクトリ内の`bundled-skills/<版>/*/plugin-authoring/types/claude-code.d.ts`を探索する。
    PATH上の`claude --version`とディレクトリ版・先頭行の`// Written by Claude Code <版>.`が一致する型を1件使う。
    不足時は対象版を含む診断と終了2を返す。
    `--types`は環境変数より優先し、両者の明示指定時は自動探索しない。
    チェックはモデル呼び出しを伴わずに実行する。
  - モジュールテスト: 型の自動探索時と同じPATH上の実行ファイルで`claude plugin test <worktree>/agent-toolkit`を実行する。
    引数にはプラグインのルートパスを渡す。
    `agent-toolkit/hooks`を渡すと`hooks/hooks.json`が見つからずエラーとなる。
- 名前の削除や改名に伴う静的チェック: 旧名の固定文字列検索で参照元を特定し、変更したPythonファイルと参照元をパス指定して型検査する。
  全体のty・aridとpylintの循環import検査はCIへ委ねる。
- CIのコンテナージョブにおいて`uv sync`が読み込む入力設定を変更した場合: `make ci-deps-check`を実行する。
  対象となるのは、Pythonバージョンの指定（`.python-version`、`.github/workflows/ci.yaml`のmatrixにおけるPythonバージョン）や、同ファイル内の「ネイティブ依存インストール」ステップである。
  また、リポジトリ直下の`uv.lock`や、同じ場所の`pyproject.toml`内の依存関係宣言・`[tool.uv]`設定の変更も含む。
  lockファイルのみの依存関係追加・削除・更新や、`agent-toolkit/pyproject.toml`の依存変更に伴う直下のlock更新も対象となる。
  `make update`では、lock更新の後にコンテナー依存のインストール確認を常に実施する。
  CIと同一のコンテナーイメージ内でmatrixの各Pythonバージョンにおける依存関係の導入と`uv sync`を再現することで、ローカル環境ではビルドできてもCIで失敗するようなsdistパッケージの問題などをpush前に検知できる。
  使用するコンテナーイメージや導入パッケージ、バージョン情報は`Makefile`内の変数で管理されており、`ci_workflow_test.py`によってCI定義との整合性が検証される。
  なお、このチェックにはDockerが必要であり、1バージョンあたり約1分を要する。
  再検証の記録は`docs/development/audit-records-project-verification.md`「.claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年10月18日」にある。
- 統合後の検証: rebase後のブランチの組み合わせはコミット時には検証できないため、fast-forwardマージを行う前に専用worktree上で`make sync`を実行し、続けて以下を一括で1回実行する。
  - 変更箇所と特定した利用側に関するpytest
    列挙が難しい場合も関連モジュールまでとし、未確認範囲を記録する
  - `uv run --frozen pyfltr fast --commands=pytest`
  - 統合後・公開前に追加した是正（統合時の検証失敗の是正、CI失敗の修正、案内文や構成の追随など）では、変更した文言・ジョブ名・設定キーの変更前の値を固定文字列で探す。
    それを期待する試験（構成を読む`ci_workflow_test.py`、`ci_workflow_ownership_test.py`、`ci_workflow_windows_test.py`、hookの期待値を持つ`<hook名>_test.py`など）を是正後のHEADで実行してから公開する。
    実Windowsの導入・API変換などCIだけで観測する条件はLinuxの成功で代替せず、公開CIで確かめる

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 変更範囲の検証の対象選定：直接影響範囲の類型の基準を適用する前 | `agent-basics:check-execution/references/verification-scope-selection.md` |
