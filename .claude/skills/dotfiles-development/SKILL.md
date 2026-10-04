---
name: dotfiles-development
user-invocable: false
description: >
  dotfilesリポジトリで`make update`・`make test`・`make format`・`make setup-browser`・`make setup-pwsh`・`make test-browser`を
  実行するとき、pyfltr・MCPの`run`・`pytest`の直接実行を選ぶとき、
  専用worktreeや回収予定の検証用複製の環境を準備するとき、mise trustを要する作業ツリーと状態ディレクトリを扱うとき、専用worktreeの変更を`atk`で動かすとき、
  画面を実描画で確かめるとき、commit typeを判定するとき、
  Claude Code本体やCodex本体のバイナリを検索してホスト機能の挙動を確かめるとき、
  `agent-toolkit:session-review`の参照文書の位置を確認するときに起動する。
---

# dotfilesの開発手順

本スキルは本リポジトリの自動チェック、コード整形、依存更新、ホスト本体のバイナリの検索手順および振り返りの参照文書の位置を提供する。
リリース運用は`dotfiles-release`、配布元と配布先の対応は`dotfiles-repo-layout`が扱う。

## 開発手順

- 専用worktreeや回収予定の検証用複製を準備する主体は、実行前に本スキルを起動する。後掲のmise trust手順を適用した後、準備する作業ツリーのrootで`env --unset=UV_FROZEN uv sync --locked --all-groups --all-extras`を実行し、その作業ツリーの`.venv`へ依存を同期する。終了コードと全出力を検収し、失敗した環境は準備完了として渡さない。
  - `mise run bootstrap`、`mise bootstrap`、`make setup`は恒久作業ツリーの初期導入に使う。回収予定の作業場所ではローカル依存の同期だけを行い、`uv tool install --editable`、`prek install`、`git config --local commit.template`を準備へ含めない。共有CLIの導入元と共通Git設定が、その作業場所の回収後も存続する必要があるためである。Git hookとtemplateは既存設定を使う。
  - ローカル依存の同期と共有登録の比較の観測・再検証手段は、`docs/development/audit-records.md`「dotfiles-development：回収予定の作業場所の環境準備：2026年10月1日」にある。
- `make update`: 実行前に現行`Makefile`の`update` targetと呼び出す子targetを読み、変更対象が実処理の更新対象に含まれる場合だけ候補にする。対象ファイル名や更新時刻は候補判定の入力から外す。現行の対象は依存更新（リポジトリ直下の`uv.lock`と`agent-toolkit/uv.lock`）、prek autoupdate、mise lock、pinactアクション更新および全テスト実行であり、`rust/claude-statusline/Cargo.lock`は対象外とする
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- ローカルで全体検証が必要な場合の実行方法: `make test`
  - 全体検証を始める際は`agent-toolkit:check-execution`を起動し、標準出力と標準エラーを保存して検収できる手段（例: `agents_server`の`start`の`shell`へ`make test`を渡す）で実行する。出力の保存先を確保してから実行し、保存済みの標準出力と標準エラーで検収する
  - `make test`（`uv run --frozen pyfltr run --no-fix`）はlintで自動修正しない。
    ただしpyfltrのformatter段（`ruff-format`・`uv-sort`・`shfmt`・`prek`・`sync-generated-files`）は
    `--no-fix`を付けても対象ファイルを書き換え、書き換えた場合も終了コード0で成功扱いになる。
    書き換えの対象は、整形結果が現在の内容と異なるファイル、`prek`が`.pre-commit-config.yaml`の
    テキスト整形hookで扱うファイル、および生成物の同期先である。
    コミット範囲を確定する前に`git status`で自分の変更以外の差分の有無を確認する。
    自動修正が必要な場合は`make format`（`uv run --frozen pyfltr fast`）を使う
  - 特定ファイルに限定する場合はMCP経由の`run`へそのファイルのパスを渡す。
    MCPを利用できない場合は`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を使う。
    初回の変更範囲の検証ではMCPの`commands`とCLIの`--commands`を指定しない。
    修正後に失敗したチェックだけを再実行する場合は、MCPでは`commands`へ`["mypy", "ruff-check"]`等を、CLIでは`--commands=mypy,ruff-check`を渡す
  - 変更範囲の検証の対象は`agent-toolkit:check-execution`の`references/verification-scope.md`の類型で選ぶ。本リポジトリで使う値は次のとおり
    - 横断テスト: ファイル名`*_invariant_test.py`で識別する。rootと`agent-toolkit/`の`pyproject.toml`の`pytest-fast-targets`がこのファイル名で対象を選び、commit時にprekが起動する`pyfltr fast`で自動実行する。通常のcommitではこの自動実行の結果を使う。配置は`pytools-edit`「テスト配置」に従う。fastの前提と再検証の手段は`docs/development/audit-records.md`「dotfiles-development：不変条件テストのfast自動実行：2026年10月4日」が持つ
    - 期待値を保持するテスト: `agent-toolkit/agent_toolkit/_hooks/`のエンドユーザー向け通知文言は、変更した挙動に対応するhook固有の`<hook名>_test.py`が期待値を持つ
    - 共有契約を変えた場合の検証単位全体: `uv run --frozen pytest -p no:cacheprovider agent-toolkit/agent_toolkit`。対象の変更は、`agent-toolkit/agent_toolkit/_common/`配下、`_hooks/`の通知生成元の`source`・`kind`、`atk.py`のサブコマンド登録、または複数の`atk`サブコマンドが共有する処理・出力の契約の変更である。
      共有する処理・出力の契約は、変更前か変更後に異なる2つ以上のサブコマンドから実際に呼ばれる処理の挙動と、共通出力の内容・書式・条件・有無を指す。内部のコメント・空白だけの変更は含めない
    - パッケージ外の呼び出し元: `agent-toolkit/`の外で`agent_toolkit`をimportする場所は`pytools/`と`scripts/`である。`agent-toolkit/agent_toolkit/`配下の`*_test.py`以外のPythonファイルを変更した場合は`uv run --frozen pytest -p no:cacheprovider pytools scripts`
    - 名前の削除・改名の全体静的検査: `uv run --frozen pyfltr run --commands=ty`。対象ファイルを渡さず`agent-toolkit/`を含むリポジトリ全体を対象にし、Pythonファイルを変更するレーンでは計画の`変更範囲の検証`行へ含める
    - 統合後の検証: fast-forwardの前に専用worktreeで、共有契約とパッケージ外の呼び出し元のpytest、`uv run --frozen pyfltr fast --commands=pytest`と`ty`を1回実行する。`uv run --frozen pyfltr run --commands=arid`も同じ時点で実行する。rebase後の組合せはcommit時には確かめられないため、同じfastの対象選択を使う
  - デバッガ・最小再現・環境切り分けでは`pytest`を直接実行してよい。
    `-o`と`-p`は`pytest`のオプションであり、`uv run --frozen pyfltr run`へ渡すと対象パスごと未認識の引数として終了コード2で終わる。
    `pytest`へ`-o addopts=''`を渡して既定オプションを解除する場合は、`-p no:cacheprovider`を併記する
  - 同じ作業ツリーで`uv run --python`によるPython版切替、依存更新またはその他の`.venv`再作成を起こし得る自動チェックは、同じ仮想環境パスへの並列実行を避ける。Python 3.13と3.14を同じ`.venv`で自動チェックする場合は直列に実行する。並列実行する場合は自動チェックごとに異なる仮想環境パスを明示する
  - pyfltrの実行時間を比較する場合は、実行後に`uv run --frozen pyfltr list-runs`でrun一覧を取得し、対象runの識別子を確認してから
    `uv run --frozen pyfltr show-run <run_id>`で変更前後の所要時間を参照する。run識別子を記憶や短縮形から組み立てない
  - 公開前の全体検証は`agent-toolkit:commit`の`references/publish.md`「検証とCI」に従う。本リポジトリでpush前に実行するCI非実行のチェックと全体走査のチェックは次の3件である
    - CIのpyfltr実行が無効化するチェック: `uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version --enable=statusline-version`
    - レーンをまたぐ重複実装の検出: `uv run --frozen pyfltr run --commands=arid`
    - 変更ファイルの外に残ったPythonの静的参照の検出: `uv run --frozen pyfltr run --commands=ty`
  - `make test`が実行するツール集合とCIの`python-lint (3.14)`ジョブの差は、同ジョブが`pyfltr ci --disable=`で無効化するチェックであり、対象は`.github/workflows/ci.yaml`の同ジョブの定義が定める。
    次の自動チェックはローカルの`make test`では実行されず、それぞれのジョブやコマンドで実行する
    - `test-windows`ジョブ: Windows実機でのchezmoi適用と、Windows固有のテスト
    - `test-linux`ジョブ: `install.sh`とchezmoiの実適用
    - `python-lint (3.13)`ジョブ: Python 3.13でのpytest
    - `rust-lint`ジョブ: `rust/claude-statusline/`のcargo検証
    - `browser-e2e`ジョブの実ブラウザーテスト: ローカルでは`make test-browser`で実行する
  - 複製元と異なる絶対パスで`mise.toml`を解決する作業場所と、既定と異なる状態ディレクトリでmiseを起動する作業場所は、その作業場所を作成した主体が検証の起動前に`mise trust`を完了させる。miseの信頼登録は設定ファイルの絶対パスへ紐づき、状態ディレクトリ配下の`trusted-configs`に保持されるため、複製元の登録は別パスの複製と別の状態ディレクトリへ及ばない
    - linked worktreeでは複製元リポジトリルートの`mise.toml`へ`mise trust`を1回実行する。miseは複製元の信頼をlinked worktreeへ共有するため、worktreeごとの登録はしない
    - 検証用の複製では、複製先の`mise.toml`の絶対パスを指定して`mise trust`を実行する
    - `XDG_STATE_HOME`などで状態ディレクトリを差し替えた隔離環境では、自動チェックへ与えるのと同じ環境変数を与えて`mise trust`を実行する
    - `MISE_TRUSTED_CONFIG_PATHS`は既存の信頼登録を置換して複製元を未信頼にするため使わない
  - `make test`はlinter`agent-doc-tone`を含む。
    単独では`uv run --frozen pyfltr run --commands=agent-doc-tone`で起動する。
    対象はエージェントが実行時に読むMarkdown（`AGENTS.md`・`agent-toolkit/`のrules・skills・share・
    `.chezmoi-source/dot_claude/`・`.claude/skills/`）とする。
    文体の密度を測り、閾値を超えたファイルを指標付きで報告する。
    測る指標と閾値は`scripts/check_agent_doc_tone.py`のdocstringが定める。
    `agent-toolkit/`の説明文はMarkdownの本文・見出し・表に加え、コードのコメント・docstring・表示文・注入文へ指定された語が戻った場合に、ファイルと行を示して非0で終える。
    引用、意図的な悪い例、検出用データと保存形式の名称は説明文と区別し、良い例と通常の説明は判定する。
    報告されたファイルは`uv run --frozen python scripts/check_agent_doc_tone.py --report <ファイルのパス>`で
    指標を確かめ、否定形の宣言と法令調の指示語を肯定形と平易な語へ書き換えて密度を下げる。
    語の再使用は`agent-toolkit:writing-standards`の`references/textlint-violations.md`に従って文全体を書き直す
- 新規Linux環境では、ユーザーが自分の端末から`make setup-browser`でChromiumとシステム依存を初期導入する。
  Ubuntu/DebianでPowerShell検証が必要な場合も、ユーザーが自分の端末から`make setup-pwsh`で初期導入する
- エージェントが`make test-browser`の前提不足を検出した場合は、システム依存を導入せず、不足する前提と未実施の検証を報告する
- `atk serve`のブラウザーUI、ブラウザーから到達するサーバー処理、静的資産、
  実ブラウザーテストを変更した場合は`make test-browser`を実行する
- 専用worktreeの変更を`atk`で動かす場合（`atk serve`で画面を確かめる場合を含む）は、起動時のcwdで動く版が決まる。
  PATH上の`atk`は複製元の`bin/atk`であり、cwdが同じリポジトリのworktree（その配下のディレクトリを含む）にあれば、
  そのworktreeの`agent-toolkit/bin/atk`へ委譲する。
  `agent-toolkit`は`atk`のconsole scriptを持たないため、`uv run atk`もPATH上の`atk`へ解決され、同じ選択になる。
  cwdがGit外か別リポジトリにある場合は複製元の`agent-toolkit/bin/atk`が動くため、
  対象worktreeの外から改修版を動かすときは`<worktreeの絶対パス>/agent-toolkit/bin/atk`を絶対パスで起動する。
  `uv run`の中から起動すると、uvが`VIRTUAL_ENV=... does not match the project environment path <パス>/agent-toolkit/.venv`と警告する。
  この警告は動作を妨げず、`<パス>`（cwd配下なら相対パス）は実際に動く`agent-toolkit`の位置を示す。
  改修版を動かすつもりで複製元の絶対パスが表示された場合は、cwdか起動パスを直して起動し直す
- 画面の実描画には、ブラウザー操作ツールに加えて、リポジトリ直下の`pyproject.toml`が依存に持つPython版Playwright（`uv run --frozen python`から`playwright`を使うスクリプト）を使える。
  ブラウザー本体は`make setup-browser`が導入し、導入済みの版は`~/.cache/ms-playwright`で確かめる
- コミットメッセージtypeの判定例: [commit-types.md](../../../docs/development/commit-types.md)

## ホスト本体のバイナリの検索

Claude Code本体は`B=$(readlink -f "$(command -v claude)")`、Codex本体は
`C=$(readlink -f "$(command -v codex)")`で実体パスを解決する。
特定のClaude Code版を調べる場合は`~/.local/share/claude/versions/<版>`を指定する。

最初に`timeout 60 rg -a -c -F -- '<語>' "$B"`で一致行数を得る。
文脈は`timeout 60 rg -a -o -- '.{0,200}<語>.{0,200}' "$B"`を使い、標準出力と標準エラーを
managed-tempのファイルへ保存してから必要な範囲を読む。Codexには同じ形で`"$C"`を渡す。
文脈を得る式の語は正規表現として引用し、語に含まれる正規表現の記号をエスケープする。
両操作の終了コードと所要時間も保持し、上限による打切りを該当なしと判定しない。

`-m`は一致行数を限定する。ファイルの先頭側は文字列表の断片で、JSソースの一致は後ろにも現れる。
一致行数が少なければ`-m`を付けず全件を保存する。`.`は改行と不正なUTF-8バイトで止まるため、
文字列表の一致では文脈が指定長より短くなることがある。

この用途の標準は`rg`とする。Claude CodeのBashツールでは`grep`が組込みugrepを呼ぶシェル関数に
置き換わる。WI起草時の観測ではugrepとGNU grepの両方で、UTF-8ロケールの`.{0,N}`による
文脈取得が30秒で終わらなかった。固定長の`.\{N\}`は前後の文脈がN文字に満たない一致を除き、
終了まで約2秒の回と120秒を超える回があったため、件数・文脈の取得と上限を上記へそろえる。
観測と再検証手段は`docs/development/audit-records.md`の「dotfiles-development：ホスト本体のバイナリの検索：2026年10月4日」にある。

## 振り返りの参照文書

`agent-toolkit:session-review`の振り返りでメインが読む本リポジトリ固有の参照文書は、Claude Codeでは`~/.claude/docs/session-review-dotfiles.md`とする。
Codexでは`~/.codex/docs/session-review-dotfiles.md`とする。
同文書はセッションの所要時間目標と本リポジトリ固有の振り返り観点を保持する。
配布元は`.chezmoi-source/dot_claude/docs/session-review-dotfiles.md`である。
