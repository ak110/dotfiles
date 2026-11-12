# 監査記録: 規範の点検

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## 規範の点検記録

点検を実施するたびに、実施日の小見出しで記録を加える。

### 2026年11月11日

2026年11月11日、公式ガイド「Prompting Claude Fable 5.2」を基準に、`agent-toolkit:refine-prompt`の「文書の点検」を規範全体へ1回実施した。
この記録は`.claude/skills/edit-dotfiles-norms/references/model-host-audit.md`が点検の要否の判定に使う。

- 基準にしたモデルID: `claude-fable-5-2`
- 基準にしたガイド: [Prompting Claude Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)と、`agent-basics/skills/writing-standards/references/agent-documents-additions-volume.md`「見直す契機と点検観点」
- ホストごとの確認済みの版: Claude Code 2.1.322、codex-cli 0.167.1
- 対象の版: 2026年11月10日のdevelopからmasterへのマージcommit（`6ec5050fa`）の本文
- 対象: 572ファイル
  - `agent-toolkit/share/rules/`の常時規範8
  - `agent-toolkit/skills/`と`agent-basics/skills/`のスキル本体21、references 481、生成物19
  - エージェントへ返す通知文を定義するhookのソース41
  - `agents_server`の利用説明2
- 1段階目（観点ごとの検索と一致箇所の読取）の判定: 提案あり40、該当なし513、生成物19、本文を読まないと判定できないファイル0
- 2段階目（通読）: 常時規範8ファイルに限る
- 提案: 63件（1段階目41件、2段階目22件）で、採否は未決定である
- 点検対象の規範への編集: なし
- 提案一覧の保存先: リポジトリ外（点検の担当がprocess-wiの統括へ引き渡し、ユーザーへは統合後の報告用UWIで届ける）

評価を担当したのは`gpt-6.1-sol`の担当であり、Claude Fable 5.2自身による評価と、追加指示の無いモデルとの比較は実施していない。
提案が挙げる行動への影響は、この点検では検証していない。
