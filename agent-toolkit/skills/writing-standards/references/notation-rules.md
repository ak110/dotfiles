# 表記とMarkdownの検査

## 日本語の表記ルール

textlintの`preset-jtf-style`で検査される項目は同プリセットに従う。以下は補足規定。

- 日本語と全角括弧の境界はスペースなしで続ける。英文脈の半角括弧は英数字との間に半角スペースを1つ置く（例: `Python (CPython 3.12)`）
- 全角丸括弧の入れ子を避け、内側の括弧は読点による区切りや別文への分割で表現する
- 日本語の地の文や見出しではダッシュ記号（emダッシュ・horizontal bar・2倍ダッシュ）を避け、同格や補足には全角丸括弧を用い、言い換えは文の分割や読点で表現する
- 技術用語、固有名詞、コード識別子は原典の表記を優先する
- 「復号」は暗号を解く処理に限定して用いる。バイト列から文字列への変換や、JSON文字列などの符号化表現から値を取り出す処理は「デコード」と書く
- 地の文は日本語で書く。定着した技術用語や略語、外部技術に由来する語、識別子は原表記を維持する
- 内部の役割や概念には自然な日本語の名前を優先し、カタカナ名を使う場合は初出で定義する
- 恒久的な成果物の文面案を執筆する前に`textlint-violations.md`を確認する
- 計画ファイルは、文章lint、口語表現チェック、ダッシュチェックの必須対象から除外する。読み手がその計画を処理する実行主体に限られ、表記の統一が対象の特定と完成条件の判定を変えないためである
- `atk wi add`・`atk wi edit`の`--body-file`へ渡すWI本文は文章lintの必須対象から除外する。口語表現チェックとダッシュチェックは必須のままとする。計画とWI本文から転記する文面は、転記先の基準で検査して整形する

## 口語表現チェック

恒久成果物にはpyfltrの有効な検査定義が持つ`targets`を確認し、対象ファイルの拡張子へ到達するコマンドを選んで実行する。Markdownでは`textlint,colloquial-check`、それ以外の対応拡張子では`colloquial-check`を指定する。次のCLI形式で既定除外を解除し、対象到達性を判定できるJSONLを取得する。`<pyfltrの起動形>`は`python.md`の「pyfltrの起動形」に従って解決する。

```sh
<pyfltrの起動形> run --commands=<対象拡張子へ到達するコマンド> --enable=colloquial-check --no-exclude --output-format=jsonl <対象ファイルの絶対パス>
```

検査済みと判定できるのは、単一ファイルを指定したJSONLの`header`レコードの`files`が1であり、指定した検査コマンドのうち対象拡張子を`targets`へ持つものが1件以上あり、そのコマンドの対象ファイル数が1である場合だけとする。`missing_targets`、`fully_excluded_files`、skip、除外が現れる対象は未到達として扱う。対象到達済みの判定にはこの3条件を用い、終了コード0、診断0件、指摘0件の成功件数は到達後の結果として扱う。

検出範囲は`.md`・`.py`・`.txt`・`.yaml`・`.yml`・`.toml`とする。Markdown引用ブロックとフェンス付きコードブロック内は対象外、ソースコード内のコメント行は対象とする。

辞書の実体は`pyfltr`パッケージの`pyfltr.colloquial.check`が保持する`DENY_PATH`と`ALLOW_PATH`が指す。対象リポジトリが`pyfltr`を依存に持つ場合は、`uv run --frozen python -c 'import pyfltr.colloquial.check as c; print(c.DENY_PATH, c.ALLOW_PATH)'`で解決する。依存に持たない場合は、同じPythonの式を`uvx --from pyfltr python -c`へ渡して解決する。環境ごとに変わる`site-packages`の絶対パスを規範へ固定しない。

起草の後に前掲のCLI形式で検査して検出箇所を解消する。

対象ファイルが検査設定を持つプロジェクトの外側にある場合は、設定を持つプロジェクトの絶対パスを
`--work-dir`へ渡し、`--allow-external-paths`を併用する。

```sh
uv run --frozen pyfltr run --commands=<対象拡張子へ到達するコマンド> --enable=colloquial-check --no-exclude --no-fix --output-format=jsonl --allow-external-paths --work-dir <検査設定を持つプロジェクトの絶対パス> <外部対象ファイルの絶対パス>
```

この経路も、JSONLの`header`レコードと各commandレコードで対象ファイルへの到達を判定する。
外部パスを理由とする警告、skip又は対象除外が現れた結果は検査済みと判定しない。

## ダッシュチェック

日本語の地の文・見出しにおけるemダッシュ・horizontal bar・2倍ダッシュは`scripts/check_dash.py`で検査する。

```sh
uv run --project <plugin rootの絶対パス> --locked --no-default-groups path/to/writing-standards/scripts/check_dash.py path/to/file.md
```

コードブロック・インラインコード・URL内は対象外とし、検出対象の詳細は`check_dash.py`を正本とする。

## 逐語引用の検出範囲

原文の改変を許さない逐語引用の記法は`writing.md`の「ユーザー入力素材の取扱い」が定める。
記法の根拠となる検査ごとの検出範囲を次に示す。

| 検査 | 引用ブロックの内側 | フェンス付きコードブロックの内側 |
| --- | --- | --- |
| 口語表現チェック | 対象外 | 対象外 |
| textlint | 対象 | 対象外 |
| ダッシュチェック | 対象 | 対象外 |

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/notation-rules.md：逐語引用の検出範囲：2026年9月5日」にある。

## Markdown記述スタイル

Markdown要素は意味的役割に沿って使う。句点位置などの細則はmarkdownlint・textlintで検証する。

口調例は例示の内容を通常の成果物検査へ混入させないため、検査除外の対象として扱う。
口調例の正本は`tone-examples.md`と`tone-examples-llm-tone.md`とし、禁止語は各ファイルの節名で間接参照する。

- 図にはMermaid記法を推奨する
- 相対パスは配置先基準で解決し、`test -e <解決後パス>`で存在を確認する

強調記法、インラインコードの用途、1文ごとの改行、箇条書きの記述単位及び補足の書き方は`textlint-violations.md`の「文体と箇条書き」が定める。
lint設定の緩和・無効化・除外指定の追加は`lint-relax-criteria.md`に従う。
