# dotfilesの検証で使う値

本書は`.claude/skills/dotfiles-development/SKILL.md`の読込表から読む参照資料であり、変更範囲の検証で本リポジトリが使う値、push前に実行するチェックとCIだけが実行するチェック、`agent-doc-tone`の対処を持つ。

## 変更範囲の検証の値

変更範囲の検証の対象は`agent-toolkit:check-execution`の類型で選ぶ。本リポジトリでの値は次のとおり。

- 横断テスト: `SKILL.md`「テスト配置」の`*_invariant_test.py`。変更範囲の検証へ`uv run --frozen pyfltr fast --commands=pytest`（対象ファイルを渡さずリポジトリrootで実行）を含め、rootと`agent-toolkit/`の両方を実行する。commit時のprekも同じfastを動かすが、検証の後なので変更範囲の検証の結果には使わない。前提と再検証手段は`docs/development/audit-records.md`「.claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月4日」にある
- WI画面の資産と擬似DOM: `agent-toolkit/agent_toolkit/_testing/serve_support.py`の`_run_node_ui`が擬似DOMの構築と実行に読む資産が対象である。
  対象は`agent-toolkit/agent_toolkit/_atk/serve/static/`の`common.js`・`wi.js`・`index.html`と、`serve_support.py`自身に限る。
  `static/`配下のその他の資産の変更には本項を適用せず、他の項目と`agent-toolkit:check-execution`の類型で検証対象を選ぶ。
  `uv run --frozen pytest -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/serve`を含める。
  共有ヘルパーを経由するテストが`app_test.py`・`assets_test.py`・`cli_test.py`・`config_test.py`・`state_test.py`へ分かれている。
  変更ファイルの名前だけでは消費側を選べないため、serve全域を検証する
- 期待値を保持するテスト: `agent-toolkit/agent_toolkit/_hooks/`のエンドユーザー向け通知文言は、hook固有の`<hook名>_test.py`が期待値を持つ
- 別の文法で定義を読む横断テスト: `.github/workflows/`のjob・step名やpytestの対象指定を変えた場合はリポジトリ直下の`ci_workflow_test.py`を含める。
  `agent-toolkit/hooks/hooks.json`・`hooks.codex.json`の登録を変えた場合と、`agent-toolkit/agent_toolkit/hook.py`か各イベントの処理が読み込むモジュールを変えた場合は`agent-toolkit/agent_toolkit/hook_test.py`を含める。
  どちらも変更ファイルの名前からは選ばれない
- 共有契約を変えた場合: `uv run --frozen pytest -v -p no:cacheprovider agent-toolkit/agent_toolkit`。対象は`_common/`配下、`_hooks/`の通知生成元の`source`・`kind`、`atk.py`のサブコマンド登録、2つ以上の`atk`サブコマンドが呼ぶ処理や共通出力の契約の変更である。`agents_server`のMCPツールと`atk agents wait`の公開応答の項目（内容・書式・条件・有無）の変更も含む。同じ応答の項目をMCP層、CLIの待機、自動再開後の応答のテストが別ファイルで確かめるためである。コメント・空白だけの変更は対象外とする
- 配布物の版指定: 次のいずれかを変えた場合は`uv run --frozen pytest -v -p no:cacheprovider install_sh_test.py`を含める。このテストは隔離したHOMEで実際のuvを使い、post-applyの`uv tool install`とpyfltr MCPのウォームアップを実行する。`pytools/_internal/warmup_pyfltr_mcp_test.py`は`uvx`を置き換えるため版指定の解決を確かめない
  - `agent-toolkit/.mcp.json`の版指定（生成物の`agent-toolkit/mcp.json`と`agent-toolkit/.mcp.codex.json`を含む）
  - リポジトリ直下と`agent-toolkit/`の`pyproject.toml`の`dependencies`
  - `.chezmoi-source/`の導入・更新処理がパッケージマネージャーへ渡す版指定
- パッケージ外の呼び出し元: `agent-toolkit/`の外で`agent_toolkit`をimportするのは`pytools/`と`scripts/`である。`agent-toolkit/agent_toolkit/`配下の`*_test.py`以外のPythonファイルを変更した場合は`uv run --frozen pytest -v -p no:cacheprovider pytools scripts`
- Function hooks module: `agent-toolkit/hooks/`の`*.ts`・`*.tsx`（`*.test.ts`・`*.test.tsx`を含む）を変更した場合は次の2つを含める。`make test`は`tsc`を有効にしておらず、CIはClaude Codeを導入しないため、どちらも実行しない
  - 型チェック: `npx --yes -p typescript@5.9.3 tsc --project <設定ファイル> --pretty false`。型宣言はホスト同梱の`plugin-authoring`スキルの起動時にClaude Codeがその基準ディレクトリへ書く`types/claude-code.d.ts`か、モジュールを読み込んだプラグインの`.claude-plugin/types/`から得る。設定ファイルはmanaged-tempへ置き、`include`に型宣言と`agent-toolkit/hooks/`の`*.ts`・`*.tsx`を挙げる。`compilerOptions`は型宣言の冒頭が示す値に`allowImportingTsExtensions: true`を加える。設定ファイルと型宣言はリポジトリへ追加しない
  - モジュールテスト: `claude plugin test <worktree>/agent-toolkit`（プラグインのルートを渡す。`agent-toolkit/hooks`を渡すと`hooks/hooks.json`が見つからず失敗する）
- 名前の削除・改名の全体静的確認: `uv run --frozen pyfltr run --commands=ty`。対象ファイルを渡さずリポジトリ全体を対象にし、Pythonファイルを変更するレーンでは計画の`変更範囲の検証`行へ含める
- CIのcontainer jobの`uv sync`が読む入力を変えた場合: `make ci-deps-check`。対象はPython版指定（`.python-version`、`.github/workflows/ci.yaml`のmatrixのPython版）と、同ファイルの`ネイティブ依存インストール`stepである。リポジトリ直下の`uv.lock`、同じ場所の`pyproject.toml`の依存宣言と`[tool.uv]`も対象に含む。lockだけの依存追加・削除・更新と、`agent-toolkit/pyproject.toml`の依存変更に伴う直下のlock更新も含む。`make update`はlock更新の後にcontainer依存導入を常に確かめる。CIと同じimageの中でmatrixの各Python版の依存導入と`uv sync`を再現し、ローカルではビルドできてもCIでは失敗するsdistなどをpush前に検出する。imageと導入パッケージと版は`Makefile`の変数が持ち、`ci_workflow_test.py`がCI定義との一致を確かめる。dockerを要し1版あたり約1分かかる。
  再検証の記録は`docs/development/audit-records.md`の「.claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月18日」にある
- 統合後の検証: rebase後の組合せはcommit時に確かめられないため、fast-forwardの前に専用worktreeで`make sync`の後に次を1回実行する。共有契約とパッケージ外の呼び出し元のpytest、`uv run --frozen pyfltr fast --commands=pytest`、`ty`、`uv run --frozen pyfltr run --commands=arid`

## push前のチェックとCIだけが実行するチェック

公開前の全体検証は`agent-toolkit:commit`が公開工程で読ませる資料の「検証とCI」に従い、CIが実行しないチェックと、変更ファイルだけでは検出できない複数ファイル間の問題を全体で確かめる。本リポジトリでは次を実行する。

- 最初に環境同期: `make sync`。仮想環境を`uv.lock`と`.python-version`へそろえ、CIの新しい環境と同じ条件で後続のチェックを動かす
- CIのpyfltrが無効化するチェック: `uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version --enable=statusline-version`
- レーンをまたぐ重複実装: `uv run --frozen pyfltr run --commands=arid`
- 型と静的参照の整合: `uv run --frozen pyfltr run --commands=ty`
- 複数ファイルにまたがる循環import: `uv run --frozen pyfltr run --no-fix --commands=pylint --pylint-args='--disable=all --enable=cyclic-import'`

`make test`とCIの`python-lint (3.15)`ジョブの差は、同ジョブが`pyfltr ci --disable=pytest,claude-plugin-validate,statusline-version`で無効化するチェックである。Python 3.15のpytestは`pytest (3.15)`ジョブが所有する。次はローカルの`make test`では実行されない。

- `test-windows`ジョブ: Windows実機でのchezmoi適用、Windows固有のテストと公開ランチャー確認
- `update-dotfiles-upgrade (windows)`ジョブ: Windows旧版からのupdate-dotfiles更新検証
- `test-linux`ジョブ: `install.sh`とchezmoiの実適用
- `pytest (3.15)`ジョブ: Python 3.15でのpytest
- `rust-lint`ジョブ: `rust/claude-statusline/`のcargo検証
- `browser-e2e`ジョブ: 実ブラウザーテスト（ローカルでは`make test-browser`）

## agent-doc-tone

- `make test`とcommit時のpre-commit（ステージした対象ファイル）が`agent-doc-tone`を実行する。単独では`uv run --frozen pyfltr run --commands=agent-doc-tone`で起動する
- 対象はエージェントが実行時に読むMarkdown（`AGENTS.md`・`agent-toolkit/`のrules・skills・share・`.chezmoi-source/dot_claude/`・`.claude/skills/`）である。`agent-toolkit/`ではコードのコメント・docstring・表示文・注入文も語の判定の対象とする
- 文体の密度が閾値を超えたファイルを指標付きで報告する（指標と閾値は`scripts/check_agent_doc_tone.py`のdocstring）。同スクリプトの`_DENIED_PATTERNS`が持つ既存の検出条件へ説明文が一致すると、ファイルと行を示して非0で終える（pre-commitはcommitを止める）
- 文脈で対象と動作が伝わりにくい語（同スクリプトの`_CAUTION_PATTERNS`）は警告だけで終了コード0とする。文脈で伝わるかを確かめる補助であり、正確な専門語や承認済みの呼称はそのまま保つ。自作の正しい例と説明は通常の検査へ渡し、引用の原文、検出用データと保存形式の名称は役割を区別して保持する
- 密度超過では、1行目の診断が超過した指標ごとの比率、件数と上限以下へ戻すのに必要な最少の書き換え件数を示す
- 後続の診断は、本文で数えた否定形で終わる文と「当該」の出現を物理行と抜粋付きで全件列挙する。引用内の段落も本文として数え、コードと検出データは密度の分子と分母の対象外とする。否定形終端率は文数、「当該」の出現率は出現回数を数えるため、同じ文に複数回出た語は出現ごとに書き換える
- 列挙された本文の位置から必要な件数の要素を選び、否定形の宣言は肯定形へ、法令調の指示語は指す対象が分かる文へ書き直す。語の再使用は`agent-toolkit:writing-standards`のtextlint指摘の修正方針に従い文全体を書き直す
