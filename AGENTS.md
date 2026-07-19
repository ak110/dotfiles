# CLAUDE.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリであり、`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 開発手順

- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `uvx pyfltr run-for-agent`
  - プロジェクト設定済みコマンドの再現が必要な場合は`pyfltr run-for-agent <path>`を使う。
    デバッガ・最小再現・環境切り分けでは直接実行してよい
  - 修正後の再実行時は`--commands=mypy,ruff-check`等で限定して実行する（最終検証はCIに委ねる前提）

## アーキテクチャの参照先

リポジトリ全体の構成・配布対象と開発対象の区別・プラットフォーム対応・bash補完運用・PowerShellスクリプト注意事項・
ホーム配下編集前の確認手順は[docs/development/architecture.md](docs/development/architecture.md)に集約している。

## 注意点

- `.claude`を含むディレクトリが3系統あり、配布元・デプロイ先・リポジトリ専用と役割が異なる
 （`.chezmoi-source/dot_claude/`/`~/.claude/`/`.claude/`）
  - 指示の対象を必ず確認する（詳細は「固有差分」の「ディレクトリ構造の注意」を参照）
- `.chezmoi-source/dot_codex/`はCodex用の配布元で`~/.codex/`へデプロイされる
  - Claude Code側と共有できるルール・スキルはコピーせず、`post_apply`の専用ステップで原本へリンクする
  - chezmoiの`symlink_`はWindowsで特権不足により失敗するため未使用で、
    Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクションを生成する
- chezmoi管理ソース（`.chezmoi-source/dot_claude/`配下）はパス上`dot_claude`命名だが、
  配布先`~/.claude/`配下のコーディングエージェント向け文書と同等として扱う
- `.chezmoi-source/`配下のファイルを削除・改名した場合、chezmoiは配布先を自動削除しない
  - 配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する（`chezmoi apply`後処理で削除される）
  - 改名時は`_REMOVED_PATHS`の`~/.claude`欄（Codex側にもリンクがある対象は`~/.codex`欄も）へ
    旧パスを追記し、`setup_codex_links.py`の`_LINKS`マッピングを新名へ更新する
- プラットフォーム対応ファイル（Linux/Windowsのペア）を編集するときは`sync-platform-pair`スキルを呼び出して両側を同期する
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）で書かれている
  - UTF-8前提のEdit/Writeツールでは文字化けや破損のリスクがあるため、ASCIIのみの修正は`sed -i`で対応する
- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する
 （CIチェックアウトや利用者環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが破綻するため）
- 単純なコマンドラッパーの新規追加には`scripts/new-bin-cmd.py <name> <command...>`を使う
 （リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成する）
- `agent-toolkit/`配下・`.claude-plugin/marketplace.json`の編集時はSkillツールで`agent-toolkit-edit`を呼び出す
  - SKILL.mdをReadで読むだけではPreToolUseフックの`agent_toolkit_edit_skill_invoked`フラグが立たず警告が返る
  - marketplace管理・フック実装の配置先判断・version bump手順も同スキルへ集約する
  - `agent-toolkit/rules/`・`agent-toolkit/skills/`配下のMarkdown編集時は`agent-standards`・`writing-standards`を併用する
- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュール
  （単一ファイル`<name>.py`またはサブパッケージ`<name>/`配下形態）を置く
  - サブパッケージは`__init__.py`が`_cli.py`の`main`を再エクスポートし、`project.scripts`はパッケージ名の`main`を参照する
  - リネーム・再配置時はサブパッケージ配下も対象に含める
- privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する
- エージェント・hook・自動化など手で起動しないスクリプトは`scripts/`配下へ置く
 （`[project.scripts]`登録は行わず、PEP 723形式の単独実行スクリプトとして書く）
- Claude Codeのhook・statuslineから起動するPEP 723スクリプトは`uv run --no-project --script`形式で呼び出す。
  対象は`agent-toolkit/hooks/hooks.json`と`share/claude_settings_json_managed.*.json`
- PEP 723スクリプト（`agent-toolkit/scripts/atk.py`等）の`dependencies`へパッケージを追加・更新する場合、リポジトリ本体の`pyproject.toml`にも同一制約で登録する
  - `watchdog`・`argcomplete`・`platformdirs`は既に`pyproject.toml`とPEP 723 header双方へ登録されている既存重複登録パターンに揃える
  - テスト実行が間接依存で偶然解決する状態を防ぐため
- `scripts/claude_hook_*.py`等の編集時は`agent-toolkit/scripts/`配下のヘルパー
 （`_plan_file.py`等）を既存類似先例として再利用する
- `pytools/`トップレベルの公開CLIモジュールはbash補完（argcomplete）に対応する
  - 手順と例外は`docs/development/architecture.md`の「bash補完」節を参照する
- Pythonテストコードはソースモジュールと同一ディレクトリに`<name>_test.py`として配置する
 （`pytools/`・`scripts/`・`agent-toolkit/`配下いずれも同方式）
  - テスト共通ヘルパーの集約方針は配布物境界に応じて区別する
    - `pytools/`配下のテストは共通ヘルパーを`pytools/_internal/_test_helpers.py`へ集約する
    - `agent-toolkit/`配下のテストは`pytools/_internal/`配下を参照せず配布物独立性を保つ
      - 共通化が必要な場合は`agent-toolkit/scripts/`配下に独自ヘルパーを置く
    - `scripts/`配下のテストは`pytools/_internal/_test_helpers.py`を必要に応じて参照してよい
  - `pytools`パッケージ配布物にテストコードを含めないため、
    `[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を除外する
  - `scripts/`配下のスクリプト固有の補足:
    - pytestはprependモードで`scripts/`を`sys.path`へ自動追加するため、テスト側からスクリプトを直接importできる
    - importしたい場合はファイル名をアンダースコア区切り（`<name>.py`）で命名する
    - shebangを持つスクリプトは`chmod +x`で実行権限を付与する（pre-commitの`check-shebang-scripts-are-executable`が強制する）
- `pytools/_internal/claude_common.py`は共通基盤モジュールとして以下を提供する
  - `find_dotfiles_root()`・`run_subprocess()`・`atomic_write_text()`・`atomic_write_json()`・
    `load_json_dict()`・`write_settings_hybrid()`
  - 新規ヘルパーを書き起こす前に公開APIを確認し、重複定義を避ける
- `.chezmoi-source/`配下のpost-applyテンプレートはハッシュキャッシュで再実行を抑制し、外部CLIを呼び出す構成をとる
  - 「入力ハッシュ一致」と「期待シム実在」の両方が満たされた場合のみキャッシュを有効と判定する
  - `pyproject.toml`の`[project.scripts]`にpost-apply処理継続に必須のCLIを追加・改名した場合の対応。
    両テンプレートの変数定義節にある`$expectedShims`・`expected_shims`定数を同一値に更新する
- `process-feedbacks`スキル完了後はコミット作成に加えて`git push`まで実施する（dotfilesリポジトリ運用ルール）
  - フィードバック投入元（feedback-inbox）の整合性を保つため、ローカルに留めず即時公開する

## 固有差分

### ロールとファイル群の対応

本リポジトリと配布物には複数のロールが関与する。ファイル群を編集する際は対象読者を意識する。

- dotfiles利用者: chezmoiソース・`bin`・`pytools`等を自分の環境にインストールして使う人
- agent-toolkit利用者: `agent-toolkit`プラグインをマーケットプレイス経由で使う人（dotfiles利用者含む）
  - 配布ルール（`~/.claude/rules/agent-toolkit/`）も導入済み前提で記述してよい
- 全プロジェクト編集者: あらゆるプロジェクトで編集作業をするコーディングエージェント
  - 配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するコーディングエージェント
  - 全プロジェクト編集者の対象に加え、リポジトリ直下の`.claude/`と`AGENTS.md`もロードする
   （Claude Codeは`CLAUDE.md`経由のfile importで読む）

各ファイル群の対象読者と役割。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `agent-toolkit/agents/`配下 | 全プロジェクト編集者 | サブエージェントの指示本体 |
| `agent-toolkit/agents/`配下のfrontmatterコメント | dotfiles編集者 | 連携先や注意事項などの編集用メタ情報 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfiles利用者 | 常時自動ロードされる行動原則（dotfiles利用者には配布先`~/.claude/`相当） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md` | agent-toolkit利用者 | プラグインの導入・更新手順 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（本ファイル） | dotfiles編集者 | 本リポジトリの修正方針・固有知見。`CLAUDE.md`は`@AGENTS.md`importの1行アダプター |
| `pytools/`・`bin/`・`scripts/` | dotfiles利用者・dotfiles編集者 | コマンドラインツールと開発スクリプト |

### コミットメッセージtypeの判定例

本リポジトリには配布物と本リポジトリ専用設定が混在するため、変更対象に応じてtypeを使い分ける。

- 配布物の振る舞いを変える変更は`feat`/`fix`/`perf`相当
  - `.chezmoi-source/`配下: dotfiles利用者の環境に展開されるため、利用者振る舞いを変える変更は機能変更
  - `agent-toolkit/`配下: プラグイン利用者のエージェントの振る舞いが変わる（スキル・サブエージェント・ルール・`references/`）
  - `pytools/`・`bin/`配下: dotfiles利用者向けCLIツールのため、挙動変更はそのまま機能変更
- 本リポジトリ専用設定の変更は`chore`相当（リポジトリ直下の`.claude/`・本ファイル`AGENTS.md`・アダプター`CLAUDE.md`）
- 配布物の利用者向け説明の変更は`docs`相当（`README.md`・`docs/guide/`配下など）
- 軽微な誤字修正・スタイル調整・コメント整形などは内容にかかわらず`chore`に倒してよい

### ディレクトリ構造の注意

Claude Code/Codex設定ディレクトリが複数あり、取り違えは影響範囲の異なる事故につながる。指示の対象を必ず確認する。

- `.chezmoi-source/dot_claude/`: 配布元。chezmoiが`~/.claude/`にデプロイする（グローバルユーザー設定の原本）
- `~/.claude/`: デプロイ先。`chezmoi apply`で上書きされるため直接編集してはならない
  - ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`
- `.claude/`（本リポジトリルート）: dotfilesリポジトリ自身のClaude Codeプロジェクト設定。配布対象外
- `.chezmoi-source/dot_codex/`: Codex配布元。`~/.codex/`へデプロイする
  - `AGENTS.md`はCodex向けアダプター。共有ルール・スキルは`setup_codex_links.py`が
    `.chezmoi-source/dot_claude/`または`agent-toolkit/`の原本へリンクを生成する
    （Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション）
- `.chezmoi-source/dot_config/`: XDG準拠ツール設定（`git`・`uv`・`pyfltr`等）の配布元
  - ユーザーが「`~/.config/<tool>`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_config/<tool>/`
    - 配布先を直接編集すると`chezmoi apply`で巻き戻る
- `AGENTS.md`（本リポジトリルート）: dotfiles編集者向けのSSOT。Claude Code／Codex双方がここを読む
  - `CLAUDE.md`は`@AGENTS.md`をimportする1行のみのアダプター
- `.claude/`（本リポジトリルート）のプロジェクト専用ルール・スキルは配布対象外
  - Codex側でも明示検出させたい場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
  - `~/.codex/skills`にはグローバルに使うスキルだけを置く
  - chezmoiはドットプレフィックス自動無視のため`.chezmoi-source/dot_claude/`と衝突なし

### SSH対話ログイン時のtmux自動アタッチ

ホスト単位でSSH対話ログイン時の`tmux`自動アタッチを有効化できる。
既定は無効で、`~/.config/dotfiles/tmux-auto-attach`の有無で切り替える。

- 有効化: `autotmux on`／無効化: `autotmux off`／状態確認: `autotmux status`（有効時0・無効時1で終了）

自動アタッチは次の全条件を満たした場合のみ実行される。

- 対話シェル
- SSH経由（`$SSH_CONNECTION`または`$SSH_TTY`が設定されている）
- tmux外（`$TMUX`空）
- 標準入力がTTY
- IDE Remote統合ターミナル外（`$VSCODE_INJECTION`空・`$TERM_PROGRAM`非`vscode`・`$TERMINAL_EMULATOR`空）
- `tmux`コマンド存在
- フラグファイル存在

tmuxセッション名は`main`に固定し、デタッチ時にSSH接続も終了する。フラグファイルはchezmoi管理対象外でホスト固有運用とする。

### 対話シェル起動時のTBD未回答表示

対話シェル起動時に`atk fb list --type=tbd --status=unanswered --skip-pull`を自動実行し、未回答TBDを1件1行で画面へ通知する。
`--skip-pull`でログイン時のリポジトリアクセスを避け、0件時は出力なしで終了する。

通知は次の全条件を満たした場合のみ実行される。

- 対話シェル
- `atk`コマンド存在（`command -v atk`）
- feedback-inbox有効（`atk fb status`が終了コード0）

`atk fb disable`でfeedback-inboxを無効化すると`atk fb status`が非ゼロ終了し、通知は自動的にスキップされる。

### Windowsの電源設定の最適化（dotfiles-setup）

`dotfiles-setup`コマンドはWindows専用で、高速スタートアップとUSB selective suspendをまとめて無効化する。

- 高速スタートアップ無効化: `HiberbootEnabled=0`レジストリ書き込みと`powercfg /hibernate off`を実行する
- USB selective suspend無効化: 電源プラン層のAC・DC両系統と、per-device層（`SelectiveSuspendEnabled`と
  `MSPower_DeviceEnable.Enable`。`HKLM:\SYSTEM\CurrentControlSet\Enum\USB`配下）を全USBデバイスへ適用する
- 管理者権限が必要で、未昇格時は`Start-Process -Verb RunAs`でUAC自昇格再起動する（子プロセスはEnter待ち）
- 適用は冪等性を保ち、現在値が望ましい値の場合は変更せず「変更なし」と表示する

### chezmoiの命名規則（早見表）

`.chezmoi-source/`配下のファイル名は以下の代表規則で`~/`配下にデプロイされる。
詳細は<https://www.chezmoi.io/reference/source-state-attributes/>を参照する。

- `dot_<name>` → `~/.<name>`
- `private_<name>` → パーミッション`600`／ディレクトリは`700`
- `executable_<name>` → 実行権限付きで配置
- `<name>.tmpl` → Goテンプレートとして評価
- `run_onchange_after_<name>.sh.tmpl` → `chezmoi apply`時の変更検知実行
- よく使うコマンド: `chezmoi apply`（反映）・`chezmoi diff`（差分確認）・`chezmoi managed`（配布対象確認）

pre-commitフックで`$HOME/dotfiles`チェックアウト時のみ`chezmoi apply`が自動実行される。
