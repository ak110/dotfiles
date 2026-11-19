---
name: pytools-edit
user-invocable: false
description: >
  `pytools/`、`scripts/`、`libexec/`、`bin/`、`rust/`配下のコマンドラインツール、スクリプト、フックスクリプトを
  新規作成・編集するときに使う。
  配置規約、実装規約、PEP 723、cmdファイルのエンコーディングを扱う。テストの配置は`develop-dotfiles`が扱う。
---

# pytools・scripts・binの編集

本リポジトリのコマンドラインツールと開発スクリプトの配置と実装の規約である。
テストの配置と検証の手順は`.claude/skills/develop-dotfiles/SKILL.md`の表から選ぶ。

## 配置規約

- `pytools/`のトップレベルには、`project.scripts`から参照する公開CLIモジュール（`<name>.py`かサブパッケージ`<name>/`）を置き、bash補完（argcomplete）に対応させる。
  サブパッケージにする場合は、`__init__.py`で`_cli.py`の`main`を再エクスポートし、`project.scripts`からはパッケージの`main`を参照する。
- 内部だけで使うヘルパーは`pytools/_internal/`に置く。
- 開発とCIの工程（prek、Makefile、pyfltr、CI）から起動するスクリプトは`scripts/`に置く。
- 他のプログラムから呼ばれ、`pytools`の外でエンドユーザーの環境（LinuxとWindows）で動く実行ファイル（`bin/`のランチャー、chezmoiの後処理、systemd unit、フック定義から起動するスクリプトなど）は`libexec/`に置き、両OSで動くように書く。
- `scripts/`と`libexec/`のPythonスクリプトは`[project.scripts]`に登録せず、PEP 723の単独実行スクリプトか、プロジェクト環境で直接起動するスクリプトとして書く。
  配布用wheelは`pytools`だけを含むため、`pytools`がimportしてよいのは`pytools`の中だけである。
- 単純なコマンドラッパーは`scripts/new_bin_cmd.py <name> <command...>`で`bin/<name>`と`bin/<name>.cmd`の対を生成する。
- 起動頻度の高いフックやstatusline相当のスクリプトは、Windowsでの`uv run`の起動の費用を考え、ネイティブバイナリ化を第一候補にする（先例は`rust/claude-statusline/`）。
- `rust/`配下は`rust/<クレート名>/`の単位でCargoクレートとして置き、書き方は`agent-basics:writing-standards/references/rust.md`に従う。

## 実装規約

- dotfilesの作業ツリーの位置は`pytools._internal.common.find_dotfiles_root()`で取得する（`None`の扱いは呼び出し元が決める）。
  CIとエンドユーザーの環境では作業ツリーが`$HOME`直下とは限らず、階層数による相対解決は構成の変更で壊れるため、`~/dotfiles`の固定パスと`Path(__file__)`からの階層計算は使わない。
  `pytools`をimportできないPEP 723スクリプトだけは`Path(__file__)`を起点にする。
  `CHEZMOI_WORKING_TREE`の読取は対象外とする。
- bash、PowerShell、JSON、chezmoiテンプレートの`~/dotfiles`の固定パスは`install.sh`のクローン先を前提にしている。
  該当は`.chezmoi-source/dot_bashrc`、`share/claude_settings_json_managed.win32.json`、`bin/lab-bg`などである。
  クローン先の前提を変える時はこれらもそろえる。
- Claude Codeに依存しない共通処理は`pytools/_internal/common.py`に置く（`find_dotfiles_root()`、`run_subprocess()`、`atomic_write_*()`、`format_cli_error()`など）。
  Claude Code固有の定数と`run_claude()`は`pytools/_internal/claude_common.py`に置く。
  関数を新しく作る前に既存の公開APIを確かめる（努力目標）。
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）である。
  書込ツールで編集する時は`agent-basics:writing-standards/references/encoding.md`「書込ツールの改行・BOM保全」に従い、ASCII文字だけの修正は`sed -i`で行う。
- 非ASCII文字を標準出力・標準エラーへ書くPythonのCLIは、開始時に両ストリームを`io.TextIOWrapper`でUTF-8かつ`errors="replace"`に再構成する（英語版Windowsなどで既定の文字コードに依存すると、日本語の出力で停止するため）。
  再構成を通さずに出すログと標準出力の文面は、`str.encode("cp932")`が成功する文字に収める（U+20BB7などの一部の漢字、em dash、en dash、`✓`、`•`は使わない）。
- `pytools/post_apply.py`の各ステップの`run`は`PostApplyOutcome`だけを返し、失敗をスキップと失敗のどちらで扱うかは`pytools/_internal/post_apply_outcome.py`の`PostApplyOutcome`のdocstringに従う。
  対象OSは`_StepSpec.platforms`だけで指定する。
- 外部コマンドの失敗をエラーメッセージ、例外、警告へ変える時は、`format_cli_error()`の結果（標準エラーと標準出力の併記、両方が空なら終了コード、実行できなければその旨）を含める。
  終了コードだけでは原因と復旧の操作が伝わらないためである。
  接続不能などの環境の異常（`systemctl --user`がユーザーマネージャーに接続できない、など）は、対象サービスの状態と区別して環境側の問題として復旧手順を案内する。
- post_applyのステップで外部ツールが無いことを理由にステップ全体を省く場合は、そのツールを同じか先行するステップで自動導入するか、`README.md`に手動の復旧手順を書く（導入先をユーザーが選ぶアプリケーションは除く）。
- post_applyが配置するファイル（ランチャー、フラグファイル、systemd unitなど）はchezmoiの管理外である。
  配置先を変えたか処理を廃止した場合は、同じ変更で旧パスを`_REMOVED_PATHS`（ユーザーが編集しうるファイルは`_REMOVED_PATHS_IF_CONTENT`）へ登録する。
  撤去テーブル（この2つと`update_claude_settings.py`の除去テーブル）の登録日と6か月後の削除は`.claude/skills/dotfiles-repo-layout/SKILL.md`「ディレクトリ構造に関する注意点」に従う。
  登録日の有無は`pytools/removed_paths_invariant_test.py`が確かめ、期限は定期実行のAudit workflowが14日前から報告する。
- 通常の`make test`は`rust/`配下を検証しないため、変えたクレートで`cargo fmt --check`、`cargo clippy`、`cargo test`を実行する（CIでは`rust-lint`ジョブが検証する）。
  クレートの版数の更新は`.claude/skills/release-dotfiles/SKILL.md`の手順1に従う。
