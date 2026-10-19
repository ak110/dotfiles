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

- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュール（`<name>.py`かサブパッケージ`<name>/`）を置き、bash補完（argcomplete）に対応する。サブパッケージは`__init__.py`が`_cli.py`の`main`を再エクスポートし、`project.scripts`はパッケージの`main`を参照する
- privateなヘルパーは`pytools/_internal/`へ置く
- 開発とCIの工程（prek・Makefile・pyfltr・CI）から起動するスクリプトは`scripts/`へ置く（エンドユーザー環境での実行は対象外）
- 他のプログラムから起動され、`pytools`パッケージの外でエンドユーザー環境（LinuxとWindows）で動く実行ファイル（`bin/`のランチャー、chezmoiの後処理、systemd unit、hook定義からの起動など）は`libexec/`へ置き、両OSで動く書き方とする
- `scripts/`と`libexec/`のPythonは`[project.scripts]`へ登録せず、PEP 723の単独実行スクリプトかプロジェクト環境で起動するスクリプトとして書く。wheelは`pytools`だけを含むため、`pytools`から`scripts/`・`libexec/`へのimportは禁止する
- 単純なコマンドラッパーは`scripts/new_bin_cmd.py <name> <command...>`で`bin/<name>`と`bin/<name>.cmd`のペアを生成する
- 高頻度に起動するhook・statusLine相当のスクリプトは、Windowsでの`uv run`の起動コストを考え、ネイティブバイナリ化を第一候補として検討する（先行事例は`rust/claude-statusline/`）

## 実装規約

- dotfilesの作業ツリーの位置は`pytools._internal.common.find_dotfiles_root()`で求める。CIやエンドユーザー環境では`$HOME`配下と一致せず、階層数による解決は配置変更のたびに修正を要するため、`~/dotfiles`や`Path(__file__)`からの階層数で求めない。`None`の扱いは呼び出し元が決める。`pytools`をimportできないPEP 723スクリプトは`Path(__file__)`起点で解決する。`CHEZMOI_WORKING_TREE`の読み取りは対象外とする
- bash、PowerShell、JSON、chezmoiテンプレートにある`~/dotfiles`の固定値は、`install.sh`のclone先を前提とする。例は`.chezmoi-source/dot_bashrc`、`share/claude_settings_json_managed.win32.json`、`bin/lab-bg`である。clone先の前提を変える場合はこれらもそろえる
- Claudeに依存しない共通処理は`pytools/_internal/common.py`（`find_dotfiles_root()`・`run_subprocess()`・`atomic_write_*()`等）にある。Claude Code固有の定数と`run_claude()`は`pytools/_internal/claude_common.py`にある。新設する前に既存の公開APIを確かめる（努力目標）
- `bin/`配下の`*.cmd`はCP932で書かれている。書込ツールでの扱いは`agent-toolkit:writing-standards`の`references/encoding.md`「書込ツールの改行・BOM保全」に従い、ASCIIのみの修正は`sed -i`で行う
- 非ASCIIを標準出力・標準エラーへ書くPython CLIは、開始時に両ストリームを`io.TextIOWrapper`でUTF-8・`errors="replace"`へ再構成する（英語版Windowsなどでロケール既定のエンコーディングに頼ると最初の日本語出力で停止する）
- 再構成を経由しないログと標準出力のメッセージは、`str.encode("cp932")`が成功する範囲に収める（U+20BB7のような一部の漢字、em dash・en dash・`✓`・`•`は符号化できない）
- `pytools/post_apply.py`のステップの`run`は`PostApplyOutcome`だけを返す。失敗をスキップと失敗のどちらに数えるかは`pytools/_internal/post_apply_outcome.py`の`PostApplyOutcome`のdocstringに従う。対象OSは`_StepSpec.platforms`だけで宣言する
- 外部コマンドの失敗を失敗メッセージ・例外・警告へ変換するときは、`pytools/_internal/common.py`の`format_cli_error()`の結果を含める。同関数は捕捉済みの標準エラーと標準出力を併記し、両方が空なら終了コードを、実行できなかった場合はその旨を返す。終了コードだけを伝えると、原因と次の操作がdotfilesユーザーへ届かない。接続不能など環境の異常（例: `systemctl --user`がユーザーマネージャーへ接続できない）は対象の状態（未有効・非稼働）として解釈せず、区別して復旧の次の操作を書く
- post_applyのステップが外部ツールの不在でステップ全体をスキップする場合は、そのツールを同じか先行するステップが導入するか、`README.md`が復旧手順を持つようにする（dotfilesユーザーが導入先を選ぶアプリケーションは除く）
- post_applyの工程が配置するファイル（ランチャー、フラグファイル、unitなど）はchezmoiの管理外であり、登録しないと旧生成物が配布先に残る。配置先の改名と工程の廃止では、同じ変更で旧パスを`_REMOVED_PATHS`（dotfilesユーザーが編集し得るファイルは`_REMOVED_PATHS_IF_CONTENT`）へ登録する。撤去表（この2つと`update_claude_settings.py`の除去表）の項目には登録日を書き、6か月を過ぎたら外す。規則は`pytools/_internal/removal_registry.py`、期限切れの検出は`pytools/removed_paths_invariant_test.py`が持つ
- `rust/`配下は`rust/<クレート名>/`のCargoクレート単位で置き、作法は`agent-toolkit:writing-standards`の`references/rust.md`に従う。`make test`は`rust/`を対象にしないため、変更したクレートで`cargo fmt --check`、`cargo clippy`、`cargo test`を変更範囲の検証として実行する（CIでは`rust-lint` job）。版数の更新は`dotfiles-release`を参照する
