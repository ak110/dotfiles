---
name: refine-prompt
description: >
  エージェントへのテキスト指示を前提知識のないサブエージェントに読ませて改善提案の一覧を取得し、
  起動目的に応じてユーザーとの対話または技術判断で採否を決めて反映する手法。
  対象はスキル、スラッシュコマンド、タスクプロンプト、CLAUDE.mdの各節、コード生成プロンプトなど。
  プロンプトやスキルを新規作成・大幅改訂した直後や、指示と挙動の不一致を評価したい場合に使用する。
# 元ネタ: https://zenn.dev/mizchi/articles/empirical-prompt-tuning
---

# プロンプトの改善案の検討

エージェントへの指示文を、前提知識を持たないプロンプト評価担当に読ませて指摘を集める。メインエージェントはその指摘をもとに改善案を作成し、採否を判断して反映する。
評価担当の内部手順は`${CLAUDE_PLUGIN_ROOT}/share/refine-prompt.subagent.md`が定める（所在参照）。起動と受領の手順は、本書の「プロンプト評価担当の起動と受領」、返却形式は「評価担当の返却契約」に従う。

評価担当の起動や試行には、委譲手段、参照資料、対象の実行環境が必要となる。これらを利用できない環境では、提示されたプロンプトを現在のセッション内で直接評価する。その際は、直接評価である事実と、未実施の試行内容を明記する。独立した担当による試行やファイルへの反映は、委譲を実行できるClaude CodeやCodexで継続する。指摘や改善案の文面を作成する際は、[文章の品質基準](references/quality-standards.md)を適用する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../check-execution/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../review-standards/references/reviewee.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/references/reviewee.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-basics.md#「定義の所有者」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md#「定義の所有者」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認の選択肢を組む手順」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認の選択肢を組む手順」` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 改善案を作成する前（エージェント向け文書が対象の場合） | `agent-basics:writing-standards`の「エージェント向け文書の編集時に読む資料」 | 判断 | `section:agent-basics:*/skills/writing-standards/SKILL.md#「エージェント向け文書の編集時に読む資料」` | |
| 対話と成果物を評価する多ターン試行を選んだ時と結果を受領する時 | `references/multi-turn-trials.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials.md` | |
| 実セッションから小課題を作り、条文変更の効果を比較する方法を選んだ時 | `references/multi-turn-trials.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials.md` | |

## 使用条件

frontmatterに記載した条件に加えて、利用頻度の高いスキルや、自動化の中核となるプロンプトを堅牢化したい場合にも本スキルを使用する。
起動コストに見合わない単発のプロンプトや、作成者の主観的な好みを反映するだけの作業は、本スキルの適用対象から除外する。この除外は本スキルという手段の選択に限定する。プロンプトの欠陥が品質（Quality）に影響する場合は、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」に従って処置の要否を判断する。
指示と挙動の不一致を問題として調査するときは、原因を指示の曖昧さに限定せず、評価や追記案の検討に先立って`agent-toolkit:bugfix`を起動する。初動対応・原因分析・対策選定を進めた結果、指示文の改訂が必要と判断された場合に本スキルを適用する。通常のプロンプト改善は、この不具合調査の流れとは区別して進める。

## ワークフロー

Plan modeの外で実行する（Claude Codeでは`ExitPlanMode`で終了する）。元の依頼における計画作成の要否は本スキルによって変わらないため、`agent-toolkit:plan-mode`の呼び出しは不要である。

1. `agent-basics:writing-standards`を呼び出す。問題を起点として評価する際は、「使用条件」で確定した原因と対策の範囲を入力として扱う。原因の確定には調査結果を用い、評価担当の指摘は参考情報として扱う。
2. 評価対象を確定し、目的に応じて手法を選択する。選択肢には、シナリオに基づく読み取り専用の単発評価、シナリオを用いない批判的通読、実際の操作・対話・成果物を採点する多ターン試行がある。単発評価は指摘の返却をもって完了とする。改訂前後の評価を公平に比較するため、シナリオは反復評価の間も初回と同じ内容を維持する。
3. 対象プロンプトの分量や論点の数に応じて評価担当の人数を決定し、起動して指摘を受け取る。
4. 受け取った指摘をまとめ、改善案を作成する。採否の判断と反映は「指摘の集約と改善案作成」に従って実施する。
5. ユーザーが対話形式での採否判断や反復を求めて起動した場合は、作業を続行するか終了するかをユーザーに確認する。続行する場合は、新しい評価担当を起動して再評価する。エージェント自身が品質確認のために起動した場合は、必要な評価と反映が完了した時点で確認を挟まず、元の依頼の残余工程へ進む。反映完了後は、対象ファイルの編集手順を定めたスキルと`agent-toolkit:check-execution`に従う。元の依頼を完了するときは、成果・検証結果を`agent-toolkit:completion-report`の1つの作業完了報告へ集約して送達する。評価結果の受領だけで全体を終了せず、同期の振り返りは起動しない。

委譲手段（`agent-toolkit:delegation`）を利用できない環境では、同一セッション内で直接評価を実施し、結果を提示する際に「プロンプト評価担当を起動できないため直接評価」と明記する。
多ターン試行は、`references/multi-turn-trials.md`に従い、独立した2つの役割を起動できる環境で手動実行する。結果の差異は、同一事例における観測事実として受け取る。
既存の固定課題や単発評価で判断できる場合はその結果を活用する。実セッションに基づく比較が採否の判断に不可欠な場合に限り、同資料の「実セッション由来の小課題による比較」を選択する。

## 指摘の集約と改善案作成

メインエージェントは、元の意図を損なわないように重複や関連する指摘をまとめ、`agent-toolkit:review-standards`および同スキルの`references/reviewee.md`に基づいて採否を判定する。定義元からの転記を求める指摘については、`agent-basics:writing-standards/references/agent-documents-basics.md`「定義の所有者」に沿って妥当性を評価する。
採用する指摘に対しては、改善案をdiff形式で作成する。評価担当の出力はユーザーの画面に直接表示されないため、指摘の文面は元の表現をできる限り尊重して保持する。

- エージェントが品質確認のために起動した場合は、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に沿って技術的な指摘の採否を自ら判断する。目的や期待される結果の変更、あるいはユーザー固有の好みや認可が必要な事項に限ってユーザーに確認する。
- ユーザーが対話による採否判断を求めて起動した場合は、後述のフォーマットに沿って提案を作成し、回答しやすい件数ごとに確認を求める。質問の構成方法は`agent-toolkit:user-confirmation-and-report`「確認の選択肢を組む手順」に従う。Claude Codeでは`AskUserQuestion`の`multiSelect`を用いて一度に最大4件ずつ提示し、`label`に「提案N: <変更対象>」、`description`に変更前後の要点を記載する。片方のみを採用すると文章に矛盾が生じる提案は、同一の選択肢にまとめる。チェックが付いた項目を採用、付かなかった項目を不採用として扱う。修正を希望する場合は「Other」欄に提案番号と修正内容を記入するよう質問文で案内し、得られた指示に沿って反映する。

### ユーザー提示フォーマット（1件ごとに繰り返す）

````text
### 提案 N: <変更対象を1行で>
- 指摘元: <プロンプト評価担当が報告した該当箇所と問題内容を1〜2文で>
- 変更理由: <なぜ変更すべきか1〜2文>
- 変更内容: 以下のdiff形式で示す。新規追加なら`+`のみ、削除なら`-`のみでよい

  ```diff
  - <現在の文言>
  + <変更後の文言>
  ```

- 期待効果: <この変更で何が改善するか1文>
- 追随対象: <採用時に追随する他の文書・記述。無い場合は「なし」>
- 種別: 加筆 / 書き換え / 削除
````

## プロンプト評価担当の起動と受領

```text
起動対象: refine-prompt.subagent.md
```

`agent-toolkit:refine-prompt`を実行するエージェントが、本書に従ってプロンプト評価担当を起動し、指摘を受け取る。
プロンプト評価担当の手順は`${CLAUDE_PLUGIN_ROOT}/share/refine-prompt.subagent.md`に従う（所在参照）。返却形式は本書「評価担当の返却契約」に従う。

### 起動

評価対象の指示文をUTF-8のファイルに保存し、ワークフローで決定した人数分の評価担当を同一の入力で並行起動する。`agents_server`の`start`を次の引数で呼び出す。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象プロンプトを保持するリポジトリの絶対パス |
| `subagent_md_path` | `refine-prompt` |
| `extra_params` | 次の名前付き入力だけ |

- `対象プロンプト`: 対象プロンプトの名前と、本文を保存したファイルの絶対パス
- `シナリオ`: ワークフローで決定した状況設定の1段落。シナリオを与えない場合は`なし`

単発評価は、読み取り専用で指摘を返却するまでの処理で完結させる。
委譲元は、入力内容と返却内容を当初の目的および現行の要求と突き合わせて検収する。記録の更新は`share/work-contract.md`「更新する時点」に従う。

再評価する際は、常に新しい評価担当を起動する（前回の指摘内容や修正履歴を知っているエージェントは評価にバイアスが生じるため）。シナリオは初回と同じ内容をそのまま渡し、改訂後の内容に合わせて条件を変更しない。

### 受領

結果は引数なしの`atk agents wait`で受け取る。返却内容が本書「評価担当の返却契約」の形式に一致しない場合は、同一の入力で新しい担当を1回に限り再起動する。
指摘の集約、採否の判断、改善案の作成は、`agent-toolkit:refine-prompt`の「指摘の集約と改善案作成」に従う。

多ターン試行を実施する場合は、`references/multi-turn-trials.md`「両役の入力と返却」に基づく自由文形式の委譲を利用し、双方のセッション終了、対話履歴全体、提出物、採点結果を受け取る。
多ターン試行の結果は、通常のプロンプト評価担当による指摘一覧とは分離して管理し、機能面、非公開の要件、対話の適切さに関する判定を事例ごとに個別に確認する。

<!-- return-contract:refine-prompt:start -->
<!-- 生成元: share/refine-prompt.subagent.md「出力」。手動編集しない。 -->
### 評価担当の返却契約

以下の固定形式のみを返却する。指摘1件ごとに`### 指摘`の見出しから`種別`の行までを繰り返して記述する。

```text
状態: completed
### 指摘 N: <問題箇所を1行で>
- 重要度: 高 / 中 / 低
- 該当箇所: <対象プロンプト中の引用または位置を1〜2文で>
- 問題内容: <何が問題か1〜2文>
- 想定される悪影響: <この問題を放置すると何が起きるか1文>
- 種別: 不明瞭 / 冗長 / 不足 / 誤り / 過剰な指定 / その他
```

続行不能と追加報告行は、共通契約`../../share/rules-subagent.md`「返却形式の受け渡し」に従う。入力不足の場合は、対象プロンプトの不足情報と取得不能の根拠を記す。
<!-- return-contract:refine-prompt:end -->
