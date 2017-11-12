#!/bin/bash -eu
DOT_DIR=~/dotfiles

git clone 'https://github.com/ak110/dotfiles.git' $DOT_DIR
cd $DOT_DIR
./deploy.sh

