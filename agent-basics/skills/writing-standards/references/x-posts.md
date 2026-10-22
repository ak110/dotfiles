# Xの投稿本文と添付写真の取得

本書はXの投稿を調査資料として読む場合に、投稿を同定して本文・日時・写真を取得する手段を定める。
引用と事実の裏付けは`investigation.md`に従う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |

## 投稿の同定と本文

- `x.com/<ユーザー名>/status/<投稿ID>`（`twitter.com`も同形式）からユーザー名と投稿IDを読む。`/photo/1`などの末尾は同じ投稿の写真表示である。`t.co`の短縮URLは公開されたWeb取得手段で転送先を確かめてから同定し、取得できなければURLを推測せず未取得として記録する
- `https://api.fxtwitter.com/<ユーザー名>/status/<投稿ID>`を取得する（リダイレクトは転送先まで取得する）。最終HTTP状態が200かつJSONの`code`が数値の200の場合だけ、`tweet.text`を本文、`tweet.created_at`を投稿日時、`tweet.media.photos[].url`を添付写真の取得先として採用する。それ以外は未取得とし、取得手段、状態、診断と未取得の範囲を報告する。成功応答でも欠けた属性は未取得とし、写真の一覧が空か写真媒体が無い場合は「写真なし」として取得失敗と区別する
- 本文の取得と写真の取得・読了は別々に記録する

このURLはFxTwitterのv1形式であり、`/2/`を含むv2形式と区別してv1の属性で読む。
参照先は[FxEmbedのAPI説明](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)と[応答実装](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)である。

## 添付写真の保存と読取

写真URLごとに実行環境が用意した一時領域へ保存する。

```sh
curl -sS --max-time 60 -o <画像保存先の絶対パス> "<画像URL>"
```

保存した内容が画像であることを確かめ（HTTPエラーの本文を写真として扱わない）、ホストのローカル画像の読取手段（Claude Codeの`Read`、Codexの`view_image`など）で実際に描画して読む。根拠に使うのは読み取れた範囲に限る。

## ホストごとの注意

元の調査ホストでは、Xへの直接のWeb取得が402、FxTwitter APIが307を返し、`pbs.twimg.com`の画像はcurlで取得できた。これはその環境での観測であり、他の環境では手段ごとの状態から代替取得を選ぶ。

Windowsのcurlで証明書の失効確認のエラーが出た元ホストでは`--ssl-no-revoke`で回復した。同じ診断が出た環境だけで、失効確認を無効にする影響を確かめて選び、証明書本体の検証は維持する。

APIの参照先と元ホストの観測条件・再検証手段は、`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/x-posts.md：投稿の同定と本文：2026年10月10日」にある。
