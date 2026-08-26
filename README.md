# TFT 메타 감시 디스코드 알림봇

롤체지지(lolchess.gg) 메타 통계 기반 TFT 덱 변화 감시 + Discord Webhook 알림봇

## 감시 대상

| 구분 | 덱 |
|------|-----|
| 메인덱 | 별돌보미 룰루 |
| AD 대체덱 | 전달자 미스 포츈, 운명술사 코르키 |
| 특수 상황덱 | 시간 균열자 이즈리얼 |
| 자동 감지 | 순방률/평균등수 TOP 3 신규, 표본 5000+ & 순방률 56%+, 기존보다 좋은 신규 덱 |

## 설치

```bash
pip install requests beautifulsoup4 python-dotenv
```

## 설정

```bash
cp .env.example .env
# .env 파일에서 DISCORD_WEBHOOK_URL 설정
```

## 실행

```bash
# 첫 실행 (현황 요약 알림 전송)
python tft_meta_watcher.py

# 테스트 모드 (Discord 전송 없이 콘솔 출력)
python tft_meta_watcher.py --dry-run

# 변경 없어도 현황 강제 전송
python tft_meta_watcher.py --force-alert

# 리롤 확률표 조회 (1~5코스트)
python tft_meta_watcher.py --reroll 3
```

## 기능

### 메타 감시 알림
- 패치 버전 변경 감지
- 롤체지지 데이터 업데이트 감지
- 별돌보미 룰루 순방률 2%p+ 하락 / 평균등수 0.15+ 악화
- AD 대체덱보다 좋은 새 덱 발견
- 시간 균열자 이즈리얼 컨디션 진입 (순방률 50%+ / 평균등수 4.35-)
- 순방률/평균등수 TOP 3 신규 진입
- 표본 5,000+ & 순방률 56%+ 신규 강력 덱
- 감시 덱이 메타 목록에서 사라진 경우

### 패치 일정 감시
- Riot 공식 패치 일정 페이지 자동 확인
- 패치 당일/전일 사전 알림
- B패치/핫픽스/긴급 패치 감지
- **패치 직후 6시간 집중 감시 모드** — 패치 적용 감지 시 자동 활성화, 매 실행마다 현황 요약 전송

### 리롤 확률 계산
- 코스트별 최적 리롤 레벨 자동 계산
- 상점 1회 확률, 50%/80%/95% 도달까지 필요한 롤 수 & 골드
- 감시 덱 현황에 리롤 가이드 표시
- `--reroll N` 옵션으로 독립 조회 가능

## 자동 실행 (macOS launchd)

30분마다 자동 실행:

### 1. plist 파일 생성

```bash
cat > ~/Library/LaunchAgents/com.tft.metawatcher.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tft.metawatcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/kakaogames/Desktop/work/project/lol_gg/tft_meta_watcher.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/kakaogames/Desktop/work/project/lol_gg</string>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>StandardOutPath</key>
    <string>/Users/kakaogames/Desktop/work/project/lol_gg/state/watcher.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/kakaogames/Desktop/work/project/lol_gg/state/watcher_error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF
```

### 2. 등록 및 시작

```bash
launchctl load ~/Library/LaunchAgents/com.tft.metawatcher.plist
launchctl list | grep tft
launchctl start com.tft.metawatcher
```

### 3. 해제

```bash
launchctl unload ~/Library/LaunchAgents/com.tft.metawatcher.plist
```

> Python 경로가 다르면 `which python3` 결과로 교체하세요.
