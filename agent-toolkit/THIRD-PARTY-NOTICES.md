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
- 取り込み先:
  - `skills/atk-mode/SKILL.md`（入口が作業の種類から手順書を1つ選ぶ構成）。
  - `skills/atk-mode/playbooks/confirmation.md`（認可された範囲の可逆な技術判断は先に進めてから示す）。
  - `skills/atk-mode/playbooks/user-utterance.md`（通じなかった説明を平易に言い直す、調査だけの依頼の成果物の境界）。
  - `skills/atk-mode/playbooks/wi-drafting.md`（最も元に近い資料へ戻る、第三者が合否を判定できる条件を置く）。
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
