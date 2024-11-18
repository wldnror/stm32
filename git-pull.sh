#!/bin/bash
LOGFILE="/home/user/stm32/git-pull.log"
echo "===== $(date) =====" >> $LOGFILE
cd /home/user/stm32 >> $LOGFILE 2>&1

# 원격 저장소 정보 업데이트
git remote update >> $LOGFILE 2>&1

# 브랜치 상태 확인
if git status -uno | grep -q 'Your branch is up to date'; then
    echo "이미 최신 상태입니다." >> $LOGFILE
    exit 0
fi

# 변경사항이 있을 경우 임시로 저장
git stash >> $LOGFILE 2>&1

# 업데이트 적용
git pull >> $LOGFILE 2>&1

# 임시로 저장한 변경사항 다시 적용
git stash pop >> $LOGFILE 2>&1

echo "업데이트 완료." >> $LOGFILE
