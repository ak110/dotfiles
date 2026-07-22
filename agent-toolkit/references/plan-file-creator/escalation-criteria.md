---
# 同期注記: 本ファイルの`agent-doc-validator`代行規定は、`plan-codex-delegate`ブロック時代行
# パターンと対称に、`agent-toolkit/agents/plan-file-creator.md`「実施済みレビュー結果の転記」パラグラフ・
# `agent-toolkit/skills/plan-mode/references/codex-review.md`「plan-file-creatorからの起動」節・
# `agent-toolkit/skills/plan-mode/references/launch-prompts-plan-file-creator.md`「起動プロンプト雛形」節の
# 計4箇所へ意図的に重複させている。改訂時は4箇所を同時更新する。
---

# plan-file-creatorのエスカレーション基準の詳細

`agent-toolkit/agents/plan-file-creator.md`「エスカレーション基準」節の詳細手順を集約する。

次のいずれかに該当する場合、直接補正で解消せず`needs_escalation`で呼び出し元へ返却する。

- 計画の成否を左右する設計判断（構成要素の配置先・責務帰属・データの格納先・方式選択等）を
  委譲情報だけで確定できない場合
- レビュー・codex指摘の対応方針についてユーザー確認が必要と判断した場合
  （`codex-review.md`「codexレビューの進め方」節の確認要件に該当する場合を含む）
- `plan-codex-delegate`起動がauto mode下でブロックされ、`mcp__codex__codex`直接フォールバックを
  自身で行わない方針により継続不能な場合。方針の典拠は
  `agent-toolkit/agents/plan-file-creator.md`のfrontmatterコメントを参照する
- `agent-doc-validator`起動がauto mode下でブロックされ、直接フォールバックを
  行わない方針により継続不能な場合。代行規定は
  `agent-toolkit/skills/plan-mode/references/codex-review.md`「plan-file-creatorからの起動」節配下の
  `agent-doc-validator`代行規定を参照する
- background並列起動下でサブエージェントの応答が得られない場合
  （催促・状態照会後も応答不能・タイムアウト等により正規経路での完遂が阻害される場合）

`needs_escalation`時は論点・観測事実・暫定案を`escalation_points`欄へ明記する。
加えて受領済みの全レビュー結果（`plan-reviewer`・`agent-doc-validator`・codexの各完了報告の原文）を
完了報告本文へ引き継ぐ。呼び出し元は再委譲時にこれを
「実施済みレビュー結果の転記」欄へ機械転記し、再起動後の指摘反映で全指摘を再現可能にする。
呼び出し元は返却論点のみを解決し、確定方針込みの縮減プロンプトで`plan-file-creator`を新規起動する。
`plan-codex-delegate`起動ブロックによる`needs_escalation`の場合、呼び出し元が
`mcp__codex__codex`直接呼び出しでcodexレビューを代行実施する。
代行実施したレビュー結果を「実施済みレビュー結果の転記」欄へ記載し、`plan-file-creator`を再起動する。
再起動された本エージェントは転記結果を実施済みとして扱い、指摘反映（進め方5.）以降から再開する。
`agent-doc-validator`起動ブロックによる`needs_escalation`の場合、呼び出し元が
`subagent_type: agent-toolkit:agent-doc-validator`をAgentツールで起動する。
代行実施したレビュー結果も同様に「実施済みレビュー結果の転記」欄へ記載し、`plan-file-creator`を再起動する。
