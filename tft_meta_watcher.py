#!/usr/bin/env python3
"""TFT 메타 감시 디스코드 알림봇 - 롤체지지 기반"""

import argparse
import json
import os
import re
import sys
import logging
from datetime import datetime
from pathlib import Path

from html import escape as html_escape

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── 설정 ──────────────────────────────────────────────────────────────────────

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "state" / "tft_meta_state.json"
HTML_OUTPUT = SCRIPT_DIR / "docs" / "index.html"
LOLCHESS_URL = "https://lolchess.gg/decks?hl=ko"
RIOT_PATCH_URL = "https://www.leagueoflegends.com/ko-kr/news/game-updates/"
RIOT_PATCH_SCHEDULE_URL = "https://support-leagueoflegends.riotgames.com/hc/ko/articles/360018987893"

# 패치 직후 집중 감시 시간 (초) - 패치 감지 후 6시간 동안 집중 모드
POST_PATCH_WATCH_DURATION = 6 * 60 * 60

# 알림 시간 설정 (KST 기준, 24시간제)
ALERT_HOUR_KST = 18  # 매일 오후 6시에 알림

# 감시 대상 덱 (config.json에서 로드, 디스코드 봇으로 변경 가능)
CONFIG_FILE = SCRIPT_DIR / "config.json"


def load_watched_decks() -> dict:
    """config.json에서 감시 덱 설정을 로드한다."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("watched_decks", {})
    # 폴백 기본값
    return {
        "main": [{"keywords": ["별돌보미", "룰루"], "label": "별돌보미 룰루"}],
        "ad_alt": [
            {"keywords": ["전달자", "미스 포츈"], "label": "전달자 미스 포츈"},
            {"keywords": ["운명술사", "코르키"], "label": "운명술사 코르키"},
        ],
        "special": [{"keywords": ["시간 균열자", "이즈리얼"], "label": "시간 균열자 이즈리얼"}],
    }


WATCHED_DECKS = load_watched_decks()

# ── 챔피언/아이템/시너지 데이터 (커뮤니티 드래곤에서 자동 로드) ──────────────────

COMMUNITY_DRAGON_URL = "https://raw.communitydragon.org/latest/cdragon/tft/ko_kr.json"

# 런타임에 채워지는 딕셔너리 (key → (한글이름, 코스트))
CHAMPION_DATA: dict[str, tuple[str, int]] = {}
ITEM_NAMES: dict[str, str] = {}
TRAIT_NAMES: dict[str, str] = {}

COST_EMOJI = {1: "⬜", 2: "🟩", 3: "🟦", 4: "🟪", 5: "🟨"}
TRAIT_STYLE = {1: "🥉", 2: "🥈", 3: "🥇", 4: "💠"}


def load_tft_data():
    """커뮤니티 드래곤에서 챔피언/아이템/시너지 데이터를 자동 로드한다."""
    global CHAMPION_DATA, ITEM_NAMES, TRAIT_NAMES
    try:
        resp = requests.get(COMMUNITY_DRAGON_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # 모든 세트의 챔프/시너지 로드 (세트간 챔프가 겹칠 수 있으므로 전부 등록)
        sets = data.get("sets", {})
        total_champs = 0
        total_traits = 0
        for set_num in sorted(sets.keys(), key=int):
            champs = sets[set_num].get("champions", [])
            for c in champs:
                api = c.get("apiName", "")
                cost = c.get("cost", 0)
                name = c.get("name", "")
                if api and name and 1 <= cost <= 5:
                    CHAMPION_DATA[api] = (name, cost)
                    total_champs += 1
            for t in sets[set_num].get("traits", []):
                api = t.get("apiName", "")
                name = t.get("name", "")
                if api and name:
                    TRAIT_NAMES[api] = name
                    total_traits += 1
        logger.info(f"챔프 {total_champs}개, 시너지 {total_traits}개 로드 완료")

        # 아이템 (전체)
        for item in data.get("items", []):
            api_name = item.get("apiName", "")
            name = item.get("name", "")
            if api_name and name and not api_name.startswith("TFT_Consumable"):
                ITEM_NAMES[api_name] = name

        logger.info(f"아이템 {len(ITEM_NAMES)}개 로드 완료")
    except Exception as e:
        logger.warning(f"커뮤니티 드래곤 데이터 로드 실패 (폴백 사용): {e}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── 데이터 수집 ──────────────────────────────────────────────────────────────

def fetch_lolchess_meta() -> dict:
    """롤체지지 메타 페이지에서 __NEXT_DATA__ JSON을 파싱한다."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    resp = requests.get(LOLCHESS_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag:
        raise RuntimeError("__NEXT_DATA__ 스크립트 태그를 찾을 수 없습니다.")

    next_data = json.loads(script_tag.string)
    page_props = next_data.get("props", {}).get("pageProps", {})

    # dehydratedState 구조 (React Query 캐시)
    dehydrated = page_props.get("dehydratedState", {})
    queries = dehydrated.get("queries", [])

    meta_deck_list = {}
    meta_decks = []

    # queries 배열에서 metaDeckList 찾기
    for query in queries:
        query_data = query.get("state", {}).get("data", {})
        if "metaDeckList" in query_data:
            meta_deck_list = query_data["metaDeckList"]
            meta_decks = meta_deck_list.get("metaDecks", [])
            break

    # 폴백: 이전 구조 (pageProps 직접)
    if not meta_decks:
        if "metaDeckList" in page_props:
            meta_deck_list = page_props["metaDeckList"]
            meta_decks = meta_deck_list.get("metaDecks", [])

    # 시즌 정보는 queries 데이터나 meta_deck_list에서
    season = ""
    for query in queries:
        query_data = query.get("state", {}).get("data", {})
        if "season" in query_data:
            season = query_data["season"]
            break

    # updatedAt이 epoch 밀리초이면 사람이 읽을 수 있는 형식으로 변환
    updated_at_raw = meta_deck_list.get("updatedAt", "")
    updated_at_display = str(updated_at_raw)
    try:
        ts = int(updated_at_raw)
        if ts > 1e12:  # 밀리초
            ts = ts / 1000
        updated_at_display = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        pass

    meta_info = {
        "season": season or meta_deck_list.get("season", ""),
        "patch": meta_deck_list.get("patch", ""),
        "updatedAt": updated_at_display,
        "updatedAtRaw": updated_at_raw,
        "queueId": meta_deck_list.get("queueId", ""),
        "tierId": meta_deck_list.get("tierId", ""),
        "dt": meta_deck_list.get("dt", ""),
        "shard": meta_deck_list.get("shard", ""),
    }

    return {"meta_info": meta_info, "decks": meta_decks, "raw_page_props_keys": list(page_props.keys())}


def fetch_riot_patch_schedule() -> list[dict]:
    """Riot 패치 일정 페이지에서 예정된 패치 날짜를 가져온다."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    try:
        resp = requests.get(RIOT_PATCH_SCHEDULE_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text()

        # 패치 일정 파싱 (예: "14.1", "1월 10일" 등)
        schedules = []

        # 날짜 패턴: "X월 X일" 또는 "20XX년 X월 X일"
        date_pattern = re.compile(r'(\d{1,2})월\s*(\d{1,2})일')
        patch_pattern = re.compile(r'패치\s*([\d.]+)')

        # 테이블이나 리스트에서 패치 일정 추출
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                row_text = " ".join(c.get_text(strip=True) for c in cells)
                patch_match = patch_pattern.search(row_text)
                date_match = date_pattern.search(row_text)
                if patch_match and date_match:
                    year = datetime.now().year
                    month = int(date_match.group(1))
                    day = int(date_match.group(2))
                    # 지난 달이면 내년으로 추정
                    if month < datetime.now().month - 1:
                        year += 1
                    schedules.append({
                        "patch": patch_match.group(1),
                        "date": f"{year}-{month:02d}-{day:02d}",
                        "raw": row_text.strip(),
                    })

        # 테이블이 없으면 본문 텍스트에서 추출
        if not schedules:
            lines = text.split("\n")
            for line in lines:
                patch_match = patch_pattern.search(line)
                date_match = date_pattern.search(line)
                if patch_match and date_match:
                    year = datetime.now().year
                    month = int(date_match.group(1))
                    day = int(date_match.group(2))
                    if month < datetime.now().month - 1:
                        year += 1
                    schedules.append({
                        "patch": patch_match.group(1),
                        "date": f"{year}-{month:02d}-{day:02d}",
                        "raw": line.strip()[:100],
                    })

        # B패치/핫픽스 키워드 감지
        hotfix_keywords = ["B패치", "b패치", "핫픽스", "hotfix", "긴급 패치", "마이크로패치"]
        for kw in hotfix_keywords:
            if kw.lower() in text.lower():
                # 해당 키워드 주변 텍스트 추출
                idx = text.lower().find(kw.lower())
                context = text[max(0, idx - 50):idx + 100].strip()
                date_match = date_pattern.search(context)
                if date_match:
                    year = datetime.now().year
                    month = int(date_match.group(1))
                    day = int(date_match.group(2))
                    schedules.append({
                        "patch": f"B패치/핫픽스",
                        "date": f"{year}-{month:02d}-{day:02d}",
                        "raw": context[:100],
                        "is_hotfix": True,
                    })

        return schedules
    except Exception as e:
        logger.warning(f"Riot 패치 일정 확인 실패: {e}")
        return []


def check_patch_day_alert(schedules: list[dict], prev_state: dict) -> list[dict]:
    """오늘이 패치일이거나 패치가 임박한 경우 알림을 생성한다."""
    alerts = []
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now().replace(hour=0, minute=0, second=0) +
                __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")

    already_notified = set(prev_state.get("notified_patch_dates", []))

    for sched in schedules:
        patch_date = sched.get("date", "")
        patch_label = sched.get("patch", "")
        is_hotfix = sched.get("is_hotfix", False)

        if patch_date == today and patch_date not in already_notified:
            emoji = "🔥" if is_hotfix else "🚨"
            alerts.append({
                "type": "patch_today",
                "title": f"{emoji} 오늘 패치 날!",
                "message": (
                    f"**패치 {patch_label}** 오늘({today}) 적용돼요.\n"
                    f"패치 후 메타 흔들릴 수 있으니 랭크 조심하세요.\n"
                    f"적용 후 데이터 리셋 + 집중 감시 자동 시작됩니다.\n"
                    f"상세: {sched.get('raw', '')}"
                ),
                "priority": "high",
            })
        elif patch_date == tomorrow and patch_date not in already_notified:
            alerts.append({
                "type": "patch_tomorrow",
                "title": "📅 내일 패치 있음",
                "message": (
                    f"**패치 {patch_label}** 내일({tomorrow}) 적용 예정이에요.\n"
                    f"오늘 안에 LP 정리하세요.\n"
                    f"상세: {sched.get('raw', '')}"
                ),
                "priority": "medium",
            })

    return alerts


def fetch_riot_patch_notes() -> list[dict]:
    """Riot 공식 TFT 패치노트 페이지에서 최신 글 목록을 가져온다."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    try:
        resp = requests.get(RIOT_PATCH_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        articles = []
        # Riot 뉴스 페이지 구조에서 기사 추출
        for article in soup.select("a[href*='patch-notes'], a[href*='patchnotes']"):
            title = article.get_text(strip=True)
            href = article.get("href", "")
            if title and href:
                if not href.startswith("http"):
                    href = "https://www.leagueoflegends.com" + href
                articles.append({"title": title, "url": href})

        # 다른 패턴도 시도
        if not articles:
            for article in soup.select("[class*='article'], [class*='card'], [class*='post']"):
                link = article.find("a")
                if link:
                    title = link.get_text(strip=True) or article.get_text(strip=True)[:100]
                    href = link.get("href", "")
                    if title and href:
                        if not href.startswith("http"):
                            href = "https://www.leagueoflegends.com" + href
                        articles.append({"title": title, "url": href})

        # 중복 제거
        seen = set()
        unique = []
        for a in articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                unique.append(a)

        return unique[:5]  # 최신 5개만
    except Exception as e:
        logger.warning(f"Riot 패치노트 확인 실패: {e}")
        return []


# ── 덱 매칭 ──────────────────────────────────────────────────────────────────

def get_deck_name(deck: dict) -> str:
    """덱 데이터에서 이름을 추출한다."""
    # 롤체지지 실제 필드: deckNameKo (한국어), deckNameEn (영어)
    for field in ["deckNameKo", "deckNameEn", "name", "deckName", "title", "deck_name"]:
        if field in deck and deck[field]:
            return deck[field]

    # deckChampionKey에서 추출
    if "deckChampionKey" in deck and deck["deckChampionKey"]:
        return deck["deckChampionKey"]

    # champions/units 목록에서 이름 생성
    units = deck.get("champions", deck.get("units", deck.get("characters", [])))
    if units:
        if isinstance(units[0], dict):
            names = [u.get("name", u.get("championName", "")) for u in units]
        else:
            names = [str(u) for u in units]
        return " / ".join(n for n in names if n)

    return str(deck.get("key", deck.get("deckKey", deck.get("id", "Unknown"))))


def deck_matches(deck: dict, watch_entry: dict) -> bool:
    """덱이 감시 대상과 매칭되는지 확인한다."""
    deck_name = get_deck_name(deck).lower()
    deck_str = json.dumps(deck, ensure_ascii=False).lower()

    for kw in watch_entry["keywords"]:
        kw_lower = kw.lower()
        if kw_lower not in deck_name and kw_lower not in deck_str:
            return False
    return True


def get_deck_stats(deck: dict) -> dict:
    """덱의 핵심 통계를 추출한다."""
    def safe_float(val, default=0.0):
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def safe_int(val, default=0):
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    # 롤체지지 실제 필드명: topRate, avgPlacement, winRate, plays, placements
    top4_rate = safe_float(
        deck.get("topRate", deck.get("top4Rate", deck.get("top4_rate", 0)))
    )
    avg_placement = safe_float(
        deck.get("avgPlacement", deck.get("avg_placement", deck.get("averagePlacement", 0)))
    )
    win_rate = safe_float(
        deck.get("winRate", deck.get("firstRate", deck.get("win_rate", 0)))
    )
    play_count = safe_int(
        deck.get("plays", deck.get("playCount", deck.get("play_count", deck.get("count", 0))))
    )
    # 1등 횟수: placements[0] 또는 firstCount
    placements = deck.get("placements", [])
    first_count = safe_int(placements[0]) if placements else safe_int(deck.get("firstCount", 0))

    # 비율이 0~1 사이면 백분율로 변환
    if 0 < top4_rate <= 1:
        top4_rate *= 100
    if 0 < win_rate <= 1:
        win_rate *= 100

    return {
        "name": get_deck_name(deck),
        "top4_rate": round(top4_rate, 2),
        "avg_placement": round(avg_placement, 2),
        "win_rate": round(win_rate, 2),
        "play_count": play_count,
        "first_count": first_count,
    }


# ── 리롤 확률 계산 ────────────────────────────────────────────────────────────

# TFT 세트14 기준 레벨별 챔프 코스트 등장 확률 (%)
# 키: 레벨, 값: [1코 확률, 2코, 3코, 4코, 5코]
REROLL_ODDS = {
    1:  [100,  0,   0,   0,   0],
    2:  [100,  0,   0,   0,   0],
    3:  [75,  25,   0,   0,   0],
    4:  [55,  30,  15,   0,   0],
    5:  [45,  33,  20,   2,   0],
    6:  [30,  40,  25,   5,   0],
    7:  [19,  35,  30,  15,   1],
    8:  [18,  25,  32,  22,   3],
    9:  [10,  20,  25,  30,  15],
    10: [ 5,  10,  20,  35,  30],
    11: [ 1,   2,  12,  50,  35],
}

# 코스트별 챔프 풀 크기 (세트14 기준 — 시즌마다 달라질 수 있음)
CHAMP_POOL_SIZE = {
    1: 22,  # 1코스트 고유 챔피언 수
    2: 20,
    3: 17,
    4: 10,
    5: 9,
}

# 코스트별 전체 풀 수량 (한 챔프당 복사 수)
COPIES_PER_CHAMP = {
    1: 22,
    2: 20,
    3: 17,
    4: 10,
    5: 9,
}


def calc_reroll_chance(level: int, target_cost: int, copies_remaining: int = None) -> dict:
    """특정 레벨에서 특정 코스트 챔프를 리롤로 뽑을 확률을 계산한다.

    Args:
        level: 현재 레벨 (1~11)
        target_cost: 원하는 챔프의 코스트 (1~5)
        copies_remaining: 풀에 남은 해당 챔프 복사 수 (None이면 최대값 사용)

    Returns:
        확률 정보 딕셔너리
    """
    if level not in REROLL_ODDS or target_cost < 1 or target_cost > 5:
        return {"error": "잘못된 입력"}

    cost_idx = target_cost - 1
    tier_chance = REROLL_ODDS[level][cost_idx] / 100  # 해당 코스트 티어가 뜰 확률

    pool_champs = CHAMP_POOL_SIZE.get(target_cost, 10)
    max_copies = COPIES_PER_CHAMP.get(target_cost, 10)

    if copies_remaining is None:
        copies_remaining = max_copies

    # 한 슬롯에서 원하는 챔프가 뜰 확률
    # = (해당 코스트 뜰 확률) × (해당 챔프 남은 수 / 해당 코스트 전체 남은 수)
    # 간략화: 전체 코스트 풀에서 다른 사람이 안 가져간 것으로 가정
    total_in_pool = pool_champs * max_copies  # 해당 코스트 전체
    single_slot_chance = tier_chance * (copies_remaining / total_in_pool) if total_in_pool > 0 else 0

    # 상점은 5슬롯 → 5번 독립 시행
    # 1 - (한 슬롯에서 안 뜰 확률)^5
    shop_chance = 1 - (1 - single_slot_chance) ** 5

    # N번 리롤 시 최소 1장 뜰 확률
    rolls_for_50 = 0
    rolls_for_80 = 0
    rolls_for_95 = 0
    if shop_chance > 0:
        import math
        for n in range(1, 200):
            cumulative = 1 - (1 - shop_chance) ** n
            if cumulative >= 0.50 and rolls_for_50 == 0:
                rolls_for_50 = n
            if cumulative >= 0.80 and rolls_for_80 == 0:
                rolls_for_80 = n
            if cumulative >= 0.95 and rolls_for_95 == 0:
                rolls_for_95 = n
                break

    return {
        "level": level,
        "target_cost": target_cost,
        "tier_chance_pct": round(tier_chance * 100, 1),
        "single_slot_pct": round(single_slot_chance * 100, 3),
        "shop_chance_pct": round(shop_chance * 100, 2),
        "rolls_for_50pct": rolls_for_50,
        "rolls_for_80pct": rolls_for_80,
        "rolls_for_95pct": rolls_for_95,
        "gold_for_50pct": rolls_for_50 * 2,
        "gold_for_80pct": rolls_for_80 * 2,
    }


def get_optimal_reroll_level(target_cost: int) -> dict:
    """특정 코스트 챔프를 리롤하기에 최적의 레벨을 계산한다."""
    best_level = 1
    best_chance = 0
    all_levels = {}

    for lvl in range(3, 10):
        result = calc_reroll_chance(lvl, target_cost)
        if "error" in result:
            continue
        all_levels[lvl] = result
        if result["shop_chance_pct"] > best_chance:
            best_chance = result["shop_chance_pct"]
            best_level = lvl

    return {
        "target_cost": target_cost,
        "optimal_level": best_level,
        "best_shop_chance": best_chance,
        "all_levels": all_levels,
    }


def build_reroll_info_for_deck(deck_stats: dict, deck_data: dict = None) -> str:
    """덱의 핵심 챔프 코스트에 맞는 리롤 가이드 문자열을 생성한다."""
    # 덱 데이터에서 핵심 유닛(캐리)의 코스트 추출 시도
    carry_costs = []
    if deck_data:
        units = deck_data.get("champions", deck_data.get("units", deck_data.get("characters", [])))
        for unit in units:
            if isinstance(unit, dict):
                cost = unit.get("cost", unit.get("tier", 0))
                is_carry = unit.get("isCarry", unit.get("is_carry", False))
                items = unit.get("items", [])
                if is_carry or len(items) >= 2:
                    carry_costs.append(int(cost))

    # 캐리 코스트를 못 찾으면 덱 이름에서 추정
    if not carry_costs:
        name = deck_stats.get("name", "")
        # 일반적으로 리롤덱은 1~3코, 레벨덱은 4~5코
        # 기본값: 3코스트 (가장 흔한 리롤 타겟)
        carry_costs = [3]

    lines = []
    for cost in sorted(set(carry_costs)):
        optimal = get_optimal_reroll_level(cost)
        opt_lvl = optimal["optimal_level"]
        info = optimal["all_levels"].get(opt_lvl, {})

        lines.append(
            f"{cost}코 캐리 → 레벨 {opt_lvl} 리롤 최적 "
            f"(상점 확률: {info.get('shop_chance_pct', 0):.1f}% | "
            f"50%까지 {info.get('rolls_for_50pct', '?')}롤/{info.get('gold_for_50pct', '?')}골드)"
        )

    return "\n".join(lines) if lines else ""


def _find_deck_url(deck_name: str, decks: list[dict]) -> str:
    """덱 이름으로 raw deck을 찾아 롤체지지 URL을 반환한다."""
    for d in decks:
        if get_deck_name(d) == deck_name:
            key = d.get("key", d.get("deckKey", ""))
            if key:
                return f"https://lolchess.gg/decks/{key}?hl=ko"
    return "https://lolchess.gg/decks?hl=ko"


# ── 분석 & 알림 ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    """이전 실행 상태를 로드한다."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    """현재 상태를 저장한다."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def analyze_changes(current_data: dict, prev_state: dict) -> list[dict]:
    """현재 데이터와 이전 상태를 비교하여 알림 목록을 생성한다."""
    # 첫 실행이면 비교 대상이 없으므로 알림 없이 리턴 (요약만 전송)
    if not prev_state:
        return []

    alerts = []
    meta_info = current_data["meta_info"]
    decks = current_data["decks"]
    prev_meta = prev_state.get("meta_info", {})
    prev_decks_stats = prev_state.get("decks_stats", {})

    # 1. 패치 변경 확인
    if prev_meta.get("patch") and meta_info["patch"] != prev_meta["patch"]:
        alerts.append({
            "type": "patch_change",
            "title": "🚨 새 패치 적용됨 — 집중 감시 시작",
            "message": (
                f"**{prev_meta['patch']}** → **{meta_info['patch']}**\n"
                f"패치 직후라 메타가 흔들릴 수 있어요. 6시간 동안 집중 감시합니다.\n"
                f"덱 데이터가 리셋돼서 이전 수치랑 비교가 안 됩니다.\n"
                f"⚠️ 표본이 적을 수 있으니 참고만 하세요."
            ),
            "priority": "high",
        })

    # 2. 업데이트 시간 변경 — 로그만 남기고 별도 알림은 보내지 않음
    # (매 30분~1시간마다 갱신되므로 알림이 너무 잦아짐)

    # 현재 덱 통계 수집
    all_stats = []
    watched_stats = {cat: {} for cat in WATCHED_DECKS}

    for deck in decks:
        stats = get_deck_stats(deck)
        all_stats.append(stats)

        # 감시 대상 매칭
        for category, entries in WATCHED_DECKS.items():
            for entry in entries:
                if deck_matches(deck, entry):
                    watched_stats[category][entry["label"]] = stats

    # 순방률/평균등수 기준 정렬
    by_top4 = sorted(all_stats, key=lambda x: x["top4_rate"], reverse=True)
    by_avg = sorted([s for s in all_stats if s["avg_placement"] > 0],
                    key=lambda x: x["avg_placement"])

    # 3. 감시 덱 전체 하락 체크 + 1위 덱 추천
    top1 = by_top4[0] if by_top4 else None
    top1_recommend = ""
    if top1:
        top1_recommend = (
            f"\n\n🏆 **현재 1위 덱: {top1['name']}**\n"
            f"순방률 {top1['top4_rate']:.1f}% | 평균등수 {top1['avg_placement']:.2f}등 | {top1['play_count']:,}판"
        )

    for category in WATCHED_DECKS:
        for label, stats in watched_stats.get(category, {}).items():
            prev = prev_decks_stats.get(label, {})
            if not prev:
                continue

            top4_diff = stats["top4_rate"] - prev.get("top4_rate", 0)
            avg_diff = stats["avg_placement"] - prev.get("avg_placement", 0)
            cat_name = {"main": "메인덱", "ad_alt": "AD덱", "ap_alt": "AP덱", "special": "특수덱"}.get(category, "")

            if top4_diff <= -2.0:
                alerts.append({
                    "type": "main_deck_warning",
                    "title": f"⚠️ {cat_name} 순방률 떨어짐",
                    "message": (
                        f"**{label}** 순방률이 {abs(top4_diff):.2f}%p 빠졌어요\n"
                        f"{prev.get('top4_rate', 0):.2f}% → **{stats['top4_rate']:.2f}%**\n"
                        f"평균등수: {stats['avg_placement']:.2f}"
                        + top1_recommend
                    ),
                    "priority": "high",
                })

            if avg_diff >= 0.15:
                alerts.append({
                    "type": "main_deck_warning",
                    "title": f"⚠️ {cat_name} 등수 밀림",
                    "message": (
                        f"**{label}** 평균등수가 {avg_diff:.2f} 밀렸어요\n"
                        f"{prev.get('avg_placement', 0):.2f}등 → **{stats['avg_placement']:.2f}등**\n"
                        f"순방률: {stats['top4_rate']:.2f}%"
                        + top1_recommend
                    ),
                    "priority": "high",
                })

    # 4. 내 메인덱보다 강한 덱 나오면 알림
    main_stats_list = list(watched_stats.get("main", {}).values())
    if main_stats_list:
        best_main_top4 = max(s["top4_rate"] for s in main_stats_list)
        best_main_avg = min(s["avg_placement"] for s in main_stats_list if s["avg_placement"] > 0) if any(s["avg_placement"] > 0 for s in main_stats_list) else 99

        # 이전에 알림 보낸 덱은 제외
        prev_notified = set(prev_state.get("notified_better_decks", []))

        for s in all_stats:
            # 내 감시 덱이면 스킵
            is_watched = False
            for cat_stats in watched_stats.values():
                if s["name"] in cat_stats:
                    is_watched = True
                    break
            if is_watched:
                continue

            # 순방률과 평균등수 모두 내 메인덱보다 좋고, 표본 충분한 경우
            if (s["top4_rate"] > best_main_top4 + 1.0
                    and 0 < s["avg_placement"] < best_main_avg
                    and s["play_count"] >= 3000
                    and s["name"] not in prev_notified):
                alerts.append({
                    "type": "better_deck_found",
                    "title": "🔥 내 메인덱보다 좋은 덱 나옴",
                    "message": (
                        f"**{s['name']}** — 내 메인덱보다 성적이 좋아요\n"
                        f"순방률 **{s['top4_rate']:.1f}%** (내 메인: {best_main_top4:.1f}%)\n"
                        f"평균등수 **{s['avg_placement']:.2f}등** (내 메인: {best_main_avg:.2f}등)\n"
                        f"판수: {s['play_count']:,}판"
                    ),
                    "priority": "medium",
                })

    # 9. 감시 대상 덱 목록에서 사라진 경우
    watched_labels = set()
    for entries in WATCHED_DECKS.values():
        for e in entries:
            watched_labels.add(e["label"])

    for label in watched_labels:
        if label in prev_decks_stats and label not in {s["name"] for s in all_stats}:
            # 키워드 재매칭으로도 못 찾으면 사라진 것
            found = False
            for category, entries in WATCHED_DECKS.items():
                for entry in entries:
                    if entry["label"] == label:
                        for deck in decks:
                            if deck_matches(deck, entry):
                                found = True
                                break
            if not found:
                alerts.append({
                    "type": "deck_disappeared",
                    "title": "❌ 감시 덱이 메타에서 빠짐",
                    "message": f"**{label}**이 롤체지지 메타 목록에서 사라졌어요.\n티어가 많이 떨어졌을 수 있으니 대체덱을 준비하세요.",
                    "priority": "high",
                })

    # 10. 평균등수 4.15 이하 신규 덱 — 요약 리포트에서 확인 가능하므로 별도 알림 안 함

    return alerts


# ── 추천 덱 알고리즘 ──────────────────────────────────────────────────────────

def find_strongest_deck(decks_raw: list[dict]) -> dict | None:
    """모두가 인정하는 1위 덱. 표본 5000판 이상 중 순방률 1위."""
    if not decks_raw:
        return None
    candidates = []
    for d in decks_raw:
        stats = get_deck_stats(d)
        if stats["play_count"] >= 5000 and stats["avg_placement"] > 0:
            candidates.append((stats["top4_rate"], d))
    if not candidates:
        for d in decks_raw:
            stats = get_deck_stats(d)
            if stats["play_count"] >= 1000 and stats["avg_placement"] > 0:
                candidates.append((stats["top4_rate"], d))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def find_hidden_op_deck(decks_raw: list[dict], strongest: dict = None) -> dict | None:
    """남들이 안 쓰는데 4등 안에 꾸준히 드는 덱.
    핵심: 평균등수 4.0 이하(= 매판 4등 안) + 픽률 낮음(= 안 겹힘) + 표본 충분.
    최강 덱과 다른 덱을 추천."""
    if not decks_raw:
        return None
    strongest_key = strongest.get("key", "") if strongest else ""
    candidates = []
    for d in decks_raw:
        if d.get("key", "") == strongest_key:
            continue
        stats = get_deck_stats(d)
        pick = d.get("pickRate", 0)
        if stats["play_count"] < 2000 or stats["avg_placement"] > 4.0 or stats["avg_placement"] <= 0:
            continue
        if pick > 2.0:
            continue
        # 점수: 평균등수 좋을수록 + 픽률 낮을수록
        score = (10 - stats["avg_placement"]) * 10 + stats["top4_rate"] * 0.5 - pick * 20
        candidates.append((score, d))
    if not candidates:
        # 조건 완화
        for d in decks_raw:
            if d.get("key", "") == strongest_key:
                continue
            stats = get_deck_stats(d)
            pick = d.get("pickRate", 0)
            if stats["play_count"] < 1000 or stats["avg_placement"] > 4.2 or stats["avg_placement"] <= 0:
                continue
            if pick > 3.0:
                continue
            score = (10 - stats["avg_placement"]) * 10 + stats["top4_rate"] * 0.5 - pick * 20
            candidates.append((score, d))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ── 덱 상세 임베드 ───────────────────────────────────────────────────────────

# 챔프 영문 → (한글, 코스트) 폴백 매핑 (커뮤니티 드래곤에 없는 챔프용)
_CHAMP_KO_FALLBACK = {
    # 1코스트
    "Elise": ("엘리스", 1), "Kennen": ("케넨", 1), "Leona": ("레오나", 1),
    "Lillia": ("릴리아", 1), "Soraka": ("소라카", 1), "Teemo": ("티모", 1),
    "Warwick": ("워윅", 1), "Zyra": ("자이라", 1), "Kobuko": ("코부코", 1),
    # 2코스트
    "Cassiopeia": ("카시오페아", 2), "Ashe": ("애쉬", 2),
    "Maokai": ("마오카이", 2), "Sett": ("세트", 2), "Sivir": ("시비르", 2),
    "Tristana": ("트리스타나", 2), "Veigar": ("베이가", 2), "Rakan": ("라칸", 2),
    "Shen": ("쉔", 2), "Sejuani": ("세주아니", 2), "GnarSmall": ("꼬마 나르", 2),
    "Gnar Small": ("꼬마 나르", 2),
    # 3코스트
    "Ahri": ("아리", 3), "Caitlyn": ("케이틀린", 3), "Diana": ("다이애나", 3),
    "Draven": ("드레이븐", 3), "Ezreal": ("이즈리얼", 3), "Hecarim": ("헤카림", 3),
    "Ivern": ("아이번", 3), "Karma": ("카르마", 3), "Morgana": ("모르가나", 3),
    "Rammus": ("람머스", 3), "RekSai": ("렉사이", 3), "Rengar": ("렝가", 3),
    "Varus": ("바루스", 3), "Yunara": ("유나라", 3),
    # 4코스트
    "Alistar": ("알리스타", 4), "Aphelios": ("아펠리오스", 4), "Azir": ("아지르", 4),
    "Kayle": ("케일", 4), "LeBlanc": ("르블랑", 4), "Le Blanc": ("르블랑", 4),
    "Malphite": ("말파이트", 4), "Ornn": ("오른", 4), "Taric": ("타릭", 4),
    "Xayah": ("자야", 4), "Yorick": ("요릭", 4),
    # 5코스트
    "Alune": ("알룬", 5), "Amumu": ("아무무", 5), "Fiddlesticks": ("피들스틱", 5),
    "MasterYi": ("마스터 이", 5), "Nidalee": ("니달리", 5), "Vi": ("바이", 5),
    "ElderDragon": ("장로 드래곤", 5), "Elder Dragon": ("장로 드래곤", 5),
    # 특수
    "KogMaw": ("코그모", 3), "Sentinel": ("감시자", 1), "Sentry": ("파수꾼", 1),
    "Brambleback": ("덤불등", 0), "CrimsonRaptor": ("진홍 칼부리", 0),
    "Gromp": ("두꺼비", 0), "Krug": ("돌거북", 0), "Murkwolf": ("큰 늑대", 0),
    "Scuttlecrab": ("바위게", 0),
}


def _normalize_champ_key(key: str) -> str:
    """DA_18_Sivir, DA_Draven18 등 다양한 키 형식에서 챔프 이름을 추출한다."""
    name = key
    # DA_ 접두사 제거
    name = re.sub(r'^DA_', '', name)
    # 18_ 접두사 또는 18 접미사 제거
    name = re.sub(r'^\d+_', '', name)
    name = re.sub(r'\d+$', '', name)
    # _AD, _AP 접미사 제거
    name = re.sub(r'_(AD|AP)$', '', name)
    return name.strip('_')


def _champ_name(key: str) -> str:
    """챔피언 API key를 한글 이름으로 변환한다."""
    if key in CHAMPION_DATA:
        return CHAMPION_DATA[key][0]
    # 키 정규화 후 다시 검색
    norm = _normalize_champ_key(key)
    for api, (name, cost) in CHAMPION_DATA.items():
        api_norm = _normalize_champ_key(api)
        if api_norm.lower() == norm.lower():
            return name
    # 폴백 매핑
    if norm in _CHAMP_KO_FALLBACK:
        val = _CHAMP_KO_FALLBACK[norm]
        return val[0] if isinstance(val, tuple) else val
    # CamelCase 분리 후 폴백
    split_name = re.sub(r'([a-z])([A-Z])', r'\1 \2', norm)
    for en, val in _CHAMP_KO_FALLBACK.items():
        if en.lower() == split_name.lower() or en.lower() == norm.lower():
            return val[0] if isinstance(val, tuple) else val
    return split_name


def _champ_cost(key: str) -> int:
    """챔피언 API key에서 코스트를 반환한다."""
    if key in CHAMPION_DATA:
        return CHAMPION_DATA[key][1]
    # 키 정규화 후 검색
    norm = _normalize_champ_key(key)
    for api, (name, cost) in CHAMPION_DATA.items():
        api_norm = _normalize_champ_key(api)
        if api_norm.lower() == norm.lower():
            return cost
    # 폴백 매핑에서 코스트 가져오기
    if norm in _CHAMP_KO_FALLBACK:
        val = _CHAMP_KO_FALLBACK[norm]
        return val[1] if isinstance(val, tuple) else 0
    split_name = re.sub(r'([a-z])([A-Z])', r'\1 \2', norm)
    for en, val in _CHAMP_KO_FALLBACK.items():
        if en.lower() == split_name.lower() or en.lower() == norm.lower():
            return val[1] if isinstance(val, tuple) else 0
    return 0


def _item_name(key: str) -> str:
    """아이템 API key를 한글 이름으로 변환한다."""
    if key in ITEM_NAMES:
        return ITEM_NAMES[key]
    # 언더스코어/공백 변형 시도
    normalized = key.replace(" ", "").replace("_", "")
    for k, v in ITEM_NAMES.items():
        if k.replace("_", "") == normalized:
            return v
    # 매핑에 없으면 key에서 추출
    name = key.replace("TFT_Item_", "").replace("TFT17_Item_", "")
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    return name


def _trait_name(key: str) -> str:
    """시너지 API key를 한글 이름으로 변환한다."""
    if key in TRAIT_NAMES:
        return TRAIT_NAMES[key]
    name = key.split("_", 1)[-1] if "_" in key else key
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    return name


def build_deck_detail_embed(deck_raw: dict) -> dict:
    """덱의 챔프 구성, 아이템, 시너지를 보기 좋게 임베드로 만든다."""
    deck_name = deck_raw.get("deckNameKo", deck_raw.get("deckNameEn", ""))
    deck_obj = deck_raw.get("deck", {})
    champions = deck_obj.get("champions", [])
    traits = deck_obj.get("traits", [])

    # ── 챔프를 코어순 → 코스트순으로 정렬 ──
    sorted_champs = sorted(champions, key=lambda c: (c.get("coreRank", 99), -_champ_cost(c.get("key", ""))))

    # 캐리 (coreRank 1~4, 아이템 있는 챔프)
    carry_lines = []
    support_names = []

    for champ in sorted_champs:
        key = champ.get("key", "")
        name = _champ_name(key)
        cost = _champ_cost(key)
        cost_emoji = COST_EMOJI.get(cost, "▪️")
        items = champ.get("items", [])
        core_rank = champ.get("coreRank", 99)

        if items and core_rank <= 10:
            # 캐리 챔프 — 아이템 표시
            item_str = " + ".join(_item_name(i) for i in items)
            star = "⭐" if core_rank <= 2 else ""
            carry_lines.append(
                f"{cost_emoji} **{name}** ({cost}코) {star}\n"
                f"  └ {item_str}"
            )
        else:
            support_names.append(f"{cost_emoji}{name}")

    # ── 시너지 정렬 (높은 등급 먼저) ──
    sorted_traits = sorted(traits, key=lambda t: -t.get("style", 0))
    trait_lines = []
    for trait in sorted_traits:
        style_emoji = TRAIT_STYLE.get(trait.get("style", 1), "▪️")
        name = _trait_name(trait.get("key", ""))
        num = trait.get("numUnits", 0)
        trait_lines.append(f"{style_emoji} {name} ({num})")

    # ── 운영 가이드 ──
    # 캐리 코스트 기반 리롤 타이밍
    carry_costs = []
    for champ in sorted_champs:
        if champ.get("coreRank", 99) <= 4 and champ.get("items"):
            carry_costs.append(_champ_cost(champ.get("key", "")))

    reroll_guide = ""
    carry_costs = [c for c in carry_costs if 1 <= c <= 5]
    if carry_costs:
        main_cost = max(set(carry_costs), key=carry_costs.count)  # 가장 많은 코스트
        opt = get_optimal_reroll_level(main_cost)
        lvl = opt["optimal_level"]
        info = opt["all_levels"].get(lvl, {})
        reroll_guide = (
            f"**리롤 타이밍**: 레벨 {lvl}에서 {main_cost}코 캐리 리롤\n"
            f"상점 확률 {info.get('shop_chance_pct', 0):.1f}% | "
            f"50%: {info.get('rolls_for_50pct', '?')}롤({info.get('gold_for_50pct', '?')}G)"
        )

    # ── 임베드 구성 ──
    stats = get_deck_stats(deck_raw)
    verdict_bar = _verdict_bar(stats["top4_rate"])
    verdict = _verdict_label(stats["top4_rate"])

    fields = []

    # 캐리 챔프 + 아이템
    if carry_lines:
        fields.append({
            "name": "🗡️ 캐리 & 아이템",
            "value": "\n".join(carry_lines),
            "inline": False,
        })

    # 서포트 챔프
    if support_names:
        fields.append({
            "name": "👥 서포트",
            "value": " ".join(support_names),
            "inline": True,
        })

    # 시너지
    if trait_lines:
        fields.append({
            "name": "🔗 시너지",
            "value": "\n".join(trait_lines),
            "inline": True,
        })

    # 성적
    fields.append({
        "name": "📊 성적",
        "value": (
            f"```\n"
            f"순방률   {verdict_bar} {stats['top4_rate']:.1f}%\n"
            f"평균등수  {stats['avg_placement']:.2f}등\n"
            f"승률     {stats['win_rate']:.1f}%\n"
            f"판수     {stats['play_count']:,}판\n"
            f"```\n"
            f"판정: {verdict}"
        ),
        "inline": False,
    })

    # 리롤 가이드
    if reroll_guide:
        fields.append({
            "name": "🎰 운영 가이드",
            "value": reroll_guide,
            "inline": False,
        })

    # 코스트별 색상
    main_carry_cost = carry_costs[0] if carry_costs else 3
    color_map = {1: 0x9E9E9E, 2: 0x4CAF50, 3: 0x2196F3, 4: 0x9C27B0, 5: 0xFFC107}

    return {
        "title": f"📖 {deck_name} 상세 가이드",
        "color": color_map.get(main_carry_cost, 0x1E90FF),
        "fields": fields,
        "footer": {"text": f"롤체지지 기준 | {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
    }


# ── HTML 대시보드 생성 ────────────────────────────────────────────────────────

def generate_html_report(meta_info, all_stats, watched_stats, decks_raw):
    """TFT 메타 대시보드 HTML 페이지를 생성한다."""
    esc = html_escape
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    patch = esc(str(meta_info.get("patch", "N/A")))
    updated_at = esc(str(meta_info.get("updatedAt", "N/A")))
    total_decks = len(all_stats)

    cost_class = {1: "cost-1", 2: "cost-2", 3: "cost-3", 4: "cost-4", 5: "cost-5"}
    trait_style_label = {1: "🥉", 2: "🥈", 3: "🥇", 4: "💠"}
    cat_label = {"main": "메인", "ad_alt": "AD대체", "ap_alt": "AP대체", "special": "특수"}
    cat_cls = {"main": "badge-main", "ad_alt": "badge-ad", "ap_alt": "badge-ap", "special": "badge-special"}

    def vc(rate):
        if rate >= 58: return "verdict-strong"
        if rate >= 55: return "verdict-good"
        if rate >= 52: return "verdict-normal"
        if rate >= 50: return "verdict-caution"
        if rate >= 47: return "verdict-weak"
        return "verdict-replace"

    def watched_html():
        cards = []
        for cat in WATCHED_DECKS:
            for label, stats in watched_stats.get(cat, {}).items():
                t4 = stats["top4_rate"]; avg = stats["avg_placement"]
                url = "https://lolchess.gg/decks?hl=ko"
                for entry in WATCHED_DECKS.get(cat, []):
                    if entry["label"] == label:
                        for d in decks_raw:
                            if deck_matches(d, entry): url = _deck_url(d); break
                        break
                cards.append(f'''<div class="deck-card"><div class="deck-card-header"><span class="deck-name">{esc(label)}</span><span class="cat-badge {cat_cls.get(cat,"")}">{cat_label.get(cat,"")}</span></div><div class="gauge-wrap"><div class="gauge-bar"><div class="gauge-fill {vc(t4)}-fill" style="width:{min(100,t4):.0f}%"></div></div><span class="gauge-label">{t4:.1f}%</span></div><div class="deck-stats"><div class="stat-item"><span class="stat-label">순방률</span><span class="stat-val">{t4:.1f}%</span></div><div class="stat-item"><span class="stat-label">평균등수</span><span class="stat-val">{avg:.2f}</span></div><div class="stat-item"><span class="stat-label">승률</span><span class="stat-val">{stats["win_rate"]:.1f}%</span></div><div class="stat-item"><span class="stat-label">판수</span><span class="stat-val">{stats["play_count"]:,}</span></div></div><div class="verdict-row"><span class="verdict-badge {vc(t4)}">{esc(_verdict_label(t4))}</span><a class="deck-link" href="{esc(url)}" target="_blank">롤체지지 보기 →</a></div></div>''')
        return "\n".join(cards) if cards else '<p class="no-data">감시 덱 없음</p>'

    def deck_detail_card(raw_deck, label):
        """덱 상세 카드 HTML을 생성한다 (챔피언 구성, 아이템, 시너지, 리롤/운영 가이드)."""
        champs = sorted(raw_deck.get("deck", {}).get("champions", []), key=lambda c: (c.get("coreRank", 99), -_champ_cost(c.get("key", ""))))
        traits = sorted(raw_deck.get("deck", {}).get("traits", []), key=lambda t: -t.get("style", 0))
        ch_rows = []; carry_costs = []
        for c in champs:
            k = c.get("key", ""); nm = _champ_name(k); co = _champ_cost(k); cc = cost_class.get(co, "cost-1"); cr = c.get("coreRank", 99); items = c.get("items", [])
            if co == 0:
                continue  # 소환수/몬스터는 표시하지 않음
            if cr <= 4 and 1 <= co <= 5:
                carry_costs.append(co)
            star = "⭐ " if cr <= 2 else ""
            itm = "".join(f'<span class="item-badge">{esc(_item_name(i))}</span>' for i in items) if items else ""
            ch_rows.append(f'<div class="champ-row{"  carry-row" if cr<=4 else ""}"><span class="champ-cost-dot {cc}"></span><span class="champ-name">{star}{esc(nm)}</span><span class="champ-cost-label">{co}코</span>{f"<div class=item-row>{itm}</div>" if itm else ""}</div>')
        tr_rows = [f'<span class="trait-badge style-{t.get("style",1)}">{trait_style_label.get(t.get("style",1),"")} {esc(_trait_name(t.get("key","")))} {t.get("numUnits",0)}</span>' for t in traits]
        rr = ""
        vc2 = [c for c in carry_costs if 1 <= c <= 5]
        if vc2:
            mc = max(set(vc2), key=vc2.count); o = get_optimal_reroll_level(mc); lv = o["optimal_level"]; inf = o["all_levels"].get(lv, {})
            rr = f'<div class="reroll-guide">🎰 <strong>리롤:</strong> Lv.{lv}에서 {mc}코 캐리 — 상점 {inf.get("shop_chance_pct",0):.1f}% | 50%: {inf.get("rolls_for_50pct","?")}롤({inf.get("gold_for_50pct","?")}G)</div>'
        # 운영 가이드 자동 생성 (캐리/탱커를 코스트별로 분류)
        op_guide = ""
        if vc2:
            mc = max(set(vc2), key=vc2.count)
            carries_high = []
            carries_low = []
            tanks_high = []
            tanks_low = []
            early_units = []

            for c in champs:
                cr = c.get("coreRank", 99)
                k = c.get("key", "")
                nm = _champ_name(k)
                co = _champ_cost(k)
                has_items = bool(c.get("items"))

                if cr <= 2:
                    if co >= 4: carries_high.append(f"{nm}({co}코)")
                    else: carries_low.append(f"{nm}({co}코)")
                elif cr <= 4 and has_items:
                    if co >= 4: tanks_high.append(f"{nm}({co}코)")
                    else: tanks_low.append(f"{nm}({co}코)")

                if co <= 2:
                    early_units.append(nm)

            carry_all = ", ".join(carries_low + carries_high) if (carries_low or carries_high) else "메인 캐리"
            early_str = ", ".join(early_units[:4]) if early_units else "1~2코 유닛"

            if mc <= 2:
                carry_str = ", ".join(carries_low) if carries_low else carry_all
                tank_str = ", ".join(tanks_low) if tanks_low else "저코 탱커"
                op_guide = f'''
            <div class="op-guide">
              <h4 class="col-title">운영 가이드</h4>
              <div class="op-phase">
                <div class="phase-badge phase-early">초반 (1~3스테이지)</div>
                <div class="phase-text">
                  <strong>{carry_str}</strong> 뜨면 무조건 잡아두기 (벤치에 모아놓기)<br>
                  아이템 재료 수급 — 캐리 아이템 재료 우선<br>
                  레벨 올리지 말고 돈 모으기, 이자 10골 이상 유지
                </div>
              </div>
              <div class="op-phase">
                <div class="phase-badge phase-mid">중반 (3-2 ~ 4-1)</div>
                <div class="phase-text">
                  <strong>레벨 {max(5, mc+3)}</strong>에서 멈추고 올인 리롤<br>
                  <strong>{carry_str}</strong> 3성이 최우선 목표<br>
                  50골 이상이면 리롤 시작, 30골 밑으로 떨어지면 멈추기
                </div>
              </div>
              <div class="op-phase">
                <div class="phase-badge phase-late">후반 (5스테이지~)</div>
                <div class="phase-text">
                  캐리 3성 완성 → 레벨 올려서 고코 유닛 추가<br>
                  못 만들었으면 남은 골드로 리롤 한번 더 → 레벨업<br>
                  포지셔닝: 캐리를 어쌔신 반대편에 배치
                </div>
              </div>
            </div>'''
            elif mc == 3:
                carry_str = ", ".join(carries_low) if carries_low else carry_all
                opt = get_optimal_reroll_level(3)
                rlvl = opt["optimal_level"]
                op_guide = f'''
            <div class="op-guide">
              <h4 class="col-title">운영 가이드</h4>
              <div class="op-phase">
                <div class="phase-badge phase-early">초반 (1~3스테이지)</div>
                <div class="phase-text">
                  <strong>{early_str}</strong> 등 저코 유닛으로 보드 채우기<br>
                  <strong>{carry_str}</strong> 뜨면 벤치에 모아두기<br>
                  아이템 재료 수급 (캐리 아이템 → 탱커 아이템 순서)
                </div>
              </div>
              <div class="op-phase">
                <div class="phase-badge phase-mid">중반 (3-2 ~ 4-5)</div>
                <div class="phase-text">
                  <strong>레벨 {rlvl}</strong>에서 리롤 (3코 확률 최고 구간)<br>
                  <strong>{carry_str}</strong> 2성 필수, 여유되면 3성 도전<br>
                  체력 50 이하면 바로 리롤, 여유 있으면 이자 태우면서 슬로우롤
                </div>
              </div>
              <div class="op-phase">
                <div class="phase-badge phase-late">후반 (5스테이지~)</div>
                <div class="phase-text">
                  캐리 2성 완성 후 레벨 8~9로 올려서 고코 유닛 추가<br>
                  {", ".join(carries_high + tanks_high) if (carries_high or tanks_high) else "4~5코 유닛"} 넣어서 보드 완성<br>
                  상대 조합 보고 포지셔닝 조정
                </div>
              </div>
            </div>'''
            else:
                carry_str = ", ".join(carries_high) if carries_high else carry_all
                tank_str = ", ".join(tanks_high + tanks_low) if (tanks_high or tanks_low) else "탱커"
                op_guide = f'''
            <div class="op-guide">
              <h4 class="col-title">운영 가이드</h4>
              <div class="op-phase">
                <div class="phase-badge phase-early">초반 (1~3스테이지)</div>
                <div class="phase-text">
                  <strong>{early_str}</strong> 등 저코 유닛 2성으로 연승 노리기<br>
                  <strong>{carry_str}</strong>은 아직 안 나옴 → 아이템 재료만 모으기<br>
                  캐리 아이템은 임시로 저코 유닛한테 들려서 연승 유지
                </div>
              </div>
              <div class="op-phase">
                <div class="phase-badge phase-mid">중반 (3-2 ~ 4-5)</div>
                <div class="phase-text">
                  <strong>패스트 레벨 8</strong> 목표 (4-2까지 레벨 8 도달)<br>
                  매 라운드 경험치 구매, 이자는 최대한 유지<br>
                  중간에 <strong>{carry_str}</strong> 뜨면 벤치에 잡아두기<br>
                  레벨 7에서 {tank_str} 넣어서 버티기
                </div>
              </div>
              <div class="op-phase">
                <div class="phase-badge phase-late">후반 (레벨 8~)</div>
                <div class="phase-text">
                  레벨 8 찍자마자 리롤 → <strong>{carry_str}</strong> 2성 만들기<br>
                  임시 유닛 빼고 아이템 캐리한테 옮기기<br>
                  완성되면 레벨 9 → 5코 추가 유닛으로 보드 업그레이드
                </div>
              </div>
            </div>'''

        dk = raw_deck.get("key", ""); du = f"https://lolchess.gg/decks/{dk}?hl=ko" if dk else "#"
        return f'<div class="guide-card"><div class="guide-header"><span class="guide-title">{esc(label)}</span><a class="deck-link" href="{du}" target="_blank">롤체지지 →</a></div><div class="guide-body"><div class="guide-col"><h4 class="col-title">챔피언 구성</h4><div class="champ-list">{"".join(ch_rows)}</div></div><div class="guide-col"><h4 class="col-title">시너지</h4><div class="trait-list">{"".join(tr_rows)}</div>{rr}</div></div>{op_guide}</div>'

    def guide_html():
        secs = []
        for cat in WATCHED_DECKS:
            for label, stats in watched_stats.get(cat, {}).items():
                raw = None
                for entry in WATCHED_DECKS.get(cat, []):
                    if entry["label"] == label:
                        for d in decks_raw:
                            if deck_matches(d, entry): raw = d; break
                        break
                if not raw: continue
                secs.append(deck_detail_card(raw, label))
        return "\n".join(secs) if secs else '<p class="no-data">없음</p>'

    def recommend_html():
        cards = []
        # 현재 최강 덱
        strongest = find_strongest_deck(decks_raw)
        if strongest:
            st = get_deck_stats(strongest)
            pick_rate = strongest.get("pickRate", 0)
            header = (
                f'<div class="deck-stats">'
                f'<div class="stat-item"><span class="stat-label">픽률</span><span class="stat-val">{pick_rate:.1f}%</span></div>'
                f'<div class="stat-item"><span class="stat-label">순방률</span><span class="stat-val">{st["top4_rate"]:.1f}%</span></div>'
                f'<div class="stat-item"><span class="stat-label">평균등수</span><span class="stat-val">{st["avg_placement"]:.2f}</span></div>'
                f'<div class="stat-item"><span class="stat-label">판수</span><span class="stat-val">{st["play_count"]:,}</span></div>'
                f'</div>'
            )
            card_html = deck_detail_card(strongest, f'🏆 현재 최강 덱 — {esc(st["name"])}')
            # 헤더를 guide-header 뒤에 삽입
            insert_pos = card_html.find('</div>', card_html.find('guide-header')) + len('</div>')
            card_html = card_html[:insert_pos] + header + card_html[insert_pos:]
            cards.append(card_html)
        # 숨은 강자
        hidden = find_hidden_op_deck(decks_raw, strongest)
        if hidden:
            ht = get_deck_stats(hidden)
            pick_rate = hidden.get("pickRate", 0)
            header = (
                f'<div class="deck-stats">'
                f'<div class="stat-item"><span class="stat-label">픽률</span><span class="stat-val">{pick_rate:.1f}%</span></div>'
                f'<div class="stat-item"><span class="stat-label">순방률</span><span class="stat-val">{ht["top4_rate"]:.1f}%</span></div>'
                f'<div class="stat-item"><span class="stat-label">평균등수</span><span class="stat-val">{ht["avg_placement"]:.2f}</span></div>'
                f'<div class="stat-item"><span class="stat-label">판수</span><span class="stat-val">{ht["play_count"]:,}</span></div>'
                f'</div>'
                f'<div class="reroll-guide" style="margin:0 14px 8px">픽률 낮아서 안 겹치고 성적 좋은 덱</div>'
            )
            card_html = deck_detail_card(hidden, f'💎 숨은 강자 — {esc(ht["name"])}')
            insert_pos = card_html.find('</div>', card_html.find('guide-header')) + len('</div>')
            card_html = card_html[:insert_pos] + header + card_html[insert_pos:]
            cards.append(card_html)
        return "\n".join(cards) if cards else '<p class="no-data">추천 덱 없음</p>'

    def top5_fn():
        by = sorted(all_stats, key=lambda x:x["top4_rate"], reverse=True); medals=["🥇","🥈","🥉","4️⃣","5️⃣"]
        return "\n".join(f'<div class="top5-row"><span class="top5-medal">{medals[i]}</span><div class="top5-info"><span class="top5-name">{esc(s["name"])}</span><div class="gauge-wrap"><div class="gauge-bar"><div class="gauge-fill {vc(s["top4_rate"])}-fill" style="width:{min(100,s["top4_rate"]):.0f}%"></div></div><span class="gauge-label">{s["top4_rate"]:.1f}%</span></div></div><div class="top5-meta"><span>{s["avg_placement"]:.2f}등</span><span>{s["play_count"]:,}판</span></div></div>' for i,s in enumerate(by[:5]))

    def reroll_fn():
        cn = {1:"⬜ 1코",2:"🟩 2코",3:"🟦 3코",4:"🟪 4코",5:"🟨 5코"}
        rows = []
        for c in range(1,6):
            o=get_optimal_reroll_level(c); lv=o["optimal_level"]; inf=o["all_levels"].get(lv,{})
            rows.append(f'<tr><td>{cn[c]}</td><td><strong>Lv.{lv}</strong></td><td>{inf.get("shop_chance_pct",0):.1f}%</td><td>{inf.get("rolls_for_50pct","-")}롤 / {inf.get("gold_for_50pct","-")}G</td><td>{inf.get("rolls_for_80pct","-")}롤</td></tr>')
        return "\n".join(rows)

    css = """*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}:root{--bg:#0e1117;--surface:#1a1f2e;--surface2:#242a3a;--border:#2e3650;--text:#e8eaf0;--text-muted:#8892aa;--accent:#4f7fff;--cost1:#9e9e9e;--cost2:#4caf50;--cost3:#2196f3;--cost4:#9c27b0;--cost5:#ffc107;--strong:#00c853;--good:#69f0ae;--normal:#ffeb3b;--caution:#ff9800;--weak:#ff5722;--replace:#f44336;--trait1:#cd7f32;--trait2:#c0c0c0;--trait3:#ffd700;--trait4:#b9f2ff;--radius:10px;--shadow:0 2px 12px rgba(0,0,0,.45)}body{background:var(--bg);color:var(--text);font-family:'Segoe UI','Noto Sans KR',Arial,sans-serif;font-size:15px;line-height:1.6;-webkit-text-size-adjust:100%}a{color:var(--accent);text-decoration:none}.site-header{background:linear-gradient(135deg,#1a1f2e,#0e1117);border-bottom:1px solid var(--border);padding:24px 16px 20px;text-align:center}.site-header h1{font-size:clamp(1.3rem,4vw,2rem);font-weight:800}.header-meta{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:10px}.header-chip{background:var(--surface2);border:1px solid var(--border);border-radius:20px;padding:4px 12px;font-size:.78rem;color:var(--text-muted)}.header-chip strong{color:var(--text)}.container{max-width:960px;margin:0 auto;padding:16px 12px 40px}.section{margin-bottom:32px}.section-title{font-size:1rem;font-weight:700;border-left:3px solid var(--accent);padding-left:10px;margin-bottom:14px}.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}.deck-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;box-shadow:var(--shadow)}.deck-card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.deck-name{font-weight:700;font-size:.93rem}.cat-badge{border-radius:4px;padding:2px 7px;font-size:.7rem;font-weight:700}.badge-main{background:#1a3a6b;color:#90caf9}.badge-ad{background:#3a1a1a;color:#ef9a9a}.badge-ap{background:#1a2a3a;color:#80d8ff}.badge-special{background:#2a1a3a;color:#ce93d8}.gauge-wrap{display:flex;align-items:center;gap:8px;margin-bottom:8px}.gauge-bar{flex:1;background:var(--surface2);border-radius:4px;height:8px;overflow:hidden}.gauge-fill{height:100%;border-radius:4px}.gauge-label{font-size:.8rem;font-weight:700;min-width:40px;text-align:right}.verdict-strong-fill{background:var(--strong)}.verdict-good-fill{background:var(--good)}.verdict-normal-fill{background:var(--normal)}.verdict-caution-fill{background:var(--caution)}.verdict-weak-fill{background:var(--weak)}.verdict-replace-fill{background:var(--replace)}.deck-stats{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:10px}.stat-item{display:flex;justify-content:space-between;background:var(--surface2);border-radius:6px;padding:4px 8px;font-size:.78rem}.stat-label{color:var(--text-muted)}.stat-val{font-weight:700}.verdict-row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:5px}.verdict-badge{font-size:.76rem;font-weight:700;padding:2px 8px;border-radius:4px}.verdict-strong{background:#003322;color:var(--strong)}.verdict-good{background:#002211;color:var(--good)}.verdict-normal{background:#332e00;color:var(--normal)}.verdict-caution{background:#332200;color:var(--caution)}.verdict-weak{background:#331100;color:var(--weak)}.verdict-replace{background:#330000;color:var(--replace)}.deck-link{font-size:.76rem;color:var(--accent)}.guide-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:14px;overflow:hidden;box-shadow:var(--shadow)}.guide-header{display:flex;justify-content:space-between;align-items:center;background:var(--surface2);padding:10px 14px;border-bottom:1px solid var(--border)}.guide-title{font-weight:700;font-size:.93rem}.guide-body{display:grid;grid-template-columns:1fr 1fr}@media(max-width:640px){.guide-body{grid-template-columns:1fr}.guide-col+.guide-col{border-left:none!important;border-top:1px solid var(--border)}}.guide-col{padding:12px 14px}.guide-col+.guide-col{border-left:1px solid var(--border)}.col-title{font-size:.76rem;font-weight:700;text-transform:uppercase;color:var(--text-muted);letter-spacing:.5px;margin-bottom:8px}.champ-list{display:flex;flex-direction:column;gap:4px}.champ-row{display:flex;align-items:center;flex-wrap:wrap;gap:5px;padding:4px 7px;border-radius:5px;background:var(--surface2);font-size:.82rem}.carry-row{border-left:2px solid var(--accent)}.champ-cost-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}.cost-1{background:var(--cost1)}.cost-2{background:var(--cost2)}.cost-3{background:var(--cost3)}.cost-4{background:var(--cost4)}.cost-5{background:var(--cost5)}.champ-name{font-weight:600;flex:1;min-width:50px}.champ-cost-label{font-size:.7rem;color:var(--text-muted)}.item-row{display:flex;flex-wrap:wrap;gap:3px;width:100%}.item-badge{background:#1e2535;border:1px solid var(--border);border-radius:3px;padding:1px 5px;font-size:.68rem;color:#afc6ff}.trait-list{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}.trait-badge{border-radius:4px;padding:2px 7px;font-size:.74rem;font-weight:600}.style-1{background:#2b1e0e;color:var(--trait1);border:1px solid #5a3a1a}.style-2{background:#1e2025;color:var(--trait2);border:1px solid #4a4a55}.style-3{background:#2b2500;color:var(--trait3);border:1px solid #6a5e00}.style-4{background:#0e2530;color:var(--trait4);border:1px solid #1a6a80}.reroll-guide{background:var(--surface2);border-radius:5px;padding:7px 9px;font-size:.8rem;margin-top:6px}.op-guide{padding:14px 16px;border-top:1px solid var(--border)}.op-phase{margin-bottom:10px}.phase-badge{display:inline-block;font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:4px;margin-bottom:4px}.phase-early{background:#1a3a2a;color:#69f0ae}.phase-mid{background:#2a2a1a;color:#ffeb3b}.phase-late{background:#3a1a1a;color:#ff8a80}.phase-text{font-size:.8rem;line-height:1.6;color:var(--text-muted);padding-left:4px}.top5-list{display:flex;flex-direction:column;gap:8px}.top5-row{display:flex;align-items:center;gap:10px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:10px 12px}.top5-medal{font-size:1.3rem;flex-shrink:0;width:28px;text-align:center}.top5-info{flex:1;min-width:0}.top5-name{display:block;font-weight:700;font-size:.88rem;margin-bottom:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.top5-meta{display:flex;flex-direction:column;align-items:flex-end;gap:1px;font-size:.76rem;color:var(--text-muted)}.reroll-table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.82rem}th{background:var(--surface2);color:var(--text-muted);font-weight:600;padding:8px 10px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}td{padding:7px 10px;border-bottom:1px solid var(--border)}tr:last-child td{border-bottom:none}.site-footer{text-align:center;padding:16px;font-size:.78rem;color:var(--text-muted);border-top:1px solid var(--border)}.no-data{color:var(--text-muted);font-size:.85rem;padding:10px 0}"""

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>TFT 메타 대시보드</title><style>{css}</style></head><body>
<header class="site-header"><h1>🎮 TFT 메타 대시보드</h1><div class="header-meta"><span class="header-chip">패치 <strong>{patch}</strong></span><span class="header-chip">업데이트 <strong>{updated_at}</strong></span><span class="header-chip">분석 덱 <strong>{total_decks}개</strong></span></div></header>
<main class="container">
<section class="section"><h2 class="section-title">🎯 내 덱 현황</h2><div class="cards-grid">{watched_html()}</div></section>
<section class="section"><h2 class="section-title">🔥 추천 덱</h2>{recommend_html()}</section>
<section class="section"><h2 class="section-title">📖 덱 상세 가이드</h2>{guide_html()}</section>
<section class="section"><h2 class="section-title">🏆 순방률 TOP 5</h2><div class="top5-list">{top5_fn()}</div></section>
<section class="section"><h2 class="section-title">🎰 리롤 확률 가이드</h2><div class="reroll-table-wrap"><table><thead><tr><th>코스트</th><th>최적 레벨</th><th>상점 확률</th><th>50% 달성</th><th>80% 달성</th></tr></thead><tbody>{reroll_fn()}</tbody></table></div></section>
</main>
<footer class="site-footer">롤체지지 기준 | 마지막 업데이트: {now_str} | <a href="https://lolchess.gg/decks?hl=ko" target="_blank">lolchess.gg</a></footer>
</body></html>"""


# ── Discord 전송 ─────────────────────────────────────────────────────────────

def _deck_url(deck_raw: dict) -> str:
    """덱의 롤체지지 상세 페이지 URL을 생성한다."""
    key = deck_raw.get("key", deck_raw.get("deckKey", ""))
    if key:
        return f"https://lolchess.gg/decks/{key}?hl=ko"
    return "https://lolchess.gg/decks?hl=ko"


def _verdict_bar(rate: float) -> str:
    """순방률을 시각적 게이지 바로 표현한다."""
    filled = int(rate / 10)
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    return bar


def _verdict_label(rate: float) -> str:
    """순방률 기준 한글 판정."""
    if rate >= 58:
        return "🟢 강력"
    elif rate >= 55:
        return "✅ 좋음"
    elif rate >= 52:
        return "🟡 보통"
    elif rate >= 50:
        return "⚡ 주의"
    elif rate >= 47:
        return "🟠 약함"
    else:
        return "🔴 교체"


def _avg_verdict(avg: float) -> str:
    """평균등수 기준 한글 판정."""
    if avg <= 3.8:
        return "🟢"
    elif avg <= 4.0:
        return "✅"
    elif avg <= 4.2:
        return "🟡"
    elif avg <= 4.4:
        return "⚡"
    else:
        return "🔴"


def build_summary_embeds(meta_info: dict, all_stats: list[dict], watched_stats: dict,
                         decks_raw: list[dict] = None) -> list[dict]:
    """전체 요약을 여러 임베드로 나눠 보기 좋게 생성한다."""
    embeds = []
    by_top4 = sorted(all_stats, key=lambda x: x["top4_rate"], reverse=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 임베드 1: 패치 정보 & 요약 헤더 ──
    total_decks = len(all_stats)
    avg_top4 = sum(s["top4_rate"] for s in all_stats) / total_decks if total_decks else 0

    DASHBOARD_URL = "https://kminto.github.io/tft-meta-watcher/"

    embeds.append({
        "title": "📋 TFT 메타 리포트",
        "description": (
            f"```\n"
            f"시즌    : {meta_info.get('season', 'N/A')}\n"
            f"패치    : {meta_info.get('patch', 'N/A')}\n"
            f"업데이트 : {meta_info.get('updatedAt', 'N/A')}\n"
            f"분석 덱  : {total_decks}개\n"
            f"```\n"
            f"[📱 웹 대시보드에서 상세 보기]({DASHBOARD_URL})"
        ),
        "color": 0x1E90FF,
        "footer": {"text": f"롤체지지 기준 | {now_str}"},
    })

    # ── 임베드 2: 내 덱 현황 (감시 대상) ──
    deck_fields = []
    category_emojis = {"main": "⭐", "ad_alt": "⚔️", "ap_alt": "🔮", "special": "🌀"}
    category_names = {"main": "메인덱", "ad_alt": "AD 대체덱", "ap_alt": "AP 대체덱", "special": "특수 상황덱"}

    for category in WATCHED_DECKS:
        label_map = watched_stats.get(category, {})
        for label, stats in label_map.items():
            emoji = category_emojis.get(category, "▪️")
            cat_name = category_names.get(category, category)
            verdict = _verdict_label(stats["top4_rate"])
            bar = _verdict_bar(stats["top4_rate"])
            avg_icon = _avg_verdict(stats["avg_placement"])

            reroll_info = build_reroll_info_for_deck(stats)

            # 롤체지지 상세 페이지 링크 찾기
            deck_link = ""
            if decks_raw:
                for entry in WATCHED_DECKS.get(category, []):
                    if entry["label"] == label:
                        for d in decks_raw:
                            if deck_matches(d, entry):
                                deck_link = _deck_url(d)
                                break
                        break

            value = (
                f"```\n"
                f"순방률   {bar} {stats['top4_rate']:.1f}%\n"
                f"평균등수  {avg_icon} {stats['avg_placement']:.2f}등\n"
                f"승률     {stats['win_rate']:.1f}%\n"
                f"게임 수   {stats['play_count']:,}판\n"
                f"```\n"
                f"판정: {verdict}\n"
            )
            if reroll_info:
                value += f"🎰 {reroll_info}\n"
            if deck_link:
                value += f"[📖 조합·아이템·운영법 보기]({deck_link})\n"

            deck_fields.append({
                "name": f"{emoji} [{cat_name}] {label}",
                "value": value,
                "inline": False,
            })

    if not deck_fields:
        deck_fields.append({
            "name": "감시 덱",
            "value": "매칭되는 덱이 없어요. 키워드 확인해보세요.",
            "inline": False,
        })

    embeds.append({
        "title": "🎯 내 덱 현황",
        "description": "내가 쓰는 덱들 실시간 성적이에요.\n🟢 강력 58%+ / ✅ 좋음 55%+ / 🟡 보통 52%+ / ⚡ 주의 50%+ / 🟠 약함 47%+ / 🔴 교체 47%-",
        "color": 0x2ECC71,
        "fields": deck_fields,
        "footer": {"text": now_str},
    })

    # ── 임베드 3: 순방률 TOP 5 ──
    top5_lines = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, s in enumerate(by_top4[:5]):
        bar = _verdict_bar(s["top4_rate"])
        top5_lines.append(
            f"{medals[i]} **{s['name']}**\n"
            f"  {bar} {s['top4_rate']:.1f}% | 등수 {s['avg_placement']:.2f} | {s['play_count']:,}판"
        )

    embeds.append({
        "title": "🏆 지금 가장 잘 나가는 덱 TOP 5",
        "description": "\n\n".join(top5_lines) if top5_lines else "데이터 없음",
        "color": 0xFFD700,
        "footer": {"text": now_str},
    })

    # ── 임베드 4: 리롤 확률 가이드 ──
    reroll_lines = []
    cost_emojis = {1: "⬜", 2: "🟩", 3: "🟦", 4: "🟪", 5: "🟨"}
    for cost in range(1, 6):
        opt = get_optimal_reroll_level(cost)
        lvl = opt["optimal_level"]
        info = opt["all_levels"].get(lvl, {})
        ce = cost_emojis.get(cost, "▪️")
        reroll_lines.append(
            f"{ce} **{cost}코스트** → 레벨 **{lvl}**에서 리롤\n"
            f"  상점 확률: {info.get('shop_chance_pct', 0):.1f}% | "
            f"50%: {info.get('rolls_for_50pct', '?')}롤({info.get('gold_for_50pct', '?')}G) | "
            f"80%: {info.get('rolls_for_80pct', '?')}롤"
        )

    embeds.append({
        "title": "🎰 리롤 확률 가이드",
        "description": (
            "몇 레벨에서 리롤해야 가장 잘 뜨는지 정리했어요.\n"
            "50% = 반반 확률로 1장 뜸 / 80% = 거의 뜸\n\n"
            + "\n\n".join(reroll_lines)
        ),
        "color": 0x9B59B6,
        "footer": {"text": f"풀 미소진 기준 | {now_str}"},
    })

    return embeds


def build_alert_embed(alert: dict) -> dict:
    """개별 알림 임베드를 생성한다."""
    color_map = {
        "high": 0xFF4444,
        "medium": 0xFFAA00,
        "info": 0x44AAFF,
    }
    priority_labels = {
        "high": "🔴 중요",
        "medium": "🟡 체크",
        "info": "🔵 참고",
    }

    # 알림 타입별 액션 가이드 추가
    action_guide = {
        "patch_change": "→ 1~2일 랭크 쉬고 메타 안정되면 돌리세요.",
        "patch_today": "→ 패치 전에 남은 판 마무리하세요.",
        "hotfix_detected": "→ 핫픽스 내용 확인하고 영향 받는 덱 체크하세요.",
        "main_deck_warning": "→ 대체덱 준비해두세요. AD덱이나 특수덱 확인.",
        "better_ad_deck": "→ 가이드 찾아보고 노말에서 연습해보세요.",
        "special_deck_good": "→ 상황 맞으면 적극적으로 가져가세요.",
        "deck_disappeared": "→ 이 덱은 접고 다른 덱으로 갈아타세요.",
        "new_top3": "→ 가이드 한번 봐두세요. 덱 풀에 넣을지 검토.",
        "new_strong_deck": "→ 표본도 많고 성적도 좋아요. 배워둘 가치 있음.",
        "new_reroll_deck": "→ 지금 쓰는 덱보다 좋아요. 갈아탈지 고민해보세요.",
        "low_avg_deck": "→ 평균등수가 좋은 덱이에요. 체크해보세요.",
        "better_deck_found": "→ 이 덱 가이드 확인해보세요. 갈아탈지 고민해볼 타이밍!",
    }

    # 덱 관련 알림이면 롤체지지 링크 추가
    deck_alert_types = {"better_ad_deck", "new_strong_deck", "new_reroll_deck",
                        "new_top3", "low_avg_deck", "special_deck_good",
                        "main_deck_warning", "deck_disappeared"}

    action = action_guide.get(alert["type"], "")
    description = alert["message"]

    if alert["type"] in deck_alert_types:
        deck_url = alert.get("deck_url", "https://lolchess.gg/decks?hl=ko")
        description += f"\n[📖 조합·아이템·운영법 보기]({deck_url})"

    if action:
        description += f"\n\n**다음 행동**\n{action}"

    return {
        "title": alert["title"],
        "description": description,
        "color": color_map.get(alert["priority"], 0x888888),
        "footer": {"text": f"{priority_labels.get(alert['priority'], '')} | {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
    }


def send_discord(embeds: list[dict], dry_run: bool = False):
    """Discord Webhook으로 임베드를 전송한다."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    # 10개씩 나눠 전송 (Discord 제한)
    for i in range(0, len(embeds), 10):
        batch = embeds[i:i + 10]
        payload = {"embeds": batch}

        if dry_run:
            logger.info("=== DRY RUN: Discord 전송 내용 ===")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue

        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code == 204:
            logger.info(f"Discord 알림 전송 성공 ({len(batch)}개 임베드)")
        else:
            logger.error(f"Discord 전송 실패: {resp.status_code} {resp.text}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TFT 메타 감시 디스코드 알림봇")
    parser.add_argument("--dry-run", action="store_true", help="Discord 전송 없이 콘솔에 출력")
    parser.add_argument("--force-alert", action="store_true", help="변경 없어도 현황 알림 강제 전송")
    parser.add_argument("--reroll", type=int, metavar="COST",
                        help="특정 코스트 챔프의 레벨별 리롤 확률표 출력 (1~5)")
    args = parser.parse_args()

    # 리롤 확률 조회 모드
    if args.reroll:
        cost = args.reroll
        if cost < 1 or cost > 5:
            logger.error("코스트는 1~5 사이여야 합니다.")
            sys.exit(1)
        print(f"\n{'='*60}")
        print(f"  {cost}코스트 챔프 리롤 확률표")
        print(f"{'='*60}")
        print(f"{'레벨':>4} | {'코스트 확률':>8} | {'상점 확률':>8} | {'50% 롤':>6} | {'50% 골드':>7} | {'80% 롤':>6}")
        print(f"{'-'*60}")
        optimal = get_optimal_reroll_level(cost)
        for lvl in range(3, 10):
            info = optimal["all_levels"].get(lvl, {})
            marker = " ◀ 최적" if lvl == optimal["optimal_level"] else ""
            print(
                f"  {lvl:>2} | {info.get('tier_chance_pct', 0):>7.1f}% | "
                f"{info.get('shop_chance_pct', 0):>7.2f}% | "
                f"{info.get('rolls_for_50pct', '-'):>5} | "
                f"{info.get('gold_for_50pct', '-'):>6}G | "
                f"{info.get('rolls_for_80pct', '-'):>5}{marker}"
            )
        print(f"{'='*60}\n")
        return

    logger.info("TFT 메타 감시 시작...")

    # 챔피언/아이템 데이터 로드 (커뮤니티 드래곤)
    load_tft_data()

    # config.json에서 최신 덱 설정 리로드
    global WATCHED_DECKS
    WATCHED_DECKS = load_watched_decks()

    # 데이터 수집
    try:
        current_data = fetch_lolchess_meta()
    except Exception as e:
        logger.error(f"롤체지지 데이터 수집 실패: {e}")
        sys.exit(1)

    meta_info = current_data["meta_info"]
    decks = current_data["decks"]
    logger.info(f"패치: {meta_info.get('patch')} | 덱 수: {len(decks)} | 업데이트: {meta_info.get('updatedAt')}")

    if not decks:
        logger.warning("메타 덱 데이터가 비어 있습니다. 페이지 구조가 변경되었을 수 있습니다.")
        logger.info(f"pageProps 키 목록: {current_data.get('raw_page_props_keys', [])}")

    # 이전 상태 로드
    prev_state = load_state()

    # 현재 덱 통계 수집
    all_stats = []
    watched_stats = {cat: {} for cat in WATCHED_DECKS}

    for deck in decks:
        stats = get_deck_stats(deck)
        all_stats.append(stats)

        for category, entries in WATCHED_DECKS.items():
            for entry in entries:
                if deck_matches(deck, entry):
                    watched_stats[category][entry["label"]] = stats

    # 변경 분석
    alerts = analyze_changes(current_data, prev_state)

    # Riot 패치노트 확인
    patch_notes = fetch_riot_patch_notes()
    prev_patch_urls = set(prev_state.get("patch_note_urls", []))
    new_notes = [n for n in patch_notes if n["url"] not in prev_patch_urls]
    if new_notes:
        for note in new_notes:
            # B패치/핫픽스 감지
            title_lower = note["title"].lower()
            is_hotfix = any(kw in title_lower for kw in ["b패치", "핫픽스", "hotfix", "마이크로", "긴급"])
            if is_hotfix:
                alerts.append({
                    "type": "hotfix_detected",
                    "title": "🔥 B패치/핫픽스 나옴!",
                    "message": (
                        f"**{note['title']}**\n{note['url']}\n\n"
                        f"적용되면 메타가 바로 바뀔 수 있어요.\n"
                        f"적용 후 집중 감시가 자동으로 켜집니다."
                    ),
                    "priority": "high",
                })
            else:
                alerts.append({
                    "type": "riot_patch_note",
                    "title": "📰 TFT 패치노트 올라옴",
                    "message": f"**{note['title']}**\n{note['url']}",
                    "priority": "info",
                })

    # 패치 일정 확인
    patch_schedules = fetch_riot_patch_schedule()
    schedule_alerts = check_patch_day_alert(patch_schedules, prev_state)
    alerts.extend(schedule_alerts)

    # 패치 직후 집중 감시 모드 체크
    is_post_patch = False
    prev_meta = prev_state.get("meta_info", {})
    prev_patch_detected_at = prev_state.get("patch_detected_at")
    if prev_meta.get("patch") and meta_info["patch"] != prev_meta.get("patch"):
        # 새 패치 감지 → 집중 감시 시작
        is_post_patch = True
    elif prev_patch_detected_at:
        # 이전에 감지된 패치의 집중 감시 시간 내인지 확인
        try:
            detected_time = datetime.fromisoformat(prev_patch_detected_at)
            elapsed = (datetime.now() - detected_time).total_seconds()
            if elapsed < POST_PATCH_WATCH_DURATION:
                is_post_patch = True
                remaining_hours = (POST_PATCH_WATCH_DURATION - elapsed) / 3600
                logger.info(f"패치 직후 집중 감시 모드 (잔여: {remaining_hours:.1f}시간)")
        except (ValueError, TypeError):
            pass

    # 상태 저장
    decks_stats_map = {}
    for s in all_stats:
        decks_stats_map[s["name"]] = s
    for cat_stats in watched_stats.values():
        for label, s in cat_stats.items():
            decks_stats_map[label] = s

    by_top4 = sorted(all_stats, key=lambda x: x["top4_rate"], reverse=True)
    by_avg = sorted([s for s in all_stats if s["avg_placement"] > 0],
                    key=lambda x: x["avg_placement"])

    # 패치 감지 시각 관리
    patch_detected_at = prev_state.get("patch_detected_at")
    if prev_meta.get("patch") and meta_info["patch"] != prev_meta.get("patch"):
        patch_detected_at = datetime.now().isoformat()
    elif patch_detected_at:
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(patch_detected_at)).total_seconds()
            if elapsed >= POST_PATCH_WATCH_DURATION:
                patch_detected_at = None  # 집중 감시 종료
        except (ValueError, TypeError):
            patch_detected_at = None

    # 알림 보낸 패치 날짜 기록
    notified_dates = list(set(prev_state.get("notified_patch_dates", [])))
    for sched in patch_schedules:
        if sched.get("date") in [datetime.now().strftime("%Y-%m-%d"),
                                  (datetime.now().replace(hour=0, minute=0, second=0) +
                                   __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")]:
            notified_dates.append(sched["date"])
    notified_dates = list(set(notified_dates))[-20:]  # 최근 20개만 유지

    # 알림 보낸 "내 덱보다 좋은 덱" 기록
    notified_better = list(prev_state.get("notified_better_decks", []))
    for a in alerts:
        if a["type"] == "better_deck_found":
            match = re.search(r'\*\*(.+?)\*\*', a.get("message", ""))
            if match:
                notified_better.append(match.group(1))
    # 패치 바뀌면 리셋 (새 패치에서는 다시 알림)
    if prev_meta.get("patch") and meta_info["patch"] != prev_meta.get("patch"):
        notified_better = []

    new_state = {
        "meta_info": meta_info,
        "decks_stats": decks_stats_map,
        "top3_top4_names": [s["name"] for s in by_top4[:3]],
        "top3_avg_names": [s["name"] for s in by_avg[:3]],
        "patch_note_urls": [n["url"] for n in patch_notes],
        "patch_detected_at": patch_detected_at,
        "notified_patch_dates": notified_dates,
        "notified_better_decks": notified_better,
        "patch_schedules": patch_schedules,
        "is_post_patch": is_post_patch,
        "last_run": datetime.now().isoformat(),
    }
    save_state(new_state)

    # 알림 전송 시간 체크 (KST 기준)
    import zoneinfo
    try:
        kst_now = datetime.now(zoneinfo.ZoneInfo("Asia/Seoul"))
    except Exception:
        kst_now = datetime.now()  # 폴백
    current_hour = kst_now.hour
    today_str = kst_now.strftime("%Y-%m-%d")
    last_alert_date = prev_state.get("last_alert_date", "")

    # 긴급 알림 (패치 변경, 핫픽스) → 시간 무관 즉시 전송
    urgent_types = {"patch_change", "hotfix_detected", "patch_today"}
    urgent_alerts = [a for a in alerts if a.get("type") in urgent_types]

    # 일반 알림 → 매일 18시(KST)에 한 번만
    is_alert_time = (current_hour == ALERT_HOUR_KST and today_str != last_alert_date)

    embeds = []

    # 긴급 알림은 항상 전송
    if urgent_alerts:
        for alert in urgent_alerts:
            if "deck_url" not in alert and decks:
                match = re.search(r'\*\*(.+?)\*\*', alert.get("message", ""))
                if match:
                    alert["deck_url"] = _find_deck_url(match.group(1), decks)
            embeds.append(build_alert_embed(alert))

    # 일반 알림 + 요약 → 18시 또는 force-alert일 때만
    if is_alert_time or args.force_alert or (not prev_state):
        normal_alerts = [a for a in alerts if a.get("type") not in urgent_types]
        for alert in normal_alerts:
            if "deck_url" not in alert and decks:
                match = re.search(r'\*\*(.+?)\*\*', alert.get("message", ""))
                if match:
                    alert["deck_url"] = _find_deck_url(match.group(1), decks)
            embeds.append(build_alert_embed(alert))

        summary_embeds = build_summary_embeds(meta_info, all_stats, watched_stats, decks)
        if is_post_patch and summary_embeds:
            summary_embeds[0]["title"] = "📋 TFT 메타 리포트 [🔥 패치 직후 집중 감시]"
            summary_embeds[0]["color"] = 0xFF6600
        embeds.extend(summary_embeds)

        # 오늘 알림 보냈다고 기록
        new_state["last_alert_date"] = today_str
        save_state(new_state)
        logger.info(f"18시 정기 알림 전송")

    if embeds:
        send_discord(embeds, dry_run=args.dry_run)
        logger.info(f"총 {len(embeds)}개 임베드 {'출력' if args.dry_run else '전송'} 완료")
    else:
        logger.info("알림 시간 아님 또는 변경 없음. 데이터만 저장.")

    # HTML 대시보드 생성 (GitHub Pages용)
    try:
        html_content = generate_html_report(meta_info, all_stats, watched_stats, decks)
        HTML_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with open(HTML_OUTPUT, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"HTML 대시보드 생성: {HTML_OUTPUT}")
    except Exception as e:
        logger.warning(f"HTML 대시보드 생성 실패: {e}")

    logger.info("TFT 메타 감시 완료.")


if __name__ == "__main__":
    main()
