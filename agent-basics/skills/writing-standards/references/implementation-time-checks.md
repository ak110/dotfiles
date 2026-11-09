# 実装時のコーディング品質：依存と自動生成物

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 指摘・エラーの原因分析と類似見直しを行う前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-principles-qcd.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-qcd.md` |
| 「コードの簡潔さ」で処理の共通化を判断する時 | `design-heuristics-responsibility.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-responsibility.md` |
| 内部の引数や選択肢の整理でテストを統合する前 | `testing-priorities.md`、`testing-selection.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-priorities.md`、`read:agent-basics:*/skills/writing-standards/references/testing-selection.md` |
| 外部通信の時間予算や再試行を選ぶ前 | `design-runtime-jobs.md`、`design-runtime-services.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-jobs.md`、`read:agent-basics:*/skills/writing-standards/references/design-runtime-services.md` |

## 依存と自動生成物

- 依存関係の追加や設定の変更には専用のCLIツールを優先して使用し、ロックファイルとマニフェストファイルの内容を常に一致させる。
  自動生成ファイルは生成元の定義ファイルや専用CLIから更新し、直接編集が必要に思える場合でも、まずは生成元の設定で対応できないかを検討する。
- 既存コードに設けられている意図的な制約（型定義の制約、アクセス制限、lint設定、不変条件のアサーションなど）は新しいコードでも尊重して維持し、変更する場合は事前にその妥当性を確認する。

## lintと機械チェック

指摘・エラーを受けたときは`agent-basics:bugfix`を起動し、原因分析と絞り込み型の類似見直しを行う。
再発防止策の要否と初回検出の例外、記録の比例性は同スキルに従う。

- lintの指摘への対応方針は、`quality-principles-qcd.md`「QCDと3段判定」におけるQCDの原則（品質を第一に守り、その制約下でコストと納期を比較する）に従って決定する。
  本節をlint対応に関する基本方針の基準とし、警告の抑制（サプレス）を採用するかどうかもエージェント自身が技術的判断として確定する。
- 正当な指摘はコード自体を修正して解消し、意図的な設計に該当して修正すべきでない指摘に限り、必要な最小範囲で警告を抑制する。
  理由が自明でない場合は抑制コメントに理由を明記する。
  コードの振る舞いを変えずに単にlint警告を回避するためだけの小手先の書き換えは避ける。
  新しく抑制を追加するときは、既存の抑制コメントの有無にとらわれず、個別に妥当性を判断する。
- ファイル単位や設定ファイル全体でのルール緩和（ファイル全体の行数上限の引き上げなど）は、プロジェクト方針との不一致がある場合や、個別の抑制を繰り返すのでは対応しきれない場合に限定する。
  緩和により検出されなくなる違反とその影響を確かめ、将来の見逃しリスクを評価した上で実施する。
  コードの肥大化が原因であれば、責務やユースケースに応じたファイルの分割を優先する。
- 新しいlintルールや静的チェックツールを有効化・強化する変更では、作業に着手する前に既存コードでの検出結果を確認し、「修正すべき対象」と「意図的な設計として許容する対象（理由付き抑制など）」へ1件ずつ分類する工程を設ける。
  例えば`pylint`の`duplicate-code`では、同期版と非同期版の並行実装や、プラットフォームごとのペア実装におけるコードの類似が正当な許容対象になり得る。
