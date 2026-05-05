# CLAUDE.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリ。
`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 開発手順

- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `uvx pyfltr run-for-agent`
  - ドキュメントなどのみの変更の場合は省略可（pre-commitで実行されるため）
  - テストコードの単体実行なども極力`uvx pyfltr run-for-agent <path>`を使う（pytestを直接呼び出さない）
  - 修正後の再実行時は、対象ファイルや対象ツールを必要に応じて限定して実行する（最終検証はCIに委ねる前提）
    - 例: `pyfltr run-for-agent --commands=mypy,ruff-check path/to/file`

## アーキテクチャの参照先

リポジトリ全体の構成・配布対象と開発対象の区別・プラットフォーム対応ファイル一覧・bash補完運用・
PowerShellスクリプト注意事項・ホーム配下編集前の確認手順は[docs/development/architecture.md](docs/development/architecture.md)に集約している。

## 注意点

- `.claude`を含むディレクトリが3系統あり取り違えやすい（`.chezmoi-source/dot_claude/`/`~/.claude/`/`.claude/`）。
  指示の対象を必ず確認する。詳細は「固有差分」の「ディレクトリ構造の注意」参照
- chezmoi管理ソース（`.chezmoi-source/dot_claude/`配下）はパス上`dot_claude`命名だが、
  配布先`~/.claude/`配下のClaude Code設定系ファイルと同等として扱う。
  編集着手前に`agent-toolkit:writing-standards`スキルと、その`references/claude-common.md`を含む
  必読リファレンスを参照する
- `.chezmoi-source/`配下のファイルを削除した場合、chezmoiは配布先を自動削除しない。
  配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する（`chezmoi apply`後処理で削除される）
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）で書かれている。
  UTF-8前提のEdit/Writeツールでは文字化けや破損のリスクがあるため、ASCIIのみの修正は`sed -i`で対応する
- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する。
  CIチェックアウトや利用者環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが破綻するため
- シンプルなコマンドラッパーの新規追加には`scripts/new-bin-cmd.py <name> <command...>`を使う。
  リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成し、`development.md`のペア一覧も自動更新する
- `.chezmoi-source/dot_claude/rules/agent-toolkit/*.md`（配布ルール本体）を改訂する際、
  `docs/guide/claude-code-guide.md`に要約・ステップ数などが再掲されていることが多い。
  本体変更前に`grep`で参照箇所を確認する
- `.chezmoi-source/dot_claude/rules/agent-toolkit/agent.md`のコミットメッセージ方針と
  `.gitmessage`は配布範囲が異なるため意図的に重複させている。SSOT化しない
- spec-driven系スキル（`spec-driven`・`spec-driven-init`・`spec-driven-promote`）は本リポジトリでは対象外。
  `docs/features/`・`docs/topics/`の運用を採らないため、機能追加時も起動しない
- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュールを置く。
  privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する。
  エージェント・hook・自動化など手で起動しないスクリプトは`scripts/`配下へ置く
 （`[project.scripts]`登録は行わず、PEP 723形式の単独実行スクリプトとして書く）
- `pytools/`トップレベルの公開CLIモジュールは原則bash補完（argcomplete）に対応する。
  手順と例外は`docs/development/architecture.md`の「bash補完」節を参照する
- Pythonテストコードはソースモジュールの隣に同居方式で配置する。
  `pytools/`・`scripts/`・`agent-toolkit/`配下のいずれにおいても、
  ソース`<name>.py`に対して同一ディレクトリ内に`<name>_test.py`を置く。
  主目的はテスト対象モジュールとテストコードの対応関係を探しやすくすること。
  テスト共通ヘルパーは`pytools/_internal/_test_helpers.py`へ集約する。
  `pytools`パッケージ配布物にテストコードを含めないため、
  `[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を除外する。
  `scripts/`配下のスクリプト固有の補足:
  - pytestはprependモードで`scripts/`を`sys.path`へ自動追加するため、
    テスト側からスクリプトを直接importできる
  - importしたい場合はファイル名をハイフン区切りではなくアンダースコア区切り（`<name>.py`）で命名する
  - shebangを持つスクリプトは`chmod +x`で実行権限を付与する
   （pre-commitの`check-shebang-scripts-are-executable`が強制し、付与漏れではコミットが失敗する）
- `pytools/_internal/claude_common.py`は共通基盤モジュールとして
  `find_dotfiles_root()`・`run_subprocess()`・`atomic_write_text()`・`atomic_write_json()`・`load_json_dict()`を提供する。
  新規ヘルパーを書き起こす前に当モジュールの公開APIを確認し、重複定義を避ける

## Claude Codeフック実装の配置先

本リポジトリにはClaude CodeのPreToolUseフックを記述できる場所が2系統ある。
新しいチェックや自動許可ロジックを追加するときはどちらへ配置するか判断する。
迷ったら推測せず必ずユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py`（個人フック）
  - chezmoi経由で自分の`~/.claude/settings.json`にのみマージされる
  - dotfiles固有の運用前提に依存するチェックに向いている
   （例: `~/.claude/`がchezmoi配布先である前提、個人の命名規約・ディレクトリ構成など）
- `agent-toolkit/`（プラグイン）
  - `.claude-plugin/marketplace.json`経由で他人にも配布される
  - 汎用的な制約・自動化に向いている
   （例: 一般的な文字化け検出、一般的なPowerShell互換性チェック）

判断基準: 汎用的な機能はプラグイン、dotfiles固有の前提に依存する機能は個人フックへ配置する。
類似のチェックが既に片方に存在する場合はそちらへ統合する（SSOT原則）。

プラグインに配置した場合は`.claude/rules/agent-toolkit.md`の手順に従い`plugin.json`のバージョンを更新する。
個人フックに配置した場合は`share/claude_settings_json_managed.posix.json`および同`win32.json`の
`matcher`に新しいツール名を追加する必要があるか確認する。

agent-toolkit配下の編集時、dotfiles固有名の混入を`scripts/claude_hook_pretooluse.py`の専用チェックがブロックする。
対象範囲は`agent-toolkit/`および`.chezmoi-source/dot_claude/rules/agent-toolkit/`配下。
ブロック対象の個人プロジェクト名固定リストには`gv`・`lc`・`smpr`・`glatasks`等が含まれる。
スキル名・pytoolsコマンド名・scripts名はhook実行時に当該ディレクトリをスキャンして動的に取得するため、
新規追加時の手動更新は不要。
OSSとして紹介する想定がある`pyfltr`・`pytilpack`はwarning通知に留める。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）は`pytools/_internal/install_claude_plugins.py`経由で
agent-toolkitプラグインを自動インストール・更新する。
marketplace配布は2段階の構成を取る。

- bootstrap経路（GitHub型）: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply経路（directory型）: 後処理がdirectory型（dotfilesリポジトリの絶対パス直接参照）で維持する。
  GitHub型登録が残存する環境では自動でdirectory型へマイグレーションする

directory型を使う理由は、dotfilesで編集した内容がpush/updateサイクルを介さずに反映されること。

### ローカル編集の反映ワークフロー

`agent-toolkit/`配下を編集したときの典型的な反映手順（chezmoi管理下）:

1. `agent-toolkit/`配下のファイルを編集する
2. `chezmoi apply`（または`update-dotfiles`）を実行する
3. Claude Codeを再起動するか`/reload-plugins`を実行する

version bumpは不要。編集が即時反映される。

## 固有差分

### ロールとファイル群の対応

本リポジトリと配布物には以下の4ロールが関与する。
ファイル群を編集する際は、対象読者を意識して文面を選ぶ。

- dotfiles利用者: 本リポジトリ（chezmoiソース・`bin`・`pytools`等）を自分の環境にインストールして使う人
- agent-toolkit利用者: `agent-toolkit`プラグインをマーケットプレイス経由で使う人。
  dotfiles利用者を含むがそれ以外もいる
- 全プロジェクト編集者: dotfilesを含むあらゆるプロジェクトで編集作業をするコーディングエージェント。
  配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するコーディングエージェント。
  全プロジェクト編集者がロードするものに加え、リポジトリ直下の`.claude/`と`CLAUDE.md`もロードする

各ファイル群の対象読者と役割は以下の通り。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/agents/`配下 | 全プロジェクト編集者 | スキル・サブエージェントの指示本体 |
| `agent-toolkit/agents/`配下のfrontmatterコメント | dotfiles編集者 | 連携先や注意事項などの編集用メタ情報 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者 | `~/.claude/rules/agent-toolkit/`配下で常時自動ロードされる行動原則 |
| `docs/guide/claude-code-guide.md` | agent-toolkit利用者 | プラグインの導入・更新手順 |
| `.chezmoi-source/dot_claude/`配下 | dotfiles利用者 | 配布先`~/.claude/`相当のClaude Code設定 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `CLAUDE.md`（本ファイル） | dotfiles編集者 | 本リポジトリの修正方針・固有知見 |
| `pytools/`・`bin/`・`scripts/` | dotfiles利用者・dotfiles編集者 | コマンドラインツールと開発スクリプト |

`.chezmoi-source/dot_claude/`配下の変更は配布先`~/.claude/`を経由して、
dotfiles利用者が他リポジトリで作業する場面にも影響する。

### ディレクトリ構造の注意

本リポジトリには`.claude`を含むディレクトリが3系統あり、取り違えると影響範囲が全く異なる事故につながる。
指示があった際はどの階層を指すか必ず確認すること。

- `.chezmoi-source/dot_claude/` — 配布元。chezmoiが`~/.claude/`にデプロイする。
  ここを書き換えると`chezmoi apply`後に全環境へ反映される（グローバルユーザー設定の原本）
- `~/.claude/` — デプロイ先（個人ホーム）。`chezmoi apply`で上書きされるため直接編集してはならない。
  ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`である
- `.claude/`（本リポジトリルート）— dotfilesリポ自身のClaude Codeプロジェクト設定。
  配布対象外で、このリポジトリで作業するClaudeにしか影響しない

chezmoiはドットプレフィックスのディレクトリ（`.claude/`など）を自動無視するため`.chezmoi-source/dot_claude/`と衝突しない。

### chezmoiの命名規則（早見表）

`.chezmoi-source/`配下のファイル名は以下の規則で`~/`配下にデプロイされる
（詳細はchezmoi公式: <https://www.chezmoi.io/reference/source-state-attributes/>）。

- `dot_<name>` → `~/.<name>`（例: `dot_bashrc` → `~/.bashrc`）
- `private_<name>` → パーミッション`600`／ディレクトリは`700`で配置
- `executable_<name>` → 実行権限（`+x`）付きで配置
- `<name>.tmpl` → Goテンプレートとして評価してから配置
- `.tmpl`本文に`{{ ... }}`構文を文字列として残したい場合は文字列リテラルでエスケープする。
  例: `{{ "{{ env.X }}" }}`と書くと配布先で`{{ env.X }}`が出力される。
  展開結果は`chezmoi execute-template < <path>`で確認できる
- `run_onchange_after_<name>.sh.tmpl` → `chezmoi apply`時に変更検知して実行
- よく使うコマンド: `chezmoi apply`（反映）・`chezmoi diff`（差分確認）・`chezmoi managed | grep <相対パス>`（配布対象確認）
- pre-commitフックで`$HOME/dotfiles`チェックアウト時のみ`chezmoi apply`が自動実行される。
  そのため`.chezmoi-source/`の編集は通常コミット前に配布先（`~/.*`配下）へ自動反映される

### 振り返りHook/Skill

振り返りを促すHook/Skillが3カ所に組み込まれている。
配布先・タイミング・対象スコープが異なるため分けて管理する。

- `agent-toolkit/scripts/stop_advisor.py` — 配布物。プロジェクトドキュメント全般が対象
- `scripts/claude_hook_stop.py` — dotfiles個人環境専用。
  agent-toolkit本体・配布ルール・pyfltrの振り返りを担当。
  対象プロジェクトはセッションのcwdに応じて切替わる
- `.chezmoi-source/dot_claude/skills/session-review/SKILL.md` — ユーザー手動起動スキル

3カ所は同じStopイベントで並列発火する前提のため、共通指示（自己完結性・行フォーマット・出力スタイル等）は
`stop_advisor.py`側のreasonへ集約し、`claude_hook_stop.py`側は章固有の指示のみ記述する。
3カ所の内容を変更する際は同期漏れに注意すること。
