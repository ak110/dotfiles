# 委譲先完了後の自動再開

この実行環境は、あなたが`agents_server`で起動したsessionが全て終端した後、同じsessionを一度だけ自動的に再開し、1ターンにつき一度だけ継続指示を届ける。
実行環境がバックグラウンドタスクの完了を通知する場合（Claude Codeの`Agent`ツールで起動した委譲先やBashのバックグラウンドタスク）も、その完了後に同じ再開が働く。
待っている対象が上記の自動再開対象で、その対象の完了通知が同じsessionを再開する場合だけ、`待機中: <実行識別子>`の1行でそのターンを終え、再開したターンで所定の返却形式を返してよい。
Codexの`functions.exec`のcell IDと`exec_command`のsession IDは、この自動再開の対象から外れる。公開されている`functions.wait`と`write_stdin`で同じターンに出力と終了状態を回収する。
`atk agents wait`で結果を回収したsessionは、再開の契機から外れる。
