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

### 2026年11月14日

GPT-6.1 Solを対象として、常時規範、両プラグインの資料、hook通知の生成元を点検した。
対象版は2.258.6の公開後の本文（`b6af4c7d9`）に固定した。

- 基準モデル: `gpt-6.1-sol`
- 一次資料: [GPT-6.1 Solモデル情報](https://developers.openai.com/api/docs/models/gpt-6.1-sol)と[Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model#gpt-61-sol)
- 確認したホスト版: Claude Code 2.1.323、codex-cli 0.167.2
- 対象: 633ファイル
  - 常時・役割規範8、agent-basics資料225、agent-toolkit資料305
  - Python hook候補ソース86、Function hooks候補ソース9
- 一次走査: 提案あり91、該当なし482、独立通読で判断する対象60
  - 各対象の検索結果と該当文脈を記録した
- 独立通読: 常時規範8と疑義のある対象を合わせた123ファイル
  - 一次走査と別の担当が20・68・35ファイルを読んだ
- 実行モデル: 一次走査はSolのlow・mediumでの未完遂を回復し、highで全対象の結果を取得した
  - 独立通読の待機結果はSolで、物理記録のidentityは未確認である
- 提案: 一次走査では91ファイルに候補があり、独立通読では16件を挙げた
  - 両段階の候補は重なるため加算しない
  - 採否は未決定である
- 対象規範への編集: 公式資料への参照追加と本記録だけ
  - 監査提案の適用は含めない
- 提案一覧と対象別判定の保存先: [Sol監査の判定と提案](audit-sol-2026-11-14.json)
  - 対象633件の判定、一次提案91ファイル、独立通読の提案16件と根拠を保存した
  - 作業用記録の保持期限後も、この記録と同じ版から取得できる

Using GPT-6の挙動説明はAstraの観測であり、Solでの実証と区別した。
Solによる変更前・変更後・追加指示なしの比較は実施していない。
提案が挙げる費用や挙動への効果は未試行の仮説である。
hook候補の件数とソース容量は、実際に配送される通知本文の量を表さない。

再点検では同じ対象版から両プラグインのスキル・references、常時規範、hook生成元を列挙する。
観点別検索で該当文脈を確認し、常時規範と判定を保留した対象を別担当が通読する。
対象別の判定集合と列挙集合を照合し、提案の採否と実モデルでの効果検証は別工程へ渡す。
