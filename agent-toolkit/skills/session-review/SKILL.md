---
name: session-review
description: >
  セッションの振り返りを実施するときに起動する。
  ユーザーの手動起動又はagent-toolkit:completion-reportからの明示呼び出しで起動する。
---

# セッション振り返り

次のセッション以降で同じユーザー介入、レビュー反復、手戻り又は無駄が再発しないよう、現セッションの問題を独立コンテキストで洗い出し、メインが恒久対策を確定する。

## 問題候補の抽出

1. `agent-toolkit:delegation`を起動し、起動直前に`atk config get orchestrate_model`で実効engine・model・effortを解決する。
2. メインが読み込んだ本`SKILL.md`の絶対パスから現行plugin rootを確定し、`scripts/_session_review_evidence.py`の実在を確認する。実在しなければ分析失敗として扱う。
3. `agents_server`で通常の読み取り専用サブエージェントを1つ起動する。専用agent定義、固定モデル及び代替モデルを使わない。
4. Claude Codeでは、メインが把握する現在のtranscript絶対パスと、確認済み抽出器の絶対パスを渡す。Codexではメインの`CODEX_THREAD_ID`と同じ抽出器絶対パスを渡し、サブエージェントが`$CODEX_HOME/sessions/*/*/*/rollout-*<thread-id>.jsonl`から完全suffix一致する正本1件を選ぶ。backupを検索せず、0件又は複数件なら`evidence_insufficient`とする。
5. 初回依頼には振り返りの起動理由を含めない。サブエージェント自身が、受領した絶対パスの抽出器について通常表示、`--warn`、`--stats`、`--hook-notices`及び必要な`--grep`・`--detail`をセッション全体へ実行する。
6. 走査では、委譲先の完了報告とエスカレーションに現れる手順の前提不成立、再試行及び自力回避も問題候補へ含める。委譲先が自ら回避して作業を続けた事象も、同じ回避が複数の委譲先で現れる場合は候補とする。
7. サブエージェントは`status`、各走査の成否、問題候補ごとの要約・観測事象・再取得用query・locator・未確認事項及び全体の未確認範囲だけを返す。原因、解決案、反映先、採否、フィードバック本文はいずれも作成しない。

メインはサブエージェントのqueryとlocatorが指す証拠だけを再取得する。再取得後、Claude Codeの`~/.claude/references/session-review-dotfiles.md`又はCodexの`~/.codex/references/session-review-dotfiles.md`が存在すれば、対応する文書をメインが全文読む。この参照文書を通常サブエージェントへは渡さない。セッション全体の要約・再抽出のいずれも作成しない。

ユーザーによるエージェントの間違い等の指摘又は同じ成果物・責任範囲のレビュー第3ラウンドが起動理由であり、対応する候補が初回出力に無い場合は、同じ`agents_server` sessionへ`send_message`で当該事象と証拠位置を送り、対象を再確認させる。別sessionを起動しない。

## 原因と対策

問題候補が1件以上なら`agent-toolkit:bugfix`を起動し、観測事象、2系統4段階の原因分析、原因分析の品質確認、原因起点の類似見直し、是正・横展開・再発防止及び反映要否をメインが確定する。

再発防止処置の必須性、省略できる条件及び良い案を確定できない場合の終端は`agent-toolkit:bugfix`の再発防止処置の必須性節に従う。`process-feedbacks`だけが起動理由で問題候補が無い場合は、対策なしで完了できる。

採用した対策のフィードバック又はTBDは`agent-toolkit:feedback-standards`に従って起草・重複判定・投入する。本スキルは本文形式・CLI手順のいずれも定義しない。

## 分析失敗

サブエージェント起動、親transcript解決又は証拠抽出が失敗した場合は、同じ実効設定で1回だけ自動再試行する。再失敗時は、推奨案「TBDへ記録して元作業を継続」と代替案「その場で再試行」を`AskUserQuestion`で確認する。

回答期限超過時は`agent-toolkit:feedback-standards`に従い、セッションID、失敗事象、解除条件及び「当該セッションをresumeしてsession-reviewを再実行する」再開工程をTBDへ記録する。transcriptパスは保存しない。メインによる全量分析又は別モデルへのフォールバックは行わない。

手動起動では分析未完了を報告できる。`process-feedbacks`から起動した場合は、分析未完了とTBDを`completion-report`へ返し、元作業の完了を妨げない。
