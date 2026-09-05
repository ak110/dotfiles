# 上流投入担当の起動と受領

`agent-toolkit:process-wi`の②で、レーンを起動する前にメインが本書を全文読み、上流投入担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/upstream-submission.subagent.md`が定める。本書へ呼び先固有の作業手順を書かない。

## 起動経路

`upstream_submission`が`なし`以外の全項目をまとめて1件の委譲先へ渡す。`agents_server`の`start`へ`model_type="execute"`を渡して起動し、新しい設定キーを追加しない。

## 起動前の前提

pickerが項目ごとの`upstream_target_repo`と`upstream_request`を確定していることを確認する。いずれかが欠ける項目は起動文へ含めない。

## 渡す入力

- `${CLAUDE_PLUGIN_ROOT}/share/upstream-submission.subagent.md`の絶対パス
- 項目ごとの元項目のAWIファイル名と、pickerが返した`upstream_target_repo`及び`upstream_request`
- 投入先を受領した値へ固定し、自ら決定しないこと
- 完了報告と成果物を日本語で書くこと

## 受領と検収

次の形式の返却を受領する。

```text
status: completed | needs_escalation
submissions:
- awi: <元項目のAWIファイル名>
  result: completed | needs_escalation
  upstream_filename: <投入先で保存されたファイル名。保存されなかった場合は「なし」>
  阻害要因: <当該項目がneeds_escalationの場合の事象と観測値。完了時は「なし」>
```

`submissions`が挙げる元項目のファイル名が重複せず、その集合が起動文へ渡した項目の集合と過不足なく一致することを確認する。
全項目の`result`が`completed`の場合だけ全体の`status`が`completed`であり、1項目以上が`needs_escalation`の場合は全体も`needs_escalation`であることを確認する。
`result`が`completed`の項目は、`upstream_filename`が`なし`でなく、`阻害要因`が`なし`であることを確認する。続いて`atk wi show <upstream_filename> --target-repo=<当該項目のupstream_target_repo> --skip-pull`が終了コード0で当該項目を返すことを確認し、投入先のファイル名を用いる後続工程へ進める。
`result`が`needs_escalation`の項目は、`阻害要因`が`なし`でないことを確認する。`upstream_filename`が`なし`でない場合は、同じ`atk wi show`で保存先の実在を確認する。当該項目のレーンを起動せず、元項目の状態を変更しない。

集合、全体の`status`又は項目ごとの値の検収に失敗した場合は、同じ上流投入担当へ不一致の修正を指示する。項目の`needs_escalation`を検収できた場合は、阻害条件を解消した後、失敗した項目だけを同じ担当へ渡して再開する。先に成功した項目は再投入しない。保存済みの`upstream_filename`がある失敗項目は当該値も渡し、既存の保存先を同じ投入経路で修復させる。`upstream_filename`が`なし`の失敗項目だけを新規投入させる。再開後の返却は再開時に渡した項目の集合に対して同じ規則で検収する。
