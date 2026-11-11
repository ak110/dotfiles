# 運用機能: 日次リリース

## 日次リリースの自動実施

dotfilesリポジトリを対象とする`agent-toolkit:process-wi`は公開工程で`develop`から`master`へのリリースPRを作成してマージまで実施するかを判定する。
同じ最上位セッションですでにリリースPRをマージ済みの場合、後続の差分は`develop`へのpushとCI成功まで進め、次のセッションのリリースPRへまとめる。
実行時に従う規範は`release-dotfiles`スキルであり、判定条件、評価の時点および実施手順は同スキルが定める。
導入の経緯と根拠は[concepts-workflows.md](concepts-workflows.md)の「developとmasterのリリース運用」にある。
