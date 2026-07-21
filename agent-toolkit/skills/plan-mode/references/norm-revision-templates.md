# 遡及スキャン結果の書式と機械転記ペア例

`norm-revision-checklist.md`「遡及スキャン結果の記述テンプレート」節および
「機械転記元テンプレート・転記先スキルペアの同時改訂」節から参照されるSSOT。

## 遡及スキャン結果の記述テンプレート

遡及スキャン結果は、textlintのsentence-length違反・jtf-style/1.1.3.箇条書き違反を予防するため
次の書式で記述する。

- 実行したgrepコマンドは`text`コードブロックへまとめて記述する。
  複数コマンドは`# <ラベル>`コメント付きで列挙する。
- 各対象範囲の検出結果は「ラベル: 検出概要（句点なし）」形式の短文箇条書きで列挙する。
  120字を超える説明を含めない。
- 長文説明を要する場合は`text`コードブロック外に別段落として補足する。

## 機械転記ペア例

`norm-revision-checklist.md`「機械転記元テンプレート・転記先スキルペアの同時改訂」節が参照する既知の機械転記ペア例。

- `agent-toolkit/skills/process-feedbacks/SKILL.md`ステップ2.5節と
  `agent-toolkit/skills/process-feedbacks/references/explore-template.md`「Explore委譲雛形」節
- `agent-toolkit/agents/plan-impl-executor.md`が参照する`agent-toolkit/references/plan-impl/execution-process.md`と
  `agent-toolkit/references/plan-impl/launch-prompts-drafting.md`「起草・改訂委譲雛形」節
- `agent-toolkit/skills/plan-mode/references/integrity-checks.md`と
  `agent-toolkit/skills/plan-mode/references/launch-prompts-integrity.md`の各雛形節
