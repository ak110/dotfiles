# 自己署名証明書の私設GitLabで`glab`がTLS検証エラーを返す場合

本書は自己署名のTLS証明書を使う私設GitLabで、`glab`がTLS証明書検証エラー（`tls: failed to verify certificate`）を返したときに、CIの状態を照会できるようにする設定と取得手段を定める。

次を設定する。

- `glab config set skip_tls_verify true --host <host>`でホスト単位のTLS検証をスキップする
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を併せて設定する

この設定はTLS検証をスキップするためMITM耐性を下げる。

CI待機（`wait_ci.py`）がCLIの失敗を示す終了コード3を返した後に限り、状態の照会と証拠の取得のために`curl -k`でAPIを直接呼び出す。

```text
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines?sha=<完全SHA>&per_page=100&page=<page>"
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines/<pipeline-id>/jobs?include_retried=false&per_page=100&page=<page>"
```

pipeline一覧と対象pipelineごとのjob一覧は、応答が100件未満になるまで`page`を増やして全ページ取得する。
待機の終端と、CI通過を確認しないで進めてよい条件は`agent-toolkit:commit`の`references/push-and-ci.md`が定める。

`curl -k`も同じくTLS検証をスキップする。認証トークン漏洩防止のため、
トークンは環境変数経由で渡し、コマンド履歴に残さない運用とする。
