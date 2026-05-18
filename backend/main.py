"""
룸메이트 매칭 시스템 — FastAPI 백엔드
======================================
Hash Table  : 별도 체이닝(Separate Chaining), 슬롯 30개
              슬롯 0–9  → Primary Partition  (절대 필터 조건 보유 사용자)
              슬롯 10–29 → Secondary Partition (일반 성향 사용자)
Similarity  : Weighted Jaccard + 시간 유사도 보정
Cache       : In-process dict (TTL 60 s) — Redis 시뮬레이션
"""

from __future__ import annotations

import time
import math
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# ── 상수 ──────────────────────────────────────────────────────────────────────
HASH_SIZE  = 30
CACHE_TTL  = 60          # seconds
PRIMARY_SLOTS = range(10)  # 슬롯 0–9

# 기본 가중치 (전역; /weights PUT으로 덮어씀)
DEFAULT_WEIGHTS: dict[str, float] = {
    "early_bird": 1.5, "night_owl": 1.5, "quiet":  1.0, "social":  1.0,
    "clean":      1.2, "eat_out":   1.0, "drink":  1.0, "study":   1.2,
    "game":       1.0, "share":     1.0, "guest":  1.0, "fashion": 1.0,
    "boardgame":  1.0, "gym":       1.0, "soccer": 1.0, "beauty":  1.0,
    "foodie":     1.0,
}

VALID_TRAITS = set(DEFAULT_WEIGHTS.keys())

TRAIT_LABELS: dict[str, str] = {
    "early_bird": "아침형",    "night_owl": "야행성",    "quiet":     "조용한 환경",
    "social":     "사교적",    "clean":     "청결 중시", "eat_out":   "외식 선호",
    "drink":      "애주가",    "study":     "방에서 공부","game":      "게임/취미",
    "share":      "물건 공유 OK","guest":   "손님 초대 OK","fashion":  "패션",
    "boardgame":  "보드게임",  "gym":       "헬스",      "soccer":    "축구",
    "beauty":     "뷰티",      "foodie":    "맛집탐방",
}

# ── 인메모리 저장소 ────────────────────────────────────────────────────────────
hash_table: list[list[dict]] = [[] for _ in range(HASH_SIZE)]
cache: dict[str, dict] = {}          # key → {"data": [...], "exp": float}
weights: dict[str, float] = dict(DEFAULT_WEIGHTS)

# ── FastAPI 앱 ─────────────────────────────────────────────────────────────────
app = FastAPI(title="룸메이트 매칭 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 개발 환경 — 운영 시 프론트엔드 origin으로 교체
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic 스키마 ────────────────────────────────────────────────────────────
class UserIn(BaseModel):
    sid:       str
    name:      str
    dept:      Optional[str] = ""
    gender:    Optional[str] = ""
    traits:    list[str]
    pf_gender: str = "any"
    pf_smoke:  str = "any"
    wake_time:  Optional[int] = None   # 5–11 (오전 시각)
    sleep_time: Optional[int] = None   # 22–27 (취침 시각, 24+ → 익일)

    @field_validator("sid")
    @classmethod
    def sid_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("학번은 6자 이상이어야 합니다")
        return v.strip()

    @field_validator("traits")
    @classmethod
    def traits_valid(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("성향을 2개 이상 선택해야 합니다")
        invalid = set(v) - VALID_TRAITS
        if invalid:
            raise ValueError(f"유효하지 않은 성향: {invalid}")
        return v


class WeightsIn(BaseModel):
    weights: dict[str, float]

    @field_validator("weights")
    @classmethod
    def weights_range(cls, v: dict[str, float]) -> dict[str, float]:
        for key, val in v.items():
            if key not in VALID_TRAITS:
                raise ValueError(f"유효하지 않은 성향 키: {key}")
            if not (1.0 <= val <= 3.0):
                raise ValueError(f"가중치는 1.0–3.0 범위여야 합니다 (받은 값: {key}={val})")
        return v


# ── 해시 함수 ─────────────────────────────────────────────────────────────────
def hash_fn(sid: str) -> int:
    """다항 롤링 해시: h = Σ(charCode[i] × 31) % HASH_SIZE"""
    h = 0
    for ch in sid:
        h = (h * 31 + ord(ch)) % HASH_SIZE
    return h


# ── 캐시 유틸 ─────────────────────────────────────────────────────────────────
def cache_get(key: str):
    entry = cache.get(key)
    if entry is None:
        return None, "MISS"
    if time.time() > entry["exp"]:
        del cache[key]
        return None, "EXPIRED"
    ttl = int(entry["exp"] - time.time())
    return entry["data"], f"HIT (TTL {ttl}s)"


def cache_set(key: str, data: list) -> None:
    cache[key] = {"data": data, "exp": time.time() + CACHE_TTL}


def cache_delete(key: str) -> None:
    cache.pop(key, None)


def cache_flush() -> None:
    cache.clear()


# ── 유사도 계산 ───────────────────────────────────────────────────────────────
def time_similarity(t1: Optional[int], t2: Optional[int]) -> float:
    """시간 유사도: 4시간 차이를 기준으로 선형 감소. 미입력 시 패널티 없음(1.0)."""
    if t1 is None or t2 is None:
        return 1.0
    diff = abs(t1 - t2)
    return max(0.0, 1.0 - diff / 4.0)


def passes_filter(me: dict, other: dict) -> bool:
    """Primary Filter: 성별 선호 + 흡연 여부 절대 조건 체크."""
    if me["pf_gender"] != "any" and other.get("gender") and other["gender"] != me["pf_gender"]:
        return False
    if me["pf_smoke"] == "no" and "smoke" in other["traits"]:
        return False
    if me["pf_smoke"] == "yes" and "smoke" not in other["traits"]:
        return False
    return True


def weighted_jaccard(a: dict, b: dict) -> float:
    """
    가중 Jaccard 유사도:
        score = Σ w(t)·[t∈A∩B] / Σ w(t)·[t∈A∪B]
    + 기상/취침 시간 보정 (±10%)
    """
    union = set(a["traits"]) | set(b["traits"])
    inter_w = sum(
        weights.get(t, 1.0)
        for t in union
        if t in a["traits"] and t in b["traits"]
    )
    union_w = sum(weights.get(t, 1.0) for t in union)

    base = inter_w / union_w if union_w > 0 else 0.0

    # 대조 성향 패널티: 한 명은 아침형, 다른 한 명은 야행성이면 감점
    a_traits, b_traits = set(a["traits"]), set(b["traits"])
    opposing_penalty = 0.0
    if ("early_bird" in a_traits and "night_owl" in b_traits) or \
       ("night_owl" in a_traits and "early_bird" in b_traits):
        opposing_penalty = 0.15  # 15% 감점

    wake_sim  = time_similarity(a.get("wake_time"),  b.get("wake_time"))
    sleep_sim = time_similarity(a.get("sleep_time"), b.get("sleep_time"))
    time_bonus = ((wake_sim + sleep_sim) / 2 - 0.5) * 0.2   # -0.10 ~ +0.10

    return min(1.0, max(0.0, base - opposing_penalty + time_bonus))


# ── 헬퍼: 사용자 조회 ─────────────────────────────────────────────────────────
def find_user(sid: str) -> Optional[dict]:
    slot = hash_fn(sid)
    for user in hash_table[slot]:
        if user["sid"] == sid:
            return user
    return None


def all_users() -> list[dict]:
    return [u for slot in hash_table for u in slot]


# ── API: 가중치 ───────────────────────────────────────────────────────────────
@app.get("/weights")
def get_weights():
    """현재 전역 가중치 반환."""
    return {"weights": weights}


@app.put("/weights")
def update_weights(body: WeightsIn):
    """가중치 부분 업데이트. 변경 시 전체 캐시 무효화."""
    weights.update(body.weights)
    cache_flush()
    return {"weights": weights, "cache_flushed": True}


@app.post("/weights/reset")
def reset_weights():
    """가중치를 기본값으로 초기화."""
    weights.update(DEFAULT_WEIGHTS)
    cache_flush()
    return {"weights": weights}


# ── API: 사용자 등록 ──────────────────────────────────────────────────────────
@app.post("/users", status_code=201)
def register_user(body: UserIn):
    """
    사용자 등록.
    - 학번을 해시해 슬롯 배정 (Separate Chaining)
    - 이미 등록된 학번이면 409 반환
    """
    slot = hash_fn(body.sid)
    if any(u["sid"] == body.sid for u in hash_table[slot]):
        raise HTTPException(status_code=409, detail=f"이미 등록된 학번입니다: {body.sid}")

    user = body.model_dump()
    hash_table[slot].append(user)
    cache_delete(f"match:{body.sid}")

    return {
        "message":  f"{body.name}님 등록 완료",
        "sid":      body.sid,
        "slot":     slot,
        "partition": "Primary" if slot in PRIMARY_SLOTS else "Secondary",
    }


@app.get("/users/{sid}")
def get_user(sid: str):
    """단일 사용자 조회."""
    user = find_user(sid)
    if not user:
        raise HTTPException(status_code=404, detail=f"사용자를 찾을 수 없습니다: {sid}")
    slot = hash_fn(sid)
    return {**user, "slot": slot, "partition": "Primary" if slot in PRIMARY_SLOTS else "Secondary"}


@app.delete("/users/{sid}")
def delete_user(sid: str):
    """사용자 삭제."""
    slot = hash_fn(sid)
    before = len(hash_table[slot])
    hash_table[slot] = [u for u in hash_table[slot] if u["sid"] != sid]
    if len(hash_table[slot]) == before:
        raise HTTPException(status_code=404, detail=f"사용자를 찾을 수 없습니다: {sid}")
    cache_delete(f"match:{sid}")
    return {"message": f"{sid} 삭제 완료"}


# ── API: 매칭 ─────────────────────────────────────────────────────────────────
@app.get("/match/{sid}")
def find_matches(sid: str):
    """
    매칭 파이프라인:
        ① Primary Filter → ② Cache GET → ③ Weighted Jaccard → ④ Cache SET → ⑤ 반환
    """
    t0 = time.perf_counter()
    me = find_user(sid)
    if not me:
        raise HTTPException(status_code=404, detail=f"사용자를 찾을 수 없습니다: {sid}")

    cache_key = f"match:{sid}"
    cached_data, cache_status = cache_get(cache_key)

    if cached_data is not None:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "me":           me,
            "slot":         hash_fn(sid),
            "results":      cached_data,
            "cache_status": cache_status,
            "elapsed_ms":   elapsed_ms,
        }

    # Primary Filter
    candidates = [u for u in all_users() if u["sid"] != sid]
    passed     = [u for u in candidates if passes_filter(me, u)]

    # Weighted Jaccard 계산 및 정렬
    scored = sorted(
        [{"user": u, "score": round(weighted_jaccard(me, u), 4)} for u in passed],
        key=lambda x: x["score"],
        reverse=True,
    )

    # 결과 직렬화 (캐시 & 응답용)
    results = [
        {
            **item["user"],
            "score":       item["score"],
            "score_pct":   round(item["score"] * 100),
            "slot":        hash_fn(item["user"]["sid"]),
            "shared_traits": [t for t in item["user"]["traits"] if t in me["traits"]],
            "unique_traits": [t for t in item["user"]["traits"] if t not in me["traits"]],
            "heavy_shared":  [
                t for t in item["user"]["traits"]
                if t in me["traits"] and weights.get(t, 1.0) > 1.0
            ],
        }
        for item in scored
    ]

    cache_set(cache_key, results)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "me":           me,
        "slot":         hash_fn(sid),
        "results":      results,
        "cache_status": cache_status,
        "elapsed_ms":   elapsed_ms,
    }


# ── API: 해시 테이블 현황 ──────────────────────────────────────────────────────
@app.get("/table")
def get_table():
    """해시 테이블 전체 현황 + 통계."""
    users = all_users()
    used_slots = sum(1 for slot in hash_table if slot)
    slots_detail = [
        {
            "slot":      i,
            "partition": "Primary" if i in PRIMARY_SLOTS else "Secondary",
            "count":     len(slot),
            "users":     [
                {"sid": u["sid"], "name": u["name"], "dept": u.get("dept",""),
                 "chain_index": idx}
                for idx, u in enumerate(slot)
            ],
        }
        for i, slot in enumerate(hash_table)
        if slot   # 비어있는 슬롯 제외
    ]
    return {
        "hash_size":   HASH_SIZE,
        "total_users": len(users),
        "used_slots":  used_slots,
        "load_factor": round(len(users) / HASH_SIZE * 100),
        "slots":       slots_detail,
    }


# ── API: 해시 계산기 ──────────────────────────────────────────────────────────
@app.get("/hash/{sid}")
def compute_hash(sid: str):
    """학번의 해시 슬롯 및 중간 계산 과정 반환."""
    h, steps = 0, []
    for i, ch in enumerate(sid):
        c = ord(ch)
        h = (h * 31 + c) % HASH_SIZE
        steps.append({"index": i, "char": ch, "code": c, "h": h})
    return {
        "sid":       sid,
        "slot":      h,
        "partition": "Primary" if h in PRIMARY_SLOTS else "Secondary",
        "steps":     steps,
    }


# ── API: 캐시 관리 ────────────────────────────────────────────────────────────
@app.get("/cache")
def get_cache():
    """현재 캐시 항목 목록."""
    now = time.time()
    return {
        "size": len(cache),
        "items": [
            {"key": k, "count": len(v["data"]), "ttl": max(0, int(v["exp"] - now))}
            for k, v in cache.items()
        ],
    }


@app.delete("/cache")
def flush_cache():
    """전체 캐시 삭제 (FLUSHALL)."""
    cache_flush()
    return {"message": "FLUSHALL OK", "size": 0}


# ── API: 메타 / 트레이트 목록 ─────────────────────────────────────────────────
@app.get("/meta/traits")
def get_traits():
    """유효한 성향 목록 + 현재 가중치."""
    return {
        "traits": [
            {"key": k, "label": TRAIT_LABELS.get(k, k), "weight": weights.get(k, 1.0)}
            for k in VALID_TRAITS
        ]
    }


# ── 샘플 데이터 초기화 ────────────────────────────────────────────────────────
def _seed():
    samples = [
        {"sid":"20231001","name":"김민준","dept":"컴퓨터공학과","gender":"남",
         "traits":["early_bird","clean","study","quiet"],"pf_gender":"any","pf_smoke":"no","wake_time":7,"sleep_time":23},
        {"sid":"20231002","name":"이서연","dept":"경영학과","gender":"여",
         "traits":["early_bird","clean","quiet","share","foodie"],"pf_gender":"여","pf_smoke":"no","wake_time":6,"sleep_time":23},
        {"sid":"20231003","name":"박지훈","dept":"전기공학과","gender":"남",
         "traits":["night_owl","game","social","drink","soccer"],"pf_gender":"any","pf_smoke":"any","wake_time":10,"sleep_time":26},
        {"sid":"20231004","name":"최수아","dept":"심리학과","gender":"여",
         "traits":["night_owl","social","eat_out","guest","beauty"],"pf_gender":"any","pf_smoke":"no","wake_time":9,"sleep_time":25},
        {"sid":"20231005","name":"정도윤","dept":"수학과","gender":"남",
         "traits":["early_bird","clean","study","quiet","share"],"pf_gender":"any","pf_smoke":"no","wake_time":7,"sleep_time":23},
        {"sid":"20231006","name":"한지아","dept":"디자인학과","gender":"여",
         "traits":["early_bird","clean","study","fashion","beauty","foodie"],"pf_gender":"여","pf_smoke":"no","wake_time":8,"sleep_time":24},
        {"sid":"20231007","name":"오준혁","dept":"체육학과","gender":"남",
         "traits":["early_bird","gym","soccer","social","eat_out"],"pf_gender":"any","pf_smoke":"any","wake_time":6,"sleep_time":22},
    ]
    for u in samples:
        slot = hash_fn(u["sid"])
        if not any(x["sid"] == u["sid"] for x in hash_table[slot]):
            hash_table[slot].append(u)


_seed()
