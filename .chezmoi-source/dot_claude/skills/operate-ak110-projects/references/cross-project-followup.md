# 足回りファイルの編集と追従作業

個人プロジェクトの足回りファイルを編集する時と、推奨ガイドや姉妹プロジェクトの共有ファイルへの追従（追従提案のAWIなど）を実施する時の手順書である。
足回りファイルはpyfltrの推奨ガイドへそろえ、追従作業は追従元の現行の内容へ合わせることに限る。

## 追従作業の着手前の確認

推奨ガイド（`~/pyfltr/docs/guide/recommended.md`、`~/pyfltr/docs/guide/recommended-nonpython.md`）か姉妹プロジェクトの共有ファイルへ追従する作業では、着手前に次を行う。

1. 推奨ガイドが新設した設定へ追随する時は、追随先が実際に取得する配布物の公開版にその設定が含まれることを、公開版を明示した実行で確かめる。
2. 追従を目的とする計画には、追従元の現行の内容への追従に限って追従元の設計の改善は対象外とすることと、追従元の改善が必要と分かったら追従元のリポジトリ宛てのAWIとして登録することを書く。

## 足回りファイルの推奨設定維持

各プロジェクトの`pyproject.toml`、`.textlintrc.yaml`、`.markdownlint-cli2.yaml`、`.pre-commit-config.yaml`、`.github/workflows/`配下を推奨ガイドにそろえる。

1. lint違反への対応と推奨設定の緩和は、`agent-basics:writing-standards/references/implementation-time.md`「lintと機械チェック」で決める。
   設定の緩和（ruff、pylint、textlintなどの設定ファイルへのignoreの追加、lint設定の弱体化）は、根本原因の修正と行単位の無視で足りない場合に限る。
2. 推奨から外れる設定を入れる時は、その箇所に理由のコメントを書く。
3. 変更を確定したら、姉妹プロジェクトと推奨ガイドへの波及を`references/project-synchronization.md`の手順で判定する。

## 返す報告に含める項目

確かめた公開版、推奨から外した設定とその理由、波及の判定へ渡した変更を返す。
