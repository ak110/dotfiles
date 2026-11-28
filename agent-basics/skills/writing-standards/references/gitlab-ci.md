# GitLab CIの使い方

GitLab CIを編集する担当が現在の仕様とlintで設定を確かめ、ユーザーが対象のGitLabで意図どおりに動くパイプラインを使えるようにする。
`.gitlab-ci.yml`を編集・確認する時と、GitLab CIのキーワードの仕様やlintの実行方法を参照する時の、公式資料と本体のlintで確かめる手順と外れやすい仕様を示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用である。
TLS検証を外す設定は原則4（`agent-basics:writing-standards/references/quality-principles-constraints.md`）の適用である。

## 基本方針

- 新しいキーワード、改訂の多いキーワード、構文が不確かな項目は、対象のバージョンの公式資料で確かめてから使う。
  起点は[キーワードの全リファレンス](https://docs.gitlab.com/ci/yaml/)、[事前定義変数](https://docs.gitlab.com/ci/variables/predefined_variables/)、[CI Lint API](https://docs.gitlab.com/api/lint/)である。
- 機能の説明ページの`Tier`と`Offering`で、YAMLで書ける項目かAPI専用かを見分ける。
- エディションは、対象のホストが確定していれば`glab api version`の`enterprise`で判定する（`false`はCommunity Edition）。

## `rules:changes`とスケジュール実行

- `compare_to`を指定しない`rules:changes`は新しいブランチと、タグ・スケジュール・手動などpushイベントを伴わないパイプラインで常にtrueになる（[公式仕様](https://docs.gitlab.com/ci/yaml/#ruleschanges)）。
- 差分で比べる時は`rules:changes:compare_to`で比較元を指定し、実行から外すジョブは`rules`の先頭に`when: never`の条件を置く。
- タグのpushの`CI_PIPELINE_SOURCE`は`push`のため、タグは`CI_COMMIT_TAG`で判定する（[種別の判定](https://docs.gitlab.com/ci/jobs/job_rules/#ci_pipeline_source-predefined-variable)）。

## lint / 検証

- ローカルで済む検証は、先に[`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local)などで確かめる。
- 最終の確認は、`include`、`workflow`、変数をまとめて評価するGitLab本体のlint（`/api/v4/ci/lint`の`content`へYAML全文を渡すか、Web UIの`/-/ci/lint`）で行う。
- GitLabに接続できない環境では、検討した設定と本体のlintへ渡す入力を返し、構文の判定はGitLabへ接続できる環境で行う。

## 自己署名証明書の私設GitLabで`glab`がTLS検証エラーを返す場合

`glab`が`tls: failed to verify certificate`を返した時は、次を設定してCIの状態を照会する。

- 信頼できる経路で取得したCAファイルを`glab config set ca_cert <CAファイル> --host <host>`で指定する。
  CAファイルを入手できない場合は、TLS検証を省くことの影響を示して認可を得た時に限り、ホスト単位で一時的に`skip_tls_verify`を設定する。
  使ったことを報告し、照会を終えたら元に戻す。
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を設定する。

この設定は中間者攻撃（MITM）への耐性を下げるため、トークンは環境変数で渡し、シェルやコマンドの実行履歴に残さない。
