if exists('b:did_ftplugin')
  finish
endif
let b:did_ftplugin = 1

set fileencodings=utf-8,euc-jp
set fileformats=unix,dos
set cindent

set expandtab
set tabstop=4

" } を押したら自動インデント
"imap } }<Esc>=%


