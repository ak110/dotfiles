# 私設ホスト（自己署名のTLS証明書）でのCI通過確認

自己署名のTLS証明書のGitLab私設ホストでも、証明書検証を維持したまま`glab`が正常動作する場合は
設定を変更しない。
CI通過確認は`agent-toolkit:commit`が示す手順を使い、forgeへ`gitlab`を指定する。TLS検証をスキップする設定の有無は、この手順を変えない。
pipeline一覧と対象pipelineの全ページのjob一覧を、カレントリポジトリから解決した同じSelf-Managedホストへ問い合わせる。

`glab`でTLS証明書検証エラー（`tls: failed to verify certificate`）が出る場合に限り、次を設定する。

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
待機の終端は`agent-toolkit:commit`の`references/push-and-ci.md`が定める終端状態で確定する。
CI通過を確認しないで進めるのは、呼び出し元が同書の定める「当該pushのCI通過をこのセッションで判定しない」旨を明示した場合に限る。

`curl -k`も同じくTLS検証をスキップする。認証トークン漏洩防止のため、
トークンは環境変数経由で渡し、コマンド履歴に残さない運用とする。
