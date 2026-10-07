# 計画バンドルの保存と参照

本書は計画ファイルと付属ファイルの保存先、stem、参照の書式および保存の時点を定める。

## 計画ファイルの保存と参照

計画は1ファイル`<stem>.md`とし、計画ファイル（バグ）を作成する場合（`plan-file-standards.md`「バグ調査」の作成条件）だけ同じstemの`<stem>.bugs.md`を同じディレクトリへ置く。
`atk plans commit`が保存する付属ファイルは計画ファイル（バグ）、実行レビュー指摘管理表およびWI実装commitの対応記録ファイル（`<stem>.wi-commits.jsonl`）だけとする。
計画ファイルと、同じstemの付属ファイル（計画ファイル（バグ）、レビュー指摘管理表など）の組を計画バンドルと呼ぶ。
計画stemで始まるそれ以外のファイルは保存されず、同じ操作で作業側から削除される。
計画に属さない作業ファイルは、計画stemとは別の名前でmanaged-tempの中へ保存する。
新規作成は`atk run-script plan-create --`を経由し、実装前の計画ファイルは`~/.claude/plans`直下へ作成する。
作成処理は同じstemに属する全ファイルを排他的に確定し、読み戻しと計画構造の自動チェックに成功してからパスを返す。
stemは計画メタ情報の`起動経路`ごとに次のとおりとし、`dd`は作成日、`HHmm`は作成時刻とする。同じstemが既にある場合は作成処理が`-<小文字16進数4桁>`を付けて再試行する。

| `起動経路` | stem |
| --- | --- |
| `agent-toolkit:process-wi`のレーン | `dd-HHmm_process-wi_レーンNN` |
| `agent-toolkit:plan-mode`のメインによる起動 | `dd-HHmm_<日本語の簡潔な名詞>` |

`~/.claude/plans`直下の計画ファイル名は、上表のstemに`.md`を続けた形と、保存先の正準形`dd-<名称>-<小文字16進数4桁>.md`の双方を受理する。保存先の正準形だけを満たす名前を自ら組み立てる必要はない。

`agent-toolkit:process-wi`のレーンは内部作成処理へ`--lane lane-NN`で委譲プロンプトのレーン識別子を渡す。`NN`は2桁のレーン番号とし、日付、時刻、固定接頭辞およびレーン番号は作成処理が組み立てる。
この`起動経路`の日付と時刻には、作成処理を実行する環境のローカルタイムゾーンを用いる。
`agent-toolkit:plan-mode`のメインによる起動では、内部作成処理へ`--name <名称>`を渡す。日時接頭辞を持たない名称では作成処理がUTCの日付と時刻を組み立て、既に`dd-HHmm_`接頭辞を持つ完全stemはそのまま用いる。

計画本文が参照する計画ファイル（バグ）とレビュー指摘管理表には、固定接頭辞`~/.claude/plans/`とファイル名を使う。
新規作成では最終stemが未確定であるため、ファイル名のstem部分へ固定プレースホルダー`__PLAN_STEM__`を書く。
作成処理がこの文字列を最終stemへ置換する。プレースホルダーの値は`${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/create_plan_files.py`の`PLAN_STEM_PLACEHOLDER`に従う。
接頭辞後の値はファイル名1件だけとし、パス区切り文字、`..`、シェル式、Windows区切り文字を含めない。
参照の解決では接頭辞を展開せず、参照を含む計画ファイルのディレクトリへファイル名を結合して実体を求める。
既存計画の本文に残る`$(atk config get private_notes)/`から始まる参照は読み取り互換として受理する。

計画は実行レビューの収束時点まで`~/.claude/plans`直下で更新し、収束後に`atk plans commit <~/.claude/plans直下のメイン計画ファイル名>`で`private-notes/plans/yyyy/MM/`へ移動して保存する。
同コマンドは計画ファイル、計画ファイル（バグ）およびレビュー指摘管理表を同じcommitで保存する。
`agent-toolkit:process-wi`のレーンが実行レビューの収束前に終端する場合も、終端の時点は`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「計画最終化」とし、統合の記録を追記した後に1回保存する。実行レビューを持たない計画（観測のみの再開で取得した計画、`マージなし`の計画）も同じであり、観測の完了と`計画検査完了`の通知は保存の時点に当たらない。
保存済み計画は完了した作業の記録とし、次のセッションの実装入力には新しい計画を作成する。
中断したレーンの再開は`agent-toolkit:process-wi`の`skills/process-wi/references/run-lanes.md`が定める。
`~/.claude/plans`直下の各計画バンドルには、計画バンドルを所有するセッションを示す局所状態（計画の所有記録）が付き、`atk plans list`で所有セッションと最終更新時刻を一覧できる。
自身が所有しない計画はそのまま残す。private-notesの計画ファイルの更新は`atk plans`の各コマンドで行う。

実行レビュー指摘管理表は計画ファイルと同じディレクトリへ`<計画stem>.exec-review.tsv`（`track`は`exec-review`）として置く。
計画を持たない実行レビューの表は`~/.claude/plans`直下へ置く。WIだけをレビュー基準とする直接実装では`wi-<処理開始時点の7文字以上の一意な短縮OID>.exec-review.tsv`、公開工程のCI失敗修正では`ci-<修正系列の開始時のHEADの7文字以上の一意な短縮OID>.exec-review.tsv`とする。短縮OIDは`git rev-parse --short=7 <revision>`が返した値をそのまま用いる。
前者は収束後に削除し、後者は`atk plans commit ci-<修正系列の開始時のHEADの7文字以上の一意な短縮OID>.exec-review.tsv`で`private-notes/plans/ci/`へ保存する。
