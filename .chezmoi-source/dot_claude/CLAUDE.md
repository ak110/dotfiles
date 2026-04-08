# カスタム指示

## 計画立案時のルール

- ユーザーに計画を提示する前に、codexコマンドで計画のレビューすること。具体的な使い方は以下の通り。

    ```bash
    # 初回実行時
    # --cdでプロジェクトディレクトリを指定して、
    # 計画のファイルパスを引数に与える。
    # --output-last-messageでレビュー結果をファイルに書き出す。
    codex exec \
      --dangerously-bypass-approvals-and-sandbox \
      --cd "{project_directory}" \
      --output-last-message "{plan_full_path}.review.md" \
      "{plan_full_path} このプランをレビューして。瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。"

    # 2回目以降
    # `exec resume`を使用して前回のレビューから続行する。
    # SESSION_IDは前回のcodex execの出力に含まれるUUID。
    # 注意: --lastは並列セッション実行時に意図しないセッションを再開する恐れがあるため使用しない。
    codex exec resume \
      --dangerously-bypass-approvals-and-sandbox \
      --output-last-message "{plan_full_path}.review.md" \
      {SESSION_ID} \
      "{plan_full_path} プランを更新したからレビューして。瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。"
    ```

- レビュー結果は `{plan_full_path}.review.md` に出力されるので、Readツールで読み取ること。
- レビュー指示の文章は適宜調整してよいが、「瑣末な点へのクソリプはしないで。致命的な点だけ指摘して。」は必ず含めること。
- Windows環境での注意: codexはPowerShell経由でファイルを読み書きする際、デフォルトでShift-JISが使われて日本語が文字化けする。
  - codexへのプロンプトに「ファイルの読み書きはUTF-8エンコーディングを明示すること（例: `Get-Content -Encoding UTF8`）」と追記して対処すること。
- codexの指摘がなくなるまでアップデート→レビューを繰り返すこと。
- 一度 codex レビューに合格したあと、ユーザーからの指摘で軽微な修正を加えただけの場合は再レビューをスキップしてよい。
  - 計画の構造や方針に影響する変更を加えた場合は再レビューすること。
- SESSION_ID は最初の `codex exec` 出力の冒頭付近に表示されるので、見落とさないよう注意する。
  - 実行ログが長くなると流れて見つけにくくなるため、初回実行直後に控えておくこと。
- codexレビューは計画のレビューなので、plan modeの制約は無視して実行してよい。
- 計画作成時は、codexレビューに備えて前提条件・ユーザーの意向などを十分に記述しておくこと。
