#!/usr/bin/env python3
"""TFT 메타 감시 Discord Bot — 슬래시 명령어로 덱 설정 변경"""

import json
import os
import logging
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ── Bot 설정 ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

CATEGORY_NAMES = {"main": "메인덱", "ad_alt": "AD 대체덱", "ap_alt": "AP 대체덱", "special": "특수 상황덱"}
DASHBOARD_URL = "https://kminto.github.io/tft-meta-watcher/"


@client.event
async def on_ready():
    await tree.sync()
    logger.info(f"봇 로그인: {client.user} | 슬래시 명령어 등록 완료")


# ── /덱목록 ───────────────────────────────────────────────────────────────────

@tree.command(name="덱목록", description="현재 감시 중인 덱 목록을 보여줍니다")
async def deck_list(interaction: discord.Interaction):
    config = load_config()
    decks = config.get("watched_decks", {})

    embed = discord.Embed(title="🎯 현재 감시 덱 목록", color=0x1E90FF)
    for cat, cat_name in CATEGORY_NAMES.items():
        entries = decks.get(cat, [])
        if entries:
            value = "\n".join(f"• **{e['label']}** (특성: {e['keywords'][0]}, 챔프: {e['keywords'][1] if len(e['keywords'])>1 else '-'})" for e in entries)
        else:
            value = "없음"
        embed.add_field(name=cat_name, value=value, inline=False)

    embed.set_footer(text="덱 변경: /메인덱변경, /AD덱변경, /특수덱변경, /덱추가, /덱삭제")
    await interaction.response.send_message(embed=embed)


# ── /메인덱변경 ───────────────────────────────────────────────────────────────

@tree.command(name="메인덱변경", description="메인덱을 변경합니다")
@app_commands.describe(
    특성="덱의 핵심 특성 (예: 별돌보미)",
    대표챔프="메인 캐리 챔프 이름 (예: 룰루)",
)
async def change_main(interaction: discord.Interaction, 특성: str, 대표챔프: str):
    config = load_config()
    label = f"{특성} {대표챔프}"
    config["watched_decks"]["main"] = [{"keywords": [특성, 대표챔프], "label": label}]
    save_config(config)

    embed = discord.Embed(
        title="✅ 메인덱 변경 완료",
        description=f"**{label}**\n특성: {특성} | 대표챔프: {대표챔프}\n\n다음 감시 때부터 적용됩니다.",
        color=0x00C853,
    )
    await interaction.response.send_message(embed=embed)
    logger.info(f"메인덱 변경: {label}")


# ── /AD덱변경 ─────────────────────────────────────────────────────────────────

@tree.command(name="ad덱변경", description="AD 대체덱을 변경합니다 (기존 AD덱 전부 교체)")
@app_commands.describe(
    특성="덱의 핵심 특성 (예: 전달자)",
    대표챔프="메인 캐리 챔프 이름 (예: 미스 포츈)",
)
async def change_ad(interaction: discord.Interaction, 특성: str, 대표챔프: str):
    config = load_config()
    label = f"{특성} {대표챔프}"
    config["watched_decks"]["ad_alt"] = [{"keywords": [특성, 대표챔프], "label": label}]
    save_config(config)

    embed = discord.Embed(
        title="✅ AD 대체덱 변경 완료",
        description=f"**{label}**\n특성: {특성} | 대표챔프: {대표챔프}\n\n기존 AD덱이 모두 교체됐어요.",
        color=0x00C853,
    )
    await interaction.response.send_message(embed=embed)


# ── /AP덱변경 ─────────────────────────────────────────────────────────────────

@tree.command(name="ap덱변경", description="AP 대체덱을 변경합니다")
@app_commands.describe(
    특성="덱의 핵심 특성 (예: 개화)",
    대표챔프="메인 캐리 챔프 이름 (예: 아리)",
)
async def change_ap(interaction: discord.Interaction, 특성: str, 대표챔프: str):
    config = load_config()
    label = f"{특성} {대표챔프}"
    config["watched_decks"]["ap_alt"] = [{"keywords": [특성, 대표챔프], "label": label}]
    save_config(config)

    embed = discord.Embed(
        title="✅ AP 대체덱 변경 완료",
        description=f"**{label}**\n특성: {특성} | 대표챔프: {대표챔프}\n\n기존 AP덱이 모두 교체됐어요.",
        color=0x00C853,
    )
    await interaction.response.send_message(embed=embed)


# ── /특수덱변경 ───────────────────────────────────────────────────────────────

@tree.command(name="특수덱변경", description="특수 상황덱을 변경합니다")
@app_commands.describe(
    특성="덱의 핵심 특성 (예: 시간 균열자)",
    대표챔프="메인 캐리 챔프 이름 (예: 이즈리얼)",
)
async def change_special(interaction: discord.Interaction, 특성: str, 대표챔프: str):
    config = load_config()
    label = f"{특성} {대표챔프}"
    config["watched_decks"]["special"] = [{"keywords": [특성, 대표챔프], "label": label}]
    save_config(config)

    embed = discord.Embed(
        title="✅ 특수덱 변경 완료",
        description=f"**{label}**\n특성: {특성} | 대표챔프: {대표챔프}",
        color=0x00C853,
    )
    await interaction.response.send_message(embed=embed)


# ── /덱추가 ───────────────────────────────────────────────────────────────────

@tree.command(name="덱추가", description="감시 덱을 추가합니다")
@app_commands.describe(
    카테고리="메인 / AD대체 / 특수",
    특성="덱의 핵심 특성 (예: 전달자)",
    대표챔프="메인 캐리 챔프 이름 (예: 조이)",
)
@app_commands.choices(카테고리=[
    app_commands.Choice(name="메인덱", value="main"),
    app_commands.Choice(name="AD 대체덱", value="ad_alt"),
    app_commands.Choice(name="AP 대체덱", value="ap_alt"),
    app_commands.Choice(name="특수 상황덱", value="special"),
])
async def add_deck(interaction: discord.Interaction, 카테고리: app_commands.Choice[str],
                   특성: str, 대표챔프: str):
    config = load_config()
    label = f"{특성} {대표챔프}"
    cat = 카테고리.value

    if cat not in config["watched_decks"]:
        config["watched_decks"][cat] = []

    for existing in config["watched_decks"][cat]:
        if existing["label"] == label:
            await interaction.response.send_message(f"⚠️ **{label}**은 이미 {카테고리.name}에 있어요.", ephemeral=True)
            return

    config["watched_decks"][cat].append({"keywords": [특성, 대표챔프], "label": label})
    save_config(config)

    embed = discord.Embed(
        title="✅ 덱 추가 완료",
        description=f"**{label}**을 {카테고리.name}에 추가했어요.\n특성: {특성} | 대표챔프: {대표챔프}",
        color=0x00C853,
    )
    await interaction.response.send_message(embed=embed)


# ── /덱삭제 ───────────────────────────────────────────────────────────────────

@tree.command(name="덱삭제", description="감시 덱을 삭제합니다")
@app_commands.describe(덱이름="삭제할 덱 이름")
async def remove_deck(interaction: discord.Interaction, 덱이름: str):
    config = load_config()
    found = False

    for cat in config["watched_decks"]:
        before = len(config["watched_decks"][cat])
        config["watched_decks"][cat] = [e for e in config["watched_decks"][cat] if e["label"] != 덱이름]
        if len(config["watched_decks"][cat]) < before:
            found = True

    if found:
        save_config(config)
        embed = discord.Embed(
            title="🗑️ 덱 삭제 완료",
            description=f"**{덱이름}**을 감시 목록에서 삭제했어요.",
            color=0xFF9800,
        )
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(f"⚠️ **{덱이름}**을 찾을 수 없어요.", ephemeral=True)


# ── /현황 ─────────────────────────────────────────────────────────────────────

@tree.command(name="현황", description="현재 메타 현황 대시보드 링크를 보여줍니다")
async def dashboard(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📱 TFT 메타 대시보드",
        description=f"[웹에서 보기]({DASHBOARD_URL})\n\n30분마다 자동 업데이트됩니다.",
        color=0x1E90FF,
    )
    await interaction.response.send_message(embed=embed)


# ── /도움말 ───────────────────────────────────────────────────────────────────

@tree.command(name="도움말", description="봇 사용법을 보여줍니다")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 TFT 메타 감시봇 사용법",
        color=0x1E90FF,
    )
    embed.add_field(
        name="덱 관리",
        value=(
            "**/덱목록** — 현재 감시 중인 덱 확인\n"
            "**/메인덱변경** — 메인덱 교체\n"
            "**/ad덱변경** — AD덱 교체\n"
            "**/특수덱변경** — 특수덱 교체\n"
            "**/덱추가** — 덱 추가\n"
            "**/덱삭제** — 덱 삭제"
        ),
        inline=False,
    )
    embed.add_field(
        name="입력 방법",
        value=(
            "**특성** = 롤체지지에 나오는 덱 특성 이름\n"
            "**대표챔프** = 메인 캐리 챔프 이름\n\n"
            "이 두 키워드로 롤체지지에서 덱을 찾아 매칭해요."
        ),
        inline=False,
    )
    embed.add_field(
        name="사용 예시",
        value=(
            "```\n"
            "/메인덱변경 특성:별돌보미 대표챔프:룰루\n"
            "/덱추가 카테고리:AD 대체덱 특성:전달자 대표챔프:조이\n"
            "/덱삭제 덱이름:전달자 조이\n"
            "```"
        ),
        inline=False,
    )
    embed.add_field(
        name="정보",
        value=(
            "**/현황** — 웹 대시보드 링크\n"
            "**/도움말** — 이 도움말"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


# ── 실행 ──────────────────────────────────────────────────────────────────────

def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")
        return
    client.run(token)


if __name__ == "__main__":
    main()
