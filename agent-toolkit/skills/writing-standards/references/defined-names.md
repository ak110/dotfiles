# 名前の方針と定義済みの名前の一覧

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/defined-names.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/defined-names.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/defined-names.md` |

## 一覧

識別子で書く対象（スキル名、コマンド名、ファイル名など）は本書へ載せない。
「指示対象」列は名前を見分ける短い見出し語であり、定義は定義元の列が示す所在だけが持つ。定義元の列は、先頭のリポジトリ相対パスと節名で示す。`defined_names_invariant_test.py`が各行の名前が定義元に現れることを確かめる。

| 名前 | 指示対象 | 定義元 |
| --- | --- | --- |
| ユーザー | エージェントへ要求・指示・判断を与える人 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| エージェント | ユーザーの要求に応じて調査・判断・作業をするコーディングエージェント（メインエージェント・サブエージェント・委譲先） | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| エンドユーザー | 成果物を使う人 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| 選定工程 | process-wiの工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レーン工程 | process-wiの工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 公開工程 | process-wiの工程 | `agent-toolkit/skills/commit/references/publish.md`冒頭 |
| 処理対象WI | 1回の実行で処理するAWI | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レーン担当 | レーン工程の担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レビュー修正担当 | レーン担当の担当種別 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| CI修正担当 | レーン担当の担当種別 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 終端担当 | 公開工程の担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 監査担当 | 自動コードレビュー監査の委譲先 | `agent-toolkit/skills/process-wi/references/review-audit-launch.md`「自動コードレビュー監査」 |
| 自動コードレビュー | 外部サービスのレビュー | `agent-toolkit/skills/process-wi/SKILL.md`「自動コードレビュー監査」 |
| WI作成 | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 計画 | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 実行 | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 実行レビュー | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 計画担当 | 計画工程の担当 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 実装担当 | 実行工程の担当 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 全体検証 | 検証の範囲 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 受入シナリオ検証 | 検証の種類 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 公開工程判定 | 完成条件の判定 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 直接影響範囲 | 変更の影響範囲 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「要件・外部仕様」 |
| 変更範囲の検証 | 検証の範囲 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「検証と終端工程」 |
| 統合時の完成条件判定 | 統合前の判定 | `agent-toolkit/skills/review-standards/references/exec-review-recording.md`「統合時の完成条件判定」 |
| 初回レビュー | 実行レビューの回 | `agent-toolkit/share/exec-review.subagent.md` |
| 再レビュー | 実行レビューの回 | `agent-toolkit/share/exec-review.subagent.md` |
| 引き継ぎ再レビュー | 実行レビューの回 | `agent-toolkit/share/exec-review.subagent.md` |
| 実行レビュー担当 | 実行レビューの委譲先 | `agent-toolkit/share/exec-review.subagent.md` |
| ユーザビリティレビュー | 画面のレビュー | `agent-toolkit/share/usability-review.parent.md` |
| 画面差分 | 画面の変更 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 並列画面レビュー | 画面差分のある運用 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| WI投入担当 | WI作成の委譲先 | `agent-toolkit/share/add-wi.subagent.md` |
| 投入前レビュー | WI原稿の保存前レビュー | `agent-toolkit/share/wi-draft-review.parent.md`冒頭 |
| 一括置換後レビュー担当 | 一括置換の委譲先 | `agent-toolkit/share/bulk-replace-review.subagent.md` |
| 投稿前レビュー担当 | 外部投稿の委譲先 | `agent-toolkit/share/external-write-review.subagent.md` |
| 調査担当 | 原因調査の委譲先 | `agent-toolkit/share/defect-investigation.subagent.md` |
| 読者別探索担当 | 読者適合の委譲先 | `agent-toolkit/share/reader-fit-review.subagent.md` |
| 再発防止セルフチェック | 具体的な再発防止観点を差分へ当てはめる工程 | `agent-toolkit/share/exec-review.subagent.md`「再発防止セルフチェックの起動と受領」 |
| 再発防止セルフチェック担当 | 再発防止観点の判定の委譲先 | `agent-toolkit/share/recurrence-self-check.subagent.md`冒頭 |
| 再発防止セルフチェック観点 | レビュー時に差分へ当てはめる項目 | `agent-toolkit/share/recurrence-self-check-items.md`冒頭 |
| プロンプト評価担当 | refine-promptの委譲先 | `agent-toolkit/share/refine-prompt.subagent.md`冒頭 |
| 説明担当 | 選定結果の説明の委譲先 | `agent-toolkit/skills/process-wi/references/selection-explanation.md` |
| ユーザー確認 | ユーザーへの行為 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| ユーザーへの報告 | ユーザーへの行為 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| 要件・意図の曖昧さ | 確認を要する事項 | `agent-toolkit/skills/user-confirmation-and-report/SKILL.md`「確認要否の判定」 |
| 3段判定 | 判断の順序 | `agent-toolkit/rules/01-agent.md`「QCDと3段判定」 |
| 委譲の要否判定 | 委譲の判定 | `agent-toolkit/skills/delegation/references/routing.md` |
| 元担当 | 既存の委譲先 | `agent-toolkit/skills/delegation/references/codex-runtime.md`「後続操作の共通先行条件」 |
| 独立文脈レビュー | レビューの形態 | `agent-toolkit/skills/delegation/references/routing.md`「会話を引き継ぐ委譲」 |
| 委譲元 | 委譲先を起動したエージェント | `agent-toolkit/skills/delegation/SKILL.md` |
| 委譲先 | 委譲元が起動したエージェント | `agent-toolkit/skills/delegation/SKILL.md` |
| 委譲プロンプト | 委譲の指示 | `agent-toolkit/skills/delegation/references/base-contract.md` |
| 返却値 | 委譲先の出力 | `agent-toolkit/skills/delegation/references/base-contract.md` |
| エージェント向け文書 | エージェントが直接読み込む文書の種別 | `agent-basics/skills/writing-standards/references/agent-documents-basics.md`冒頭 |
| 読込表 | 文書内の表 | `agent-basics/skills/writing-standards/references/agent-documents-basics.md`「読込表」 |
| 配送範囲表 | 文書内の表 | `agent-toolkit/skills/writing-standards/references/delivery-scope.md`冒頭 |
| 常時規範 | 規範の種別 | `agent-basics/skills/writing-standards/references/agent-documents-basics.md`「置き場所と配送」 |
| プロジェクト規範 | 規範の種別 | `agent-basics/skills/writing-standards/references/agent-documents-basics.md`「置き場所と配送」 |
| プロジェクト方針 | 方針の範囲 | `agent-basics/skills/writing-standards/references/agent-documents-basics.md`「置き場所と配送」 |
| 作成規範 | 品質基準 | `agent-toolkit/skills/review-standards/SKILL.md`「対象成果物の作成規範」 |
| 完成条件 | WIの条件 | `agent-toolkit/skills/wi-standards/references/awi-body.md`「通常AWIの本文」 |
| 完了条件 | 計画の条件 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「要件・外部仕様」 |
| レビュー指摘管理表 | 実行レビューの指摘と応答を記録する表 | `agent-toolkit/skills/review-standards/SKILL.md`「レビュー指摘管理表の共通操作」 |
| CI対応レビュー指摘管理表 | 計画の無いCI失敗のレビュー指摘管理表 | `agent-toolkit/skills/review-standards/SKILL.md`「レビュー指摘管理表の共通操作」 |
| セッション記録 | 会話の記録 | `agent-toolkit/skills/writing-standards/references/session-records.md`冒頭 |
| メイン記録 | 会話の記録 | `agent-toolkit/skills/writing-standards/references/session-records.md`「Claude Codeの記録」 |
| サブエージェント記録 | 会話の記録 | `agent-toolkit/skills/writing-standards/references/session-records.md`「Claude Codeの記録」 |
| 計画バンドル | 計画のファイル群 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`冒頭の呼称の表 |
| 計画の所有記録 | 計画の局所状態 | `agent-toolkit/skills/plan-mode/references/plan-file-storage.md`「計画ファイルの保存と参照」 |
| バックグラウンドタスク | 非同期の処理 | `agent-toolkit/skills/writing-standards/references/session-state-and-flags.md` |
| バックグラウンドタスクの所有記録 | セッション状態の記録 | `agent-toolkit/skills/writing-standards/references/session-state-and-flags.md` |
