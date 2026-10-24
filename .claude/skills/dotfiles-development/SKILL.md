---
name: dotfiles-development
user-invocable: false
description: >
  dotfilesリポジトリで`make update`・`make test`・`make format`・`make setup-browser`・`make setup-pwsh`・`make test-browser`を
  実行するとき、pyfltr・MCPの`run`・`pytest`の直接実行を選ぶとき、
  専用worktreeや回収予定の検証用複製の環境を準備するとき、mise trustを要する作業ツリーと状態ディレクトリを扱うとき、専用worktreeの変更を`atk`で動かすとき、
  画面を実描画で確かめるとき、本リポジトリのテストの配置を決めるとき、テストを新規作成・編集するとき、
  commit typeを判定するとき、コミットメッセージ方針と`.gitmessage`を編集するとき、
  Claude Code本体やCodex本体のバイナリを検索してホスト機能の挙動を確かめるとき、
  `agent-toolkit:session-review`の参照文書の位置を確認するときに起動する。
---

# dotfilesの開発手順

本スキルは本リポジトリの自動チェック、コード整形、依存更新、テスト配置、コミットメッセージ方針、ホスト本体のバイナリの検索手順および振り返りの参照文書の位置を提供する。
リリース運用は`dotfiles-release`、配布元と配布先の対応は`dotfiles-repo-layout`が扱う。

本リポジトリで`agent-toolkit:process-wi`を起動したセッションは、振り返りと完了報告を含む全工程で3時間以内を目標にする。
計測範囲は[セッションの所要時間目標](../../../.chezmoi-source/dot_claude/docs/session-review-dotfiles.md#セッションの所要時間目標)に従い、進行中の見直しには`agent-basics:writing-standards`の`references/quality-principles.md`「QCDと3段判定」を適用する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

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
| 変更範囲の検証の対象を選ぶ前、push前の全体検証を行う前、ローカルの`make test`が実行しないチェックを確かめる時、または`agent-doc-tone`の報告へ対処する前 | [references/verification-values.md](references/verification-values.md) | 判断 | `read:*/skills/dotfiles-development/references/verification-values.md` | |
| コミットメッセージのtypeを判定する時 | [commit-types.md](../../../docs/development/commit-types.md)（判定例） | 判断 | `read:*/docs/development/commit-types.md` | |
| 不変条件のテストの検証対象を探索する時 | `agent-toolkit:check-execution`の`references/verification-scope.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/references/verification-scope.md` | |

## 開発手順

### 作業ツリーの準備

- 専用worktreeや回収予定の検証用複製は、mise trustを済ませた後に依存を同期し、成功を確かめてから渡す。同期はその作業ツリーのrootで`make sync`を実行し、リポジトリ直下と`agent-toolkit/`の`.venv`を`uv.lock`のパッケージ集合と`.python-version`の版へそろえる
  - 回収予定の検証用複製は、`atk managed-temp create`の作業用領域へ`git worktree add --detach`で作成し、`make sync`で環境をそろえる。作業ツリーをファイル複製すると、入れ子の`.claude/worktrees/`と`.venv`を実容量で複製する。使用後は`git worktree remove`の後に`atk managed-temp cleanup`で回収する
  - 回収予定の作業場所ではローカル依存の同期だけを行い、Git hookとtemplateは既存設定を使う。共有CLIの導入元と共通Git設定は回収後も存続する必要があるためである。`mise run bootstrap`・`mise bootstrap`・`make setup`、`uv tool install --editable`、`prek install`、`git config --local commit.template`は恒久作業ツリーの初期導入に限る。観測と再検証手段は`docs/development/audit-records.md`「dotfiles-development：回収予定の作業場所の環境準備：2026年10月1日」にある
- miseの信頼登録は設定ファイルの絶対パスへ紐づき、状態ディレクトリ配下に保持される。そのため複製元と異なる絶対パスで`mise.toml`を解決する作業場所や、`XDG_STATE_HOME`などで状態ディレクトリを差し替える作業場所は、作成したエージェントが検証の起動前に`mise trust`を済ませる
  - linked worktreeは複製元の信頼を共有するため、複製元リポジトリルートの`mise.toml`へ1回実行すれば十分である。検証用の複製は複製先の`mise.toml`の絶対パスを指定し、状態ディレクトリを差し替えた環境は自動チェックと同じ環境変数を与えて実行する
  - `MISE_TRUSTED_CONFIG_PATHS`は既存の信頼登録を置換して複製元を未信頼にするため使わない
- 専用worktreeの変更を`atk`で動かす場合（`atk serve`を含む）は起動するファイルごとに版の選択を確認する。
  - PATH上の`atk`が複製元の`bin/atk`を指す場合は、cwdが同じリポジトリのworktree配下ならそのworktreeの`agent-toolkit/scripts/atk`へ委譲し、Git外や別リポジトリなら複製元の版を動かす（`uv run atk`も同じ選択）。
  - 保持配布物の`scripts/atk`はその配布物の固定rootで起動する。
  - 検証・探索の委譲先へは`<対象worktreeの絶対パス>/agent-toolkit/scripts/atk`を渡し、入れ子のコマンドにも同じ絶対パスを指定する。
  - scripts/launch.pyが配布rootと依存宣言・lockfile・版数から専用環境を選び、書き込み可能なデータ領域へ置く。
  - 指定したCLIの`info`で、実行するrootが検証対象と一致することを確かめる。

### 自動チェックと依存更新

- `make update`は読込表のtargetの実処理の更新対象に変更対象が含まれる場合だけ候補にする。現行の対象は`uv.lock`と`agent-toolkit/uv.lock`の依存更新である。lock更新後にcontainer依存導入を検証する（`ci-deps-check`）。さらにprek autoupdate、mise lock、pinactアクション更新と全テストを実行する。`rust/claude-statusline/Cargo.lock`は対象外である。GitHub Actionsのハッシュピンだけなら`make update-actions`
- 全体検証は`make test`（`uv run --frozen pyfltr run --no-fix`）。`agent-toolkit:check-execution`を起動し、標準出力と標準エラーを保存して検収できる手段（例: `agents_server`の`start`の`shell`）で実行する
  - pyfltrのformatter段（`ruff-format`・`uv-sort`・`shfmt`・`prek`・`sync-generated-files`）は`--no-fix`でもファイルを書き換え、終了コード0で終わる。コミット範囲を確定する前に`git status`で自分以外の差分を確かめる。自動修正は`make format`（`uv run --frozen pyfltr fast`）で行う

pyfltrを調査・計測用途で実行する場合は`--no-fix`で自動修正を抑止する。formatter段は`--no-fix`でも書き換えるため、読取専用の用途では書き換える段を実行しない。

- 特定ファイルに限定する検証は、MCPの`run`へパスを渡すか、`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を使う。初回は`commands`（CLIは`--commands`）を指定せず、修正後の再実行だけ失敗したチェックへ限定する
  - pytestを選ぶ実行は、渡したファイルによらず`pytest-always-targets`の横断テストをrootと`agent-toolkit/`の両方で加える（`references/verification-values.md`の横断テストの項）。同じ内容への2回目以降の静的チェックは`commands`でpytestを除き、失敗したテストだけの再確認は`--only-failed`を使う。単一テストの局所確認は`pytest`を対象ファイル（必要なら`-k`）で直接実行し、出力の収集件数が意図した集合と一致することを確かめる
- デバッガ・最小再現・環境切り分けでは`pytest`を直接実行してよい。`-o`と`-p`は`pytest`のオプションで、`pyfltr run`へ渡すと終了コード2で終わる。`-o addopts=''`で`addopts`を解除する場合は`-p no:cacheprovider`を併記する
- `.venv`を再作成し得る自動チェック（`uv run --python`による版切替、依存更新など）は、同じ仮想環境パスでは直列に実行する。並列にする場合は自動チェックごとに別の仮想環境パスを明示する
- pyfltrの所要時間を比べる場合は、`uv run --frozen pyfltr list-runs`で取得したrun識別子を`uv run --frozen pyfltr show-run <run_id>`へ渡す

### 画面とブラウザー

- `make setup-browser`（Chromiumとシステム依存）と`make setup-pwsh`（Ubuntu/DebianのPowerShell）は、ユーザーが自分の端末で実行する初期導入である。エージェントが`make test-browser`の前提不足を検出した場合は、システム依存を導入せず、不足する前提と未実施の検証を報告する
- `atk serve`のブラウザーUI、ブラウザーから到達するサーバー処理、静的資産、実ブラウザーテストを変更した場合は`make test-browser`を実行する
- レビュー用に一時データで`atk serve`を起動する場合は、保存先を`AGENT_TOOLKIT_PRIVATE_NOTES=<一時領域の絶対パス>`で指定する。起動前に、起動に使うのと同じCLI・同じ環境変数で`atk config get private_notes`を実行し、結果が一時領域と一致したときだけ起動する。不一致か解決の失敗では起動せず、保存先の指定を直してから取得結果と一時領域が一致するかを再確認する（未対応の変数名で指定すると通常の保存先、つまり実データへ接続する）
- 画面の実描画には、ブラウザー操作ツールのほか、`pyproject.toml`が依存に持つPython版Playwright（`uv run --frozen python`から使う）を使える。導入済みのブラウザーは`~/.cache/ms-playwright`で確かめる

## ホスト本体のバイナリの検索

本体の検索は、ホスト機能の公式ドキュメントと提供元の公開Issueトラッカーにある既知の報告の確認を補う手段である。結論と回避策の採否はそれらと合わせて決める。基準は`agent-basics:writing-standards`の`references/investigation.md`と`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」にある。

- 実体は`B=$(readlink -f "$(command -v claude)")`、`C=$(readlink -f "$(command -v codex)")`で解決する。特定のClaude Code版は`~/.local/share/claude/versions/<版>`を指定する
- 件数は`timeout 60 rg -a -c -F -- '<語>' "$B"`、文脈は`timeout 60 rg -a -o -- '.{0,200}<語>.{0,200}' "$B"`で得る（文脈の語は正規表現としてエスケープする）。出力はmanaged-tempへ保存してから読む。上限による打切りを該当なしと区別する
- 一致はファイル先頭側の文字列表の断片と後方のJSソースの両方に現れるため、件数が少なければ`-m`を付けず全件を保存する。`.`は改行と不正なUTF-8で止まるため、文脈が指定長より短いことがある
- `rg`を使う。Claude CodeのBashツールの`grep`は組込みugrepへ置き換わり、ugrepとGNU grepのどちらも`.{0,N}`の文脈取得が30秒で終わらなかった。観測と再検証手段は`docs/development/audit-records.md`「dotfiles-development：ホスト本体のバイナリの検索：2026年10月4日」にある

## 振り返りの参照文書

`agent-toolkit:session-review`でメインが読む本リポジトリ固有の参照文書は、所要時間目標と固有の振り返り観点を持つ。Claude Codeでは`~/.claude/docs/session-review-dotfiles.md`、Codexでは`~/.codex/docs/session-review-dotfiles.md`である。配布元は`.chezmoi-source/dot_claude/docs/session-review-dotfiles.md`である。

## テスト配置

- テストは確かめる対象のできるだけ近くへ置く。Pythonモジュールの動作テストは同じディレクトリの`<name>_test.py`、文書・設定・スクリプトの実物を読むテストはその実物と同じディレクトリか対象群を包含する最も近いディレクトリへ置く。fixtureで入力を作成して実装を呼ぶテストは実装の近くへ置く。近くへ置けない場合は成立する最も近い場所を選び、理由をモジュールdocstringへ記す
- 複数領域の既存成果物の不変条件を確かめるテストは`*_invariant_test.py`と名付ける。rootと`agent-toolkit/`の`pyproject.toml`の`pytest-fast-targets`がこの名前で選び、`pyfltr fast`が実行するため、通常の動作テストは別ファイルに分ける。検証対象の探索は`agent-toolkit:check-execution`の`references/verification-scope.md`に従い、テストと編集対象の対応表を規範へ増やさない
- テスト共通ヘルパーは`pytools/`では`pytools/_internal/_test_helpers.py`、リポジトリ直下では`_test_helpers.py`、`scripts/`では`scripts/_scripts_test_helpers.py`に置く。直下と`scripts/`はともに`sys.path`へ入るため、補助モジュールには互いに異なる名前を付ける。`agent-toolkit/`のテストは配布物の独立性のため`pytools/_internal/`への参照を避ける。共通化は`agent-toolkit-edit`の`references/distribution-and-hooks.md`「agent_toolkitパッケージの配置と層」のテスト専用パッケージへ置く
- テストは`agent-toolkit/agent_toolkit/_testing/isolation.py`の隔離の下で動き、ホームと設定ディレクトリはテストごとの一時ディレクトリを指す。実Gitの動作を確かめるテストは、履歴・tag・branch・作業状態を自身の一時リポジトリへ準備し、全てのGit呼び出しで対象リポジトリを明示する。`_testing/git_repository.py`の`init_repository`・`commit_all`・`run_git`を使える。実行元リポジトリの不変条件を調べるテストのため、全テストのcwdは実行元のまま保つ。再検証手段は`docs/development/audit-records.md`「dotfiles-development：Git動作テストの状態準備：2026年10月9日」にある
- 実際の外部ランタイムやCLIを子プロセスで起動するテストを新設・変更するときは、同モジュールの`host_environ()`で起動に必要なホスト設定を組み立てて子の`env`へ渡す。子へ環境を渡せない起動方法では`restore_host_environment(monkeypatch)`でテストプロセス自身のHOME・設定ディレクトリ・PATHを隔離前へ戻し、テスト対象の設定と状態は別の一時領域へ明示して隔離を保つ。
  変更範囲の検証では、[CI設定](../../../.github/workflows/ci.yaml)の対象jobの実行環境が使うランチャーからも対象テストを実行する。
  次の専用CLIはuvによるPATH変更の後にmiseのshimを先頭へ置き、対象ランタイムの解決先を確かめて同じPythonプロセスでpytestを起動する。runtime名と`--`以降のpytest引数を対象へ替え、修正前の失敗対照と対応付けて環境指定の不足を検出できることを確かめる。`-v -n 0 -p no:cacheprovider`はCLIが付け、pytestの終了コードをそのまま返す。miseの照会・shimの解決に失敗した場合は診断付きの終了2で止まる。

  ```sh
  uv run --frozen python scripts/run_mise_runtime_tests.py node -- agent-toolkit/agent_toolkit/_hooks/message_format_test.py
  ```

- パッケージを取得して起動する外部ツール（pnpmの`dlx`、corepackなど）を動かすテストは、同モジュールの`share_package_caches`で取得物の保存先だけをホストと共有する
- `pytools`の配布物には`[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を含めない
- `scripts/`配下はpytestのprependモードで`sys.path`へ入るためテストから直接importできる。importするスクリプトはアンダースコア区切りで命名し、shebang付きスクリプトは`chmod +x`する

## コミットメッセージ方針と.gitmessage

`<plugin root>/skills/commit/references/message.md`のコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させ、SSOT化の対象から外す。
