# 品質判断の原則：外部部品の版の追従

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 外部部品の具体的な版指定と更新経路を判断する前 | `dependency-management.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dependency-management.md` |

## 外部部品の版の追従

依存パッケージ、コンテナーイメージ、言語処理系、CIや自動ビルドが取得するツールとactionなどの外部部品は、可能な限り最新の安定版へ追従させる。
LTS制度を持つ製品では最新のLTSを選び、公開待機設定がある場合は待機期間を過ぎた最新版へ追従する。
新しい版で動かなくなった場合は、組み合わせて動く部品も含めて追随改修する。
古い系列へ留める指定は、最新版で動かない原因を観測し、同じ作業で直せない場合に限る一時的な措置とする。
指定箇所へ理由と解除条件を残し、最新版へ移す作業を作業項目へ登録する。
再現性と改ざん防止のための固定（ロックファイル、digest、commit SHA）は、固定値を最新版へ更新し続ける自動化か定期的な更新手順と組にして使う。
具体的な版指定は`dependency-management.md`「最新版への追従」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 外部部品の具体的な版指定と更新経路を判断する前 | `dependency-management.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dependency-management.md` |
