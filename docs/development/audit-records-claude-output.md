# 監査記録: Claude Codeの表示と確認

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年8月31日

2026年8月31日、Claude Code v2.1.251・Fable 5で、ツール結果と次のツール呼び出しの間へ置いた地の文が`· summarized`付きの短縮文へ置換されることを確認した。
この観測では、ターン冒頭と`AskUserQuestion`直前の地の文は原文どおり表示された。

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年9月2日

2026年9月2日、Claude Code公式ドキュメント`https://code.claude.com/docs/en/agent-sdk/user-input`の`Question format`節、`Response format`節および`Limitations`節で確認した。
再検証は同じ3節を読む。

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年9月3日

2026年9月3日、Claude Code v2.1.258・Opus 5では、複数のツール呼び出しの間へ置いた地の文の同じ置換をユーザーが端末表示で確認できなかった。

## agent-toolkit/share/rules-main.claude-code.md：ターンの終え方：2026年9月4日

```text
2026年9月4日、Claude Code 2.1.260で、Bash背景ジョブの稼働中はデバッグログへ`[goal] evaluation deferred — background work still running`が出て評価が発動せず、背景ジョブを起動しない指示では評価が発動することを実測した。再検証は`claude --debug-file <path> --print --session-id <uuid> --permission-mode auto --allowedTools Bash -- '/goal <条件>。まずBashツールをrun_in_background=trueで使い sleep 60 を実行し、その後は追加作業をせずターンを終えること'`を実行してそのログ行の有無を確認し、背景ジョブを起動しない指示でも同じコマンドを実行し、評価の発動を対にして確認する。
```

## agent-toolkit/share/rules-main.claude-code.md：ユーザー確認（`AskUserQuestion`）：2026年9月5日

2026年9月5日、Fable 5.1で`AskUserQuestion`直前の地の文へ選択肢の前提を置いた。
ユーザーはその説明を読めず判断できないと回答し、確認の再発行を要した。
この観測で使ったClaude Codeの版数は記録していない。
再検証はモデルと版ごとに、ターン冒頭、ツール呼び出しの間および`AskUserQuestion`直前の3箇所へ地の文を置き、表示を確認する。

## agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年9月28日

2026年9月28日、Claude Code 2.1.283をtmux内で起動して、複数行の可視本文、Read呼び出し、最後の1行を順に出力するよう指示した。
tmux画面とtranscriptの双方へ、4行の本文、ツール呼び出し、最後の本文が同じ順序で現れた。
一方、同日の別セッションで実行主体がツール前へ書いたつもりの回答と完了報告は、画面にもtranscriptの`text`にも現れなかった。
再検証は新しいClaude Codeセッションへ同じ順序の出力を指示し、画面表示とtranscriptの`text`、`thinking`、`tool_use`を対応付ける。
ツール前の複数行本文が可視となることと、拡張思考へ置いた文が可視本文として配送されないことを分けて確認する。

## agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年10月3日

拡張思考の表示に関する変更は、2026年10月4日「fix: 拡張思考の表示を断定せず発話本文への出力を求める」、`31a598fd5`である。
要求を起草した時点の観測環境はClaude Code 2.1.288、モデル`claude-opus-5-5`、`showThinkingSummaries=false`だった。
transcriptの`thinking`ブロックに保存された文がユーザーの画面へ
通常の応答文と同じ見た目で表示された。
一次記録（当時のセッション記録とユーザーが貼った画面表示）はリポジトリ外にあり、
本節は観測内容だけを保持する。
続くStopはtext本文の欠落を遮断したが、思考の非表示を述べる理由は画面と一致しなかった。
2026年9月28日の記録（Claude Code 2.1.283）では思考の文が表示されなかった。
当時の記録は保持する。
表示の有無はエージェントが応答時に確かめられないため、規範と通知は表示されないものとして発話本文へ書くよう求める。
再検証ではホスト版、モデルと同設定を保持し、ユーザーの画面表示をtranscriptの同一messageのtext・thinkingへ対応付ける。
transcriptだけの取得を画面表示の観測として扱わず、画面へ届いた内容とhookが述べる理由を比べる。

## agent-toolkit/share/rules-main.claude-code.md：ツールの入出力：2026年10月6日

同じ応答でツール呼び出しより前に置いた地の文の一部が、APIの応答の時点で要約へ置き換わる根拠を記す。
観測はいずれも`thinking`ブロックの署名を開かずに行った。

公式文書「Thinking」（<https://platform.claude.com/docs/en/build-with-claude/thinking>、2026年10月7日取得）の節「Progress updates between tool calls」を確かめた。
同節はFable 5.1、Mythos 5.1、Opus 5.5、Sonnet 5.5、Fable 5ではモデルがツール呼び出しの間にprogress updateを書き、直後の`tool_use`の前に独立した`thinking`ブロックとして返ると記す。
返り方は`display`で決まる。
`"omitted"`（これらのモデルの既定）では文面が空で、`"updates"`（beta）では要約文になる。
`"summarized"`では推論のブロックと区別できない要約になる。
`"updates"`については「any `thinking` block with non-empty text is a progress update」「The text you receive is a summary of the progress update」と記す。
どの`display`でも文面が空で返ることがあるとも記す。

Claude Code 2.1.291の本体では、出力の項目`narration_block_indexes`の説明が該当ブロックを「server summaries of the prose between tool calls, not the model's own reasoning」と記す。
項目`narration_hint`の説明はそのブロックの末尾へ`· summarized`を描く規則を端末の表示と同じとする。
本体が要求へ`display: "updates"`とbeta`thinking-display-updates-2026-08-18`を加える条件は次の3つが揃うときである。
環境変数`CLAUDE_CODE_THINKING_DISPLAY_UPDATES`が未設定であること、思考の表示の指定が`omitted`でないこと、設定`showThinkingSummaries`が`true`でないことである。
観測した環境は`showThinkingSummaries`が`false`で、同環境変数を設定していなかった。

判別では`display: "updates"`を要求している記録で、文面を持つ`thinking`ブロックをprogress updateの要約（ナレーション要約）として数える方法とした。
`showThinkingSummaries`が`true`の環境と前掲の環境変数で無効にした環境は、この判別の前提を満たさない。

集計の母集団は、2026年9月1日以降に更新されたClaude Codeのメイン記録のうち2026年10月6日19時13分6秒（日本時間）以前に始まった1466件で、同時刻以前の`isSidechain`でない応答のブロック列を数えた。
数えた層はAPIの応答を保存したtranscriptである。

| モデル | 文面を持つ`thinking` | 後続にツール呼び出しがある`text` | 後続にツール呼び出しが無い`text` |
| --- | --- | --- | --- |
| Opus 5.5 | 3663件 | 12900件 | 4602件 |
| Fable 5.1 | 98件 | 131件 | 47件 |
| Sonnet 5.5 | 12件 | 252件 | 334件 |
| Opus 5、Sonnet 5、Haiku 4.5 | 0件 | 集計対象外 | 集計対象外 |

ツール呼び出しより前の地の文の多くは`text`のまま届いており、順番は要約への置換の必要条件だが十分条件ではない。
文面を持つ`thinking`のうち後続にツール呼び出しが無い4件は、いずれも直後にユーザーの中断があった。
画面の表示は2026年8月31日と2026年10月3日の節が持ち、いずれも要約が表示された点で一致する。

既知の報告としてIssue `anthropics/claude-code#95764`（2026年9月21日作成）が同じ事象を報告し、2026年10月7日時点でOPENで、Anthropicの回答は無かった。

対策の`send_to_user`は2026年10月6日にClaude Code 2.1.291で試作のFunction hooks moduleを使って確かめた。
`claude plugin validate`と`claude plugin test`（`ToolUse`の行を端末と`desktop`で描くテスト）が合格した。
`claude -p --plugin-dir <試作> --model claude-opus-5-5`へ公式ガイドの誘導文を渡し、答えを先に伝えてからファイルを読むよう指示した。
モデルはこのツールを呼び、入力は原文のまま記録された。
tmux内の対話のClaude Code（Opus 5.5）では、ツールで送った見出しと箇条書きが端末の画面へその形で表示された。

再検証は次の2つに限る。
1つ目は本体のバイナリを`rg -a -F 'server summaries of the prose between tool calls'`で検索して`narration_block_indexes`の説明を確かめることである。
検索の手順は`.claude/skills/develop-dotfiles/SKILL.md`「ホスト本体のバイナリの検索」に従う。
2つ目は`display: "updates"`を要求する環境の記録で、`thinking`ブロックを文面の有無で数えることである。

## agent-toolkit/share/rules-main.claude-code.md：ターンの終え方：2026年10月7日

2026年10月7日、Claude Codeのセッション記録を`Your previous response had no visible output`の固定文字列で検索した。
ホストが挿入したこの表示は3件（Claude Code 2.1.281・2.1.282・2.1.283、2026年9月24日から26日）で、いずれも直前のアシスタント応答がthinkingだけを持ち、地の文もツール呼び出しも持たなかった。
同じ文字列を含む残りの記録2件は、ツール結果に同じ文字列が含まれただけだった。
ツール呼び出しだけで地の文を持たない応答は記録に多数あるが、その直後にこの表示は現れなかった。
再検証では同じ文字列を検索し、表示の直前のアシスタント応答が持つ要素の種類を確かめる。
