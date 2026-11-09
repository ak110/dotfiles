# メインによるWI選定とレーン分け：集合確定直後の処理開始

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 目標起点の判断を行う前 | `lanes-goals.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes-goals.md` | |
| 是正要求を分類する前 | `immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` | |
| 処理対象WIについて、包含、除外または構成の理由をユーザーが尋ねたとき | `selection-explanation.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-explanation.md` | |
| 回答済みUWIが元項目を指し、同じ元項目を指す未回答のactiveなUWIが残る時と、包含する候補へ依存を設定するかを判定する時 | `agent-toolkit:wi-standards`の`references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` | |

## 集合確定直後の処理開始

**30.** 候補と選定の集合が確定したら、選定YAMLの保存、チェック、レーン起動を待たずに本節の手順を行う。

**31.** 開始時の候補と選定完了前の明示的な追加WIを合わせた処理可能集合が、`選定`のファイル名集合と完全に一致し、各ファイル名が1回のみ出現し、複数レーンに重複して割り当てられていないことを確認する。

 初期選定・追加選定では、次の手順の前に`atk wi show <選定の全ファイル名> --target-repo=<対象worktreeの絶対パス> --skip-pull`を1回実行する。
終了コードが0で、保存済みの`target_repo`と対象作業ツリーのoriginが一致していることを確認する。
originを解決できない旨の診断が出た場合は不足しているoriginまたは入力を補い、不一致の診断が出た場合は`selection-procedure-candidates.md`「処理対象の決定」のリポジトリ移転判定と編集手順に戻る。
後続の`adopt`、`unhold`、`start-processing`を実行する際も、状態を変更する前に両識別子の一致を確認する。

 1. 処理対象に含めた作業を伴わない回答済みUWIを、`atk wi adopt <filename>... --target-repo=<対象リポジトリの絶対パス>`で終了処理する。
 2. 回答済みUWIが参照している元項目のうち、旧運用で`hold`状態にあるものを、`atk wi unhold <filename>... --target-repo=<対象リポジトリの絶対パス>`で`inbox`状態に戻す。
 3. `inbox`状態にある`選定`対象の項目、および`unhold`した元項目を、それぞれ1回ずつ`atk wi start-processing <filename>... --target-repo=<対象リポジトリの絶対パス>`で`processing`状態へ移す。

**32.** 成功を確認した状態遷移コマンドは再実行せず、引数にはまだ`processing`状態でない項目だけを指定する。
各状態変更の成功報告と警告の不在を確認し、部分的な状態不一致や原因不明の失敗が発生した場合は、追加の状態遷移をせずに`続行できない理由`を記録する。

**33.** 一括処理開始の状態変更後に失敗したら、変更後のキュー・差分・履歴・upstreamから、全対象の`state=processing`、対象遷移の未commit差分なし、今回集合の開始を記録した遷移commitあり、remote設定時はそのcommitがupstreamに包含、の全条件を確かめる。
全て成立した場合だけ開始済みとし、欠ければ項目別に再実行せず未完了で止める。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| メインによるWI選定とレーン分け：処理対象の決定の基準を適用する前 | `selection-procedure-candidates.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure-candidates.md` |
