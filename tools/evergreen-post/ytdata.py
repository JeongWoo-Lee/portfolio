"""YouTube Data API v3 클라이언트.

주제어로 '연금형' 채널 후보를 발굴하고, 채널별 상위 조회수 영상을 수집한다.

할당량(quota) 설계가 이 모듈의 핵심이다. 기본 할당량은 하루 10,000 유닛이고
search.list 한 번이 100 유닛이라 가장 비싸다. 그래서 검색은 1회만 쓰고,
채널별 영상 목록은 uploads 재생목록(1유닛/50개)으로 긁는다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import requests

API_ROOT = "https://youtube.googleapis.com/youtube/v3"

# https://developers.google.com/youtube/v3/determine_quota_cost
QUOTA_COST = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}


class QuotaExceeded(RuntimeError):
    pass


@dataclass
class QuotaMeter:
    """이번 실행에서 쓴 할당량을 추적한다. 기본 일일 한도는 10,000."""

    budget: int = 10_000
    spent: int = 0
    calls: list[tuple[str, int]] = field(default_factory=list)

    def charge(self, endpoint: str) -> None:
        cost = QUOTA_COST[endpoint]
        if self.spent + cost > self.budget:
            raise QuotaExceeded(
                f"할당량 예산 초과: {self.spent}+{cost} > {self.budget} "
                f"({endpoint}.list 호출 시도)"
            )
        self.spent += cost
        self.calls.append((endpoint, cost))

    def summary(self) -> str:
        by_endpoint: dict[str, int] = {}
        for name, cost in self.calls:
            by_endpoint[name] = by_endpoint.get(name, 0) + cost
        detail = ", ".join(f"{k}={v}" for k, v in sorted(by_endpoint.items()))
        return f"{self.spent} units ({detail or 'none'})"


@dataclass
class Video:
    video_id: str
    title: str
    published_at: dt.datetime
    views: int
    duration: str

    @property
    def age_years(self) -> float:
        now = dt.datetime.now(dt.timezone.utc)
        return (now - self.published_at).days / 365.25


@dataclass
class Channel:
    channel_id: str
    title: str
    handle: str | None
    subscribers: int | None  # 비공개 채널이면 None
    total_views: int | None
    video_count: int | None
    uploads_playlist: str | None = None
    description: str = ""
    videos: list[Video] = field(default_factory=list)

    @property
    def url(self) -> str:
        if self.handle:
            return f"https://www.youtube.com/@{self.handle}"
        return f"https://www.youtube.com/channel/{self.channel_id}"


def _parse_ts(value: str) -> dt.datetime:
    # API는 "2019-03-14T09:00:00Z" 형태로 준다.
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeClient:
    def __init__(self, api_key: str, meter: QuotaMeter | None = None, timeout: int = 30):
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY가 비어 있습니다.")
        self.api_key = api_key
        self.meter = meter or QuotaMeter()
        self.timeout = timeout
        self._session = requests.Session()

    def _get(self, endpoint: str, **params) -> dict:
        self.meter.charge(endpoint)
        params["key"] = self.api_key
        resp = self._session.get(
            f"{API_ROOT}/{endpoint}", params=params, timeout=self.timeout
        )
        if resp.status_code != 200:
            # 할당량 소진은 403 + reason=quotaExceeded 로 온다. 메시지를 그대로 보여준다.
            raise RuntimeError(
                f"YouTube API {endpoint}.list 실패 (HTTP {resp.status_code}): {resp.text[:400]}"
            )
        return resp.json()

    # ------------------------------------------------------------------
    # 1단계: 발굴
    # ------------------------------------------------------------------
    def discover_channel_ids(
        self,
        topic: str,
        min_age_years: float = 3.0,
        max_results: int = 50,
        region: str = "KR",
        language: str = "ko",
    ) -> list[str]:
        """주제어로 '오래됐는데 조회수 높은' 영상을 찾아 그 채널들을 후보로 반환한다.

        publishedBefore + order=viewCount 조합이 이 도구의 핵심 트릭이다.
        "3년 넘은 영상 중 조회수 최상위"가 곧 연금형 채널의 정의에 가깝기 때문에,
        별도 필터링 없이 검색 한 번으로 후보군의 질이 확보된다.

        비용: 100 유닛 (검색 1회).
        """
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=min_age_years * 365.25)
        payload = self._get(
            "search",
            part="snippet",
            q=topic,
            type="video",
            order="viewCount",
            publishedBefore=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=min(max_results, 50),
            regionCode=region,
            relevanceLanguage=language,
        )
        seen: list[str] = []
        for item in payload.get("items", []):
            cid = item.get("snippet", {}).get("channelId")
            if cid and cid not in seen:
                seen.append(cid)
        return seen

    # ------------------------------------------------------------------
    # 2단계: 채널 메타데이터
    # ------------------------------------------------------------------
    def fetch_channels(self, channel_ids: list[str]) -> list[Channel]:
        """채널 정보를 50개씩 묶어서 가져온다. 비용: 50개당 1 유닛."""
        out: list[Channel] = []
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i : i + 50]
            payload = self._get(
                "channels",
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=50,
            )
            for item in payload.get("items", []):
                stats = item.get("statistics", {})
                snippet = item.get("snippet", {})
                uploads = (
                    item.get("contentDetails", {})
                    .get("relatedPlaylists", {})
                    .get("uploads")
                )
                hidden = stats.get("hiddenSubscriberCount", False)
                out.append(
                    Channel(
                        channel_id=item["id"],
                        title=snippet.get("title", ""),
                        handle=(snippet.get("customUrl") or "").lstrip("@") or None,
                        subscribers=None if hidden else _to_int(stats.get("subscriberCount")),
                        total_views=_to_int(stats.get("viewCount")),
                        video_count=_to_int(stats.get("videoCount")),
                        uploads_playlist=uploads,
                        description=snippet.get("description", ""),
                    )
                )
        return out

    # ------------------------------------------------------------------
    # 3단계: 채널별 영상 수집
    # ------------------------------------------------------------------
    def fetch_channel_videos(self, channel: Channel, max_videos: int = 300) -> list[Video]:
        """uploads 재생목록을 훑어 영상 조회수/업로드일을 모은다.

        search.list(channelId=..., order=viewCount)를 쓰면 상위 영상을 바로 받을 수
        있지만 100 유닛이 든다. uploads 재생목록은 50개당 1유닛이라 300개를 긁어도
        12유닛이면 끝난다. 정렬은 이쪽에서 직접 한다.
        """
        if not channel.uploads_playlist:
            return []

        video_ids: list[str] = []
        page_token: str | None = None
        # 삭제/비공개된 항목 때문에 items가 빈 페이지가 올 수 있다. 그때도
        # nextPageToken은 따라오므로 페이지 수 상한을 따로 걸어야 무한루프를 막는다.
        max_pages = -(-max_videos // 50) + 2
        for _ in range(max_pages):
            if len(video_ids) >= max_videos:
                break
            params = dict(
                part="contentDetails",
                playlistId=channel.uploads_playlist,
                maxResults=50,
            )
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("playlistItems", **params)
            for item in payload.get("items", []):
                vid = item.get("contentDetails", {}).get("videoId")
                if vid:
                    video_ids.append(vid)
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        video_ids = video_ids[:max_videos]
        videos: list[Video] = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            payload = self._get(
                "videos",
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=50,
            )
            for item in payload.get("items", []):
                views = _to_int(item.get("statistics", {}).get("viewCount"))
                if views is None:
                    continue  # 조회수 비공개 영상은 지표에서 제외
                videos.append(
                    Video(
                        video_id=item["id"],
                        title=item.get("snippet", {}).get("title", ""),
                        published_at=_parse_ts(item["snippet"]["publishedAt"]),
                        views=views,
                        duration=item.get("contentDetails", {}).get("duration", ""),
                    )
                )

        videos.sort(key=lambda v: v.views, reverse=True)
        return videos

    def resolve_handle(self, handle: str) -> Channel | None:
        """@핸들 하나를 채널로 변환한다. 비용: 1 유닛."""
        payload = self._get(
            "channels",
            part="snippet,statistics,contentDetails",
            forHandle=handle.lstrip("@"),
        )
        items = payload.get("items", [])
        if not items:
            return None
        item = items[0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        return Channel(
            channel_id=item["id"],
            title=snippet.get("title", ""),
            handle=(snippet.get("customUrl") or "").lstrip("@") or handle.lstrip("@"),
            subscribers=(
                None
                if stats.get("hiddenSubscriberCount", False)
                else _to_int(stats.get("subscriberCount"))
            ),
            total_views=_to_int(stats.get("viewCount")),
            video_count=_to_int(stats.get("videoCount")),
            uploads_playlist=(
                item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            ),
            description=snippet.get("description", ""),
        )
