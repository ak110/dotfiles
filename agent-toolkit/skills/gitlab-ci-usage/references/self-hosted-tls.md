# 私設ホスト（自己署名のTLS証明書）でのCI通過確認

自己署名のTLS証明書のGitLab私設ホストでも、証明書検証を維持したまま`glab`が正常動作する場合は
設定を変更しない。
`agent-toolkit:commit`が示すCI通過確認の手順を使い、forgeへ`gitlab`を指定する。
pipeline一覧と対象pipelineの全ページのjob一覧を、カレントリポジトリから解決した同じSelf-Managedホストへ問い合わせる。

`glab`でTLS証明書検証エラー（`tls: failed to verify certificate`）が出る場合に限り、次を設定する。

- `glab config set skip_tls_verify true --host <host>`でホスト単位のTLS検証をスキップする
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を併せて設定する

この設定は`curl -k`と同様にMITM耐性を下げる。
設定後は`agent-toolkit:commit`の手順を使い、forgeへ`gitlab`を指定する。

`glab`自体が機能しない場合に限り、`curl -k`によるAPI直呼び出しを代替経路とする。

```text
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines?sha=<完全SHA>&per_page=100&page=<page>"
curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://${GITLAB_HOST}/api/v4/projects/<project-id>/pipelines/<pipeline-id>/jobs?include_retried=false&per_page=100&page=<page>"
```

pipeline一覧と対象pipelineごとのjob一覧は、応答が100件未満になるまで`page`を増やして全ページ取得する。
`allow_failure`が`false`の`failed` jobを1件検出するか、対象pipelineがすべて完了するまでpollingする。
取得済みjobがすべて成功していてもpipelineが未完了なら待機を続ける。全て不可能な場合は
ユーザーの明示判断でCI通過確認スキップを許容する（記録は必須）。

`curl -k`はTLS検証をスキップするためMITM耐性が下がる。認証トークン漏洩防止のため、
トークンは環境変数経由で渡し、コマンド履歴に残さない運用とする。
