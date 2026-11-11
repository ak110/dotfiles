# 監査記録: 委譲の待機と結果回収

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：背景ジョブ完了通知と実プロセスの終了順：2026年9月21日

2026-09-21に、Claude Codeの背景ジョブ完了通知が届いた後も、同じ`atk agents wait`の実プロセスが稼働している事例を観測した。
通知後に`ps -eo pid,etimes,args`を実行し、PID 3566782、3566794および3566974の同じ待機が経過時間155秒で残っていることを確認した。
旧実装ではこの状態で後続の`atk agents wait`を発行すると、待機所有権が残っているため終了コード8になり得た。
同じ処理回の背景タスク`bjhq8zib4`では、警告1行だけを持つ1回目の`completed`通知後に後続の待機が終了コード8を返した。
呼び出し元が待機と照会を発行せずターンを終えた後、同じ背景タスクが結果本文を伴う2回目の`completed`通知を送った。
再検証は背景タスクの通知と`ps -eo pid,etimes,args`の結果を比較し、同じ待機プロセスの生存と後続の`atk agents wait`の終了コードを確認する。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：待機中の定期再確認と背景転換：2026年10月4日

HEAD 2026年10月4日「fix: 不変条件のテストを通常のcommitで自動実行する」、`80ce39d82`、agent-toolkit 2.188.0の公開CLIで`atk watch --help`を確認した。
保持記録から解決した値を`atk watch --worktree "$worktree_path"`と
`atk watch --file "$artifact_path"`へ個別に渡し、両方の終了コード0を確認した。
前者は作業ツリーのdirty件数とHEAD、後者はファイルのlinesとageを返した。
Cronの発火や委譲sessionの終了はこの観測の対象へ含めず、状態と完了通知を用いる既存契約を保つ。
再検証は同じ公開ヘルプを取得し、その回の読み取り可能な作業ツリーと通常ファイルを保持記録から解決して、
上記の各コマンドへ渡す。
cwd、コマンド、標準出力、標準エラーと終了コードを保存し、項目と受理形式を比べる。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：一時領域と成果物の観測：2026年10月9日

2026年10月9日10:49～10:50 JST、Claude Code 2.1.295で、メインが`Agent`を使って背景の委譲先1件を起動した。
委譲先の作業は`sleep 5; echo`を4回実行するだけとした。
稼働中の`output_file`へ`stat`と`stat -L`を対にして6秒間隔で3回実行した。
リンク自体はサイズ153バイト・更新時刻10:49:55.606955323で固定だった。
リンク先は通常ファイルで、サイズ231295、334100、337929バイト、更新時刻10:49:58、10:50:05、10:50:13へ進んだ。

観測は対象worktreeのcleanなHEAD `6c062a92ac0be19953900a48b14337992fdda7ca`で`atk run-command`を使って保存され、子終了コード0、標準エラー0バイトだった。
再検証ではClaude Codeの版を記録し、同じ背景作業を`Agent`で起動する。
起動結果の`output_file`へ`stat -c '%s %y %F'`と`stat -L -c '%s %y %F'`を同じ間隔で対にして実行し、リンク自体とリンク先の値を比較する。
補助観測の更新は終了判定へ代用しない。

## agent-toolkit/skills/delegation/references/wait-anomalies.md：終了コードと呼び出しの失敗：2026年9月20日

2026年9月20日、`atk agents wait`が`agents_server`の状態投影に対象を持たない状態を終了コード10で報告し、実行ホストの組み込み委譲は同じ状態投影へ登録されないことを確認した。
再検証は`agents_server` sessionと組み込み委譲をそれぞれ起動し、`atk agents list`への登録有無、`atk agents wait`の終了コードおよびホストの委譲一覧が返すstatusを比較する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月2日

2026年9月2日に別のセッションが起動した背景ジョブの実行識別子を`TaskOutput`へ渡すと、`No task found with ID`で拒否された。
一方、そのジョブの出力ファイルには絶対パスで到達でき、全出力と`[exited with code 0]`が残っていた。
再検証は同じ形で背景実行し、終了済みセッションが残した出力ファイルを絶対パスで読めることと、同じ実行識別子を`TaskOutput`が解決できないことを対にして確認する。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年9月3日

2026年9月3日から9月4日にかけて、`agents_server`の`start`で起動した委譲先が外部コマンドの完了待ちを表明し、`status: completed`で終了した。
その後、待機対象のコマンドが終了しても委譲先は再開しなかった。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：背景ジョブの起動形（Claude Code）：2026年9月4日

2026年9月4日、Claude Code 2.1.260で次の2点を確認した。
終了コード7で終わる`run_in_background=true`のBashについて、起動結果が返した出力ファイルは出力の全量と`[exited with code 7]`を保持した。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：進捗照会と結果回収：2026年10月10日

Claude Code公式資料「Keep Claude working toward a goal」の「Background work defers evaluation」（<https://code.claude.com/docs/en/goal>、2026年10月10日取得）を読解した。
背景のエージェントやシェルが稼働している間は評価を保留し、進捗を確認する通知と、完了結果による新しいターンを区別する仕様である。
本改訂時にGoalを実機で起動した観測ではない。

2026年10月9日、Claude Code 2.1.295のセッションで次の順序を観測した。
一次記録はリポジトリ外のセッション記録にある。
Goal check-inの後、未完了の`atk agents wait`出力を`cat`で読み、同じ出力の2回目の読取がblockされて終了コード2となった。
同じ応答で発行した`agents_server`の`list`は遮断の後に活動情報を返した。
遮断後に新しく照会した因果にはしない。

再検証は同じ公式節を取得する。
hookの判定は`background_task_outputs_test.py`で、明示背景化・実行上限・手動移行の未完了出力を同一sessionで2回読む入力、完了通知を加えた入力、別パスを読む入力を比較する。
2回とも警告でblockが空、完了後は警告も空であることを確認する。
Goalの自然発生を待たず、進捗の照会を終了時の結果へ代用しない。

## agent-toolkit/skills/delegation/references/waiting-claude-code.md：Codex最上位の待機条件：2026年11月2日

- 観測日: 2026年11月2日。
- 確認版: Codex CLI0.166.0。
  公開execのcell/command識別子は同一turnのwait/write_stdinで回収する。
  App Serverの圧縮・使用量通知とCLIのproxyは公開されているが、この最上位待機形はturnを閉じるClaude背景waitと異なる。
  agents_serverの所有backendだけが、turn完了後に担当結果を保留し未完了子を待つ状態を持つ。
- 再検証: 公開execで待機コマンドを起動し、返る実行識別子と同一turnでの終了回収を確認する。
  `codex --version`、`codex app-server --help`、`codex queue --help`と対象版生成schemaで圧縮入力・使用量通知を確認し、最上位クライアントがターン間背景待機を提供する場合はその所有者と状態遷移を再判定する。
  既存のユーザー会話へ検証用の圧縮を送らない。

## agent-toolkit/skills/delegation/references/waiting-and-monitoring.md：待機区間の構成：2026年11月2日

- 観測日: 2026年11月2日。
- 確認版: Python 3.15.0、pytest 9.2.0。
  非対話Claude Codeの背景無効化時に、Bash前景上限600000ミリ秒と背景引数の不在を観測した起票元の記録を再利用した。
  その外側観測のClaude Code版番号は記録から特定しておらず、担当自身による外側の再測定は未実施である。
  公開CLIと警告hookの試験では、背景無効・TTL 1hの540秒、終了コード3、再待機後の終了結果回収を模擬時計で確認した。
- 再検証: 同条件のClaude Codeで公開Bashの引数定義を取得し、背景無効時の前景上限と背景引数の有無を確認する。
  下記の試験でCLI期限、MCP期限の維持、警告と受理できる回収操作の対応を検証する。
  実時間で540秒を待つ試験とは区別する。

```sh
uv run --frozen pytest -v -p no:cacheprovider \
  agent-toolkit/agent_toolkit/_agents_server/agents_wait_foreground_test.py \
  agent-toolkit/agent_toolkit/_hooks/pretooluse/shell_wait_guidance_test.py \
  agent-toolkit/agent_toolkit/_common/wait_foreground_test.py
```
