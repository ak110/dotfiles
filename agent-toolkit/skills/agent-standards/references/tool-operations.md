# ツール操作の補足規定

大量の文書読込、大規模なブロック置換、plugin資源のroot失効時に参照する。

## 大量の文書読込

複数の文書を全文読み込む場合は、先に行数またはバイト数を確認し、長文を1つのツール出力へ連結しない。
切り詰めを観測した場合は取得済みの範囲を再取得せず、未取得の範囲だけを分けて読む（努力目標）。

## 大規模なブロック置換（Claude Code）

100行超の連続ブロック置換は`Edit`の`old_string`にブロック全体を含めるか行範囲スライスを使う。

## plugin資源のroot再解決

plugin資源のrootが失効したことを観測した場合、以後は保持済みのrootを再確認なしで再利用せず、
実行環境が提供するplugin導入情報から現行の導入版とrootを再解決し、
利用する資源の実在を確認してから後続処理へ渡す。

Claude Codeでは、導入版とrootは`~/.claude/plugins/installed_plugins.json`の`installPath`から取得する。
`~/.claude/plugins/data/`配下はplugin本体の展開先ではない。

Codexでは、実行中スキルのSKILL.md絶対パスから末尾成分（`skills/<skill-name>/SKILL.md`）を除いた
接頭部をrootとして導出する。導出したrootは配下資源の実在確認を経てから用いる。
