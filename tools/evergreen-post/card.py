"""수집한 지표로 게시글 첨부용 카드 이미지를 만든다.

capture.py(유튜브 페이지 스크린샷)의 대안이다. 유튜브 페이지에 접근하지
않고 API 데이터만으로 그리기 때문에, youtube.com이 막힌 환경에서도 돌고
모바일에서도 쓸 수 있다.

HTML을 그린 뒤 로컬 Chromium으로 캡쳐한다. 네트워크는 최초 1회 폰트를
받을 때만 쓰고, 이후로는 캐시된 폰트로 오프라인 동작한다.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

from score import Metrics

FONT_DIR = Path(__file__).parent / ".fontcache"
FONT_URLS = {
    400: "https://fonts.gstatic.com/s/notosanskr/v39/PbyxFmXiEBPT4ITbgNA5Cgms3VYcOA-vvnIzzuoyeLQ.ttf",
    700: "https://fonts.gstatic.com/s/notosanskr/v39/PbyxFmXiEBPT4ITbgNA5Cgms3VYcOA-vvnIzzg01eLQ.ttf",
    900: "https://fonts.gstatic.com/s/notosanskr/v39/PbyxFmXiEBPT4ITbgNA5Cgms3VYcOA-vvnIzzkM1eLQ.ttf",
}

# 카드의 세로 치수 (px). CSS와 높이 계산이 이 값을 공유하므로 여기만 고치면 된다.
H_PAD = 56      # body 상·하 여백
H_HEAD = 88     # 채널명 헤더
H_STATS = 104   # 지표 3칸 (+ margin 26/30)
H_ROW = 74      # 영상 한 줄
H_NOTE = 52     # 부분 데이터 경고 (padding 포함)
H_FOOT = 72     # 푸터 (padding 포함)

CHROME_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def _find_chrome() -> str:
    for name in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for path in Path("/opt/pw-browsers").glob("chromium*/chrome-linux/chrome"):
        return str(path)
    raise RuntimeError(
        "Chrome/Chromium을 찾지 못했습니다. 설치하거나 CHROME_PATH 환경변수를 지정하세요."
    )


def ensure_fonts() -> bool:
    """폰트를 캐시에 준비한다. 실패하면 False (시스템 폰트로 폴백)."""
    FONT_DIR.mkdir(exist_ok=True)
    ok = True
    for weight, url in FONT_URLS.items():
        dest = FONT_DIR / f"NotoSansKR-{weight}.ttf"
        if dest.exists() and dest.stat().st_size > 100_000:
            continue
        try:
            import requests

            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        except Exception as exc:
            print(f"  [경고] 폰트 {weight} 다운로드 실패({exc}). 시스템 폰트를 씁니다.")
            ok = False
    return ok


def _views(n: int) -> str:
    """조회수를 한국식으로 읽는다. 26,210,000 -> '2621만'."""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}억".replace(".0억", "억")
    if n >= 10_000:
        return f"{n / 10_000:.0f}만"
    return f"{n:,}"


def _subs(n: int | None) -> str:
    return f"구독자 {_views(n)}명" if n else "구독자 비공개"


def build_html(metrics: Metrics, top_n: int = 6, has_fonts: bool = True) -> str:
    ch = metrics.channel
    videos = metrics.top_videos[:top_n]
    peak = max((v.views for v in videos), default=1)

    face = ""
    if has_fonts:
        face = "\n".join(
            f"@font-face{{font-family:'NotoKR';font-weight:{w};"
            f"src:url('file://{(FONT_DIR / f'NotoSansKR-{w}.ttf').resolve()}') format('truetype');}}"
            for w in FONT_URLS
        )

    rows = []
    for i, v in enumerate(videos, 1):
        pct = v.views / peak * 100
        rows.append(f"""
      <div class="row">
        <div class="rank">{i}</div>
        <div class="main">
          <div class="title">{html.escape(v.title)}</div>
          <div class="bar"><span style="width:{pct:.1f}%"></span></div>
        </div>
        <div class="nums">
          <div class="views">{_views(v.views)}</div>
          <div class="age">{v.age_years:.0f}년 전</div>
        </div>
      </div>""")

    note = ""
    if metrics.partial_data:
        note = (f'<div class="note">※ 전체 {ch.video_count}개 중 '
                f'{metrics.sample_size}개 표본 기준</div>')

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><style>
{face}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'NotoKR','WenQuanYi Zen Hei',sans-serif;background:#0d1117;
  color:#e6edf3;width:1200px;padding:56px 60px}}
/* 높이를 파이썬 쪽에서 정확히 계산해야 해서, 세로 치수는 전부 고정값이다.
   여기 숫자를 바꾸면 render()의 height 계산도 같이 바꿔야 한다. */
.head{{height:{H_HEAD}px;display:flex;align-items:baseline;gap:16px;
  border-bottom:1px solid #21262d}}
h1{{font-size:44px;font-weight:900;letter-spacing:-1px;line-height:1.2}}
.handle{{font-size:22px;color:#7d8590}}
.subs{{font-size:20px;color:#7d8590;margin-left:auto}}
.stats{{display:flex;gap:14px;height:{H_STATS}px;margin:26px 0 30px}}
.stat{{flex:1;background:#161b22;border:1px solid #21262d;border-radius:14px;padding:20px 22px}}
.stat .k{{font-size:17px;color:#7d8590;margin-bottom:8px;line-height:1.2}}
.stat .v{{font-size:38px;font-weight:900;letter-spacing:-1px;line-height:1.2}}
.stat .v small{{font-size:21px;font-weight:700;color:#7d8590;margin-left:3px}}
.hl .v{{color:#3fb950}}
.row{{display:flex;align-items:center;gap:20px;height:{H_ROW}px;
  border-bottom:1px solid #161b22}}
.rank{{font-size:22px;font-weight:900;color:#30363d;width:34px;flex-shrink:0}}
.main{{flex:1;min-width:0}}
.title{{font-size:21px;font-weight:700;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;margin-bottom:9px;line-height:1.3}}
.bar{{height:6px;background:#161b22;border-radius:3px;overflow:hidden}}
.bar span{{display:block;height:100%;background:linear-gradient(90deg,#1f6feb,#3fb950);
  border-radius:3px}}
.nums{{text-align:right;width:128px;flex-shrink:0}}
.views{{font-size:26px;font-weight:900;letter-spacing:-0.5px;line-height:1.2}}
.age{{font-size:17px;color:#7d8590;margin-top:3px;line-height:1.2}}
.note{{height:{H_NOTE}px;font-size:16px;color:#6e7681;padding-top:18px}}
.foot{{height:{H_FOOT}px;padding-top:26px;font-size:16px;color:#6e7681;
  display:flex;justify-content:space-between}}
</style></head><body>
  <div class="head">
    <h1>{html.escape(ch.title)}</h1>
    <div class="handle">@{html.escape(ch.handle or '')}</div>
    <div class="subs">{_subs(ch.subscribers)} · 영상 {ch.video_count or '?'}개</div>
  </div>
  <div class="stats">
    <div class="stat hl"><div class="k">상위 영상 중앙 나이</div>
      <div class="v">{metrics.median_age_years:.1f}<small>년</small></div></div>
    <div class="stat hl"><div class="k">최신 영상 의존도</div>
      <div class="v">{metrics.recency_dependence:.0%}</div></div>
    <div class="stat"><div class="k">100만 뷰 이상</div>
      <div class="v">{metrics.million_view_count}<small>개</small></div></div>
  </div>
  {''.join(rows)}
  {note}
  <div class="foot"><span>조회수 상위 {len(videos)}개 · YouTube Data API</span>
    <span>{html.escape(ch.url)}</span></div>
<script>document.title = "H" + document.documentElement.scrollHeight;</script>
</body></html>"""


def _measure_height(chrome: str, page: Path) -> int | None:
    """실제로 렌더한 뒤 브라우저에게 높이를 물어본다.

    고정 높이를 손으로 계산하면 폰트 메트릭 차이로 1~20px씩 어긋나서 푸터가
    잘린다. 렌더링 엔진이 직접 잰 값을 쓰는 편이 훨씬 안정적이다.
    """
    try:
        result = subprocess.run(
            [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--window-size=1200,800", "--virtual-time-budget=4000",
             "--dump-dom", f"file://{page.resolve()}"],
            capture_output=True, timeout=90,
        )
        match = re.search(r"<title>H(\d+)</title>",
                          result.stdout.decode("utf-8", "replace"))
        if match:
            return int(match.group(1)) + 2  # 반올림 오차 여유
    except Exception:
        pass
    return None


def _estimate_height(metrics: Metrics, top_n: int) -> int:
    """측정에 실패했을 때 쓰는 폴백. 넉넉하게 잡는다."""
    n_rows = len(metrics.top_videos[:top_n])
    return (H_PAD * 2 + H_HEAD + H_STATS + 56 + n_rows * H_ROW
            + (H_NOTE if metrics.partial_data else 0) + H_FOOT + 40)


def render(metrics: Metrics, out_path: str | Path, top_n: int = 6,
           scale: int = 2) -> Path:
    """카드 PNG를 만들어 경로를 돌려준다."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    has_fonts = ensure_fonts()
    page = build_html(metrics, top_n=top_n, has_fonts=has_fonts)

    tmp = out_path.parent / f".{out_path.stem}.html"
    tmp.write_text(page, encoding="utf-8")

    chrome = _find_chrome()
    height = _measure_height(chrome, tmp) or _estimate_height(metrics, top_n)

    cmd = [
        chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--force-device-scale-factor=" + str(scale),
        f"--window-size=1200,{height}",
        "--default-background-color=0d1117",
        f"--screenshot={out_path.resolve()}",
        f"file://{tmp.resolve()}",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    tmp.unlink(missing_ok=True)

    if not out_path.exists():
        raise RuntimeError(
            "카드 렌더링 실패:\n" + result.stderr.decode("utf-8", "replace")[-800:]
        )
    return out_path


if __name__ == "__main__":
    import fixtures
    import score as score_mod

    channel, videos = fixtures.deep_sleep_guide()
    m = score_mod.compute(channel, videos)
    path = render(m, "out/card-demo.png")
    print(f"저장됨: {path.resolve()}  ({path.stat().st_size // 1024} KB)")
