"""'연금형 채널' 지표 계산.

주의: 아래 점수는 표준 지표가 아니라 이 도구가 정의한 휴리스틱이다.
가중치는 근거 있는 추정치일 뿐 검증된 값이 아니므로, 쓰면서 조정하는 걸 전제로
각 구성요소 점수를 전부 따로 노출한다. 최종 점수보다 breakdown을 보는 게 낫다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from ytdata import Channel, Video

TOP_N = 20          # 지표 계산에 쓸 상위 조회수 영상 개수
RECENT_YEARS = 1.0  # '최신 영상' 기준

WEIGHTS = {
    "age": 0.35,         # 상위 영상이 얼마나 오래됐나 (연금의 핵심)
    "durability": 0.30,  # 최신 영상에 얼마나 안 기대나
    "reach": 0.20,       # 구독자 대비 조회수 (검색/추천 유입)
    "depth": 0.15,       # 100만 뷰 이상 영상이 몇 개나 쌓였나
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Metrics:
    channel: Channel
    top_videos: list[Video]
    sample_size: int
    median_age_years: float
    recency_dependence: float      # 상위 영상 조회수 중 최근 1년치 비중 (낮을수록 연금형)
    views_per_sub: float | None
    million_view_count: int
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    partial_data: bool = False     # 전체 카탈로그를 못 봤으면 True

    @property
    def oldest_top_age(self) -> float:
        return max((v.age_years for v in self.top_videos), default=0.0)

    def as_prompt_facts(self) -> dict:
        """초안 생성기에 넘길 '사실만' 담은 딕셔너리.

        모델이 지어낼 여지를 없애려고, 프롬프트에 들어갈 숫자는 전부 여기서만 나온다.
        """
        ch = self.channel
        return {
            "채널명": ch.title,
            "핸들": f"@{ch.handle}" if ch.handle else None,
            "채널URL": ch.url,
            "구독자수": ch.subscribers,
            "총영상수": ch.video_count,
            "채널총조회수": ch.total_views,
            "분석한_영상수": self.sample_size,
            "상위영상": [
                {
                    "제목": v.title,
                    "조회수": v.views,
                    "업로드_경과_년": round(v.age_years, 1),
                }
                for v in self.top_videos
            ],
            "상위영상_중앙값_경과년": round(self.median_age_years, 1),
            "최신영상_의존도": round(self.recency_dependence, 3),
            "구독자당_조회수": (
                round(self.views_per_sub, 1) if self.views_per_sub is not None else None
            ),
            "백만뷰_영상수": self.million_view_count,
            "연금점수": round(self.score, 1),
            "점수_구성": {k: round(v, 3) for k, v in self.breakdown.items()},
            "데이터_불완전": self.partial_data,
        }


def compute(channel: Channel, videos: list[Video] | None = None,
            top_n: int = TOP_N) -> Metrics:
    vids = sorted(videos if videos is not None else channel.videos,
                  key=lambda v: v.views, reverse=True)
    if not vids:
        raise ValueError(f"'{channel.title}' 채널에 분석할 영상이 없습니다.")

    top = vids[:top_n]
    top_view_total = sum(v.views for v in top)

    median_age = statistics.median(v.age_years for v in top)

    recent_views = sum(v.views for v in top if v.age_years < RECENT_YEARS)
    recency_dependence = (recent_views / top_view_total) if top_view_total else 0.0

    # 전체 카탈로그를 못 봤으면 채널 총조회수로 대체 (더 정확함)
    partial = channel.video_count is not None and len(vids) < channel.video_count
    basis_views = channel.total_views if channel.total_views is not None else top_view_total
    views_per_sub = (
        basis_views / channel.subscribers
        if channel.subscribers else None
    )

    million_count = sum(1 for v in vids if v.views >= 1_000_000)

    # --- 구성요소별 0~1 정규화 -------------------------------------------
    # age: 5년 이상이면 만점. 연금형 채널은 대체로 상위 영상이 4~6년 됐다.
    age_c = _clamp(median_age / 5.0)

    # durability: 최신 영상 의존도의 반대값. 그대로 0~1이라 변환 불필요.
    durability_c = _clamp(1.0 - recency_dependence)

    # reach: 구독자당 조회수를 로그 스케일로. 10배=0점, 1000배=만점.
    if views_per_sub and views_per_sub > 0:
        reach_c = _clamp((math.log10(views_per_sub) - 1.0) / 2.0)
    else:
        reach_c = 0.0

    # depth: 100만뷰 영상 개수 로그 스케일. 1개=0점, 100개=만점.
    depth_c = _clamp(math.log10(max(million_count, 1)) / 2.0)

    breakdown = {
        "age": age_c,
        "durability": durability_c,
        "reach": reach_c,
        "depth": depth_c,
    }
    score = 100.0 * sum(WEIGHTS[k] * v for k, v in breakdown.items())

    return Metrics(
        channel=channel,
        top_videos=top,
        sample_size=len(vids),
        median_age_years=median_age,
        recency_dependence=recency_dependence,
        views_per_sub=views_per_sub,
        million_view_count=million_count,
        score=score,
        breakdown=breakdown,
        partial_data=partial,
    )


def format_table(results: list[Metrics]) -> str:
    """터미널용 랭킹 표."""
    lines = [
        f"{'점수':>5}  {'채널':<28} {'구독자':>10} {'중앙나이':>8} {'최신의존':>8} {'100만+':>7}",
        "-" * 78,
    ]
    for m in results:
        subs = f"{m.channel.subscribers:,}" if m.channel.subscribers else "비공개"
        title = m.channel.title[:26]
        lines.append(
            f"{m.score:>5.1f}  {title:<28} {subs:>10} "
            f"{m.median_age_years:>7.1f}년 {m.recency_dependence:>7.0%} "
            f"{m.million_view_count:>7}"
        )
    return "\n".join(lines)
