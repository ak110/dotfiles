# 待機の異常時の扱い：終了コードと呼び出しの失敗

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 待機の呼び出しが拒否された時、またはclassifierが判定不能を返した時 | `../../user-confirmation-and-report/references/permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |
| 必須操作を報告する前 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

## 終了コードと呼び出しの失敗

- 終了コード4: `agents_server`の状態ディレクトリを解決できない実行環境で発生する。
  発生した事象と実行環境の情報を委譲元へ報告する。
- 終了コード8: run記録を持たない旧形式の待機との競合、先行runへの合流中に待機期限へ達したこと、先行run記録の読み取り失敗、または記録が未公開・配信済み・回収済みであることを示す。
  配信済みrunへの合流では本文を再出力せず、`run_path`を含む回復情報を返す。
  これは待機対象自体の異常とは異なる。
  競合によって期限に達した場合は、書き込み主体、固定run識別子、待機対象、および`run_path`の絶対パスを返す。
  先行するバックグラウンドタスクの保存出力を確認し、先行待機が終了した後に改めて待機する。
  保存出力を取得できない場合は、`jq -r .output -- <run_path>`でrun記録の終了行を確認し、記載されている`agent_message_path`から本文を読み取る。
  取得に失敗した場合でも原本、退避ファイル、先行runを保持し、診断情報を委譲元へ報告する。
- 終了コード10: 委譲元の状態管理に待機対象が登録されていないことを示す。
  これは委譲先自体の失敗とは異なる。
  起動時の応答や`atk agents list`でセッションが存在するか確認する。
  ホストの組み込み機能で起動した委譲対象については、そのホストの委譲一覧や指定された成果物から終了を観測する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/wait-anomalies.md：終了コードと呼び出しの失敗：2026年9月20日」を参照する。
- 待機対象の登録と`starting`状態の保持中セッションがいずれも0件の状態で非0終了した場合は、回収すべき対象が存在しないため再実行せず、起動時の応答を確認する。
  保持中のセッションが残っている場合は、通常の待機上限まで待機を継続する。
- ホストのリソース上限やメモリー不足によって背景待機の停止通知を受け取った場合は、次節の手順に従って元の待機状態と実行結果を回収する。
  委譲先が停滞・中断・再起動しているかどうかは、`atk agents list`で確認できる各セッションの`status`で判定する。
- 委譲先または自分自身のツール呼び出しが終了ステータスを返さずに終了した場合（アイドル上限によるアボート、待機上限の超過、無応答による中断など）は、実行基盤側の欠陥として扱う。
  `agent-basics:bugfix`を起動し、`agent-basics:bugfix/references/root-cause-analysis-initial.md`「問題を見つけたときの対処」に従って対処する。
  あわせて、待機を継続するかどうかも個別に判定する（単に続行するだけでは、同じ欠陥が別のセッションでも再発するためである）。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 初動と直接的原因の確認：問題を見つけたときの対処の基準を適用する前 | `agent-basics:bugfix/references/root-cause-analysis-initial.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-initial.md` |
