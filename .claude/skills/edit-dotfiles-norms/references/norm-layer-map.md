# 規範の層と置き場所の地図

本書は、本リポジトリ（dotfiles）でエージェント向け文書を追記・新設・移動するエージェントが、置き場所を層から順に選ぶための一覧である。
選び方の手順は`agent-basics:writing-standards/references/agent-documents-basics.md`「置き場所の選定」が定め、本書はその手順が使う層の一覧を持つ。
各スキルの責務は、そのスキルの`description`と冒頭の責務文が定め、本書へ再掲しない。
各層の本文が実際にどの起動区分へ届くかは`agent-toolkit:atk-mode/references/agent-documents.md`「配送範囲表」で確かめる。

## 層の一覧

上の行ほど読む主体が広く、読む契機が早い。
起動時に読む対象は、その起動契約と読込表の「起動時」の行が定める。
規範の適用範囲（読む主体）と読む契機に合う最も狭い層を選ぶ。

| 層 | 所在 | 読む主体 | 読む契機 | 置く内容 | 置かない内容 |
| --- | --- | --- | --- | --- | --- |
| 入口 | `agent-toolkit/skills/atk-mode/SKILL.md` | agent-toolkitを導入した環境の全メイン | 作業の前と会話の圧縮の後（SessionStartのhookの案内。OpenCodeは設定の`instructions`の案内） | 全ての作業に当てはまる原則の要旨、硬い制約、ユーザーの方針、作業と手順書の表 | 1つの作業だけの手順、形式の定義（上限は5,000トークン。`agent-documents-additions.md`「常時規範への追記条件」） |
| 委譲先の入口 | `agent-toolkit/skills/atk-mode/playbooks/subagent.md` | 全ての委譲先 | `agents_server`の起動の本文（全mode）。`Agent`ツールの委譲先はSubagentStartの通知の案内で読む | 委譲先に共通の制約、委譲元と双方が守る受け渡し（状態行、続行不能の区分、通知）、起動の種類ごとの権限 | 役割固有の手順（役割文書へ置く） |
| 手順書 | `agent-toolkit/skills/atk-mode/playbooks/`（ホスト固有の操作は`host-*.md`） | 入口の表の条件に当たったエージェント | 入口の「作業と手順書」の条件が成立した時 | 1つの作業の流れ（メインの報告と点検、委譲の起動・待機・受領・復旧、commitなど） | 全ての作業に当てはまる決まり（入口へ置く）、形式の定義 |
| 形式の定義と細則 | `agent-toolkit/skills/atk-mode/references/`（特定の判断だけに要る細則は`conduct.md`） | 手順書の段が指したエージェント | 手順書の段が指した時 | 1主題の形式の定義、入口と原則で言えない特定の判断の細則 | 作業の流れ（手順書へ置く） |
| 役割文書 | `agent-toolkit/skills/atk-mode/roles/<役割名>.parent.md`・`<役割名>.subagent.md` | 委譲元（`.parent.md`）、委譲先（`.subagent.md`） | `.subagent.md`は起動の本文として届き、`.parent.md`は委譲元が起動の前に読む | 役割固有の入力、手順、返却形式、受領と検収 | 他の役割や全エージェント共通の規定 |
| agent-basicsのスキル | `agent-basics/skills/<スキル>/SKILL.md`と`references/` | 品質基準を適用する全プロジェクトのエージェント | 常時（frontmatterの`description`）、スキル起動時（本文）、読込表の条件（references） | 成果物の品質基準（文章、コード、設計、レビュー、検索、不具合対応） | agent-toolkitの工程・WI・委譲の運用、dotfiles固有の事実 |
| hook通知・CLIの出力 | `agent-toolkit/agent_toolkit/_hooks/`、`agent-toolkit/hooks/`、`atk`の`次の操作` | 通知の契機に当たったエージェント | 操作・ターンの境界（hookの発火、コマンドの実行） | 機械的な検出・強制の結果、必要な操作、規範の節への参照 | 判断手順や条件の本文（規範の節に書いて参照する） |
| dotfilesのプロジェクト規範 | `AGENTS.md`、`.claude/skills/`（本スキルなど） | 本リポジトリを編集するエージェントだけ | 常時（`AGENTS.md`）、スキル起動時、読込表の条件 | 本リポジトリ固有の構成、開発・公開手順、記録の所在 | 配布先でも必要な規範（agent-basics・agent-toolkitへ置く） |

設計記録（`docs/development/design*.md`・`concepts*.md`・`incidents*.md`）は経緯と理由の記録であり、実行時に従う規定を置く層には数えない。
配布形式と配布先の対応は`.claude/skills/dotfiles-repo-layout/SKILL.md`、agent-basicsとagent-toolkitの境界の理由は`docs/development/design-agent-basics.md`を参照する。

## 選定の例

2026年11月、全エージェントが30分ごとに行う進捗点検の手順が、hook通知の本文（`progress_self_check.py`）と委譲の資料へ追記された。
読む主体は全エージェント、読む契機は通知を受けた時であり、表では「手順書」が該当する。
委譲の資料は委譲の責務に一致せず、hook通知は判断手順の本文を置かない層のため、どちらも置き場所から外れる。
現在は手順を`agent-toolkit:atk-mode/playbooks/main-reporting.md`「進捗点検」に置き、通知はその節を参照する。
