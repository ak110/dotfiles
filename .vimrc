scriptencoding utf-8
" ↑一番上に書かないとダメっぽい？

syntax enable

set nocompatible

set nobackup
set viminfo='50,:0,f0,h
" '     マークの履歴数(ファイル毎のカーソル位置？)
" "     レジスタの中身の履歴数
" :     コマンドラインの履歴数
" /     検索パターンの履歴数
" %     バッファリストの履歴数
" f     ファイルマークを保存するかどうか
" h     viminfo ファイルの読み込み時に、'hlsearch' を無効にする
set history=50
set noexrc          " カレントディレクトリ内のvimrcとかを読み込まない

set shortmess+=I    " 起動時のメッセージを消す？


set number          " 行番号の表示
set ruler           " ルーラーを表示
set ttyfast         " 高速ターミナル接続を行う
set noerrorbells    " ビープ鳴らさない

set hlsearch        " 検索結果文字列のハイライト
set noshowmatch     " 括弧の対応を確認
set noincsearch     " インクリメントサーチ
set nowrap          " 折り返し

" タブ・インデント設定
"set autoindent
"set cindent
set smartindent
set cinoptions+=g0
set expandtab
set tabstop=8
set shiftwidth=4
set softtabstop=4

set list
set listchars=tab:>-,trail:#

set statusline=%n:\ %<%f\ %m%r%h%w[%{has('multi_byte')&&\ &fileencoding!=''?&fileencoding:&encoding}][%{&fileformat}]\ 0x%B=%b%=%l,%c\ %P
set laststatus=2
set cmdheight=1
set showcmd         " 入力途中のコマンドを右下に表示
set showmode        " 今使っているモードを表示
set title           " 謎。

" BSでインデント・改行・入力モードに入る前の文字の削除を許可
set backspace=indent,eol,start

" マウス関係。PuTTYのコピーが出来なくなる風味なのでやめとく。
"set mouse=a
"set mouse=nvi
"set mousemodel=extend
set mouse=

" 折り返しな行の移動
nnoremap j gj
nnoremap gj j
nnoremap k gk
nnoremap gk k

" インデント考慮して貼り付け
"nmap p p=']
"nmap P P=']

" backspace
noremap  
noremap!  

" ftpluginを有効にしたりとか
if has("autocmd")
  filetype plugin indent on
endif

" 全角スペースを視覚化
highlight ZenkakuSpace cterm=underline ctermfg=lightblue guibg=white
match ZenkakuSpace /　/

" vimdiffの色調整
highlight DiffAdd    cterm=bold ctermfg=10 ctermbg=22
highlight DiffDelete cterm=bold ctermfg=10 ctermbg=52
highlight DiffChange cterm=bold ctermfg=10 ctermbg=17
highlight DiffText   cterm=bold ctermfg=10 ctermbg=21

" 文字コード関連とか

let plugin_verifyenc_disable = 0

if exists('&ambiwidth')
    set ambiwidth=double " 記号でのカーソル位置ずれ防止
endif

set fileformats=unix,dos,mac
" set encoding=euc-jp
" set fileencodings=euc-jp,sjis,utf-16,utf-8,iso-2022-jp
" set fileencodings=utf-8,ucs-2le
set encoding=utf-8
set fileencodings=utf-8

" 文字コードの自動認識
if $LANG == 'ja_JP.eucJP'
    set encoding=euc-jp
elseif $LANG == 'ja_JP.SJIS'
    set encoding=sjis
endif
if has('iconv')
    let s:enc_euc = 'euc-jp'
    let s:enc_jis = 'iso-2022-jp'
    " iconvがeucJP-msに対応しているかをチェック
    if iconv("\x87\x64\x87\x6a", 'cp932', 'eucjp-ms') ==# "\xad\xc5\xad\xcb"
        let s:enc_euc = 'eucjp-ms'
        let s:enc_jis = 'iso-2022-jp-3'
        " iconvがJISX0213に対応しているかをチェック
    elseif iconv("\x87\x64\x87\x6a", 'cp932', 'euc-jisx0213') ==# "\xad\xc5\xad\xcb"
        let s:enc_euc = 'euc-jisx0213'
        let s:enc_jis = 'iso-2022-jp-3'
    endif
    " fileencodingsを構築
    if &encoding ==# 'utf-8'
        let s:fileencodings_default = &fileencodings
        let &fileencodings = s:enc_jis .','. s:enc_euc .',cp932'
        let &fileencodings = &fileencodings .','. s:fileencodings_default
        unlet s:fileencodings_default
    else
        let &fileencodings = &fileencodings .','. s:enc_jis
        set fileencodings+=utf-8,ucs-2le,ucs-2
        if &encoding =~# '^\(euc-jp\|euc-jisx0213\|eucjp-ms\)$'
            set fileencodings+=cp932
            set fileencodings-=euc-jp
            set fileencodings-=euc-jisx0213
            set fileencodings-=eucjp-ms
            let &encoding = s:enc_euc
            let &fileencoding = s:enc_euc
        else
            let &fileencodings = &fileencodings .','. s:enc_euc
        endif
    endif
    " 定数を処分
    unlet s:enc_euc
    unlet s:enc_jis
endif

" ucs-bom
let &fileencoding='ucs-bom,' . &fileencoding

" 日本語を含まない場合は fileencoding に encoding を使うようにする
if has('autocmd')
    function! AU_ReCheck_FENC()
        if &fileencoding =~# 'iso-2022-jp' && search("[^\x01-\x7e]", 'n') == 0
            let &fileencoding=&encoding
        endif
    endfunction
    autocmd BufReadPost * call AU_ReCheck_FENC()
endif


