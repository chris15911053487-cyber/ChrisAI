"""内置课程：公开 / 登录可见范围、进度读写、清单与内容文件一致性。"""
import uuid

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config, courses, storycheck
from app.main import app

CID = "ai-intro"


@pytest.fixture(scope="module")
def anon():
    with TestClient(app) as c:
        yield c


def _user_client():
    c = TestClient(app)
    c.__enter__()
    r = c.post("/api/auth/register", json={"username": "c" + uuid.uuid4().hex[:10], "password": "secret123"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def user():
    c = _user_client()
    yield c
    c.__exit__(None, None, None)


# ---------------- 清单与内容 ----------------
def test_manifest_consistent():
    """每节课都有正文；标了 story 的课有故事页文件；工具卡引用的课程存在；阶段引用有效。"""
    m = yaml.safe_load((config.COURSES_DIR / CID / "course.yaml").read_text(encoding="utf-8"))
    stage_ids = {s["id"] for s in m["stages"]}
    lesson_ids = [l["id"] for l in m["lessons"]]
    tool_ids = {t["id"] for t in m["tools"]}
    assert len(lesson_ids) == 6 and len(set(lesson_ids)) == 6
    assert [l["seq"] for l in m["lessons"]] == [1, 2, 3, 4, 5, 6]  # 注意：YAML 里 "no" 会被解析成 False，故用 seq
    for l in m["lessons"]:
        assert l["stage"] in stage_ids
        assert (config.COURSES_DIR / CID / "lessons" / f"{l['id']}.md").is_file()
        if l.get("story"):
            assert (config.COURSES_DIR / CID / "stories" / f"{l['id']}.html").is_file()
        assert "interactive" not in l and "recommended" not in l  # 旧字段已下线
        assert set(l.get("takeaways") or []) <= tool_ids
    for t in m["tools"]:
        assert t["lesson"] in lesson_ids and t["points"]


def test_all_stories_pass_storycheck():
    files = sorted((config.COURSES_DIR / CID / "stories").glob("*.html"))
    assert files, "至少要有一节故事页"
    for f in files:
        assert storycheck.check(f.read_text(encoding="utf-8")) == [], f.name


def test_l4_story_is_lesson_four():
    html = (config.COURSES_DIR / CID / "stories" / "l4.html").read_text(encoding="utf-8")
    assert "第二句话" in html and "小林" in html and "老陈" in html and "王总" in html


# ---------------- 故事页校验器 ----------------
_OK = """<header class="story-meta" data-stages="一|二"><div class="t">T</div></header>
<div class="chapter" data-title="开场" data-stage="0"><section class="beat st"><h2>Hi</h2></section></div>
<div class="chapter" data-no="01" data-title="对话" data-stage="1"><section class="scene">
  <div class="stage"><div class="phone"><div class="phone-hd"><b>你</b></div><div class="body">
    <div class="m u" data-i="0">问</div><div class="m a" data-i="1">答</div><div class="m u" data-i="c"></div>
  </div></div></div>
  <div class="steps">
    <div class="step st" data-show="0,1"><div class="card">看</div></div>
    <div class="step st" data-show="0,1" data-act="choice" data-unlock="1" data-slot="c"><div class="card">
      <button class="opt" data-c="a" data-show="0,1,c" data-fb="x">A</button><button class="opt" data-c="b" data-show="0,1,c" data-fb="y">B</button>
      <div class="fb"></div></div></div>
    <div class="step st locked" data-gate="1"><div class="card"><div class="res"></div>
      <template data-res="a">ra</template><template data-res="b">rb</template></div></div>
  </div></section></div>
<div class="chapter locked" data-gate="1" data-title="后" data-stage="2"><section class="beat st"><h2>End</h2></section></div>"""


def test_storycheck_accepts_valid_fragment():
    assert storycheck.check(_OK) == []


@pytest.mark.parametrize("bad,needle", [
    (_OK.replace('<h2>Hi</h2>', '<script>alert(1)</script>'), "<script>"),
    (_OK.replace('<h2>Hi</h2>', '<img src=x onerror="alert(1)">'), "onerror"),
    (_OK.replace('<h2>Hi</h2>', '<a href="javascript:alert(1)">x</a>'), "javascript"),
    (_OK.replace('data-show="0,1"><div class="card">看', 'data-show="0,9"><div class="card">看'), "不存在的 data-i：9"),
    (_OK.replace('data-unlock="1"', 'data-unlock="7"'), "data-gate=1 没有任何互动"),
    (_OK.replace('<template data-res="b">rb</template>', ''), "结果卡 data-res"),
    (_OK.replace('data-fb="y"', ''), "data-fb"),
    (_OK.replace('data-stage="2"', 'data-stage="5"'), "超出"),
    (_OK.replace('class="chapter locked" data-gate="1"', 'class="chapter" data-gate="1"'), "class locked"),
])
def test_storycheck_rejects(bad, needle):
    errs = storycheck.check(bad)
    assert any(needle in e for e in errs), errs


# ---------------- 未登录：只能看课程目录 ----------------
def test_list_public(anon):
    r = anon.get("/api/courses")
    assert r.status_code == 200
    by = {c["id"]: c for c in r.json()["courses"]}
    assert by["ai-intro"]["status"] == "published" and by["ai-intro"]["lesson_count"] == 6
    assert by["ai-knowledge"]["status"] == "coming"
    assert list(by) == ["ai-intro", "ai-knowledge"]  # 按 sort 排序


def test_detail_anon_has_catalog_but_no_gated_data(anon):
    d = anon.get(f"/api/courses/{CID}").json()
    assert d["logged_in"] is False and d["progress"] is None
    assert len(d["stages"]) == 4 and [l["no"] for l in d["lessons"]] == [1, 2, 3, 4, 5, 6]
    l4 = next(l for l in d["lessons"] if l["id"] == "l4")
    assert l4["modes"]["story"]["available"] and l4["modes"]["story"]["minutes"] > 0
    assert l4["modes"]["text"]["available"] and not l4["modes"]["video"]["available"]
    assert not any(l["modes"]["story"]["available"] for l in d["lessons"] if l["id"] != "l4")  # 其余课制作中
    assert "interactive" not in l4["modes"]
    assert d["tools"] and all("points" not in t for t in d["tools"])  # 工具卡要点需登录


@pytest.mark.parametrize("path", ["text", "video", "story", "progress?mode=text"])
def test_gated_endpoints_require_login(anon, path):
    assert anon.get(f"/api/courses/{CID}/lessons/l1/{path}").status_code == 401


def test_progress_write_requires_login(anon):
    assert anon.put(f"/api/courses/{CID}/lessons/l1/progress", json={"mode": "text", "done": True}).status_code == 401


def test_story_requires_login_even_for_l4(anon):
    assert anon.get(f"/api/courses/{CID}/lessons/l4/story").status_code == 401


def test_coming_course_has_no_lessons(anon):
    d = anon.get("/api/courses/ai-knowledge").json()
    assert d["status"] == "coming" and d["lessons"] == [] and d["topics"]


# ---------------- 登录后 ----------------
def test_user_can_read_text_and_story(user):
    r = user.get(f"/api/courses/{CID}/lessons/l2/text")
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert "常识4" in md and "常识5" not in md  # 第2课只保留 4 个常识，其余归入 AI知识普及
    r = user.get(f"/api/courses/{CID}/lessons/l4/story")
    assert r.status_code == 200
    d = r.json()
    assert 'class="story-meta"' in d["html"] and "<script" not in d["html"]
    assert d["progress"] == {"done": False, "state": None, "updated_at": 0}
    assert user.get(f"/api/courses/{CID}/lessons/l1/story").status_code == 404      # 制作中
    assert user.get(f"/api/courses/{CID}/lessons/l4/interactive").status_code == 404  # 旧学法已下线
    assert user.get(f"/api/courses/{CID}/lessons/l1/video").status_code == 404        # 视频制作中


def test_user_detail_has_tool_points(user):
    d = user.get(f"/api/courses/{CID}").json()
    assert d["logged_in"] is True and d["progress"] == {}
    assert all(t["points"] for t in d["tools"])


def test_progress_roundtrip(user):
    base = f"/api/courses/{CID}/lessons/l4/progress"
    assert user.get(base + "?mode=story").json() == {"done": False, "state": None, "updated_at": 0}
    st = {"v": 1, "cur": 12, "visited": [0, 1, 2], "gates": ["1"], "choice": {"1": "good"}, "pick": {}}
    r = user.put(base, json={"mode": "story", "state": st})
    assert r.status_code == 200 and r.json()["state"]["cur"] == 12 and r.json()["done"] is False
    # 故事页接口带回阅读进度（续读）
    assert user.get(f"/api/courses/{CID}/lessons/l4/story").json()["progress"]["state"]["choice"] == {"1": "good"}
    # 只更新 done 时保留已有 state
    r = user.put(base, json={"mode": "story", "done": True})
    assert r.json()["done"] is True and r.json()["state"]["cur"] == 12
    # done 只升不降
    r = user.put(base, json={"mode": "story", "done": False, "state": {"v": 1, "cur": 0}})
    assert r.json()["done"] is True and r.json()["state"] == {"v": 1, "cur": 0}
    # 重置：清空过程状态，保留完成标记
    r = user.delete(base + "?mode=story")
    assert r.json()["state"] is None and r.json()["done"] is True
    prog = user.get(f"/api/courses/{CID}").json()["progress"]
    assert prog["l4"]["story"]["done"] is True


def test_progress_isolated_between_users(user):
    user.put(f"/api/courses/{CID}/lessons/l1/progress", json={"mode": "text", "done": True})
    other = _user_client()
    try:
        assert other.get(f"/api/courses/{CID}").json()["progress"] == {}
    finally:
        other.__exit__(None, None, None)


@pytest.mark.parametrize("body,status", [
    ({"mode": "bogus", "done": True}, 400),
    ({"mode": "interactive", "done": True}, 400),
    ({"mode": "text", "state": {"x": "y" * (courses.STATE_MAX_BYTES + 10)}}, 413),
])
def test_progress_validation(user, body, status):
    assert user.put(f"/api/courses/{CID}/lessons/l1/progress", json=body).status_code == status


@pytest.mark.parametrize("path", [
    "/api/courses/ai-intro/lessons/l99/text",
    "/api/courses/nope/lessons/l1/text",
    "/api/courses/ai-knowledge/lessons/l1/text",
    "/api/courses/..%2F..%2Fetc/lessons/l1/text",
    "/api/courses/ai-intro/lessons/..%2Fcourse/text",
    "/api/courses/ai-intro/lessons/..%2Fcourse/story",
])
def test_unknown_or_traversal_404(user, path):
    assert user.get(path).status_code == 404


def test_bad_video_name_rejected(tmp_path, monkeypatch):
    """视频文件名只能是 videos/ 下的 mp4/webm，不能带路径。"""
    c = {"id": CID}
    for name in ("../lessons/l1.md", "a/b.mp4", "x.html", ""):
        assert courses._video_file(c, {"video": name}) is None
