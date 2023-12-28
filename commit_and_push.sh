#!/bin/bash

# 변경 사항 스테이징
git add -A

# 커밋 생성
read -p "Enter commit message: " commit_message
git commit -m "$commit_message"

# GitHub에 푸시
git push origin master
