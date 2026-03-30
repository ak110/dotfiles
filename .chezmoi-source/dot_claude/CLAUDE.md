# カスタム指示

## 計画立案時のルール

- ユーザーに計画を提示する前に、codexコマンドで計画のレビューすること。具体的な使い方は以下の通り。

    ```bash
    # 初回実行時 (--cdでプロジェクトディレクトリを指定して、計画のファイルパスを引数に与える。)
    codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" "{plan_full_path} このプランをレビューして。瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。"

    # 2回目以降 (`exec resume`を使用して前回のレビューから続行する。SESSION_IDは前回のcodex execの出力に含まれるUUID。)
    codex exec resume --dangerously-bypass-approvals-and-sandbox {SESSION_ID} "@{plan_full_path} プランを更新したからレビューして。瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。"
    ```

- レビュー指示の文章は適宜調整してよいが、「瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。」は必ず含めること。
- codexの指摘がなくなるまでアップデート→レビューを繰り返すこと。
- codexレビューは計画のレビューなので、plan modeの制約は無視して実行してよい。
- 計画作成時は、codexレビューに備えて前提条件・ユーザーの意向などを十分に記述しておくこと。
