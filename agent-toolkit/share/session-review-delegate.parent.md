# 振り返り担当の起動と受領

```text
起動対象: session-review-delegate.subagent.md
```

`agent-toolkit:session-review`で、メインが本書を全文読み、振り返り担当の起動、返却の検収及び引き継ぎ経路の終端へ適用する。

## 起動前の前提

メインは`agent-toolkit:delegation`のSKILL.md、同スキルの`references/base-contract.md`及び`references/mandatory-rules.md`を全文読む。本書が起動経路と必須入力を逐語で定めるため、起動側は`references/routing.md`と`references/handoff-record.md`を読まない。

メインは起動の前に、`agent-toolkit:session-review`のSKILL.mdが定める準備工程を完了する。標準出力から`evidence_script`、`transcript_path`又は`codex_thread_id`、`managed_temp`、`observation_boundary`及び`target_repo`を取得する。項目を取得できない場合と準備工程が非0で終了した場合は、振り返り担当を起動せず分析失敗として扱う。

観測境界は、境界より後に親記録へ追加されるメイン自身の進捗報告を、過去の未完了工程と区別するために取得する。`managed_temp`が指す領域はメインが所有し、振り返り担当はその領域へ書き込むだけとする。回収はメインが行う。

振り返り用の参照文書と所要時間目標は、振り返り担当が起動時の`cwd`から解決したプロジェクト規範から取る。メインは目標の有無と目標値を成果ファイルの`## 対象セッション`から読み、`agent-toolkit:completion-report`へ渡す入力の保持要否を判定する。

## 起動

起動の前に`atk managed-temp create --prefix session-review-output`を1回実行し、終了コード0と単一行の絶対パスを確認する。そのディレクトリ直下の`<対象セッションの識別子>.md`を出力先ファイルとし、メインが所有する。

メインは`agent-toolkit:delegation`をSkill機能で起動し、`agents_server`の`start`で通常のサブエージェントを1つ起動する。
`subagent_md_path`には`${CLAUDE_PLUGIN_ROOT}/share/session-review-delegate.subagent.md`を解決した絶対パスを渡す。
`target_repo`が値を持つ場合は`cwd`へ対象リポジトリの絶対パスを渡す。`target_repo`が`null`の場合は、Git worktreeではない`managed_temp`の絶対パスを`cwd`へ渡す。

`extra_params`の名前付き必須入力は次の6項目とし、値を次のとおり確定する。

- 対象セッションの実行系: 対象セッションを実行しているコーディングエージェントの製品名。Claude Codeでは`Claude Code`、Codexでは`Codex`とする
- 対象セッションの識別子: Claude Codeでは準備工程が返した`transcript_path`の拡張子を除いたファイル名、Codexでは`codex_thread_id`の値とする
- 管理対象一時領域: 準備工程が返した`managed_temp`の値とする
- 観測境界: 準備工程が返した`observation_boundary`の値とする
- 出力先ファイル: 本節冒頭で確定した出力先ファイルの絶対パスとする
- 引き継ぎ記録先: `atk managed-temp create --prefix=handoff`で作成した領域の直下のファイルの絶対パスへ`（新規）`を続けた値。領域の作成と回収は`agent-toolkit:writing-standards`の`references/managed-temp.md`に従う

起動経路は固定タスク契約、抽出器は現行plugin root、対象リポジトリとプロジェクト規範は`cwd`から振り返り担当が解決するため、名前付き入力は前記の6項目に限る。

メインは2つの領域の絶対パスを保持し、保持、進捗記録及び回収のいずれもメインが担う。

## 受領

振り返り担当は`${CLAUDE_PLUGIN_ROOT}/share/session-review-delegate.subagent.md`が定める形式で返す。メインは`output_file`が起動文の絶対パスと一致することを確認し、そのファイルを読む。`completed`の場合は、成果ファイルの`## 対象セッション`、`## 問題候補の判定記録`、`## メイン由来の改善点`、`## 規範適用による停止`、`## 所要時間の内訳と改善提案`、`## 登録したキュー項目`及び`## 未確認範囲`の全節を検収する。

`needs_escalation`の場合は、返された確認事項を確認し、回答を得られない場合はUWIを登録する。
回答を得た場合は回答を、得られない場合はUWIの正本ファイル名を同じsessionへ配送する。
`analysis_failed`の場合は同じ入力で1回だけ起動し直す。再失敗時は確認又はUWI登録をする。メインが再取得するのは、成果ファイルが示すlocatorが指す証拠に限る。セッション全体の要約と再抽出は振り返り担当の工程に属する。

## メイン由来の改善点の配送

メインが自身のコンテキストから列挙した改善点を、`## ユーザー発話の追加分の配送`と同じ`send_message`の経路で配送する。
配送する項目は、その列挙を書いたファイルの絶対パスとする。配送の時点は、振り返り担当の成果を受領した後とする。
機械的な抽出は標識を残さない事象を拾わないため、この配送が無いとメインのコンテキストだけが持つ観測が候補へ入らない。

## ユーザー発話の追加分の配送

追加分が1件以上の場合は、同じ`agents_server` sessionへ`send_message`で配送し、振り返り担当が成果ファイルへ反映して再返却するまで待つ。配送先は既存のsessionとし、新しいsessionの起動はこの経路の外に置く。振り返りの実行中に受領したユーザー介入も配送対象とし、類似見直しと再発防止策へ反映する。

配送する項目は次のとおりとする。

- 追加分のlocator: 追加分の`record`欄と`line`欄の組とする
- 追加分の要点: 追加分ごとの本文の要点とする
- 再照合境界: `agent-toolkit:session-review`のSKILL.mdの`## 問題候補の抽出`の手順3がその反復で取得した値とする。振り返り担当はその値を`--observation-boundary`へ渡して集約実行を1回追加し、生成された候補集合を構造検査の入力とするため、反復ごとにその反復の値を渡す

## 即時対応と後始末

メインは成果ファイルの登録したキュー項目について、`agent-toolkit:process-wi`のSKILL.mdの即時対応の判定を適用する。成果ファイルは、成果の検収、即時対応と次セッションへの登録の確定、確定した処置の実施、及び`agent-toolkit:completion-report`の振り返り欄への反映が完了するまで保持する。

回収は、最後に発行した`wait`が終端を返しその後に指示を配送していないこと、全消費工程と恒久記録が完了したこと、成果ファイルを再読又は担当へ継続を依頼する工程が残っていないことを確認してから実行する。メインは出力先と抽出結果の2領域をそれぞれ`atk managed-temp cleanup --path <対象の絶対パス>`で回収する。未完了工程が残る場合は、両方の領域を保持したまま次の工程へ進む。
