---
name: check-execution
user-invocable: false
description: >
  変更に着手する直前（検証の基準版を記録する時点）、formatter、linter、testerまたはプロジェクト固有の
  チェックツールを起動する直前と、変更範囲の検証の対象を選ぶ時点（計画の検証コマンドを書くとき、
  既存の検証コマンドを再利用して実行する直前を含む）に起動する。
  検証のために成果物を構築・起動・単発実行するコマンドを選ぶ前にも起動する。
---

# formatter・linter・testerの実行

本スキルは、formatter、linter、tester、プロジェクト固有のチェックツール、および検証のために成果物を構築・起動・単発実行するエージェントに対して、コマンドの適切な選び方を案内する。
各ツールの受け付け形式や出力形式については、各ツールのヘルプ、MCPツールのスキーマ、または公式ドキュメントに従う。

チェックを実行するには、対象の実行環境とCLI、またはチェック用のMCPツールが必要である。また、保存された実行結果を判定するには、その記録を読み取る手段が必要となる。実行手段が存在しない環境では、提示された保存結果の判定や、検証対象およびコマンドの選定までを担当し、未実行のチェック項目を明確に示す。新規の検証実行や実行記録の保存は、該当処理を実行できる環境で行う。結果を説明する際は、[文章の品質基準](references/quality-standards.md)に準拠する。

QCDの観点、3段判定、事実と推論の区別、硬い制約と努力目標の扱いについては、`agent-basics:writing-standards`の`references/quality-principles.md`が定める定義に従う。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したら判断や操作の前に指定節と、その成立に必要な前提・例外を自分で読む。全体が判断に必要な場合は全文を読む。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/quality-standards.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 実装に着手する直前（基準版を記録する時点）と、テスト・検証・CIの結果を成功時も含めて判定する前（プロジェクトの段階別・全体所要基準との比較を含む） | `references/diagnostics.md`（「読む出力」に所要時間の評価を集約） | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「読む出力」` | |
| 変更範囲の検証で動かすチェックとテストを選ぶ前（計画の検証コマンドを書くとき、実装後に検証する前、計画の検証行・前回の実行・CI失敗の調査記録にある検証コマンドを再利用して実行する前、実行レビューで直接影響範囲を求めるとき） | `references/verification-scope.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md` | |
| 要求の結果を観測する手段と環境を選ぶ前 | `agent-basics:writing-standards/references/testing.md`の「検証単位の選定」と冒頭 | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「検証単位の選定」`、`section:agent-basics:*/skills/writing-standards/references/testing.md#冒頭` | |
| 受入シナリオ検証を実施する前（文書の試行、操作・故障注入・自動終了の失敗が使用する資源の所有者と共有範囲を判定するとき、本番の起動単位からの公開前観測を含む） | `references/acceptance-scenarios.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md` | |
| 起動時 | `agent-basics:writing-standards/references/quality-principles.md` | 起動時 | `skill:check-execution` | |

## 実行条件と結果

1. 要求の結果を直接観測できるよう、読込表の`agent-basics:writing-standards/references/testing.md`「検証単位の選定」で手段と環境を選ぶ。
   変更範囲は`verification-scope.md`、実装直前の基準版は`diagnostics.md`に従って確定する。
2. 実行条件を保つため、構築・起動・単発実行の下層ツールを呼ぶ前に、作業ディレクトリからリポジトリルートまでのタスクランナー定義と、README・プロジェクト規範の案内を確認する。
   定義の例には`Makefile`、`mise.toml`、`justfile`、`Taskfile.yml`、`package.json`の`scripts`、`pyproject.toml`のタスクがあり、ツール固有の設定ファイルが無くても起動方法を持つことがある。
3. 結果を判定できるよう、公開仕様から目的に適した出力形式と対象範囲を選び、正規コマンドを実行する。
   対象限定などでそのまま使えない場合はレシピの引数・環境変数・前処理を引き継ぐ。
   画面への本文表示と、構造化診断の取得・要求への紐付けは用途を分ける。
4. 主張の範囲を確定するため、結果と取得条件の一致、テストの成功範囲を確認し、`diagnostics.md`に従って診断と所要を判定する。
5. 未観測を成功と取り違えないよう、未実施・失敗・観測不能の要求、理由、試した手段、限界を既存の検証記録へ残して返す。
   実行・結果保存の機能が無ければ対象と手順、未実行の範囲を整理する。
   記録生成前の実行基盤の失敗は既存の実行ログを使い、検証手段の改善に必要な情報を保つ。
