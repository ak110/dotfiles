# 検証結果と手動観測の保存：手動観測の保存

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| OSを模擬した試験の保証と環境条件を記録する前 | `agent-basics:writing-standards/references/testing-selection.md`、`agent-basics:writing-standards/references/testing-platforms.md` |
| 受入シナリオを起動する前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` |
| 訂正済み検証表を使う前 | `../skills/write-plan/references/plan-file-standards-verification.md` |
| 保存出力を取得する前 | `check-execution-operations-output.md` |
| 取得版の異なる観測を検証結果へ書く前 | `exec-review-recording-grounds.md` |
| レビューへ渡す最終観測を取得する前 | `../skills/commit/references/commit-verification.md` |
| 保存済みの成功集合と限定再検証から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` |

## 手動観測の保存

受入シナリオまたは完成条件を手動で観測する直前に、受入シナリオ検証（`agent-toolkit:delegation/references/acceptance-scenarios.md`）の起動条件に従って対象を起動する。
コマンドの実行は、実行ハーネスが提供する標準の実行・背景実行・待機機能を用いて対象worktreeから実施する。

手動観測と通常の検証のどちらも、後続のレビューや統合で結果を使う場合は、実行前に「検証時本文の保存」を適用する。

実行レビュー担当者など別の担当者が出力の全量を確認する必要がある観測では、自身のセッションのmanaged-temp配下に`mktemp -d '<セッションのmanaged-temp>/<用途>-XXXXXXXX'`でディレクトリを作成し、標準出力と標準エラー出力を別々のファイルへ保存する。
その観測における終了コード、cwd、argv、取得時点の`git rev-parse HEAD`による完全OID、および`git status --porcelain=v1`の結果を検証結果に記載する。
保存に失敗した観測や、終了コードまたは取得時のHEADが記録されていない観測は、正しく保存できる形で再実行する（実行レビュー担当者は保存された観測結果のみを検証対象として参照できる）。
PATH上のコマンドなど、インストール済みの別バイナリを実行した観測については、その実体のバージョンも記録する。
保存したディレクトリは、その検証結果を利用する実行レビューおよび統合の受領処理が完了した後、作成した担当者が`rm -r -- <作成した絶対パス>`を実行して確実に回収する。

## 検証時本文の保存

検証を実行する担当は、実行前に取得HEADの完全OID、変更状態、および検証対象と直接影響範囲を確定する。
未コミットの変更を含む場合は、その範囲の実行時本文を復元できる保存物を自身のmanaged-temp内に確保し、後続のレビュー担当・レーン担当が出力と対象版を対応付けられるようにする。
追跡済みの変更は取得HEADを基点とする標準Git差分（バイナリを含む場合は`git diff --binary <取得HEAD> -- <対象パス...>`）などで保存し、差分に含まれない未追跡ファイルの本文は相対パスを保った複製などで補う。
削除・改名・実行属性も含め、その保存物と取得HEADから検証時の対象内容を復元できることを確認する。
Git管理外の設定・入力を変更して使う場合も、必要な本文を同様に保存する。
ただし、作業ツリー外の認証情報ファイルは複製せず、既存の取扱規定を守る。
同じ内容で複数のコマンドを実行するときは、同じ保存物を参照できる。
cleanなcommitの本文はOIDで再取得し、本文の保存物を省略できる。
実行時本文の同定には本文を復元できる保存物を使い、パス一覧、status、ハッシュ値、検証後のcommitは補足として扱う。
リポジトリ全体の複製、履歴を変更する退避、全コマンドの自動snapshotは追加しない。

実行中は検証対象と直接影響範囲の編集を止め、実行後は保存物との実際の差分から本文の不変を確かめる。
formatterやcommit hookによる更新も同じ比較に含める。
実行中に本文が変わり、検証した内容を確定できない結果は、終了後の本文保存で補ったことにせず、影響する検証範囲の追加観測へ戻す。
保存物が欠けている場合も、特定できない本文と影響する範囲を示し、その範囲だけを追加観測する。

実行後の`検証結果`には保存物の絶対パスと復元方法、取得HEAD、変更状態、cwd・argv・入力・環境条件、両出力の保存先と終了コードを対応付ける。
cleanな版は本文保存物の代わりにそのOIDを記す。
後続担当へ保存物を引き渡し、参照するレビュー・統合の受領処理が完了するまで保持する。
