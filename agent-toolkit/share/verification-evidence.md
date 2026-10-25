# 検証結果と手動観測の保存

実装するメインとレーン担当が変更範囲を検証して手動で観測し、その結果を返却の`検証結果`と進捗ログへ残す共通手順である。手動観測の前は「手動観測の保存」、`検証結果`を書く前は「完成条件証拠の検証記録」を読む。工程の認可、実装とレビューの分担は呼出側の契約を使う。
最終観測の取得順序は`agent-toolkit:commit`の`SKILL.md`「検証」、共有する検証のまとめ実行と保存結果の被覆判定は`agent-basics:check-execution`の`references/verification-scope.md`「実行条件と再実行」に従う。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 受入シナリオを起動する前 | `../skills/check-execution/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/references/acceptance-scenarios.md` |
| 訂正済み検証表を使う前 | `../skills/plan-mode/references/plan-file-standards.md#「検証と終端工程」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「検証と終端工程」` |
| 保存出力を取得する前 | `../skills/check-execution/SKILL.md#「保存した実行出力の取得」` | 判断 | `section:agent-toolkit:*/skills/check-execution/SKILL.md#「保存した実行出力の取得」` |
| 取得版の異なる観測を検証結果へ書く前 | `../skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| レビューへ渡す最終観測を取得する前 | `../skills/commit/SKILL.md#「検証」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「検証」` |
| 保存済みの成功集合と限定再検証から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |

## 手動観測の保存

受入シナリオか完成条件を手動で観測する直前に、受入シナリオ検証（`agent-toolkit:check-execution`の`references/acceptance-scenarios.md`）の起動条件で対象を起動する。コマンドはハーネスの標準実行・背景実行・待機機能で対象worktreeから実行する。

実行レビュー担当など別の担当が出力の全量を読む観測は、自分のセッションのmanaged-tempへ`mktemp -d '<セッションのmanaged-temp>/<用途>-XXXXXXXX'`で作成したディレクトリへ標準出力と標準エラーを別々のファイルで保存する。同じ観測の終了コード、cwd、argv、取得時の`git rev-parse HEAD`の完全OIDと`git status --porcelain=v1`の結果を、検証結果へ書く。保存に失敗した観測と、終了コードか取得時のHEADを記録していない観測は、保存する形でやり直す（実行レビュー担当が読めるのは保存された観測だけである）。PATH上のコマンドなどインストール済みの別実体を実行した観測は、その実体の版を記録する。
保存したディレクトリは、その検証結果を使う実行レビューと統合の受領が終わってから、作成した担当が`rm -rf -- <作成した絶対パス>`で回収する。

## 完成条件証拠の検証記録

実行レビューが完成条件証拠を判定する対象では、実装担当とメインが実装完了とレビュー修正の返却へ`検証結果`を書き、同じ内容を進捗ログ（計画なしでは引き継ぎ記録）へ記録する。実行レビュー担当はこの記録を判定の材料にし、判定は同担当だけが書く（`agent-toolkit:review-standards`の`references/exec-review-recording.md`）。

- 計画の`## 検証`（訂正済み検証表があればその表）の各コマンドを、対象worktreeで標準実行する。計画なしでは引き継ぎ記録で確定した検証コマンドを使う
- コマンドごとに、コマンド、cwd、終了コード、判定に使った出力の要点（失敗・警告・対象到達・件数）と、検証した版の完全OID（`git rev-parse HEAD`）を書く。未commitの変更を含む状態で検証した場合はその`git status --porcelain=v1`も書き、全量を保存した場合は両出力の絶対パスを添える
- 受入シナリオごとに、公開された呼び出し手段・入力・期待結果に対応する実結果として、テスト完全名と結果、または保存した手動観測の入力・操作・期待結果・観測結果の所在を書く。pytestのテスト名は、同じ版の`-v`付き出力にその名前と結果の行があることを確かめる。点表示と総数は個別の合否の根拠にならない
- 検証済み・未実施・条件変更の判断を分けて書く。後続工程や不採用・割当の項目には、検証済みの範囲と採否・後続工程を記録した箇所を書く。計画確定時に決まった後続工程は計画の`## 実施内容`、実装中に作成した後続工程は対象AWIへの追記先、同じセッション内で読まれるものは引き継ぎ記録を示す
- 判定（達成・未達・根拠不足）の記入は実行レビュー担当に任せる

各記録には、その要求に直接必要な観測と現在の成果物へ適用できる理由を書く。旧取得版の観測が現在も必要なら、取得時のHEADと適用理由を保持し、失効した説明や別要求の経緯は書き換える。書き分けの詳細は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。
修正ラウンドの経緯・応答・履歴統合はレビュー指摘管理表と観測記録へ残し、`検証結果`には判定に必要な所在と観測内容を対応付ける。
複数要求が同じログを使う場合も、各要求に該当する観測の所在と内容を書き分ける。
不足する観測は自身の操作の範囲で補い、入力・権限・外部状態を要する不足は続行できない理由で返す。
レビュー修正と履歴統合の後は、修正が作用する検証を新しいHEADで実行し直して`検証結果`を更新する。結果に作用する差が無い検証は、取得時のHEADと適用理由を書いて再利用する。CI記録だけのレビューで証拠要求が無い場合も、実行した検証の`検証結果`は同じ形で書く。
