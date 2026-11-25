"""auto-spec - 공모전 공고 정리기 (웹 버전, Streamlit).

공고 URL을 넣으면 fetcher → parser → extractor 순서로 분석해 보여 줍니다.
데스크톱 버전(ui.py)과 같은 분석 코드를 그대로 사용합니다.

로컬 실행:   streamlit run app.py
배포:        Streamlit Community Cloud (Secrets에 OPENAI_API_KEY 등록)
"""

import json
import os
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

APP_NAME = "auto-spec · 공모전 공고 정리기"
KST = ZoneInfo("Asia/Seoul")  # 서버 시간이 UTC라서 D-day는 한국 날짜로 계산

st.set_page_config(page_title=APP_NAME, page_icon="📋", layout="wide")


# ── API 키 준비 ───────────────────────────────────────────
# 배포 환경에서는 .env가 없으므로 Streamlit Secrets 값을 환경 변수로 옮깁니다.
# (extractor.py는 os.getenv로 읽기 때문에 코드 수정 없이 그대로 동작)
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
LINK_FIELDS = {"official_homepage", "related_links"}
APPLY_WORDS = ("온라인 접수", "접수하기", "접수 페이지", "참가 신청", "참가신청", "신청하기", "지원하기", "apply")


# ── 도움 함수 (ui.py와 동일한 규칙) ───────────────────────
def has_value(value) -> bool:
    return bool(value) and str(value).strip() not in ("", NOT_STATED)


def today() -> date:
    return datetime.now(KST).date()


def days_left(deadline) -> int | None:
    try:
        return (date.fromisoformat(str(deadline)) - today()).days
    except (TypeError, ValueError):
        return None


def dday_text(days: int | None) -> str:
    if days is None:
        return "마감일 미기재"
    if days < 0:
        return "마감"
    if days == 0:
        return "D-day"
    return f"D-{days}"


def dday_badge(days: int | None) -> str:
    text = dday_text(days)
    if days is None:
        return f":gray-background[{text}]"
    if days < 0:
        return f":gray-background[{text}]"
    if days <= 3:
        return f":red-background[{text}]"
    if days <= 7:
        return f":orange-background[{text}]"
    return f":blue-background[{text}]"


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


def as_markdown_text(value: str) -> str:
    """줄바꿈을 살려서 보여 줍니다."""
    return "  \n".join(str(value).splitlines())


def as_links(value: str) -> str:
    lines = []
    for line in str(value).splitlines():
        text = line
        for url in find_urls(line):
            text = text.replace(url, f"[{url}]({url})")
        lines.append(f"- {text}")
    return "\n".join(lines)


def record_as_text(record: dict) -> str:
    info = record["info"]
    lines = [contest_name(record), ""]
    for key, label in LABELS.items():
        if key != "competition_name":
            lines.append(f"[{label}]\n{info.get(key, NOT_STATED)}\n")
    if record.get("url"):
        lines.append(f"[원문]\n{record['url']}")
    return "\n".join(lines)


# ── 접근 비밀번호 (선택) ──────────────────────────────────
# 공개 주소로 배포하면 누구나 내 OpenAI 키로 분석을 돌릴 수 있어서,
# Secrets에 APP_PASSWORD를 넣으면 비밀번호를 아는 사람만 분석할 수 있게 합니다.
def app_password() -> str:
    try:
        return str(st.secrets.get("APP_PASSWORD", ""))
    except FileNotFoundError:
        return ""


def can_analyze() -> bool:
    password = app_password()
    if not password or st.session_state.get("unlocked"):
        return True
    with st.sidebar:
        st.subheader("🔒 분석 잠금")
        typed = st.text_input("접근 비밀번호", type="password")
        if typed:
            if typed == password:
                st.session_state.unlocked = True
                st.rerun()
            st.error("비밀번호가 맞지 않아요.")
    return False


# ── 상태 ──────────────────────────────────────────────────
# 클라우드 서버는 재시작하면 파일이 사라지므로, 저장 목록은 세션에 두고
# JSON 내보내기/불러오기로 보관합니다.
st.session_state.setdefault("saved", [])
st.session_state.setdefault("current", None)


def analyze(url: str) -> None:
    with st.status("공고를 분석하는 중…", expanded=True) as status:
        try:
            st.write("페이지에 접속하는 중 (1/3)")
            html = fetch_html(url)
            st.write("본문을 정리하는 중 (2/3)")
            title, body = parse_html(html, url)
            if not body.strip():
                raise ValueError("공고 본문을 찾지 못했습니다. 목록 페이지가 아닌 상세 공고 페이지인지 확인하세요.")
            st.write("공고 정보를 추출하는 중 (3/3)")
            info = extract_info(title, body, url)
        except (ValueError, RuntimeError, ParseError, ExtractError) as error:
            status.update(label="분석하지 못했어요", state="error")
            st.error(str(error))
            return
        except Exception as error:  # noqa: BLE001
            status.update(label="분석하지 못했어요", state="error")
            st.error(f"예상하지 못한 오류가 발생했습니다 ({type(error).__name__}).")
            return
        status.update(label="분석 완료", state="complete", expanded=False)
    st.session_state.current = {"id": None, "url": url, "title": title, "saved_at": None, "info": info}


def save_current() -> None:
    record = st.session_state.current
    if record is None:
        return
    if record.get("id") is None:
        record["id"] = uuid.uuid4().hex
        record["saved_at"] = datetime.now(KST).strftime("%Y-%m-%dT%H:%M")
        st.session_state.saved.append(record)
    st.toast("저장 목록에 추가했어요.")


def delete_record(record_id: str) -> None:
    st.session_state.saved = [r for r in st.session_state.saved if r["id"] != record_id]
    current = st.session_state.current
    if current and current.get("id") == record_id:
        st.session_state.current = None


# ── 사이드바: 저장 목록 ───────────────────────────────────
def sort_key(record: dict):
    days = days_left(record["info"].get("deadline"))
    # 마감 안 된 것 → 마감일 없는 것 → 지난 것 순서
    if days is None:
        return (1, 0)
    if days < 0:
        return (2, -days)
    return (0, days)


with st.sidebar:
    st.header("저장한 공고")
    saved = sorted(st.session_state.saved, key=sort_key)
    if not saved:
        st.caption("아직 저장한 공고가 없어요.")
    for record in saved:
        days = days_left(record["info"].get("deadline"))
        col_name, col_del = st.columns([5, 1])
        if col_name.button(f"{dday_text(days)} · {contest_name(record)}", key=f"open_{record['id']}",
                           width="stretch"):
            st.session_state.current = record
        if col_del.button("✕", key=f"del_{record['id']}", help="목록에서 삭제"):
            delete_record(record["id"])
            st.rerun()

    st.divider()
    st.caption("웹 버전은 창을 닫으면 목록이 사라져요. 파일로 내보내 두세요.")
    st.download_button(
        "목록 내보내기 (JSON)",
        data=json.dumps(st.session_state.saved, ensure_ascii=False, indent=2),
        file_name="saved_contests.json",
        mime="application/json",
        disabled=not st.session_state.saved,
        width="stretch",
    )
    uploaded = st.file_uploader("목록 불러오기", type="json", label_visibility="collapsed")
    if uploaded is not None and st.session_state.get("loaded_file") != uploaded.file_id:
        try:
            data = json.loads(uploaded.getvalue().decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError
            known = {r["id"] for r in st.session_state.saved}
            st.session_state.saved += [r for r in data if isinstance(r, dict) and "info" in r and r.get("id") not in known]
            st.session_state.loaded_file = uploaded.file_id
            st.rerun()
        except (ValueError, UnicodeDecodeError, KeyError, TypeError):
            st.error("saved_contests.json 형식의 파일만 불러올 수 있어요.")


# ── 본문 ──────────────────────────────────────────────────
st.title("📋 auto-spec")
st.caption("공모전 공고 URL을 넣으면 AI가 참가 자격, 일정, 시상, 제출 서류를 정리해 드려요.")

unlocked = can_analyze()
with st.form("analyze_form", border=False):
    col_url, col_btn = st.columns([5, 1])
    url_input = col_url.text_input("공고 URL", placeholder="https://www.wevity.com/...", label_visibility="collapsed")
    submitted = col_btn.form_submit_button("분석하기", type="primary", width="stretch",
                                           disabled=not unlocked)
if not unlocked:
    st.info("분석하려면 왼쪽에서 접근 비밀번호를 입력하세요.")

if submitted:
    url = normalize_url(url_input)
    if not url:
        st.warning("공고 URL을 입력하세요.")
    else:
        analyze(url)

record = st.session_state.current
if record is None:
    st.info("공모전 상세 페이지 주소를 넣고 **분석하기**를 눌러 보세요. 목록 페이지가 아닌 개별 공고 페이지여야 해요.")
    st.stop()

info = record["info"]
days = days_left(info.get("deadline"))

st.divider()
head_left, head_right = st.columns([3, 1])
with head_left:
    st.subheader(contest_name(record))
    if has_value(info.get("hosting_organization")):
        st.caption(info["hosting_organization"])
with head_right:
    st.markdown(f"### {dday_badge(days)}")
    if days is not None:
        st.caption("마감 " + info["deadline"].replace("-", "."))

actions = st.columns(4)
if record.get("id") is None:
    actions[0].button("저장하기", on_click=save_current, type="primary", width="stretch")
else:
    actions[0].button("저장됨 ✓", disabled=True, width="stretch")
apply_url = find_apply_url(info)
if apply_url:
    actions[1].link_button("접수 페이지", apply_url, width="stretch")
if record.get("url"):
    actions[2].link_button("원문 보기", record["url"], width="stretch")
actions[3].download_button("텍스트로 받기", data=record_as_text(record),
                           file_name=f"{contest_name(record)[:40]}.txt", width="stretch")

for section, keys in SECTIONS:
    st.markdown(f"#### {section}")
    for key in keys:
        value = info.get(key, NOT_STATED)
        left, right = st.columns([1, 4])
        left.markdown(f"**{LABELS[key]}**")
        if not has_value(value):
            right.markdown(f":gray[{NOT_STATED}]")
        elif key in LINK_FIELDS:
            right.markdown(as_links(value))
        else:
            right.markdown(as_markdown_text(value))

missing = [LABELS[k] for k in LABELS if k not in ("competition_name", "deadline") and not has_value(info.get(k))]
if missing:
    st.caption("공고에 없던 항목: " + ", ".join(missing))
