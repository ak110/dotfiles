# Xの投稿本文と添付写真の取得

X（旧Twitter）の投稿を調査資料として参照する時に、投稿を特定し、本文、投稿日時、添付写真を取得する手順を定める。
引用と事実確認の基準は`agent-basics:writing-standards/references/investigation.md`に従う。

## 投稿の同定と本文の取得

1. 投稿URLの`x.com/<ユーザー名>/status/<投稿ID>`（`twitter.com`も同じ）からユーザー名と投稿IDを取る。
   末尾の`/photo/1`などは同じ投稿の写真表示である。
   `t.co`などの短縮URLは、Web取得ツールで転送先を確かめる。
   転送先を取得できなければURLを推測せず「未取得」と記録する。
2. `https://api.fxtwitter.com/<ユーザー名>/status/<投稿ID>`へリクエストし、リダイレクトを追跡して応答をファイルへ保存する。
   最終HTTPステータスが200で、JSONの`code`が数値の200であることを確かめてから、必要な属性だけを`jq`などで抽出する。
   応答が条件外か必要な属性が欠けるなら保全してID・HTTPステータス・診断を確認し、不具合は `agent-basics:bugfix` で認可内に限り直す。
   権限拒否は迂回せず、許可済みの代替取得を試し、未取得の範囲・試行・再開条件を伝える。
3. 本文と日時を区別する。
   - `tweet.article`が無いか`null`なら通常投稿で、`tweet.text`が本文である。
   - `tweet.article`がオブジェクトなら記事形式で、`tweet.article.title`が表題、`tweet.article.content.blocks[].text`を配列順に改行で連結したものが本文である。
     `tweet.text`と`tweet.article.preview_text`はプレビューであり本文と区別する。
   - `tweet.created_at`は投稿日時、`tweet.article.created_at`は記事作成日時である。
     引用投稿は別の投稿として取得する。
4. 写真は`tweet.media.photos[].url`から取得し、「添付写真の保存と確認」へ進む。
   配列が空か写真メディアが無ければ「写真なし」とし、通信の失敗と区別する。
   記事の`blocks[].text`の連結だけでは、記事内の画像や埋込を取得したと扱わない。
5. 本文と写真の取得状況を別々に記録し、画像は実際に確認した範囲を残す。

上の属性名はFxTwitterのv1形式であり、`/2/`を含むv2形式と区別する。
仕様は[FxEmbedのAPIドキュメント](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)と[レスポンスの実装](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)にある。

## 添付写真の保存と確認

保存・確認済みの応答JSONから写真URLをデータとして取り出し、一時ディレクトリへ写真ごとにダウンロードする。
URLはシェルのコードへ直接置換せず、変数を引用して`curl`へ渡す。

```sh
response_file='<保存済み応答JSONの絶対パス>'
image_file='<画像保存先の絶対パス>'
photo_index=0
photo_url=$(jq -er --argjson index "$photo_index" \
  '.tweet.media.photos[$index].url | select(type == "string" and length > 0)' \
  -- "$response_file") &&
  curl -fsS --max-time 60 --proto '=https' --output "$image_file" --url "$photo_url"
```

- URLの欠落・不正や取得の失敗は「未取得」とする。
  HTTPエラーページのHTMLなどを画像と誤認しないよう、正常な画像データであることを確かめる。
- ホストの画像表示手段（Claude Codeの`Read`、Codexの`view_image`など）で実際に表示する。
  根拠に使うのは画像から読み取れた情報だけで、「投稿の同定と本文の取得」の手順5へ戻る。

## ホスト環境別の留意点

- 過去の調査環境では、Xへの直接アクセスがHTTP 402、FxTwitter APIがHTTP 307を返したが、`pbs.twimg.com`の画像は`curl`で取得できた。
  特定環境の観測であり、他の環境では応答に応じて代替手段を選ぶ。
- Windowsの`curl`でSSL証明書の失効確認エラーが出た環境では、`--ssl-no-revoke`で通信できた。
  同じエラーが実際に出た環境に限り、失効確認を無効にする影響を理解したうえで使い、証明書自体の検証は維持する。
