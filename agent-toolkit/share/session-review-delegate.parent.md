# 振り返り担当の起動と受領

```text
起動対象: session-review-delegate.subagent.md
```

`agent-toolkit:session-review`で、メインが本書を全文読み、振り返り担当の起動、返却の検収及び引き継ぎ経路の終端へ適用する。

## 起動前の前提

メインは`agent-toolkit:delegation`のSKILL.md、同スキルの`references/base-contract.md`及び`references/mandatory-rules.md`を全文読む。本書が起動経路と必須入力を逐語で定めるため、起動側は`references/routing.md`と`references/handoff-record.md`を読まない。

メインは起動の前に、`agent-toolkit:session-review`のSKILL.mdが定める準備工程を完了し、標準出力の1行JSONから`manifest_path`を取得する。その絶対パスに実在するファイルから`evidence_script`、セッション識別子、`managed_temp`、`output_temp`、`output_file`、`observation_boundary`、`target_repo`を取得する。`manifest_path`と`output_file`の親ディレクトリは同じ`output_temp`でなければならない。セッション識別子は`transcript_path`と`codex_thread_id`の一方とする。項目を取得できない場合、manifestが実在しない場合、又は準備工程が非0で終了した場合は、振り返り担当を起動せず分析失敗として扱う。以降はこのファイルを準備結果の単一正本とし、準備スクリプトを再実行しない。

観測境界は、境界より後に親記録へ追加されるメイン自身の進捗報告を、過去の未完了工程と区別するために取得する。`managed_temp`が指す領域はメインが所有し、振り返り担当はその領域へ書き込むだけとする。回収はメインが行う。

メインは振り返り担当を起動する前に、自身のコンテキストから列挙した改善点を`<managed_temp>/main-observations.md`へ保存する。改善点が0件の場合も空ファイルを作成する。固定パスから担当が初回処理で取得するため、このファイルを新しい名前付き入力へ追加しない。

振り返り用の参照文書と所要時間目標は、振り返り担当が起動時の`cwd`から解決したプロジェクト規範から取る。メインは目標の有無と目標値を成果ファイルの`## 対象セッション`から読み、`agent-toolkit:completion-report`へ渡す入力の保持要否を判定する。

## 起動

準備manifestの`output_file`を出力先ファイルとし、メインが所有する。

メインは`agent-toolkit:delegation`をSkill機能で起動し、`agents_server`の`start`で通常のサブエージェントを1つ起動する。
`subagent_md_path`には`${CLAUDE_PLUGIN_ROOT}/share/session-review-delegate.subagent.md`を解決した絶対パスを渡す。
`target_repo`が値を持つ場合は`cwd`へ対象リポジトリの絶対パスを渡す。`target_repo`が`null`の場合は、Git worktreeではない`managed_temp`の絶対パスを`cwd`へ渡す。

`extra_params`の名前付き必須入力は次の5項目とし、値を次のとおり確定する。

- 対象セッションの実行系: 対象セッションを実行しているコーディングエージェントの製品名。Claude Codeでは`Claude Code`、Codexでは`Codex`とする
- 対象セッションの識別子: Claude Codeでは準備工程が返した`transcript_path`の拡張子を除いたファイル名、Codexでは`codex_thread_id`の値とする
- 準備manifest: 準備工程が返した`manifest_path`の絶対パスとする
- 出力先ファイル: 準備manifestの`output_file`の絶対パスとする
- 引き継ぎ記録先: `atk managed-temp create --prefix=handoff`で作成した領域の直下のファイルの絶対パスへ`（新規）`を続けた値。領域の作成と回収は`agent-toolkit/share/managed-temp.md`に従う

起動経路は固定タスク契約、抽出器、対象リポジトリ、管理対象一時領域及び観測境界は準備manifest、プロジェクト規範は`cwd`から振り返り担当が解決するため、名前付き入力は前記の5項目に限る。

メインは2つの領域の絶対パスを保持し、保持、進捗記録及び回収のいずれもメインが担う。

選択した委譲経路で終端観測が成立しないと判明した場合だけ、`agent-toolkit:delegation`の`references/runtime-routing.md`と`references/waiting-and-monitoring.md`に従って代替経路へ切り替える。起動時から保持した識別子で所有対象と一致することを確認できる実行だけを終了し、その終端を観測してから標準Codexを含む代替経路を起動する。識別子と所有権のどちらかを確認できない稼働中対象は停止対象から外す。

## 受領

振り返り担当は`${CLAUDE_PLUGIN_ROOT}/share/session-review-delegate.subagent.md`が定める形式で返す。メインは`output_file`が起動文の絶対パスと一致することを確認し、そのファイルを読む。`completed`の場合は、成果ファイルの`## 対象セッション`、`## 問題候補の判定記録`、`## メイン由来の改善点`、`## 規範適用による目的逸脱`、`## 所要時間の内訳と改善提案`、`## 登録したキュー項目`及び`## 未確認範囲`の全節を検収する。
欠陥と判定された各候補では、`処置`に根本原因へ対応する再発防止策の実装済み成果物と終了状態、又は根本原因と必要な全処置を覆うactiveなAWIがあることを独立に確認する。AWIを根拠とする場合は`atk wi show`で本文とactive状態を照合する。既存規範を守るという宣言だけの報告と、処置の実体が欠ける報告は`completed`として受理せず、同じ振り返り担当へ不足した原因分析と処置の確定を返す。

`needs_escalation`の場合は、返された確認事項を確認し、回答を得られない場合はUWIを登録する。
回答を得た場合は回答を、得られない場合はUWIの正本ファイル名を同じsessionへ配送する。
`analysis_failed`の場合は同じ入力で1回だけ起動し直す。再失敗時は失敗事象、原因、解除条件及び再現手順を持つ欠陥AWIを登録し、元依頼のうち認可済みで技術的に実行可能な工程を継続する。元作業の終了条件は振り返りの成否から独立させる。メインが再取得するのは、成果ファイルが示すlocatorが指す証拠に限る。セッション全体の要約と再抽出は振り返り担当の工程に属する。

## メイン由来の改善点の配送

メインは成果の`## メイン由来の改善点`へ`<managed_temp>/main-observations.md`の絶対パス、受領件数及び各項目の統合先又は分析結果があることを検収する。全てが揃う通常時は、その検収結果を採用して追加配送を省く。
いずれかが欠ける場合だけ、`## ユーザー発話の追加分の配送`と同じ`send_message`の経路で、同じファイルの絶対パスと期待した改善点件数を既存sessionへ配送する。担当が補完した成果を再返却するまで待つ。

## ユーザー発話の追加分の配送

追加分が1件以上の場合は、同じ`agents_server` sessionへ`send_message`で配送し、振り返り担当が成果ファイルへ反映して再返却するまで待つ。配送先は既存のsessionとし、新しいsessionの起動はこの経路の外に置く。振り返りの実行中に受領したユーザー介入も配送対象とし、類似見直しと再発防止策へ反映する。

配送する項目は次のとおりとする。

- 追加分のlocator: 追加分の`record`欄と`line`欄の組とする
- 追加分の要点: 追加分ごとの本文の要点とする
- 再照合境界: `agent-toolkit:session-review`のSKILL.mdの`## 問題候補の抽出`の手順3がその反復で取得した値とする。振り返り担当はその値を`--observation-boundary`へ渡して集約実行を1回追加し、生成された候補集合を構造検査の入力とするため、反復ごとにその反復の値を渡す

## 即時対応と後始末

メインは成果ファイルの登録したキュー項目について、`agent-toolkit:process-wi`のSKILL.mdの即時対応の判定を適用する。成果ファイルは、成果の検収、即時対応と次セッションへの登録の確定、確定した処置の実施、及び`agent-toolkit:completion-report`の振り返り欄への反映が完了するまで保持する。

回収は、最後に発行した`wait`が終端を返しその後に指示を配送していないこと、全消費工程と恒久記録が完了したこと、成果ファイルを再読又は担当へ継続を依頼する工程が残っていないことを確認してから実行する。メインは出力先と抽出結果の2領域をそれぞれ`atk managed-temp cleanup --path <対象の絶対パス>`で回収する。未完了工程が残る場合は、両方の領域を保持したまま次の工程へ進む。
