# 品質基準と作業運用のプラグイン分離

agent-basicsはコード・設計・テスト・レビュー・文章の品質基準を提供し、agent-toolkitはWI・委譲・承認・記録・工程管理を提供する。
品質基準の単独導入を可能にし、個人の作業運用を導入する範囲をエンドユーザーが選べることを目的とする。

## 定義元と読込

品質基準の正式な起動スキルは`agent-basics:writing-standards`と`agent-basics:review-standards`である。
原因分析と検証の品質は同プラグインの`bugfix`と`check-execution`が持つ。
技術ごとの読込条件と資料名を保ち、品質資料は同じskill名・references名で移す。
toolkit側の公開skillと既存CLIは互換の呼び出し手段として保持し、品質の定義をbasicsへ委ね、運用固有の読込条件を持つ。

writing-standardsの`delivery-scope.md`、`session-state-and-flags.md`、`session-records.md`はtoolkitの配送とセッション操作に依存するため、同プラグインに残す。
混在する文書では、品質を判断する条件と、委譲・表の保存・工程遷移を分ける。
basics単独のエンドユーザーには、プロジェクトが採用する認可と作業手順を適用する。
toolkit併用時には、toolkitが自身の運用契約を適用してから、各品質スキルの条件付き資料へ進む。

## 配布形式と依存方向

両プラグインを同じGitリポジトリと`ak110-dotfiles`マーケットプレイスで配る。
Claude Codeではtoolkitの`dependencies`に名前だけの`agent-basics`を指定する。
Codexは両方を明示的に導入・更新し、basicsを先にそろえる。
basicsのCodex配布はskillsだけなので、toolkitのhookコマンド変換用の複製を必要としない。
Agent Plugins 1.0.0向けroot manifestは共通メタデータだけを投影し、Claude固有の依存配列を含めない。

basicsは常時フックとMCPを同梱せず、ホームへtoolkit運用規範を配送しない。
toolkitの常時規範、役割別規範とセッション操作は、toolkitを選んだエンドユーザーへの配布として保持する。
Codex向けAGENTSの生成はtoolkitの常時規範から行い、品質本文の新しい提供元を参照する。

## 維持する品質判断

言語横断の標準ツール、単体HTMLのCDN、依存管理、技術別読込表と必要資料の読了条件を保つ。
移設のための提供元名・パス・運用呼出しの書換えと、既存品質本文の移動を分けて検収する。
後続プロジェクトはagent-basicsへの明示依存と品質skill名を追従させ、個人のtoolkit運用設定をプロジェクト常時規範へ混入させない。

## 選択理由

同じリポジトリと生成器を使うため、別リポジトリと独自の依存解決器は導入しない。
品質の定義をbasicsへ集約し、toolkitの互換の呼び出し手段は参照と運用契約を持つ。
ホスト別の依存機能は各manifestへ投影し、ホストが提供していない自動依存を仮定しない。

Claudeの依存配列は[公式仕様](https://code.claude.com/docs/en/plugin-dependencies)、Agent Pluginsの許可項目は[1.0.0のschema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json)を根拠とする。
