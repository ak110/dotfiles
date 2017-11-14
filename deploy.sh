#!/bin/bash -eu
DOT_DIR=~/dotfiles

# 元から入っていたものを退避するディレクトリ (diffりやすいようにdotfilesと同じ階層にする)
if [ ! -d ~/.dotfiles.bk ] ; then
    mkdir ~/.dotfiles.bk
fi

for f in .??*
do
    [[ "$f" == ".git" ]] && continue

    if [ -e ~/$f -a ! -L ~/$f ] ; then mv ~/$f ~/.dotfiles.bk/ ; fi
    ln -snfv $DOT_DIR/$f ~/$f
done

