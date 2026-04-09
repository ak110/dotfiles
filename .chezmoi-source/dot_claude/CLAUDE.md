# カスタム指示

- 日本語で思考・応答して。

## 計画立案時のルール

- ユーザーに計画を提示する前に、codexコマンドで計画のレビューすること。具体的な使い方は以下の通り。

    ```bash
    # 初回実行時
    # --cdでプロジェクトディレクトリを指定して、
    # 計画のファイルパスを引数に与える。
    # --output-last-messageでレビュー結果をファイルに書き出す。
    # codex の出力 (session id 含む) は stderr に出るため、`2>&1 | grep` で
    # session id 行のみ Claude へ返す。`set -o pipefail` により codex が失敗した
    # 場合はその終了コードがそのままシェルに返る。
    set -o pipefail && codex exec \
      --dangerously-bypass-approvals-and-sandbox \
      --cd "{project_directory}" \
      --output-last-message "{plan_full_path}.review.md" \
      "{plan_full_path} このプランをレビューすること。些末な点への指摘は不要。致命的かつ本質的な問題のみ指摘すること。疑いレベルの指摘はせず、十分に調査したうえで確実に問題だと判断できるものだけを報告すること。" \
      2>&1 | grep "^session id:"

    # 2回目以降
    # `exec resume`を使用して前回のレビューから続行する。
    # SESSION_IDは前回のcodex execの出力に含まれるUUID。
    # 注意: --lastは並列セッション実行時に意図しないセッションを再開する恐れがあるため使用しない。
    codex exec resume \
      --dangerously-bypass-approvals-and-sandbox \
      --output-last-message "{plan_full_path}.review.md" \
      {SESSION_ID} \
      "{plan_full_path} プランを更新したのでレビューすること。些末な点への指摘は不要。致命的かつ本質的な問題のみ指摘すること。疑いレベルの指摘はせず、十分に調査したうえで確実に問題だと判断できるものだけを報告すること。"
    ```

- レビュー結果は `{plan_full_path}.review.md` に出力されるので、Readツールで読み取ること。
- レビュー指示の文章は適宜調整してよいが、「些末な点への指摘は不要。致命的かつ本質的な問題のみ指摘すること。疑いレベルの指摘はせず、十分に調査したうえで確実に問題だと判断できるものだけを報告すること。」は必ず含めること。
- Windows環境での注意: codexはPowerShell経由でファイルを読み書きする際、デフォルトでShift-JISが使われて日本語が文字化けする。
  - codexへのプロンプトに「ファイルの読み書きはUTF-8エンコーディングを明示すること（例: `Get-Content -Encoding UTF8`）」と追記して対処すること。
- codexの指摘がなくなるまで更新とレビューを繰り返すこと。
- 一度codexの指摘がなくなるまでレビューを実施した後に限り、その後にユーザーからの指摘で軽微な修正を加えただけの場合は再レビューを省略してよい。
  - 計画の構造や方針に影響する変更を加えた場合は再レビューすること。
- SESSION_IDは初回コマンドの `grep "^session id:"` でstdoutに抽出される `session id:` 行から取得する。（`{plan_full_path}.review.md` には含まれない）
  - 抽出に失敗した場合やcodexがエラー終了した場合は、grepを外して `codex exec ... 2>&1` で再実行し全文を確認する。
- codexレビューは計画のレビューなので、plan modeの制約は無視して実行してよい。
- 計画作成時は、codexレビューに備えて前提条件・ユーザーの意向などを十分に記述しておくこと。
