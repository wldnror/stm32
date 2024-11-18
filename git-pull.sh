#!/bin/bash
LOGFILE="/home/user/stm32/git-pull.log"
echo "===== $(date) =====" >> $LOGFILE
cd /home/user/stm32 >> $LOGFILE 2>&1

# 현재 체크아웃된 브랜치 가져오기
current_branch=$(git rev-parse --abbrev-ref HEAD)
echo "현재 브랜치: $current_branch" >> $LOGFILE

# 원격 저장소 정보 업데이트
git remote update >> $LOGFILE 2>&1

# 브랜치 상태 확인
status=$(git status -uno)
echo "git status -uno 결과: $status" >> $LOGFILE

if echo "$status" | grep -q 'Your branch is behind'; then
    echo "브랜치 '$current_branch'가 업데이트가 필요합니다." >> $LOGFILE

    # 변경사항 임시 저장
    git stash >> $LOGFILE 2>&1

    # 업데이트 적용
    git pull >> $LOGFILE 2>&1

    # 임시 저장한 변경사항 다시 적용
    git stash pop >> $LOGFILE 2>&1

    echo "브랜치 '$current_branch' 업데이트 완료." >> $LOGFILE
    exit 1  # 업데이트가 수행되었음을 알리기 위해 비-0 반환
else
    echo "브랜치 '$current_branch'은(는) 최신 상태입니다." >> $LOGFILE
    exit 0
fi

echo "===== 업데이트 스크립트 종료 =====" >> $LOGFILE
