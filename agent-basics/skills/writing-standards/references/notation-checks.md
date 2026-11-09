# 表記チェックの対象と実行

本書は、表記の検証を担当するエージェント向けに、チェックの対象範囲、一括実行と診断時の手順を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `tone-examples.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/tone-examples.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `tone-examples-llm-tone.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/tone-examples-llm-tone.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `input-material.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/input-material.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |
| 手順6で診断へ対応する前 | `textlint-violations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/textlint-violations.md` |

## チェックの対象範囲

- 計画ファイルは、文章lint、口語表現チェック、ダッシュ記号チェックの必須対象から除外する（読み手がその計画を実行するエージェントに限られており、表記の統一がタスクの特定や完了条件の判定に影響しないため）。
- エージェント向け文書に掲載する用例は、正しい表現と、その表現が成立する条件を説明する文章で構成し、通常の文章チェックの対象とする。
  用例と解説の書き方は`tone-examples.md`および`tone-examples-llm-tone.md`を参照する。
  なお、検出用テストデータ、内部保存形式の識別子、および他者の逐語引用は、自身が作成した用例と明確に区別して原文のまま保持する。

| 規則 | 検出を担当する既存ツール | 検出対象外の扱いと意味確認の担当 |
| --- | --- | --- |
| 日本語と半角英数字の間隔、和文のハイフン、山括弧の対応 | textlintのJTF 3.1.1、4.2.6、4.3.7 | コードブロック内の逐語データやコードは原文を維持する。インライン識別子の正確さや操作例の意味は作成者が確認する |
| 1文150文字制限、読点の個数、箇条書きの末尾句点、接続詞の重複 | textlintのsentence-length、max-ten、JTF 1.1.3、no-doubled-conjunction | コードブロックを除く通常の本文を検出対象とする。文を分割した際の条件、確からしさ、意味の正確さは修正担当者が確認する |
| 同一階層における同名見出し | markdownlint MD024 | 見出しと本文の意味的な整合性や親子関係はレビュー限定基準に従って確認する |
| 口語表現の辞書突き合わせ | 口語表現チェッカー（pyfltrのcolloquial-checkなど） | Markdownの引用ブロックとコードブロックは検出対象外とする。引用の原文は維持し、辞書にない不自然な表現はレビュー担当者が文脈から判断する。和語の動詞を漢語へ置き換える候補（「作る」→「作成する」、「出す」→「出力する」など）は、文脈で対象と動作が明確なら採らずに和語のまま残す |
| 地の文におけるemダッシュ、horizontal bar、2倍ダッシュ | プロジェクトが用意するダッシュチェッカー | コードブロック、インラインコード、URLを対象外とし、識別子の表記を維持する |

上記チェッカーを持たない実行環境では、完成時に手動の自己点検によって各規則を確認する。
チェックを実行できない状態を「合格」とみなさない。
引用の正確さ、識別子の保持、および文章の意味の整合性はツールの機械判定では保証されないため、各担当者が目視で確認する。
エラー診断時の詳しい対処手順は、`textlint-violations.md`を参照する。

### 逐語引用の検出範囲

原文の改変が許されない逐語引用の書式ルールは、`input-material.md`の「ユーザー入力素材の取扱い」に規定されている。
各チェックツールにおける逐語引用箇所の検出対象範囲は次のとおりである。

| チェック項目 | 引用ブロック（`>`）の内側 | フェンス付きコードブロック（````）の内側 |
| --- | --- | --- |
| 口語表現チェック | 対象外 | 対象外 |
| textlint | 検出対象 | 対象外 |
| ダッシュチェック | 検出対象 | 対象外 |

監査記録は、`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/notation-rules.md：逐語引用の検出範囲：2026年9月5日」を参照する。

## 文章チェックの一括実行

1. 対象漏れを防ぐため、「チェックの対象範囲」と「逐語引用の検出範囲」で対象を選ぶ。
   対象拡張子は`.md`、`.py`、`.txt`、`.yaml`、`.yml`、`.toml`で、ソースコード内のコメント行も含む。
   チェッカー未導入なら同表の各規則を手動で自己点検する。
   以降のpyfltr手順は、プロジェクトがpyfltrと表記チェッカーを導入している場合に適用する。
2. 全対象へ到達するため、恒久的な成果物には現在のホストが公開するserver key `pyfltr`の`run`を選び、引数をそろえる（完全修飾名はClaude CodeとCodexで異なる）。
   - `paths`: 対象全ファイルの絶対パス（重複を除く）
   - `work_dir`: チェック設定を持つプロジェクトの絶対パス
   - `commands`: 対象拡張子に対応するコマンドの和集合。
     Markdownを含む場合は`["textlint", "colloquial-check"]`、それ以外の対応拡張子のみなら`["colloquial-check"]`とし、登録されたダッシュチェック等も含める
   - `enable`: `["colloquial-check"]`
   - `no_exclude`と`no_fix`: ともに`true`
   - `allow_external_paths`: `work_dir`のプロジェクト外のファイルを含む場合のみ`true`
3. 同じ設定の検査をまとめるため、複数ファイルを1回の`run`で一括チェックする。
   MCPの`run`が使えなければ、導入済みpyfltrの公開ヘルプ（`--help`）でCLIの起動形を確認し、同様の対象とオプションで実行する。
   外部ファイルには`--allow-external-paths`を付ける。

   ```sh
   <pyfltrの起動形> run --commands=<全対象へ到達するコマンドの和集合> --enable=colloquial-check --no-exclude --no-fix --output-format=jsonl --work-dir <チェック設定を持つプロジェクトの絶対パス> <対象ファイルの絶対パス...>
   ```

4. 未到達を完了と誤認しないため、レスポンスの`completion`が`completed`、`files_reached`が指定ファイル数と一致し、全指定コマンドが`completed_commands`に含まれることを確認する。
   `incomplete_commands`、`missing_targets`、`fully_excluded_files`もすべて空であることを確かめる。
   CLIはJSONL最終行の`summary`で同じ条件を確認する。
   `incomplete`や`not_reached`は未完了とし、上記3項目と`skipped_reason`、`warnings`から原因を特定して対処する
5. 完了した検査の診断を把握するため、完了確定後に警告・エラー件数を確認する。
   詳細が必要なら`run_id`をMCPの`show_run_diagnostics`へ渡す
6. 意味と原文を保つため、`textlint-violations.md`に従って診断へ対応し、警告解消だけを目的に情報量を減らす書き換えを避ける。
   他者の原文（記事や発表のタイトル、引用した発言、固有名詞など）の警告語句は保持し、残した箇所と理由を完了報告に明記する
