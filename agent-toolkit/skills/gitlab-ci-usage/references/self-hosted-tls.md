# 自己署名証明書の私設GitLabで`glab`がTLS検証エラーを返す場合

`glab`が`tls: failed to verify certificate`を返したときは、次の設定をしてCIの状態を照会できるようにする。

- `glab config set skip_tls_verify true --host <host>`を実行し、ホスト単位でTLS検証をスキップする
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を設定する

CI待機スクリプト（`wait_ci.py`）がCLIの失敗を示す終了コード3を返したときに限り、状態の照会と証拠取得のために`curl -k`でAPIを直接呼び出す。
パイプライン一覧、および対象パイプラインごとのジョブ一覧は、返却件数が100件未満になるまで`page`を増やしながら全ページを取得する。

```text
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines?sha=<完全SHA>&per_page=100&page=<page>"
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines/<pipeline-id>/jobs?include_retried=false&per_page=100&page=<page>"
```

いずれの設定もTLS検証をスキップして中間者攻撃（MITM）への耐性を下げるため、トークンは環境変数経由で渡し、シェルやコマンドの実行履歴に残さないようにする。
待機の完了条件や、CI通過を確認せずに作業を進めてよい条件については、`agent-toolkit:commit`の`references/push-and-ci.md`に従う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` |
