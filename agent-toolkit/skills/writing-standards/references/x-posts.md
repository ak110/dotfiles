# Xの投稿本文と添付写真の取得

本書はXの投稿を調査資料として読むエージェントが、投稿を同定し、本文・日時・写真を取得する手順を定める。
引用と事実の裏付けは`investigation.md`に従う。

## 投稿の同定と本文

1. `x.com/<ユーザー名>/status/<投稿ID>`または`twitter.com`の同形式からユーザー名と投稿IDを読む。`/photo/1`などの末尾は写真表示を指し、同じ投稿IDの投稿として扱う。
2. `t.co`の短縮URLは、公開されたWeb取得手段でリダイレクト先を確認してから同定する。転送先を取得できない場合は投稿のURLを推測せず、未取得の範囲を記録する。
3. `https://api.fxtwitter.com/<ユーザー名>/status/<投稿ID>`へアクセスし、HTTP状態とJSONの`code`を確認する。リダイレクトの場合は応答が示す転送先を取得し、最終応答を確認する。最終HTTP状態が200かつJSONの`code`が数値の200の場合だけ、`tweet.text`を本文、`tweet.created_at`を投稿日時として採用する。`tweet.media.photos[].url`は添付写真の取得先である。通信失敗、HTTPの非200、`code`の非200・欠落・型の不一致、JSONの解析失敗では投稿を未取得として扱い、取得手段、状態、得られた診断と未取得の範囲を報告する。両方が200でも、必要な本文・日時・写真URLの属性が欠ける場合は、その属性を未取得とする。成功応答で写真の一覧が空か写真媒体がない場合は「写真なし」であり、取得失敗と区別する。本文の取得と写真の取得・読了も別々に記録する。

このURLはFxTwitterのv1形式である。`/2/`を含むv2形式と区別し、v1の属性へ対応付けて読む。
参照先は[FxEmbedのAPI説明](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)と[応答実装](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)である。

## 添付写真の保存と読取

写真URLごとに`agent-toolkit:managed-temp`が定める領域の絶対パスを選び、次の形式で保存する。

```sh
curl -sS --max-time 60 -o <画像保存先の絶対パス> "<画像URL>"
```

終了コードと標準エラーを確認し、保存された内容が画像であることを確かめる。
HTTPエラーの本文を写真として扱わない。Claude Codeでは画像読取に対応する`Read`、Codexでは`view_image`など、そのホストが公開するローカル画像の読取手段へ保存先を渡す。
画像の取得と内容の読了を分け、実際に画像を描画して読み取ってから、その画像で確認できた範囲を根拠に使う。

## ホストに依存する注意

元の調査ホストでは、Xへの直接のWeb取得が402、FxTwitter APIへの取得が307を返し、`pbs.twimg.com`の画像はcurlで取得できた。
これらはそのホストの取得手段で観測された結果として、その環境に限定して扱う。
手段ごとの状態と理由を読んで代替取得を選び、取得の可否の根拠を公開投稿で確かめた範囲に限定する。

Windowsのcurlで証明書の失効確認のエラーが発生した元ホストでは、`--ssl-no-revoke`の追加が回復手段となった。
このオプションは同じ診断が出た環境だけで、失効確認を無効にする影響を確認して選ぶ。
証明書本体の検証は維持する。

APIの参照先と元ホストの観測条件・再検証手段は、`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/x-posts.md：投稿の同定と本文：2026年10月10日」にある。
