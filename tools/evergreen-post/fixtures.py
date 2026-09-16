"""오프라인 테스트용 고정 데이터.

API 키 없이 점수 계산과 초안 생성 로직을 돌려보기 위한 것이다.

출처: 사용자가 제공한 '브레이너 제이의 숙면 여행' 채널 스크린샷.
      화면에 보이는 상위 6개 영상만 옮겨 적었다 (실제 채널 영상은 264개).
      조회수/경과연수는 스크린샷 표기 그대로이며, 채널 총조회수는 화면에
      없었으므로 None이다. 즉 이 fixture는 의도적으로 '불완전한 데이터'이고,
      partial_data 경로를 실제로 검증하는 역할도 한다.
"""

from __future__ import annotations

import datetime as dt

from ytdata import Channel, Video

_NOW = dt.datetime.now(dt.timezone.utc)


def _years_ago(years: float) -> dt.datetime:
    return _NOW - dt.timedelta(days=years * 365.25)


# (제목, 조회수, 스크린샷의 '~년 전', 재생시간)
_RAW = [
    ("최상의 숙면을 경험하세요. 깸 없는 깊은 잠을 돕는 수면뇌파 동조화 수면 사운드 - 90분 수면사이클 버전",
     26_210_000, 5, "PT9H43M6S"),
    ("NASA 최첨단 수면실에서 6시간 회복 숙면하세요! 오래 잔 듯, 가득 충전되는 생체에너지 회복실",
     22_260_000, 4, "PT6H22M8S"),
    ("적게 자도 숙면하세요! 4시간30분 수면 관리 가이드, 장시간 브레인 파워냅 for 수험생, 공시생 (알람 3회 有)",
     12_420_000, 6, "PT5H24M12S"),
    ("[사운드 보완] 밤 비행기 1등석에서 딥슬립하세요! 기내 소리와 수면을 돕는 최적의 사운드 with 10,000m",
     11_770_000, 5, "PT7H32M21S"),
    ("기적의 두뇌 효율 만드는 6시간 수면 관리 가이드, 장시간 파워 수면 for 수험생, 공시생 (알람 3회 有)",
     7_380_000, 6, "PT6H52M"),
    ("최적의 꿀잠습관 만들기 7시간 30분 수면 관리 가이드, 중력 수면 영상 버전 (알람 3회 有) [BSM Level 1]",
     6_500_000, 6, "PT8H22M15S"),
]


def deep_sleep_guide() -> tuple[Channel, list[Video]]:
    videos = [
        Video(
            video_id=f"fixture{i}",
            title=title,
            published_at=_years_ago(age),
            views=views,
            duration=duration,
        )
        for i, (title, views, age, duration) in enumerate(_RAW)
    ]
    channel = Channel(
        channel_id="UC_fixture_deepsleepguide",
        title="브레이너 제이의 숙면 여행",
        handle="DeepSleepGuide",
        subscribers=802_000,
        total_views=None,   # 스크린샷에 없음 -> partial 경로를 탄다
        video_count=264,
        uploads_playlist=None,
        description="《대한민국 No.1 수면/이완 전문 채널 '브레이너 제이의 숙면여행'》",
        videos=videos,
    )
    return channel, videos


FIXTURES = {"deepsleepguide": deep_sleep_guide}
