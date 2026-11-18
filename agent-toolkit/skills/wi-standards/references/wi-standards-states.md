# AWIとUWIの共通規範：状態と依存

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| WIの状態と依存を判定・変更する前 | `states-and-dependencies.md` |
| 上流リポジトリへの投入を選ぶ前 | `cross-repository-submission.md` |

## 状態と依存

WIの保存先は`private-notes/yyyy/MM/<filename>`に固定する。
項目の種別と保存状態は、frontmatterの`type`および`state`で管理する。
通常の本文取得には`atk wi show <WIファイル名>`を使用する。
手動で物理パスを組み立てず、保存先の解決は共通の配置解決機能に任せる。
状態遷移した後も、ファイルパス、内部キー、画面URLは同じ値を維持する。
旧配置からの移行は公開CLIの移行機能に任せ、配置の競合が発生した場合は診断メッセージに従って解消してから再実行する。

WIの保存状態には`inbox`、`processing`、`hold`、`adopted`、`rejected`があり、`agent-toolkit:process-wi`は`inbox`と`processing`のみを処理対象とする。
既存WIの更新・修復・採否の見直しが必要と判明した時点や、既存WIに対する具体的な懸念が寄せられた時点では、詳細な調査や認識合わせに先立って対象項目を保留（`hold`）にする。
処理中の項目の扱い: 別のセッションが処理している`processing`状態の項目については、保留や本文の置き換えを行わず、変更したい内容を新規項目として投入する。
状態遷移、採否の操作、保留と本文の修正、終了処理、依存関係と冷却時間、事前承認型UWIを待つ元項目の依存、および「回答済みUWIの取り込み」の運用は[references/states-and-dependencies.md](states-and-dependencies.md)が定める。
上流リポジトリへの投入手順は[references/cross-repository-submission.md](cross-repository-submission.md)が定める。

重要フラグはfrontmatterの`important: true`で設定し、キーの省略時はfalse、フラグの解除はキー自体の削除として扱う。
一覧表示、検索、全件表示およびWeb画面の一覧では、`all`、`yes`、`no`の重要度フィルターを利用できる。
重要フラグは、通常のprocess-wiにおける選定や優先度、状態遷移には影響せず、ユーザーが対象集合として明示的に指定した場合に参照する。

選定の優先度は、重要フラグとは別の優先区分（frontmatterの`priority: urgent`と根拠の3欄`priority_impact`・`priority_evidence`・`priority_release`）で表す。
urgentは、遅らせると具体的な損失が続く項目（処理の停止・遮断、誤った判断・成果物、エンドユーザーの環境での継続的な失敗、データ損失の危険など）に限り、観測した影響、観測条件と根拠の所在、解除条件を示せる場合だけ付ける。
バグという分類や改善の期待は、損失の根拠とは別に扱う。
起票時はWI投入担当が、処理中に新たな影響や解消を観測した時は処理担当が、`atk wi set-priority`で付与・更新・解除する。
区分の無い項目は通常扱いとする。
巡回の選定量から外れた未着手項目は`inbox`のまま次回の候補に残す。
量の調整だけを理由に保留・本文変更・依存追加を行わない。
選ぶ順序と件数の下限は`agent-toolkit:process-wi/references/selection-procedure-allocation.md`に従う。
