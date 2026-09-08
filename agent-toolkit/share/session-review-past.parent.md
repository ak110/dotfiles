# 別セッションの振り返り担当の起動と受領

`agent-toolkit:session-review`の別セッション経路で、メインが本書を全文読み、対象セッションの取得、振り返り担当の起動、返却の検収及び引き継ぎ経路の終端へ適用する。
本書は振り返り担当自身が行う分析と成果本文の作成手順を定義しない。

## 対象セッションの取得

メインが`atk session-review-target`を1回実行し、終了コード0を確認する。
Claude Codeでは現在のtranscriptの絶対パスを`--transcript`へ、Codexでは`CODEX_THREAD_ID`の値を`--codex-thread-id`へ渡す。

```sh
atk session-review-target --target-repo=<対象リポジトリの絶対パス> --transcript=<現在のtranscriptの絶対パス>
atk session-review-target --target-repo=<対象リポジトリの絶対パス> --codex-thread-id=<現在のthread ID>
```

当該コマンドは、Claude CodeとCodexの保存済みセッション記録から、対象リポジトリで動いた自身以外の本体セッションのうち更新時刻が最新の1件を1行のJSONで返す。
該当するセッションが無い場合は何も返さない。
返った行が0件の場合は振り返り担当を起動せず、対象が無い旨を`agent-toolkit:completion-report`の振り返り欄へ渡す。
返った行が1件の場合は1件の振り返り担当を起動する。行の`engine`と`session_id`をそのまま起動文へ渡し、値を組み立て直さない。

## 起動

起動の前に`atk managed-temp create --prefix session-review-past`を1回実行し、終了コード0と単一行の絶対パスを確認する。
当該ディレクトリの直下の`<session_id>.md`を振り返りの成果の出力先ファイルとし、メインが当該領域を所有する。

起動文を組む前に`agent-toolkit:delegation`をSkill機能で起動する。
`agents_server`の`start`へ`model_type="session_review"`と対象リポジトリの絶対パスを渡し、通常のサブエージェントを1つ起動する。
engine、model及びeffortはサーバーが解決するため指定しない。

起動文は`agent-toolkit:delegation`のSKILL.mdの`## 送信`に従い、1行目で`${CLAUDE_PLUGIN_ROOT}/share/session-review-past.subagent.md`を指す。
次を名前付き必須入力とし、これ以外を渡さない。

- 対象セッションの実行系
- 対象セッションの識別子
- 対象リポジトリの絶対パス
- プロジェクト規範の絶対パス
- 出力先ファイルの絶対パス

振り返りの起動理由を起動文へ含めない。起動理由を渡すと、分析が当該理由へ適合する事象へ偏る。

## 受領

振り返り担当は`${CLAUDE_PLUGIN_ROOT}/share/session-review-past.subagent.md`が定める形式で返す。
メインは`output_file`が起動文で渡した絶対パスと一致することを確認し、当該ファイルを読んで検収へ用いる。
一致しない場合と当該ファイルを読み取れない場合は、観測値を添えて同じsessionへ再取得を指示する。

`status`が`completed`の場合は、成果ファイルが対象セッション、問題候補の判定記録、規範適用による停止、所要時間の内訳と改善提案、登録したキュー項目及び未確認範囲の各節を持つことを検収する。
いずれかが欠ける場合は、欠けた節を示して同じsessionへ補完を1回だけ指示する。

`status`が`needs_escalation`の場合は、返された確認事項をメインが`AskUserQuestion`で確認する。
回答を得られない場合は`agent-toolkit:wi-standards`に従ってUWIを登録する。
いずれの場合も回答又はUWIの正本ファイル名を同じsessionへ配送し、振り返りを完了させる。

`status`が`analysis_failed`の場合は、同じ入力で振り返り担当を1回だけ起動し直す。
再失敗した場合は`agent-toolkit:wi-standards`に従い、対象セッションの識別子、失敗事象、解除条件及び再開工程をUWIへ登録する。

## 即時対応と後始末

メインは成果ファイルの登録したキュー項目について、`agent-toolkit:process-wi`のSKILL.mdの「即時対応」節の判定を適用する。
振り返り担当が当該項目を既に登録しているため、同節の手順2の登録を重ねて行わない。
当該項目は当該セッションのレーンへ組み込まず、同節の手順3の委譲文へ正本ファイル名を渡す。

成果ファイルは、成果の検収、即時対応と次セッションへの登録の確定、確定した処置の実施、
及び`agent-toolkit:completion-report`の振り返り欄への反映が完了するまで入力として保持する。
対象セッションごとに、成果ファイルから確定した結論と処置を現在の計画の進捗ログへ記録し、
登録したキュー項目や対象リポジトリの成果物がある場合は、その正本も同じ記録へ対応付ける。
未完了工程がある場合は、当該工程と成果ファイルの絶対パスを現在の計画の進捗ログへ記録する。

全ての対象について前段の消費工程と恒久記録が完了し、再開時に成果ファイルを再読する工程が残っていないことを確認した後に、
`atk managed-temp cleanup --path <起動前に作成した領域の絶対パス>`を1回実行し、終了コード0を確認する。
未完了工程が残る場合は回収せず、記録した絶対パスから次のターンで継続する。
