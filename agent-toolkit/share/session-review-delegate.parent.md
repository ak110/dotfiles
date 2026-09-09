# 振り返り担当の起動と受領

```text
起動対象: session-review-delegate.subagent.md
```

`agent-toolkit:session-review`の両経路で、メインが本書を全文読み、対象セッションの取得、振り返り担当の起動、返却の検収及び引き継ぎ経路の終端へ適用する。

## 起動前の前提

メインは起動の前に、`agent-toolkit:session-review`のSKILL.mdが定める準備工程を完了する。標準出力から`evidence_script`、`transcript_path`又は`codex_thread_id`、`managed_temp`、`observation_boundary`及び`target_repo`を取得する。項目を取得できない場合と準備工程が非0で終了した場合は、振り返り担当を起動せず分析失敗として扱う。

観測境界は、境界より後に親記録へ追加されるメイン自身の進捗報告を、過去の未完了工程と誤認させないために取得する。`managed_temp`が指す領域はメインが所有し、振り返り担当は当該領域へ書き込むだけとし、回収しない。

振り返り用の参照文書と所要時間目標はメインが解決せず、振り返り担当が受領したプロジェクト規範の絶対パスから解決する。メインは目標の有無と目標値を成果ファイルの`## 対象セッション`から読み、`agent-toolkit:completion-report`へ渡す入力の保持要否を判定する。

## 対象セッションの取得

本節は別セッション経路だけで実行する。自セッション経路では本節を実行せず、メイン自身の記録の識別子を対象セッションの識別子とする。

メインが`atk session-review-target`を1回実行し、終了コード0を確認する。Claude Codeでは`atk session-review-target --transcript=<現在のtranscriptの絶対パス>`、Codexでは`atk session-review-target --codex-thread-id=<CODEX_THREAD_IDの値>`とする。行が0件の場合は振り返り担当を起動せず、対象が無い旨を`agent-toolkit:completion-report`の振り返り欄へ渡す。行が1件の場合は`engine`と`session_id`をそのまま起動文へ渡す。

## 起動

- `引き継ぎ記録先`: `atk managed-temp create --prefix=handoff`で作成した領域の直下のファイルの絶対パス。当該委譲の全工程の完了後に`atk managed-temp cleanup --path <当該領域の絶対パス>`で回収する

起動の前に`atk managed-temp create --prefix session-review-output`を1回実行し、終了コード0と単一行の絶対パスを確認する。当該ディレクトリ直下の`<対象セッションの識別子>.md`を出力先ファイルとし、メインが所有する。

メインは`agent-toolkit:delegation`をSkill機能で起動し、`agents_server`の`start`へ`model_type="session_review"`と対象リポジトリの絶対パスを渡して通常のサブエージェントを1つ起動する。

起動文の1行目で`${CLAUDE_PLUGIN_ROOT}/share/session-review-delegate.subagent.md`を指す。起動経路、対象セッションの実行系、対象セッションの識別子、抽出器、管理対象一時領域、観測境界、対象リポジトリ、プロジェクト規範、出力先ファイルだけを名前付き必須入力として渡す。`target_repo`が`null`の場合は対象リポジトリを`なし`とする。

メインは2つの領域の絶対パスを保持し、保持、進捗記録及び回収のいずれもメインが担う。

## 受領

振り返り担当は`${CLAUDE_PLUGIN_ROOT}/share/session-review-delegate.subagent.md`が定める形式で返す。メインは`output_file`が起動文の絶対パスと一致することを確認し、当該ファイルを読む。`completed`の場合は、対象セッション、問題候補の判定記録、規範適用による停止、所要時間の内訳と改善提案、登録したキュー項目及び未確認範囲の各節を検収する。

`needs_escalation`の場合は、返された確認事項が対象セッションそのものの不成立を示すかを先に判定する。
不成立とは、当該セッションが対象リポジトリで`agent-toolkit:process-wi`を起動していないこと、稼働中で分析できないこと、記録を読み取れないことを指す。
不成立を示す場合は、当該返却を本工程の結論として扱わない。
`## 対象セッションの取得`が定める目的から対象を導き直し、対象を得られた場合は当該対象で振り返り担当を起動し直す。
導き直した対象が無い場合は、対象が無い旨を`agent-toolkit:completion-report`の振り返り欄へ渡して、この経路を終える。
不成立を示さない場合は、返された確認事項を確認し、回答を得られない場合はUWIを登録する。
回答を得た場合は回答を、得られない場合はUWIの正本ファイル名を同じsessionへ配送する。
`analysis_failed`の場合は同じ入力で1回だけ起動し直す。再失敗時は経路に応じて確認又はUWI登録をする。メインは成果ファイルが示すlocatorが指す証拠だけを再取得し、セッション全体の要約と再抽出は作成しない。

## ユーザー発話の追加分の配送

本節は自セッション経路だけで実行する。追加分が1件以上の場合は、同じ`agents_server` sessionへ追加分の`record`欄と`line`欄の組及び本文の要点を`send_message`で配送し、振り返り担当が成果ファイルへ反映して再返却するまで待つ。別sessionを起動しない。

## 即時対応と後始末

メインは成果ファイルの登録したキュー項目について、`agent-toolkit:process-wi`のSKILL.mdの即時対応の判定を適用する。成果ファイルは、成果の検収、即時対応と次セッションへの登録の確定、確定した処置の実施、及び`agent-toolkit:completion-report`の振り返り欄への反映が完了するまで保持する。

回収は、最後に発行した`wait`が終端を返しその後に指示を配送していないこと、全消費工程と恒久記録が完了したこと、成果ファイルを再読又は担当へ継続を依頼する工程が残っていないことを確認してから実行する。メインは出力先と抽出結果の2領域をそれぞれ`atk managed-temp cleanup --path <対象の絶対パス>`で回収する。未完了工程が残る場合はいずれも回収しない。
