# WI投入担当の起動と受領

```text
起動対象: add-wi.subagent.md
```

AWIやUWIの作成・更新にあたって成立性調査や起草をWI投入担当へ委ねるエージェントは、本書を全文読み、起動、入力の受け渡し、および返却された成果物の検収に適用する。
`agent-toolkit:process-wi`のレーン工程で上流リポジトリへの投入を要する項目も、同様にこの委譲で扱う。
委譲先の手順と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.subagent.md`に定めている（所在参照）。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 対象集合の見直し・存廃・要否・仕分けを求める依頼（先行成果物から登録項目を選ぶ依頼を含む）を渡す前 | `../skills/wi-standards/references/drafting-completion-scope.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md` | |
| 要件と委譲元の判断を分類する前 | `../skills/wi-standards/references/drafting-workflow-requirements.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-requirements.md` | |
| 原稿の意味を検収する前 | `agent-toolkit:wi-standards`の`references/draft-semantic-review.md`と、同資料が指定する工程別の共通基準 | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md` | |

## 入力

- 必須入力名: `投入する要求`、`引き継ぎ記録先`

入力値の確定と起動前の検収は`add-wi-parent-inputs.md`に従う。
`subagent_md_path`には`add-wi`を渡す。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-parent-existing-defects.md` | 判断 | `read:agent-toolkit:*/share/exec-review-parent-existing-defects.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:user-confirmation-and-report/references/pending-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation-sources.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-sources.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/wi-standards-origin.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-standards-origin.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/user-utterance-incorporation.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance-incorporation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/base-contract-handoff.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract-handoff.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wi-draft-review.parent.md` | 判断 | `read:agent-toolkit:*/share/wi-draft-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/origin-and-approval-origin.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-origin.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

<!-- return-contract:add-wi:start -->
<!-- 生成元: share/add-wi.subagent.md「出力」。手動編集しない。 -->
## WI投入担当の返却契約

`成立性の証拠`には原稿自身や既存の作業記録の絶対パスを指定できる。
出所・判断根拠・検証結果・冒頭抽出の対応を既存資料から直接取得できるようにし、不足分だけを補う。
原稿や調査過程の重複する証拠要約は作成しない。

続行不能、状態・結果行の抑止と追加報告行は、共通契約`rules/rules-subagent.md`「返却形式の受け渡し」に従う。
以下の理由・区分の2行は、続行できない要求にだけ配置する。

### 起草の返却

調査と起草が完了した段階で、次の形式のみを返して終了する。
原稿および証拠の絶対パスには、実在するファイルを指定する。

```text
状態: drafted
起草結果:
- 要求: <要求の識別子>
  結果: drafted | condition_not_met
  再発の経過を問う要求: <該当|非該当と根拠となる逐語語句>
  種別: awi | uwi
  原稿: <絶対パス。省略時の値は「なし」>
  成立性の証拠: <根拠・読み直し・適用した既存CLIの結果の絶対パス。省略時の値は「なし」>
  条件判定: <判定と根拠。省略時の値は「無条件」>
  判断の変更: <変更と理由。省略時の値は「なし」>
  既存項目の扱い: <「重複判定」で既存項目が全てを覆う場合は「再利用: <ファイル名>」、部分的に覆う場合は「部分被覆: <ファイル名と被覆する範囲>」。省略時の値は「なし」>
  続行できない区分: <共通契約の区分>
  続行できない理由: <要求固有の不足と解除に必要な入力・判断>
```

入力された要求集合を過不足なく重複なしで返す。
既存項目が全体を網羅している要求は、`結果`を`drafted`、`原稿`を`なし`とし、`既存項目の扱い`に再利用するファイル名を記載する。
`drafted`は未保存の原稿であることを示す。
省略時のデフォルト値と一致する任意の行は省略してよく、行が存在しない場合は省略時の値として解釈する。
`condition_not_met`は、投入条件が成立しなかった場合に加え、前提が成立していない、すでに対応済みである、取り下げられたなど、調査によって投入不要と確定した場合の結論としても使用する。
`条件判定`には確認対象、結論、根拠、および証拠の所在を記載して返し、確定した結論を続行不能として返しない。

### 保存の返却

検収完了後に指示された要求の保存を終えたら、次の形式のみを返す。
`ファイル名`には、投入結果として得られた文字列をそのまま記載する。

```text
状態: completed
投入結果:
- 要求: <委譲元が付けた要求の識別子>
  結果: completed | reused | condition_not_met
  種別: awi | uwi
  ファイル名: <`completed`では投入したファイル名、`reused`では再利用した既存項目のファイル名。省略時の値は「なし」>
  既存項目の扱い: <`completed`で既存項目が要求の一部を覆う場合は「部分被覆: <ファイル名と被覆する範囲>」。省略時の値は「なし」>
  被覆の根拠: <`reused`と部分被覆で、既存項目の本文の該当節と比較結果の所在。省略時の値は「なし」>
  条件判定: <「無条件」、成立した条件と現物や実行結果から得た根拠、不成立の条件と現物や実行結果から得た根拠、または判定不能の理由。省略時の値は「無条件」>
  判断の変更: <覆した`委譲元の判断`、採った案と理由。省略時の値は「なし」>
  続行できない区分: <共通契約の区分>
  続行できない理由: <保存できない要求固有の不足と解除に必要な入力・判断>
```

入力された各要求を重複なく1回ずつ記載する。
投入に成功した要求は`completed`、投入不要と確定した要求は「起草の返却」と同様に`condition_not_met`とその根拠を返す。
既存項目が要求全体を網羅しており新規保存や更新が不要な要求（`../skills/wi-standards/references/drafting-completion-duplicates.md`「重複判定」）は`reused`とする。
`ファイル名`、`既存項目の扱い`、`被覆の根拠`、`条件判定`、`判断の変更`は、省略時の値と一致していれば出力を省略してよく、委譲元は行の不在を省略時の値とみなす。
`ファイル名`には、その要求の保存先または再利用先のみを記載する。
調査中に独立した別要求であることが判明した内容については即座に通知し、共通手順の「調査と本文の起草」に従って残りの原稿を完成させる。

通常AWIの登録条件は、`../skills/wi-standards/references/wi-standards-body.md`「起票目的別の調査出口」に従う。
<!-- return-contract:add-wi:end -->

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 起動前の前提・渡す入力に関する判断・作業の前 | `add-wi-parent-inputs.md` | 判断 | `read:agent-toolkit:*/share/add-wi-parent-inputs.md` |
| 起動方法に関する判断・作業の前 | `add-wi-parent-launch.md` | 判断 | `read:agent-toolkit:*/share/add-wi-parent-launch.md` |
| 受領と検収・受領後の報告に関する判断・作業の前 | `add-wi-parent-acceptance.md` | 判断 | `read:agent-toolkit:*/share/add-wi-parent-acceptance.md` |
