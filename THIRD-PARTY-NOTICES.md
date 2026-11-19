# 第三者素材の著作権・許諾表示

リポジトリ直下の開発用資料（`.claude/skills/`と`AGENTS.md`）へ取り込んだ第三者素材の原典、改変内容と許諾全文を示す。
配布プラグイン（agent-toolkit、agent-basics）へ取り込んだ素材は、各プラグインの`THIRD-PARTY-NOTICES.md`に示す。
第三者素材を追加・改訂する編集者は、許諾表示を取得できる状態を保つ。
パスはリポジトリルートからの相対パスである。

## pstack

- 原典: [cursor/plugins](https://github.com/cursor/plugins/tree/bf0c7cd80abdbf7fe47b634978a7d1b4071f71a7/pstack)の`pstack/`（確認commit `bf0c7cd80abdbf7fe47b634978a7d1b4071f71a7`）。
  以下の原典パスは`pstack/`からの相対パスである。
- 取り込んだ資料と取り込み先:
  - `skills/poteto-mode/playbooks/authoring-a-skill.md`「Test cases if structural. Skip if subjective.」。
    取り込み先は`.claude/skills/edit-dotfiles-norms/SKILL.md`「規範を削除・縮小するときの消失確認」の手順4。
  - `skills/poteto-mode/playbooks/opening-a-pr.md`「one-minute briefing」。
    取り込み先は`.claude/skills/release-dotfiles/SKILL.md`の手順4。
  - `skills/babysit/SKILL.md`の確認だけのモード。
    取り込み先は`.claude/skills/merge-pr/SKILL.md`の冒頭。
  - `skills/poteto-mode/playbooks/shipping.md`「a requested auto-merge is not a merge」。
    取り込み先は`.claude/skills/merge-pr/SKILL.md`の手順4。
  - `skills/poteto-help/SKILL.md`「For a help question, don't start the work」。
    取り込み先は`AGENTS.md`。
  - `skills/poteto-mode/playbooks/eval.md`「never from the candidate's own claims」。
    取り込み先は`.claude/skills/agent-toolkit-edit/references/review-eval-set.md`。
- 改変: 各語句を日本語へ翻訳し、取り込み先の手順の判断基準へ組み込んだ。
- 許諾: [MIT License](https://github.com/cursor/plugins/blob/bf0c7cd80abdbf7fe47b634978a7d1b4071f71a7/pstack/LICENSE)。

```text
MIT License

Copyright (c) 2026 Lauren Tan
Copyright (c) 2026 Michael Denyer


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
