---
name: agent-toolkit-edit
user-invocable: false
description: >
  `agent-toolkit/`・`agent-basics/`配下のプラグイン（入口`atk-mode`と手順書を含むスキル・サブエージェント・フックスクリプト・marketplace記述）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  版数更新・marketplace管理、hookと役割文書の規約、内部仕様の資料の索引、レビュー担当の指示・モデル変更時の評価セットの比較と、評価の事例・合成素材の追加を含む。
---

# agent-toolkit（Agent Plugins・Claude Code・Codex・Antigravity）

`agent-toolkit/`と`agent-basics/`の配布物、`.claude-plugin/marketplace.json`を編集する時の入口である。
作業から読む資料を下の表で選び、条件が成立した時にその資料を全文読む。
エージェント向け文書の改訂に共通する手順（既存の方針との照合、規範を削除・縮小する時の消失確認など）は`.claude/skills/edit-dotfiles-norms/SKILL.md`が扱う。

配布物の差分は、`*_test.py`だけの差分を除き、版数の更新と組にする（`claude plugin update`は版数が上がった時だけ更新を配信する）。
判定と手順は`references/distribution.md`にある。

## 作業と資料

| 作業・条件 | 読む資料 |
| --- | --- |
| 配布物のコード、文書、コメントを編集・改訂する | スキル`agent-basics:writing-standards`と`agent-basics:review-standards`を起動する |
| 配布物を変えてから公開担当へ渡すまで（計画への記録、版数の判定と更新、生成物の同期、競合時の再採番、marketplaceへの反映、Codexのrootの再確認） | `references/distribution.md` |
| 作業ツリーで改訂した配布物の挙動を確かめる、hookが新たに操作を止めた | `references/distribution.md`「worktreeでの編集時の注意」 |
| 新しいCLI、コマンド、ラッパースクリプトを公開する計画を起草する | `references/distribution.md`「新規CLI公開時の疎通確認」 |
| `agent-toolkit:atk-mode`の作業`process-wi`で全レーンの完了後に版数を上げる | `agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`、続けて`references/distribution.md`「バージョン更新」 |
| hookの実装、登録、判定、通知、出力と、`atk`のサブコマンドと出力を新設・変更する | `references/hooks.md` |
| 既存のhookの実装、登録、判定を変える計画の採否を決める（実装だけの改修を含む） | `docs/development/incidents.md`「区分」から「フックとセッション状態の不全」の所在ファイルを選んで同じ原因条件の事例を読み、`docs/development/design.md`の索引から選ぶhookの設計記録の該当節を読む。読んだ事例から守る条件を採否と検証へ対応付ける |
| hook、自動チェック、永続状態、配送経路など新しい仕組みを足す計画を起草する、その変更を実行レビューする | `.claude/skills/edit-dotfiles-norms/SKILL.md` |
| `agent-toolkit/agent_toolkit/`と`agent-toolkit/scripts/`の配置とimport、起動と依存の準備、toolkitのPython実装、機械チェックスクリプト、常駐CLIの待機と自動起動、MCPサーバーの識別子とツール名、プラグイン内の実行パス、権限設定、計画ファイルの構造定数、`atk serve`の静的資産を新設・変更する | `references/implementation.md` |
| サブエージェント定義と役割文書（`<役割名>.parent.md`、`<役割名>.subagent.md`）か、それらが関わる手順を作る・改める | `references/roles.md`。コンテキスト境界の判断は`agent-toolkit:atk-mode/references/agent-documents.md` |
| 配布するプラグインの文書、スキル、役割の契約を編集する、機構や外部インターフェースの変更に伴う文書と生成物の追随先を確定する、観測に基づく技術的条文と監査記録を扱う | `references/agent-documents.md` |
| 読込表の行を書く・直す | `agent-basics:writing-standards/references/agent-documents-basics.md`「読込表」 |
| WI処理の工程や運用を担うスキル、`agent-toolkit/skills/atk-mode/roles/`の`<役割名>.subagent.md`、`atk wi`の実装、`agent-toolkit/agent_toolkit/_atk/run_skill.py`、`agent-toolkit/agent_toolkit/_atk/orchestrator.py`を編集する | `agent-toolkit:atk-mode/references/workflow-overview.md`を読む。ユーザーの判断、操作、観測結果が変わる時だけ、対応する`docs/guide/`の節も読む |
| 統括の起動引数、turnの終了、再開、外部制御の経路を変える | `agent-toolkit:atk-mode/references/orchestrator-runtime.md`。同資料の実装由来の要約も同じ変更で直す |
| スキル（`SKILL.md`、`references/`）を新設・編集する | `agent-basics:writing-standards/references/agent-skills.md` |
| `.ps1`、`.ps1.tmpl`、`.psm1`、`.psd1`を編集する | `agent-basics:writing-standards/references/powershell.md` |
| レビュー担当への指示かモデルの割り当てを変える、レビュー評価の事例・合成素材を足す・直す | `references/review-eval-set.md` |
| 確認要否を定める条文を変える | `references/confirmation-eval-set.md` |

## 内部仕様の索引

`agents_server`の共有状態、hookが共有するセッション状態、委譲先の調査の手順、環境変数の一覧は、実装者向けの資料として`docs/development/`にある。
状態の保存先、更新する主体、状態ファイルの構成、ログの所在、診断項目、環境変数を変える変更では、同じ変更でその資料も直す。

| 作業・条件 | 読む資料 |
| --- | --- |
| `agents_server`の実装と、共有状態の読書き、保存先、更新主体、利用関係を変更・調査する | `docs/development/internals.md`「共有状態」 |
| セッション状態ファイルと内部フラグを設計・変更・調査する | `docs/development/internals.md`「セッション状態」 |
| `agents_server`が起動した委譲先が動かない事象（起動失敗、初期化未到達、無応答、結果の欠落）を調べる | `docs/development/internals.md`「委譲先の調査」 |
| 環境変数を新設・変更する、既存の環境変数の名前、用途、参照主体を確かめる | `docs/development/internals.md`「環境変数」 |

## ファイル構成と参照方向

- `agent-toolkit/`: Agent Plugins、Claude Code、Codexが共有するプラグインのroot。
- `agent-basics/`: 成果物の品質のスキルを独立して配るプラグインのroot。
  toolkitからbasicsへの一方向の依存とする。
- `agent-toolkit/skills/atk-mode/`: エージェントへ届ける規範の原本の置き場所。
  入口の`SKILL.md`に全ての作業に当てはまる決まりを、`playbooks/`に作業ごとの手順書を、`references/`に形式の定義を、`roles/`に役割文書を置く。
  メインはSessionStartのhookの案内（OpenCodeは設定の`instructions`の案内）で入口を起動し、委譲先は`agents_server`が`playbooks/subagent.md`から組み立てた起動の本文で受け取る。
  置き場所と各起動区分への配送は`agent-toolkit:atk-mode/references/agent-documents.md`「置き場所と配送」にある。
  振り分けの基準は`agent-basics:writing-standards/references/agent-documents-additions.md`「規範追記時の判定」にある。
- 環境変数の接頭辞は、配布物の中で完結するものを`AGENT_TOOLKIT_<PURPOSE>`、個人の環境で完結するものを`DOTFILES_`とする。

参照の方向は「dotfilesリポジトリ → プラグイン」を許す。
`pytools/`は同じリポジトリのagent-toolkitをeditableで導入して解決するため、`_`で始まるサブパッケージもimportしてよい。
プライベートなモジュールを移す・改名する時は、同じ変更で`pytools/`のimportもそろえる。
特定のツール、データ、命名、CI、運用の形態を前提とする内容はプロジェクトの側に置き、固有の要素を同種の別の要素へ置き換えても判定基準、工程の順、終了条件が成り立つ汎用の内容だけを配布物に入れる。
記述の置き場所は、読み込まれる時点と成立に要る前提から`agent-basics:writing-standards/references/agent-documents-basics.md`「置き場所の選定」で決める。

## 配布物としての記述方針

本プラグインはdotfilesのエンドユーザー以外にも配るため、環境で変わりうる設定値やディレクトリ構成などの前提は条件付きで書く。

- 本文、docstring、コメントには配布物自身の挙動と仕様だけを書き、個人用のhookとの連携などエンドユーザーの環境の側の固有の設計はプロジェクトの側に置く。
- agent-toolkitに同梱するスキルは、`agent-basics:review-standards`のようにプラグイン名で修飾した完全名で書く（努力目標。同名のスキルとの取り違えを防ぐため）。
  `.claude/skills/`のプロジェクトローカルのスキルは単体のスキル名で書き、サブエージェント名は省略しない完全名で書く。
- 配布物が参照する単一情報源は、dotfiles固有のファイルや配布しないファイルではなく、配布物の中に置く。
  例外として、観測値を根拠とする条文の監査記録は配布物の外の`docs/development/audit-records*.md`に置き、失効の判定だけに使う。
  記録の所在の示し方は`references/agent-documents.md`「エージェント向け文書の技術的条文」に従う。
- 配布物のスキルの本文では、hookの挙動をエンドユーザーが観測できる結果として示し、ハッシュ値の比較や状態フラグの更新などの内部実装は書かない（努力目標。内部実装の変更のたびに配布物の改訂が要るのを避けるため）。
  状態フラグとhookの間の連携の内部仕様は、本書「内部仕様の索引」の資料に置く。

## 読込表

編集操作で配送する資料は次の表で宣言する。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 工程別モデルの初期値とプリセット（`agent-toolkit/agent_toolkit/_atk/config_settings.py`）を編集する前 | `agent-toolkit:atk-mode/references/model-settings.md` |
