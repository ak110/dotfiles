if exists('b:did_ftplugin')
  finish
endif
let b:did_ftplugin = 1

set fileencodings=utf-8
set fileformats=unix,dos
set cindent

set expandtab
set tabstop=4

" } を押したら自動インデント
"imap } }<Esc>=%

" lint
nmap ,l :call RubyLint()<CR>

" RubyLint
" @author halt feits <halt.feits at gmail.com>
function RubyLint()
    let result = system( &ft . ' -c ' . bufname(""))
    echo result
endfunction


