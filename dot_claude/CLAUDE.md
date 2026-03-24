# カスタム指示

- 日本語で応答すること。

## 計画立案時のルール

- ユーザーに計画を提示する前に、codex コマンドで計画のレビューすること。具体的な使い方は以下の通り。

    ```bash
    # initial plan review request
    PLAN_CONTENT=$(cat "{plan_full_path}") && codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" "このプランをレビューして。瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。

    ${PLAN_CONTENT}"

    # updated plan review request (毎回新規セッション)
    PLAN_CONTENT=$(cat "{plan_full_path}") && codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" "プランを更新したからレビューして。瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。

    ${PLAN_CONTENT}"
    ```

- レビュー指示の文章は適宜調整すること。ただし codex コマンドは本質的じゃない指摘をしてくるので「瑣末な点へのクソリプするな。致命的な点のみ指摘しろ。」という指示は必ず入れた方がいい。
- codexの指摘がなくなるまでアップデート→レビューを繰り返すこと。
- codexレビューは計画のレビューなので、plan modeの制約は無視して実行してよい。
- 計画作成時は、codexレビューに備えて前提条件・ユーザーの意向などを十分に記述しておくこと。
