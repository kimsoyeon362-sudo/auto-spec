"""공고 URL에 접속해 페이지 HTML을 가져옵니다."""

import re
from urllib.parse import urlparse

import requests

# 중간 인증서를 빠뜨린 사이트(국내 사이트에 흔함)는 브라우저에서는 열리지만 requests에서는
# SSL 오류가 납니다. truststore를 쓰면 Windows·macOS 인증서 저장소로 검사해서
# 브라우저와 같은 결과가 나옵니다. (pip install truststore, Python 3.10 이상)
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    truststore = None


# "Mozilla/5.0"만 보내면 봇으로 보고 막는 사이트가 많아 실제 브라우저 값에 가깝게 보냅니다.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
MAX_BYTES = 5 * 1024 * 1024  # 5MB 넘는 페이지는 공고가 아닐 가능성이 큼
META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w\-]+)""", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """앞뒤 공백을 지우고, www.example.com처럼 입력하면 https://를 붙입니다."""
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def _decode(response: requests.Response) -> str:
    """한글이 깨지지 않도록 HTML 안의 charset을 먼저 보고 인코딩을 정합니다."""
    content = response.content
    header_has_charset = "charset" in response.headers.get("Content-Type", "").lower()

    encoding = response.encoding if header_has_charset else None
    if not encoding:
        match = META_CHARSET.search(content[:4096])
        if match:
            encoding = match.group(1).decode("ascii", "ignore")
    if not encoding:
        encoding = response.apparent_encoding or "utf-8"

    # EUC-KR 페이지에 cp949 전용 글자가 섞여 깨지는 경우가 많아 cp949로 읽습니다.
    if encoding.lower().replace("_", "-") in ("euc-kr", "ks-c-5601-1987", "ksc5601"):
        encoding = "cp949"

    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _get(url: str) -> requests.Response:
    """브라우저처럼 요청합니다. 403이면 첫 화면을 먼저 열어 쿠키를 받은 뒤 한 번 더 시도합니다."""
    parsed = urlparse(url)
    home = f"{parsed.scheme}://{parsed.netloc}/"
    with requests.Session() as session:
        session.headers.update(HEADERS)
        response = session.get(url, headers={"Referer": home}, timeout=15)
        if response.status_code == 403:
            session.get(home, timeout=15)
            response = session.get(url, headers={"Referer": home}, timeout=15)
        return response


def fetch_html(url: str) -> str:
    """URL에 접속해 페이지 HTML을 반환한다."""
    url = normalize_url(url)
    parsed_url = urlparse(url)

    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("http:// 또는 https://로 시작하는 올바른 URL을 입력해 주세요.")

    try:
        response = _get(url)
        response.raise_for_status()

    except requests.exceptions.Timeout as error:
        raise RuntimeError("페이지 접속 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.") from error

    except requests.exceptions.HTTPError as error:
        # Response는 4xx/5xx일 때 False로 평가되므로 반드시 'is not None'으로 확인합니다.
        status_code = error.response.status_code if error.response is not None else "알 수 없음"
        hint = {
            403: "사이트가 자동 접속을 막고 있어요.",
            404: "페이지를 찾을 수 없어요. 주소를 확인해 주세요.",
            429: "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.",
        }.get(status_code, "")
        raise RuntimeError(f"페이지 요청에 실패했어요 (상태 코드 {status_code}). {hint}".strip()) from error

    except requests.exceptions.SSLError as error:
        if truststore is None:
            hint = "터미널에서 'pip install truststore'를 실행한 뒤 다시 시도해 주세요."
        else:
            hint = "사이트 인증서 자체에 문제가 있어 안전하게 접속할 수 없어요."
        raise RuntimeError(f"사이트의 보안 인증서를 확인하지 못했어요. {hint}") from error

    except requests.exceptions.RequestException as error:
        raise RuntimeError("페이지에 접속하지 못했어요. URL과 인터넷 연결을 확인해 주세요.") from error

    content_type = response.headers.get("Content-Type", "").lower()
    if content_type and "html" not in content_type and "xml" not in content_type:
        kind = "PDF 파일" if "pdf" in content_type else "웹 페이지가 아닌 파일"
        raise RuntimeError(f"입력한 주소는 {kind}이에요. 공고 페이지 주소를 입력해 주세요.")

    if len(response.content) > MAX_BYTES:
        raise RuntimeError("페이지가 너무 커서 분석할 수 없어요.")

    return _decode(response)
