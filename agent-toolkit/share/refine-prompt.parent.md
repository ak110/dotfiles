# プロンプト評価担当の起動と受領

```text
起動対象: refine-prompt.subagent.md
```

`agent-toolkit:refine-prompt`を実行するエージェントが、本書に従ってプロンプト評価担当を起動し、指摘を受領する。
プロンプト評価担当の手順と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/refine-prompt.subagent.md`が定める。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 多ターン試行を起動する前と、その結果を受領する時 | `${CLAUDE_PLUGIN_ROOT}/skills/refine-prompt/references/multi-turn-trials.md` |

## 起動

評価する指示文をUTF-8のファイルへ保存し、`agent-toolkit:refine-prompt`のワークフローが決めた体数を同じ入力で並行して起動する。`agents_server`の`start`を次の引数で呼ぶ。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象プロンプトを保持するリポジトリの絶対パス |
| `subagent_md_path` | `refine-prompt` |
| `extra_params` | 次の名前付き入力だけ |

- `対象プロンプト`: 対象プロンプトの名前と、本文を保存したファイルの絶対パス
- `シナリオ`: ワークフローで決めた状況設定の1段落。シナリオを与えない場合は`なし`
- `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

再評価では前回の担当を継続せず新しく起動する（前回の指摘と反映を知る担当は評価が偏る）。シナリオは初回の内容のまま渡し、反映に合わせて容易にしない。

## 受領

結果は引数なしの`atk agents wait`で受け取る。返却が`## 出力`の形式に合わない場合は、同じ入力で1回だけ新しい担当を起動し直す。
指摘の統廃合、取捨選択と改善案の作成は`agent-toolkit:refine-prompt`「指摘の集約と改善案作成」に従う。

多ターン試行では同参照文書の自由本文の委譲を使い、両役の終端、全対話、提出物と採点結果を受領する。
既存のプロンプト評価担当の指摘リストへ多ターンの結果を混ぜず、機能、隠した要件、対話の判定を事例別に読む。
