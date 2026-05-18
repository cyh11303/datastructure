#!/usr/bin/env bash
# ─────────────────────────────────────────────
#  룸메이트 매칭 시스템 — 백엔드 빠른 실행 스크립트
#  사용법:  chmod +x start.sh && ./start.sh
# ─────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"

echo "🏠  룸메이트 매칭 시스템 시작"
echo "────────────────────────────────"

# 가상환경 없으면 생성
if [ ! -d "$VENV_DIR" ]; then
  echo "📦  가상환경 생성 중..."
  python3 -m venv "$VENV_DIR"
fi

# 활성화
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 패키지 설치
echo "📥  패키지 설치 중..."
pip install -q -r "$BACKEND_DIR/requirements.txt"

echo ""
echo "✅  서버 시작: http://localhost:8000"
echo "📖  Swagger UI: http://localhost:8000/docs"
echo "🖥️   프론트엔드: frontend/index.html 을 브라우저에서 열어주세요"
echo "────────────────────────────────"

# 백엔드 실행
cd "$BACKEND_DIR"
uvicorn main:app --reload --host 0.0.0.0 --port 8000
