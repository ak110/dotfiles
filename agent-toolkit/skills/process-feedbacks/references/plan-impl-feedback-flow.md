# 計画実装型フィードバック

トップレベルの`plan_file`を持つactive feedbackだけを計画実装型とする。本文から型を推測しない。

## 実行wave

readyな計画が1件なら現在のcleanなworktreeで実行する。
複数件なら変更対象ファイルの重複をwave分割条件にせず、利用可能なwriter枠まで上流追随済みの別worktreeへ1計画ずつ割り当てる。
1つのworktreeへ複数のwriterを割り当てず、dirty差分を前提とする計画は並列化しない。

各laneは`agent-toolkit:delegation`に従って`plan-impl-executor`を起動する。
各laneの呼び出し元は`agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`を全文読み、
executorの起動と受領に関するsender契約の正本とする。
readyな計画が1件の場合は、借用する現在worktreeを回収不可として含む完全な一覧を同referenceに従って構成する。
複数laneの場合は、lane用worktreeと計画が明示する管理対象worktreeを含む完全な一覧を同referenceに従って構成する。
起動文には計画、プロジェクト規範、worktreeの完全な一覧、feedback filename、追加指示、
許容済みの挙動変化、権限だけを渡し、
実装とreviewのtask本文を複製しない。

laneの結果を受領後、呼び出し元が次を1件ずつ直列に行う。

1. 最新基準への追随と競合解消
2. executorのcommit、検証、二系統review、完了条件の実測
3. 追随後にcommitが変わった場合の再検証と二系統review
4. pushとCI通過確認
5. `atk mq adopt`と成功したlaneのworktree cleanup

失敗したlaneは調査可能な状態で保持する。ユーザー合意を覆す判断は自動反映せずTBDへ送る。
