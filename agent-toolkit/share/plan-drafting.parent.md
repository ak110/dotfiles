# 計画担当の起動と受領

計画作成を要する工程の開始時に、起動主体が本書を全文読み、計画担当の起動、`計画作成完了`の受領及び指摘の配送へ適用する。本書は計画担当自身が行う手順を定義しない。

## 起動経路の明示

計画作成がフィードバック処理の委譲による場合は、起動文へ`起動経路: agent-toolkit:process-feedbacks`の行を含める。
ユーザーが`agent-toolkit:plan-mode`又は`agent-toolkit:plan-and-add-feedback`を直接起動した経路では当該行を含めない。
計画担当はこの行の有無でユーザーとの共通理解に到達するための確認手順の要否を判別するため、経路にかかわらず同じ文言で明示する。

## 起動

`atk config get plan_model`で実効設定を解決し、新規の計画担当へ次の入力を渡す。

- `${CLAUDE_PLUGIN_ROOT}/share/plan-drafting.subagent.md`の絶対パス
- 対象
- 要求単位の由来
- 採否
- 不採用確認結果
- 対象リポジトリ
- プロジェクト規範
- 作成規範
- フィードバック固有の指示
- 計画ファイルの保存先

## 受領

計画担当から`計画作成完了`を受領したら、計画ファイル（メイン）・計画ファイル（詳細）の絶対パスを検収し、`${CLAUDE_PLUGIN_ROOT}/share/plan-review.parent.md`に従って計画レビュー担当を起動する工程へ進む。

## 指摘の配送

起動主体が計画担当へレビュー指摘を配送する場合は、配送文へ`agent-toolkit:reviewee-standards`のSKILL.mdと`agent-toolkit:review-standards`の判断詳細契約の絶対パスを含める。

計画担当又はレビュー担当からエスカレーションを受領した場合は、工程を中断し、内容だけを`needs_escalation`で呼び出し元へ中継する。回答後は同じthreadへ中継する。
