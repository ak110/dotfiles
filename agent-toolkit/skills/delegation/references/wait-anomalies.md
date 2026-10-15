# 待機の異常時の扱い

本書は`atk agents wait`と待機中のツール呼び出しが異常で終わった場合と、`agents_server`のMCPサーバーが再起動した後の扱いを定める。平常時の待機は`references/waiting-and-monitoring.md`が定める。

## 終了コードと呼び出しの失敗

- 終了コード4: `agents_server`の状態ディレクトリを解決できない実行環境である。事象と実行環境を委譲元へ返す
- 終了コード8: run記録を持たない旧形式の待機との競合、先行run記録の読取不能、未公開または回収済みを示し、待機対象の異常とは別の事象である。診断本文を委譲元へ返す
- 終了コード10: 委譲元の状態に待機対象が登録されていないことを示し、委譲先の失敗とは別の事象である。起動応答と`atk agents list`でsessionの存在を確かめ、ホストの組み込み委譲で起動した対象はそのホストの委譲一覧と指定成果物で終端を観測する。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/wait-anomalies.md：終了コードと呼び出しの失敗：2026年9月20日」にある
- 待機対象の登録と`starting`を含む保持中sessionがともに0件で非0終了した場合は、回収対象が無いため再発行せず起動応答を確かめる。保持中sessionがあれば通常の待機上限まで待つ
- ホストの上限で`atk agents wait`の呼び出し自体が失敗しても、sessionは実行を続けている。`atk agents list`で`status`を確かめてCLIを再実行し、停滞・中断・再起動の判定もこの`status`で行う
- 委譲先か自身のツール呼び出しが終端statusを返さずに終わった場合（アイドル上限によるアボート、待機上限の超過、無応答のままの中断）は、実行基盤の欠陥として扱う。`agent-toolkit:bugfix`を起動して`agent-toolkit/skills/bugfix/SKILL.md`「問題を見つけたときの対処」へ送る。待機の継続判定もそれとは別に行う（続行の判断だけで済ませると、同じ欠陥が別のセッションで繰り返し起きる）

## MCPサーバーの再起動後

再起動後も同じ`session_id`はsession登録簿から遅延解決され、終端が確定した記録だけを結果観測と会話再開に使える。正常停止したサーバーは実行中だったturnを`interrupted`として公開する。強制終了で`running`のまま残ったCodexの記録は、どのサーバーもそのsessionを保持しておらず、Codexのthreadの記録がturnの終端を示す場合に限り、`show`か`send_message`の照会で終端として引き継がれる。

- `error.recovery=turn_unobserved`: 終端が未確定である。同じ作業を新しい`start`でやり直さず、実行中の可能性を添えて委譲元へ返す
- `missing`、`unreadable`、`no_resume_info`: そのsessionは失われたものとして扱ってよい
