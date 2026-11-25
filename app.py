"""auto-spec - 공모전 공고 정리기 (웹 버전, Streamlit).

데스크톱 버전(ui.py)과 같은 화면 구조를 웹으로 옮긴 파일입니다.
  - 위: 공고 URL 입력 + 분석 버튼
  - 왼쪽: 저장한 공고 목록 (검색, 마감일 색상)
  - 오른쪽: 공고 상세 (마감 표시, 버튼, 항목별 정리, 수정)
  - 아래: 상태 표시줄

분석 로직은 fetcher → parser → extractor를 그대로 사용합니다.

로컬 실행:   streamlit run app.py
배포:        Streamlit Community Cloud (Secrets에 OPENAI_API_KEY 등록)
"""

import html
import json
import os
import re
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

APP_NAME = "공모전 공고 정리기"
KST = ZoneInfo("Asia/Seoul")  # 서버 시간이 UTC라서 D-day는 한국 날짜로 계산

st.set_page_config(page_title=f"auto-spec · {APP_NAME}", page_icon="📋", layout="wide")


# ── API 키 준비 ───────────────────────────────────────────
# 배포 환경에는 .env가 없으므로 Streamlit Secrets 값을 환경 변수로 옮깁니다.
# (extractor.py는 os.getenv로 읽기 때문에 수정 없이 그대로 동작)
def load_secrets_to_env() -> None:
    try:
        for key in ("OPENAI_API_KEY", "OPENAI_MODEL"):
            if key in st.secrets and not os.getenv(key):
                os.environ[key] = str(st.secrets[key])
    except FileNotFoundError:  # 로컬에서 secrets.toml 없이 .env만 쓰는 경우
        pass


load_secrets_to_env()

from extractor import NOT_STATED, ExtractError, extract_info, find_urls  # noqa: E402
from fetcher import fetch_html, normalize_url  # noqa: E402
from parser import ParseError, parse_html  # noqa: E402


# ── 색·항목 정의 (ui.py와 동일) ──────────────────────────
MUTED = "#5F6368"
LINK = "#005FB8"
URGENT = "#C42B1C"
SOON = "#9D5D00"
CLOSED = "#8A8A8A"
TEXT = "#1C1C1C"

LABELS = {
    "competition_name": "공모전 이름",
    "hosting_organization": "주최·주관",
    "deadline": "마감일",
    "participant_eligibility": "참가 자격",
    "participation_type": "참가 방식",
    "max_team_members": "팀 최대 인원",
    "contest_period": "접수 기간",
    "major_schedule": "심사·발표 일정",
    "contest_topics": "공모 주제",
    "prize_details": "시상 내역",
    "submission_method": "접수 방법",
    "required_documents": "제출 서류",
    "official_homepage": "공식 홈페이지",
    "related_links": "관련 링크",
    "contact_information": "문의처",
    "other_conditions": "유의 사항",
}
SECTIONS = [
    ("참가", ["participant_eligibility", "participation_type", "max_team_members"]),
    ("일정", ["contest_period", "major_schedule"]),
    ("주제와 시상", ["contest_topics", "prize_details"]),
    ("접수", ["submission_method", "required_documents"]),
    ("링크", ["official_homepage", "related_links"]),
    ("문의 및 기타", ["contact_information", "other_conditions"]),
]
EDIT_SECTIONS = [("기본 정보", ["competition_name", "hosting_organization", "deadline"])] + SECTIONS
SINGLE_LINE = {"competition_name", "deadline", "participation_type", "max_team_members"}
LINK_FIELDS = {"official_homepage", "related_links"}
APPLY_WORDS = ("온라인 접수", "접수하기", "접수 페이지", "참가 신청", "참가신청", "신청하기", "지원하기", "apply")
STEPS = ["페이지에 접속하는 중", "본문을 정리하는 중", "공고 정보를 추출하는 중"]

SAMPLE = {
    "id": None, "url": "", "title": "", "saved_at": None, "sample": True,
    "info": {
        "competition_name": "2026 AI 콘텐츠 공모전",
        "hosting_organization": "주최: 중앙대학교 / 주관: AI융합연구소",
        "deadline": "2026-12-31",
        "participant_eligibility": "대학생 및 일반인",
        "participation_type": "개인 또는 팀",
        "max_team_members": "5명",
        "contest_period": "2026.10.20(화) ~ 2026.12.31(목) 18:00",
        "major_schedule": "1차 심사: 2027.01.05\n결과 발표: 2027.01.15\n시상식: 2027.01.22",
        "contest_topics": "AI를 활용한 창의적인 콘텐츠 제작 및 아이디어 제안",
        "prize_details": "대상 1팀: 100만원\n최우수상 2팀: 각 50만원\n우수상 3팀: 각 20만원",
        "submission_method": "공식 홈페이지 온라인 접수",
        "required_documents": "참가신청서, 작품 파일(PDF 또는 MP4), 재학증명서",
        "official_homepage": "https://example.org/contest",
        "related_links": "온라인 접수하기: https://example.org/contest/apply\n공모요강.hwp: https://example.org/files/guide.hwp",
        "contact_information": "contest@example.org / 02-000-0000",
        "other_conditions": "수상작의 저작권은 응모자에게 있으며, 주최 측은 홍보 목적으로 사용할 수 있습니다.",
    },
}


# ── 화면 스타일 ───────────────────────────────────────────
# UI 수정은 주로 여기(CSS)와 아래 render_* 함수들에서 하면 됩니다.
BORDER = "#E3E3E3"
st.markdown(
    f"""
    <style>
    header[data-testid="stHeader"] {{ height: 0; }}
    .block-container {{ padding-top: 2.6rem; padding-bottom: 4rem; max-width: 1280px; }}
    .muted {{ color: {MUTED}; }}
    .small {{ font-size: 0.85rem; }}

    /* 헤더 */
    .brand {{ display: flex; align-items: baseline; gap: 0.8rem; margin-bottom: 0.9rem; }}
    .brand .logo {{ font-size: 1.55rem; font-weight: 800; letter-spacing: -0.02em; color: {TEXT}; }}
    .brand .logo span {{ color: {LINK}; }}
    .brand .tagline {{ color: {MUTED}; font-size: 0.95rem; }}
    .toolbar-label {{ padding-top: 0.45rem; font-weight: 500; }}

    .section-title {{ font-size: 1.05rem; font-weight: 700; margin: 0 0 0.5rem; color: {TEXT}; }}

    /* 목록 카드: 카드 전체를 투명 버튼이 덮어서 클릭되게 함 */
    [class*="st-key-card"] {{ position: relative; background: #FFFFFF; border: 1px solid {BORDER};
        border-radius: 8px; padding: 0.7rem 0.85rem; gap: 0; }}
    [class*="st-key-card"]:hover {{ border-color: #B8C7D9; }}
    [class*="st-key-cardsel_"] {{ border: 1.5px solid {LINK}; background: #F5F9FD; }}
    [class*="st-key-card"] .stButton {{ position: absolute; inset: 0; z-index: 2; }}
    [class*="st-key-card"] .stButton button {{ width: 100%; height: 100%; opacity: 0; cursor: pointer; }}
    .card-row {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 0.6rem; }}
    .card-name {{ font-weight: 600; color: {TEXT}; line-height: 1.35; }}
    .card-sub {{ color: {MUTED}; font-size: 0.8rem; margin-top: 0.2rem; }}
    .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.8rem;
        font-weight: 700; white-space: nowrap; }}

    /* 상세 */
    .detail-title {{ font-size: 1.6rem; font-weight: 700; line-height: 1.3; margin: 0; color: {TEXT}; }}
    .summary {{ display: grid; grid-template-columns: 1.1fr 1fr 1fr 1fr; gap: 0.6rem; margin: 1rem 0 0.9rem; }}
    .summary .cell {{ background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 8px; padding: 0.7rem 0.85rem; }}
    .summary .label {{ color: {MUTED}; font-size: 0.78rem; margin-bottom: 0.25rem; }}
    .summary .value {{ font-weight: 700; color: {TEXT}; line-height: 1.35; }}
    .summary .dday {{ font-size: 1.7rem; font-weight: 800; line-height: 1.1; }}
    .box {{ background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 8px;
        padding: 0.9rem 1.1rem 0.6rem; margin-bottom: 0.8rem; }}
    .kv {{ width: 100%; border-collapse: collapse; margin: 0; }}
    .kv tr, .kv td {{ border: none !important; background: transparent !important; }}
    .kv td {{ vertical-align: top; padding: 0.28rem 0; }}
    .kv td.k {{ width: 120px; color: {MUTED}; white-space: nowrap; padding-right: 1rem; }}
    .kv a {{ color: {LINK}; text-decoration: underline; }}

    /* 첫 화면 */
    .welcome {{ padding: 2.2rem 0 1rem; }}
    .welcome h3 {{ font-size: 1.3rem; font-weight: 700; margin: 0 0 0.3rem; color: {TEXT}; }}
    .steps {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.7rem; margin: 1.3rem 0 1.2rem; }}
    .step {{ background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 8px; padding: 0.9rem 1rem; }}
    .step .num {{ color: {LINK}; font-weight: 800; font-size: 0.85rem; }}
    .step .title {{ font-weight: 700; margin: 0.2rem 0; color: {TEXT}; }}
    .step .desc {{ color: {MUTED}; font-size: 0.85rem; line-height: 1.45; }}

    .statusbar {{
        position: fixed; left: 0; right: 0; bottom: 0; z-index: 99;
        display: flex; justify-content: space-between;
        padding: 0.35rem 1.2rem; font-size: 0.8rem; color: {MUTED};
        background: #F3F3F3; border-top: 1px solid #E5E5E5;
    }}
    .statusbar .error {{ color: {URGENT}; }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ── 도움 함수 (ui.py와 동일한 규칙) ───────────────────────
def has_value(value) -> bool:
    return bool(value) and str(value).strip() not in ("", NOT_STATED)


def days_left(deadline) -> int | None:
    try:
        return (date.fromisoformat(str(deadline)) - datetime.now(KST).date()).days
    except (TypeError, ValueError):
        return None


def remaining_text(days: int | None) -> str:
    if days is None:
        return "-"
    if days < 0:
        return "마감"
    if days == 0:
        return "오늘"
    return f"{days}일"


def remaining_color(days: int | None) -> str:
    if days is None:
        return MUTED
    if days < 0:
        return CLOSED
    if days <= 3:
        return URGENT
    if days <= 7:
        return SOON
    return TEXT


def find_apply_url(info: dict) -> str:
    """관련 링크·접수 방법에서 '접수/신청'이라는 이름이 붙은 URL을 찾습니다."""
    for key in ("related_links", "submission_method"):
        for line in str(info.get(key, "")).splitlines():
            urls = find_urls(line)
            name = line
            for url in urls:
                name = name.replace(url, "")
            if urls and any(word in name.lower() for word in APPLY_WORDS):
                return urls[0]
    return ""


def contest_name(record: dict) -> str:
    name = record["info"].get("competition_name")
    return name if has_value(name) else (record.get("title") or "이름 없음")


def copy_text(record: dict) -> str:
    info = record["info"]
    lines = [f"{LABELS[k]}: {info[k]}" if "\n" not in str(info[k]) else f"{LABELS[k]}:\n{info[k]}"
             for k in LABELS if has_value(info.get(k))]
    if record.get("url"):
        lines.append(f"공고 원문: {record['url']}")
    return "\n".join(lines)


def esc(text) -> str:
    return html.escape(str(text)).replace("\n", "<br>")


def link_lines_html(text: str) -> str:
    """'이름: URL' 줄은 이름을 링크로, URL만 있으면 URL을 링크로 보여 줍니다."""
    out = []
    for line in str(text).splitlines():
        urls = find_urls(line)
        if not urls:
            out.append(esc(line))
            continue
        name = line
        for url in urls:
            name = name.replace(url, "")
        name = name.strip(" :-·|()")
        shown = name or (urls[0] if len(urls[0]) <= 70 else urls[0][:67] + "...")
        out.append(f'<a href="{html.escape(urls[0], quote=True)}" target="_blank">{esc(shown)}</a>')
    return "<br>".join(out)



# ── 요약·배지 (UI용) ──────────────────────────────────────
BADGE_STYLE = {  # 상태: (글자색, 배경색)
    "urgent": (URGENT, "#FDE7E9"),
    "soon": (SOON, "#FFF4CE"),
    "closed": (CLOSED, "#EEEEEE"),
    "normal": (LINK, "#E8F1FB"),
    "none": (MUTED, "#EEEEEE"),
}


def deadline_state(days: int | None) -> str:
    if days is None:
        return "none"
    if days < 0:
        return "closed"
    if days <= 3:
        return "urgent"
    if days <= 7:
        return "soon"
    return "normal"


def dday_text(days: int | None) -> str:
    if days is None:
        return "날짜 미정"
    if days < 0:
        return "마감"
    if days == 0:
        return "D-day"
    return f"D-{days}"


def badge_html(days: int | None) -> str:
    fg, bg = BADGE_STYLE[deadline_state(days)]
    return f'<span class="badge" style="color:{fg};background:{bg}">{dday_text(days)}</span>'


def shorten(text: str, limit: int = 22) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def prize_summary(text) -> str:
    """'총상금' 줄이 있으면 그 금액을, 없으면 첫 줄을 짧게 보여 줍니다."""
    if not has_value(text):
        return "-"
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    for line in lines:
        if re.search(r"총\s*상금", line):
            value = re.split(r"[:：]", line, maxsplit=1)[-1].strip()
            return shorten(value or line)
    return shorten(lines[0])


def team_summary(info: dict) -> str:
    ptype = info.get("participation_type")
    team = info.get("max_team_members")
    if not has_value(ptype):
        return "-"
    if "팀" in ptype and has_value(team):
        return shorten(f"{ptype} · 최대 {team}")
    return ptype



# ── 상태 ──────────────────────────────────────────────────
# 클라우드 서버는 재시작하면 파일이 사라지므로 저장 목록은 세션에 두고,
# '목록 파일'에서 JSON으로 내보내기/불러오기 합니다.
ss = st.session_state
ss.setdefault("saved", [])
ss.setdefault("current", None)
ss.setdefault("editing", False)
ss.setdefault("status", ("", False))
ss.setdefault("list_ver", 0)      # 바꾸면 목록 선택이 초기화됨
ss.setdefault("last_pick", None)


def set_status(text: str, error: bool = False) -> None:
    ss.status = (text, error)


def open_record(record: dict | None, clear_selection: bool = True) -> None:
    ss.current = record
    ss.editing = False
    if clear_selection:
        ss.list_ver += 1
        ss.last_pick = None


def save_current() -> None:
    record = ss.current
    existing = next((r for r in ss.saved if record.get("url") and r.get("url") == record["url"]), None)
    record["id"] = existing["id"] if existing else uuid.uuid4().hex
    record["saved_at"] = datetime.now(KST).isoformat(timespec="minutes")
    if existing:
        ss.saved.remove(existing)
    ss.saved.insert(0, record)
    ss.list_ver += 1
    set_status("저장했습니다." if not existing else "이미 저장된 공고를 새 내용으로 바꿨습니다.")


@st.dialog("공고 삭제")
def confirm_delete() -> None:
    record = ss.current
    st.write(f"'{contest_name(record)}'을(를) 삭제할까요?")
    left, right = st.columns(2)
    if left.button("삭제", type="primary", width="stretch"):
        ss.saved = [r for r in ss.saved if r["id"] != record["id"]]
        open_record(None)
        set_status("삭제했습니다.")
        st.rerun()
    if right.button("취소", width="stretch"):
        st.rerun()


def run_analysis(url: str, area) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        set_status("OpenAI API 키가 설정되지 않았습니다. Streamlit Secrets를 확인하세요.", error=True)
        return
    progress = area.progress(0, text=STEPS[0] + "… (1/3)")
    try:
        html_text = fetch_html(url)
        progress.progress(1 / 3, text=STEPS[1] + "… (2/3)")
        title, body = parse_html(html_text, url)
        if not body.strip():
            raise ValueError("공고 본문을 찾지 못했습니다. 목록 페이지가 아닌 상세 공고 페이지인지 확인하세요.")
        progress.progress(2 / 3, text=STEPS[2] + "… (3/3)")
        info = extract_info(title, body, url)
    except (ValueError, RuntimeError, ParseError, ExtractError) as error:
        set_status(str(error), error=True)
        return
    except Exception as error:  # noqa: BLE001
        set_status(f"예상하지 못한 오류가 발생했습니다 ({type(error).__name__}).", error=True)
        return
    finally:
        progress.empty()
    open_record({"id": None, "url": url, "title": title, "saved_at": None, "info": info})
    set_status("분석을 마쳤습니다. 내용을 확인하고 저장하세요.")


# ── 접근 비밀번호 (선택) ──────────────────────────────────
# Secrets에 APP_PASSWORD를 넣으면 비밀번호를 아는 사람만 분석할 수 있습니다 (API 요금 보호).
def app_password() -> str:
    try:
        return str(st.secrets.get("APP_PASSWORD", ""))
    except FileNotFoundError:
        return ""


# ── 상단 도구 막대 ────────────────────────────────────────
def render_toolbar() -> None:
    locked = bool(app_password()) and not ss.get("unlocked")
    with st.form("toolbar", border=False):
        c_label, c_url, c_btn = st.columns([0.9, 9, 1.1], vertical_alignment="center")
        c_label.markdown('<div class="toolbar-label">공고 URL</div>', unsafe_allow_html=True)
        url_value = c_url.text_input("공고 URL", placeholder="https://", label_visibility="collapsed")
        submitted = c_btn.form_submit_button("분석", type="primary", width="stretch", disabled=locked)
    progress_area = st.empty()

    if locked:
        typed = st.text_input("접근 비밀번호", type="password", placeholder="분석하려면 비밀번호를 입력하세요")
        if typed:
            if typed == app_password():
                ss.unlocked = True
                st.rerun()
            st.error("비밀번호가 맞지 않습니다.")

    if submitted:
        url = normalize_url(url_value)
        if not url:
            set_status("공고 URL을 입력하세요.", error=True)
        elif ss.editing:
            set_status("수정 중인 내용을 먼저 저장하거나 취소하세요.", error=True)
        else:
            run_analysis(url, progress_area)
            st.rerun()
    st.divider()


# ── 왼쪽: 저장한 공고 목록 ───────────────────────────────
FILTERS = ["전체", "마감 임박", "마감"]


def sorted_records(query: str, mode: str) -> list[dict]:
    query = query.strip().lower()
    items = [r for r in ss.saved
             if query in (contest_name(r) + " " + str(r["info"].get("hosting_organization", ""))).lower()]
    if mode == "마감 임박":
        items = [r for r in items if (d := days_left(r["info"].get("deadline"))) is not None and 0 <= d <= 7]
    elif mode == "마감":
        items = [r for r in items if (d := days_left(r["info"].get("deadline"))) is not None and d < 0]

    def by_deadline(record):
        days = days_left(record["info"].get("deadline"))
        if days is None:
            return (2, 0)
        return (1, -days) if days < 0 else (0, days)  # 진행 중 → 마감됨 → 날짜 없음

    return sorted(items, key=by_deadline)


def render_card(record: dict) -> None:
    info = record["info"]
    days = days_left(info.get("deadline"))
    selected = ss.current is not None and ss.current.get("id") == record["id"]
    org = info.get("hosting_organization")
    sub = shorten(org, 28) if has_value(org) else "주최 정보 없음"
    if days is not None:
        sub += " · " + info["deadline"].replace("-", ".")
    key = ("cardsel_" if selected else "card_") + record["id"]
    with st.container(key=key):
        st.markdown(
            f'<div class="card-row"><div><div class="card-name">{esc(contest_name(record))}</div>'
            f'<div class="card-sub">{esc(sub)}</div></div>{badge_html(days)}</div>',
            unsafe_allow_html=True,
        )
        if st.button("열기", key=f"open_{record['id']}"):
            if ss.editing:
                set_status("수정 중에는 다른 공고를 열 수 없습니다. 저장하거나 취소하세요.", error=True)
            else:
                open_record(record)
            st.rerun()


def render_list() -> None:
    st.markdown('<div class="section-title">저장한 공고</div>', unsafe_allow_html=True)
    query = st.text_input("검색", placeholder="이름 또는 주최 기관 검색", label_visibility="collapsed")

    counts = {
        "전체": len(ss.saved),
        "마감 임박": sum(1 for r in ss.saved if (d := days_left(r["info"].get("deadline"))) is not None and 0 <= d <= 7),
        "마감": sum(1 for r in ss.saved if (d := days_left(r["info"].get("deadline"))) is not None and d < 0),
    }
    mode = st.segmented_control("보기", FILTERS, default="전체", label_visibility="collapsed",
                                format_func=lambda m: f"{m} {counts[m]}", key="filter") or "전체"

    records = sorted_records(query, mode)
    if not records:
        st.markdown('<p class="muted small">' + ("저장한 공고가 없습니다." if not ss.saved
                    else "조건에 맞는 공고가 없습니다.") + "</p>", unsafe_allow_html=True)
    with st.container(height=480 if len(records) > 6 else None, border=False):
        for record in records:
            render_card(record)

    with st.expander("목록 파일 (내보내기 / 불러오기)"):
        st.caption("웹 버전은 창을 닫으면 목록이 사라집니다. 파일로 내보내 두세요.")
        st.download_button("내보내기 (saved_contests.json)",
                           data=json.dumps(ss.saved, ensure_ascii=False, indent=2),
                           file_name="saved_contests.json", mime="application/json",
                           disabled=not ss.saved, width="stretch")
        uploaded = st.file_uploader("불러오기", type="json", label_visibility="collapsed")
        if uploaded is not None and ss.get("loaded_file") != uploaded.file_id:
            ss.loaded_file = uploaded.file_id
            try:
                data = json.loads(uploaded.getvalue().decode("utf-8"))
                if not isinstance(data, list):
                    raise ValueError
                known = {r.get("id") for r in ss.saved}
                new = [r for r in data if isinstance(r, dict) and "info" in r and r.get("id") not in known]
                ss.saved += new
                set_status(f"공고 {len(new)}개를 불러왔습니다.")
                st.rerun()
            except (ValueError, UnicodeDecodeError):
                set_status("saved_contests.json 형식의 파일만 불러올 수 있습니다.", error=True)


# ── 오른쪽: 상세 ─────────────────────────────────────────
def render_header(record: dict, info: dict) -> None:
    org = info.get("hosting_organization")
    st.markdown(
        f'<div class="detail-title">{esc(contest_name(record))}</div>'
        f'<div class="muted" style="margin-top:0.3rem">{esc(org) if has_value(org) else "주최 정보 없음"}</div>',
        unsafe_allow_html=True,
    )


def render_summary(info: dict) -> None:
    """제목 아래 요약 카드 4개: 마감 / 총상금 / 참가 방식 / 참가 대상."""
    days = days_left(info.get("deadline"))
    fg, _ = BADGE_STYLE[deadline_state(days)]
    if days is None:
        due = "마감일 확인 필요"
    else:
        due = "마감 " + info["deadline"].replace("-", ".")
        due += " · 마감되었습니다" if days < 0 else " · 오늘 마감" if days == 0 else ""
    eligibility = info.get("participant_eligibility")
    st.markdown(
        '<div class="summary">'
        f'<div class="cell"><div class="label">남은 기간</div>'
        f'<div class="dday" style="color:{fg}">{dday_text(days)}</div><div class="label" style="margin:0.2rem 0 0">{esc(due)}</div></div>'
        f'<div class="cell"><div class="label">상금</div><div class="value">{esc(prize_summary(info.get("prize_details")))}</div></div>'
        f'<div class="cell"><div class="label">참가 방식</div><div class="value">{esc(team_summary(info))}</div></div>'
        f'<div class="cell"><div class="label">참가 대상</div><div class="value">'
        f'{esc(shorten(eligibility) if has_value(eligibility) else "-")}</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_actions(record: dict, info: dict) -> None:
    cols = st.columns([1.5, 1.1, 0.9, 2.5, 0.9, 0.9])
    apply_url = find_apply_url(info)
    if apply_url:
        cols[0].link_button("접수 페이지 열기", apply_url, type="primary", width="stretch")
    if record.get("url"):
        cols[1].link_button("공고 원문", record["url"], width="stretch")
    with cols[2].popover("복사", width="stretch"):
        st.caption("오른쪽 위 복사 아이콘을 누르세요.")
        st.code(copy_text(record), language=None)

    if record.get("sample"):
        cols[4].markdown('<div class="muted" style="padding-top:0.45rem;text-align:right;white-space:nowrap">샘플 데이터</div>', unsafe_allow_html=True)
        return
    if cols[4].button("수정", width="stretch"):
        ss.editing = True
        st.rerun()
    if record.get("id"):
        if cols[5].button("삭제", width="stretch"):
            confirm_delete()
    elif cols[5].button("저장", type="primary", width="stretch"):
        save_current()
        st.rerun()


def render_sections(info: dict) -> None:
    parts, shown_any = [], False
    for section, keys in SECTIONS:
        present = [k for k in keys if has_value(info.get(k))]
        if not present:
            continue
        shown_any = True
        rows = "".join(
            f'<tr><td class="k">{LABELS[k]}</td>'
            f'<td class="v">{link_lines_html(info[k]) if k in LINK_FIELDS else esc(info[k])}</td></tr>'
            for k in present
        )
        parts.append(f'<div class="box"><div class="section-title">{section}</div><table class="kv">{rows}</table></div>')

    if not shown_any:
        parts.append('<p class="muted">공고에서 정리할 수 있는 정보를 찾지 못했습니다.</p>')

    missing = [LABELS[k] for k in LABELS if k not in ("competition_name", "deadline") and not has_value(info.get(k))]
    if missing:
        parts.append(f'<p class="muted small" style="margin-top:0.6rem">공고에 없는 항목: {esc(", ".join(missing))}</p>')
    st.markdown("".join(parts), unsafe_allow_html=True)
def render_edit_form(record: dict, info: dict) -> None:
    with st.form("edit", border=False):
        note, cancel_col, save_col = st.columns([5, 0.9, 0.9], vertical_alignment="center")
        note.markdown('<span class="muted">내용을 수정하는 중입니다. 빈칸은 \'공고에 미기재\'로 저장됩니다.</span>',
                      unsafe_allow_html=True)
        cancelled = cancel_col.form_submit_button("취소", width="stretch")
        saved = save_col.form_submit_button("저장", type="primary", width="stretch")
        st.divider()

        values = {}
        for section, keys in EDIT_SECTIONS:
            st.markdown(f'<div class="section-title">{section}</div>', unsafe_allow_html=True)
            for key in keys:
                label_col, input_col = st.columns([1, 4])
                label_col.markdown(f'<div class="muted" style="padding-top:0.5rem">{LABELS[key]}'
                                   f'{" (YYYY-MM-DD)" if key == "deadline" else ""}</div>',
                                   unsafe_allow_html=True)
                value = info.get(key, "")
                value = "" if value == NOT_STATED else str(value)
                if key in SINGLE_LINE:
                    values[key] = input_col.text_input(LABELS[key], value, label_visibility="collapsed")
                else:
                    lines = max(2, min(value.count("\n") + 1 + len(value) // 60, 8))
                    values[key] = input_col.text_area(LABELS[key], value, height=lines * 26 + 20,
                                                      label_visibility="collapsed")

    if cancelled:
        ss.editing = False
        st.rerun()
    if saved:
        values = {k: v.strip() for k, v in values.items()}
        if values.get("deadline") and days_left(values["deadline"]) is None:
            st.error("마감일은 2026-12-31처럼 연-월-일 형식으로 입력하세요.")
            return
        info.update({k: (v or NOT_STATED) for k, v in values.items()})
        ss.editing = False
        ss.list_ver += 1
        set_status("수정한 내용을 반영했습니다.")
        st.rerun()


def render_welcome() -> None:
    """선택된 공고가 없을 때 보여 주는 첫 화면 (사용 방법 3단계 + 샘플 체험)."""
    st.markdown(
        '<div class="welcome"><h3>공모전 공고, 링크 하나로 정리하세요</h3>'
        '<div class="muted">긴 공고문을 끝까지 읽지 않아도 참가 자격, 마감일, 상금, 제출 서류를 바로 확인할 수 있어요.</div>'
        '<div class="steps">'
        '<div class="step"><div class="num">STEP 1</div><div class="title">공고 URL 붙여넣기</div>'
        '<div class="desc">위비티, 올콘 같은 공모전 사이트의 상세 페이지 주소를 위 입력칸에 넣으세요.</div></div>'
        '<div class="step"><div class="num">STEP 2</div><div class="title">항목별로 자동 정리</div>'
        '<div class="desc">공고 본문을 읽고 참가 자격, 일정, 시상, 접수 방법을 나눠서 보여 줍니다.</div></div>'
        '<div class="step"><div class="num">STEP 3</div><div class="title">저장하고 마감 관리</div>'
        '<div class="desc">저장한 공고는 마감일 순으로 정렬되고, 임박한 공고는 색으로 표시됩니다.</div></div>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    if st.button("샘플 공고로 체험하기", type="primary"):
        open_record(json.loads(json.dumps(SAMPLE)))
        st.rerun()


def render_detail() -> None:
    record = ss.current
    if record is None:
        render_welcome()
        return
    info = record["info"]
    render_header(record, info)
    if ss.editing:
        st.write("")
        render_edit_form(record, info)
        return
    render_summary(info)
    render_actions(record, info)
    st.write("")
    render_sections(info)
# ── 하단 상태 표시줄 ──────────────────────────────────────
def render_statusbar() -> None:
    text, error = ss.status
    css = "error" if error else ""
    st.markdown(
        f'<div class="statusbar"><span class="{css}">{esc(text)}</span>'
        f'<span>저장된 공고 {len(ss.saved)}개</span></div>',
        unsafe_allow_html=True,
    )


# ── 화면 조립 ────────────────────────────────────────────
st.markdown(
    '<div class="brand"><div class="logo">auto<span>-</span>spec</div>'
    '<div class="tagline">공모전 공고, 한눈에 정리</div></div>',
    unsafe_allow_html=True,
)
render_toolbar()
list_col, detail_col = st.columns([3.2, 6.8], gap="large")
with list_col:
    render_list()
with detail_col:
    render_detail()
render_statusbar()
