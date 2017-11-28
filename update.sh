#!/bin/bash -eux
DOT_DIR=~/dotfiles

cd $DOT_DIR
git pull
./deploy.sh

