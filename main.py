from fetcher import fetch_html
from parser import parse_html
from extractor import extract_info, ExtractError


INFO_FIELDS = [
    ("공모전 이름", "competition_name"),
    ("개최 기관", "hosting_organization"),
    ("참가 자격", "participant_eligibility"),
    ("참가 방식", "participation_type"),
    ("팀 최대 인원", "max_team_members"),
    ("공모전 기간", "contest_period"),
    ("심사·결과 발표·시상 일정", "major_schedule"),
    ("공모 주제", "contest_topics"),
    ("시상 내역", "prize_details"),
    ("접수 방법", "submission_method"),
    ("제출 서류", "required_documents"),
    ("문의처", "contact_information"),
    ("공식 홈페이지", "official_homepage"),
    ("관련 링크·첨부파일", "related_links"),
    ("유의 사항", "other_conditions"),
]


def main():
    url = input("공고 URL을 입력해 주세요: ").strip()

    try:
        html = fetch_html(url)
        title, body = parse_html(html, url)
    except (ValueError, RuntimeError) as error:
        print(f"오류: {error}")
        return

    if not body.strip():
        print("오류: 페이지 본문을 찾지 못했어요.")
        return

    print("\n페이지 제목")
    print("-" * 40)
    print(title if title else "(제목 없음)")

    print("\n페이지 본문")
    print("-" * 40)
    print(body)

    answer = input("\nAI로 필요한 정보를 추출할까요? (y/n): ").strip().lower()

    if answer not in ("y", "yes"):
        print("\nAI 분석을 건너뛰었습니다.")
        return

    try:
        info = extract_info(title, body, url)
    except ExtractError as error:
        print(f"\n오류: {error}")
        return

    print("\n공고 정보")
    print("-" * 40)

    for label, key in INFO_FIELDS:
        value = info.get(key, "")
        print(f"{label}: {value if value is not None else ''}")


if __name__ == "__main__":
    main()