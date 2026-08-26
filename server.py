#!/usr/bin/env python3
"""Render 배포용 통합 서버 — Discord 봇 + 메타 감시 + 헬스체크"""

import threading
import time
import os
import logging
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
WATCHER_INTERVAL = 30 * 60  # 30분


class HealthHandler(BaseHTTPRequestHandler):
    """헬스체크용 HTTP 핸들러"""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TFT Meta Watcher is running")

    def log_message(self, format, *args):
        pass  # 헬스체크 로그 숨김


def run_health_server():
    """Render가 서비스 살아있는지 확인하는 HTTP 서버"""
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"헬스체크 서버 시작 (포트 {port})")
    server.serve_forever()


def run_watcher_loop():
    """30분마다 메타 감시 실행"""
    time.sleep(10)  # 봇 시작 후 10초 대기
    while True:
        try:
            logger.info("메타 감시 실행 중...")
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "tft_meta_watcher.py")],
                cwd=str(SCRIPT_DIR),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("메타 감시 완료")
            else:
                logger.error(f"메타 감시 실패: {result.stderr[-300:]}")
        except Exception as e:
            logger.error(f"메타 감시 에러: {e}")

        time.sleep(WATCHER_INTERVAL)


def run_discord_bot():
    """Discord 봇 실행"""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.warning("DISCORD_BOT_TOKEN이 없어서 봇을 시작하지 않습니다.")
        return

    try:
        import discord_bot
        discord_bot.main()
    except Exception as e:
        logger.error(f"Discord 봇 에러: {e}")


if __name__ == "__main__":
    # 1. 헬스체크 서버 (Render용)
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    # 2. 메타 감시 루프 (30분마다)
    watcher_thread = threading.Thread(target=run_watcher_loop, daemon=True)
    watcher_thread.start()

    # 3. Discord 봇 (메인 스레드)
    logger.info("TFT 메타 감시 서버 시작")
    run_discord_bot()
