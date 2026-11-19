# 再発の記録の手順

## 再発の記録

本節は、dotfilesの不具合を原因分析した担当（process-wiのレーン、add-awi-by-user、review-sessionなど）に適用する。
`agent-basics:bugfix`の問い4で同じ害への既存の対策が見つかった場合は再発として扱い、`agent-basics:bugfix/references/recurrence.md`「前回の対策を調べる」で防げなかった理由を分類する。
該当する場合は分析の中で次の事項を記録する。
記録は振り返りが原因の違いを問わず同じ害の再発を数え、同じ対策を繰り返す前に条文や仕組みを見直す材料になる。

- `docs/development/incidents*.md`の該当事例へ`再発（YYYY年M月D日）:`行を加え、分類、事象、是正の内容を書く。
  公開する文書のため、作業項目のファイル名などの個人記録の識別子は書かない（`agent-basics:writing-standards/references/writing.md`の個人記録の規定）。
  事例が無ければ元の事象を事例として`incidents.md`「区分」の所在ファイルへ置いてから行を加える
- ユーザーから反復の指摘（「何度も」「散々」「前にも」など）を受けた決定は、是正と同じ変更で`docs/development/concepts.md`の該当節へ決定の要旨（何を守るか、何をしてはならないか）を1行で加える。
  要旨が既にあれば、是正の内容に合わせて更新する。
  決定が索引から読めないまま次の変更が入ると、`.claude/skills/edit-dotfiles-norms/SKILL.md`「既存の方針との照合」の全件照合で衝突を確認できないためである
- 是正するAWIと元の対策のAWIの対応はWIの側に保つ。
  元の対策が採用済みAWIにあれば、次の操作で効果確認を`insufficient`にし、是正するAWIと対応付ける

  ```sh
  atk wi effect-check <元のAWI> --worktree <対象worktree> --file <元の対策で変更したファイル> --verdict insufficient --checked-on <判定日> --review-record <判定の記録位置> --corrective-awi <是正するAWI>
  ```
