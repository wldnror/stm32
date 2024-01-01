#!/bin/bash
cd /home/user/stm32
git fetch origin

local=$(git rev-parse @)
remote=$(git rev-parse @{u})

if [ $local = $remote ]; then
    echo "이미 최신 상태입니다."
    exit 0
fi

git stash
git pull
git stash pop
