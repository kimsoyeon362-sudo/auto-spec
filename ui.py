"""공모전 공고 정리기 - 데스크톱 화면.

공고 URL을 넣으면 fetcher → parser → extractor 순서로 분석해 보여 주고,
저장한 공고는 saved_contests.json에 보관합니다.

화면 테마: sv-ttk (pip install sv-ttk). 설치되어 있지 않으면 기본 테마로 실행됩니다.
"""

import json
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import uuid
import webbrowser
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk

from extractor import NOT_STATED, ExtractError, extract_info, find_urls
from fetcher import fetch_html, normalize_url
from parser import ParseError, parse_html

try:
    import sv_ttk
except ImportError:  # 테마 없이도 실행은 되도록
    sv_ttk = None


APP_NAME = "공모전 공고 정리기"
SAVE_FILE = Path(__file__).with_name("saved_contests.json")

# 색은 테마가 정하고, 여기서는 의미가 있는 글자색만 씁니다.
MUTED = "#5F6368"
LINK = "#005FB8"
URGENT = "#C42B1C"
SOON = "#9D5D00"
CLOSED = "#8A8A8A"
FIELD_BORDER = "#D0D0D0"

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


# ── 저장 파일 ─────────────────────────────────────────────
def load_saved() -> list[dict]:
    try:
        data = json.loads(SAVE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def write_saved(records: list[dict]) -> None:
    temp = SAVE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SAVE_FILE)  # 저장 도중 꺼져도 기존 파일이 깨지지 않도록 교체 방식으로 저장


# ── 도움 함수 ─────────────────────────────────────────────
def has_value(value) -> bool:
    return bool(value) and str(value).strip() not in ("", NOT_STATED)


def days_left(deadline) -> int | None:
    try:
        return (date.fromisoformat(str(deadline)) - date.today()).days
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
    return MUTED


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


def enable_hidpi() -> None:
    """Windows 고해상도 화면에서 글자가 흐릿하게 보이지 않도록 합니다."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def korean_font(root: tk.Tk) -> str | None:
    families = set(tkfont.families(root))
    for name in ("맑은 고딕", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR", "NanumGothic"):
        if name in families:
            return name
    return None


# ── 위젯 ─────────────────────────────────────────────────
class PlaceholderEntry(ttk.Entry):
    """비어 있을 때 회색 안내 문구를 보여 주는 입력칸."""

    def __init__(self, parent, placeholder: str, textvariable: tk.StringVar, **kw):
        super().__init__(parent, **kw)
        self.placeholder = placeholder
        self.variable = textvariable
        self.showing = False
        self.bind("<FocusIn>", self._hide)
        self.bind("<FocusOut>", self._show)
        self.bind("<KeyRelease>", lambda e: self.variable.set(self.value()))
        self._show()

    def value(self) -> str:
        return "" if self.showing else self.get()

    def set_value(self, text: str):
        self._hide()
        self.delete(0, "end")
        self.insert(0, text)
        self.variable.set(text)
        if self.focus_get() is not self:
            self._show()

    def _show(self, event=None):
        if not self.get():
            self.showing = True
            self.insert(0, self.placeholder)
            self.configure(style="Placeholder.TEntry")

    def _hide(self, event=None):
        if self.showing:
            self.showing = False
            self.delete(0, "end")
            self.configure(style="TEntry")


class ScrollArea(ttk.Frame):
    """세로 스크롤 영역. 마우스 휠은 App이 포인터 위치를 보고 전달합니다."""

    def __init__(self, parent, background: str):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=background)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.content.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))

    def scroll(self, event):
        if self.content.winfo_height() <= self.canvas.winfo_height():
            return
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:  # Windows는 ±120 단위, macOS는 ±1~10 단위
            step = (-1 if event.delta > 0 else 1) * max(1, abs(event.delta) // 120)
        self.canvas.yview_scroll(step * 2, "units")


# ── 앱 ──────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x760")
        self.minsize(960, 600)

        self._setup_theme()

        self.saved = load_saved()
        self.current: dict | None = None
        self.editing = False
        self.editors: dict[str, tk.Widget] = {}
        self.wrap_labels: list[ttk.Label] = []
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.sort_by, self.sort_reverse = "deadline", False

        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._build_body()

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, self._on_wheel, add="+")
        mod = "Command" if sys.platform == "darwin" else "Control"
        self.bind_all(f"<{mod}-l>", lambda e: self._focus_url())
        self.bind_all(f"<{mod}-s>", lambda e: self.save_action())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_list()
        self.show_detail()
        self._focus_url()

    # 테마·글꼴 ------------------------------------------------
    def _setup_theme(self):
        style = ttk.Style(self)
        if sv_ttk is not None:
            sv_ttk.set_theme("light")
        else:
            style.theme_use("vista" if "vista" in style.theme_names() else "clam")
            style.configure("Accent.TButton", foreground="white", background=LINK)
            style.map("Accent.TButton", background=[("active", "#004C93"), ("disabled", "#C8C8C8")])

        family = korean_font(self)
        if family:
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont",
                         "SunValleyCaptionFont", "SunValleyBodyFont", "SunValleyBodyStrongFont",
                         "SunValleyBodyLargeFont", "SunValleySubtitleFont", "SunValleyTitleFont"):
                try:
                    tkfont.nametofont(name).configure(family=family)
                except tk.TclError:
                    pass
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        self.font_title = tkfont.Font(family=base, size=17, weight="bold")
        self.font_section = tkfont.Font(family=base, size=11, weight="bold")
        self.font_body = tkfont.Font(family=base, size=10)
        self.font_strong = tkfont.Font(family=base, size=10, weight="bold")
        self.font_small = tkfont.Font(family=base, size=9)
        self.font_link = tkfont.Font(family=base, size=10, underline=True)

        self.background = style.lookup("TFrame", "background") or "#FAFAFA"
        style.configure("Placeholder.TEntry", foreground=MUTED)
        style.configure("Muted.TLabel", foreground=MUTED)
        # 화면 배율(125%, 150%)이 달라도 글자에 맞게 행 높이와 항목 이름 칸 너비를 정합니다.
        style.configure("Treeview", rowheight=self.font_body.metrics("linespace") + 14)
        self.label_width = max(self.font_body.measure(text) for text in LABELS.values()) + 28

    # 메뉴 ---------------------------------------------------
    def _build_menu(self):
        mod = "Cmd" if sys.platform == "darwin" else "Ctrl"
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="새 공고 분석", accelerator=f"{mod}+L", command=self._focus_url)
        file_menu.add_command(label="저장", accelerator=f"{mod}+S", command=self.save_action)
        file_menu.add_command(label="삭제", command=self.delete_current)
        file_menu.add_separator()
        file_menu.add_command(label="샘플 공고 열기", command=lambda: self.open_record(json.loads(json.dumps(SAMPLE))))
        file_menu.add_separator()
        file_menu.add_command(label="종료", command=self._on_close)
        menubar.add_cascade(label="파일", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="내용 수정", command=self.start_edit)
        edit_menu.add_command(label="공고 정보 복사", command=self.copy_text)
        menubar.add_cascade(label="편집", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label=f"{APP_NAME} 정보", command=lambda: messagebox.showinfo(
            APP_NAME, f"{APP_NAME}\n\n공모전 공고 페이지에서 참가 자격, 일정, 시상 내역 등을 정리합니다.\n"
                      f"저장 위치: {SAVE_FILE}"))
        menubar.add_cascade(label="도움말", menu=help_menu)
        self.configure(menu=menubar)

    # 상단 도구 막대 ------------------------------------------
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(14, 12, 14, 12))
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="공고 URL").pack(side="left", padx=(0, 10))
        self.url_value = tk.StringVar()
        self.url_entry = PlaceholderEntry(bar, "https://", self.url_value)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.start_analysis())
        self.analyze_button = ttk.Button(bar, text="분석", style="Accent.TButton", width=8, command=self.start_analysis)
        self.analyze_button.pack(side="left", padx=(8, 0))
        ttk.Separator(self, orient="horizontal").pack(side="top", fill="x")

    # 하단 상태 표시줄 ----------------------------------------
    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(14, 5, 14, 6))
        bar.pack(side="bottom", fill="x")
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        self.status_label = ttk.Label(bar, text="", style="Muted.TLabel", font=self.font_small)
        self.status_label.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=len(STEPS), length=160)
        self.saved_count = ttk.Label(bar, text="", style="Muted.TLabel", font=self.font_small)
        self.saved_count.pack(side="right")

    def set_status(self, text: str, error: bool = False):
        self.status_label.configure(text=text, foreground=URGENT if error else MUTED)

    # 본문: 목록 | 상세 ----------------------------------------
    def _build_body(self):
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, padding=(14, 14, 8, 14))
        ttk.Label(left, text="저장한 공고", font=self.font_section).pack(anchor="w")
        self.search_value = tk.StringVar()
        self.search_entry = PlaceholderEntry(left, "이름 또는 주최 기관 검색", self.search_value)
        self.search_entry.pack(fill="x", pady=(10, 10))
        self.search_value.trace_add("write", lambda *_: self.refresh_list())

        tree_box = ttk.Frame(left)
        tree_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_box, columns=("name", "deadline", "left"), show="headings", selectmode="browse")
        for column, text, width, anchor, stretch in (("name", "공모전", 200, "w", True),
                                                     ("deadline", "마감일", 92, "center", False),
                                                     ("left", "남음", 56, "center", False)):
            key = {"name": "name", "deadline": "deadline", "left": "deadline"}[column]
            self.tree.heading(column, text=text, anchor=anchor, command=lambda k=key: self._sort(k))
            self.tree.column(column, width=width, minwidth=width if not stretch else 120, anchor=anchor, stretch=stretch)
        scrollbar = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.tag_configure("urgent", foreground=URGENT)
        self.tree.tag_configure("soon", foreground=SOON)
        self.tree.tag_configure("closed", foreground=CLOSED)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Delete>", lambda e: self.delete_current())
        self.tree.bind("<BackSpace>", lambda e: self.delete_current())

        self.detail = ttk.Frame(panes, padding=(20, 16, 14, 0))
        panes.add(left, weight=0)
        panes.add(self.detail, weight=1)
        self.after_idle(lambda: panes.sashpos(0, 380))

    # 목록 ---------------------------------------------------
    def _sort(self, key: str):
        if self.sort_by == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_by, self.sort_reverse = key, False
        self.refresh_list()

    def _sorted_records(self) -> list[dict]:
        query = self.search_entry.value().strip().lower()
        items = [r for r in self.saved
                 if query in (contest_name(r) + " " + str(r["info"].get("hosting_organization", ""))).lower()]
        if self.sort_by == "name":
            items.sort(key=contest_name, reverse=self.sort_reverse)
        else:
            def by_deadline(record):
                days = days_left(record["info"].get("deadline"))
                if days is None:
                    return (2, 0)
                return (1, -days) if days < 0 else (0, days)  # 진행 중 → 마감됨 → 날짜 없음
            items.sort(key=by_deadline, reverse=self.sort_reverse)
        return items

    def refresh_list(self, select_id: str | None = None):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        for record in self._sorted_records():
            info = record["info"]
            days = days_left(info.get("deadline"))
            deadline = info["deadline"].replace("-", ".") if days is not None else "-"
            tag = ()
            if days is not None:
                tag = ("closed",) if days < 0 else ("urgent",) if days <= 3 else ("soon",) if days <= 7 else ()
            self.tree.insert("", "end", iid=record["id"], values=(contest_name(record), deadline, remaining_text(days)), tags=tag)

        arrow = " ▼" if self.sort_reverse else " ▲"
        self.tree.heading("name", text="공모전" + (arrow if self.sort_by == "name" else ""))
        self.tree.heading("deadline", text="마감일" + (arrow if self.sort_by == "deadline" else ""))
        self.saved_count.configure(text=f"저장된 공고 {len(self.saved)}개")

        if select_id and self.tree.exists(select_id):
            self.tree.selection_set(select_id)
            self.tree.see(select_id)

    def _on_select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        record = next((r for r in self.saved if r["id"] == selected[0]), None)
        if record and record is not self.current:
            if not self.open_record(record):
                # 수정 중이라 이동을 취소한 경우 선택을 되돌립니다.
                current_id = (self.current or {}).get("id")
                self.tree.selection_set(current_id) if current_id and self.tree.exists(current_id) else self.tree.selection_remove(selected)

    # 상세 ---------------------------------------------------
    def open_record(self, record: dict) -> bool:
        if self.editing and not messagebox.askyesno(APP_NAME, "수정한 내용을 저장하지 않고 이동할까요?"):
            return False
        self.editing = False
        self.current = record
        if not record.get("id"):
            self.tree.selection_remove(self.tree.selection())
        self.show_detail()
        return True

    def show_detail(self):
        for child in self.detail.winfo_children():
            child.destroy()
        self.wrap_labels = []
        self.editors = {}

        if self.current is None:
            empty = ttk.Frame(self.detail)
            empty.place(relx=0.5, rely=0.42, anchor="center")
            ttk.Label(empty, text="선택된 공고가 없습니다", font=self.font_section).pack()
            ttk.Label(empty, text="위에 공고 URL을 입력하거나 왼쪽 목록에서 공고를 선택하세요.",
                      style="Muted.TLabel").pack(pady=(6, 0))
            return

        record, info = self.current, self.current["info"]
        self._detail_header(record, info)
        self._detail_actions(record, info)
        ttk.Separator(self.detail, orient="horizontal").pack(fill="x", pady=(14, 0))

        area = ScrollArea(self.detail, self.background)
        area.pack(fill="both", expand=True)
        body = ttk.Frame(area.content, padding=(0, 4, 18, 24))
        body.pack(fill="both", expand=True)
        if self.editing:
            self._edit_form(body, info)
        else:
            self._info_sections(body, info)

        def rewrap(event):
            for widget in self.wrap_labels:
                if widget.winfo_exists():
                    widget.configure(wraplength=max(240, event.width - self.label_width - 40))
        area.canvas.bind("<Configure>", lambda e: (area.canvas.itemconfigure(area.window, width=e.width), rewrap(e)))

    def _detail_header(self, record, info):
        head = ttk.Frame(self.detail)
        head.pack(fill="x")
        head.columnconfigure(0, weight=1)

        title = ttk.Label(head, text=contest_name(record), font=self.font_title, wraplength=560)
        title.grid(row=0, column=0, sticky="w")
        org = info.get("hosting_organization")
        ttk.Label(head, text=org if has_value(org) else "주최 정보 없음", style="Muted.TLabel",
                  wraplength=560).grid(row=1, column=0, sticky="w", pady=(4, 0))
        head.bind("<Configure>", lambda e: title.configure(wraplength=max(300, e.width - 220)))

        days = days_left(info.get("deadline"))
        right = ttk.Frame(head)
        right.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(24, 0))
        if days is None:
            ttk.Label(right, text="마감일 확인 필요", style="Muted.TLabel").pack(anchor="e")
        else:
            ttk.Label(right, text="마감 " + info["deadline"].replace("-", "."), font=self.font_strong).pack(anchor="e")
            note = "마감되었습니다" if days < 0 else "오늘 마감" if days == 0 else f"{days}일 남음"
            ttk.Label(right, text=note, foreground=remaining_color(days), font=self.font_small).pack(anchor="e", pady=(2, 0))

    def _detail_actions(self, record, info):
        row = ttk.Frame(self.detail)
        row.pack(fill="x", pady=(16, 0))

        if self.editing:
            ttk.Button(row, text="저장", style="Accent.TButton", command=self.finish_edit).pack(side="right")
            ttk.Button(row, text="취소", command=self.cancel_edit).pack(side="right", padx=(0, 8))
            ttk.Label(row, text="내용을 수정하는 중입니다. 빈칸은 '공고에 미기재'로 저장됩니다.",
                      style="Muted.TLabel").pack(side="left")
            return

        apply_url = find_apply_url(info)
        if apply_url:
            ttk.Button(row, text="접수 페이지 열기", style="Accent.TButton",
                       command=lambda: webbrowser.open_new_tab(apply_url)).pack(side="left", padx=(0, 8))
        if record.get("url"):
            ttk.Button(row, text="공고 원문",
                       command=lambda: webbrowser.open_new_tab(record["url"])).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="복사", command=self.copy_text).pack(side="left")

        if record.get("sample"):
            ttk.Label(row, text="샘플 데이터", style="Muted.TLabel").pack(side="right")
            return
        if record.get("id"):
            ttk.Button(row, text="삭제", command=self.delete_current).pack(side="right")
        else:
            ttk.Button(row, text="저장", style="Accent.TButton", command=self.save_current).pack(side="right")
        ttk.Button(row, text="수정", command=self.start_edit).pack(side="right", padx=(0, 8))

    def _info_sections(self, body, info):
        shown_any = False
        for section, keys in SECTIONS:
            present = [k for k in keys if has_value(info.get(k))]
            if not present:
                continue
            shown_any = True
            ttk.Label(body, text=section, font=self.font_section).pack(anchor="w", pady=(18, 6))
            grid = ttk.Frame(body)
            grid.pack(fill="x")
            grid.columnconfigure(0, minsize=self.label_width)
            grid.columnconfigure(1, weight=1)
            for row, key in enumerate(present):
                ttk.Label(grid, text=LABELS[key], style="Muted.TLabel").grid(row=row, column=0, sticky="nw", pady=4)
                cell = ttk.Frame(grid)
                cell.grid(row=row, column=1, sticky="ew", pady=4)
                if key in LINK_FIELDS:
                    self._link_lines(cell, str(info[key]))
                else:
                    value = ttk.Label(cell, text=str(info[key]), wraplength=520, justify="left")
                    value.pack(anchor="w", fill="x")
                    self.wrap_labels.append(value)

        if not shown_any:
            ttk.Label(body, text="공고에서 정리할 수 있는 정보를 찾지 못했습니다.", style="Muted.TLabel").pack(anchor="w", pady=18)

        missing = [LABELS[k] for k in LABELS if k not in ("competition_name", "deadline") and not has_value(info.get(k))]
        if missing:
            note = ttk.Label(body, text="공고에 없는 항목: " + ", ".join(missing), style="Muted.TLabel",
                             font=self.font_small, wraplength=520, justify="left")
            note.pack(anchor="w", pady=(22, 0))
            self.wrap_labels.append(note)

    def _link_lines(self, parent, text):
        for line in text.splitlines():
            urls = find_urls(line)
            if not urls:
                plain = ttk.Label(parent, text=line, wraplength=520, justify="left")
                plain.pack(anchor="w")
                self.wrap_labels.append(plain)
                continue
            name = line
            for url in urls:
                name = name.replace(url, "")
            name = name.strip(" :-·|()")
            shown = name or (urls[0] if len(urls[0]) <= 70 else urls[0][:67] + "...")
            link = ttk.Label(parent, text=shown, foreground=LINK, font=self.font_link, cursor="hand2")
            link.pack(anchor="w", pady=(0, 2))
            link.bind("<Button-1>", lambda e, u=urls[0]: webbrowser.open_new_tab(u))

    def _edit_form(self, body, info):
        for section, keys in EDIT_SECTIONS:
            ttk.Label(body, text=section, font=self.font_section).pack(anchor="w", pady=(18, 6))
            grid = ttk.Frame(body)
            grid.pack(fill="x")
            grid.columnconfigure(0, minsize=self.label_width)
            grid.columnconfigure(1, weight=1)
            for row, key in enumerate(keys):
                name = LABELS[key] + (" (YYYY-MM-DD)" if key == "deadline" else "")
                ttk.Label(grid, text=name, style="Muted.TLabel", wraplength=self.label_width - 8).grid(row=row, column=0, sticky="nw", pady=(8, 4))
                value = info.get(key, "")
                value = "" if value == NOT_STATED else str(value)
                if key in SINGLE_LINE:
                    editor = ttk.Entry(grid)
                    editor.insert(0, value)
                else:
                    lines = max(2, min(value.count("\n") + 1 + len(value) // 60, 8))
                    editor = tk.Text(grid, height=lines, wrap="word", font=self.font_body, relief="flat",
                                     padx=8, pady=6, background="white", foreground="#1C1C1C",
                                     highlightthickness=1, highlightbackground=FIELD_BORDER,
                                     highlightcolor=LINK, insertbackground="#1C1C1C")
                    editor.insert("1.0", value)
                editor.grid(row=row, column=1, sticky="ew", pady=4)
                self.editors[key] = editor
        first = self.editors.get("competition_name")
        if first:
            first.focus_set()

    # 동작 ---------------------------------------------------
    def _focus_url(self):
        self.url_entry.focus_set()
        self.url_entry.select_range(0, "end")

    def start_edit(self):
        if self.current is None or self.current.get("sample") or self.editing:
            return
        self.editing = True
        self.show_detail()

    def cancel_edit(self):
        self.editing = False
        self.show_detail()

    def finish_edit(self):
        values = {}
        for key, widget in self.editors.items():
            text = widget.get("1.0", "end-1c") if isinstance(widget, tk.Text) else widget.get()
            values[key] = text.strip()
        if values.get("deadline") and days_left(values["deadline"]) is None:
            messagebox.showwarning(APP_NAME, "마감일은 2026-12-31처럼 연-월-일 형식으로 입력하세요.")
            self.editors["deadline"].focus_set()
            return
        self.current["info"].update({k: (v or NOT_STATED) for k, v in values.items()})
        self.editing = False
        if self.current.get("id"):
            self._persist()
            self.refresh_list(select_id=self.current["id"])
        self.show_detail()
        self.set_status("수정한 내용을 반영했습니다.")

    def save_action(self):
        if self.editing:
            self.finish_edit()
        elif self.current and not self.current.get("id") and not self.current.get("sample"):
            self.save_current()

    def save_current(self):
        record = self.current
        existing = next((r for r in self.saved if record.get("url") and r.get("url") == record["url"]), None)
        record["id"] = existing["id"] if existing else uuid.uuid4().hex
        record["saved_at"] = datetime.now().isoformat(timespec="minutes")
        if existing:
            self.saved.remove(existing)
        self.saved.insert(0, record)
        self._persist()
        self.refresh_list(select_id=record["id"])
        self.show_detail()
        self.set_status("저장했습니다." if not existing else "이미 저장된 공고를 새 내용으로 바꿨습니다.")

    def delete_current(self):
        record = self.current
        if not record or not record.get("id"):
            return
        if not messagebox.askyesno(APP_NAME, f"'{contest_name(record)}'을(를) 삭제할까요?"):
            return
        self.saved = [r for r in self.saved if r["id"] != record["id"]]
        self._persist()
        self.current = None
        self.editing = False
        self.refresh_list()
        self.show_detail()
        self.set_status("삭제했습니다.")

    def _persist(self):
        try:
            write_saved(self.saved)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"저장 파일에 기록하지 못했습니다.\n{error}")

    def copy_text(self):
        if not self.current:
            return
        info = self.current["info"]
        lines = [f"{LABELS[k]}: {info[k]}" if "\n" not in str(info[k]) else f"{LABELS[k]}:\n{info[k]}"
                 for k in LABELS if has_value(info.get(k))]
        if self.current.get("url"):
            lines.append(f"공고 원문: {self.current['url']}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.set_status("공고 정보를 클립보드에 복사했습니다.")

    # 분석 ---------------------------------------------------
    def start_analysis(self):
        if self.running:
            return
        url = normalize_url(self.url_entry.value())
        if not url:
            self.set_status("공고 URL을 입력하세요.", error=True)
            self._focus_url()
            return
        if self.editing and not messagebox.askyesno(APP_NAME, "수정 중인 내용이 있습니다. 저장하지 않고 분석할까요?"):
            return
        self.url_entry.set_value(url)
        self.running = True
        self.analyze_button.state(["disabled"])
        self.url_entry.state(["disabled"])
        self.progress.configure(value=0)
        self.progress.pack(side="right", padx=(0, 16))
        self.set_status(STEPS[0] + "… (1/3)")
        threading.Thread(target=self._worker, args=(url,), daemon=True).start()
        self.after(100, self._poll)

    def _worker(self, url: str):
        """백그라운드 스레드. 화면은 건드리지 않고 queue로만 알립니다."""
        try:
            self.events.put(("step", 0))
            html = fetch_html(url)
            self.events.put(("step", 1))
            title, body = parse_html(html, url)
            if not body.strip():
                raise ValueError("공고 본문을 찾지 못했습니다. 목록 페이지가 아닌 상세 공고 페이지인지 확인하세요.")
            self.events.put(("step", 2))
            info = extract_info(title, body, url)
            self.events.put(("success", {"id": None, "url": url, "title": title, "saved_at": None, "info": info}))
        except (ValueError, RuntimeError, ParseError, ExtractError) as error:
            self.events.put(("error", str(error)))
        except Exception as error:  # noqa: BLE001
            self.events.put(("error", f"예상하지 못한 오류가 발생했습니다 ({type(error).__name__})."))

    def _finish_running(self):
        self.running = False
        self.analyze_button.state(["!disabled"])
        self.url_entry.state(["!disabled"])
        self.progress.pack_forget()

    def _poll(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "step":
                self.progress.configure(value=payload)
                self.set_status(f"{STEPS[payload]}… ({payload + 1}/{len(STEPS)})")
            elif kind == "error":
                self._finish_running()
                self.set_status("분석하지 못했습니다.", error=True)
                messagebox.showerror(APP_NAME, payload)
                return
            elif kind == "success":
                self._finish_running()
                self.editing = False
                self.open_record(payload)
                save_key = "Cmd+S" if sys.platform == "darwin" else "Ctrl+S"
                self.set_status(f"분석을 마쳤습니다. 목록에 보관하려면 저장({save_key})을 누르세요.")
                return
        if self.running:
            self.after(100, self._poll)

    # 기타 ---------------------------------------------------
    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and not isinstance(widget, ScrollArea):
            widget = widget.master
        if widget is not None:
            widget.scroll(event)

    def _on_close(self):
        if self.editing and not messagebox.askyesno(APP_NAME, "수정한 내용을 저장하지 않고 종료할까요?"):
            return
        self.destroy()


if __name__ == "__main__":
    enable_hidpi()
    App().mainloop()
