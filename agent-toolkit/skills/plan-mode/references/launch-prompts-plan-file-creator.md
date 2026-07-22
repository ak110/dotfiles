---
# 同期注記: 「実施済みレビュー結果の転記」欄の`agent-doc-validator`代行規定は、
# `plan-codex-delegate`ブロック時代行パターンと対称に、
# `agent-toolkit/agents/plan-file-creator.md`「エスカレーション基準」節・
# 「実施済みレビュー結果の転記」パラグラフと
# `agent-toolkit/skills/plan-mode/references/codex-review.md`
# 「plan-file-creatorからの起動」節の計4箇所へ意図的に重複させている。改訂時は4箇所を同時更新する。
---

# plan-file-creator起動プロンプト雛形

`agent-toolkit:plan-mode`工程6で`plan-file-creator`を起動する際の起動プロンプトを、
コピー可能な完全コードブロックで集約する。

## 起動プロンプト雛形

    以下の情報をもとに計画ファイルの作成（または改訂）と整合性チェック・codexレビューを
    完遂してください。

    - 計画ファイルパス: {新規作成時は`~/.claude/plans/{stem}.md`、改訂時は既存パス}
    - 工程2〜5で確定した内容: {要件対話の結果・認識合わせの内容・恒久化検討の結果・
      リファクタリング検討の結果を過不足なく列挙。動作確認・検証ステップを計画へ含める場合は、
      対象（ファイル・コマンド等）・判定手段（何を観測して合否を決めるか）・
      不合格時の再検証条件を委譲元が具体化してから埋め込む}
    - ユーザー発話・提示素材との照合結果: {呼び出し元が起動前に実施した点検結果}
    - メイン側実施済み観点の内訳:
      - 機械チェック実施結果: {check_plan_file.pyの実行結果。
        新規作成時は「該当なし（新規作成のため未実施）」と明記する}
      - 遡及スキャン結果: {norm-revision-checklist.md規定に従い実施した結果}
      - 横断grep確認の結果: {実施内容と検出件数}
    - permission_mode: {plan | 非plan}
    - 実施済みレビュー結果の転記（該当時）: {`plan-codex-delegate`起動ブロックにより
      呼び出し元が`mcp__codex__codex`直接呼び出しで代行実施したcodexレビュー結果、
      `agent-doc-validator`起動ブロックにより呼び出し元が
      `subagent_type: agent-toolkit:agent-doc-validator`で代行実施したレビュー結果に加え、
      前回の`plan-file-creator`起動が完了報告へ引き継いだ`plan-reviewer`・`agent-doc-validator`の
      完了報告原文も含める。該当なしの場合は「なし」と明記}
    - 実施範囲: {起草のみ | 起草＋整合性チェック（既定値、省略時はこちら）}

    ## 制約

    - 計画の成否を左右する設計判断・ユーザー確認要事項・レビュー重大指摘の対応方針を
      委譲情報だけで確定できない場合は`needs_escalation`で返却する
    - codexレビューは`plan-codex-delegate`（用途: 計画レビュー）の観点分担並列起動経由で行い、
      `mcp__codex__codex`への直接フォールバックはしない
    - 「実施済みレビュー結果の転記」欄に内容がある場合、当該codexレビューを実施済みとして扱い、
      指摘反映以降の工程から再開する

## 検収手順

呼び出し元は`plan-file-creator`の完了報告を受領した後、次の手順で検収する。

1. `plan_file_path`欄がサンドボックスパスの場合、`Read`で内容を取得し正規パス
   （`~/.claude/plans/{stem}.md`）へ`Write`で反映する。正規パスの場合はそのまま検収する
2. `bump_judgment`・`review_summary`・`check_results`を確認し、`status: needs_escalation`の場合は
   `escalation_points`を解決してから縮減プロンプトで`plan-file-creator`を新規起動する
3. `status: completed`の場合、ユーザー可視のテキスト応答へ計画ファイル絶対パスを1行で明示する
   （`計画ファイル: <絶対パス>`形式）
