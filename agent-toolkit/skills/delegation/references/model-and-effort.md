# 工程別設定の対象外のモデル選択

本書は、工程別モデル設定の対象外となる委譲でモデルとreasoning effortを選ぶ方針を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` |

## modelとreasoning effort

工程別設定の対象外となる委譲でも、`process-model-settings.md`「工程別モデル設定」の判断所有と自律選択の境界に従う。
内容が確定した書込は標準モデル、判定基準を明記できる定型走査は軽量モデルを候補とし、総費用で分担を決める（努力目標）。
reasoning effortの省略時は`medium`を適用する。
モデルとeffortは、同程度の課題で要求品質を満たす成果を受け入れるまでの総費用で比べる。
初回の推論・出力に加え、再試行、修正、追加調査、委譲と検収に使う量を含める。
単発のトークン量、時間当たりの利用枠消費、完成までの総費用は別の指標として扱う。
低いeffortで再試行や修正が増えれば総費用が上がる場合があり、高いeffortも同品質の成果に寄与するかで選ぶ。
投稿の構成例や節約率を全モデル・課題への保証として用いず、条件が比較できる実績と未計測の部分を区別する。
高いeffortは、推論の深さが結果を左右すると確かめた工程に限って選ぶ（努力目標）。
モデルを明示するときはeffortも指定し、可用性による切替は`fallback-launch.md`「代替起動」に従う。
関連する監査記録は、`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/runtime-routing.md：modelとreasoning effort：2026年8月」に記載されている。

エージェント自身がeffortを選ぶ場合は、次の方針を適用する。
工程別設定の初期値・プリセット、ユーザーの設定値は保持する。

- `xhigh`・`max`は、同種の作業で品質の向上を測って確かめた場合に限って選ぶ（努力目標。高いeffortは思考と出力トークンを増やし、前記の監査記録には軽量モデルの`max`で自動コンパクションが10回発生したというユーザーからの報告がある）。
- effortを指定できる起動では、思考を増減させる指示文を委譲プロンプトへ追加する前にeffortで調整する（努力目標。指示文より設定で調整する方針である）。
  effortを指定できない起動には適用しない。

公式ガイドの対象モデルと推奨内容の確認記録は、`docs/development/audit-records.md`にある。
項目名は「agent-basics/skills/writing-standards/references/agent-documents-basics.md：語調と現行規則の表現：2026年10月31日」である。
