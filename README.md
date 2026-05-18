# 🏠 룸메이트 매칭 시스템

**FastAPI 백엔드 + 바닐라 HTML 프론트엔드**로 구성된 해시 기반 룸메이트 매칭 시스템입니다.

```
roommate/
├── backend/
│   ├── main.py          ← FastAPI 애플리케이션 (모든 비즈니스 로직)
│   └── requirements.txt
├── frontend/
│   └── index.html       ← 단일 파일 프론트엔드 (API 호출 기반)
└── README.md
```

---

## 알고리즘 구조

```
[등록]  학번 → hash_fn() → 슬롯 배정 (Separate Chaining)
                            ├── 슬롯 0–9  : Primary Partition  (절대 필터 조건)
                            └── 슬롯 10–29: Secondary Partition (일반 성향)

[매칭]  ① Primary Filter   → 성별/흡연 절대 조건 불일치 제외
        ② Cache GET         → match:{sid} 키 조회 (TTL 60s)
        ③ Weighted Jaccard  → score = Σ w(t)·[t∈A∩B] / Σ w(t)·[t∈A∪B]
                              + 기상/취침 시간 유사도 보정 (±10%)
        ④ Cache SET         → 결과 캐싱
        ⑤ 정렬 & 반환       → score 내림차순
```

---

## 빠른 시작

### 1. 백엔드 실행

```bash
cd backend

# 가상환경 생성 (선택 사항이지만 권장)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt

# 서버 실행
uvicorn main:app --reload --port 8000
```

서버가 뜨면 → http://localhost:8000/docs 에서 Swagger UI로 API를 바로 테스트할 수 있습니다.

### 2. 프론트엔드 열기

```bash
# 별도 설치 없이 브라우저에서 직접 열기
open frontend/index.html          # macOS
start frontend/index.html         # Windows
xdg-open frontend/index.html      # Linux
```

> **주의**: 프론트엔드의 API 주소 기본값은 `http://localhost:8000` 입니다.
> 서버 주소가 다르면 `frontend/index.html` 상단 `const API = '...'` 값을 수정하세요.

---

## API 엔드포인트 요약

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/meta/traits` | 성향 목록 + 현재 가중치 반환 |
| `GET` | `/weights` | 현재 전역 가중치 반환 |
| `PUT` | `/weights` | 가중치 부분 업데이트 (캐시 자동 무효화) |
| `POST` | `/weights/reset` | 가중치 기본값으로 초기화 |
| `POST` | `/users` | 사용자 등록 |
| `GET` | `/users/{sid}` | 단일 사용자 조회 |
| `DELETE` | `/users/{sid}` | 사용자 삭제 |
| `GET` | `/match/{sid}` | 매칭 검색 (캐시 포함) |
| `GET` | `/table` | 해시 테이블 전체 현황 + 통계 |
| `GET` | `/hash/{sid}` | 학번의 해시 슬롯 계산 과정 반환 |
| `GET` | `/cache` | 캐시 항목 목록 조회 |
| `DELETE` | `/cache` | 전체 캐시 삭제 (FLUSHALL) |

### 요청/응답 예시

**사용자 등록 `POST /users`**
```json
{
  "sid": "20231234",
  "name": "홍길동",
  "dept": "컴퓨터공학과",
  "gender": "남",
  "traits": ["early_bird", "clean", "study", "quiet"],
  "pf_gender": "any",
  "pf_smoke": "no",
  "wake_time": 7,
  "sleep_time": 23
}
```

**매칭 결과 `GET /match/20231234`**
```json
{
  "me": { "sid": "20231234", "name": "홍길동", ... },
  "slot": 12,
  "results": [
    {
      "sid": "20231001", "name": "김민준", "score": 0.9142, "score_pct": 91,
      "shared_traits": ["early_bird", "clean", "study"],
      "heavy_shared":  ["early_bird"],
      "unique_traits": ["quiet"]
    },
    ...
  ],
  "cache_status": "MISS",
  "elapsed_ms": 0.84
}
```

**가중치 업데이트 `PUT /weights`**
```json
{ "weights": { "clean": 1.8, "study": 2.0 } }
```

---

## 유효한 성향(trait) 키 목록

| 키 | 라벨 | 기본 가중치 |
|----|------|------------|
| `early_bird` | 아침형 인간 | 1.5 |
| `night_owl` | 야행성 | 1.5 |
| `clean` | 청결 중시 | 1.2 |
| `study` | 방에서 공부 | 1.2 |
| `quiet` | 조용한 환경 | 1.0 |
| `social` | 사교적 | 1.0 |
| `eat_out` | 외식 선호 | 1.0 |
| `drink` | 애주가 | 1.0 |
| `game` | 게임/취미 | 1.0 |
| `share` | 물건 공유 OK | 1.0 |
| `guest` | 손님 초대 OK | 1.0 |
| `fashion` | 패션 | 1.0 |
| `boardgame` | 보드게임 | 1.0 |
| `gym` | 헬스 | 1.0 |
| `soccer` | 축구 | 1.0 |
| `beauty` | 뷰티 | 1.0 |
| `foodie` | 맛집탐방 | 1.0 |

---

## 운영 환경 배포 시 체크리스트

- `main.py`의 `allow_origins=["*"]` → 실제 프론트엔드 도메인으로 교체
- `hash_table` / `cache`를 실제 DB(PostgreSQL 등)와 Redis로 교체
- `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4` 로 멀티 워커 실행
- 프론트엔드 `const API`를 실제 백엔드 URL로 수정
