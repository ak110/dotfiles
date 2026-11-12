# rules-main.claude-code.md: Claude Codeのメインエージェント向け規範

`rules-main.md`のClaude Code固有規範を定める。

`Usage limit reached; a short grace allowance remains`で始まる猶予通知では`agent-toolkit:user-confirmation-and-report`を起動し、状況伝達・続行・再開手順に従う。
続行許可中は残工程を進め、再開禁止時は状況と必要なユーザー操作を伝える。
終了判断は実待機を`wait`、観測した技術的不成立を`blocked`とする。

## ツールの入出力

- Windows版Bashは引用符付きheredoc・単一引用符内でも`\\`を`\`へ縮めるため、エスケープを含むコードはWriteでmanaged-tempへ保存して実行する。
- ユーザー向け本文は`mcp__agent-toolkit__send_to_user`の`message`で送る。
  拡張思考は表示未確認、地の文はAPIで要約され得るためである。
  確認質問は`AskUserQuestion`を使う。
  Function hooksの登録名は`send_to_user`、遅延時はToolSearchの`select:mcp__agent-toolkit__send_to_user`で取得する。
  未提供なら最終応答に本文を書く。
  同手段で伝えた回答だけを委譲入力・記録に「回答済み」と書く。

## 自身の会話の記録

- 自身のセッションID・transcript絶対パスはSessionStartの通知値を使い、圧縮・再開・clear・fork後は更新する。
  更新時刻順・cwdからの推測名・MCPの`root_session_id`・委譲先IDで代えない（取り違え防止）。
- 通知で取得不能とされた値は、Bash・PowerShellの`CLAUDE_CODE_SESSION_ID`か、特定済みIDを渡す`atk run-script session-review-evidence -- --claude-session-id <ID>`で解決する。
- 委譲先へ証拠を渡すときもこの値を使い、過去・委譲先の会話は指定識別子で取得する。

## ユーザー確認（`AskUserQuestion`）

- `AskUserQuestion`前に共通読込表の確認資料を読み（圧縮後は再取得）、質問ごとに「確認要否の判定」を適用する。
  確認不要なら確定案・根拠を発話で報告する。
  - 質問・選択肢数はスキーマに従い、自由記述は各質問への回答、一般的な返答は別の`response`で受け取る。
  - 判断材料（経緯・調査結果・案の比較）と直前の問いへの答えは先に`send_to_user`で送る。
    質問単独で通じる最小限の前提・問い・選択肢・推奨を書く（ダイアログ単独表示と拒否後の再作成に備える）。
    ツール未提供なら地の文の要約に備え、判断材料を質問本文か`preview`へ書く。
  - 推奨案は第1選択肢に`(Recommended)`を付ける。
    `共通前提:`・質問・選択肢は同スキル「確認の選択肢を組む手順」に従う。
- 質問・承認待ちでターンを終えるときは、地の文の質問も`AskUserQuestion`で回答待ちを登録する。

## ターンの終え方

非同期処理が残らない終了本文は準備印によらず日本語で確認し、途中待機の末尾は言語遮断から除く。
進捗率は共通の30分点検に限る。

- 作業完了報告は送達前に`agent-toolkit:report-completion`を起動し、その書式・終了処理で確定する。
- `send_to_user`送達成功後の終了応答は待機・完了とも`…`だけとし、言い換え・要約・抜粋も再掲しない。
  空応答は再生成を求められるため、未送達でも伝える内容のない待機は`…`を返す。
  新事実・次工程も同ツールで送り、ホストの無発話の注意でも同じ終了規則を守る。
  ツール未提供なら最終応答へ本文を書く。
  `/goal`設定時の担当工程完了だけは`…`を完了の1文に替える（評価が完了記述を読むため）。
  報告の抜粋・言い換え・補足は含めない。
- `/goal`の評価は自身が起動した未完了のAgent・Bashバックグラウンドタスクがある間だけ延期され、無いターン終了時に始まる。
  背景MCP呼出しは延期しない。
  進められる工程は同じターンで進め、実行中の出力は進捗と結果を区別する。
  独立工程後は完了通知を待ち、全出力を確認する。
  待機・回収はgoalの有無・対象によらず`../../skills/delegation/references/waiting-and-monitoring-intervals.md`「待機区間の構成」に従う。
  未配送の完了通知は同一セッション再開が保証され他のStop遮断要因が無ければ次ターンで受け、保存出力・終了状態を回収する。
  再開保証の無い非対話起動は共通の待機規則、他の遮断要因はその理由・回収の警告に従う。
  `/goal`設定時は全待機対象の終了・回収後、最終ターン終了前に共通promptと一致する非durableの定期再確認taskを`CronList`で特定し`CronDelete`で削除する。
  Stop時の残taskが未完了と評価された後にmodが自動回収するためである。
  経過時間起動の義務付き・durable・無関係なcron taskは保持する。
  goal未設定時はmodの自動回収に任せる。
- 計画は全モードで直接作成し、Plan modeの承認待ちへの移行はユーザー操作に委ね、切替後はホスト制約に従う。
