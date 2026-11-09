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

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 配布物のコードや文書を編集する前 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 配布物の技術的条文の根拠や監査記録を扱う前 | `agent-basics:writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 配布物の文書・コード・コメントを改訂する前 | `agent-basics:review-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../edit-dotfiles-norms/SKILL.md` | 判断 | `read:*/skills/edit-dotfiles-norms/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `./../dotfiles-repo-layout/SKILL.md` | 判断 | `read:*/skills/dotfiles-repo-layout/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../agent-basics/skills/writing-standards/references/agent-documents-additions-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions-additions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../agent-basics/skills/writing-standards/references/agent-documents-basics-purpose.md`、`../../../agent-basics/skills/writing-standards/references/agent-documents-basics-placement.md`、`../../../agent-basics/skills/writing-standards/references/agent-documents-basics-read-tables.md`、`../../../agent-basics/skills/writing-standards/references/agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-purpose.md`、`read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-placement.md`、`read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-read-tables.md`、`read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| WI処理の工程や運用を担うスキル、`share/`配下の`<役割名>.subagent.md`、または`atk wi`の実装を編集する前 | `agent-toolkit:workflow-overview`を起動する。ユーザーの判断、操作、観測結果が変わる場合のみ、対応する`docs/guide/`節も読む | 判断 | `read:agent-toolkit:*/skills/workflow-overview/SKILL.md` |
| `agent-toolkit/agent_toolkit/agents_server_mcp.py`、`agent-toolkit/agent_toolkit/_agents_server/`配下、または`rust/claude-statusline/src/agents_server.rs`の実装、および共有状態の読書き・保存先・更新主体・利用関係を変更・調査する前 | `references/agents-server-shared-state.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state.md` |
| セッション状態や内部フラグを設計・変更・調査する前 | `references/session-state-and-flags.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags.md` |
| `agents_server`が起動した委譲先が動作しない事象（起動失敗、初期化未到達、委譲先の無応答、結果の欠落）を調査する前 | `references/agents-server-investigation.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agents-server-investigation.md` |
| `agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptを変更する前 | `references/atk-serve-static.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/atk-serve-static.md` |
| 次のいずれかを変更する前、および作業ツリーで改訂した配布物の挙動を確認する前: `atk`のサブコマンドと出力、MCPサーバー識別子とツール名、フックの実装と登録、プラグイン内の実行時パス、権限設定、marketplaceと配布の手順 | `references/distribution-and-hooks.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/distribution-and-hooks.md` |
| hook、自動チェック、永続状態、配送経路など新しい仕組みを加える計画を起草する前と、その変更を実行レビューする前 | `./../edit-dotfiles-norms/SKILL.md` | 判断 | `read:*/skills/edit-dotfiles-norms/SKILL.md` |
| `agent-toolkit/`配下を変更対象に含む計画を起草する前 | `references/version-bump-planning.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/version-bump-planning.md` |
| 全レーンの完了後にバージョンを更新する時点、および`references/version-bump.md`の手順へ入る前 | `agent-toolkit/skills/process-wi/references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |
| `agent-toolkit/agent_toolkit/`・`agent-toolkit/scripts/`の配置とインポートを変更する前 | `references/distribution-and-hooks-layers.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-layers.md` |
| 配布プラグインの文書・スキル・役割契約を編集する前、機構や外部インターフェースの変更に伴う文書・生成物の追随先を確定する前 | `references/agent-documents.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agent-documents.md` |
| プラグインの版数・説明文・派生マニフェストを更新する前、または公開時の採番要否を確定する前 | `references/version-bump.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/version-bump.md` |

## ファイル構成と参照方向

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 環境変数の一覧から名称と用途を確認する前 | `./references/claude-hooks.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/claude-hooks.md` |

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

## agents_serverの共有状態

共有状態ごとの保存場所や読み書きの担当モジュールは、`references/agents-server-shared-state.md`から選ぶ主題別資料に記録されている。
保存先、更新する主体、または状態ディレクトリ配下のファイル構成を変更する場合は、同一の変更単位で該当資料も更新する。
`references/agents-server-shared-state.md`「状態資料の選択」「共有状態資料の選択」から、必要な共通資料と主題別資料を選んで全文を読む。

## agents_serverの委譲不具合の調査

`references/agents-server-investigation.md`は、ログ記録の所在、切り分けの順序、外部プロセスでの再現手順をまとめている。
ログの所在、診断項目、または委譲先CLIへ渡す引数を変更する場合は、同一の変更単位で同文書も更新する。

## セッション状態フラグ

`agent-toolkit`プラグインで管理するフラグの仕様は、`references/session-state-and-flags.md`から選ぶ主題別資料に集約する。
フラグを追加・変更する際は、該当する主題別資料を更新する。
フックの実装・編集やセッション状態の設計・変更する際は、`agent-basics:writing-standards`を起動して参照する（フック実装の基準を含む。エージェントの行動規範自体は役割別ルールまたは作業別スキルに置く）。
`references/session-state-and-flags.md`「セッション状態資料の選択」から、必要な共通資料と主題別資料を選んで全文を読む。

## 開発専用資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| hookの実装・登録・遮断や警告の判定・出力・通知formatterを変更する前 | `references/distribution-and-hooks.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks.md` |
| toolkitのPython実装を新設・変更する前 | `references/python.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/python.md` |
| 機械チェックスクリプトを新設・改修する前 | `references/check-script-design.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/check-script-design.md` |

## レビュー評価セット

対象はレビュー担当への指示とモデルの割り当てを変える変更である。
指示の例は`agent-toolkit/share/exec-review*.md`、`agent-toolkit/share/recurrence-self-check.parent.md`、`agent-basics:review-standards`である。
これらの変更では、`scripts/review_eval_cases.json`の評価セットで変更前後の結果を比べてから採用する。
見逃しと誤指摘の件数を実例で比べ、改訂が良くなったかを印象ではなく結果で判断するためである。
モデルの費用がかかるため、CIや定期工程には組み込まない。

1. `uv run --frozen python scripts/review_eval.py prepare --out <managed-tempの絶対パス>`で事例ごとの作業ツリーと計画の複製を作る。
   計画が新形式AWIを含む事例では、AWIの完成条件とH1・冒頭から写した条件表（`review_contract`）も作る。
   出力の`start`をそのままagents_serverの`start`へ渡し、比べるモデルは`model_type`で指定する
2. 変更前は稼働中のplugin rootの役割名（`exec-review`）、変更後は作業ツリーの`agent-toolkit/share/exec-review.subagent.md`の絶対パスを`subagent_md_path`へ渡す。
   agent-basicsのスキルなど役割文書の外の改訂は、稼働版へ反映された後の実行と、反映前に保存した結果を比べる
3. 各担当の終了後、`uv run --frozen python scripts/review_eval.py score --result <事例ID>=<出力のtableのパス>`で事例別の検出・見逃し・誤指摘・未分類と合計を出す。
   未分類の行は本文を読み、正しい指摘か誤指摘かを手で分類する
4. 変更後に悪化した事例があれば理由を確かめ、改訂の採否を決める。
   比較結果の保存先を計画の検証結果へ残し、出力の`cleanup`で作業ツリーを回収する

障害事例や振り返りで、レビューの見逃し（レビュー後に判明した欠陥）か誤指摘（レビュー担当の前提の誤り・撤回で不採用になった指摘）が確定したら、`scripts/review_eval_cases.json`へ事例を追加する。
入力はprivate-notesの`plans`配下の計画と`.wi-commits.jsonl`から再構成できるものを選び、期待には検出すべき問題か出すべきでない指摘を、同じ指摘行に揃って現れる照合語句（正規表現）で書く。
メインやレーン担当の技術判断による見送りは誤指摘に数えない。
実行レビュー以外のレビュー担当の事例は、`role`に役割名を、`extra_params`にその役割の起動入力を書いて追加する。
値の`{plan}`・`{base}`・`{case_dir}`は準備時に計画の複製・開始時のHEAD・事例の出力先へ置き換わる。
