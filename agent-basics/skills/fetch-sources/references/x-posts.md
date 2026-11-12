# Xの投稿本文と添付写真の取得

本書は、X（旧Twitter）の投稿を調査資料として参照する際に、対象の投稿を一意に特定し、本文、投稿日時、添付写真を取得するための手順をまとめたものである。
引用のルールや事実確認の裏付け手順は、`agent-basics:writing-standards`の`references/investigation.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 投稿の同定と本文の取得

1. 対象を同定する。
   投稿URLの`x.com/<ユーザー名>/status/<投稿ID>`（`twitter.com`も同様）からユーザー名と投稿IDを抽出する。
   末尾の`/photo/1`などは同一投稿の写真表示画面である。
   `t.co`などの短縮URLはWeb取得ツールでリダイレクト先を確認し、転送先を取得できなければURLを推測せず「未取得」と記録する。
2. 「ホスト環境別の留意点」を踏まえて応答を保存・検査する。
   `https://api.fxtwitter.com/<ユーザー名>/status/<投稿ID>`へリクエストし、リダイレクト先まで追跡して応答をファイルへ保存する。
   最終HTTPステータスが200かつJSONの`code`が数値の200であることを確かめてから、必要な属性だけを`jq`などで会話へ抽出する。
   条件を満たさない応答と、成功応答でも必要な属性が欠ける場合は「未取得」とし、取得手段、HTTPステータス、診断メッセージ、取得できなかった範囲を報告する。
3. 本文と日時を区別して抽出する。
   `tweet.article`が無い、または`null`の通常投稿は`tweet.text`を本文とする。
   `tweet.article`がオブジェクトなら記事形式として、`tweet.article.title`を表題、`tweet.article.content.blocks[].text`を配列順に改行で連結したものを本文とする。
   記事本文にはこの配列を使い、`tweet.text`や`tweet.article.preview_text`と区別する。
   `tweet.created_at`は投稿日時、`tweet.article.created_at`は記事作成日時とする。
   必要な引用投稿は別の投稿として取得する。
4. 写真を確認する。
   `tweet.media.photos[].url`から写真URLを取得し、「添付写真の保存と確認」へ進む。
   配列が空か写真メディアが無い場合は「写真なし」とし、通信の失敗と区別する。
   記事の`blocks[].text`の連結だけでは記事内の画像や埋込を取得したとは扱わない。
5. 根拠に使う範囲を記録する。
   本文の取得状況と写真の取得・確認状況を独立して記録し、画像は実際に確認した範囲を残す。

上記はFxTwitterのv1形式であり、`/2/`を含むv2形式と区別してv1の属性名を使う。
仕様は[FxEmbedのAPIドキュメント](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)と[レスポンスの実装コード](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)を参照する。

## 添付写真の保存と確認

**1.** 保存・検査済みの応答JSONから写真URLをデータとして取り出し、実行環境の一時ディレクトリへダウンロードする。
保存先の絶対パスと写真の0始まりの配列位置を指定し、写真ごとに実行する。
URLはシェルのコードへ直接置換せず、取得した変数を引用してcurlの引数へ渡す。

```sh
response_file='<保存済み応答JSONの絶対パス>'
image_file='<画像保存先の絶対パス>'
photo_index=0
photo_url=$(jq -er --argjson index "$photo_index" \
  '.tweet.media.photos[$index].url | select(type == "string" and length > 0)' \
  -- "$response_file") &&
  curl -fsS --max-time 60 --proto '=https' --output "$image_file" --url "$photo_url"
```

URLの欠落・不正や取得コマンドの失敗は「未取得」と記録し、保存・実体確認を終えた写真だけを表示する。
該当節は「agent-basics/skills/fetch-sources/references/x-posts.md：添付写真の保存と確認：2026年11月10日」である。

**2.** HTTPエラーページのHTML等を画像と誤認しないよう、正常な画像データであることを確認する。

**3.** ホストの画像表示手段（Claude Codeの`Read`やCodexの`view_image`など）で実際に表示する。
調査の根拠には、画像から読み取れた情報だけを使い、「投稿の同定と本文の取得」の手順5へ戻る。

## ホスト環境別の留意点

過去の調査環境では、Xへの直接的なWebアクセスでHTTP 402が返り、FxTwitter APIからはHTTP 307（一時リダイレクト）が返されたが、`pbs.twimg.com`上の画像はcurlで正常に取得できた。
これは当時の特定環境における観測事実であり、他の環境で作業する場合は各手段の応答状況に応じて適切な代替手段を選択する。

Windows環境のcurlでSSL証明書の失効確認エラーが発生した過去の環境では、`--ssl-no-revoke`オプションを付与することで通信が成功した。
同様のエラーが実際に発生した環境に限り、失効確認を無効化するセキュリティ上の影響を理解した上でこのオプションを選択する。
その場合でも、証明書自体の検証処理は維持する。
詳細は`docs/development/audit-records-fetch-sources.md`の「agent-basics/skills/fetch-sources/references/x-posts.md：投稿の同定と本文の取得：2026年10月10日」を参照する。
