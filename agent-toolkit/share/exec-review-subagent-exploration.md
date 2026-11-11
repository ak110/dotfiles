# 実行レビュータスク：判定の観点と並行する探索

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 本文の起動指定に従って作成規範を取得する前 | `agent-basics:writing-standards/SKILL.md` | | | |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` | | | |
| 条件表を検収する前 | `exec-review-recording-contract.md` | | | |
| 初回レビューに入る前 | `exec-review-criteria.md` | | | |
| 再レビュー・引き継ぎ再レビューに入る前 | `exec-review-criteria.md` | | | |
| 文章成果物の初回レビュー・再レビュー・引き継ぎ再レビューで、読者別探索の起動または省略を判断する前 | `${CLAUDE_PLUGIN_ROOT}/share/reader-fit-review.parent.md` | | | |
| 差分を判定する前 | `agent-basics:writing-standards`が変更ファイルの成果物種別ごとに定める資料 | | | |
| テストコードを含む差分を判定する前 | `agent-basics:writing-standards/references/testing.md`、`agent-toolkit:commit/references/testing.md` | | | |
| `計画`を受領し、条項を生成する前 | `../skills/write-plan/references/plan-file-standards.md`、`../skills/write-plan/references/plan-file-standards-structure.md`、`../skills/write-plan/references/plan-file-standards-scope.md`、`../skills/write-plan/references/plan-file-standards-requirements.md`、`../skills/write-plan/references/plan-file-standards-design.md`、`../skills/write-plan/references/plan-file-standards-verification.md` | | | |
| `計画`を受領し、計画が過去の契約へ戻す変更、過去の契約を撤去する変更、または保持・不変性・完全復元の契約を含む場合 | `agent-toolkit:write-plan`の`references/plan-restoration-contracts.md` | | | |
| 複数のWIを`atk wi show`で取得する前 | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` | | | |
| 再発防止セルフチェックの観点を収集する前と、担当を起動・受領する前 | `recurrence-self-check.parent.md` | | | |
| 指摘を登録するか完成条件証拠を書く直前 | `agent-basics:review-standards/SKILL.md`、`exec-review-recording-issues.md`、`exec-review-recording-evidence.md`、`exec-review-recording-grounds.md` | | | |
| 着手時 | `exec-review-criteria.md`、`exec-review-criteria-execution.md` | 起動時 | `role:exec-review` | |
| 初回レビューで種類7の作成規範を点検する前 | `agent-basics:writing-standards/references/review-only-criteria.md` | | | |
| 操作や検証手段を選ぶ前 | `agent-toolkit:/share/exec-review-operations.md` | | | |
| レビュー工程の判定・記録を始める前 | `review-table-operations.md` | | | |
| レビュー工程の判定・記録を始める前 | `exec-review-criteria.md` | | | |
| レビュー工程の判定・記録を始める前 | `exec-review-recording.md`、`exec-review-recording-verdicts.md` | | | |

## 判定の観点と並行する探索

**5.** 適用する観点で走査し、独立した探索の結果を受領して判定する。

レビューの各ラウンドでは、`recurrence-self-check.parent.md`に従ってチェック観点を収集する。
`agent-toolkit:/share/exec-review-criteria.md`に基づく走査と並行して、`agents_server`の`task`により再発防止セルフチェック担当を1件起動する。
観点が存在しない場合や、再レビューおよび引き継ぎ再レビューで今回の差分がなくセルフチェック由来の未解決指摘が0件である場合の起動省略、ならびに結果の裏付け確認、採否判定、種類7としての登録手続きについても、`recurrence-self-check.parent.md`に従う。
再レビュー全体の確認範囲は`exec-review-subagent-followup.md`「再レビュー」の規定を維持し、セルフチェックへ渡す今回の差分は`recurrence-self-check.parent.md`で定める比較元から生成する。

レビュー観点には`exec-review-criteria-execution.md`「実行レビューの判定」を適用する。
種類8の走査では、同節の直接影響範囲に関する要点に沿って直接影響範囲を独自に算出し、提出された探索記録や検証結果と突き合わせて漏れがあれば管理表へ記録する。
テストと観測範囲の判定にも、読込表のtesting.mdの基準を適用する。

文章成果物の直接確認と別担当の選択、起動、準備、対象集合の分割は、`reader-fit-review.parent.md`「起動」に従う。
通常のリスクを含め、共通の読者像で確認できる文書群を取りまとめ、ファイルごとの比較資料、検証結果、証拠、識別子を適切に対応付ける。
実行レビューにおける他の段階判定は、各工程で定められた既存の契約を維持する。
この検証は`exec-review-criteria.md`「初回レビュー」の走査と並行して進める。
起動を省略できる差分の条件についても同書の定めに従う。

- Git管理下の比較資料は、`{"repository":"<対象worktreeの絶対パス>","base":"<比較元OID>","head":"<レビュー対象HEAD>","paths":["<リポジトリ相対パス>",...]}`の形式で渡す。
  初回の比較元は今回の開始時点とし、再レビュー時は前回確認版に記録されたレビューHEADとする。
  同一の`paths`を読者ごとの`成果物`（作業ツリーの絶対パス）と対応付け、`修正範囲`には比較元と現行版のOID、および直接影響範囲を明記する。
  新規作成の文書や回答は全文を差分として扱う。
  再レビューでは未走査の範囲や根拠不足の範囲も渡し、`レビュー種別`には`再レビュー`を指定する
- 同一レーン内の複数計画において判定対象を自計画のcommitに限定する場合は、パスごとに自計画の判定対象commitから`git show --format= <commit> -- <パス>`を実行して順次差分ファイルへ保存し、そのパスと保存先ファイルの対応を`判定範囲差分`として同一の比較資料に加える
- Git管理外のファイルは、保存済みの前回本文の複製と現行本文を使用する。
  `atk`経由でのみ取得できる本文については、`reader-fit-review.parent.md`「入力」に従って取得・保存する
- 管理表の前回指摘および回答から該当読者の`未解決事項`を抽出し、自身のセッションのmanaged-temp配下のファイルへ保存してその絶対パスを渡す。
  該当項目がない場合は`なし`を指定する
- 差分外で見つかった既存の不具合は`気付いた改善点:`として扱い、新たな要件の追加についてはその旨を委譲元へ報告して、未達の指摘や未解決件数の集計対象からは除外する
- ユーザーがAWIの`## ユーザーコメント`やUWIの`## 回答`へ担当者宛てに直接記入した質問への回答下書きは、Gitの変更差分に含まれていなくても文章成果物として扱う（AWI本文へ転記された質問は対象外）。
  検証結果に示された下書きの絶対パスを成果物、種別を「質問への回答」、読者を「質問したユーザー」として渡す。
  本担当は事実を断定している記述について根拠と現物を確認し、根拠の欠落があれば種類1として指摘して、修正指示を下書きへ差し戻す。
  回答UWIの保存処理は、レビューが収束した後にメインエージェントが行う

画面差分が存在する場合はユーザビリティレビューを並行実施するため、統合時点で適用される`agent-toolkit:/share/exec-review-criteria.md`の指摘の種類5（完了条件を満たしていない）の登録は、統合段階まで保留する。

## 再発防止セルフチェックの起動と受領

起動・入力・受領と採否・返却契約は手順5で扱う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 実行レビュータスク：再レビューの基準を適用する前 | `exec-review-subagent-followup.md` |
