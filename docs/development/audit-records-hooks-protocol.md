# 監査記録: フックの公開契約

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/writing-standards/references/claude-hooks.md：組織向け設定：2026年11月15日

- 観測日: 2026年11月12日までの既存調査と、2026年11月15日の公式資料の確認。
- 観測した版数: 既存調査はClaude Code 2.1.322。
  doorをdelivery/queueへ限定した後もsession.appendの省略通知が続いた記録である。
  今回の公開資料確認は2.1.324以降のremote marketplaceの組織mod認識条件も含む。
- 2.1.325の実機比較: 同じ一時modへ無条件の`session.receive`と`session.append`（`queue|delivery`）を登録した。
  通常の背景Bashで2秒待機した完了通知は、appendで同じtask-idとcompleted状態を取得でき、receiveには入力がなかった。
  そのため完了検知のappend登録を維持する。
- 再検証手段: [公式のMod管理](https://code.claude.com/docs/en/plugins/mods/admin)の省略イベントと組織modの条件を確認する。
  組織ガード下の新規会話でsession.compactとsession.appendの省略通知を確認する。
  PreCompactの短文化と実タスク完了による待機除外を比べる。
  組織modにした対照では通知と要約生成省略を別々に確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：出力フィールドの使い分け：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルと公式のHooksリファレンスで確認した。
再検証ではこの2つの資料を読み、対象の文字列がどちらに由来するか確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：出力上限：2026年10月20日

- 観測日: 2026年10月20日に[公式Hooksリファレンス](https://code.claude.com/docs/en/hooks#json-output)を再取得した。
  10,000文字の制限は各JSON文字列と通常の標準出力へ個別に適用され、超過時はセッション内の保存ファイルと最大2,000文字のプレビューへ置き換わる。
  標準エラー44,224文字が接頭辞込み44,346文字で全文届いた結果は、2026年10月19日05:33 UTCの既存調査記録に由来し、今回は再現していない
- 観測した版数: 標準エラー配送の既存観測はClaude Code 2.1.303。
  公式ページの再取得は2026年10月20日時点の公開内容
- 再検証手段: 固定したBash操作のPreToolUseへ、連番本文と末尾の`END-MARK-QZX`を標準エラーへ出力して終了2を返すhookを装着する。
  stream-jsonのツール結果との全文一致とモデルが回答した末尾を比べる。
  JSON各値と通常の標準出力は10,000文字の前後で保存先とプレビューを確認する。
  標準エラーの単一条件の成功から、配送量が無制限とは判断しない

## agent-basics/skills/writing-standards/references/claude-hooks.md：組織向け設定：2026年10月20日

- 観測日: 2026年10月20日の起草時調査記録にある、本体の読解と組織設定のユーザー表示の観測による。
  今回の文書追随では本体の読解を再実行していない
- 観測した版数: Claude Code 2.1.304。
  既存記録は2.1.302と2.1.303で該当通知が0件だった比較を含む
- 再検証手段: 対象版の`PASSED_OVER_EVENTS`・`PASSED_OVER_DOORS`・`passedOverPatterns`を公開仕様と比べ、全Function hooks登録へ同じ判定を適用する不変条件テストを実行する。
  組織設定下でセッション名の自動設定、手動名の保持と`rate_limit`後の再開予約を確認する

## agent-basics/skills/writing-standards/references/claude-hooks.md：入力：2026年9月4日

2026年9月4日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節、`Stop`節および`SubagentStop`節で前段の入力仕様を確認した。
同日、Claude Code 2.1.260のStopフックへ渡る入力を捕捉した。
`run_in_background`で起動したBashジョブが、`type`を`shell`、`status`を`running`とする要素として`background_tasks`へ現れた。
再検証は同3節を読み、Stopフックへ渡る入力を捕捉して`background_tasks`の有無と要素の構造を確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント<https://code.claude.com/docs/en/hooks.md>の`Common input fields`節と`SubagentStop`節で確認した。
再検証は同2節を読む。

## agent-basics/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日

2026年9月4日、Claude Code 2.1.260の実行ファイルへ埋め込まれたパターンマッチ関数を確認した。
この関数は値が空文字列と`"*"`のいずれかのときに正規表現へ変換せず一致を返す。
同ドキュメントにも同じ3分類が記載されていた。
再検証は同ドキュメントの`Matcher patterns`節を取得し、`strings`で抽出した関数が空値と`"*"`を短絡することを確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日

2026年9月6日、Claude Code 2.1.263で次を確認した。
地の文を1文書いた直後に同じ応答で`Bash`を1回呼ぶ指示を与え、その呼び出しの`PreToolUse`が受領した`transcript_path`の内容を捕捉した。
フックの発火時点では、JSONLにアシスタントのエントリが1件も無かった。
実行後の同じJSONLには、同一の`message.id`を持つ思考ブロック、テキストブロックおよびツール呼び出しの3エントリが並んでいた。
再検証は同じ指示を与えて発火時点のJSONLの内容と実行後の内容を比較する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日

2026年9月8日、Claude Code 2.1.263で次を確認した。
存在しないリポジトリを指す`git -C /tmp log --oneline -1`は終了コード128で終わり、セッションの状態ファイルの`git_log_checked`は未設定のままだった。
続けて実在するworktreeを指す同じ形の`git log`を実行すると、そのcwdのキーが真になった。
再検証はこの2つのコマンドを単独で順に実行し、`session_state.state_path`が返す現行JSONの`git_log_checked`を前後で比較する。
当時の保存先は`{tempdir}/claude-agent-toolkit-<session_id>.json`だった。
現行の再検証では`state_dir()/hook-sessions`のJSONと固定の`.state.lock`を使い、clear・期限回収後もlockが同じパスへ残ることを確認する。

## agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年10月7日

2026年10月7日、Claude Code 2.1.292の環境で公式ドキュメント<https://code.claude.com/docs/en/hooks.md>を取得し、`PostToolUseFailure`節と`PermissionDenied`節を確認した。
前者は実行を始めたツールの失敗で発火し、`additionalContext`を受理する。
未知のツール名、入力検証の失敗と権限拒否では発火しない。
後者はauto modeの拒否だけで発火し、手動の拒否、`PreToolUse`の遮断とdeny規則の一致では発火しない。
出力は`hookSpecificOutput.retry`だけを受理する。
再検証は同ドキュメントの両節を読み、発火条件と受理する出力を確かめる。

## agent-basics/skills/writing-standards/references/claude-hooks.md：出力フィールドの使い分け：2026年11月11日

- 観測日: 2026年11月10日と11月11日。
- 確認版: Claude Code 2.1.322、公式Hooksリファレンス（<https://code.claude.com/docs/en/hooks>、2026年11月11日取得）。
- 再検証: 公式Hooksリファレンスの「PreToolUse decision control」と「Add context for Claude」を再取得する。
  `additionalContext`が「alongside the tool result」としてエージェントへ届き、「on the next model request」で読まれる記述が残るかを確かめる。
  実機では、PreToolUseで`additionalContext`だけを返すhookを置いて`Agent`ツールを呼び、呼び出しが実行されたうえで通知がツール結果と同時に表示されることを確かめる。
  2026年11月10日には、組み込みのweb-fetchとExploreの起動に対する警告が起動を止めず、起動の結果と同時に表示された。

## docs/development/design-hosts-rules-delivery.md：SessionStartの整理後の水準：2026年11月15日

最終出力10,000文字の上限に対し、超過を是正した後の目安を8,000文字とする。
これはエージェントの選定値であり、ホストの新しい制限や実測した追記量ではない。
固定規範・案内に加え、最大2,000文字の追加指示、長いパス、契約の囲みと通知、圧縮後の案内が生成時に増えるため、単一の短い起動結果から選ばない。
既存のrules_context_startup_test.pyが使うパス長21・241文字、Windows・Linux、契約なし・短い契約・長い契約の組合せで最終出力を比較する。
上限との差2,000文字は、数回の段落追記を吸収する余裕として選んだ見積もりであり、固定部分の必須条件を削る許可にはしない。
共通方針は`agent-basics/skills/writing-standards/references/agent-documents-additions.md`「記述量の水準」に従い、上限以下では整理水準のための測定・削減を追加しない。
2026年11月15日、規範の場面固有部分を条件別資料へ移設し、既存の容量条件21件が成功した。
変更後の生成を手動で観測したWindows・241文字パス・最大追加指示・長い契約の条件は7,978文字、WindowsのSubagentStartは7,538文字だった。
これは当該条件の観測であり、全環境の最大値ではない。
再検証は同じ既存試験と生成条件を使い、最終additionalContextを文字数で比較する。
8,000文字との比較は超過是正時だけに行い、常時の合否上限は10,000文字を保つ。

## agent-toolkit/share/rules/rules-main.claude-code.md：ツールの入出力：2026年11月15日

- 観測日: 2026年11月15日。
- 観測版: Claude Code 2.1.325、Opus 5.5、Linuxのtmux対話会話。
- 観測結果: 初回確認後にCode Modeの有効表示、REPLによる2ファイルの読取、指定した本文の実描画と`send_to_user`の`ok:true`を確認した。
  初回は`<synthetic>`を持つ実ツール呼出しをStopが応答へ数えず誤警告したため、API失敗の代替本文との区別を修正した。
  修正後は模擬transcriptのStop試験で、実ツール呼出しを応答へ数え、API失敗の本文を応答へ数えない結果を確認した。
  API失敗自体の実機再現は行っていない。
- 再検証: 作業ツリーのagent-toolkitを`--plugin-dir`で読む。
  ユーザー設定の`env.CLAUDE_CODE_EXPERIMENTAL_CODE_MODE="1"`を有効にした新規会話で初回確認を選ぶ。
  Code Modeの有効表示とREPLによる2ファイルの読取を確認する。
  `mcp__plugin_agent-toolkit_agents_server__send_to_user`から送った原文の画面表示と`ok:true`を対応付ける。
  transcriptの実ツール呼出しが`<synthetic>`を持つ場合も、API失敗の代替本文と区別してStopの応答有無判定を確認する。
  送達した会話を`/exit`で終えて`claude --resume`で開き、各本文が元の呼び出し位置に1回ずつ読めることを確認する。
