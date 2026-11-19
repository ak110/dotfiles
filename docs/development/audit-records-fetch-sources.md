# 監査記録: 外部資料の取得

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/fetch-sources/references/x-posts.md：投稿の同定と本文の取得：2026年10月10日

2026年10月10日、FxEmbedの公開原本を読解した。
API版とURLは[API introduction](https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx)、v1の処理は[src/realms/api/router.ts](https://github.com/FxEmbed/FxEmbed/blob/main/src/realms/api/router.ts)、応答の`code`・`message`・`tweet`は[src/embed/status.ts](https://github.com/FxEmbed/FxEmbed/blob/main/src/embed/status.ts)、本文・日時・写真の属性は[api-status.ts](https://github.com/FxEmbed/FxEmbed/blob/main/packages/atmosphere/src/types/api-status.ts)と[api-schemas.ts](https://github.com/FxEmbed/FxEmbed/blob/main/packages/atmosphere/src/types/api-schemas.ts)へ対応付けた。
v1は`/:handle/status/:id`、v2は`/2/`付きであり、資料のv1手順をv2の応答構造へ読み替えない。

元ホストの取得条件はリポジトリ外の作業記録に由来する。
X直接取得のHTTP 402、APIのHTTP 307、pbs画像の取得成功とWindows curlの失効確認エラーはその環境の記録であり、本改訂時に同じ通信を再現した結果でも全ホスト共通の仕様でもない。

同日の追加読解では、API introductionのResponse FormatがHTTP状態とは別に`code`を確認する必要を説明し、v1の応答実装が数値の`code`、200の`OK`、401・404・503・500の失敗を分岐し、API応答のHTTP状態にも`code`を設定することを確認した。
最終HTTP 200かつ数値のcode 200という採用条件を手順へ明示した。
これは公式実装の読解の記録である。
通信失敗や写真なしの実通信は新たに再現していない。

再検証では前記原本のv1の処理と応答生成を取得し、属性と版を比較する。
通信を試す場合は公開投稿のURL・最終URL・HTTP状態・応答本文を保存し、最終HTTP 200と数値の`code` 200の両方、必要な`tweet`の属性の成立を確認してから画像URLをmanaged-tempへ取得する。
HTTPだけの成功、codeの不一致・欠落・型の違い、通信とJSONの失敗、必要属性の欠落を未取得として区別する。
成功応答で写真の一覧が空か写真媒体がない場合の「写真なし」と、写真の取得失敗も区別する。
画像を実際に読んだホストと保存先を記録し、通信失敗の本文を画像や投稿の内容として扱わない。

2026年10月31日、FxTwitter v1で[記事形式の投稿](https://x.com/hexobuilds/status/2111056183385833515)と[通常投稿](https://x.com/undefinedKi/status/2116278013398557171)を、curlのリダイレクト追跡と60秒上限で保存取得した。
両応答は最終HTTP 200、JSONの数値`code` 200だった。
記事は`tweet.text`が記事URLであり、`tweet.article.title`と79件の`content.blocks`を持ち、各`text`を配列順に改行で連結して本文を保存できた。
通常投稿は`tweet.article`が`null`で、従来どおり`tweet.text`を抽出できた。
投稿日時の`tweet.created_at`と記事作成日時の`tweet.article.created_at`を別の属性として確認した。
[公式型定義](https://github.com/FxEmbed/FxEmbed/blob/main/packages/atmosphere/src/types/api-schemas.ts)の`TwitterArticleSchema`、`TwitterArticleContentStateSchema`、`TwitterArticleContentBlockSchema`でも表題、作成日時、本文配列と文字列型の`text`を確認した。
写真は記事0件・通常投稿1件という属性だけを確認し、画像と埋込の取得・表示は実施していない。
再検証は同じ2投稿の応答とHTTPヘッダーを保存し、数値`code`を確認した後、`jq`で表題・日時・本文を抽出する。
記事本文には`.tweet.article.content.blocks | map(.text) | join("\n")`、通常投稿には`.tweet.text`を使い、画像・埋込の取得状況を本文の取得状況と区別する。

## agent-basics/skills/fetch-sources/references/x-posts.md：添付写真の保存と確認：2026年11月10日

2026年11月10日、Bash 5.2.37、jq 1.8.2、curl 8.14.1で、保存済みJSONから写真URLを変数へ取得し、引用付きの変数展開で渡す例を確認した。
curlの公開ヘルプで`--url`、`--output`、`--proto`、`--max-time`の受理形式を確認した。
通常URL、ドル記号のコマンド置換記法、バッククォート、単一・二重引用符を含む合成URLを文書の例へ与え、curlを引数の記録だけを行う代用にした比較で、各URLが元の文字列のまま1引数になり、保存先、60秒上限、HTTPSの指定も保持された。
写真配列が空の入力ではjqが終了4となり、curlは呼ばれなかった。
外部通信、画像のダウンロード・実体確認・表示はこの引数境界の試験では行っていない。
再検証では同じ合成JSON群を用意し、文書中のシェル例をファイルへ保存してBashから実行する。
PATHの先頭にcurlの引数記録用の代用だけを配置し、`--url`の次の引数と元URLの完全一致、1引数であること、保存先と上限を確認する。
写真なしの場合の取得未実施と、写真がある場合の取得失敗を区別し、実通信の写真を根拠に使う場合は従来の実体確認・表示も行う。
