# 新設と改訂の評価

本書は、エージェント向け文書の新設と改訂のうち、`agent-toolkit:refine-prompt`で評価する場合を定める。
規定の追記と記述量の管理の基準は`agent-basics:writing-standards/references/agent-documents-additions.md`が定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 評価する場合

- スキル、役割文書（`<役割名>.subagent.md`）、常時規範の節を新しく作る場合は、レビューへ渡す前に`agent-toolkit:refine-prompt`を起動し、シナリオを与えて評価する。
  対象は利用頻度の高いスキルと自動化の中核となる文書に限り、起動の費用に見合わない単発のプロンプトは対象外とする。
- 指示と挙動の食い違いを原因分析し、指示文の改訂が必要と確定した場合は、改訂案を同じ方法で評価する。
- 評価の対象はこの2つの場合に限り、どちらにも当たらない既存の規範の改訂は対象外とする。
