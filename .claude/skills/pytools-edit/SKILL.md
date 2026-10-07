---
name: pytools-edit
user-invocable: false
description: >
  `pytools/`・`scripts/`・`libexec/`・`bin/`・`rust/`配下のコマンドラインツール・スクリプト・hookスクリプトを
  新規作成・編集するとき、および本リポジトリのテストの配置を決めるとき、テストを新規作成・編集するときに使う。
  配置規約・テスト配置・PEP 723・wheel設定・cmdエンコーディングを扱う。
---

# pytools・scripts・bin の編集

本スキルは本リポジトリのコマンドラインツールと開発スクリプトの配置規約・実装規約を提供する。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `bin/`配下の`*.cmd`を書込ツールで扱う前 | `agent-toolkit:writing-standards`の`references/encoding.md` |
| `rust/`配下のクレートを編集する前 | `agent-toolkit:writing-standards`の`references/rust.md` |
| 不変条件のテストの検証対象を探索する時 | `agent-toolkit:check-execution`の`references/verification-scope.md` |

## 配置規約

- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュール
  （単一ファイル`<name>.py`またはサブパッケージ`<name>/`配下形態）を置き、bash補完（argcomplete）に対応する
  - サブパッケージは`__init__.py`が`_cli.py`の`main`を再エクスポートし、`project.scripts`はパッケージ名の`main`を参照する
- privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する
- 開発とCIの工程（prek・Makefile・pyfltr・CI）から起動するスクリプトは`scripts/`配下へ置く。エンドユーザー環境では実行しない
- `bin/`のランチャー、chezmoiの後処理、systemd unit、Claude Codeのhook定義など他のプログラムから起動され、
  `pytools`パッケージの外でエンドユーザー環境（LinuxとWindows）で動く実行ファイルは`libexec/`配下へ置き、両OSで動く書き方とする
- `scripts/`と`libexec/`のPythonは`[project.scripts]`へ登録せず、PEP 723形式の単独実行スクリプトか、プロジェクト環境で起動するスクリプトとして書く。
  `pytools`から`scripts/`と`libexec/`をimportしない。配布するwheelは`pytools`だけを含み、editable導入以外では解決できないためである
- 単純なコマンドラッパーの新規追加には`scripts/new_bin_cmd.py <name> <command...>`を使う
  （リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成する）
- 高頻度起動するhook・statusLine相当のスクリプトは、Windowsでの`uv run`起動コストを考慮し、
  ネイティブバイナリ化を実装方式の第一候補として検討する（先行事例は`rust/claude-statusline/`）

## 実装規約

- `pytools`とプロジェクト環境で起動するスクリプトは、dotfilesの作業ツリーの位置を`pytools._internal.common.find_dotfiles_root()`で求める。
  `Path.home()`起点の`~/dotfiles`や`Path(__file__)`からの階層数で求めない。CIチェックアウトやエンドユーザー環境では`$HOME`と`~/dotfiles`が一致せず、
  階層数による解決は配置を変えるたびに各所の修正を要するためである。`None`が返った場合の扱いは呼び出し元が決める。
  PEP 723形式の単独実行スクリプトは`pytools`をimportできないため、`Path(__file__)`起点で解決する。
  chezmoiからの起動の判定を兼ねる環境変数`CHEZMOI_WORKING_TREE`の読み取りはこの規定の対象外とする
- bash、PowerShell、JSON、chezmoiテンプレートの`~/dotfiles`の固定値（`.chezmoi-source/dot_bashrc`、`share/claude_settings_json_managed.win32.json`、`bin/lab-bg`など）は
  `install.sh`がclone先を`~/dotfiles`とすることを前提とする。clone先の前提を変える場合はこれらの箇所もそろえる
- `pytools/_internal/common.py`はClaudeに依存しない共通処理（`find_dotfiles_root()`・`run_subprocess()`・`atomic_write_*()`等）、
  `pytools/_internal/claude_common.py`はClaude Code固有の定数と`run_claude()`を提供する。新規ヘルパーを書き起こす前に公開APIを確認し、重複定義を避ける（努力目標。共通基盤を使うと実装の分岐を防げる）
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）で書かれている。書込ツールで扱う手段は`agent-toolkit:writing-standards`の
  `references/encoding.md`「書込ツールの改行・BOM保全」に従い、ASCIIのみの修正は`sed -i`で対応する
- 非ASCIIを標準出力または標準エラーへ書くPython CLIは、開始時に`io.TextIOWrapper`の両ストリームをUTF-8・`errors="replace"`へ再構成する。英語版Windowsなどで、エンコーディングを指定せずにランタイムがロケールから選ぶ値へ依存すると、日本語の最初の出力でCLIが停止するためである
- ストリームの再構成を経由しないログと標準出力のメッセージは、WindowsのCP932で符号化できる範囲に収める。可否は`str.encode("cp932")`の成否で判定する。漢字にもU+20BB7のように符号化できないものがあり、em dash・en dash・`✓`・`•`も符号化できない
- `pytools/post_apply.py`のステップの`run`は`PostApplyOutcome`だけを返し、失敗をスキップと失敗のどちらに数えるかは`pytools/_internal/post_apply_outcome.py`の`PostApplyOutcome`のdocstringの基準に従う。
  対象OSは`_StepSpec.platforms`だけで宣言し、ステップのモジュールでは判定しない
- `pytools/post_apply.py`のステップが外部ツールの不在でそのステップ全体をスキップする場合は、そのツールを同じステップまたは先行するステップが導入するか、`README.md`が復旧手順を持つかのいずれかを満たす。
  dotfilesユーザーが導入先を選ぶアプリケーションは、この対象から外す
- `pytools/post_apply.py`の工程が配置するファイル（ランチャー、フラグファイル、unitなど）の配置先を改名する場合と工程を廃止する場合は、同じ変更で旧パスを`_REMOVED_PATHS`へ登録する。dotfilesユーザーが編集し得るファイルは`_REMOVED_PATHS_IF_CONTENT`へ登録する。
  工程の生成物はchezmoiの管理外であり、登録しないと旧生成物が配布先に残り続ける。
  撤去表（`_REMOVED_PATHS`・`_REMOVED_PATHS_IF_CONTENT`と`update_claude_settings.py`の除去表）の各項目には登録日を書き、登録日から6か月を過ぎた項目は表から外す。
  通常の更新を続ける環境ではその期間内に除去が済むためである。登録日の決め方と期限の数え方は`pytools/_internal/removal_registry.py`が定め、期限切れは`pytools/removed_paths_invariant_test.py`が検出する
- `rust/`配下の配置の単位は`rust/<クレート名>/`のCargoクレートとする。
  記述作法は`agent-toolkit:writing-standards`の`references/rust.md`が定める。
  `make test`は`rust/`配下を対象に含まないため、変更したクレートで`cargo fmt --check`、`cargo clippy`および`cargo test`を変更範囲の検証として実行する。
  CIでは`rust-lint` jobが同等の検証を担う。
  配布版数の更新要求は`dotfiles-release`を参照する

## テスト配置

- テストを置く場所は、確かめる対象のできるだけ近くから選ぶ。Pythonモジュールの動作を確かめるテストは、
  そのモジュールと同じディレクトリの`<name>_test.py`とする。文書・設定・スクリプトの実物を読むテストは、
  その実物と同じディレクトリか、対象群を包含する最も近いディレクトリへ置く。
  fixtureで文書などの入力を作成して実装を呼ぶテストは、実装の動作テストとして実装の近くへ残す。
  収集や配布の制約で近くへ置けない場合は、成立する最も近い場所を選び、その理由を対象テストファイルのモジュールdocstringへ記す
- 複数領域の既存成果物の不変条件を確かめるPythonテストは、ファイル名を`*_invariant_test.py`として識別する。
  rootと`agent-toolkit/`の`pyproject.toml`の`pytest-fast-targets`がこのファイル名で対象を選び、`pyfltr fast`が実行する。
  通常の動作テストと混在するときは、不変条件のテストだけを近接する`*_invariant_test.py`へ分離し、通常テストをfastの対象に含めない。
  検証対象の探索は`agent-toolkit:check-execution`の`references/verification-scope.md`に従い、
  個々のテストと編集対象の対応表を規範へ増やさない
- テスト共通ヘルパーは`pytools/`配下では`pytools/_internal/_test_helpers.py`、リポジトリ直下のテストでは直下の`_test_helpers.py`、
  `scripts/`配下のテストでは`scripts/_scripts_test_helpers.py`へ集約し、テスト間で共有する関数と定数はこれらの補助モジュールからimportする。
  pytestのprependモードでは直下と`scripts/`がともに`sys.path`へ入り、同名のトップレベルモジュールは先に読み込んだ方だけが残るため、
  両ディレクトリの補助モジュールには互いに異なる名前を付ける。
  `agent-toolkit/`配下のテストは配布物独立性を保つため`pytools/_internal/`配下を参照せず、
  共通化が必要な場合は`agent-toolkit-edit`スキルの`references/distribution-and-hooks.md`「scripts配下の配置」が定めるテスト専用パッケージへ置く
- テストはリポジトリ直下と`agent-toolkit/`の`conftest.py`が適用する`agent-toolkit/agent_toolkit/_testing/isolation.py`の隔離の下で動き、ホームと設定ディレクトリはテストごとの一時ディレクトリを指す。
  パッケージを取得して起動する外部ツール（pnpmの`dlx`、corepackなど）を実際に動かすテストは、同モジュールの`share_package_caches`で取得物の保存先だけをホストと共有する。
  共有しないと、テストのたびに空の保存先へ取得し直して所要が延びる
- `pytools`パッケージ配布物にテストコードを含めないため、
  `[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を除外する
- `scripts/`配下はpytestのprependモードで`sys.path`へ自動追加されるためテストから直接importできる。
  importしたいスクリプトはアンダースコア区切りで命名し、shebang付きスクリプトは`chmod +x`する
