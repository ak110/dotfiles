---
name: pytools-edit
user-invocable: false
description: >
  `pytools/`・`scripts/`・`libexec/`・`bin/`・`rust/`配下のコマンドラインツール・スクリプト・hookスクリプトを
  新規作成・編集するときに使う。
  配置規約・実装規約・PEP 723・cmdエンコーディングを扱う。テストの配置は`dotfiles-development`が扱う。
---

# pytools・scripts・bin の編集

本スキルは本リポジトリのコマンドラインツールと開発スクリプトの配置規約・実装規約を提供する。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `bin/`配下の`*.cmd`を書込ツールで扱う前 | `agent-toolkit:writing-standards`の`references/encoding.md` |
| `rust/`配下のクレートを編集する前 | `agent-toolkit:writing-standards`の`references/rust.md` |

## 配置規約

- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュール
  （単一ファイル`<name>.py`またはサブパッケージ`<name>/`配下形態）を置き、bash補完（argcomplete）に対応する
  - サブパッケージは`__init__.py`が`_cli.py`の`main`を再エクスポートし、`project.scripts`はパッケージ名の`main`を参照する
- privateなヘルパーは`pytools/_internal/`へ集約する
- 開発とCIの工程（prek・Makefile・pyfltr・CI）から起動するスクリプトは`scripts/`配下へ置く。エンドユーザー環境では実行しない
- `bin/`のランチャー、chezmoiの後処理、systemd unit、Claude Codeのhook定義など他のプログラムから起動され、
  `pytools`パッケージの外でエンドユーザー環境（LinuxとWindows）で動く実行ファイルは`libexec/`配下へ置き、両OSで動く書き方とする
- `scripts/`と`libexec/`のPythonは`[project.scripts]`へ登録せず、PEP 723形式の単独実行スクリプトか、プロジェクト環境で起動するスクリプトとして書く。
  wheelは`pytools`だけを含むため、`pytools`から`scripts/`・`libexec/`をimportしない
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
  `pytools/_internal/claude_common.py`はClaude Code固有の定数と`run_claude()`を提供する。新設前に公開APIを確認して共通処理を再利用する（努力目標。重複と実装の分岐を防ぐ）
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
