# auto-spec

공모전 공고 URL을 넣으면 AI가 공고 내용을 항목별로 정리해 주는 프로그램입니다.
참가 자격, 접수 기간과 마감일(D-day), 시상 내역, 제출 서류, 접수 링크를 한눈에 볼 수 있습니다.

## 동작 방식

1. `fetcher.py`: 공고 페이지에 접속해 HTML을 가져옵니다 (한글 인코딩, 403 차단 대응).
2. `parser.py`: HTML에서 메뉴·광고를 걷어내고 공고 본문만 골라냅니다.
3. `extractor.py`: OpenAI API로 본문에서 공모전 정보를 구조화해 추출합니다.
4. 화면
   - `app.py`: 웹 버전 (Streamlit)
   - `ui.py`: 데스크톱 버전 (tkinter)
   - `main.py`: 터미널 버전

## 로컬 실행

```bash
pip install -r requirements.txt
# .env.example을 .env로 복사하고 OPENAI_API_KEY 입력
streamlit run app.py
```

데스크톱 버전은 `pip install -r requirements-desktop.txt` 후 `python ui.py`로 실행합니다.

## 배포 (Streamlit Community Cloud)

앱 설정의 Secrets에 다음을 등록합니다. 형식은 `.streamlit/secrets.toml.example`을 참고하세요.

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-4o-mini"
APP_PASSWORD = "선택 사항"
```

## 기술 스택

Python · Streamlit · requests · BeautifulSoup · OpenAI API · Pydantic
