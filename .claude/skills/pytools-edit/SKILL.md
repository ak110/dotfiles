---
name: pytools-edit
description: >
  `pytools/`・`scripts/`・`bin/`・`rust/`配下のコマンドラインツール・スクリプト・hookスクリプトを
  新規作成・編集するときに使う。配置規約・テスト配置・PEP 723・wheel設定・cmdエンコーディングを扱う。
---

# pytools・scripts・bin の編集

本スキルは、本リポジトリのコマンドラインツールと開発スクリプトの配置規約・実装規約を提供する。

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
  （CIチェックアウトやエンドユーザー環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが破綻するため）
- `pytools/_internal/claude_common.py`は共通基盤モジュール（`find_dotfiles_root()`・`run_subprocess()`・
  `atomic_write_*()`等）を提供する。新規ヘルパーを書き起こす前に公開APIを確認し、重複定義を避ける
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）で書かれている。UTF-8前提のEdit/Writeツールでは
  文字化けや破損のリスクがあるため、ASCIIのみの修正は`sed -i`で対応する

## テスト配置

- Pythonテストコードはソースモジュールと同一ディレクトリに`<name>_test.py`として配置する
  （`pytools/`・`scripts/`・`agent-toolkit/`配下いずれも同方式）
- テスト共通ヘルパーは`pytools/`配下では`pytools/_internal/_test_helpers.py`へ集約する。
  `agent-toolkit/`配下のテストは配布物独立性を保つため`pytools/_internal/`配下を参照せず、
  共通化が必要な場合は`agent-toolkit/scripts/`配下に独自ヘルパーを置く
- `pytools`パッケージ配布物にテストコードを含めないため、
  `[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を除外する
- `scripts/`配下はpytestのprependモードで`sys.path`へ自動追加されるためテストから直接importできる。
  importしたいスクリプトはアンダースコア区切りで命名し、shebang付きスクリプトは`chmod +x`する
