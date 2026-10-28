---
name: dotfiles-development
user-invocable: false
description: >
  dotfilesリポジトリで`make update`・`make test`・`make format`・`make setup-browser`・`make setup-pwsh`・`make test-browser`を
  実行するとき、pyfltr・MCPの`run`・`pytest`の直接実行を選ぶとき、
  専用worktreeや回収予定の検証用複製の環境を準備するとき、mise trustが必要な作業ツリーと状態ディレクトリを扱うとき、専用worktreeの変更を`atk`で動かすとき、
  画面を実描画で確認するとき、本リポジトリのテスト配置を決めるとき、テストを新規作成・編集するとき、
  commit typeを判定するとき、コミットメッセージ方針や`.gitmessage`を編集するとき、
  Claude Code本体やCodex本体のバイナリを検索してホスト機能の挙動を確かめるとき、
  `agent-toolkit:session-review`の参照文書の位置を確認するときに起動する。
---

# dotfilesの開発手順

本スキルでは、本リポジトリにおける自動チェック、コード整形、依存関係の更新、テストの配置、コミットメッセージの方針、ホスト本体バイナリの検索手順、および振り返りで参照する文書の位置を説明する。
リリース運用は`dotfiles-release`、配布元と配布先の対応関係は`dotfiles-repo-layout`で扱う。

本リポジトリで`agent-toolkit:process-wi`を実行するセッションでは、振り返りと完了報告を含む全工程を3時間以内で終えることを目標とする。
計測範囲は[セッションの所要時間目標](../../../.chezmoi-source/dot_claude/docs/session-review-dotfiles.md#セッションの所要時間目標)に従い、作業の進行中に進め方を見直す際は`agent-basics:writing-standards`の`references/quality-principles.md`「QCDと3段判定」を適用する。

## 名指し参照の読込方法

本文の各参照における適用条件を確認し、選んだ資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の行に指定された資料は、起動本文と一緒に届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 進捗と時間目標を見直す前 | `agent-basics:writing-standards/references/quality-principles.md#「QCDと3段判定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「QCDと3段判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-release/SKILL.md` | 判断 | `read:*/skills/dotfiles-release/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-repo-layout/SKILL.md` | 判断 | `read:*/skills/dotfiles-repo-layout/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:check-execution/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../agent-toolkit-edit/references/distribution-and-hooks.md#「agent_toolkitパッケージの配置と層」` | 判断 | `section:*/skills/agent-toolkit-edit/references/distribution-and-hooks.md#「agent_toolkitパッケージの配置と層」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| `make update`を実行の候補にする前 | 現行`Makefile`の`update` targetと、それが呼び出す子target | 判断 | `section:*/Makefile#make:update` | |
| 公開前に実行する検証を選定する前 | `agent-toolkit:commit`の`references/publish.md`「検証とCI」 | 判断 | `section:agent-toolkit:*/skills/commit/references/publish.md#「検証とCI」` | |
| 変更範囲の検証の対象を選ぶ前、push前の全体検証を行う前、ローカルの`make test`が実行しないチェックを確かめる時、または`agent-doc-tone`の報告へ対処する前 | [references/verification-values.md](references/verification-values.md) | 判断 | `read:*/skills/dotfiles-development/references/verification-values.md` | |
| コミットメッセージのtypeを判定する時 | [commit-types.md](../../../docs/development/commit-types.md)（判定例） | 判断 | `read:*/docs/development/commit-types.md` | |
| 不変条件のテストの検証対象を探索する時 | `agent-toolkit:check-execution`の`references/verification-scope.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/references/verification-scope.md` | |

## 開発手順

### 作業ツリーの準備

- 専用worktreeや回収予定の検証用複製を用意する際は、`mise trust`を実行した後に依存関係を同期し、正しく完了したことを確認してから後続へ渡す。同期時は対象作業ツリーのルートで`make sync`を実行し、リポジトリ直下と`agent-toolkit/`の`.venv`を`uv.lock`に記載されたパッケージ一覧および`.python-version`のバージョンに合わせる。
  - 回収予定の検証用複製には`.git`が含まれる。そのため、managed-tempの一時ルート直下に`mktemp -d '<一時ルート>/<用途>-XXXXXXXX'`で作業領域を作成する。その領域へ`git worktree add --detach`で複製を作成し、`make sync`で環境を整える。通常のファイルコピーで作業ツリーを複製すると、内部にある`.claude/worktrees/`や`.venv`まで実容量で複製してしまう。作業完了時および起動したプロセスの終了後は、`git worktree remove`を実行したうえで`rm -r -- <作成した絶対パス>`により確実に回収する。
  - 回収予定の作業領域ではローカル依存関係の同期のみを行い、Git hookやテンプレートには既存の設定を利用する。共有CLIのインストール元や共通のGit設定は、作業領域の回収後も残す必要があるためである。`mise run bootstrap`、`mise bootstrap`、`make setup`、`uv tool install --editable`、`prek install`、`git config --local commit.template`の実行は、恒久的に使う作業ツリーの初期設定時に限定する。観測と再検証の手順は`docs/development/audit-records.md`「dotfiles-development：回収予定の作業場所の環境準備：2026年10月1日」に記載がある。
- miseの信頼登録は設定ファイルの絶対パスに紐づいており、状態ディレクトリの配下に保存される。このため、複製元と異なる絶対パスで`mise.toml`を参照する作業領域や、`XDG_STATE_HOME`などで状態ディレクトリを切り替える作業領域では、作成したエージェントが検証を開始する前に`mise trust`を実行しておく。
  - linked worktreeは複製元の信頼設定を共有するため、複製元リポジトリのルートにある`mise.toml`に対して1回実行すれば足りる。検証用の複製では複製先の`mise.toml`の絶対パスを指定し、状態ディレクトリを差し替えた環境では自動チェック時と同じ環境変数を渡して実行する。
  - `MISE_TRUSTED_CONFIG_PATHS`を指定すると既存の信頼登録が上書きされ、複製元が未信頼の状態に戻ってしまうため使用を避ける。
- 専用worktreeの変更を`atk`で動作確認する場合（`atk serve`を含む）は、起動するファイルごとにどのバージョンが選ばれるかを確認する。
  - PATH上の`atk`が複製元の`bin/atk`を指している場合、カレントディレクトリ（cwd）が同じリポジトリのworktree配下であれば、そのworktreeの`agent-toolkit/scripts/atk`へ処理を委譲する。Git管理外や別リポジトリの場合は複製元のバージョンを実行する（`uv run atk`でも同様に判定される）。
  - 配布物に含まれる`scripts/atk`は、その配布物の固定ルートで起動する。
  - 検証や調査を委譲する際は、委譲先へ`<対象worktreeの絶対パス>/agent-toolkit/scripts/atk`を渡し、入れ子で呼ぶコマンドにも同じ絶対パスを指定する。
  - `scripts/launch.py`は、依存に作用するpyproject設定・`uv.lock`・選択したPythonの実行ファイルパス・OS・アーキテクチャから共有環境を選定し、書き込み可能なデータ領域へ配置する。配布ルートやプラグイン版だけの違いでは環境を分けない。
  - 使用するCLIの`info`サブコマンドを実行し、動作するルートが検証対象と一致していることを確認する。

### 自動チェックと依存更新

- `make update`は、読込表にあるターゲットの実際の処理対象に変更対象が含まれている場合のみ実行候補とする。現在の主な更新対象は`uv.lock`と`agent-toolkit/uv.lock`の依存関係更新である。lockファイルの更新後はコンテナーでの依存関係インストールを検証し（`ci-deps-check`）、さらに`prek autoupdate`、`mise lock`、`pinact`によるアクション更新、および全テストを実行する。なお`rust/claude-statusline/Cargo.lock`は対象外である。GitHub Actionsのハッシュピンのみを更新する場合は`make update-actions`を実行する。
- 公開前に実行する検証は、`agent-toolkit:commit`の`references/publish.md`「検証とCI」で選定する。ローカル全体検証が必要と判定された場合は`make test`（`uv run --frozen pyfltr run --no-fix`）を用いる。`agent-toolkit:check-execution`を起動し、標準出力と標準エラー出力を保存して結果を確認できる手段（例: `agents_server`の`start`ツールで`shell`を指定）で実行する。
  - pyfltrのフォーマッター段（`ruff-format`、`uv-sort`、`shfmt`、`prek`、`sync-generated-files`）は、`--no-fix`を指定した場合でもファイルを整形して終了コード0を返す。そのため、コミット対象を確定する前に`git status`を実行して意図しない差分が生じていないか確認する。コードの自動修正を明示的に行う場合は`make format`（`uv run --frozen pyfltr fast`）を使用する。

pyfltrを調査や計測の目的で実行する場合は、`--no-fix`を付けて自動修正を抑止する。ただしフォーマッター段は`--no-fix`でもファイルを書き換えるため、読み取り専用で実行したい場合はファイルを変更する段を除外して実行する。

- 特定のファイルに限定して検証する場合は、MCPの`run`ツールにパスを渡すか、`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を実行する。初回は`commands`（CLI引数では`--commands`）を指定せずに実行し、修正後の再実行時のみ失敗したチェックに対象を絞る。
  - pytestを含む検証を実行する際は、指定したファイルにかかわらず、リポジトリルートと`agent-toolkit/`の両方で`pytest-always-targets`に定義された横断テストが常に追加される。詳細は`references/verification-values.md`の横断テストの項を参照のこと。同一内容に対して2回目以降の静的チェックを行う場合は、`commands`でpytestを除外し、失敗したテストのみを再確認する際は`--only-failed`を指定する。単一テストをピンポイントで確認したい場合は、対象ファイル（必要に応じて`-k`）を指定して`pytest`を直接実行し、収集されたテスト件数が想定と一致していることを確認する。
- デバッガーの使用、最小限の再現コード実行、環境の切り分け時には、`pytest`を直接実行してよい。`-o`や`-p`は`pytest`固有のオプションであり、`pyfltr run`に渡すと引数エラー（終了コード2）となる。`-o addopts=''`で既存の`addopts`を無効化する場合は、合わせて`-p no:cacheprovider`を指定する。
- pyfltrの実行枠として扱われるのは、`make test`、`uv run --frozen pyfltr run`、`git commit`（コミットフック内の`pyfltr fast`）、および`agent_toolkit._testing.pytest_plugin`を読み込むpytestの実行である。ルートと`agent-toolkit/`の`addopts`には、このプラグインを読み込む`-p`オプションが設定されている。`-o addopts=''`でプラグインを解除する`make test-browser`などは実行枠の対象外となる。
  - 実行枠に含まれる検証をシェルから実行する際は、`agent-toolkit:check-execution`「統合実行ツール経由の起動」に記載の起動形式と3分類に従う。
- `.venv`を再作成する可能性がある自動チェック（`uv run --python`によるPythonバージョンの切り替えや依存関係の更新など）は、同一の仮想環境パスに対して直列で実行する。並行して進める場合は、チェックごとに異なる仮想環境パスを明示的に割り当てる。
- pyfltrの実行時間を比較・確認する際は、`uv run --frozen pyfltr list-runs`で取得した実行ID（run_id）を使い、`uv run --frozen pyfltr show-run <run_id>`を実行する。

### 画面とブラウザー

- `make setup-browser`（Chromiumとシステム依存パッケージ）および`make setup-pwsh`（Ubuntu/Debian向けPowerShell）は、ユーザーが自身の端末で直接実行する初期セットアップ用のコマンドである。エージェントが`make test-browser`の前提環境の不足を検知した場合は、システム依存パッケージのインストールを勝手に行わず、不足している前提条件と未実施の検証項目をユーザーに報告する。
- `atk serve`のブラウザーUI、ブラウザー経由でアクセスされるサーバー側処理、静的ファイル、または実ブラウザーテストを変更した場合は、`make test-browser`を実行して動作を確認する。
- レビュー目的で一時データを使って`atk serve`を起動する際は、保存先環境変数を`AGENT_TOOLKIT_PRIVATE_NOTES=<一時領域の絶対パス>`のように設定する。起動する前に、本番起動と同じCLIおよび同じ環境変数を用いて`atk config get private_notes`を実行し、出力された保存先が一時領域のパスと一致していることを確認する。パスが一致しない場合や解決に失敗した場合は起動を取りやめ、環境変数の設定を見直したうえで再度一致を確認する（誤った変数名を指定すると、既定の通常保存先である実データに接続してしまうためである）。
- 画面の描画結果を実際に確認する際は、ブラウザー操作ツールのほかに、`pyproject.toml`の依存関係に含まれるPython版Playwright（`uv run --frozen python`経由で使用可能）も利用できる。インストール済みのブラウザーバイナリは`~/.cache/ms-playwright`で確認できる。

## ホスト本体のバイナリの検索

ホスト本体バイナリの検索は、ホスト機能に関する公式ドキュメントや提供元のIssueトラッカーにおける既知の報告の確認を補足するための手段である。調査の結論や回避策を採用するかどうかは、ドキュメントやIssueの内容と合わせて総合的に判断する。判断基準は`agent-basics:writing-standards`の`references/investigation.md`および`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」に準拠する。

- バイナリの実体パスは、`B=$(readlink -f "$(command -v claude)")`や`C=$(readlink -f "$(command -v codex)")`を実行して解決する。特定のClaude Codeバージョンを対象にする場合は`~/.local/share/claude/versions/<バージョン>`を指定する。
- ヒット件数の確認には`timeout 60 rg -a -c -F -- '<検索文字列>' "$B"`を用い、前後の文脈確認には`timeout 60 rg -a -o -- '.{0,200}<検索文字列>.{0,200}' "$B"`を実行する。文脈検索の検索文字列は正規表現として適切にエスケープする。コマンドの出力結果はいったんmanaged-tempに保存してから参照する。タイムアウトなどの上限到達による中断と、該当なしの結果を明確に区別して判断する。
- 検索文字列の一致箇所は、ファイル先頭付近の文字列テーブルの断片と後方のJavaScriptソースコードの両方に現れる可能性がある。そのため、件数が少ない場合は`-m`で制限せずに全件を保存する。なお、正規表現の`.`は改行や不正なUTF-8シーケンスで一致が止まるため、取得された前後の文脈が指定した文字数より短くなることがある。
- 検索には`rg`を使用する。Claude CodeのBashツール内の`grep`は組み込みのugrepに置き換わっており、ugrepおよびGNU grepのいずれを用いても`.{0,N}`による文脈取得が30秒以内に完了しなかったためである。この観測結果と再検証の手順は`docs/development/audit-records.md`「dotfiles-development：ホスト本体のバイナリの検索：2026年10月4日」に記録されている。

## 振り返りの参照文書

`agent-toolkit:session-review`の実行時にメインエージェントが参照する本リポジトリ固有の文書には、所要時間の目標やリポジトリ固有の振り返り観点が記載されている。ファイル配置は、Claude Code環境では`~/.claude/docs/session-review-dotfiles.md`、Codex環境では`~/.codex/docs/session-review-dotfiles.md`である。配布元の原本ファイルは`.chezmoi-source/dot_claude/docs/session-review-dotfiles.md`に置かれている。

## テスト配置

- テストコードは、検証対象となる実装やファイルの可能な限り近くに配置する。Pythonモジュールの単体テストは同一ディレクトリ内の`<name>_test.py`とし、文書・設定ファイル・スクリプトの実物を読み込んで検証するテストは、そのファイルと同じディレクトリか、対象ファイルを包括する最も近い上位ディレクトリに置く。fixtureで入力を組み立てて実装を呼び出すテストも、実装の近くに配置する。やむを得ず離れた場所に置く場合は、配置可能な最も近い場所を選び、その理由をモジュールのdocstringに明記する
- 複数の領域にまたがる既存成果物の不変条件（インバリアント）を検証するテストは、`*_invariant_test.py`と命名する。リポジトリルートおよび`agent-toolkit/`の`pyproject.toml`にある`pytest-fast-targets`はこの命名規則に基づいて対象を選定し、`pyfltr fast`で実行されるため、通常の動作テストとは別ファイルに分ける。検証対象の探し方は`agent-toolkit:check-execution`の`references/verification-scope.md`に従い、エージェント向け文書側に対象ファイルの対応表をむやみに増やさない
- テストの共通ヘルパーモジュールは、`pytools/`配下では`pytools/_internal/_test_helpers.py`、リポジトリ直下では`_test_helpers.py`、`scripts/`配下では`scripts/_scripts_test_helpers.py`にそれぞれ配置する。リポジトリ直下と`scripts/`はどちらも`sys.path`に追加されるため、ヘルパーモジュール名が重複しないよう配慮する。なお、`agent-toolkit/`のテストコードは配布物の独立性を保つため、`pytools/_internal/`を参照しない構成にする。共通処理が必要な場合は、`agent-toolkit-edit`の`references/distribution-and-hooks.md`「agent_toolkitパッケージの配置と層」に記載されたテスト専用パッケージに配置する
- 各テストは`agent-toolkit/agent_toolkit/_testing/isolation.py`による環境隔離のもとで実行され、ホームディレクトリや設定ディレクトリはテストごとに用意される一時ディレクトリを指す。実際のGit操作を伴うテストでは、コミット履歴、タグ、ブランチ、作業ツリーの状態をテスト自身の一時リポジトリ内に構築し、すべてのGitコマンド呼び出しで対象リポジトリを明示的に指定する。この際、`_testing/git_repository.py`が提供する`init_repository`、`commit_all`、`run_git`を活用できる。実行元リポジトリ自体の不変条件を検証するテストが存在するため、テスト実行時のカレントディレクトリ（cwd）は常に実行元リポジトリのルートを維持する。再検証手順は`docs/development/audit-records.md`「dotfiles-development：Git動作テストの状態準備：2026年10月9日」を参照する
- 外部ランタイムやCLIを子プロセスとして起動するテストを新設・修正する場合は、同モジュールの`host_environ()`を用いてテスト起動に必要なホスト側の環境変数を構築し、子プロセスの`env`へ渡す。子プロセスに直接環境変数を渡せない実行形態の場合は、`restore_host_environment(monkeypatch)`を使ってテストプロセス自身のHOME・設定ディレクトリ・PATHを一時的に隔離前の状態に戻し、テスト対象の設定や状態は別の一時ディレクトリへ明示的に退避して隔離性を確保する。
  変更範囲を検証する際は、[CI設定](../../../.github/workflows/ci.yaml)の対象ジョブで使われるランチャーを経由してもテストを実行する。
  以下の専用CLIスクリプトは、uvによるPATHの変更後にmiseのshimを先頭へ配置し、目的のランタイムが正しく解決されることを確認したうえで、同一Pythonプロセスからpytestを起動する。runtime名と`--`以降のpytest引数を変更対象に合わせて指定し、修正前の失敗ケースと突き合わせて環境変数の設定漏れを検出できることを確認する。`-v -n 0 -p no:cacheprovider`はスクリプト側で自動付与され、pytestの終了コードがそのまま返される。miseの照会やshimの解決に失敗した場合は、診断メッセージを出力して終了コード2で停止する。

  ```sh
  uv run --frozen python scripts/run_mise_runtime_tests.py node -- agent-toolkit/agent_toolkit/_hooks/message_format_test.py
  ```

- パッケージをダウンロードして実行する外部ツール（pnpmの`dlx`やcorepackなど）を扱うテストでは、同モジュールの`share_package_caches`を利用して、取得したパッケージキャッシュの保存先のみをホスト環境と共有する
- `pytools`の配布パッケージには、`[tool.hatch.build.targets.wheel]`の`exclude`設定により`*_test.py`および`_test_helpers.py`を含めない
- `scripts/`配下のディレクトリは、pytestのprependモードによって自動的に`sys.path`に追加されるため、テストコードから直接importできる。import対象とするスクリプトはアンダースコア区切りで命名し、shebang（`#!/...`）を持つスクリプトには実行権限（`chmod +x`）を付与する
- 別ディレクトリにあるスクリプトを、テストコードから単独のモジュール名（裸の名前）で直接importすることは避ける。pytestのprependモードやpylintは、同一の実行に含まれるファイルのディレクトリのみを`sys.path`に追加するため、特定のテストや単体ファイルのみを指定して実行した場合にモジュール解決エラーが発生する。登録済みスクリプトを実行する場合は`agent_toolkit._atk.run_script.dispatch`を経由して呼び出すか、テストコード内で`sys.path`に対象ディレクトリを明示的に追加する。この規約違反は`test_import_resolution_invariant_test.py`によって自動検知される

## コミットメッセージ方針と.gitmessage

`<plugin root>/skills/commit/references/message.md`に記載されているコミットメッセージ方針と、`.gitmessage`テンプレートは、それぞれ配布される対象範囲が異なる。そのため両者の内容は意図的に重複させて管理しており、単一の情報源（SSOT）への一本化は行わない。
