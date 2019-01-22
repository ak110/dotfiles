#!/bin/bash
set -eux
DOT_DIR=~/dotfiles

cd $DOT_DIR
git pull
git submodule update --init
./deploy.sh

