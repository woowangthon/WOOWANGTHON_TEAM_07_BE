"""CORS 허용 Origin 해석. 환경 변수 CORS_ALLOWED_ORIGINS(쉼표 구분)로만 허용한다.

- 기본값은 빈 값이다. 빈 값이면 CORS 미들웨어를 붙이지 않는다(교차 출처 요청을 허용하지 않는다).
- 와일드카드(*), 경로·쿼리가 있는 값, http(s)가 아닌 스킴은 서버 시작 시 오류다.
- 명시된 Origin에만 쿠키를 허용한다. 변경 요청은 인증 라우터에서 Origin·CSRF를 추가 확인한다.
"""

from urllib.parse import urlsplit

ALLOWED_METHODS = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
ALLOWED_HEADERS = ["Content-Type", "X-CSRF-Token"]


def parse_origins(raw: str) -> list[str]:
    origins: list[str] = []
    for item in raw.split(","):
        value = item.strip().rstrip("/")
        if not value:
            continue
        if "*" in value:
            raise ValueError("CORS_ALLOWED_ORIGINS에 와일드카드(*)를 쓸 수 없습니다. Origin을 명시하세요.")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment:
            raise ValueError(f"CORS Origin 형식이 올바르지 않습니다(예: http://localhost:5173): {value!r}")
        if value not in origins:
            origins.append(value)
    return origins
