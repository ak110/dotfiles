# 継続処理の起動・隔離・制御の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

- [オーケストレーター選択](design-process-loop-orchestrator.md)
- [process-loopのモジュール構成](design-process-loop-modules.md)
- [開始前の活動猶予とUWI回答待ち](design-process-loop-start-gate.md)
- [登録全体の統括と対象別所有](design-process-loop-ownership.md)
- [process-loopのworktree隔離](design-process-loop-worktrees.md)
- [会話IDによる識別と中断要求](design-process-loop-control.md)
- [Codexセッションの終了](design-process-loop-termination.md)
- [次の起動への追加指示](design-process-loop-instructions.md)
