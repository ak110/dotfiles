# dotfilesのテスト配置

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 不変条件のテストの検証対象を探索する時 | `agent-basics:check-execution/references/verification-scope.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../agent-toolkit-edit/references/distribution-and-hooks.md#「agent_toolkitパッケージの配置と層」` | 判断 | `section:*/skills/agent-toolkit-edit/references/distribution-and-hooks.md#「agent_toolkitパッケージの配置と層」` |

## テスト配置

- テストコードは、検証対象となる実装やファイルの可能な限り近くに配置する。Pythonモジュールの単体テストは同一ディレクトリ内の`<name>_test.py`とし、文書・設定ファイル・スクリプトの実物を読み込んで検証するテストは、そのファイルと同じディレクトリか、対象ファイルを包括する最も近い上位ディレクトリに置く。fixtureで入力を組み立てて実装を呼び出すテストも、実装の近くに配置する。やむを得ず離れた場所に置く場合は、配置可能な最も近い場所を選び、その理由をモジュールのdocstringに明記する
- 複数の領域にまたがる既存成果物の不変条件（インバリアント）を検証するテストは、`*_invariant_test.py`と命名する。リポジトリルートおよび`agent-toolkit/`の`pyproject.toml`にある`pytest-fast-targets`はこの命名規則に基づいて対象を選定し、`pyfltr fast`で実行されるため、通常の動作テストとは別ファイルに分ける。検証対象の探し方は`agent-basics:check-execution`の`references/verification-scope.md`に従い、エージェント向け文書側に対象ファイルの対応表をむやみに増やさない
- テストの共通ヘルパーモジュールは、`pytools/`配下では`pytools/_internal/_test_helpers.py`、リポジトリ直下では`_test_helpers.py`、`scripts/`配下では`scripts/_scripts_test_helpers.py`にそれぞれ配置する。リポジトリ直下と`scripts/`はどちらも`sys.path`に追加されるため、ヘルパーモジュール名が重複しないよう配慮する。なお、`agent-toolkit/`のテストコードは配布物の独立性を保つため、`pytools/_internal/`を参照しない構成にする。共通処理が必要な場合は、`agent-toolkit-edit`の`references/distribution-and-hooks.md`「agent_toolkitパッケージの配置と層」に記載されたテスト専用パッケージに配置する
- 各テストは`agent-toolkit/agent_toolkit/_testing/isolation.py`による環境隔離のもとで実行され、ホームディレクトリや設定ディレクトリはテストごとに用意される一時ディレクトリを指す。実際のGit操作を伴うテストでは、コミット履歴、タグ、ブランチ、作業ツリーの状態をテスト自身の一時リポジトリ内に構築し、すべてのGitコマンド呼び出しで対象リポジトリを明示的に指定する。この際、`_testing/git_repository.py`が提供する`init_repository`、`commit_all`、`run_git`を活用できる。実行元リポジトリ自体の不変条件を検証するテストが存在するため、テスト実行時のカレントディレクトリ（cwd）は常に実行元リポジトリのルートを維持する。再検証手順は`docs/development/audit-records.md`「develop-dotfiles：Git動作テストの状態準備：2026年10月9日」を参照する
- 外部ランタイムやCLIを子プロセスとして起動するテストを新設・修正する場合は、同モジュールの`host_environ()`を用いてテスト起動に必要なホスト側の環境変数を構築し、子プロセスの`env`へ渡す。子プロセスに直接環境変数を渡せない実行形態の場合は、`restore_host_environment(monkeypatch)`を使ってテストプロセス自身のHOME・設定ディレクトリ・PATHを一時的に隔離前の状態に戻し、テスト対象の設定や状態は別の一時ディレクトリへ明示的に退避して隔離性を確保する。
  変更範囲を検証する際は、[CI設定](../../../../.github/workflows/ci.yaml)の対象ジョブで使われるランチャーを経由してもテストを実行する。
  以下の専用CLIスクリプトは、uvによるPATHの変更後にmiseのshimを先頭へ配置し、目的のランタイムが正しく解決されることを確認したうえで、同一Pythonプロセスからpytestを起動する。runtime名と`--`以降のpytest引数を変更対象に合わせて指定し、修正前の失敗ケースと突き合わせて環境変数の設定漏れを検出できることを確認する。`-v -n 0 -p no:cacheprovider`はスクリプト側で自動付与され、pytestの終了コードがそのまま返される。miseの照会やshimの解決に失敗した場合は、診断メッセージを出力して終了コード2で停止する。

  ```sh
  uv run --frozen python scripts/run_mise_runtime_tests.py node -- agent-toolkit/agent_toolkit/_hooks/message_format_test.py
  ```

- パッケージをダウンロードして実行する外部ツール（pnpmの`dlx`やcorepackなど）を扱うテストでは、同モジュールの`share_package_caches`を利用して、取得したパッケージキャッシュの保存先のみをホスト環境と共有する
- `pytools`の配布パッケージには、`[tool.hatch.build.targets.wheel]`の`exclude`設定により`*_test.py`および`_test_helpers.py`を含めない
- `scripts/`配下のディレクトリは、pytestのprependモードによって自動的に`sys.path`に追加されるため、テストコードから直接importできる。import対象とするスクリプトはアンダースコア区切りで命名し、shebang（`#!/...`）を持つスクリプトには実行権限（`chmod +x`）を付与する
- 別ディレクトリにあるスクリプトを、テストコードから単独のモジュール名（裸の名前）で直接importすることは避ける。pytestのprependモードやpylintは、同一の実行に含まれるファイルのディレクトリのみを`sys.path`に追加するため、特定のテストや単体ファイルのみを指定して実行した場合にモジュール解決エラーが発生する。登録済みスクリプトを実行する場合は`agent_toolkit._atk.run_script.dispatch`を経由して呼び出すか、テストコード内で`sys.path`に対象ディレクトリを明示的に追加する。この規約違反は`test_import_resolution_invariant_test.py`によって自動検知される
