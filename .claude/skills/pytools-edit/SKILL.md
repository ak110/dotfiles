---
name: pytools-edit
user-invocable: false
description: >
  `pytools/`、`scripts/`、`libexec/`、`bin/`、`rust/`配下のコマンドラインツール、スクリプト、フックスクリプトを
  新規作成・編集するときに使う。
  配置規約、実装規約、PEP 723、cmdファイルのエンコーディングを扱う。テストの配置は`develop-dotfiles`が扱う。
---

# pytools・scripts・binの編集

このスキルは、本リポジトリにおけるコマンドラインツールや開発スクリプトの配置規約および実装規約をまとめたものである。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../develop-dotfiles/SKILL.md` | 判断 | `read:*/skills/develop-dotfiles/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../release-dotfiles/SKILL.md` | 判断 | `read:*/skills/release-dotfiles/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| `bin/`配下の`*.cmd`を書込ツールで扱う前 | `agent-basics:writing-standards`の`references/encoding.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/encoding.md` | |
| `rust/`配下のクレートを編集する前 | `agent-basics:writing-standards`の`references/rust.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/rust.md` | |

## 配置規約

- `pytools/`のトップレベルには、`project.scripts`から参照される公開CLIモジュール（`<name>.py`またはサブパッケージ`<name>/`）を配置し、bash補完（argcomplete）に対応させる。サブパッケージ構成にする場合は、`__init__.py`で`_cli.py`の`main`を再エクスポートし、`project.scripts`からはそのパッケージの`main`を参照するように設定する。
- 内部でのみ使用するプライベートなヘルパーモジュールは、`pytools/_internal/`配下に配置する。
- 開発時やCIの工程（prek、Makefile、pyfltr、CI）から起動するスクリプトは、`scripts/`配下に配置する（エンドユーザーの日常的な環境で実行するものは対象外）。
- 他のプログラムから呼び出され、`pytools`パッケージの外側でエンドユーザーの環境（LinuxおよびWindows）で動作する実行ファイルは`libexec/`配下に配置する（`bin/`のランチャー、chezmoiの後処理スクリプト、systemd unit、フック定義から起動されるスクリプトなど）。両OSで正しく動作するように記述する。
- `scripts/`や`libexec/`配下のPythonスクリプトは`[project.scripts]`には登録せず、PEP 723形式の単独実行スクリプト、またはプロジェクト環境で直接起動するスクリプトとして記述する。配布用wheelパッケージには`pytools`のみが含まれるため、`pytools`内から`scripts/`や`libexec/`をimportすることは禁止する。
- 単純なコマンドラッパーを作成する場合は、`scripts/new_bin_cmd.py <name> <command...>`を実行して、`bin/<name>`と`bin/<name>.cmd`のペアを生成する。
- 実行頻度が非常に高いフックやstatusline相当のスクリプトは、Windows環境における`uv run`の起動オーバーヘッドを考慮し、ネイティブバイナリ化を第一候補として検討する（先行事例として`rust/claude-statusline/`を参照）。

## 実装規約

- dotfilesの作業ツリーの位置を取得する際は、必ず`pytools._internal.common.find_dotfiles_root()`を使用する。CI環境やエンドユーザー環境では作業ツリーが`$HOME`直下にあるとは限らず、また階層数に基づく相対解決はディレクトリ構成の変更時に壊れやすいため、`~/dotfiles`という固定パスや`Path(__file__)`からの階層計算で取得しない。なお、ルートが見つからず`None`が返された場合の処理は呼び出し元が判断する。`pytools`をimportできないPEP 723スクリプトに限っては、`Path(__file__)`を起点として解決する。`CHEZMOI_WORKING_TREE`の読み取りは対象外とする。
- bash、PowerShell、JSON、chezmoiテンプレート内に記述されている`~/dotfiles`という固定パスは、`install.sh`がクローンする既定の配置場所を前提としている。これには`.chezmoi-source/dot_bashrc`、`share/claude_settings_json_managed.win32.json`、`bin/lab-bg`などが該当する。クローン先の前提を変更する場合は、これらの記述も漏れなくそろえる。
- Claude Codeに依存しない汎用的な共通処理は、`pytools/_internal/common.py`（`find_dotfiles_root()`、`run_subprocess()`、`atomic_write_*()`など）に配置する。Claude Code固有の定数や`run_claude()`は`pytools/_internal/claude_common.py`に配置する。新しく関数等を作成する前に、既存の公開APIが存在しないかをあらかじめ確認する（努力目標）。
- `bin/`配下の`*.cmd`ファイルはCP932（Shift_JIS）でエンコードされている。書き込みツールで編集する際は、`agent-basics:writing-standards`の`references/encoding.md`「書込ツールの改行・BOM保全」に従う。ASCII文字のみの修正であれば`sed -i`を用いて行う。
- 非ASCII文字を標準出力や標準エラー出力へ書き出すPython CLIツールは、処理の開始時に両ストリームを`io.TextIOWrapper`を用いてUTF-8かつ`errors="replace"`で再構成する。英語版Windowsなどの環境でシステムの既定エンコーディングに依存していると、日本語を出力した瞬間に文字コードエラーで停止するためである。
- 上記のストリーム再構成を通さずに直接出力されるログメッセージや標準出力の文面は、`str.encode("cp932")`が成功する文字の範囲に収める（U+20BB7などの一部の漢字や、em dash、en dash、`✓`、`•`などはCP932でエンコードできないため使用を避ける）。
- `pytools/post_apply.py`における各ステップの`run`メソッドは、必ず`PostApplyOutcome`のみを返すように実装する。処理の失敗をスキップとして扱うか失敗として扱うかの基準は、`pytools/_internal/post_apply_outcome.py`にある`PostApplyOutcome`のdocstringに従う。対象OSの指定は`_StepSpec.platforms`のみで行う。
- 外部コマンドの失敗をエラーメッセージ、例外、警告へと変換する際は、`pytools/_internal/common.py`の`format_cli_error()`による出力結果を含める。この関数は、キャプチャ済みの標準エラー出力と標準出力を併記し、両方が空であれば終了コードを、コマンド自体を実行できなかった場合はその旨を返す。終了コードだけを伝えても、失敗の原因や復旧に向けた次の操作がdotfilesユーザーに伝わらないためである。なお、接続不能などの環境異常（例: `systemctl --user`がユーザーマネージャーに接続できない場合など）は、対象サービス自体の状態（無効化されている、起動していないなど）として解釈するのではなく、環境側の問題として区別したうえで復旧手順を案内する。
- post_applyのステップにおいて、外部ツールが存在しないことを理由にステップ全体をスキップする場合は、そのツールを同一または先行するステップで自動導入するか、あるいは`README.md`に手動での復旧手順を明記しておく。なお、dotfilesユーザーが各自で導入先を選択するようなアプリケーションは除く。
- post_applyの処理で配置されるファイル（ランチャー、フラグファイル、systemd unitなど）はchezmoiの管理外となるため、適切に登録しておかないと古い生成物が配布先に残り続けてしまう。そのため、配置先のパスを変更したり処理自体を廃止したりした場合は、同じ変更の中で旧パスを`_REMOVED_PATHS`（dotfilesユーザー自身が編集する可能性のあるファイルは`_REMOVED_PATHS_IF_CONTENT`）に登録する。撤去テーブル（この2つおよび`update_claude_settings.py`の除去テーブル）に登録する各項目には登録日を明記し、登録から6か月が経過したものは一覧から削除する。この運用規則は`pytools/_internal/removal_registry.py`に定義されており、期限切れの検出テストは`pytools/removed_paths_invariant_test.py`が担当している。
- `rust/`配下のコードは、`rust/<クレート名>/`という単位でCargoクレートとして配置し、実装作法は`agent-basics:writing-standards`の`references/rust.md`に従う。通常の`make test`では`rust/`配下は検証されないため、変更を加えたクレート内で`cargo fmt --check`、`cargo clippy`、`cargo test`を変更範囲のローカル検証として実行する（CIでは`rust-lint`ジョブがこれを検証する）。クレートのバージョン番号を更新する際は、`release-dotfiles`の手順を参照する。
