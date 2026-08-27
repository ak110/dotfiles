# feedbacks-plannerの入出力

## 入力

初回起動では、ファイル名昇順の対象一覧と対象リポジトリ、実装順序の向き、キュー外素材の逐語本文と出所、対象worktree、プロジェクト規範、基準となる計画ファイル（メイン）の絶対パス、バグ対応の有無を受け取る。計画を分割する場合は基準パスのstemから`<stem>-NN.md`を導出し、計画ファイル（詳細）、レビュー表及び必要なバグ調査ファイルのパスも同じstemから導出する。

人間由来の直接受領素材は、受領順を保持した素材レコード集合として扱う。各レコードは種別、出所及び引用範囲をこの順で記録し、逐語本文・回答全文をレコードの末尾へ続ける。

`awaiting_confirmation`後の再開起動では、停止済み識別子を再利用せず、初回入力に加えて次を受け取る。

- `original_investigations`: 元のバッチ全項目の調査結果全文
- `raw_sources`: 原文frontmatterの`source`原値。欠落は値なしとする
- `user_decisions`: `agent-toolkit:process-feedbacks`の採否記録契約が定める原文正本IDごとの累積レコード
- `answer_or_tbd`: 出所と引用範囲付きの逐語回答又は保存TBD。未受領のIDも保持する
- `plan_path`: 初回起動と同じ計画ファイルの絶対パス

plugin内のタスク文書と規範スキルの絶対パスは、注入済みの`delegation`スキルから現行plugin rootを確定して解決する。調査結果が対象とするファイル種別に応じて自身が選定する作成規範スキルも、受信者へ渡す前に実在を確認する。解決不能、必須入力の欠落又は職務外の判断が生じた場合は`needs_escalation`で返す。push、フィードバック投入、worktreeの作成と回収は行わない。

## 出力

```text
status: completed | awaiting_confirmation | needs_escalation
decision: <採否記録契約に基づく根拠>
decisions:
- <バッチ全項目のファイル名、素材ID、採否、理由・差異、範囲及びキュー操作判定>
plan: <計画ファイルの絶対パス、実在・分量の証跡、担当項目との対応及び要約。該当しない場合はなし>
review: <計画ファイルごとの収束状態、検査結果、write_status>
tbd:
- <通常の将来判断TBD候補。無ければ「なし」>
user_decisions:
- <採否記録契約が定める累積レコード>
blockers:
- <未完了事項。完了時は「なし」>
confirmation_context:
- original_investigations: <awaiting_confirmation時は必須>
- raw_sources: <awaiting_confirmation時は必須>
- user_decisions: <awaiting_confirmation時は必須>
- answer_or_tbd: <当サイクルの回答又は保存TBD>
- plan_path: <同じ計画ファイルの絶対パス>
```

完了報告には採用・reject対象・hold対象・技術的失敗別のファイル名を含める。reject対象とhold対象はキュー状態を変更せず、メインが検収後に操作する。技術的失敗には事象、期待値、実際値、発生条件、直接的原因、再開に必要な情報及び元のファイル名を含める。

通常の完了報告へ計画全文、調査結果又はレビュー指摘の内訳を含めない。`awaiting_confirmation`では再開に必要な`confirmation_context`を欠落させない。完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。
