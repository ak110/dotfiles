# pickerの起動と受領

①の開始時に、process-feedbacksのメインが本書を全文読み、選定モデルの起動、出力の検収及びキューの占有へ適用する。
本書は全体の処理対象を選定させる契約であり、通常レーンの計画起草や実装の手順を定義しない。

## 起動

メインはキュー一覧を自ら取得せず、pickerへ選定させる。
`atk config get pick_feedbacks_model`を起動直前に実行し、`agent-toolkit:delegation`の工程別モデル設定に従って選択モデルを直接起動する。
`feedbacks-planner`を全体選定の中間調整主体にしない。

pickerへ次の入力だけを渡す。

- `${CLAUDE_PLUGIN_ROOT}/share/pick-feedbacks.subagent.md`の絶対パス
- 対象リポジトリの絶対パス
- プロジェクト規範の絶対パス

ユーザーが本スキルの起動時又は起動後の追加指示で処理対象のフィードバックを明示した場合は、pickerを起動せず当該指示が挙げる項目だけを処理する。処理区分の判定とレーン分けはメインが直接行う。

## 中断再開項目の残作業調査

`processing`状態のフィードバックは、過去の`process-feedbacks`が中断して残した項目である。
メインは、ユーザーが処理対象を明示した経路とpickerが選定した経路のいずれでも、`processing`状態の項目を②へ渡す前に次を調査して残作業を確定し、中断地点から再開して完遂させる。

- frontmatterの`plan_file`が指す計画ファイルの進捗ログと未完了の工程
- `target_commit`以降の対象リポジトリのコミットに含まれる、当該項目の反映済み範囲
- レーン用worktreeとbranchの残存、及びそこに残る未コミットの差分

pickerは選定時点の保存状態だけを返し、この調査を担当しない。

## 出力の受領

pickerは`${CLAUDE_PLUGIN_ROOT}/share/pick-feedbacks.subagent.md`が定める形式で返す。
メインは出力のファイル名、選定時点の状態、区分、依存、確認境界及び固有順序を検収してから「処理開始」節へ進む。
`needs_escalation`の場合は、pickerの結果を変更せず、確認又はTBDの回答を受領して一般的な同一担当継続契約へ渡す。

全要求不採用と確定できるエージェント由来項目は、メインが`atk mq reject <filename>`を実行し、保存結果を照合する。
人間由来要求の全部又は一部の不採用、公開契約変更及びユーザー選好はpickerが`needs_escalation`で返し、メインが確認する。

同一セッション中にTBDの回答を受領した場合は、メインが回答をTBDへ保存し、TBDを先に終端して元項目の依存解除を確認する。
routeとtaskが有効なら同じpicker threadを再開し、その他は一般的な継続契約に従う。別セッションまで機械的に待たない。

## 処理開始

メインは出力の検収を完了した直後に、pickerが選定時点の状態を`inbox`と報告したファイル名だけを引数として
`atk mq start-processing <filename>... --target-repo=<repo>`を1回実行する。
選定時点で既に`processing`だった再開項目は、この引数へ含めない。
実行後は`atk mq list --target-repo=<repo> --skip-pull`で対象の全件が`processing`へ配置されたことを確認して①を完了する。
警告、失敗又は部分状態を検出した場合は対象を再取得し、意図した状態なら再実行せず、部分状態又は原因不明なら計画ファイル、managed-temp、worktree及び実装担当の起動を含む②へ進まない。
起動時に固定した集合だけを、そのセッションの処理対象とする。
②の全レーンが後始末まで完了した後もready一覧を再取得せず、③へ進む。
