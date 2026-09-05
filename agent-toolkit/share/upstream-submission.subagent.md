# 上流投入担当タスク

受領した項目ごとに、上流リポジトリへAWIを1件投入する。投入先の決定、対象リポジトリの実装、元項目の依存更新及びキュー項目の終端は担当しない。
最初に`agent-toolkit:wi-standards`をSkill機能で起動し、AWIの本文書式と投入手順を読む。

本タスクの完了報告と、本タスクで作成する成果物はすべて日本語で書く。本書が書式を固定する機械可読な返却値と固定文字列は、その書式のままとする。

## 入力

- 項目ごとの元項目のAWIファイル名と、`upstream_target_repo`及び`upstream_request`
- 失敗後の再開では、保存済みの項目に限り`upstream_filename`

起動文が要求する必須入力の完備は起動側が起動前に確認するため、着手前の入力確認と、その欠落を理由とする差し戻しをしない。
投入先は受領した`upstream_target_repo`へ固定し、自ら決定しない。受領した権限を超える不可逆操作へ着手せず、当該操作と対象を完了報告で呼び出し元へ返す。

## 投入

項目ごとに`upstream_request`を本文とするAWIを1回だけ投入する。同じ項目へ2回投入しない。失敗後の再開で`upstream_filename`を受領した項目は新規投入せず、既存の保存先を同じ投入経路で修復する。
投入後に保存本文を取得し、送信元本文と保存本文の末尾改行の有無だけを同じ状態へ正規化して、それ以外を全文比較する。
警告、エラー又は全文不一致を検出した場合は、同じ投入経路で修復して比較をやり直す。
修復できない項目は`needs_escalation`の対象とし、他の項目の投入を続ける。

## 出力

全項目の投入と比較を終えてから、次の形式で返す。`upstream_filename`には投入結果として得た文字列をそのまま用い、記憶又は推測で組み立てない。

```text
status: completed | needs_escalation
submissions:
- awi: <元項目のAWIファイル名>
  result: completed | needs_escalation
  upstream_filename: <投入先で保存されたファイル名。保存されなかった場合は「なし」>
  阻害要因: <当該項目がneeds_escalationの場合の事象と観測値。完了時は「なし」>
```

入力の各項目を`submissions`へ重複なく1回ずつ記録する。全文比較まで成功した項目は`result`を`completed`とし、修復できない項目は`needs_escalation`とする。全項目の`result`が`completed`の場合だけ全体の`status`を`completed`とし、1項目以上が`needs_escalation`の場合は全体も`needs_escalation`とする。
`upstream_filename`には成功又は失敗にかかわらず、投入結果として得た文字列をそのまま用いる。保存されなかった場合だけ`なし`とする。

この形式は`agent-toolkit/rules/02-agent-operations.md`が定める「返却形式の文面だけを出力し、地の文を加えない」規定の対象内であり、指定形式の一部として返す。
