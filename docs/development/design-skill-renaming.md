# 手順スキルの改名の設計記録

[設計記録の索引](design.md)と[振り返り・知見集約の設計記録](design-session-review.md)から参照する主題別の記録である。

## 手順スキルの改名

手順を提供するスキルは、名前から実行する作業を読み取れるよう動詞で始める。
判断基準・契約・規約集を提供するスキルは対象を表す名前を保持する。
命名基準は`agent-basics/skills/writing-standards/references/agent-skills.md`「YAML frontmatter」が所有する。
2026年11月1日に次の8件を改名し、旧名の中継スキルは配置しない。

| 旧名 | 新名 |
| --- | --- |
| `completion-report` | `report-completion` |
| `external-write-review` | `review-external-writes` |
| `plan-mode` | `write-plan` |
| `session-review` | `review-session` |
| `dotfiles-release` | `release-dotfiles` |
| `dotfiles-norm-edit` | `edit-dotfiles-norms` |
| `dotfiles-development` | `develop-dotfiles` |
| `ak110-projects-operations` | `operate-ak110-projects` |

役割名`external-write-review`、公開CLIの`session-review-evidence`・`session-review-prepare`・`session-review-state`、状態ディレクトリとWIの`source`値は保持する。
これらはスキル名と別の契約であり、改名に合わせて保存済みデータを移行すると要求範囲を超えるためである。
新しいUWIは新名のscopeで保存し、過去の通常起動とbatchの3形式を対応する起動単位で取り込む。
取込規則は`agent-toolkit/skills/user-confirmation-and-report/references/answer-transitions.md`に置く。
実行目的へのscope列挙は`agent-toolkit/agent_toolkit/_common/skill_modes.py`と`agent-toolkit/agent_toolkit/_atk/run_skill.py`が担う。
旧配布先はpost_applyの撤去表で回収し、Codexのリンクは新名へ切り替える。
