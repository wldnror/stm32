#!/bin/bash
cd /home/user/stm32 || exit

# 원격 저장소의 변경 사항을 가져옵니다.
git fetch

# 로컬 브랜치와 원격 브랜치의 커밋 수를 비교합니다.
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse @{u})
BASE=$(git merge-base @ @{u})

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "이미 최신 상태입니다."
    exit 0
elif [ "$LOCAL" = "$BASE" ]; then
    echo "업데이트가 있습니다. pull을 수행합니다."
    git pull
    exit 1
else
    echo "로컬 브랜치가 원격 브랜치보다 앞서 있습니다."
    exit 2
fi
