#!/bin/bash
# TFT 메타 감시봇 Oracle Cloud 서버 세팅 스크립트
# 사용법: ssh로 접속 후 이 스크립트 실행

set -e

echo "=========================================="
echo "  TFT 메타 감시봇 서버 세팅"
echo "=========================================="

# 1. 시스템 업데이트 + Python 설치
echo "[1/5] 시스템 업데이트..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git

# 2. 코드 클론
echo "[2/5] 코드 다운로드..."
cd ~
if [ -d "tft-meta-watcher" ]; then
    cd tft-meta-watcher && git pull
else
    git clone https://github.com/kminto/tft-meta-watcher.git
    cd tft-meta-watcher
fi

# 3. Python 환경 설정
echo "[3/5] Python 패키지 설치..."
python3 -m venv venv
source venv/bin/activate
pip install -q requests beautifulsoup4 python-dotenv discord.py

# 4. .env 설정
echo "[4/5] 환경변수 설정..."
if [ ! -f .env ]; then
    echo "DISCORD_WEBHOOK_URL과 DISCORD_BOT_TOKEN을 입력해주세요."
    read -p "DISCORD_WEBHOOK_URL: " WEBHOOK_URL
    read -p "DISCORD_BOT_TOKEN: " BOT_TOKEN
    cat > .env << EOF
DISCORD_WEBHOOK_URL=${WEBHOOK_URL}
DISCORD_BOT_TOKEN=${BOT_TOKEN}
EOF
    echo ".env 파일 생성 완료"
else
    echo ".env 파일이 이미 있습니다."
fi

# 5. systemd 서비스 등록
echo "[5/5] 서비스 등록..."

# Discord 봇 서비스
sudo tee /etc/systemd/system/tft-bot.service > /dev/null << EOF
[Unit]
Description=TFT Meta Watcher Discord Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/tft-meta-watcher
ExecStart=$HOME/tft-meta-watcher/venv/bin/python3 discord_bot.py
Restart=always
RestartSec=10
EnvironmentFile=$HOME/tft-meta-watcher/.env

[Install]
WantedBy=multi-user.target
EOF

# 메타 감시 타이머 (30분마다)
sudo tee /etc/systemd/system/tft-watcher.service > /dev/null << EOF
[Unit]
Description=TFT Meta Watcher Cron Job

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$HOME/tft-meta-watcher
ExecStart=$HOME/tft-meta-watcher/venv/bin/python3 tft_meta_watcher.py
EnvironmentFile=$HOME/tft-meta-watcher/.env
EOF

sudo tee /etc/systemd/system/tft-watcher.timer > /dev/null << EOF
[Unit]
Description=TFT Meta Watcher 30분 타이머

[Timer]
OnBootSec=1min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# 서비스 시작
sudo systemctl daemon-reload
sudo systemctl enable --now tft-bot.service
sudo systemctl enable --now tft-watcher.timer

echo ""
echo "=========================================="
echo "  세팅 완료!"
echo "=========================================="
echo ""
echo "  Discord 봇: 실행 중 (24시간)"
echo "  메타 감시:  30분마다 자동 실행"
echo ""
echo "  상태 확인:"
echo "    sudo systemctl status tft-bot"
echo "    sudo systemctl status tft-watcher.timer"
echo ""
echo "  로그 확인:"
echo "    sudo journalctl -u tft-bot -f"
echo "    sudo journalctl -u tft-watcher -f"
echo ""
echo "  봇 재시작:"
echo "    sudo systemctl restart tft-bot"
echo "=========================================="
