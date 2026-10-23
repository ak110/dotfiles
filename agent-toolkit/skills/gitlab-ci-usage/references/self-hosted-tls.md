# 自己署名証明書の私設GitLabで`glab`がTLS検証エラーを返す場合

`glab`が`tls: failed to verify certificate`を返したら、次を設定してCIの状態を照会できるようにする。

- `glab config set skip_tls_verify true --host <host>`でホスト単位のTLS検証をスキップする
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を設定する

CI待機（`wait_ci.py`）がCLIの失敗を示す終了コード3を返した後に限り、状態の照会と証拠の取得のために`curl -k`でAPIを直接呼び出す。
pipeline一覧と対象pipelineごとのjob一覧は、応答が100件未満になるまで`page`を増やして全ページ取得する。

```text
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines?sha=<完全SHA>&per_page=100&page=<page>"
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines/<pipeline-id>/jobs?include_retried=false&per_page=100&page=<page>"
```

どちらの設定もTLS検証をスキップしてMITM耐性を下げるため、トークンは環境変数経由で渡してコマンド履歴に残さない。
待機の終端と、CI通過を確認しないで進めてよい条件は`agent-toolkit:commit`の`references/push-and-ci.md`が定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` |
