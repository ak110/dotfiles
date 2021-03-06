#!/bin/bash
set -eu
DOT_DIR=~/dotfiles

# 元から入っていたものを退避するディレクトリ (diffりやすいようにdotfilesと同じ階層にする)
if [ ! -d ~/.dotfiles.bk ] ; then
    mkdir ~/.dotfiles.bk
fi

# chmod
chmod 600 $DOT_DIR/.ssh/*
chmod 700 $DOT_DIR/.ssh $DOT_DIR/.ssh/conf.d
chmod 755 $DOT_DIR

# deploy
for f in .??*
do
    [[ "$f" == ".git" ]] && continue
    [[ "$f" == ".gitignore" ]] && continue

    if [ -e ~/$f -a ! -L ~/$f ] ; then mv ~/$f ~/.dotfiles.bk/ ; fi
    ln -snfv $DOT_DIR/$f ~/$f
done

# ~/.ssh/config
$DOT_DIR/bin/update-ssh-config

