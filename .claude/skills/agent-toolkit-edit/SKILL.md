---
name: agent-toolkit-edit
user-invocable: false
description: >
  `agent-toolkit/`・`agent-basics/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/share/rules/common/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  版数更新・marketplace管理・セッション状態フラグの扱い、レビュー担当の指示・モデル変更時の評価セットの比較を含む。
---

# agent-toolkit（Agent Plugins・Claude Code・Codex・Antigravity）

本スキルは、`agent-toolkit/`配下の配布物や`.claude-plugin/marketplace.json`を編集するエージェントに向けて、ファイル構成、参照関係、付帯作業の扱い、バージョン更新、配布および同期に関する規約を提供する。
エージェント向け文書の改訂全般に共通する手順（編集前に確認すべき記録や、規範を削除・縮小する際の消失確認など）は、`edit-dotfiles-norms`で扱う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 配布物のコードや文書を編集する前 | `agent-basics:writing-standards/SKILL.md` |
| 配布物の技術的条文の根拠や監査記録を扱う前 | `agent-basics:writing-standards/references/investigation.md` |
| 配布物の文書・コード・コメントを改訂する前 | `agent-basics:review-standards/SKILL.md` |

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| WI処理の工程や運用を担うスキル、`share/`配下の`<役割名>.subagent.md`、`atk wi`の実装、`agent-toolkit/agent_toolkit/_atk/run_skill.py`または`agent-toolkit/agent_toolkit/_atk/orchestrator.py`を編集する前 | `agent-toolkit:workflow-overview`を起動する。ユーザーの判断、操作、観測結果が変わる場合のみ、対応する`docs/guide/`節も読む |
| 統括の起動引数、turn終了、再開または外部制御の経路を変更する前 | `agent-toolkit:workflow-overview/references/orchestrator-runtime.md`。同資料の実装由来の要約も同じ変更単位で更新する |
| `agent-toolkit/agent_toolkit/agents_server_mcp.py`、`agent-toolkit/agent_toolkit/_agents_server/`配下、`rust/claude-statusline/src/agents_server.rs`または`rust/claude-statusline/src/agents_server/`配下の実装、および共有状態の読書き・保存先・更新主体・利用関係を変更・調査する前 | `references/agents-server-shared-state.md`。保存先、更新する主体、状態ディレクトリ配下のファイル構成を変更する場合は、同一の変更単位で該当資料も更新する |
| セッション状態や内部フラグを設計・変更・調査する前 | `references/session-state-and-flags.md`。フラグを追加・変更する際は、該当する主題別資料を更新する |
| `agents_server`が起動した委譲先が動作しない事象（起動失敗、初期化未到達、委譲先の無応答、結果の欠落）を調査する前 | `references/agents-server-investigation.md`。ログの所在、診断項目、委譲先CLIへ渡す引数を変更する場合は、同一の変更単位で同資料も更新する |
| toolkitのPython実装を新設・変更する前 | `references/python.md` |
| 機械チェックスクリプトを新設・改修する前 | `references/check-script-design.md` |
| 環境変数を新設・変更する前、環境変数の一覧から名称と用途を確認する前 | `references/claude-hooks.md` |
| レビュー担当への指示・モデルの割り当てを変える前 | `references/review-eval-set.md` |
| WindowsのCodex境界試験の記録、またはpytest回収失敗時の標準エラー`resource-diagnostics:`を調べる前 | `references/agents-server-investigation-resource-diagnostics.md` |
| `agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptを変更する前 | `references/atk-serve-static.md` |
| 次のいずれかを変更する前、および作業ツリーで改訂した配布物の挙動を確認する前: `atk`のサブコマンドと出力、MCPサーバー識別子とツール名、フックの実装と登録、遮断や警告の判定・出力・通知formatter、プラグイン内の実行時パス、権限設定、marketplaceと配布の手順 | `references/distribution-and-hooks.md` |
| hook、自動チェック、永続状態、配送経路など新しい仕組みを加える計画を起草する前と、その変更を実行レビューする前 | `./../edit-dotfiles-norms/SKILL.md` |
| `agent-toolkit/`配下を変更対象に含む計画を起草する前 | `references/version-bump-planning.md` |
| 新しいCLI・コマンド・ラッパースクリプトを公開する計画を起草する前 | `references/distribution-and-hooks-new-cli.md` |
| 全レーンの完了後にバージョンを更新する時点、および`references/version-bump.md`の手順へ入る前 | `agent-toolkit/skills/process-wi/references/finish-session.md` |
| `agent-toolkit/agent_toolkit/`・`agent-toolkit/scripts/`の配置とインポートを変更する前 | `references/distribution-and-hooks-layers.md` |
| スキル（`SKILL.md`・`references/`）を新設・編集する前 | `../../../agent-toolkit/share/agent-skills-operations.md` |
| `.ps1`・`.ps1.tmpl`・`.psm1`・`.psd1`を編集する前 | `../../../agent-toolkit/share/powershell-operations.md` |
| 配布プラグインの文書・スキル・役割契約を編集する前、機構や外部インターフェースの変更に伴う文書・生成物の追随先を確定する前 | `references/agent-documents.md` |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂（`<役割名>.parent.md`・`<役割名>.subagent.md`を含む） | `references/sub-agents.md`、`references/sub-agents-declarations.md` |
| プラグインの版数・説明文・派生マニフェストを更新する前、または公開時の採番要否を確定する前 | `references/version-bump.md` |

## ファイル構成と参照方向

- `agent-toolkit/`: Agent Plugins、Claude Code、Codexが共有するプラグインルート。
- `agent-basics/`: 成果物の品質スキルを独立して配布するプラグインルート。
  toolkitからbasicsへの一方向依存とする。
- `agent-toolkit/share/rules/`: エージェントへ配る規範の原本を1か所に置く。
  全主体へ常時配る規範は`common/`、役割・ホスト別に注入する規範は直下の`rules-*.md`とし、配る相手をディレクトリとファイル名で区別する。
- `agent-toolkit/share/rules/common/`: 全主体へ常時配るルールファイルの配置場所（`01-agent.md`は基本原則、`02-agent-operations.md`は製品横断の実行運用ルール）。
  メイン、サブエージェント、委譲先のすべてに適用する条文のみを置き、サブディレクトリは作成しない（`scripts/gen_install_files.py`とインストーラーは直下の`*.md`のみを名前で配布一覧へ列挙・取得する）。
  配布先や同期手順は`dotfiles-repo-layout`「ディレクトリ構造に関する注意点」が扱う。
- `agent-toolkit/share/rules/rules-main.md`・`rules-main.claude-code.md`・`rules-main.codex.md`: メイン向けの共通規範および各ホスト固有の規範。
- `agent-toolkit/share/rules/rules-common.codex.md`: Codexの全エージェント向けの規範。
- `agent-toolkit/share/rules/rules-subagent.md`・`rules-subagent.claude-code.md`: 委譲先向けの共通規範およびClaude Code固有の規範。
  Codex固有の差分が必要な場合は`rules-subagent.codex.md`を追加する。
  振り分け基準は`agent-basics:writing-standards/references/agent-documents-additions-additions.md`「規範追記時の判定」に従う。
- 環境変数の命名規則として、配布物内で完結するものは`AGENT_TOOLKIT_<PURPOSE>`、個人環境で完結するものは`DOTFILES_`を接頭辞とする。
  一覧は`references/claude-hooks.md`にある。

参照方向は「dotfilesリポジトリ → プラグイン」および「プラグイン ↔ ルールファイル」を許容する。
`pytools/`は同一リポジトリ内のagent-toolkitをeditableインストールして解決するため、`_`で始まるプライベートなサブパッケージもインポートしてよい。
プライベートなモジュールを移動または改名する場合は、同一の変更で`pytools/`側のインポートも揃える。
記述の配置場所は、読み込まれる時点と成立に必要な前提条件に基づいて決定する。
次の資料を参照する。

- `agent-basics:writing-standards/references/agent-documents-basics-purpose.md`
- `agent-basics:writing-standards/references/agent-documents-basics-placement.md`
- `agent-basics:writing-standards/references/agent-documents-basics-read-tables.md`
- `agent-basics:writing-standards/references/agent-documents-basics-ownership.md`
特定のツール、データ、命名、CI、運用形態などを前提とする内容はプロジェクト側に置き、それらの固有要素を同種の別要素へ置き換えても判定基準・工程順・終了条件が成立する汎用的な内容のみを配布物の対象とする。

## 配布物としての記述方針

本プラグインはdotfilesユーザー以外にも配布されるため、環境によって異なる可能性のある設定値やディレクトリ構成などの前提は、条件付きで記述する。

- 本文、docstring、コメントには配布物自身の挙動や仕様を記述し、個人用フックとの連携などエンドユーザー環境側の固有設計は含めない。
- agent-toolkit同梱スキルは、`agent-basics:review-standards`のようにプラグイン名で修飾した完全名で記述する（努力目標）。
  `.claude/skills/`のプロジェクトローカルスキルは単体のスキル名とし、サブエージェント名は省略しない完全名称で記述する。
- 配布物が参照する単一情報源（SSOT）は配布物内に配置し、dotfiles固有のファイルや非配布ファイルを参照先に指定しない。
  例外として、実測値を根拠とする条文が参照する監査記録`docs/development/audit-records.md`は配布物外に置く（失効判定でのみ参照するため）。
  記録先は`agent-basics:writing-standards`の`references/investigation.md`で定めている。
- ファイル編集時に`pytools/claude_hook/pretooluse.py`の固有名チェックが個人環境固有の識別子を検出した場合は、汎用的な表現へ置き換える。
- 配布物スキルの本文では、フックの挙動をエンドユーザーが観測できる結果として示し、ハッシュ値の比較や状態フラグの更新といった内部実装の詳細は記載しない（努力目標）。
  状態フラグやフック間連携の内部仕様は`references/session-state-and-flags.md`へ集約する。
