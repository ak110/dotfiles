# 計画実装型フィードバック

トップレベルの`plan_file`を持つactive feedbackだけを計画実装型とする。本文から型を推測しない。

## 実行wave

readyな計画が1件なら現在のcleanなworktreeで実行する。
複数件なら変更対象ファイルの重複をwave分割条件にせず、利用可能なwriter枠まで上流追随済みの別worktreeへ1計画ずつ割り当てる。
1つのworktreeへ複数のwriterを割り当てず、dirty差分を前提とする計画は並列化しない。

各laneは`agent-toolkit:delegation`に従って`plan-impl-executor`を起動する。
起動文には計画、worktree、プロジェクト規範、feedback filename、権限だけを渡し、
実装とreviewのtask本文を複製しない。

laneの結果を受領後、呼び出し元が次を1件ずつ直列に行う。

1. 最新基準への追随と競合解消
2. executorのcommit、検証、二系統review、完了条件の実測
3. 追随後にcommitが変わった場合の再検証と二系統review
4. pushとCI通過確認
5. `atk mq adopt`と成功したlaneのworktree cleanup

失敗したlaneは調査可能な状態で保持する。ユーザー合意を覆す判断は自動反映せずTBDへ送る。
