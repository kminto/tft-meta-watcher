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

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── 설정 ──────────────────────────────────────────────────────────────────────

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "state" / "tft_meta_state.json"
LOLCHESS_URL = "https://lolchess.gg/decks?hl=ko"
RIOT_PATCH_URL = "https://www.leagueoflegends.com/ko-kr/news/game-updates/"
RIOT_PATCH_SCHEDULE_URL = "https://support-leagueoflegends.riotgames.com/hc/ko/articles/360018987893"

# 패치 직후 집중 감시 시간 (초) - 패치 감지 후 6시간 동안 집중 모드
POST_PATCH_WATCH_DURATION = 6 * 60 * 60

# 감시 대상 덱 (키워드 매칭)
WATCHED_DECKS = {
    "main": [
        {"keywords": ["별돌보미", "룰루"], "label": "별돌보미 룰루"},
    ],
    "ad_alt": [
        {"keywords": ["전달자", "미스 포츈"], "label": "전달자 미스 포츈"},
        {"keywords": ["운명술사", "코르키"], "label": "운명술사 코르키"},
    ],
    "special": [
        {"keywords": ["시간 균열자", "이즈리얼"], "label": "시간 균열자 이즈리얼"},
    ],
}

# ── 챔피언/아이템/시너지 한글 매핑 ─────────────────────────────────────────────

# TFT 세트17 챔피언 (key → 한글이름, 코스트)
CHAMPION_DATA = {
    # 1코스트
    "TFT17_Darius": ("다리우스", 1), "TFT17_Elise": ("엘리스", 1),
    "TFT17_Jax": ("잭스", 1), "TFT17_Kindred": ("킨드레드", 1),
    "TFT17_Morgana": ("모르가나", 1), "TFT17_Nocturne": ("녹턴", 1),
    "TFT17_Poppy": ("뽀삐", 1), "TFT17_Seraphine": ("세라핀", 1),
    "TFT17_Shaco": ("샤코", 1), "TFT17_Twisted_Fate": ("트위스티드 페이트", 1),
    "TFT17_Twitch": ("트위치", 1), "TFT17_Ziggs": ("직스", 1),
    # 2코스트
    "TFT17_Cassiopeia": ("카시오페아", 2), "TFT17_Draven": ("드레이븐", 2),
    "TFT17_Galio": ("갈리오", 2), "TFT17_KhaZix": ("카직스", 2),
    "TFT17_Lillia": ("릴리아", 2), "TFT17_Renekton": ("레넥톤", 2),
    "TFT17_Senna": ("세나", 2), "TFT17_Soraka": ("소라카", 2),
    "TFT17_Syndra": ("신드라", 2), "TFT17_Tristana": ("트리스타나", 2),
    "TFT17_Vex": ("벡스", 2), "TFT17_Zyra": ("자이라", 2),
    # 3코스트
    "TFT17_Ekko": ("에코", 3), "TFT17_Ezreal": ("이즈리얼", 3),
    "TFT17_Graves": ("그레이브즈", 3), "TFT17_Katarina": ("카타리나", 3),
    "TFT17_Lulu": ("룰루", 3), "TFT17_MissFortune": ("미스 포츈", 3),
    "TFT17_Mordekaiser": ("모데카이저", 3), "TFT17_Neeko": ("니코", 3),
    "TFT17_Sett": ("세트", 3), "TFT17_Veigar": ("베이가", 3),
    "TFT17_Wukong": ("오공", 3), "TFT17_Zoe": ("조이", 3),
    # 4코스트
    "TFT17_Aphelios": ("아펠리오스", 4), "TFT17_Corki": ("코르키", 4),
    "TFT17_Gwen": ("그웬", 4), "TFT17_KSante": ("크산테", 4),
    "TFT17_Nami": ("나미", 4), "TFT17_TahmKench": ("탐 켄치", 4),
    "TFT17_Talon": ("탈론", 4), "TFT17_Vladimir": ("블라디미르", 4),
    "TFT17_Zed": ("제드", 4), "TFT17_Yone": ("요네", 4),
    # 5코스트
    "TFT17_AurelionSol": ("아우렐리온 솔", 5), "TFT17_Camille": ("카밀", 5),
    "TFT17_Heimerdinger": ("하이머딩거", 5), "TFT17_Jinx": ("징크스", 5),
    "TFT17_Milio": ("밀리오", 5), "TFT17_Samira": ("사미라", 5),
    "TFT17_Silco": ("실코", 5), "TFT17_Xerath": ("제라스", 5),
    "TFT17_Viktor": ("빅토르", 5),
    # 추가 챔프 (세트17 확장 / 매핑 누락분)
    "TFT17_Aatrox": ("아트록스", 4), "TFT17_Caitlyn": ("케이틀린", 3),
    "TFT17_Gnar": ("나르", 2), "TFT17_Gragas": ("그라가스", 2),
    "TFT17_Illaoi": ("일라오이", 3), "TFT17_Leona": ("레오나", 2),
    "TFT17_Maokai": ("마오카이", 1), "TFT17_Nunu": ("누누", 2),
    "TFT17_Pantheon": ("판테온", 4), "TFT17_Rhaast": ("라아스트", 4),
    "TFT17_Riven": ("리븐", 3), "TFT17_Ornn": ("오른", 4),
    "TFT17_Rammus": ("람머스", 2), "TFT17_Shen": ("쉔", 3),
}

# 코스트별 이모지
COST_EMOJI = {1: "⬜", 2: "🟩", 3: "🟦", 4: "🟪", 5: "🟨"}

# TFT 아이템 한글 매핑
ITEM_NAMES = {
    # 완성 아이템
    "TFT_Item_BFSword": "B.F. 대검", "TFT_Item_ChainVest": "쇠사슬 조끼",
    "TFT_Item_GiantsBelt": "거인의 허리띠", "TFT_Item_NeedlesslyLargeRod": "쓸데없이 큰 지팡이",
    "TFT_Item_NegatronCloak": "음전자 망토", "TFT_Item_RecurveBow": "곡궁",
    "TFT_Item_SparringGloves": "연습용 장갑", "TFT_Item_Spatula": "뒤집개",
    "TFT_Item_TearOfTheGoddess": "여신의 눈물",
    # 조합 아이템
    "TFT_Item_ArchangelsStaff": "대천사의 지팡이",
    "TFT_Item_Bloodthirster": "피바라기",
    "TFT_Item_BlueBuff": "푸른 파수꾼",
    "TFT_Item_BrambleVest": "덤불 조끼",
    "TFT_Item_Crownguard": "왕관수호대",
    "TFT_Item_Deathblade": "죽음의 검",
    "TFT_Item_DragonsClaw": "용의 발톱",
    "TFT_Item_EdgeOfNight": "밤의 끝자락",
    "TFT_Item_EmblemsEmblem": "상징",
    "TFT_Item_GargoyleStoneplate": "가고일 돌갑옷",
    "TFT_Item_GiantSlayer": "거인 학살자",
    "TFT_Item_GuardianAngel": "수호 천사",
    "TFT_Item_GuinsoosRageblade": "구인수의 격노검",
    "TFT_Item_HandOfJustice": "정의의 손",
    "TFT_Item_HextechGunblade": "마법공학 총검",
    "TFT_Item_InfinityEdge": "무한의 대검",
    "TFT_Item_IonicSpark": "이온 충격기",
    "TFT_Item_JeweledGauntlet": "보석 건틀릿",
    "TFT_Item_LastWhisper": "최후의 속삭임",
    "TFT_Item_Leviathan": "리바이어던",
    "TFT_Item_LocketOfTheIronSolari": "솔라리의 펜던트",
    "TFT_Item_Morellonomicon": "모렐로노미콘",
    "TFT_Item_NashorsTooth": "내셔의 이빨",
    "TFT_Item_ProtectorsVow": "수호자의 맹세",
    "TFT_Item_Quicksilver": "수은",
    "TFT_Item_RabadonsDeathcap": "라바돈의 죽음모자",
    "TFT_Item_RedBuff": "붉은 파수꾼",
    "TFT_Item_Redemption": "구원",
    "TFT_Item_RunaansHurricane": "루난의 허리케인",
    "TFT_Item_SpearOfShojin": "쇼진의 창",
    "TFT_Item_StatikkShiv": "스태틱의 단검",
    "TFT_Item_SteadfastHeart": "굳건한 심장",
    "TFT_Item_SteraksGage": "스테락의 도전",
    "TFT_Item_SunfireCape": "태양불꽃 망토",
    "TFT_Item_ThiefsGloves": "도둑의 장갑",
    "TFT_Item_TitansResolve": "거인의 결의",
    "TFT_Item_WarmogsArmor": "워모그의 갑옷",
    "TFT_Item_ZekesHerald": "지크의 전령",
    "TFT_Item_Zephyr": "서풍",
    "TFT_Item_ZzRotPortal": "즈롯 차원문",
    # 추가 아이템 (세트17 신규 / 누락분)
    "TFT_Item_AdaptiveHelm": "적응형 투구",
    "TFT_Item_FrozenHeart": "얼어붙은 심장",
    "TFT_Item_MadredsBloodrazor": "매드레드의 피바라기",
    "TFT_Item_PowerGauntlet": "힘의 건틀릿",
    "TFT17_Item_MadredsBloodrazor": "매드레드의 피바라기",
    "TFT17_Item_PowerGauntlet": "힘의 건틀릿",
    "TFT17_Item_AdaptiveHelm": "적응형 투구",
    "TFT17_Item_FrozenHeart": "얼어붙은 심장",
    "TFT17_Item_PsyOpsDroneMod": "사이옵스 드론",
}

# TFT 세트17 시너지 한글 매핑
TRAIT_NAMES = {
    "TFT17_Stargazer": "별돌보미", "TFT17_Emissary": "전달자",
    "TFT17_Oracle": "운명술사", "TFT17_ChronoBreaker": "시간 균열자",
    "TFT17_NOVA": "N.O.V.A.", "TFT17_Slayer": "학살자",
    "TFT17_Techno": "테크노", "TFT17_Bruiser": "싸움꾼",
    "TFT17_Bastion": "보루", "TFT17_Invoker": "기원사",
    "TFT17_Marksman": "명사수", "TFT17_Sorcerer": "마법사",
    "TFT17_Assassin": "암살자", "TFT17_Vanguard": "선봉대",
    "TFT17_Protector": "수호자", "TFT17_Rebel": "반군",
    "TFT17_Rapidfire": "속사포", "TFT17_Aegis": "방패술사",
    "TFT17_Artillerist": "포병", "TFT17_Duelist": "결투가",
    "TFT17_Executioner": "처형자", "TFT17_Guardian": "수호자",
    "TFT17_Mystic": "신비술사", "TFT17_Redeemer": "구원자",
    "TFT17_Shapeshifter": "변신술사",
    # 추가 시너지 (세트17 확장 / 누락분)
    "TFT17_ManaTrait": "마나",
    "TFT17_APTrait": "주문력",
    "TFT17_RangedTrait": "원거리",
    "TFT17_HPTank": "체력탱",
    "TFT17_ResistTank": "저항탱",
    "TFT17_ShieldTank": "방패탱",
    "TFT17_DRX": "DRX",
    "TFT17_ADMIN": "관리자",
    "TFT17_Astronaut": "우주비행사",
    "TFT17_Fateweaver": "운명술사",
    "TFT17_PsyOps": "사이옵스",
    "TFT17_Timebreaker": "시간 균열자",
    # 고유 특성
    "TFT17_MorganaUniqueTrait": "모르가나 고유",
    "TFT17_RhaastUniqueTrait": "라아스트 고유",
    "TFT17_ShenUniqueTrait": "쉔 고유",
    "TFT17_MissFortuneUniqueTrait": "미스 포츈 고유",
    "TFT17_TahmKenchUniqueTrait": "탐 켄치 고유",
}

# 시너지 스타일 (style → 등급)
TRAIT_STYLE = {1: "🥉", 2: "🥈", 3: "🥇", 4: "💠"}


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
    watched_stats = {"main": {}, "ad_alt": {}, "special": {}}

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

    # 3. 별돌보미 룰루 체크
    lulu_stats = watched_stats["main"].get("별돌보미 룰루")
    if lulu_stats:
        prev_lulu = prev_decks_stats.get("별돌보미 룰루", {})
        if prev_lulu:
            top4_diff = lulu_stats["top4_rate"] - prev_lulu.get("top4_rate", 0)
            avg_diff = lulu_stats["avg_placement"] - prev_lulu.get("avg_placement", 0)

            if top4_diff <= -2.0:
                alerts.append({
                    "type": "main_deck_warning",
                    "title": "⚠️ 메인덱 순방률 떨어짐",
                    "message": (
                        f"**별돌보미 룰루** 순방률이 {abs(top4_diff):.2f}%p 빠졌어요\n"
                        f"{prev_lulu.get('top4_rate', 0):.2f}% → **{lulu_stats['top4_rate']:.2f}%**\n"
                        f"평균등수: {lulu_stats['avg_placement']:.2f}"
                    ),
                    "priority": "high",
                })

            if avg_diff >= 0.15:
                alerts.append({
                    "type": "main_deck_warning",
                    "title": "⚠️ 메인덱 등수 밀림",
                    "message": (
                        f"**별돌보미 룰루** 평균등수가 {avg_diff:.2f} 밀렸어요\n"
                        f"{prev_lulu.get('avg_placement', 0):.2f}등 → **{lulu_stats['avg_placement']:.2f}등**\n"
                        f"순방률: {lulu_stats['top4_rate']:.2f}%"
                    ),
                    "priority": "high",
                })

    # 4~8: 나머지 덱 변동은 요약 리포트에서 확인 가능하므로 별도 알림 안 함
    # (TOP 3 진입, 신규 강력덱, AD덱 후보, 이즈리얼 컨디션 등)

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


# ── 덱 상세 임베드 ───────────────────────────────────────────────────────────

def _champ_name(key: str) -> str:
    """챔피언 API key를 한글 이름으로 변환한다."""
    if key in CHAMPION_DATA:
        return CHAMPION_DATA[key][0]
    # 매핑에 없으면 key에서 추출 (TFT17_Lulu → Lulu)
    name = key.split("_", 1)[-1] if "_" in key else key
    # CamelCase 분리
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    return name


def _champ_cost(key: str) -> int:
    """챔피언 API key에서 코스트를 반환한다."""
    if key in CHAMPION_DATA:
        return CHAMPION_DATA[key][1]
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

    embeds.append({
        "title": "📋 TFT 메타 리포트",
        "description": (
            f"```\n"
            f"시즌    : {meta_info.get('season', 'N/A')}\n"
            f"패치    : {meta_info.get('patch', 'N/A')}\n"
            f"업데이트 : {meta_info.get('updatedAt', 'N/A')}\n"
            f"분석 덱  : {total_decks}개\n"
            f"```"
        ),
        "color": 0x1E90FF,
        "footer": {"text": f"롤체지지 기준 | {now_str}"},
    })

    # ── 임베드 2: 내 덱 현황 (감시 대상) ──
    deck_fields = []
    category_emojis = {"main": "⭐", "ad_alt": "⚔️", "special": "🌀"}
    category_names = {"main": "메인덱", "ad_alt": "AD 대체덱", "special": "특수 상황덱"}

    for category in ["main", "ad_alt", "special"]:
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
    watched_stats = {"main": {}, "ad_alt": {}, "special": {}}

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

    new_state = {
        "meta_info": meta_info,
        "decks_stats": decks_stats_map,
        "top3_top4_names": [s["name"] for s in by_top4[:3]],
        "top3_avg_names": [s["name"] for s in by_avg[:3]],
        "patch_note_urls": [n["url"] for n in patch_notes],
        "patch_detected_at": patch_detected_at,
        "notified_patch_dates": notified_dates,
        "patch_schedules": patch_schedules,
        "is_post_patch": is_post_patch,
        "last_run": datetime.now().isoformat(),
    }
    save_state(new_state)

    # 알림 전송
    embeds = []

    if alerts:
        for alert in alerts:
            # 덱 관련 알림이면 덱 URL 자동 추가
            if "deck_url" not in alert and decks:
                match = re.search(r'\*\*(.+?)\*\*', alert.get("message", ""))
                if match:
                    alert["deck_url"] = _find_deck_url(match.group(1), decks)
            embeds.append(build_alert_embed(alert))

    # 첫 실행, force-alert, 패치 직후, 중요 알림 있을 때만 요약 전송
    if not prev_state or args.force_alert or is_post_patch or alerts:
        summary_embeds = build_summary_embeds(meta_info, all_stats, watched_stats, decks)
        if is_post_patch and summary_embeds:
            summary_embeds[0]["title"] = "📋 TFT 메타 리포트 [🔥 패치 직후 집중 감시]"
            summary_embeds[0]["color"] = 0xFF6600
        embeds.extend(summary_embeds)

    if embeds:
        send_discord(embeds, dry_run=args.dry_run)
        logger.info(f"총 {len(embeds)}개 임베드 {'출력' if args.dry_run else '전송'} 완료")
    else:
        logger.info("변경 사항 없음. 알림을 보내지 않습니다.")

    logger.info("TFT 메타 감시 완료.")


if __name__ == "__main__":
    main()
