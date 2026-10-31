# Xの投稿本文と添付写真の取得

本書は、X（旧Twitter）の投稿を調査資料として参照する際に、対象の投稿を一意に特定し、本文、投稿日時、添付写真を取得するための手順をまとめたものである。
引用のルールや事実確認の裏付け手順は、`investigation.md`に従う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |

## 投稿の同定と本文の取得

- 投稿URLの`x.com/<ユーザー名>/status/<投稿ID>`（`twitter.com`も同様）からユーザー名と投稿IDを抽出する。URL末尾の`/photo/1`などは同一投稿の写真表示画面である。`t.co`などの短縮URLは、Web取得ツールを用いてリダイレクト先を実際に確認した上で投稿を同定し、転送先を取得できない場合はURLを推測せず「未取得」として記録する
- `https://api.fxtwitter.com/<ユーザー名>/status/<投稿ID>`へリクエストを送信する（リダイレクトが発生した場合は転送先まで追跡する）。応答をファイルへ保存し、最終HTTPステータスが200かつJSONの`code`が数値の200であることを確かめてから、必要な属性だけを`jq`などで会話へ抽出する
- `tweet.article`が無い、または`null`の通常投稿は`tweet.text`を本文とする。`tweet.article`がオブジェクトの投稿は記事形式として、`tweet.article.title`を表題、`tweet.article.content.blocks[].text`を配列の順序通りに改行で連結したものを本文とする。記事本文の取得にはこの配列を使い、`tweet.text`や`tweet.article.preview_text`と区別する
- `tweet.created_at`を投稿日時、記事の`tweet.article.created_at`を記事作成日時として区別する。添付写真のURLは`tweet.media.photos[].url`から取得する。引用投稿の本文が必要な場合は、その投稿を別の投稿として取得する
- HTTPまたは`code`の条件を満たさない応答と、成功応答でも必要な属性が欠ける場合は「未取得」とする。取得手段、HTTPステータス、診断メッセージ、取得できなかった範囲を報告する。写真の配列が空か写真メディアが無い場合は「写真なし」とし、通信の失敗と区別する
- 本文の取得状況と、写真の取得・確認状況は、それぞれ独立して記録する
- 記事の`blocks[].text`を連結しても、記事内の画像や埋込を取得したとは扱わない。画像は「添付写真の保存と確認」に従って保存・表示し、実際に確認した範囲を記録する

上記のURLはFxTwitterのv1形式であるため、`/2/`を含むv2形式とは区別し、v1の仕様に沿った属性名で取得する。
詳細な仕様は[FxEmbedのAPIドキュメント](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)および[レスポンスの実装コード](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)を参照する。

## 添付写真の保存と確認

取得した写真URLごとに、実行環境が提供する一時ディレクトリへ画像をダウンロードして保存する。

```sh
curl -sS --max-time 60 -o <画像保存先の絶対パス> "<画像URL>"
```

ダウンロードしたファイルが正常な画像データであることを確認し（HTTPエラーページのHTML等を画像として誤認しない）、ホスト環境の画像表示手段（Claude Codeの`Read`やCodexの`view_image`など）を用いて実際に表示して確認する。調査の根拠として利用できるのは、実際に画像から読み取れた情報の範囲に限定する。

## ホスト環境別の留意点

過去の調査環境では、Xへの直接的なWebアクセスでHTTP 402が返り、FxTwitter APIからはHTTP 307（一時リダイレクト）が返されたが、`pbs.twimg.com`上の画像はcurlで正常に取得できた。これは当時の特定環境における観測事実であり、他の環境で作業する場合は各手段の応答状況に応じて適切な代替手段を選択する。

Windows環境のcurlでSSL証明書の失効確認エラーが発生した過去の環境では、`--ssl-no-revoke`オプションを付与することで通信が成功した。同様のエラーが実際に発生した環境に限り、失効確認を無効化するセキュリティ上の影響を理解した上でこのオプションを選択する。その場合でも、証明書自体の検証処理は維持する。

APIの参照先仕様、過去環境での観測条件や再検証手順を記録している。詳細は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/x-posts.md：投稿の同定と本文の取得：2026年10月10日」を参照する。
