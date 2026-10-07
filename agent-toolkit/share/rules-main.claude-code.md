# rules-main.claude-code.md: Claude Codeのメインエージェントだけに適用する規範

本書はClaude Codeのメインに適用する。共通判断は`01-agent.md`と`02-agent-operations.md`に従う。

利用上限の猶予通知を受けたときは、`agent-toolkit:user-confirmation-and-report`を起動し、同スキルが定める状況伝達・続行・再開の手順を適用する。

## ツールAPIと権限

- Windows版Claude CodeのBashツールは引用符付きheredocや単一引用符内でもコマンド中の`\\`をシェルへ渡す前に`\`へ縮め、エスケープ表記を含むコードを直接渡すと成果物へ制御文字や改行を書き込む。そのコードはWriteツールでmanaged-tempの中のファイルへ保存してから実行する。
- ツール呼び出しも地の文もない応答はホストが再生成を求める。待機だけでターンを終え、伝える内容がない場合に限り`…`を出力する。通常のツール呼び出しへ付けると不要な文章が積み上がる。監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年10月7日」にある。
- ユーザーの質問への回答、確認結果および作業完了報告は、拡張思考ではなく発話本文へ置く。拡張思考（その要約を含む）が画面に表示されたかはエージェントから観測できないため、表示されないものとして扱う。同じ応答でツール呼び出しより前に置いた地の文は、APIがモデルの原文を要約へ置き換えて返すことがあり、その場合ユーザーには要約だけが届く。このため、ツール呼び出しより前にユーザーへ原文どおり届ける内容は`send_to_user`ツールの`message`へ書く。対象は質問への直接の回答、確認結果、判明した事実や原因、作業完了報告、振り返りの予告などで、作業経過の説明と推論は通常の本文に書く。ターンを終える応答の本文（最後のツール呼び出しより後の本文）も通常の本文として書く。ツール一覧に`send_to_user`が無い場合（Function hooks moduleを読み込まないClaude Code）は、届ける内容をターンを終える応答の本文へ書く。委譲先への入力や記録へ「回答済み」と書けるのは、`send_to_user`か、ターンを終える応答の本文で伝えた回答に限る。根拠の観測は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月28日」にある。異なる表示の観測は同ファイルの「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年10月3日」にある。要約への置換の根拠は同ファイルの「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年10月6日」にある。
- `AskUserQuestion`を呼ぶ前に、`agent-toolkit:user-confirmation-and-report`の読込表の`references/approval-scope.md`と`references/choice-construction.md`を全文読む（会話圧縮の後は読み直す）。そのうえで各質問へ同スキル「確認要否の判定」を当てる。確認を要しないと判定した質問は発行せず、自ら確定して採った案と根拠を発話本文で報告する。発行する質問は次に従って組む。
  - `AskUserQuestion`は1回に質問1〜4件、各質問の選択肢2〜4件を受け取る。自由記述の回答は質問へ対応し、一般の返答は別の`response`へ入る。監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月2日」にある。
  - 呼び出し前の地の文は要約されることがあるため、判断材料と、直前の回答に含まれた問いへの答えを質問または`preview`へ含める。監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年8月31日」にある。同じ箇条の記録は「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月3日」と「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月5日」にもある。
  - 推奨案は`(Recommended)`を付けた第1選択肢として置く。`共通前提:`行と質問・選択肢の組み方は同スキル「確認の選択肢を組む手順」に従う。
- 質問と承認待ちでターンを終える場合は`AskUserQuestion`を使う。地の文で問いを示す場合も、回答待ちの登録には`AskUserQuestion`を使う。
- `/goal`があるセッションでは、自分が起動した未完了のAgentタスクやBashのバックグラウンドタスクがない状態でターンを終えると目標評価が始まる。MCPの背景移行はこの延期に含まれない。技術的に成立する工程を同じターンで進める。監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月4日」にある。
- モードによらず計画ファイルを直接作成し、Plan modeの承認待ちへの遷移はユーザーの操作に委ねる。ユーザーがPlan modeへ切り替えた場合はホストの制約に従う。
