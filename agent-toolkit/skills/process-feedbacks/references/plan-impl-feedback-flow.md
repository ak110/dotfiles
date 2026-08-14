# 計画実装型フィードバック

トップレベルの`plan_file`を持つactive feedbackだけを計画実装型とする。本文から型を推測しない。
計画実装型を処理する呼び出し元は、通常開始又は中断後再開の最初の委譲前に`agent-toolkit:delegation`をSkill機能で起動する。

## 実行wave

同じ計画ファイル（同じ`plan_file`）を持つready項目を1 laneとし、1 laneは1つの計画ファイルを表す。
readyなlaneが1件なら現在のcleanなworktreeで実行する。
完全な一覧には`用途=lane`、`管理対象領域=なし`、`作成主体=既存`、`回収可否=不可`として含める。
複数laneなら変更対象ファイルの重複をwave分割条件にせず、利用可能なwriter枠まで上流追随済みの別worktreeへ1計画ファイルずつ割り当てる。
呼び出し元は管理対象領域内へlane worktreeを作成する。
完全な一覧の記録属性は`plan-impl-caller-reception.md`を正本とする。
1つの計画ファイルに属する実装単位は、1 writerが同じworktreeで順次実装する。
異なる計画ファイルのlaneだけを別worktreeで並列化し、dirty差分を前提とする計画は並列化しない。

各laneの呼び出し元は、executorの起動前に`agent-toolkit:delegation`をSkill機能で起動する。
各laneは`plan-impl-executor`を起動する。
各laneの呼び出し元は`agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`を全文読み、
executorの起動と受領に関するsender契約の正本とする。
readyな計画が1件の場合は、借用する現在worktreeを回収不可として含む完全な一覧を同referenceに従って構成する。
複数laneの場合は、lane worktreeと計画が明示する管理対象worktreeを含む完全な一覧を同referenceに従って構成する。
起動文には計画、プロジェクト規範、worktreeの完全な一覧、ソート済みfeedback filename一覧、追加指示、
許容済みの挙動変化、権限だけを渡し、
実装とreviewのtask本文を複製しない。
feedback filename一覧が0件の場合はlaneを起動しない。
1件の場合も一覧として渡し、複数件の場合は項目をfilename昇順に保つ。

laneの結果を受領後、呼び出し元は`atk managed-temp create --prefix <統合用途を示す値>`を単独で実行し、
標準出力の絶対パスを統合用管理対象領域として保持する。
同領域内に統合対応表を作成し、領域の絶対パス、用途、所有主体、回収可否を各lane計画の`## 進捗ログ`へ記録する。

統合対応表はlane項目とレビュー修正項目を判別可能にする。
lane項目はソート済みfeedback filename一覧、lane commitの完全OID、計画ファイルの絶対パス、統合順を持つ。
レビュー修正項目は安定ID、関係する全計画パス、指摘ID、適用元OID、再適用後OIDまたは
状態`適用済みスキップ`、統合順を持つ。
新規レビュー修正だけを追加し、既存項目は統合writerの適用結果で置換更新する。

呼び出し元は`git fetch`後の上流最新OIDから同領域内へ統合worktreeを新規作成する。

### 統合writerの起動

統合writerの各新規起動又は継続接続の直前に`atk config get merge_model`を実行し、
`runtime-routing.md`「工程別モデル設定」で経路を解決する。
統合writerへ`merge-task.md`、統合worktreeと作成時HEADの完全OID、統合対応表、全計画、プロジェクト規範、
author skill、検証コマンド、commit可・push不可・worktree作成回収不可・queue変更不可の権限を渡す。
統合writerはrebaseを行わず、全項目を単一cherry-pickシーケンスで適用する。

初回統合では、統合worktreeの作成後に本節の手順で統合writerを起動する。

各laneのcommitはlane内で二系統レビュー済みのため、統合後のレビューは競合を解消した箇所だけを対象とする。
競合を解消せずに全項目を適用できた場合は統合後のレビューを実施しない。
競合を解消した場合は、解消した箇所と同一ファイルの隣接する記述だけを対象として
`plan-impl-executor`の統合後レビュー調整モードを起動し、累積差分全体の再レビューを求めない。
起動時は共通入力として、モード指定`統合後レビュー調整モード`、プロジェクト規範、追加指示、
許容済みの挙動変化、git操作の制約を渡す。
モード固有入力は、統合worktree、最終HEADの完全OID、競合を解消したファイルと箇所、
統合対応表に含まれる全計画の絶対パス、統合スレッドの検証結果、統合用管理対象領域の絶対パス、
再レビュー時は既存6列表ファイルの絶対パスだけとする。
executorは採用指摘の6列表を統合用管理対象領域内へ保存し、絶対パスと実在・分量証跡を返す。
呼び出し元は内容を読まず、再統合と再レビューへ絶対パスだけを渡す。

push直前のfetch照合、pushのdry-run、実pushで上流進行またはnon-fast-forward拒否を観測した場合は再統合する。
統合対応表へ新規レビュー修正commitを追加し、既存レビュー修正項目を適用結果で更新してから旧worktreeを回収する。
新しい上流最新OIDから統合worktreeを再作成し、本節の手順で統合writerを起動する。
全項目の単一cherry-pickシーケンスと検証を再実施し、レビューは前掲の競合解消箇所限定の条件で判定する。
前回の採用指摘と差分は既存6列表ファイルのパスで引き継ぐ。

終了条件は実push成功と当該OIDのCI通過とする。
終端工程を持つ項目は、終了条件の後かつadoptの前に本文が列挙した終端工程を統合順で1回だけ実行する。
完了後に成功した項目へ、ソート済みfeedback filename一覧の順で既存の`atk mq adopt`を1件ずつ実行する。
各保存結果を照合した後に、成功したlane worktreeを回収する。
統合用管理対象領域はadopt完了後にだけ`atk managed-temp cleanup --path <検収済み絶対パス>`で回収する。
中断後はqueueの`plan_file`から各計画の進捗ログを辿り、領域の実在、所有、対象feedbackとの対応を照合して再利用する。
照合できない旧領域は保持し、統合を最初からやり直す。

失敗したlaneは調査可能な状態で保持する。ユーザー合意を覆す判断は自動反映せずTBDへ送る。
