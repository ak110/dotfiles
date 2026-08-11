# 計画実装型フィードバック

トップレベルの`plan_file`を持つactive feedbackだけを計画実装型とする。本文から型を推測しない。

## 実行wave

readyな計画が1件なら現在のcleanなworktreeで実行する。
完全な一覧には`用途=統合用`、`管理対象領域=なし`、`作成主体=既存`、`回収可否=不可`として含める。
複数件なら変更対象ファイルの重複をwave分割条件にせず、利用可能なwriter枠まで上流追随済みの別worktreeへ1計画ずつ割り当てる。
呼び出し元は管理対象領域内へlane用統合worktreeを作成する。
完全な一覧には用途、絶対パス、HEADの完全OID、管理対象領域の絶対パス、`作成主体=caller`、`回収可否=可`を記録する。
1つのworktreeへ複数のwriterを割り当てず、dirty差分を前提とする計画は並列化しない。

各laneは`agent-toolkit:delegation`に従って`plan-impl-executor`を起動する。
各laneの呼び出し元は`agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`を全文読み、
executorの起動と受領に関するsender契約の正本とする。
readyな計画が1件の場合は、借用する現在worktreeを回収不可として含む完全な一覧を同referenceに従って構成する。
複数laneの場合は、lane用統合worktreeと計画が明示する管理対象worktreeを含む完全な一覧を同referenceに従って構成する。
起動文には計画、プロジェクト規範、worktreeの完全な一覧、feedback filename、追加指示、
許容済みの挙動変化、権限だけを渡し、
実装とreviewのtask本文を複製しない。

laneの結果を受領後、呼び出し元は`atk managed-temp create --prefix <統合用途を示す値>`を単独で実行し、
標準出力の絶対パスを統合用管理対象領域として保持する。
同領域内に統合対応表を作成し、領域の絶対パス、用途、所有主体、回収可否を各lane計画の`## 進捗ログ`へ記録する。

統合対応表はlane項目とレビュー修正項目を判別可能にする。
lane項目はfeedback filename、lane commitの完全OID、計画ファイルの絶対パス、統合順を持つ。
レビュー修正項目は安定ID、関係する全計画パス、指摘ID、適用元OID、再適用後OIDまたは
状態`適用済みスキップ`、統合順を持つ。
新規レビュー修正だけを追加し、既存項目は統合writerの適用結果で置換更新する。

呼び出し元は`git fetch`後の上流最新OIDから同領域内へ統合worktreeを新規作成する。
`atk config get merge_model`を実行し、`runtime-routing.md`「工程別モデル設定」で統合writerの経路を解決する。
統合writerへ`merge-task.md`、統合worktreeと作成時HEADの完全OID、統合対応表、全計画、プロジェクト規範、
author skill、検証コマンド、commit可・push不可・worktree作成回収不可・queue変更不可の権限を渡す。
統合writerはrebaseを行わず、全項目を単一cherry-pickシーケンスで適用する。

複数laneを統合した場合は、内容変化の有無にかかわらず`plan-impl-executor`の統合後レビュー調整モードで
最終HEADへの二系統レビューを実施する。
単一laneのレビュー済みcommitを同一treeのまま使う場合だけ省略できる。
executorは採用指摘の6列表を統合用管理対象領域内へ保存し、絶対パスと実在・分量証跡を返す。
呼び出し元は内容を読まず、再統合と再レビューへ絶対パスだけを渡す。

push直前のfetch照合、pushのdry-run、実pushで上流進行またはnon-fast-forward拒否を観測した場合は再統合する。
統合対応表へ新規レビュー修正commitを追加し、既存レビュー修正項目を適用結果で更新してから旧worktreeを回収する。
新しい上流最新OIDから統合worktreeを再作成し、全項目の単一cherry-pickシーケンス、検証、最終二系統レビューを再実施する。
前回の採用指摘と差分は既存6列表ファイルのパスで引き継ぐ。

終了条件は実push成功と当該OIDのCI通過とする。
完了後に`atk mq adopt`を実行し、成功したlane worktreeを回収する。
統合用管理対象領域はadopt完了後にだけ`atk managed-temp cleanup --path <検収済み絶対パス>`で回収する。
中断後はqueueの`plan_file`から各計画の進捗ログを辿り、領域の実在、所有、対象feedbackとの対応を照合して再利用する。
照合できない旧領域は保持し、統合を最初からやり直す。

失敗したlaneは調査可能な状態で保持する。ユーザー合意を覆す判断は自動反映せずTBDへ送る。
