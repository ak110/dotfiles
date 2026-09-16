---
name: dotfiles-development
description: >
  dotfilesリポジトリで`make update`・`make test`・`make format`・`make setup-browser`・`make test-browser`を
  実行するとき、pyfltr・MCPの`run`・`pytest`の直接実行を選ぶとき、
  mise trustを要する作業ツリーと状態ディレクトリを扱うとき、commit typeを判定するとき、
  `agent-toolkit:session-review`の参照文書の位置を確認するときに起動する。
---

# dotfilesの開発手順

本スキルは、本リポジトリの検査、整形、依存更新及び振り返りの参照文書の位置を提供する。
リリース運用は`dotfiles-release`、配布元と配布先の対応は`dotfiles-repo-layout`が扱う。

## 開発手順

- `make update`: 依存更新 + prek autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- ローカルで全体検査が必要な場合の実行方法: `make test`
  - `make test`（`uv run --frozen pyfltr run --no-fix`）はlintで自動修正しない。
    ただしpyfltrのformatter段（`ruff-format`・`uv-sort`・`shfmt`・`prek`・`sync-generated-files`）は
    `--no-fix`を付けても対象ファイルを書き換え、書き換えた場合も終了コード0で成功扱いになる。
    書き換えの対象は、整形結果が現在の内容と異なるファイル、`prek`が`.pre-commit-config.yaml`の
    テキスト整形hookで扱うファイル、及び生成物の同期先である。
    コミット範囲を確定する前に`git status`で自分の変更以外の差分の有無を確認する。
    自動修正が必要な場合は`make format`（`uv run --frozen pyfltr fast`）を使う
  - 特定ファイルに限定する場合はMCP経由の`run`へ当該ファイルのパスを渡す。
    MCPを利用できない場合は`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を使う。
    デバッガ・最小再現・環境切り分けでは`pytest`を直接実行してよい。
    `-o`と`-p`は`pytest`のオプションであり、`uv run --frozen pyfltr run`へ渡すと対象パスごと未認識の引数として終了コード2で終わる。
    `pytest`へ`-o addopts=''`を渡して既定オプションを解除する場合は、`-p no:cacheprovider`を併記する
  - 修正後の再実行時は、MCPでは`commands`へ`["mypy", "ruff-check"]`等を渡して限定する。
    CLIフォールバックでは`--commands=mypy,ruff-check`を使う（最終検証はCIに委ねる前提）
  - pyfltrの実行時間を比較する場合は、実行後に`uv run --frozen pyfltr list-runs`でrun一覧を取得し、対象runの識別子を確認してから
    `uv run --frozen pyfltr show-run <run_id>`で変更前後の所要時間を参照する。run識別子を記憶や短縮形から組み立てない
  - 検証は変更ファイルに対応する近接検査を先に実行する。公開前の全体検査はCIへ委ね、ローカルでは次の2件を実行する。CIの成功を確認して全体検査の結論を確定する
    - CIが実行しない検査: `uv run --frozen pyfltr run --commands=claude-plugin-validate`
    - 複数の書込主体の成果を統合した後にだけ成立する検査: `uv run --frozen pyfltr run --commands=arid`。レーンをまたぐ重複実装は個々のレーンの近接検査では検出できないため、全体検査をCIへ委ねる判定が成立する場合も、各レーンの統合直後に1回、および公開工程のpush前に1回実行する
  - ユーザーが局所変更の即時公開と、次回の`agent-toolkit:process-wi`での正式対応の両方を同じ指示で明示した場合だけ、即時公開では近接検査の成功と正式対応AWIの登録を条件として、全体検査とCI成功の待機を省略できる。push後はCIの起動とrun URLを確認し、省略した検査、未確定のCI、run URL及び正式対応AWIを報告する。次回の正式対応では通常どおり全体検査とCI成功を確認し、即時公開済みであることを検査の代替にしない
  - 複製元と異なる絶対パスで`mise.toml`を解決する作業場所と、既定と異なる状態ディレクトリでmiseを起動する作業場所は、当該作業場所を作成した主体が検査の起動前に`mise trust`を完了させる。miseの信頼登録は設定ファイルの絶対パスへ紐づき、状態ディレクトリ配下の`trusted-configs`に保持されるため、複製元の登録は別パスの複製と別の状態ディレクトリへ及ばない
    - linked worktreeでは複製元リポジトリルートの`mise.toml`へ`mise trust`を1回実行する。miseは複製元の信頼をlinked worktreeへ共有するため、worktreeごとの登録はしない
    - 検証用の複製では、複製先の`mise.toml`の絶対パスを指定して`mise trust`を実行する
    - `XDG_STATE_HOME`などで状態ディレクトリを差し替えた隔離環境では、検査へ与えるのと同じ環境変数を与えて`mise trust`を実行する
    - `MISE_TRUSTED_CONFIG_PATHS`は既存の信頼登録を置換して複製元を未信頼にするため使わない
- 新規Linux環境では、実ブラウザーテストに必要なChromiumとシステム依存を`make setup-browser`で一度導入する。
  OSパッケージの導入には権限が必要となる場合がある
- `atk serve`のブラウザーUI、ブラウザーから到達するサーバー処理、静的資産、
  実ブラウザーテストを変更した場合は`make test-browser`を実行する
- コミットメッセージtypeの判定例: [commit-types.md](../../../docs/development/commit-types.md)

## 振り返りの参照文書

`agent-toolkit:session-review`が読む本リポジトリ固有の参照文書は、Claude Codeでは`~/.claude/references/session-review-dotfiles.md`とする。
Codexでは`~/.codex/references/session-review-dotfiles.md`とする。
同文書はセッションの所要時間目標と本リポジトリ固有の振り返り観点を保持する。
配布元は`.chezmoi-source/dot_claude/references/session-review-dotfiles.md`である。
