---
name: pytools-edit
description: >
  `pytools/`・`scripts/`・`bin/`・`rust/`配下のコマンドラインツール・スクリプト・hookスクリプトを
  新規作成・編集するときに使う。配置規約・テスト配置・PEP 723・wheel設定・cmdエンコーディングを扱う。
---

# pytools・scripts・bin の編集

本スキルは本リポジトリのコマンドラインツールと開発スクリプトの配置規約・実装規約を提供する。

## 配置規約

- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュール
  （単一ファイル`<name>.py`またはサブパッケージ`<name>/`配下形態）を置き、bash補完（argcomplete）に対応する
  - サブパッケージは`__init__.py`が`_cli.py`の`main`を再エクスポートし、`project.scripts`はパッケージ名の`main`を参照する
- privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する
- エージェント・hook・自動化など手で起動しないスクリプトは`scripts/`配下へ置く
  （`[project.scripts]`登録は行わず、PEP 723形式の単独実行スクリプトとして書く）
- 単純なコマンドラッパーの新規追加には`scripts/new-bin-cmd.py <name> <command...>`を使う
  （リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成する）
- 高頻度起動するhook・statusLine相当のスクリプトは、Windowsでの`uv run`起動コストを考慮し、
  ネイティブバイナリ化を実装方式の第一候補として検討する（先行事例は`rust/claude-statusline/`）

## 実装規約

- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する
  （CIチェックアウトやエンドユーザー環境で`$HOME`と`~/dotfiles`が一致しない場合にimportに失敗するため）
- `pytools/_internal/claude_common.py`は共通基盤モジュール（`find_dotfiles_root()`・`run_subprocess()`・
  `atomic_write_*()`等）を提供する。新規ヘルパーを書き起こす前に公開APIを確認し、重複定義を避ける
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）で書かれている。書込ツールで扱う手段は`agent-toolkit:writing-standards`の
  `references/encoding.md`「書込ツールの改行・BOM保全」に従い、ASCIIのみの修正は`sed -i`で対応する
- 非ASCIIを標準出力又は標準エラーへ書くPython CLIは、開始時に`io.TextIOWrapper`の両ストリームをUTF-8・`errors="replace"`へ再構成する。英語版Windowsなどのランタイム既定エンコーディングへ依存すると、日本語の最初の出力でCLIが停止するためである
- ストリームの再構成を経由しないログと標準出力のメッセージは、WindowsのCP932で符号化できる範囲に収める。可否は`str.encode("cp932")`の成否で判定する。漢字にもU+20BB7のように符号化できないものがあり、em dash・en dash・`✓`・`•`も符号化できない
- `pytools/post_apply.py`のステップが外部ツールの不在で当該ステップ全体をスキップする場合は、当該ツールを同じステップ又は先行するステップが導入するか、`README.md`が復旧手順を持つかのいずれかを満たす。
  利用者が導入先を選ぶアプリケーションは、この対象から外す
- `pytools/post_apply.py`の工程が配置するファイル（ランチャー、フラグファイル、unitなど）の配置先を改名する場合と工程を廃止する場合は、同じ変更で旧パスを`_REMOVED_PATHS`へ登録する。利用者が編集し得るファイルは`_REMOVED_PATHS_IF_CONTENT`へ登録する。
  工程の生成物はchezmoiの管理外であり、登録しないと旧生成物が配布先に残り続ける（PATH先頭の旧ランチャーが作業ツリー版の`atk`を2か月覆い隠した事例がある）
- `rust/`配下の配置の単位は`rust/<クレート名>/`のCargoクレートとする。
  記述作法は`agent-toolkit:writing-standards`の`references/rust.md`が定める。
  `make test`は`rust/`配下を対象に含まないため、変更したクレートで`cargo fmt --check`、`cargo clippy`及び`cargo test`を近接検証として実行する。
  CIでは`rust-lint` jobが同等の検証を担う。
  配布版数の更新要求は`dotfiles-release`が定め、本書へ再掲しない

## テスト配置

- Pythonテストコードはソースモジュールと同一ディレクトリに`<name>_test.py`として配置する
  （`pytools/`・`scripts/`・`agent-toolkit/`配下いずれも同方式）
- テスト共通ヘルパーは`pytools/`配下では`pytools/_internal/_test_helpers.py`へ集約する。
  `agent-toolkit/`配下のテストは配布物独立性を保つため`pytools/_internal/`配下を参照せず、
  共通化が必要な場合は`agent-toolkit-edit`スキル「scripts配下の配置」節が定めるテスト専用パッケージへ置く
- `pytools`パッケージ配布物にテストコードを含めないため、
  `[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を除外する
- `scripts/`配下はpytestのprependモードで`sys.path`へ自動追加されるためテストから直接importできる。
  importしたいスクリプトはアンダースコア区切りで命名し、shebang付きスクリプトは`chmod +x`する
