# dotfilesの検証で使う値

本書は`.claude/skills/dotfiles-development/SKILL.md`の読込表から参照する資料である。変更範囲の検証において本リポジトリで指定する具体的な値、push前に実行すべきチェックとCIのみが実行するチェックの一覧、および`agent-doc-tone`の指摘への対処手順をまとめている。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:check-execution/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「テスト配置」` | 判断 | `section:*/skills/dotfiles-development/SKILL.md#「テスト配置」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |

## 変更範囲の検証の値

変更範囲の検証対象は、`agent-toolkit:check-execution`の類型に基づいて選定する。本リポジトリで指定する具体的な値は次のとおりである。

- 横断テスト: `SKILL.md`「テスト配置」に記載された`*_invariant_test.py`を指す。変更範囲の検証には`uv run --frozen pyfltr fast --commands=pytest`（個別ファイルを渡さず、リポジトリルートで1回実行）を含める。この1回の実行により、ルートリポジトリとサブプロジェクト`agent-toolkit/`の両方がまとめてテストされる。保存した出力結果から`# subproject: .`と`# subproject: agent-toolkit`それぞれのpytestの終了状態を確認し、双方のプロジェクトにまたがる横断検証の結果として採用する。ルートと`agent-toolkit/`の`pytest-always-targets`設定により、pytestを実行する他のpyfltr操作（対象ファイルを指定した`run`やMCP経由の`run`、`commands`未指定の実行など）でも、同じ横断テストが自動的に追加されて実行される。変更範囲の検証にそうした実行が含まれている場合は、その実行結果からサブプロジェクトごとの終了状態を確認して横断検証の結果に充てることができ、同じ内容に対してfastを重ねて実行する必要はない。なお、コミット時にprekが実行するfastは検証完了後の動作であるため、変更範囲の検証結果の根拠としては扱わない。前提条件と再検証の手順は`docs/development/audit-records.md`「.claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月4日」に記載されている。
- WI画面のアセットと擬似DOM: `agent-toolkit/agent_toolkit/_testing/serve_support.py`内の`_run_node_ui`が擬似DOMの構築と実行時に読み込むアセットが対象となる。
  具体的には、`agent-toolkit/agent_toolkit/_atk/serve/static/`にある`common.js`、`wi.js`、`index.html`、および`serve_support.py`自身の変更が該当する。
  `static/`配下にあるその他のアセットの変更には本項を適用せず、他の検証項目や`agent-toolkit:check-execution`の類型に沿って対象を選ぶ。
  検証には`uv run --frozen pytest -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/serve`を実行する。
  共有ヘルパーを利用するテストは`app_test.py`、`assets_test.py`、`cli_test.py`、`config_test.py`、`state_test.py`に分かれており、変更したファイル名だけから利用側のテストを特定することが難しいため、serveモジュール全体を検証対象とする。
- 期待値を保持するテスト: `agent-toolkit/agent_toolkit/_hooks/`にあるエンドユーザー向け通知メッセージを変更した場合は、各hookに対応する`<hook名>_test.py`が期待値を持っているため、対象のテストを実行する。
- 別の構文で定義を読み込むテスト（「横断テスト」の項と異なり、`pyfltr fast`の対象に含まれないため個別に指定する）: `.github/workflows/`内のジョブ名・ステップ名やpytestの対象指定を変更した場合は、リポジトリ直下の`ci_workflow_test.py`を含める。
  `agent-toolkit/hooks/hooks.json`や`hooks.codex.json`の登録内容を変更した場合、および`agent-toolkit/agent_toolkit/hook.py`や各イベント処理が読み込むモジュールを変更した場合は、`agent-toolkit/agent_toolkit/hook_test.py`を含める。
  いずれも変更されたファイル名からは自動選定されないため注意する。
- 共有契約を変更した場合: `uv run --frozen pytest -v -p no:cacheprovider agent-toolkit/agent_toolkit`を実行する。対象となるのは、`_common/`配下、`_hooks/`の通知生成元で使われる`source`や`kind`、`atk.py`のサブコマンド登録、および2つ以上の`atk`サブコマンドから呼ばれる共通処理や出力契約の変更である。また、`agents_server`のMCPツールや`atk agents wait`における公開レスポンスの項目（内容、形式、条件、項目の有無）を変更した場合も含まれる。同一レスポンスの各項目を、MCP層・CLI待機処理・自動再開後処理の各テストが別ファイルで検証しているためである。なお、コメントや空白のみの変更は対象外とする。
- 配布パッケージのバージョン指定: 次のいずれかを変更した場合は、`uv run --frozen pytest -v -p no:cacheprovider install_sh_test.py`を含める。このテストは隔離されたHOME環境で実際のuvを動作させ、post-apply処理における`uv tool install`とpyfltr MCPのウォームアップ処理を検証する。`pytools/_internal/warmup_pyfltr_mcp_test.py`は`uvx`をモックで置き換えるため、実際のバージョン解決までは確認できない。
  - `agent-toolkit/.mcp.json`のバージョン指定（自動生成される`agent-toolkit/mcp.json`および`agent-toolkit/.mcp.codex.json`を含む）
  - リポジトリ直下および`agent-toolkit/`の`pyproject.toml`にある`dependencies`
  - `.chezmoi-source/`のインストール・更新処理がパッケージマネージャーへ渡すバージョン指定
- パッケージ外からの呼び出し元: `agent-toolkit/`の外部から`agent_toolkit`パッケージをimportしているのは`pytools/`と`scripts/`である。そのため、`agent-toolkit/agent_toolkit/`配下にあるPythonファイル（`*_test.py`を除く）を変更した場合は、`uv run --frozen pytest -v -p no:cacheprovider pytools scripts`を実行する。
- Function hooksモジュール: `agent-toolkit/hooks/`配下の`*.ts`および`*.tsx`（テストを含む）の変更は、既存の`make test`とコミットフックの`pyfltr fast`から`function-hooks-types`を起動し、全モジュールを一括型検査する。
  - 型チェック: TypeScript 5.9.3を使用する。Claude Codeの`plugin-authoring`で生成した`claude-code.d.ts`を環境変数`FUNCTION_HOOKS_TYPES`へ絶対パスで指定する。未指定時はworktree直下または`agent-toolkit/`の`.claude-plugin/types/`と`types/claude-code.d.ts`を参照する。型定義不足は非0終了する。一時設定と生成型定義はコミットしない。CIはClaude Code生成型を用意しないため、このコマンドを明示無効化する。
  - 未指定かつworktree内に生成型がない場合は、OS一時領域（`TMPDIR`などを反映）の直下および`claude-*`ディレクトリ内の`bundled-skills/<版>/*/plugin-authoring/types/claude-code.d.ts`を探索する。PATH上の`claude --version`とディレクトリ版・先頭行の`// Written by Claude Code <版>.`が一致する型を1件使う。不足時は対象版を含む診断と終了2を返す。`--types`は環境変数より優先し、両者の明示指定時は自動探索しない。チェックはモデルを呼び出さない。
  - モジュールテスト: 型の自動探索時と同じPATH上の実行ファイルで`claude plugin test <worktree>/agent-toolkit`を実行する。
    引数にはプラグインのルートパスを渡す。
    `agent-toolkit/hooks`を渡すと`hooks/hooks.json`が見つからずエラーとなる。
- 名前の削除や改名に伴う全体静的チェック: `uv run --frozen pyfltr run --commands=ty`を実行する。対象ファイルを絞らずリポジトリ全体を対象として実行し、Pythonファイルを変更するレーンでは計画書の「変更範囲の検証」欄に必ず含める。
- CIのコンテナージョブにおいて`uv sync`が読み込む入力設定を変更した場合: `make ci-deps-check`を実行する。対象となるのは、Pythonバージョンの指定（`.python-version`、`.github/workflows/ci.yaml`のmatrixにおけるPythonバージョン）や、同ファイル内の「ネイティブ依存インストール」ステップである。また、リポジトリ直下の`uv.lock`や、同じ場所の`pyproject.toml`内の依存関係宣言・`[tool.uv]`設定の変更も含む。lockファイルのみの依存関係追加・削除・更新や、`agent-toolkit/pyproject.toml`の依存変更に伴う直下のlock更新も対象となる。`make update`では、lock更新の後にコンテナー依存のインストール確認を常に実施する。CIと同一のコンテナーイメージ内でmatrixの各Pythonバージョンにおける依存関係の導入と`uv sync`を再現することで、ローカル環境ではビルドできてもCIで失敗するようなsdistパッケージの問題などをpush前に検知できる。使用するコンテナーイメージや導入パッケージ、バージョン情報は`Makefile`内の変数で管理されており、`ci_workflow_test.py`によってCI定義との整合性が検証される。なお、このチェックにはDockerが必要であり、1バージョンあたり約1分を要する。
  再検証の記録は`docs/development/audit-records.md`「.claude/skills/dotfiles-development/references/verification-values.md：変更範囲の検証の値：2026年10月18日」にある。
- 統合後の検証: rebase後のブランチの組み合わせはコミット時には検証できないため、fast-forwardマージを行う前に専用worktree上で`make sync`を実行し、続けて以下を一括で1回実行する。
  - 共有契約およびパッケージ外の呼び出し元に関するpytest
  - `uv run --frozen pyfltr fast --commands=pytest`
  - `ty`
  - `uv run --frozen pyfltr run --commands=arid`

## push前のチェックとCIだけが実行するチェック

公開前の全体検証は、`agent-toolkit:commit`が公開工程で参照する資料「検証とCI」の定めに従い、CIでは実行されないチェックや、変更ファイル単体では検知できない複数ファイル間の問題をリポジトリ全体で確認する。本リポジトリでは以下の手順を実行する。

- 最初に環境を同期する: `make sync`を実行する。仮想環境を`uv.lock`および`.python-version`の内容に合わせ、CIのクリーンな実行環境と同様の条件で後続のチェックを動かす
- CIのpyfltrで無効化されているチェックを実行する。
  `uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version,function-hooks-types --enable=statusline-version`
- 複数レーンにまたがる重複実装を検査する: `uv run --frozen pyfltr run --commands=arid`
- 型定義と静的参照の整合性を検証する: `uv run --frozen pyfltr run --commands=ty`
- 複数ファイル間にまたがる循環importを検査する: `uv run --frozen pyfltr run --no-fix --commands=pylint --pylint-args='--disable=all --enable=cyclic-import'`

CIの`python-lint (3.15)`ジョブは、ローカルの`make test`から一部のチェックを除外する。
実行コマンドは`pyfltr ci --disable=pytest,claude-plugin-validate,statusline-version,function-hooks-types`である。Python 3.15環境でのpytestは`pytest (3.15)`ジョブが担当している。また、以下のジョブはローカルの`make test`では実行されない。

- `test-windows`ジョブ: Windows実機環境でのchezmoi適用、Windows固有のテスト、および公開ランチャーの動作確認
- `update-dotfiles-upgrade (windows)`ジョブ: Windows環境における旧バージョンからのupdate-dotfilesによるアップデート検証
- `test-linux`ジョブ: `install.sh`およびchezmoiの実際の適用確認
- `pytest (3.15)`ジョブ: Python 3.15環境におけるpytestの実行
- `rust-lint`ジョブ: `rust/claude-statusline/`のcargoによる検証
- `browser-e2e`ジョブ: 実際のブラウザーを用いたE2Eテスト（ローカル環境では`make test-browser`で実行可能）

## agent-doc-tone

- `make test`およびコミット時のpre-commit（ステージされた対象ファイル向け）によって`agent-doc-tone`が実行される。単独で実行する場合は`uv run --frozen pyfltr run --commands=agent-doc-tone`を使用する。
- チェックの対象は、エージェントが実行時に参照するMarkdownファイル（`AGENTS.md`、`agent-toolkit/`配下のrules・skills・share、`.chezmoi-source/dot_claude/`、`.claude/skills/`）である。なお`agent-toolkit/`配下では、コード内のコメント、docstring、表示メッセージ、プロンプト注入文も語彙判定の対象に含まれる。
- 文体の密度が規定の閾値を超えたファイルは、測定指標とともに出力される（指標と閾値の詳細は`scripts/check_agent_doc_tone.py`のdocstringに記載されている）。同スクリプトの`_DENIED_PATTERNS`に定義された禁止パターンに説明文が一致した場合は、該当ファイル名と行番号を表示して終了コード1以上で停止する（pre-commitではコミットが中断される）。
- 文脈によって対象や動作が曖昧になりやすい表現（同スクリプトの`_CAUTION_PATTERNS`）については、警告メッセージのみを出力して終了コード0で終了する。これは文脈上意味が正しく伝わるかを確認するための補助機能であり、正確な専門用語や合意済みの正式名称はそのまま維持する。自身で作成した適切な用例や説明は通常のチェックへ通し、引用した原文、検出用の定義データ、保存形式の名称などは用途を区別してそのまま保持する。
- 密度が閾値を超過した場合、診断結果の1行目に超過した各指標の比率、該当件数、および閾値以下へ抑えるために必要な最小書き換え件数が表示される。
- 続く詳細診断では、本文中から検出された否定形で終わる文や「当該」の使用箇所が、行番号および該当箇所の抜粋とともにすべて列挙される。引用ブロック内の段落も本文としてカウントされるが、コードブロックや検出用データは密度の計算対象（分子・分母）から除外される。否定形終端率は文の数を基準とし、「当該」の出現率は単語の出現回数を基準としてカウントするため、同一の文に複数回出現した場合は出現ごとに書き換える。
- 列挙された検出箇所の中から必要な件数を選び、否定形による宣言文は肯定形の表現へ、法令調の指示語は指示対象が具体的に伝わる表現へ書き直す。語彙の再利用を避ける際は、`agent-basics:writing-standards`におけるtextlint指摘の修正方針に準拠し、文全体の構成を見直して書き直す。
