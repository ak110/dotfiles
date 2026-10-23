# WI原稿の投入前レビューの起動と検収

```text
起動対象: wi-draft-review.subagent.md
```

投入前レビューは、親が意味を検収しない専念型の作成主体が、保存前に中位の独立担当へ原稿を渡して受けるレビューである。付随型はadd-wi.parent.mdの親の検収を使う。
起動の要否は`agent-toolkit:wi-standards`の`references/drafting-workflow.md`「直接起草と独立した検収の選択」に従う。
同節で直接起草を選べる原稿は、メインの意味検収と機械チェックを代わりに使う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/drafting-workflow.md#「直接起草と独立した検収の選択」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#「直接起草と独立した検収の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/origin-and-approval.md#%E3%80%8C%E7%94%B1%E6%9D%A5%E3%81%A8%60source%60%E3%80%8D` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md#%E3%80%8C%E7%94%B1%E6%9D%A5%E3%81%A8%60source%60%E3%80%8D` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/drafting-completion.md#「実現方式の成立」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md#「実現方式の成立」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-heuristics.md#「問題と手段の比例性」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「問題と手段の比例性」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/drafting-workflow.md#「投入」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#「投入」` |

## 起動

`agents_server`の`start`へ、`cwd`を対象リポジトリの絶対パス、`subagent_md_path`を`wi-draft-review`として渡す。`extra_params`は次の入力とする。工程別model_typeは役割表から解決する。

- `原稿`: 単一原稿の絶対パス、または複数原稿の対応表を保存したUTF-8 JSONファイルの絶対パス。対応表は空でない配列で、各要素は`要求識別子`（重複しない文字列）・`原稿`・`要求と出所`・`成立性の証拠`（後3項は読取可能な絶対パス）を持つ。共通の出所・証拠は同じパスを参照できる。既存の単一原稿入力はそのまま受理する。
- `要求と出所`: 原文、確定要件、技術判断、割当と由来を持つ既存の記録の絶対パス。複数原稿では各原稿と対応付ける。
- `成立性の証拠`: 観測と原因・採否の根拠、引用・構造チェックの結果の絶対パス。共通資料は1回取得して再利用し、要求ごとの証拠対応を保つ。
- `引き継ぎ記録先`: 省略と継続はdelegationの共通契約に従う。

同じ作成主体が同時に用意した原稿は対応表でまとめ、未到着の原稿を待つためだけの待機は設けない。3つの公開入力名・パス形式は単一原稿と共通とし、対応表を使う場合も`要求と出所`・`成立性の証拠`には共通資料の絶対パスを渡す。要求別の対応は対応表が所有し、共通資料を1回取得して各要求へ適用する。
wi-draft-prepareが生成した3つの入力パスと対応表は直接受領する。対応表の任意の`ユーザー向け部分`は原文抽出の絶対パス、`比較資料`はその抽出の読者確認へ渡す生成済みの値である。原稿別の引用選択JSON、同じ根拠説明や対応表を再生成しない。

## 検収と修正

原要求の解釈がレビュー担当と作成主体で分かれ、結果や範囲が変わる場合は、`agent-toolkit:review-standards`の共通規定へ戻す。
原文・実回答・現物でも解消せずユーザーだけの値が残れば、その値を必要とする修正を保留し、両解釈と結果をユーザー確認できるメインへ返す。
返却の要求識別子・原稿の集合を入力の集合と比べ、一致することを確かめる。要求ごとの意味判定と、検収者の冒頭判定・条件付きで起動した読者確認の識別子・結果・証拠を追跡する。合格済み原稿は他原稿の修正待ちと独立に保存できる。追加原稿は新しい識別子で既存の担当へ渡し、既読共通資料を再利用する。
各判定の`読者別探索:`を確かめ、識別子がある場合は返却を読み、その判定のユーザー向け部分への確認であることを確かめる。再利用では前回の抽出との一致、起動条件非該当では条件と自己確認の根拠を確かめる。行の欠落または無効な省略は、合格でも同じ担当へ返して確認を求める。
終端と報告を受領し、review-standardsを適用して各指摘の採否と必要な再調査を上位の作成主体が確定する。要件との不一致を指摘された場合は、指摘が要件とした値の出所を原要求・実回答と`agent-toolkit:wi-standards`の`references/origin-and-approval.md`「由来と`source`」に照らして採否を決める。質問や選択肢の説明を要件へ含めた指摘は由来を示して同じ担当の再判定へ渡し、ユーザーだけの値が残る事項だけを確認する。
修正では局所の追記で閉じず、変更した方式を確かめてから再提出する。確かめる対象は、開始・終了・途中失敗から再開までのつながりで、基準は`agent-toolkit:wi-standards`の`references/drafting-completion.md`「実現方式の成立」とする。修正のたびに新しい所有状態や照会の手段を追加する指摘が続いた場合は、方式の維持・縮小・置換を比べる。比べる基準は`agent-basics:writing-standards`の`references/design-heuristics.md`「問題と手段の比例性」とする。指摘の反復の原因は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「反復した指摘の原因判定」で判定する。未解決の成立性は、反復した場合も解消してから合格とする。修正後は同じレビュー担当へ変更した要求識別子・現行原稿・差分・直接影響を渡して再判定する。変更のない共通資料と合格済み要求の結果を保持し、変更のない抽出は読者別探索の既存の再点検省略条件で再利用する。検収済みの参照関係に基づく正式名への置換だけの差分は再判定へ戻さず、`agent-toolkit:wi-standards`の`references/drafting-workflow.md`「投入」で作成主体が先行項目の保存結果と比べてから保存へ進む。未解決指摘0件と原稿・証拠との対応を確かめてから、共通のWI保存手順へ進む。ユーザーだけが決める値は確認まで保留する。
