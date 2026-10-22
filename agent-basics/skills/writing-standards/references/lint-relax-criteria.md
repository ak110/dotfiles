# lint設定緩和の判断基準

textlint・markdownlint・pyfltrの推奨設定例などで、検出ルールの緩和・無効化・除外指定の追加を検討する際の判断基準。
意味のある検出を無効化せずに、誤検知・過剰検知への対処を決めることを目的とする。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `textlint-violations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/textlint-violations.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「lintと機械チェック」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「lintと機械チェック」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-principles.md#「要件と認可」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「要件と認可」` |

## 根本目的（緩和判断の基準）

lint設定全体は次の目的を支える。
緩和の判断では、そのルールがいずれかを支えているかで評価する。

- LLM風の癖を排除したフォーマルな日本語表現の維持（口語表現・比喩動詞・主観評価語・空虚な形容など。`preset-ja-technical-writing`配下のルール、pyfltr内蔵版`colloquial-check`）
- Markdownとしてセマンティックに正しい記述の維持（強調・コードブロック・見出し階層の意味的役割。markdownlintの`MD022`・`MD031`・`MD040`等）

## 緩和判断のフレーム

- 緩和の対象が現行版で再現することを、対象ツールの個別ルール実行（`textlint --rule <name>`・`markdownlint --rules <name>`等）で確かめる。再現しない違反への緩和は不要な除外指定として残る
- ルールが何を防ぐかを公式ドキュメントと`textlint-violations.md`で確かめ、根本目的のいずれかを支えるなら、設定での緩和も行単位の無視コメントも使わず文面側で対処する
- ルールの無効化は、そのルールが特定構文で仕様上不可避な誤変換を起こす場合（コード識別子の`.`を句点へ自動変換するなど）に限る
- 緩和の手段と範囲は`implementation-time.md`「lintと機械チェック」に従う
- 緩和・無効化・閾値変更を確定したら、そのルールへ言及する運用ガイドの記述も同じ改訂で実設定へそろえる

ユーザーが明示的に投入した緩和指示の扱いは`quality-principles.md`「要件と認可」に従い、観測事実が原文の理由と一致しない場合も採否を覆さず観測事実を計画ファイルへ記録する。

## pyfltr推奨設定例への反映

pyfltrの推奨設定ガイドへ反映する場合も同じ判断で既存推奨設定を見直し、緩和する箇所には根拠（根本目的のいずれも支えていない、仕様上不可避など）を設定ファイルのコメントとして残す。
