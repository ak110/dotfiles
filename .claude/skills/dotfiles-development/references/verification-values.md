# dotfilesの検証で使う値

本書は`.claude/skills/dotfiles-development/SKILL.md`の読込表から読む参照資料であり、変更範囲の検証で本リポジトリが使う値、push前に実行するチェックとCIだけが実行するチェック、`agent-doc-tone`の対処を持つ。

## 変更範囲の検証の値

- 変更範囲の検証の対象は`agent-toolkit:check-execution`が定める変更範囲の検証の類型で選ぶ。本リポジトリで使う値は次のとおり
  - 横断テスト: ファイル名`*_invariant_test.py`で識別する。rootと`agent-toolkit/`の`pyproject.toml`の`pytest-fast-targets`がこのファイル名で対象を選び、commit時にprekが起動する`pyfltr fast`で自動実行する。通常のcommitではこの自動実行の結果を使う。配置は`pytools-edit`「テスト配置」に従う。fastの前提と再検証の手段は`docs/development/audit-records.md`「.claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月4日」が持つ
  - 期待値を保持するテスト: `agent-toolkit/agent_toolkit/_hooks/`のエンドユーザー向け通知文言は、変更した挙動に対応するhook固有の`<hook名>_test.py`が期待値を持つ
  - 共有契約を変えた場合の検証単位全体: `uv run --frozen pytest -v -p no:cacheprovider agent-toolkit/agent_toolkit`。対象の変更は、`agent-toolkit/agent_toolkit/_common/`配下、`_hooks/`の通知生成元の`source`・`kind`、`atk.py`のサブコマンド登録、または複数の`atk`サブコマンドが共有する処理・出力の契約の変更である。`agents_server`のMCPツールと`atk agents wait`が公開する応答の変更も対象に含める。
    共有する処理・出力の契約は、変更前か変更後に異なる2つ以上のサブコマンドから実際に呼ばれる処理の挙動と、共通出力の内容・書式・条件・有無を指す。公開する応答の変更は、応答の項目の内容・書式・条件・有無の変更を指す。同じ応答の項目をMCP層、CLIの待機および自動再開後の応答のテストが別々のファイルで確かめるためである。いずれも内部のコメント・空白だけの変更は含めない
  - 配布物の版指定をエンドユーザーの環境の解決方法で確かめるテスト: `install_sh_test.py`。隔離したHOMEへ配布設定を展開し、実際のuvと配布する公開待機設定でpost-applyの`uv tool install`とpyfltr MCPのウォームアップを実行する。次のいずれかを変えた場合は、変更範囲の検証へ`uv run --frozen pytest -v -p no:cacheprovider install_sh_test.py`を含める。
    - `agent-toolkit/.mcp.json`の版指定（生成物の`agent-toolkit/mcp.json`と`agent-toolkit/.mcp.codex.json`を含む）
    - リポジトリ直下と`agent-toolkit/`の`pyproject.toml`の`dependencies`
    - `.chezmoi-source/`の導入・更新処理がパッケージマネージャーへ渡す版指定

    `pytools/_internal/warm_pyfltr_mcp_test.py`は`uvx`を代替実行ファイルへ置き換えるため、版指定を解決できるかを確かめない
  - パッケージ外の呼び出し元: `agent-toolkit/`の外で`agent_toolkit`をimportする場所は`pytools/`と`scripts/`である。`agent-toolkit/agent_toolkit/`配下の`*_test.py`以外のPythonファイルを変更した場合は`uv run --frozen pytest -v -p no:cacheprovider pytools scripts`
  - 名前の削除・改名の全体静的確認: `uv run --frozen pyfltr run --commands=ty`。対象ファイルを渡さず`agent-toolkit/`を含むリポジトリ全体を対象にし、Pythonファイルを変更するレーンでは計画の`変更範囲の検証`行へ含める
  - 統合後の検証: fast-forwardの前に専用worktreeで、共有契約とパッケージ外の呼び出し元のpytest、`uv run --frozen pyfltr fast --commands=pytest`と`ty`を1回実行する。`uv run --frozen pyfltr run --commands=arid`も同じ時点で実行する。rebase後の組合せはcommit時には確かめられないため、同じfastの対象選択を使う

## push前のチェックとCIだけが実行するチェック

- 公開前の全体検証は`agent-toolkit:commit`が公開工程の着手時に読ませる資料の「検証とCI」に従う。本リポジトリでpush前に実行するCI非実行のチェックと全体走査のチェックは次の3件である
  - CIのpyfltr実行が無効化するチェック: `uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version --enable=statusline-version`
  - レーンをまたぐ重複実装の検出: `uv run --frozen pyfltr run --commands=arid`
  - 変更ファイルの外に残ったPythonの静的参照の検出: `uv run --frozen pyfltr run --commands=ty`
- `make test`が実行するツール集合とCIの`python-lint (3.14)`ジョブの差は、同ジョブが`pyfltr ci --disable=pytest,claude-plugin-validate,statusline-version`で無効化するチェックである。Python 3.14のpytestは`pytest (3.14)`ジョブが所有する。
  次の自動チェックはローカルの`make test`では実行されず、それぞれのジョブやコマンドで実行する
  - `test-windows`ジョブ: Windows実機でのchezmoi適用、Windows固有のテストと公開ランチャー確認
  - `update-dotfiles-upgrade (windows)`ジョブ: Windows旧版からのupdate-dotfiles更新検証
  - `test-linux`ジョブ: `install.sh`とchezmoiの実適用
  - `python-lint (3.13)`ジョブ: Python 3.13でのpytest
  - `pytest (3.14)`ジョブ: Python 3.14でのpytest
  - `rust-lint`ジョブ: `rust/claude-statusline/`のcargo検証
  - `browser-e2e`ジョブの実ブラウザーテスト: ローカルでは`make test-browser`で実行する

## agent-doc-tone

- `make test`はlinter`agent-doc-tone`を含む。
  commit時のpre-commitも、ステージした変更ファイルのうち対象に当たるものへ`agent-doc-tone`を実行し、3語の検出でcommitを止める。
  単独では`uv run --frozen pyfltr run --commands=agent-doc-tone`で起動する。
  対象はエージェントが実行時に読むMarkdown（`AGENTS.md`・`agent-toolkit/`のrules・skills・share・
  `.chezmoi-source/dot_claude/`・`.claude/skills/`）とする。
  文体の密度を測り、閾値を超えたファイルを指標付きで報告する。
  測る指標と閾値は`scripts/check_agent_doc_tone.py`のdocstringが定める。
  `agent-toolkit/`の説明文はMarkdownの本文・見出し・表に加え、コードのコメント・docstring・表示文・注入文を語の判定の対象とする。
  `agent-toolkit:writing-standards`の表記規則が説明に使わないと定める3語が戻った場合は、ファイルと行を示して非0で終える。
  文脈によって対象と動作が伝わりにくい語（判定する語は同スクリプトの`_CAUTION_PATTERNS`が定める）は、ファイルと行を示す警告を標準エラーへ出力する。警告だけの場合は終了コード0で終える。
  警告は文脈で対象と動作が伝わるかを確かめる補助であり、正確な専門語や承認済みの呼称はそのまま保つ。
  引用、意図的な悪い例、検出用データと保存形式の名称は説明文と区別し、良い例と通常の説明は判定する。
  報告されたファイルは`uv run --frozen python scripts/check_agent_doc_tone.py --report <ファイルのパス>`で
  指標を確かめ、否定形の宣言と法令調の指示語を肯定形と平易な語へ書き換えて密度を下げる。
  語の再使用は`agent-toolkit:writing-standards`が定めるtextlint指摘の修正方針に従って文全体を書き直す
