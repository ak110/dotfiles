# 計画バンドルの保存と参照

本書は計画ファイルと付属ファイルの保存先、stem、参照の書式および保存の時点を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `plan-file-standards.md#「バグ調査」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「バグ調査」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `plan-file-standards.md#冒頭` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/lane-integration.subagent.md#「計画最終化」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「計画最終化」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` |

## 計画ファイルの保存と参照

計画は1ファイル`<stem>.md`とし、計画ファイル（バグ）を作成する場合（`plan-file-standards.md`「バグ調査」の作成条件）だけ同じstemの`<stem>.bugs.md`を同じディレクトリへ置く。
`atk plans commit`が保存する付属ファイルは計画ファイル（バグ）、実行レビュー指摘管理表、WI実装commitの対応記録ファイル（`<stem>.wi-commits.jsonl`）だけである（計画バンドルの定義は`plan-file-standards.md`冒頭の呼称の表）。計画stemで始まる他のファイルは保存されず、同じ操作で作業側から削除されるため、計画に属さない作業ファイルは別の名前でmanaged-tempへ置く。
新規作成は`atk run-script plan-create --`を経由し、`~/.claude/plans`直下へ作成する。作成処理は同じstemの全ファイルを排他的に確定し、読み戻しと計画構造の自動チェックに成功してからパスを返す。
stemは`起動経路`ごとに次のとおりとする（`dd`は作成日、`HHmm`は作成時刻）。同じstemがあれば作成処理が`-<小文字16進数4桁>`を付けて再試行する。

| `起動経路` | stem |
| --- | --- |
| `agent-toolkit:process-wi`のレーン | `dd-HHmm_process-wi_レーンNN` |
| `agent-toolkit:plan-mode`のメインによる起動 | `dd-HHmm_<日本語の簡潔な名詞>` |

`~/.claude/plans`直下の計画ファイル名は、上表のstemに`.md`を続けた形と、保存先の正準形`dd-<名称>-<小文字16進数4桁>.md`の双方を受理する。正準形は保存時に生成されるため、上表のstemだけを使えばよい。
レーンは作成処理へ`--lane lane-NN`（2桁のレーン番号）を渡し、日時（実行環境のローカルタイムゾーン）、接頭辞、番号は作成処理が組み立てる。メインによる起動では`--name <名称>`を渡し、日時接頭辞の無い名称にはUTCの日時が付き、`dd-HHmm_`接頭辞を持つ完全stemはそのまま使われる。

計画本文から計画ファイル（バグ）とレビュー指摘管理表を参照するときは、固定接頭辞`~/.claude/plans/`とファイル名を使う。新規作成ではstem部分へ固定プレースホルダー`__PLAN_STEM__`を書き、作成処理が最終stemへ置換する（値は`${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/create_plan_files.py`の`PLAN_STEM_PLACEHOLDER`）。
接頭辞後はファイル名1件だけとし、パス区切り文字、`..`、シェル式、Windows区切り文字を含めない。参照は接頭辞を展開せず、計画ファイルのディレクトリへファイル名を結合して解決する。既存計画の`$(atk config get private_notes)/`で始まる参照は読み取り互換として受理する。

計画は実行レビューの収束まで`~/.claude/plans`直下で更新し、収束後に`atk plans commit <~/.claude/plans直下のメイン計画ファイル名>`で`private-notes/plans/yyyy/MM/`へ移して保存する。計画ファイル、計画ファイル（バグ）、レビュー指摘管理表は同じcommitで保存される。
`agent-toolkit:process-wi`のレーンが実行レビューの収束前に終端する場合も、保存の時点は`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「計画最終化」とし、統合の記録を追記した後に1回保存する。実行レビューを持たない計画（観測のみの再開で取得した計画、`マージなし`の計画）も同じで、観測の完了と`計画検査完了`の通知は保存の時点に当たらない。
保存済み計画は完了した作業の記録とし、次のセッションの実装には新しい計画を作成する。中断したレーンの再開は`agent-toolkit:process-wi`の`skills/process-wi/references/run-lanes.md`が定める。
`~/.claude/plans`直下の各計画バンドルには所有セッションを示す局所状態（計画の所有記録）が付き、`atk plans list`で所有セッションと最終更新時刻を一覧できる。自身が所有しない計画はそのまま残す。private-notesの計画は`atk plans`の各コマンドで更新する。
保存済み計画をcheckoutして履歴書換えへ追随させた主体は、その工程の末尾で更新した全計画バンドルを`atk plans commit`で再保存する。未終端のWIや延期adoptを持つ計画も省かず、終了コード0で未適用・未完了の警告がないことを検収する。レーンの実装途中・統合時、観測のみの再開、CI修正では既存の保存時点を使う。

計画の節を差し替える前に、行頭完全一致の見出し行など一意に定まる境界を選び、各境界文字列の一致が1件であることを確かめてから置換する。

実行レビュー指摘管理表は計画と同じディレクトリへ`<計画stem>.exec-review.tsv`（`track`は`exec-review`）として置く。
計画を持たない実行レビューの表は`~/.claude/plans`直下へ置く。名前はWIだけを基準とする直接実装では`wi-<処理開始時点の7文字以上の一意な短縮OID>.exec-review.tsv`、公開工程のCI失敗修正では`ci-<修正系列の開始時のHEADの7文字以上の一意な短縮OID>.exec-review.tsv`とする。短縮OIDは`git rev-parse --short=7 <revision>`の返り値をそのまま使う。
前者は収束後に削除する。後者は修正系列の終端で`atk plans commit ci-<修正系列の開始時のHEADの7文字以上の一意な短縮OID>.exec-review.tsv`で`private-notes/plans/ci/`へ保存する。この保存の時機は`agent-toolkit:bugfix`の`references/ci-failure-handling.md`の`入力計画`の段落が定める。
