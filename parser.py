"""공고 페이지에서 제목, 상세 본문, 공고 관련 링크를 추출합니다."""

import html as html_module
import json
import re
from copy import deepcopy
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


class ParseError(Exception):
    """HTML 파싱 중 오류가 발생했을 때 사용합니다."""


INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
BLOCK_TAGS = {
    "address", "article", "blockquote", "dd", "div", "dl", "dt", "figure",
    "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "li", "main", "ol",
    "p", "section", "table", "tr", "ul",
}
NOISE_TAGS = {
    "button", "footer", "form", "iframe", "nav", "noscript", "script",
    "select", "style", "svg", "template", "textarea",
}
NOISE_CLASS = re.compile(
    r"(?:^|[-_\s])(?:advert|ads|banner|breadcrumb|comment|footer|gnb|lnb|"
    r"menu|navi|pagination|recommend|related|reply|right_cont|share|"
    r"sidebar|social|sns|toolbar)(?:$|[-_\s])",
    re.IGNORECASE,
)
NOTICE_WORDS = re.compile(
    r"공모|모집|접수|참가|응모|신청|기간|자격|시상|주최|주관|문의|"
    r"제출|심사|발표|수상|지원|대상|상금|일정"
)
# '바로가기'나 일반적인 '접수'만으로는 링크를 선택하지 않습니다.
LINK_LABELS = re.compile(
    r"공식\s*홈페이지|주최사\s*홈페이지|홈페이지|공식\s*사이트|"
    r"온라인\s*접수|접수하기|신청하기|참가\s*신청|지원하기|"
    r"신청서|양식\s*다운로드|다운로드|첨부파일|세부요강|공모요강|"
    r"상세\s*안내|참가\s*안내|원문\s*보기",
    re.IGNORECASE,
)
RECOMMENDATION_WORDS = re.compile(
    r"추천 공모전|추천 공고|유사 공고|관련 공고|다른 공모전|"
    r"함께 보면 좋은|인기 순위|인기 대회|인기 대외활동|"
    r"공모전 인기|대외 활동 인기|전체현황",
    re.IGNORECASE,
)
# 추천 영역 '제목'으로 볼 최대 길이. 본문 문장("다양한 분야의 아이디어를 …")에서
# 잘리지 않도록 짧은 줄에서만 본문 끝으로 판단합니다.
RECOMMENDATION_HEADING_MAX = 25
# 파일명이나 다운로드 주소로 첨부파일을 알아봅니다.
FILE_LINK = re.compile(
    r"\.(?:hwp|hwpx|pdf|docx?|xlsx?|pptx?|zip|txt)(?:$|[?#\s])|"
    r"download|filedown|file_down|fileDown|atchFile|attach",
    re.IGNORECASE,
)
# 이미지 공고 판별용
MIN_TEXT_LENGTH = 60
LAYOUT_TAGS = {"html", "body", "main", "article"}
EMBEDDED_FIELD_PATTERN = re.compile(
    r"""["']?(?:description|articleBody|body|detail|content|summary|text)["']?
        \s*[:=]\s*("(?:\\.|[^"\\])*")""",
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)
SOCIAL_HOSTS = {
    "facebook.com", "www.facebook.com",
    "twitter.com", "www.twitter.com",
    "x.com", "www.x.com",
    "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "blog.naver.com", "cafe.daum.net", "band.us",
}
DETAIL_SELECTORS = (
    "[itemprop='articleBody']",
    "#view", ".view",
    "#contest-view", ".contest-view",
    "#contest-detail", ".contest-detail",
    "#detail", ".detail",
    ".view-content", ".view_content",
    ".article-content", ".content-body", ".detail-content",
    ".entry-content", ".post-content",
    "article", "[role='main']", "main",
    "#content", "#contents",
)
DETAIL_SELECTOR_CSS = ", ".join(DETAIL_SELECTORS)


def _clean_text(text: str) -> str:
    text = html_module.unescape(text)
    text = INVISIBLE.sub("", text).replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [
        re.sub(r"[ \t\f\v]+", " ", line).strip()
        for line in text.split("\n")
    ]

    result = []
    previous = None

    for line in lines:
        if not line:
            continue
        if len(line) <= RECOMMENDATION_HEADING_MAX and RECOMMENDATION_WORDS.search(line):
            break
        if line != previous:
            result.append(line)
        previous = line

    return "\n".join(result)


def _extract_text(element: Tag) -> str:
    """HTML 요소의 텍스트를 블록별 줄바꿈으로 추출합니다."""
    parts = []

    def visit(node):
        # HTML 주석(예: <!-- //header -->)을 일반 글로 취급하지 않습니다.
        if isinstance(node, Comment):
            return

        if isinstance(node, NavigableString):
            parts.append(str(node))
            return

        if not isinstance(node, Tag) or node.name in NOISE_TAGS:
            return

        if node.name == "br":
            parts.append("\n")
            return

        if node.name == "table":
            # 중첩 표의 행이 두 번 나오지 않도록 이 표에 직접 속한 행만 읽습니다.
            for row in node.find_all("tr"):
                if row.find_parent("table") is not node:
                    continue
                cells = [
                    " ".join(cell.stripped_strings)
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                if cells:
                    parts.append("\n" + " | ".join(cells) + "\n")
            return

        is_block = node.name in BLOCK_TAGS
        if is_block:
            parts.append("\n")

        for child in node.children:
            visit(child)

        if is_block:
            parts.append("\n")

    visit(element)
    return _clean_text("".join(parts))


def _fragment_to_text(value: str) -> str:
    """본문 문자열에 HTML 태그가 포함된 경우 태그를 제거합니다."""
    if "<" in value and ">" in value:
        try:
            fragment = BeautifulSoup(value, "html.parser")
            for tag in fragment.find_all(list(NOISE_TAGS)):
                tag.decompose()

            if fragment.body:
                return _extract_text(fragment.body)
            return _extract_text(fragment)
        except Exception:
            pass

    return _clean_text(value)


def _page_title(soup: BeautifulSoup) -> str:
    selectors = (
        ("meta[property='og:title']", "content"),
        ("main h1", None),
        ("article h1", None),
        ("h1", None),
        ("title", None),
    )

    for selector, attribute in selectors:
        tag = soup.select_one(selector)
        if not tag:
            continue

        value = tag.get(attribute, "") if attribute else tag.get_text(" ", strip=True)
        title = " ".join(str(value).split())

        if title:
            return title

    return ""


def _remove_noise(soup: BeautifulSoup) -> BeautifulSoup:
    """메뉴, 추천, SNS, 페이지 주변 영역을 복사본에서 제거합니다."""
    document = deepcopy(soup)

    # 주석은 출력 텍스트에 섞이지 않도록 제거합니다.
    for comment in document.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    total_length = max(len(document.get_text(strip=True)), 1)

    for tag in list(document.find_all(True)):
        if tag.decomposed or tag.parent is None:
            continue

        if tag.name in NOISE_TAGS or tag.name in {"header", "input", "aside"}:
            tag.decompose()
            continue

        if tag.name in LAYOUT_TAGS:
            continue

        attributes = (
            str(tag.get("id", "")) + " " + " ".join(tag.get("class", []))
        )
        if not NOISE_CLASS.search(attributes):
            continue

        # class="layout has-sidebar" 같은 래퍼에 본문 전체가 들어 있는 경우가 있어
        # 본문 영역을 품고 있거나 페이지 글의 절반 이상을 차지하면 지우지 않습니다.
        if tag.select_one(DETAIL_SELECTOR_CSS):
            continue
        if len(tag.get_text(strip=True)) >= total_length * 0.5:
            continue
        tag.decompose()

    return document


def _score(text: str) -> float:
    text = _clean_text(text)

    if len(text) < 60:
        return -1

    indicators = len(NOTICE_WORDS.findall(text))
    if indicators < 2:
        return -1

    return min(len(text), 12000) / 100 + min(indicators, 30) * 2


def _candidate_elements(document: BeautifulSoup) -> list[Tag]:
    elements = []
    seen = set()

    for selector in DETAIL_SELECTORS:
        for element in document.select(selector):
            if id(element) not in seen:
                seen.add(id(element))
                elements.append(element)

    if document.body and id(document.body) not in seen:
        elements.append(document.body)

    return elements


def _best_content_element(document: BeautifulSoup) -> tuple[str, Tag | None]:
    """페이지 안에서 공고 본문으로 보이는 영역을 선택합니다."""
    best_text = ""
    best_element = None
    best_score = float("-inf")

    for element in _candidate_elements(document):
        text = _extract_text(element)
        score = _score(text)
        if score < 0:
            continue

        # 메뉴처럼 링크만 많은 영역은 감점합니다. 첨부파일 링크가 몇 개 있는
        # 짧은 공고가 통째로 탈락하지 않도록 감점 폭을 제한합니다.
        link_text_length = sum(
            len(anchor.get_text(" ", strip=True))
            for anchor in element.select("a")
        )
        link_ratio = link_text_length / max(len(text), 1)
        if link_ratio > 0.5:
            score -= link_ratio * 60

        if element.name == "article" or element.get("itemprop") == "articleBody":
            score += 15

        if element.name == "body":
            score -= 30

        if score > best_score:
            best_text = text
            best_element = element
            best_score = score

    return best_text, best_element


def _strings_from_json(value) -> list[str]:
    found = []

    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            found.extend(_strings_from_json(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_strings_from_json(child))

    return found


def _embedded_candidates(raw_html: str, soup: BeautifulSoup) -> list[str]:
    """JSON·스크립트·메타 정보에 포함된 본문 후보를 찾습니다."""
    candidates = []

    for match in EMBEDDED_FIELD_PATTERN.finditer(raw_html):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        if isinstance(value, str):
            text = _fragment_to_text(value)
            if _score(text) >= 0:
                candidates.append(text)

    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw:
            continue

        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            continue

        for value in _strings_from_json(data):
            text = _fragment_to_text(value)
            if _score(text) >= 0:
                candidates.append(text)

    for meta in soup.find_all("meta"):
        value = meta.get("content")
        if isinstance(value, str):
            text = _fragment_to_text(value)
            if _score(text) >= 0:
                candidates.append(text)

    return candidates


def _link_is_in_noise_area(anchor: Tag) -> bool:
    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue

        if parent.name in {"nav", "header", "footer", "aside"}:
            return True

        attributes = (
            str(parent.get("id", "")) + " " + " ".join(parent.get("class", []))
        )
        if NOISE_CLASS.search(attributes):
            return True

    return False


def _collect_relevant_links(
    content_element: Tag | None,
    document: BeautifulSoup,
    page_url: str,
) -> list[str]:
    """공고 본문 영역에서 이름이 명확한 신청·홈페이지·자료 링크만 수집합니다."""
    if content_element is not None and content_element.name != "body":
        root = content_element
    else:
        root = document.body

    if root is None:
        return []

    source_url = page_url.rstrip("/")
    links = []
    seen = set()

    for anchor in root.select("a[href]"):
        if _link_is_in_noise_area(anchor):
            continue

        href = anchor.get("href", "").strip()
        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:")
        ):
            continue

        absolute_url = urljoin(page_url, href) if page_url else href
        if absolute_url.rstrip("/") == source_url:
            continue

        host = urlparse(absolute_url).netloc.lower()
        if host in SOCIAL_HOSTS:
            continue

        label = " ".join(anchor.stripped_strings).strip()

        if not label:
            label = (
                anchor.get("aria-label", "")
                or anchor.get("title", "")
            ).strip()

        if not label:
            image = anchor.find("img")
            if image:
                label = (
                    image.get("alt", "")
                    or image.get("title", "")
                ).strip()

        if not label or RECOMMENDATION_WORDS.search(label):
            continue

        # 카드 제목의 '접수', 일반 메뉴의 '바로가기'는 링크로 취급하지 않습니다.
        # 단, '2026_공모안내.hwp'처럼 파일명만 있는 첨부파일 링크는 포함합니다.
        if not (LINK_LABELS.search(label) or FILE_LINK.search(label) or FILE_LINK.search(href)):
            continue

        item = f"{label}: {absolute_url}"
        if item not in seen:
            seen.add(item)
            links.append(item)

    return links[:15]


def _content_images(content_element: Tag | None, page_url: str) -> list[str]:
    """본문 영역의 이미지 주소를 찾습니다(이미지로만 된 공고 판별용)."""
    if content_element is None:
        return []

    images = []
    for image in content_element.find_all("img"):
        src = (image.get("src") or image.get("data-src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        absolute = urljoin(page_url, src) if page_url else src
        if absolute not in images:
            images.append(absolute)
    return images


def parse_html(html: str, page_url: str = "") -> tuple[str, str]:
    """HTML에서 페이지 제목과 공고 본문·관련 링크를 반환합니다."""
    if not isinstance(html, str):
        raise ParseError(
            f"HTML은 문자열(str)이어야 합니다. 받은 타입: {type(html).__name__}"
        )

    if not html.strip():
        return "", ""

    try:
        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)
        document = _remove_noise(soup)

        body, content_element = _best_content_element(document)

        # HTML 안의 JSON에 본문이 더 자세히 들어 있으면 해당 내용을 사용합니다.
        for candidate in _embedded_candidates(html, soup):
            if _score(candidate) > _score(body):
                body = candidate

        if title:
            lines = body.splitlines()
            if lines and lines[0].strip() == title:
                body = "\n".join(lines[1:]).strip()

        # 글 대신 포스터 이미지로만 올라온 공고는 AI에 보내도 빈 결과가 나오므로
        # 비용을 쓰기 전에 알려줍니다.
        if len(body) < MIN_TEXT_LENGTH:
            if content_element is None:
                content_element = document.select_one(DETAIL_SELECTOR_CSS) or document.body
            if _content_images(content_element, page_url):
                raise ParseError(
                    "공고 내용이 글이 아닌 이미지(포스터)로 되어 있어 읽을 수 없어요. "
                    "공고문 첨부파일이나 다른 공고 페이지 주소로 시도해 주세요."
                )

        links = _collect_relevant_links(
            content_element,
            document,
            page_url,
        )

        if links:
            body += "\n\n공고 관련 링크\n" + "\n".join(links)

        return title, body.strip()

    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(
            f"HTML 파싱 중 오류가 발생했습니다: {type(exc).__name__}: {exc}"
        ) from exc