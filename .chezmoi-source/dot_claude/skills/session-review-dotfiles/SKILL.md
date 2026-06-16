---
name: session-review-dotfiles
description: >
  ユーザー手動起動またはStopフックからの明示的な呼び出し指示でのみ起動する。
  `agent-toolkit:session-review`スキルへのdotfiles個人環境向け拡張章として、
  pyfltrとagent-toolkitの改善提案章を提供する。
# 本スキルは`agent-toolkit:session-review`スキルと併用される拡張章。
# 4ステップ手順は`agent-toolkit:session-review`が担い、本スキルは拡張章のみを提供する。
# 本ファイル修正時は`scripts/claude_hook_stop.py`の誘導文も同期させる。
# 同hookは併用呼び出しの誘導文の前段に
# `agent-toolkit/scripts/_message_format.SESSION_REVIEW_PRECHECK`（完了の言い切り・
# 質問待ちなし・バックグラウンド待機表明なしの3条件）を付与し、満たさない場合は
# スキル起動を抑止する設計のため、本スキルの編集時はprecheck文言の前提が
# 変わっていないかを確認する。「起動方針」節の終了判定はprecheckと同一基準の
# 多重チェックとして保持する。
---

# セッション振り返り（dotfiles拡張章）

## 起動方針

本スキルは`agent-toolkit:session-review`スキルと必ず併用する。
Stopフックの誘導でどちらか一方のみを起動して報告を終えてはならない。
両スキルを起動したうえで、両者の章を1つのレポートにまとめて提示する。
提示する章は最大3章（プロジェクトドキュメント章・pyfltr改善提案章・agent-toolkit改善提案章）とする。
各章の取捨（pyfltr未使用時の省略、作業中プロジェクト自身に関わる章のプロジェクトドキュメント章への統合）は
「提示フォーマット」節の規定に従う。

Stopフックから呼ばれた場合の終了判定基準は`agent-toolkit:session-review`スキルの「起動方針」節に従う。
終了でないと判断した場合は両スキルとも振り返りを実施せず、作業継続または最終応答へ戻る旨を1文で示して終了する。

## 提示フォーマット

`agent-toolkit:session-review`スキルのステップ3で示すプロジェクトドキュメント章に続き、
以下の2章を同一フォーマットで追加する。

```markdown
## pyfltr改善提案

- <提案内容>
- ...

## agent-toolkit改善提案

- <提案内容>
- ...
```

各項目はセクション見出し配下に1項目1行・1コンセプトで書く。
対象ファイル・対象節が特定できる場合は提案内容の冒頭で明示する。
提案が無い章には同見出し配下に「提案無し」とのみ書く。
当該セッションで利用しなかった項目（例: pyfltr未使用）はスキップしてよい。

自己完結性の要件（観測した具体事象・改善後の振る舞いと根拠の明記、暗黙参照表現の禁止）は
`agent-toolkit:session-review`スキルのステップ3に従う。
本拡張章の提案も同スキルのステップ2自己検証(c)に従う。

### pyfltr改善提案

対象: pyfltr本体の挙動・メッセージ。

pyfltrプロジェクトで作業中の場合は、`agent-toolkit:session-review`スキルのプロジェクトドキュメント章へ統合する。

### agent-toolkit改善提案

対象: `agent-toolkit`プラグイン（スキル・フック・サブエージェント。`skills/pyfltr-usage/SKILL.md`を含む）と
`~/.claude/rules/agent-toolkit/`配下のルール。

dotfilesプロジェクトで作業中の場合は、`agent-toolkit:session-review`スキルのプロジェクトドキュメント章へ統合する。

## ステップ4の適用範囲

本拡張章で示した提案の反映は原則として別セッションで行う。

`~/.config/agent-toolkit/feedback-inbox.enabled`が存在する場合、提案提示後にメインが`AskUserQuestion`で各提案の採否を確認する。
採用分を対象リポジトリごとに`feedback-add`へ位置引数で渡して投入する。

- 提案件数が`AskUserQuestion`の上限（1問あたり最大4件）を超える場合は複数問に分割する
- いずれの提案も不採用となった場合は投入をスキップする
- CLIは内部で`git pull --ff-only` → ファイル生成 → `git commit` → `git push`を実行するため、
  別環境からの未取得コミットの取り込みとリモートへの反映までを自動で完了する

複数の対象リポジトリがある場合はリポジトリごとに呼び出す。

```sh
feedback-add <repo-path> <message1> [<message2> ...]
```

- pyfltr章の採用分: `<repo-path>`は`~/pyfltr`
- agent-toolkit章の採用分: `<repo-path>`は`~/dotfiles`
- プロジェクトドキュメント章の採用分: `<repo-path>`はカレントディレクトリ

CLIの標準出力（投入件数・ファイル名）をそのままユーザーへ提示する。

フラグファイル不在の環境では、各章の改善提案の反映に
`agent-toolkit:apply-feedback`スキルを別セッションで使う旨を案内する。
章ごとの対象リポジトリも併せて案内する（pyfltr章は`~/pyfltr`、agent-toolkit章は`~/dotfiles`）。
