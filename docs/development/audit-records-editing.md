# 監査記録: ファイル編集

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/writing-standards/references/file-editing.md：ファイル更新時の編集ツールの前提：2026年10月7日

確認日は2026年10月7日、観測した版はClaude Code 2.1.292である。
`agents_server`で起動したClaudeの委譲先（auto mode）で、管理対象一時領域の1行のファイルを使って取得手段ごとにEditとWriteを発行した。
拒否されたのは次の4条件で、文面はいずれも`File has not been read yet. Read it first before writing to it.`だった。

- `printf ... >`で作成したファイルへのEdit
- `cp`で作成した複製へのEdit（複製元はReadで読んだ）
- `cat "$M/..."; sed -n 1p "$M/..."`の1回の連結で表示した2ファイルへのEdit
- 未読の既存ファイルへのWrite

同じファイルをReadで読んだ後の同じEditは成功した。
単独の`cat <絶対パス>`で表示したファイルへのEditは成功し、新しい主セッション（`claude -p`、既定の権限モード）でも同じ条件で成功した。

2026年9月30日にClaude Code 2.1.285で行った再検証では、新しい主セッションと`Agent`ツールで前景起動したサブエージェントで、単純な`cat`、forループの`cat -n`、1201行のファイルを読むforループのいずれでも、Readを経ない初回Editが成功した。
単独の`cat`の成功は2.1.292でも同じであり、拒否はBashでの作成、変数を含むパスや複数コマンドの連結、別パスのReadという取得手段の違いで生じる。
どのBashの読取をRead状態として扱うかは公開されていないため、条文はBashでの表示を前提として扱わない。

Claude Code本体（`~/.local/share/claude/versions/2.1.292`）を`rg -a`で検索すると、Editの検証は読取状態の記録（`readFileState`）にパスが無い場合に前記の文面を返す。
Editのツール説明は、編集の前に同じ会話でファイルを読むことを求め、読まずに編集すると失敗すると述べる。
Writeのツール説明は「Overwriting an existing file you haven't Read will fail.」を含む。

2026年9月7日以降に更新されたClaude Codeのセッション記録1736件（`~/.claude/projects`配下の`*.jsonl`、サブエージェントの記録を含む）では、Read不足で拒否されたツール結果が31件（Edit 26件、Write 5件）あり、17件の記録に分布した。
数えた層は`is_error`を持つ`tool_result`のうち前記の文面を含み、対応する`tool_use`のIDへ結び付いたものである。
同じ期間のEditの呼び出しは1920件、Writeは2464件である。

再検証では管理対象一時領域に1行のファイルを準備し、主セッション、`Agent`ツールのサブエージェントおよび`agents_server`のClaudeの委譲先のそれぞれで発行する。
Readを経ない取得手段（Bashのリダイレクトでの作成、`cp`での複製、変数を含むパスと複数コマンドの連結での表示、単独の`cat <絶対パス>`での表示、同じ内容の別パスのRead）ごとにEditを発行し、未読の既存ファイルへWriteを発行する。
条件ごとに拒否の有無と文面、Readで読んだ後の同じ操作の成否を保存して比べる。
版数は`claude --version`で取得する。
