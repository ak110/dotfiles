# AWIとUWIの共通規範：状態と依存

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| WIの状態と依存を判定・変更する前 | `states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 上流リポジトリへの投入を選ぶ前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本書に関わる操作・判断に着手する前 | `../SKILL.md`の読込表から、その操作・判断に該当する資料を選ぶ | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |

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
