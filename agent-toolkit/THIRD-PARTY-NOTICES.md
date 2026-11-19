# 第三者素材の著作権・許諾表示

agent-toolkitへ取り込んだ第三者素材の原典、改変内容と許諾全文を示す。
第三者素材を追加・改訂する編集者は、配布物から許諾表示を取得できる状態を保つ。
パスはagent-toolkitルートからの相対パスである。

## pstack

- 原典: [cursor/plugins](https://github.com/cursor/plugins/tree/bf0c7cd80abdbf7fe47b634978a7d1b4071f71a7/pstack)の`pstack/`（確認commit `bf0c7cd80abdbf7fe47b634978a7d1b4071f71a7`）。
- 取り込んだ資料:
  - `skills/principle-never-block-on-the-human/SKILL.md`、`skills/bro/SKILL.md`、`skills/sharpen/SKILL.md`。
  - `skills/poteto-mode/playbooks/investigation.md`。
  - `skills/poteto-mode/SKILL.md`と`skills/poteto-mode/playbooks/`の構成。
  - `skills/poteto-mode/playbooks/multi-phase-plan.md`、`opening-a-pr.md`、`babysit.md`、`shipping.md`。
  - `skills/show-me-your-work/SKILL.md`、`skills/figure-it-out/SKILL.md`、`skills/principle-prove-it-works/SKILL.md`。
  - `skills/blast-radius/SKILL.md`、`skills/ketchup/SKILL.md`。
  - `skills/poteto-mode/playbooks/eval.md`、`skills/poteto-mode/playbooks/authoring-a-skill.md`の評価に関する文。
  - `skills/poteto-mode/playbooks/bug-fix.md`、`feature.md`、`refactoring.md`、`perf-issue.md`、`hillclimb.md`、`prototype.md`、`visual-parity.md`。
- 取り込み先:
  - `skills/atk-mode/SKILL.md`（入口が作業の種類から手順書を1つ選ぶ構成）。
  - `skills/atk-mode/playbooks/confirmation.md`（認可された範囲の可逆な技術判断は先に進めてから示す）。
  - `skills/atk-mode/playbooks/user-utterance.md`（通じなかった説明を平易に言い直す、調査だけの依頼の成果物の境界）。
  - `skills/atk-mode/playbooks/wi-drafting.md`（最も元に近い資料へ戻る、第三者が合否を判定できる条件を置く）。
  - `skills/atk-mode/playbooks/write-plan.md`（調査だけの依頼は計画もcommitも作らずに終える）。
  - `skills/atk-mode/references/plan-format.md`（1行を1つの作業単位とし証拠と対にする、記録の原本を1つにする）。
  - `skills/atk-mode/playbooks/report-completion.md`（進捗・判断・報告の原本を1つの作業記録にする）。
  - `skills/atk-mode/references/report-formats.md`（ユーザーの判断を待つ項目を報告の本文だけで決められるように書く）。
  - `skills/atk-mode/playbooks/verification.md`（簡単に成功しすぎた時は観測の方法を先に確かめる、安全の根拠になる1つの事実を確かめる、検証結果の3値）。
  - `skills/atk-mode/playbooks/publish.md`（PRの本文は短い説明にする）。
  - `skills/atk-mode/references/ci.md`（既知の修正を1回のpushへまとめる、自動マージの予約はマージの完了ではない）。
  - `skills/atk-mode/playbooks/refine-prompt.md`、`skills/atk-mode/playbooks/refine-prompt-trials.md`。
    評価の入力に評価と分かる語や他の候補の存在を含めない。
    成果物から採点する。
    盲検の採点担当が1回で採点する。
    構造のある対象だけケースを作る。
  - `skills/atk-mode/playbooks/implementation.md`の「実装」手順1と「作業の種類ごとの要点」。
    先に終えないと他が進まない工程を先に終える。
    不具合の修正は、残る原因の候補を最も多く減らす実験から順に確かめる。
    整理は、構造を動かす前に今の挙動を固定する。
    速くする作業は、やめる段から安くする段までの順に比べる。
    繰り返して改善する作業は、1回に1つだけ変えて1回測る。
    試作は、決める問いがある時だけ作る。
  - `skills/atk-mode/playbooks/visual-parity.md`（移行の前に比較の基準を作り、移行の間は比較の基準と仕組みを変えない）。
- 改変: 該当の文を日本語へ翻訳し、既存の規範の手順へ要約・再構成して組み込んだ。
  目的の変更と公開の操作に事前の承認を求める条件を加えた。
- 許諾: [MIT License](https://github.com/cursor/plugins/blob/bf0c7cd80abdbf7fe47b634978a7d1b4071f71a7/pstack/LICENSE)。

```text
MIT License

Copyright (c) 2026 Lauren Tan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
