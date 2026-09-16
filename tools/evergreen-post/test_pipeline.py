"""네트워크·API 키 없이 도는 회귀 테스트.

  python3 test_pipeline.py

YouTube 응답을 모의로 갈아끼워 파싱/점수/할당량 로직을 검증한다.
초안 생성(draft.py)은 실제 API 호출이 필요해서 여기서는 다루지 않는다.
"""

from __future__ import annotations

import datetime as dt

import fixtures
import score
import ytdata

NOW = dt.datetime.now(dt.timezone.utc)


def ago(years: float) -> str:
    return (NOW - dt.timedelta(days=years * 365.25)).strftime("%Y-%m-%dT%H:%M:%SZ")


FAKE = {
    "search": {"items": [
        {"snippet": {"channelId": "UC_a"}},
        {"snippet": {"channelId": "UC_b"}},
        {"snippet": {"channelId": "UC_a"}},  # 중복
    ]},
    "channels": {"items": [
        {"id": "UC_a",
         "snippet": {"title": "수면채널", "customUrl": "@sleepy", "description": "d"},
         "statistics": {"subscriberCount": "802000", "viewCount": "430000000",
                        "videoCount": "264"},
         "contentDetails": {"relatedPlaylists": {"uploads": "UU_a"}}},
        {"id": "UC_b",
         "snippet": {"title": "숨김채널", "customUrl": "", "description": ""},
         "statistics": {"hiddenSubscriberCount": True, "viewCount": "1000",
                        "videoCount": "3"},
         "contentDetails": {"relatedPlaylists": {"uploads": "UU_b"}}},
    ]},
    "playlistItems": {"items": [{"contentDetails": {"videoId": f"v{i}"}} for i in range(3)]},
    "videos": {"items": [
        {"id": "v0", "snippet": {"title": "오래된 히트", "publishedAt": ago(5)},
         "statistics": {"viewCount": "26210000"}, "contentDetails": {"duration": "PT9H"}},
        {"id": "v1", "snippet": {"title": "최근 영상", "publishedAt": ago(0.5)},
         "statistics": {"viewCount": "50000"}, "contentDetails": {"duration": "PT1H"}},
        {"id": "v2", "snippet": {"title": "조회수 비공개", "publishedAt": ago(2)},
         "statistics": {}, "contentDetails": {"duration": "PT1H"}},  # 제외 대상
    ]},
}


class MockClient(ytdata.YouTubeClient):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls: list[tuple[str, dict]] = []

    def _get(self, endpoint, **params):
        self.meter.charge(endpoint)
        self.calls.append((endpoint, {k: v for k, v in params.items() if k != "key"}))
        return FAKE[endpoint]


def test_discovery_dedupes_and_filters_by_age():
    c = MockClient("fake")
    ids = c.discover_channel_ids("수면", min_age_years=3)
    assert ids == ["UC_a", "UC_b"], ids
    params = c.calls[0][1]
    assert params["order"] == "viewCount"
    assert params["type"] == "video"
    # publishedBefore가 과거여야 '오래된 히트' 검색이 성립한다
    cutoff = dt.datetime.strptime(params["publishedBefore"], "%Y-%m-%dT%H:%M:%SZ")
    assert cutoff < NOW.replace(tzinfo=None)


def test_hidden_subscriber_and_empty_handle():
    c = MockClient("fake")
    a, b = c.fetch_channels(["UC_a", "UC_b"])
    assert a.subscribers == 802_000 and a.handle == "sleepy"
    assert b.subscribers is None, "hiddenSubscriberCount를 못 걸러냄"
    assert b.handle is None, "빈 customUrl은 None이어야 함"


def test_videos_skip_hidden_viewcount_and_sort():
    c = MockClient("fake")
    channel = c.fetch_channels(["UC_a"])[0]
    videos = c.fetch_channel_videos(channel)
    assert len(videos) == 2, "조회수 비공개 영상이 섞여 들어옴"
    assert videos[0].views > videos[1].views, "조회수 내림차순 정렬 실패"


def test_quota_is_metered_and_enforced():
    c = MockClient("fake")
    c.discover_channel_ids("수면")
    assert c.meter.spent == 100, c.meter.spent
    try:
        MockClient("fake", meter=ytdata.QuotaMeter(budget=50)).discover_channel_ids("x")
    except ytdata.QuotaExceeded:
        pass
    else:
        raise AssertionError("할당량 초과를 막지 못함")


def test_pagination_terminates_on_empty_pages():
    """items는 비었는데 nextPageToken이 계속 오는 응답에서도 멈춰야 한다."""
    calls = 0

    class Evil(ytdata.YouTubeClient):
        def _get(self, endpoint, **p):
            nonlocal calls
            calls += 1
            if calls > 100:
                raise AssertionError("무한루프")
            self.meter.charge(endpoint)
            return {"items": [], "nextPageToken": "forever"}

    ch = ytdata.Channel("id", "t", "h", 1, 1, 1, uploads_playlist="UU_x")
    Evil("k", meter=ytdata.QuotaMeter(budget=99_999)).fetch_channel_videos(ch, max_videos=300)
    assert calls <= 10, f"페이지 호출이 과도함: {calls}"


def test_scoring_on_real_fixture():
    channel, videos = fixtures.deep_sleep_guide()
    m = score.compute(channel, videos)
    # 상위 영상이 전부 4년 이상 + 최근 1년 영상 없음 -> age/durability 만점
    assert m.breakdown["age"] == 1.0
    assert m.breakdown["durability"] == 1.0
    assert m.partial_data is True, "264개 중 6개만 봤으므로 partial이어야 함"
    assert 75 <= m.score <= 90, m.score
    facts = m.as_prompt_facts()
    assert facts["채널총조회수"] is None
    assert len(facts["상위영상"]) == 6


def test_recency_dependence_detects_flash_in_the_pan():
    """최신 영상이 조회수를 다 먹는 채널은 durability가 낮아야 한다."""
    videos = [
        ytdata.Video("a", "최근 대박", NOW - dt.timedelta(days=30), 10_000_000, ""),
        ytdata.Video("b", "옛날 영상", NOW - dt.timedelta(days=1800), 100_000, ""),
    ]
    ch = ytdata.Channel("id", "반짝채널", "h", 500_000, 10_100_000, 2)
    m = score.compute(ch, videos)
    assert m.recency_dependence > 0.9, m.recency_dependence
    assert m.breakdown["durability"] < 0.1
    assert m.score < 40, f"반짝 채널이 고득점: {m.score}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  ❌ {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    raise SystemExit(1 if failed else 0)
