# Agentツールで起動した担当の継続

本書は、Claude Codeの`Agent`ツールで起動した担当を継続するときの追加条件を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-code-runtime-receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-receiving.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |

## Agentツールの継続

Claude Codeの`Agent`ツールで起動した担当に限り、`continuation.md`「継続と新規起動」の共通条件に加えて本節を適用する。

- すでに完了報告を返して停止している識別子であっても、同一の担当者へ同一タスクの未完了作業、指摘への対応、または再レビューを依頼する場合で、かつ継続直前に再確認した実効の`engine`、`model`、`effort`が起動時と完全に一致している場合に限り、`SendMessage`を用いて再開させてよい。
  継続時の指示本文には、継続前に作成・読み取りしたファイルであっても書き込みの直前に現行の内容を再取得する旨の条件を含める
- 完了通知の配送先はプロバイダーやシステム構成によって異なり、3階層以上の構成における配送先は未検証である。
  最上位や直接の親が配送先であると決めつけず、起動結果として得られたIDへの`SendMessage`の成功と、送信後に対応する完了通知の受信をもって継続の成立を判定する。
  その上で`claude-code-runtime-receiving.md`「完了通知と中継の実行順」へ対応付ける。
  関連する監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：同一セッション内の再開：2026年8月」に記載されている
- 担当者とタスクの組み合わせが異なる場合や、実効の3設定値（engine・model・effort）の不一致、完了報告の配送不能、前提条件の無効化、処理中断のいずれかが起きた場合は、停止済みの識別子を再利用しない。
  必要な前提条件を明示的に再構成した上で新規セッションを起動する。
  参照可能な記録が存在する場合は、絶対パス、対象ID、および未記録の差分のみを引き渡す
- `SendMessage`が`No transcript found for agent ID: <識別子>`というエラーで失敗した場合は配送不能と判断し、同一の識別子に対する再開を中止して新規セッションを起動する。
  セッション識別子に変更が生じた場合は、タスクの出力先ディレクトリの識別子を`ls`等で突き合わせ、旧識別子の配下に存在する委譲先への失敗エラーと照らし合わせて確認する
