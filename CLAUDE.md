# CLAUDE.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリ。
`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 主なディレクトリ

- `.chezmoi-source/` — chezmoiソースディレクトリ（`dot_` prefix → `~/.*`に反映される）
- `pytools/` — Pythonコマンドラインツール群（uv tool installでインストール）
- `bin/` — ユーザーのPATHに追加して使うコマンドラッパー（リポジトリ直下でgit管理）
- `plugins/` — Claude Code用プラグイン（マーケットプレイス経由で他人にも配布）
- `scripts/` — リポジトリ開発専用スクリプト（pre-commit/Makefileから呼ばれる。配布対象外）
- `.claude/` — dotfilesリポ自身のClaude Codeプロジェクト設定（配布対象外）

## 開発手順

- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `uv run pyfltr run-for-agent`
  - ドキュメントなどのみの変更の場合は省略可（pre-commitで実行されるため）
  - テストコードの単体実行なども極力 `uv run pyfltr run-for-agent <path>` を使う（pytestを直接呼び出さない）
    - 詳細な情報などが必要な場合に限り `uv run pytest -vv <path>` などを使用
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

- plugins/agent-toolkit/scripts/stop_advisor.py
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
- ホーム配下のファイルを編集する前に`chezmoi managed | grep <相対パス>`で配布対象か確認する。
  配布対象は`.chezmoi-source/`側を編集する
- `.chezmoi-source/`配下のファイルを削除した場合、chezmoiは配布先を自動削除しない。
  配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する（`chezmoi apply`後処理で削除される）
- 配布対象（Linux/Windows両対応）と開発対象（Linuxのみ）でサポート範囲が異なる。ファイル追加時にどちら用か意識する
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する。
  対応ファイル一覧は[docs/development/development.md](docs/development/development.md)の「プラットフォーム対応ファイル」参照
- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する。
  CIチェックアウトや利用者環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが壊れるため
- シンプルなコマンドラッパーの新規追加には`scripts/new-bin-cmd.py <name> <command...>`を使う。
  リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成し、`development.md`のペア一覧も自動更新する
- `agent-toolkit/*.md`（配布ルール本体）を改訂する際、`docs/guide/claude-code-guide.md`に要約・ステップ数などが
  再掲されていることが多い。本体変更前に`grep`で参照箇所を確認する
- `agent-toolkit/`配下のファイル分割（`agent.md`・`styles.md`など）は編集・レビュー時の見通し改善が目的で、
  配布先の`~/.claude/rules/agent-toolkit/`では全ファイルが常時自動ロードされる
- `agent-toolkit`編集時の方針:
  - 参照方向の許容範囲: dotfilesリポジトリ → プラグイン配布物、およびプラグイン配布物 ↔ 配布ルール
   （`~/.claude/rules/agent-toolkit/`）の参照は方向性として許容する。
    配布ルールは常時ロードされるためスキル側からのファイル名指定は省略可能だが、必須ではない。
  - SKILL.md本体に必要な情報は本体に直接書く。`references/`から別の`references/`を多段参照させない
   （Skillsのベストプラクティスに沿うため）
  - サブエージェント間で共通する判断基準・制約は各エージェントに重複記述したまま維持する
   （別コンテキストで実行されるため、統合するとコンテキスト汚染や指示漏れが起きる）
  - 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を必ず確認する。
    例えば`careful-impl`経路と`careful-impl不使用`経路で同じ手順を分担する場合、
    片側にある排他的表現（「唯一のガード」など）が新設で不整合になりやすい
  - 配布ルールは常時ロード、スキル本体は呼び出し時のみという責務分担を意識する
   （配布ルールに常時ロードする内容と、スキル側で扱う詳細は重複させない）
  - 「実行時エラーで判明する仕様（tool quirk）」「具体例」は事前知識・見落とし防止のため削除候補から外す
  - 配布物（`plugins/agent-toolkit/`配下）の出力文字列・hookメッセージ・docstringには
    リポジトリ管理外の個人メモファイル名を含めない。
    検出対象は`scripts/claude_hook_pretooluse.py`の項目3が定義する。
    リポジトリ管理ファイルから個人メモへ言及するとhookが警告で阻止する。
    利用者向けに同名ファイル作成を推奨する文脈は配布対象外のドキュメントへ寄せる
  - 配布物（`plugins/agent-toolkit/`配下と`.chezmoi-source/dot_claude/rules/agent-toolkit/`配下）には、
    執筆者の手元プロジェクト固有の前提を断定的に書かない。
    特定設定値の採用状況・特定のディレクトリパス・「本リポジトリは〜」のような自指的表現を避け、
    条件付き表現（「`～`設定が有効な場合、」など）で書く
   （ルール名・設定キー名そのものは仕様参照として書いてよい）
- `agent-toolkit/agent.md`のコミットメッセージ方針と`.gitmessage`は意図的に重複させている。
  前者はプラグイン配布対象（他リポジトリでも参照される）、後者は本リポジトリ固有のコミット補助テンプレート
 （`ccommit`等も参照する想定）のため、SSOT化せず双方に必要な情報を持たせる。片方を参照リンクに置き換えない
- spec-driven系スキル（`spec-driven`・`spec-driven-init`・`spec-driven-promote`）は本リポジトリでは対象外。
  `docs/features/`・`docs/topics/`の運用を採らないため、機能追加時も起動しない
- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュールを置く。
  privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する
- `pytools/_internal/claude_common.py`は共通基盤モジュールとして
  `find_dotfiles_root()`・`run_subprocess()`・`atomic_write_text()`・`atomic_write_json()`・`load_json_dict()`を提供する。
  新規ヘルパーを書き起こす前に当モジュールの公開APIを確認し、重複定義を避ける
- 依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使う。`UV_FROZEN`はCI/make内で自動適用される
- `pytools/_internal/setup_registry.py`はWindows向けの少数のレジストリ値（Explorerの拡張子表示など）を
  `winreg`で直接書き込むモジュール。`post_apply`のステップから呼ばれる
