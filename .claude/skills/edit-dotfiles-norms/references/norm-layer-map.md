# 規範の層と置き場所の地図

本書は、本リポジトリ（dotfiles）でエージェント向け文書を追記・新設・移動するエージェントが、置き場所を層から順に選ぶための一覧である。
選び方の手順は`agent-basics:writing-standards/references/agent-documents-placement-selection.md`「置き場所の選定」が定め、本書はその手順が使う層の一覧を持つ。
各スキルの責務は、そのスキルの`description`と冒頭の責務文が定め、本書へ再掲しない。
各層の本文が実際にどの起動区分へ届くかは`agent-toolkit:delegation/references/delivery-scope.md`の配送範囲表で確かめる。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 層の一覧

上の行ほど読む主体が広く、読む契機が早い。
起動時に読む対象は、その起動契約と読込表の「起動時」の行が定める。
規範の適用範囲（読む主体）と読む契機に合う最も狭い層を選ぶ。

| 層 | 所在 | 読む主体 | 読む契機 | 置く内容 | 置かない内容 |
| --- | --- | --- | --- | --- | --- |
| 常時規範（共通） | `agent-toolkit/share/rules/common/` | 全エージェント（配布先全体） | 常時（ユーザー規範・`~/.codex/AGENTS.md`・委譲先の配送） | 優先順位、硬い制約、全工程の判断原則と、詳細へ届く起動契機 | 判断手順の詳細、特定工程・役割だけの規定（`agent-documents-additions-additions.md`「常時規範への追記条件」） |
| 共通の詳細 | `agent-toolkit/share/agent-conduct-details.md` | 全エージェント | 常時規範の各節が示す時点の明示読取 | 常時規範の節に対応する全エージェント共通の手順・例外 | 役割・工程・スキル固有の手順 |
| 役割別規範 | `agent-toolkit/share/rules/rules-main.md`・`rules-subagent.md`とホスト別の`rules-*.md` | メインだけ、または委譲先だけ（ホスト別は該当ホストだけ） | 起動時の注入（SessionStart、SubagentStart、agents_serverの配送） | 片側の役割だけに課す常時の規定 | 双方が守る受け渡しの規定（共通へ置く）、工程固有の手順 |
| 役割文書 | `agent-toolkit/share/<役割名>.parent.md`・`<役割名>.subagent.md`と、同じ役割から読込表で読む`share/`配下の資料 | 委譲元（`.parent.md`）、委譲先（`.subagent.md`） | 役割の起動時と読込表の条件 | 役割固有の入力、手順、返却形式、受領と検収 | 他の役割や全エージェント共通の規定 |
| agent-basicsのスキル | `agent-basics/skills/<スキル>/SKILL.md`と`references/` | 品質基準を適用する全プロジェクトのエージェント | 常時（frontmatterの`description`）、スキル起動時（本文）、読込表の条件（references） | 成果物の品質基準（文章、コード、設計、レビュー、検索、不具合対応） | agent-toolkitの工程・WI・委譲の運用、dotfiles固有の事実 |
| agent-toolkitのスキル | `agent-toolkit/skills/<スキル>/SKILL.md`と`references/` | agent-toolkitを使う全プロジェクトのエージェント | 常時（frontmatterの`description`）、スキル起動時（本文）、読込表の条件（references） | 工程と運用（WI、計画、委譲、レビュー工程、確認と報告、commitなど）。スキルの責務に一致するものだけ | 責務の異なる工程の規定、全エージェント共通の手順 |
| 配送文と注入 | `agent-toolkit/share/agents-server-*.md`、agent定義とfrontmatterの`skills` | agents_serverの委譲先、`Agent`ツールのサブエージェント | 起動時の配送・注入 | 起動区分ごとの権限と返却の短い契約 | 役割固有の手順（役割文書へ置く） |
| hook通知・CLIの出力 | `agent-toolkit/agent_toolkit/_hooks/`、`agent-toolkit/hooks/`、`atk`の`次の操作` | 通知の契機に当たったエージェント | 操作・ターンの境界（hookの発火、コマンドの実行） | 機械的な検出・強制の結果、必要な操作、規範の節への参照 | 判断手順や条件の本文（規範の節に書いて参照する） |
| dotfilesのプロジェクト規範 | `AGENTS.md`、`.claude/skills/`（本スキルなど） | 本リポジトリを編集するエージェントだけ | 常時（`AGENTS.md`）、スキル起動時、読込表の条件 | 本リポジトリ固有の構成、開発・公開手順、記録の所在 | 配布先でも必要な規範（agent-basics・agent-toolkitへ置く） |

設計記録（`docs/development/design*.md`・`concepts*.md`・`incidents*.md`）は経緯と理由の記録であり、実行時に従う規定を置く層には数えない。
配布形式と配布先の対応は`.claude/skills/dotfiles-repo-layout/SKILL.md`、agent-basicsとagent-toolkitの境界の理由は`docs/development/design-agent-basics.md`を参照する。

## 選定の例

2026年11月、全エージェントが30分ごとに行う進捗点検の手順が、hook通知の本文（`progress_self_check.py`）と`agent-toolkit:delegation`のreferencesへ追記された。
読む主体は全エージェント、読む契機は通知を受けた時であり、表では「共通の詳細」が該当する。
`agent-toolkit:delegation`は委譲の責務に一致せず、hook通知は判断手順の本文を置かない層のため、どちらも置き場所から外れる。
現在は手順を`agent-conduct-details.md`「進捗点検」に置き、通知はその節を参照する。
