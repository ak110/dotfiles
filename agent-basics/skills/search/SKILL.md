---
name: search
user-invocable: false
description: >
  リポジトリまたはディレクトリのファイル属性と内容を検索する直前に起動する。
---

# 検索手段の選定と出力量の制御

ファイル属性やファイル内容を検索する際の手段選定、検索範囲の指定、および出力量の制御について定める。

検索するには、対象資料へアクセスできる読み取り手段と、CLIや検索用MCPが必要となる。利用可能な手段が提示された資料の読み取りに限られる場合は、その資料の範囲内で該当箇所を示し、未取得のリポジトリ全体についての不在や件数は未確認として扱う。検索手段が存在しない環境では、対象と検索条件をClaude CodeまたはCodexで実行できる形式に整理する。説明文の作成時は[文章の品質基準](references/quality-standards.md)を確認する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-basics:*/skills/search/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../secret-files/SKILL.md` | 判断 | `read:agent-basics:*/skills/secret-files/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md#「不在の主張」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/investigation.md#「不在の主張」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-basics.md#「読込表」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md#「読込表」` |
| 判断対象と検索の母集団を決める時 | `agent-basics:writing-standards/references/quality-principles.md#「事実と推論」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「事実と推論」` |

## 手段の選定

- 属性探索（ファイル名、種類、更新時刻、ディレクトリ構造など）: 対象パスを限定した`find`を使用する
- 現在のGit作業ツリーの追跡対象に限定した内容検索や、追跡ファイル全体の残存確認: `git grep`を使用する。`rg`は`--hidden`を指定しないと隠しディレクトリ配下の追跡ファイルを検索対象から外してしまうためである。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（1）」を参照
- 作業ツリー外の明示パス（別リポジトリを含む）、Git管理外の対象、または除外設定に従う内容検索: `rg`を使用する。隠しファイルや隠しディレクトリを含める場合は`--hidden`を付与する。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（2）」を参照
- 除外設定を反映したファイル一覧の取得: `rg --files`を使用する。構造化された集計や全体件数の上限設定が必要な検索には、MCPまたはCLIの`pyfltr grep`を使用する

`rg`も`git grep`も使えない環境では、`--include`、`--exclude`、`--exclude-dir`で対象を絞り込んだ`command grep`の出力をファイルへ保存し、必要な範囲だけを読み取る。
作業ツリー外のファイルから秘匿情報とみられる値を検索する場合は、`agent-basics:secret-files`に従い、値そのものを出力しない形式で実行する。

## 引数とシェルの扱い

Claude CodeのBash環境では、`grep`がugrepへ転送するシェル関数として定義されている場合がある（`type grep`で確認できる。パイプの後段に置いた`grep`も同様）。ugrepはignore設定やバイナリ判定によって除外された場合に無出力かつ終了コード1を返すほか、上限付き繰り返しを2つ含む`-E`の正規表現（例: `.{0,160}a.{0,160}`）で複雑度エラーとなり終了コード2・標準出力が空になる。不在や件数の確定、有界反復の検索には、対照として`command grep`を使用する。監査記録は`docs/development/audit-records.md`「agent-basics/skills/search/SKILL.md：引数とシェルの扱い：2026年10月9日」を参照。

`git grep`を使う際は`-F`、`-E`、`-P`のいずれかを明示し、オプション、検索パターン、`--`、pathspecの順に引数を並べて、`grep.patternType`などのホスト側の設定に影響されないようにする。正規表現のメタ文字を含む文字列を`-F`なしで渡すと、意図せず一致0件になることがある。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月7日」を参照。
また、`grep.lineNumber=true`が設定されている環境では`-h`を指定しても行番号が出力され続けるため、行番号を消すには`--no-line-number`を指定する。観測結果は`docs/development/audit-records.md`の「agent-basics/skills/search/SKILL.md：引数とシェルの扱い：2026年9月14日」に記録されている。
なお、grepやrgの単語境界指定（`-w`、`\b`）は日本語の文字も単語構成文字として扱うため、日本語に隣接する英単語には一致しない。日本語ドキュメントを検索する際は、単語境界をパターン側で明示的に表現する。

パイプラインを使用する際は`pipefail`などを有効にして全段の終了ステータスを取得する。`rg`は一致0件のときに終了コード1、パスの不在や引数の誤りのときに終了コード2と標準エラー出力を返す。`git grep`は一致するファイルがないpathspecに対しても終了コード1を返すため、推測したパスやglobは`git ls-files`や`rg --files`で実在を確かめてから渡す。

## 出力量の制御

リポジトリやディレクトリ全体を対象に内容検索する際は、まず件数、ファイル名、要約（`-l`、`-c`、あるいは`pyfltr grep`の要約・上限指定）を取得する。候補数と出力量を確認してから本文の取得へ進む（努力目標。一致箇所の本文をいきなり大量に出力させると、コンテキストウィンドウを急激に消費してしまうため）。source mapやminifyされたファイルの極端に長い単一行にも注意する。
`-c`で得られる値は一致行数であり、関数やクラスなどの実体数として扱う前に、1つの実体と1行が1対1で対応しているかを構造から確認する。候補のリストアップは所在を特定する段階と位置づけ、完了条件として本文の判定が必要な場合は、共通定義、生成側、消費側のコード取得へと進む。全文を読むのか所在の確認だけで済むのかは、受け取った完了条件に基づいて判断する。

## 検索の範囲と母集団

検索結果を判断へ用いる前に、`agent-basics:writing-standards/references/quality-principles.md`「事実と推論」に従って、要件や公開契約から定めた判断対象が検索の母集団に含まれるかを確認する。
検索可能な対象の一覧だけで判断対象を置き換えない。

通常の検索は、リポジトリのルートディレクトリから`.gitignore`やツール固有の除外設定を維持した状態で実行する。依存パッケージやビルド成果物は自動的に除外されるため、ルートから検索しても出力量はほとんど増えない。
検索範囲を特定のディレクトリやglobで絞り込むのは、対象がその範囲内にしか存在しないことが別の観測によって確定している場合に限る。推測で見込みをつけて範囲を狭めると、テストコードなど範囲外の一致箇所を見落としたまま結論を出してしまい、同一の値や識別子を持つコードが未修正のまま残る原因となる。

意味的に定義される集合を調査する場合は、定義元や利用側が値を取り出す方法（CLIの出力、説明用属性、辞書のキーなど）から母集団を定め、検索語ごとの一致件数を記録し、一致する語がなかった対象先も漏れなく確認する。未調査の対象が残る間は、一部の0件を対象全体の不在へ拡大解釈しない。

## 省略された結果の扱い

不在、件数、網羅性、終了条件の成立を結論づける際は、対象母集団全体をカバーし、本文が省略されていない取得結果を用いる。件数制限、行長制限、ファイルサイズ上限や、`head`、`cut`などによって一部が切り詰められた結果はあくまで限定的な観測であり、結論を出す前に省略のない完全な形で再取得する（データ全量が大きすぎる場合は、コンテキストを分離した環境で取得して要約を受け取る）。一般的な原則は`agent-basics:writing-standards/references/investigation.md`「不在の主張」に従う。
出力の切り詰めへの対処や保存された出力の読み取りでは、自らの権限で取得可能な不足分を担当内で補う。権限不足、ネットワーク到達不能、入力情報不足など自力で解消できない要因がある場合は、確認できた範囲と続行できない理由を分けて報告する。
なお、検索によって資料内の該当節を特定できた場合であっても、読込表において「全文読む」と定められている資料は全文を読み通す（`agent-basics:writing-standards/references/agent-documents-basics.md`「読込表」）。
