#!/bin/bash
set -eu

for file in "$@"; do
    claude plugin validate --strict "$file"
done
