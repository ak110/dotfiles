# 計画バンドルの保存と参照

本書は、計画ファイルおよび付属ファイルの保存先、stemの命名規則、参照書式、ならびに保存タイミングを定める。

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

計画ファイルは1ファイルで`<stem>.md`とし、計画ファイル（バグ）を作成する場合（`plan-file-standards.md`「バグ調査」の作成条件を満たす場合）に限り、同一stemの`<stem>.bugs.md`を同じディレクトリに配置する。
`atk plans commit`が保存対象とする「計画バンドル」は、メインの計画ファイルとあらかじめ定義された付属ファイルで構成される。
付属ファイルには、計画ファイル（バグ）、実行レビュー指摘管理表、およびWI実装commitの対応記録ファイル（`<stem>.wi-commits.jsonl`）が含まれる。
計画バンドルの詳細な定義は、`plan-file-standards.md`冒頭の呼称一覧表に記載されている。
計画バンドルに含まれない同一stemの既知の一時ファイル（`.lock`、`.bak`、`.tmp`）は、作業環境側から回収される。
それ以外の同一stemを持つファイルは、保存も削除も行われずに作業環境に残るため、計画に属さない作業ファイルは別名にしてmanaged-tempへ配置する。
新規作成時は`atk run-script plan-create --`を経由して`~/.claude/plans`直下に作成する。作成処理は同一stemの全ファイルを排他的に確定し、読み戻しと計画構造の自動チェックに成功した後にパスを返す。
stemの命名規則は`起動経路`ごとに下表のとおりとする（`dd`は作成日、`HHmm`は作成時刻）。すでに同じstemが存在する場合は、作成処理が自動的に末尾へ`-<小文字16進数4桁>`を付与して再試行する。

| `起動経路` | stem |
| --- | --- |
| `agent-toolkit:process-wi`のレーン | `dd-HHmm_process-wi_レーンNN` |
| `agent-toolkit:plan-mode`のメインによる起動 | `dd-HHmm_<日本語の簡潔な名詞>` |

`~/.claude/plans`直下の計画ファイル名としては、上表のstemに`.md`を付与した形式と、保存先の正準形である`dd-<名称>-<小文字16進数4桁>.md`の双方が受け付けられる。正準形は保存時に自動生成されるため、作成時は上表のstemの形式を用いればよい。
レーンで作成する場合は作成処理へ`--lane lane-NN`（2桁のレーン番号）を渡し、日時（実行環境のローカルタイムゾーン）、接頭辞、番号は作成処理が自動で組み立てる。メインエージェントからの起動時は`--name <名称>`を渡し、日時接頭辞がない名称にはUTCの日時が付与され、`dd-HHmm_`接頭辞を持つ完全なstemはそのまま使用される。

計画本文から計画ファイル（バグ）やレビュー指摘管理表を参照する際は、固定接頭辞`~/.claude/plans/`とファイル名を組み合わせて記述する。新規作成時はstem部分に固定プレースホルダー`__PLAN_STEM__`を記述しておき、作成処理がこれを最終的なstemへ自動置換する。プレースホルダーの値は`${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/create_plan_files.py`の`PLAN_STEM_PLACEHOLDER`で定義されている。
接頭辞の後には単一のファイル名のみを記述し、パス区切り文字、`..`、シェル式、Windows固有の区切り文字を含めない。参照解決時は接頭辞を展開せず、計画ファイルが存在するディレクトリにファイル名を直接結合して解決する。なお、既存の計画ファイルに含まれる`$(atk config get private_notes)/`で始まる参照は、後方互換性のため読み取り対象として受け付ける。

計画は実行レビューが収束するまで`~/.claude/plans`直下で更新し、収束した段階で`atk plans commit <~/.claude/plans直下のメイン計画ファイル名>`を実行して`private-notes/plans/yyyy/MM/`へ移動・保存する。計画ファイル、計画ファイル（バグ）、およびレビュー指摘管理表は、同一のコミットとして保存される。
`agent-toolkit:process-wi`のレーンが実行レビューの収束前に終了する場合でも、保存のタイミングは`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「計画最終化」に従い、統合記録を追記した後に1回保存する。実行レビューを実施しない計画（観測のみの再開で取得した計画や、「マージなし」の計画）も同様であり、観測の完了や「計画検査完了」の通知は保存の契機にはならない。
保存済みの計画は完了した作業の記録として扱い、後続セッションで実装する際は新しい計画ファイルを作成する。中断したレーンの再開手順については、`agent-toolkit:process-wi`の`skills/process-wi/references/run-lanes.md`に従う。
`~/.claude/plans`直下の各計画バンドルには、所有セッションを示すローカル状態（計画の所有記録）が付与されており、`atk plans list`を実行すれば所有セッションと最終更新時刻を一覧確認できる。自身が所有していない計画は変更せずにそのまま残す。private-notes内の計画は`atk plans`の各種コマンドで更新する。
保存済み計画をcheckoutして履歴の書き換えに追従させた主体は、その工程の最後で更新されたすべての計画バンドルを`atk plans commit`で再保存する。未完了のWIや延期（adopt）を含む計画も省略せずに対象とし、終了コード0かつ未適用・未完了の警告が出ないことを確認する。レーンの実装途中や統合時、観測のみの再開時、およびCI修正時には、既存の保存タイミングをそのまま用いる。

計画の各節を差し替える際は、行頭完全一致の見出し行など一意に特定できる境界を選び、それぞれの境界文字列の一致件数が1件であることを確認してから置換を実行する。

実行レビュー指摘管理表は、計画ファイルと同一ディレクトリに`<計画stem>.exec-review.tsv`（`track`は`exec-review`）として配置する。
計画ファイルを持たない実行レビューの表は、`~/.claude/plans`直下に配置する。ファイル名は、WIのみを基準とする直接実装の場合は`wi-<処理開始時点の7文字以上の一意な短縮OID>.exec-review.tsv`、公開工程におけるCI失敗修正の場合は`ci-<修正系列の開始時のHEADの7文字以上の一意な短縮OID>.exec-review.tsv`とする。短縮OIDには`git rev-parse --short=7 <revision>`の出力をそのまま用いる。
前者はレビュー収束後に削除する。後者は修正系列の終了時に`atk plans commit ci-<修正系列の開始時のHEADの7文字以上の一意な短縮OID>.exec-review.tsv`を実行し、`private-notes/plans/ci/`へ保存する。この保存タイミングは、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`内にある「入力計画」の段落に従う。
