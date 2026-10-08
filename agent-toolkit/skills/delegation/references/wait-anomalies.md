# 待機の異常時の扱い

本書は`atk agents wait`と待機中のツール呼び出しが異常で終わった場合と、`agents_server`のMCPサーバーが再起動した後の扱いを定める。読む時点は`agent-toolkit:delegation`の読込表が定める。平常時の待機は`references/waiting-and-monitoring.md`が定める。

## 終了コードと呼び出しの失敗

- `atk agents wait`は`agents_server`の状態ディレクトリを解決できるエージェントでだけ成立する。解決できないエージェントでは終了コード4で終わるため、その事象と実行環境を委譲元へ返す
- `atk agents wait`が返す終了コード8は、run記録を持たない旧形式の待機との競合、先行run記録の読取不能、未公開または回収済みを示す。待機対象の異常とは別の事象として、診断本文を委譲元へ返す
- `atk agents wait`の終了コード10は、委譲元の状態へ待機対象が登録されていないことを示し、委譲先の実行失敗を示さない。
  委譲元が受け取った起動応答と`atk agents list`で`agents_server`のsessionが存在するかを確認し、実行ホストの組み込み委譲として起動した対象はそのホストの委譲一覧と指定成果物で終端を観測する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/wait-anomalies.md：終了コードと呼び出しの失敗：2026年9月20日」にある
- `atk agents wait`が、待機対象の登録と`starting`を含む保持中sessionがともに0件で非0終了した場合は、回収対象が存在しないため、再発行に代えて委譲元が受け取った起動応答を確認する。保持中sessionがある場合は通常の待機上限まで待つ
- 実行ホストの上限により`atk agents wait`の呼び出し自体が失敗した場合も、待機対象のsessionは終端せず実行を続ける。
  `atk agents list`でそのsessionの`status`を確認し、CLIを再実行して待機を継続する。
  停滞、中断および再起動の判定は、この`status`の確認で行う
- 委譲先と自身のツール呼び出しが、実行環境のアイドル上限によるアボート、待機上限の超過、無応答のままの中断のいずれかで終端statusを返さずに終了した場合を対象とする。
  この事象は実行基盤の欠陥の観測として扱い、待機するエージェントが`agent-toolkit:bugfix`を起動して`agent-toolkit/skills/bugfix/SKILL.md`「問題を見つけたときの対処」の手順へ送る。
  待機の継続判定はこの事象と切り離して行い、欠陥の処置の確定と待機の継続判定の両方を実行する。
  異常終了の通知を待機の続行判断だけで消費すると、同じ欠陥が別のセッションで繰り返し発生する

## MCPサーバーの再起動後

- `agents_server`のMCPサーバーが再起動した場合、同じ`session_id`はsession登録簿から遅延解決する。
  終端が確定している記録だけを同じ識別子の結果観測と会話再開へ用いる。
  MCPサーバーは正常に停止するとき、実行中だったturnを`interrupted`として登録簿へ公開する。
  強制終了などで`running`のまま残ったCodexの記録は、どのMCPサーバーもそのsessionを保持していない状態でCodexのthreadの記録がturnの終端を示す場合に限り、再起動後の`show`か`send_message`の照会で終端として引き継がれる。
  終端が確定していない場合は`error.recovery=turn_unobserved`を受け取り、同じ作業を新しい`start`でやり直さず、そのsessionが実行中である可能性を添えて委譲元へ返す。
  `missing`、`unreadable`、`no_resume_info`はそのsessionを失われたものとして扱ってよい
