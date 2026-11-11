# 状態と依存：採用済みAWIの効果記録

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 採用済みAWIの効果記録

`effect_check`は採用済みAWIの実装後の確認を採用状態と別に表すfrontmatterである。
実装commit付きの新しい採用では`pending`を保存し、実利用での適用機会と結果を確認するまで保持する。
`pending`は確認待ち、`confirmed`は効果確認済み、`insufficient`は効果不足と判定して是正AWIを保存済み、`not_required`は後日確認が不要と判断して理由を保存済みを表す。
判定の単位は作業項目の目的であり、目的を達したかの判定を作業項目の全体へ保存する。
変更ファイルごとの判定も保存でき、その場合は全変更ファイルが確認済みで全体を`confirmed`、1ファイル以上の効果不足で全体を`insufficient`とし、部分確認は判定済みのファイルを保持して全体を`pending`とする。
適用機会なし、日時や対応commitの解決不能は理由付きの確認待ちとし、確認不要や効果不足と区別する。
キーが無い既存項目は未確認であり、日時と変更内容から対象化する。
差分なしの要求充足、回答のみのUWI、却下項目は自動の確認待ちの対象外とする。
確認待ちは日数によらず残し、依存解決やprocess-loopの未着手対象へ戻さない。

保存には次の操作を使う。

```text
atk wi effect-check <ファイル名> --worktree <対象worktreeの絶対パス> --verdict confirmed --checked-on <YYYY-MM-DD> --review-record <振り返りの記録位置> --reason <判定の根拠>
```

`insufficient`では`--corrective-awi <保存済み是正AWIファイル名>`が必須となる。
変更ファイルごとに保存する場合は`--file <変更ファイルのリポジトリ相対パス>`を加える。
全体の確認待ち・確認不要は`--file`を省略し、`--verdict pending`または`--verdict not_required`と`--reason <空でない1行の理由>`を指定する。
`--worktree`、`--checked-on`、`--review-record`は同様に指定し、対応commitが不明でも対象リポジトリの一致を検証する。
同じ判定と理由を保存する対象は、`atk wi effect-check <ファイル名> [<ファイル名> ...]`でまとめて指定できる。
全対象を検証してから保存し、commitとpushを1回にまとめる。
途中の保存やcommitに失敗した場合は、同じ引数で再実行する。
処理結果節のない旧項目では、確認待ち・確認不要の記録用に末尾へ節を追加し、既存本文を保持する。
全体へ保存する`confirmed`と`insufficient`は、既存のファイル別結果を保持する。
同じ引数の再実行では既存の判定行を再利用する。
`--file`を指定した場合は、実際の対応commitの変更パスに含まれることを確かめ、逸脱するパスと不正値を拒否する。
状態、要求本文、ユーザーコメントおよび既存処理結果行を保ち、次の構造の行を最後の`## 処理結果`節へ追記してcommitとpushを行う。

```text
- 効果確認: {"file":"agent-toolkit/rules/01-agent.md","verdict":"confirmed","checked_on":"2026-10-31","review_record":"claude:session:42"}
```

不足判定のJSONには`corrective_awi`も保存する。
`atk wi show`と一覧JSONの`effect_check`・`effect_check_reason`、WI詳細の汎用frontmatter表示で確認できる。
`atk wi list --state=adopted --effect-check=pending`で確認待ちを絞り込める。
ほかの判定値も同じ引数で指定でき、既存の一覧条件との積集合を返す。
新しい採用日時はタイムゾーン付きで保存する。
過去のタイムゾーンなしの値は既存の採用操作が保存したローカル時刻として解釈し、適用したタイムゾーンを確認材料へ残す。
日時が解釈できない項目は理由付きで未確認に残す。
通常の`atk wi edit`は引き続きinbox・processing・holdだけを編集対象とする。
