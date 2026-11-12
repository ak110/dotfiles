# 再発の記録の手順

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 不具合の原因が過去の対策と同じかを分類する前 | `agent-basics:bugfix/references/root-cause-prevention-requirements.md` |

## 再発の記録

本節は、dotfilesの不具合を原因分析した担当（process-wiのレーン、add-awi-by-user、review-sessionなど）に適用する。
`agent-basics:bugfix/references/root-cause-prevention-requirements.md`「再発防止策の必須性」の分類で過去の対策と同じ原因（範囲不足・未適用・効果不足）と判定したら、その分析の中で次の2つを記録する。
記録は振り返りが原因ごとに再発を数え、同じ対策を繰り返す前に条文や仕組みを見直す材料になる。

- `docs/development/incidents*.md`の該当事例へ`再発（YYYY年M月D日）:`行を加え、分類、事象、是正の内容を書く。
  公開する文書のため、作業項目のファイル名などの個人記録の識別子は書かない（`agent-basics:writing-standards/references/writing-common.md`の個人記録の規定）。
  事例が無ければ元の事象を事例として`incidents.md`「区分」の所在ファイルへ置いてから行を加える
- ユーザーから反復の指摘（「何度も」「散々」「前にも」など）を受けた決定は、是正と同じ変更で`docs/development/concepts.md`の該当節へ決定の要旨（何を守るか、何をしてはならないか）を1行で加える。
  要旨が既にあれば、是正の内容に合わせて更新する。
  決定が索引から読めないまま次の変更が入ると、全件照合（`SKILL.md`の読込表直後の段落）で衝突が見えないためである
- 是正するAWIと元の対策のAWIの対応はWIの側に保つ。
  元の対策が採用済みAWIにあれば、次の操作で効果確認を`insufficient`にし、是正するAWIと対応付ける

  ```sh
  atk wi effect-check <元のAWI> --worktree <対象worktree> --file <元の対策で変更したファイル> --verdict insufficient --checked-on <判定日> --review-record <判定の記録位置> --corrective-awi <是正するAWI>
  ```
