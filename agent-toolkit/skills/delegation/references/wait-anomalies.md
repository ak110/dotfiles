# 待機の異常時の扱い

本書は`atk agents wait`と待機中のツール呼び出しが異常で終わった場合、ホストから背景待機の停止通知を受けた場合と、`agents_server`のMCPサーバーが再起動した後の扱いを定める。平常時の待機は`references/waiting-and-monitoring.md`が定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |

## 終了コードと呼び出しの失敗

- 終了コード4: `agents_server`の状態ディレクトリを解決できない実行環境である。事象と実行環境を委譲元へ返す
- 終了コード8: run記録を持たない旧形式の待機との競合、先行runへの合流中の待機期限到達、先行run記録の読取不能、未公開または回収済みを示し、待機対象の異常とは別の事象である。競合の期限到達では書込主体、固定run識別子、待機対象と`run_path`の絶対パスを返す。先行するバックグラウンドタスクの保存出力を読み、先行待機の終了後に再待機する。保存出力を受け取れない場合は`atk read-file -- <run_path>`でrun記録の`output`を読み、そこにある`agent_message_path`から本文を読む。本文ファイルが無くても`output`の`agent_message`を読める。取得失敗時も原本と耐久退避、先行runを保持し、診断本文を委譲元へ返す
- 終了コード10: 委譲元の状態に待機対象が登録されていないことを示し、委譲先の失敗とは別の事象である。起動応答と`atk agents list`でsessionの存在を確かめ、ホストの組み込み委譲で起動した対象はそのホストの委譲一覧と指定成果物で終端を観測する。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/wait-anomalies.md：終了コードと呼び出しの失敗：2026年9月20日」にある
- 待機対象の登録と`starting`を含む保持中sessionがともに0件で非0終了した場合は、回収対象が無いため再発行せず起動応答を確かめる。保持中sessionがあれば通常の待機上限まで待つ
- ホストの上限やメモリー不足で背景待機の停止通知を受けた場合は、次節の判断で元の待機と結果を回収する。委譲先の停滞・中断・再起動は`atk agents list`のsessionの`status`で判定する
- 委譲先か自身のツール呼び出しが終端statusを返さずに終わった場合（アイドル上限によるアボート、待機上限の超過、無応答のままの中断）は、実行基盤の欠陥として扱う。`agent-toolkit:bugfix`を起動して`agent-toolkit/skills/bugfix/SKILL.md`「問題を見つけたときの対処」へ送る。待機の継続判定もそれとは別に行う（続行の判断だけで済ませると、同じ欠陥が別のセッションで繰り返し起きる）

## ホストによる背景待機の停止後

停止通知の`killed`は外側のバックグラウンドタスクの終了を示す。
起動結果と停止通知が示す実行識別子・元の出力ファイルを保持し、外側のシェルと、その起動からたどれる待機の子プロセスの終了を分けて確かめる。
子プロセスの同定には起動時刻・親子関係・コマンドを組み合わせ、同じ実行であることを確かめる。

同じ待機の子が生存している間は、既存の完了通知または定期再確認で終了を観測し、元の出力ファイルから結果本文の全量と終了状態を回収する。
先行待機がロックを保持する間の後発待機の失敗は、先行待機の停止や結果喪失を意味しない。
元の待機が終了し、元の出力にも必要な終端結果が無い場合は、既存の担当と結果を保持して同じ対象の観測を再開する。
結果観測だけの再開は、ユーザーの再依頼や確認を待たず自律的に行う。
結果が既に回収済みなら、その結果から後続工程へ進む。

CI監視にも同じ判断を使い、既存のSHAとbaselineまたは待機専用記録で`wait_ci.py`の観測を続ける。
この操作はCIジョブや委譲先の実処理を再起動しない。
ホスト注記の行動指示は既存の優先順位で扱い、実際の権限拒否、技術的不成立と区別する。
権限拒否は`agent-toolkit:user-confirmation-and-report`の`references/permission-denial.md`、実処理の再起動は既存の認可と副作用の判断へ返す。
Codexのcell IDとコマンドsession IDは、公開された同一実行の継続機能で回収する。

## MCPサーバーの再起動後

再起動後も同じ`session_id`はsession登録簿から遅延解決され、終端が確定した記録だけを結果観測と会話再開に使える。正常停止したサーバーは実行中だったturnを`interrupted`として公開する。強制終了で`running`のまま残ったCodexの記録は、どのサーバーもそのsessionを保持しておらず、Codexのthreadの記録がturnの終端を示す場合に限り、`show`か`send_message`の照会で終端として引き継がれる。

- `error.recovery=turn_unobserved`: 終端が未確定である。同じ作業を新しい`start`でやり直さず、実行中の可能性を添えて委譲元へ返す
- `missing`、`unreadable`、`no_resume_info`: そのsessionは失われたものとして扱ってよい
