# rules-common.claude-code.md: Claude Codeの全エージェント向け規範

## 資料の取得

hookの対象一覧は、導入済みスキル名なら`Skill`、参照資料・起動禁止スキル・別版の写しなら`Read`で取得する。
`plugin:名前/references/資料.md`は起動通知の実在するplugin rootの`skills/名前/references/資料.md`、`plugin:share/資料.md`等はroot直下へ解決する。
明示された実パスは、その実体を取得対象とする。
