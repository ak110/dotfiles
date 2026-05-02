# CLAUDE.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリ。
`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## ロールとファイル群の対応

本リポジトリと配布物には以下の4ロールが関与する。
ファイル群を編集する際は、対象読者を意識して文面を選ぶ。

- dotfiles利用者: 本リポジトリ（chezmoiソース・`bin`・`pytools`等）を自分の環境にインストールして使う人
- agent-toolkit利用者: `agent-toolkit`プラグインをマーケットプレイス経由で使う人。
  dotfiles利用者を含むがそれ以外もいる
- エージェント: Claude Codeなどのコーディングエージェント。
  実行時にagent-toolkit本体やルール・スキルをロードして動く
- 編集者: 本リポジトリや`agent-toolkit`本体を修正する主体。エージェントと人間の双方を含む

各ファイル群の対象読者と役割は以下の通り。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/skills/*/SKILL.md`本文・`agent-toolkit/agents/*` | エージェント | スキル・サブエージェントの指示本体 |
| 上記のfrontmatterコメント | 編集者 | 連携先や注意事項などの編集用メタ情報 |
| `.chezmoi-source/dot_claude/rules/agent-toolkit/`配下のルール本体 | エージェント | `~/.claude/rules/agent-toolkit/`配下で常時自動ロードされる行動原則 |
| `docs/guide/claude-code-guide.md` | agent-toolkit利用者 | プラグインの導入・更新手順 |
| `.chezmoi-source/dot_claude/`配下 | dotfiles利用者 | 配布先`~/.claude/`相当のClaude Code設定 |
| `.claude/`（リポジトリ直下） | 編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `CLAUDE.md`（本ファイル） | 編集者 | 本リポジトリの修正方針・固有知見 |
| `pytools/`・`bin/`・`scripts/` | dotfiles利用者・編集者 | コマンドラインツールと開発スクリプト |

`.chezmoi-source/dot_claude/`配下の変更は配布先`~/.claude/`を経由して、
dotfiles利用者が他リポジトリで作業する場面にも影響する。

## 開発手順

- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `uvx pyfltr run-for-agent`
  - ドキュメントなどのみの変更の場合は省略可（pre-commitで実行されるため）
  - テストコードの単体実行なども極力`uvx pyfltr run-for-agent <path>`を使う（pytestを直接呼び出さない）
  - 修正後の再実行時は、対象ファイルや対象ツールを必要に応じて絞って実行する（最終検証はCIに委ねる前提）
    - 例: `pyfltr run-for-agent --commands=mypy,ruff-check path/to/file`

## chezmoiの命名規則（早見表）

`.chezmoi-source/`配下のファイル名は以下の規則で`~/`配下にデプロイされる
（詳細はchezmoi公式: <https://www.chezmoi.io/reference/source-state-attributes/>）。

- `dot_<name>` → `~/.<name>`（例: `dot_bashrc` → `~/.bashrc`）
- `private_<name>` → パーミッション`600`／ディレクトリは`700`で配置
- `executable_<name>` → 実行権限（`+x`）付きで配置
- `<name>.tmpl` → Goテンプレートとして評価してから配置
- `run_onchange_after_<name>.sh.tmpl` → `chezmoi apply`時に変更検知して実行
- よく使うコマンド: `chezmoi apply`（反映）・`chezmoi diff`（差分確認）・`chezmoi managed | grep <相対パス>`（配布対象確認）
- pre-commitフックで`$HOME/dotfiles`チェックアウト時のみ`chezmoi apply`が自動実行される。
  そのため`.chezmoi-source/`の編集は通常コミット前に配布先（`~/.*`配下）へ自動反映される

## 振り返りHook/Skill

本リポジトリには、コーディングエージェントに対して当該セッションの振り返りを促すHook/Skillが以下の3カ所に組み込まれている。
配布先やタイミングなどが異なるため分けているが、極力内容を同期するよう注意すること。
ただし配布単位が異なる箇所は責務分離を優先し、共通モジュール化できる部分のみ集約する。

- agent-toolkit/scripts/stop_advisor.py
- scripts/claude_hook_stop.py
- .chezmoi-source/dot_claude/skills/session-review/SKILL.md

なお`stop_advisor.py`は配布物のため対象をプロジェクトドキュメント全般の振り返りに限定する。
agent-toolkitプラグイン本体・配布ルールの振り返りは
dotfiles個人環境専用の`scripts/claude_hook_stop.py`が担当する。

## 注意点

- `.claude`を含むディレクトリが3系統あり取り違えやすい（`.chezmoi-source/dot_claude/` / `~/.claude/` / `.claude/`）。
  指示の対象を必ず確認する。詳細は[docs/development/development.md](docs/development/development.md)の
 「ディレクトリ構造の注意」参照
- chezmoi管理ソース（`.chezmoi-source/dot_claude/`配下）はパス上`dot_claude`命名だが、
  配布先`~/.claude/`配下のClaude Code設定系ファイルと同等として扱う。
  編集着手前に`agent-toolkit:writing-standards`スキルと、その`references/claude-common.md`を含む
  必読リファレンスを参照する
- ホーム配下を編集する場合の手順は[docs/development/development.md](docs/development/development.md)の
 「ホーム配下のファイルを編集する前の確認」参照
- `.chezmoi-source/`配下のファイルを削除した場合、chezmoiは配布先を自動削除しない。
  配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する（`chezmoi apply`後処理で削除される）
- 配布対象と開発対象のサポート範囲の差は[docs/development/development.md](docs/development/development.md)の
 「開発者と利用者の対象環境」参照
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する。
  対応ファイル一覧は[docs/development/development.md](docs/development/development.md)の「プラットフォーム対応ファイル」参照
- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する。
  CIチェックアウトや利用者環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが壊れるため
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
- `scripts/`配下にPython製スクリプトを追加する場合、
  テスト同居（`scripts/<name>_test.py`）方式で動作する。
  pytestはprependモードで`scripts/`を`sys.path`へ自動追加するため、
  テスト側からスクリプトを直接importできる。
  importしたい場合はファイル名をハイフン区切りではなくアンダースコア区切り（`<name>.py`）で命名する
- `pytools/_internal/claude_common.py`は共通基盤モジュールとして
  `find_dotfiles_root()`・`run_subprocess()`・`atomic_write_text()`・`atomic_write_json()`・`load_json_dict()`を提供する。
  新規ヘルパーを書き起こす前に当モジュールの公開APIを確認し、重複定義を避ける
