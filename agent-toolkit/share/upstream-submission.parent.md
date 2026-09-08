# 上流投入担当の起動と受領

`agent-toolkit:process-wi`のレーン工程で、レーンを起動する前にメインが本書を全文読み、上流投入担当の起動、入力の受け渡し及び返却値の検収へ適用する。
呼び先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/upstream-submission.subagent.md`が定める。本書へ呼び先固有の作業手順を書かない。

## 起動経路

`upstream_submission`が`なし`以外の全項目をまとめて1件の委譲先へ渡す。`agents_server`の`start`へ`model_type="execute"`を渡して起動し、新しい設定キーを追加しない。

## 起動前の前提

pickerが項目ごとの`upstream_target_repo`と`upstream_request`を確定していることを確認する。`upstream_target_repo`が複数の投入先を含む場合は、元項目と投入先の組へ展開する。投入先又は要求が欠ける項目は起動文へ含めない。

## 渡す入力

- `${CLAUDE_PLUGIN_ROOT}/share/upstream-submission.subagent.md`の絶対パス
- 元項目を保持する対象リポジトリの絶対パス
- 元項目と投入先の組ごとの、元項目のAWIファイル名、1件の`upstream_target_repo`及びpickerが返した`upstream_request`
- 投入先を受領した値へ固定し、自ら決定しないこと
- 完了報告と成果物を日本語で書くこと

## 受領と検収

次の形式の返却を受領する。

```text
status: completed | needs_escalation
submissions:
- awi: <元項目のAWIファイル名>
  upstream_target_repo: <投入先リポジトリ識別子>
  result: completed | condition_not_met | needs_escalation
  upstream_filename: <投入先で作成又は確認したAWIファイル名。既定値は「なし」>
  条件判定: <「無条件」、成立した条件と実測根拠、不成立の条件と実測根拠、又は判定不能の理由>
  阻害要因: <needs_escalationの理由。既定値は「なし」>
```

`upstream_filename`と`阻害要因`は、値が既定値`なし`と一致する場合に当該行が出力されない。メインは当該行の不在を既定値`なし`として解釈し、欠落として扱わない。
`submissions`が挙げる`awi`と`upstream_target_repo`の組が重複せず、その集合が起動文へ渡した組の集合と過不足なく一致することを確認する。
全組の`result`が`completed`又は`condition_not_met`の場合だけ全体の`status`が`completed`であり、1組以上が`needs_escalation`の場合は全体も`needs_escalation`であることを確認する。
`result`が`completed`の組は、`upstream_filename`の行があり値が`なし`でないことと、`阻害要因`の行が無いことを確認する。`条件判定`は`無条件`か、成立した条件と実測根拠を示す値とする。続いて`atk wi show <upstream_filename> --target-repo=<当該組のupstream_target_repo> --skip-pull`が終了コード0で当該項目を返すことを確認する。保存本文を`agent-toolkit:wi-standards`の「通常AWIの本文」と照合し、全項目が規定順で存在することを確認する。元項目を参照せず、反映内容、反映先、完成条件を判断できることも確認する。
`result`が`condition_not_met`の組は、`upstream_filename`と`阻害要因`のいずれの行も無いことを確認する。`条件判定`は、不成立の条件、調査対象、実測結果を示す値とする。当該組は投入先のファイル名を用いる後続工程へ含めない。
`result`が`needs_escalation`の組は、`阻害要因`の行があり値が`なし`でないことを確認する。`upstream_filename`の行がある場合は、同じ`atk wi show`で保存先の実在を確認する。当該組を含む元項目のレーンを起動せず、元項目の状態を変更しない。

集合、全体の`status`、組ごとの値、保存本文のいずれかの検収に失敗した場合は、同じ上流投入担当へ不一致の修正を指示する。組の`needs_escalation`を検収できた場合は、阻害条件を解消した後、失敗した組だけを同じ担当へ渡して再開する。先に`completed`か`condition_not_met`となった組は再投入しない。保存済みの`upstream_filename`がある失敗組は当該値も渡し、既存の保存先を同じ投入経路で修復させる。`upstream_filename`の行が無い失敗組だけを条件判定から再開させる。再開後の返却は再開時に渡した組の集合に対して同じ規則で検収する。
