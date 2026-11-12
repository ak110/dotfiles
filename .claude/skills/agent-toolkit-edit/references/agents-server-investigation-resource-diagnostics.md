# agents_serverの委譲不具合の調査手順：Windows境界試験とpytest回収失敗の診断

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 診断の読み方

委譲先が動作しない事象ではなく、WindowsのCodex境界試験とpytestの回収失敗で採取した資源診断（`resource-diagnostics:`）を読む手順である。
資源記録（`resource_snapshot`など）の一般的な読み方は`agents-server-investigation.md`「資源記録の読み方」に従う。

- WindowsのCodex境界試験では、試験開始・終了のpytest PIDとUTC時刻、App Serverの終了要求前・終了確認後の所有PID集合を対応付ける。
  各PIDの`created_at`と`ppid`で起動時刻と親子関係を確認し、終了後の`unavailable: NoSuchProcess`を保持PID不在の照会結果と区別する。
- pytestの回収失敗時は、標準エラーの`resource-diagnostics:`にある失敗パス・Windowsエラー番号・`before`・`after`を読む。
  `ownership.state`の`success`だけが保持PID照会の成功を示し、`unavailable`は採取不能、`unsupported`は対象外OSを表す。
  診断handleの自己PIDは外部保持者と分けて記録され、同じPIDの別handleの有無は判別できない。
  CIの開始・終了行と試験開始記録から、Python完全版・実行ファイル、runner image、Codex・pytest・xdistの版、並列度と最終終了コードも突き合わせる。
- 診断を採取できなかった場合も`next_action`に従い、元の回収例外のtracebackから失敗パス・winerrorを保存し、同じ実行の終了前後PID記録・CI環境・最終コードを原因分析担当へ渡す。
