# 自己署名証明書の私設GitLabで`glab`がTLS検証エラーを返す場合

`glab`が`tls: failed to verify certificate`を返したときは、次の設定をしてCIの状態を照会できるようにする。

- `glab config set skip_tls_verify true --host <host>`を実行し、ホスト単位でTLS検証をスキップする
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を設定する

いずれの設定もTLS検証をスキップして中間者攻撃（MITM）への耐性を下げるため、トークンは環境変数経由で渡し、シェルやコマンドの実行履歴に残さないようにする。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。
