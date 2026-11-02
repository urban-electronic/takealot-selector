"""卖家门户目录接口（takealot_portal_api）的解析与容错。

样本取自真实响应：PLID98340039 的 `/1/catalogue/mpv/PLID98340039`。
关键点是「一个请求同时给图和重尺」，以及「拿不到就留空、不猜」。
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Store  # noqa: F401  建表前必须先把 models 全部注册进来
from services import takealot_portal_api as portal


@pytest.fixture(autouse=True)
def _fresh_quota():
    """配额是进程级状态，用例之间必须清干净，否则会互相拖慢/串味。"""
    portal.QUOTA.reset()
    yield
    portal.QUOTA.reset()


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    """`_cipher()` 用服务端访问密钥派生，没有它就加不了密。"""
    monkeypatch.setenv("API_KEY", "test-master-key-for-portal-token-encryption")


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _payload(**overrides):
    base = {
        "productlineId": 98340039,
        "title": "scientific calculator",
        "coverImage": {
            "publicUrl": "https://media.takealot.com/covers_images/aaa/s-pdpxl.file",
            "zoomUrl": "https://media.takealot.com/covers_images/aaa/s-zoom.file",
        },
        "productlineImages": [
            {"zoomUrl": "https://media.takealot.com/covers_images/aaa/s-zoom.file"},
            {"zoomUrl": "https://media.takealot.com/covers_images/bbb/s-zoom.file"},
        ],
        "tsinImages": [],
        "merchantLineages": [
            "Office & Stationery → Office → Home & Office Electronics → Calculators → Scientific",
            "Office & Stationery → Stationery → Student Supplies → Calculate, Cut & Measure "
            "→ Calculators → Scientific",
        ],
        "variants": [
            {
                "tsinId": 99689186,
                "hasOffer": False,
                "bestPrice": {"price": 159.0},
                "attributes": {
                    "cbs_package_weight": {"name": "Cubiscan packaged weight",
                                           "value": "0.14 kg"},
                    "cbs_package_dimensions": {"name": "Cubiscan Packaged dimensions",
                                               "value": "88.0 x 190.0 x 26.0 mm"},
                    "merchant_packaged_weight": {"name": "Merchant Packaged Weight",
                                                 "value": "165 g"},
                    "merchant_packaged_dimensions": {"name": "Merchant Packaged Dimensions",
                                                     "value": "3 x 8 x 18 cm"},
                    "consolidated_packaged_dimensions": {
                        "name": "Consolidated Packaged Dimensions",
                        "value": "88.0 x 26.0 x 190.0 mm",
                    },
                },
            }
        ],
    }
    base.update(overrides)
    return base


class TestParseDimensions:
    def test_millimetres(self):
        assert portal.parse_dimensions("88.0 x 26.0 x 190.0 mm") == {
            "length_mm": 88.0, "width_mm": 26.0, "height_mm": 190.0}

    def test_centimetres_are_scaled(self):
        assert portal.parse_dimensions("3 x 8 x 18 cm") == {
            "length_mm": 30.0, "width_mm": 80.0, "height_mm": 180.0}

    def test_comma_decimal_separator(self):
        assert portal.parse_dimensions("1,5 x 2,5 x 3,5 cm") == {
            "length_mm": 15.0, "width_mm": 25.0, "height_mm": 35.0}

    def test_missing_unit_defaults_to_centimetres(self):
        # 这个字段没写单位时按 cm 处理，是最常见的写法
        assert portal.parse_dimensions("10 x 20 x 30") == {
            "length_mm": 100.0, "width_mm": 200.0, "height_mm": 300.0}

    def test_order_follows_upstream(self):
        # 顺序原样保留：门户页面显示哪个顺序，这里就存哪个，用户核对时不会对不上
        assert portal.parse_dimensions("190.0 x 88.0 x 26.0 mm") == {
            "length_mm": 190.0, "width_mm": 88.0, "height_mm": 26.0}

    @pytest.mark.parametrize("value", ["", None, "bad", "1 x 2", 12])
    def test_garbage_returns_none(self, value):
        assert portal.parse_dimensions(value) is None


class TestParseWeight:
    def test_kilograms(self):
        assert portal.parse_weight("0.14 kg") == 0.14

    def test_grams(self):
        assert portal.parse_weight("165 g") == 0.165

    def test_comma_decimal(self):
        assert portal.parse_weight("0,14 kg") == 0.14

    def test_pounds(self):
        assert portal.parse_weight("1.2 lb") == pytest.approx(0.544, abs=1e-3)

    @pytest.mark.parametrize("value", ["", None, "n/a"])
    def test_garbage_returns_none(self, value):
        assert portal.parse_weight(value) is None


class TestExtractDimensions:
    def test_consolidated_wins_over_merchant(self):
        dims = portal.extract_dimensions(_payload())
        assert dims["source"] == "consolidated"
        assert dims["length_mm"] == 88.0
        assert dims["height_mm"] == 190.0

    def test_falls_back_to_cubiscan_then_merchant(self):
        payload = _payload()
        attrs = payload["variants"][0]["attributes"]
        del attrs["consolidated_packaged_dimensions"]
        dims = portal.extract_dimensions(payload)
        assert dims["source"] == "cubiscan"

        del attrs["cbs_package_dimensions"]
        dims = portal.extract_dimensions(payload)
        assert dims["source"] == "merchant"
        assert dims["length_mm"] == 30.0

    def test_weight_comes_from_cubiscan_first(self):
        assert portal.extract_dimensions(_payload())["weight_kg"] == 0.14

    def test_prefers_variant_with_offer(self):
        payload = _payload()
        payload["variants"] = [
            {"hasOffer": False, "attributes": {}},
            {"hasOffer": True, "attributes": {
                "consolidated_packaged_dimensions": {"value": "10 x 20 x 30 mm"}}},
        ]
        dims = portal.extract_dimensions(payload)
        assert dims["length_mm"] == 10.0

    def test_no_dimensions_at_all_is_empty(self):
        payload = _payload()
        payload["variants"][0]["attributes"] = {}
        assert portal.extract_dimensions(payload) == {}


class TestExtractImages:
    def test_cover_image_is_first_and_deduped(self):
        urls = portal.extract_images(_payload())
        assert urls[0].endswith("/aaa/s-zoom.file")
        assert len(urls) == 2

    def test_missing_gallery_is_empty(self):
        assert portal.extract_images({}) == []


class TestExtractCategory:
    def test_deepest_lineage_wins(self):
        path = portal.extract_category(_payload())
        assert path.startswith("Office & Stationery > Stationery >")
        assert path.endswith("Scientific")
        # 分隔符必须和公开接口、爬虫一致，否则 fee_category_matcher 全失效
        assert ">" in path and "→" not in path

    def test_no_lineage_is_none(self):
        assert portal.extract_category({}) is None


class TestExtract:
    def test_shapes_match_the_public_api(self):
        record = portal.extract("98340039", _payload())
        assert record["success"] is True
        assert record["plid"] == "98340039"
        assert record["product_name"] == "scientific calculator"
        assert record["product_image_url"].endswith("s-zoom.file")
        assert record["takealot_category_path"]
        assert record["dimensions"]["length_mm"] == 88.0
        assert record["source"] == "seller_portal"


class TestFetch:
    def test_no_token_reports_not_configured(self):
        record = portal.fetch("98340039", "")
        assert record["success"] is False
        assert record["portal_not_configured"] is True

    def test_unknown_plid_is_reported(self):
        assert portal.fetch("abc", "a.b.c")["success"] is False

    def test_401_means_the_token_needs_refreshing(self, monkeypatch):
        class _Response:
            status_code = 401
            text = "{}"
            headers: dict = {}

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: _Response())
        record = portal.fetch("98340039", "a.b.c")
        assert record["portal_access_blocked"] is True
        assert "令牌已失效" in record["warnings"][0]

    def test_404_means_not_in_catalogue(self, monkeypatch):
        class _Response:
            status_code = 404
            text = "{}"
            headers: dict = {}

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: _Response())
        record = portal.fetch("11111111", "a.b.c")
        assert record["not_found"] is True

    def test_200_returns_the_full_record(self, monkeypatch):
        class _Response:
            status_code = 200
            headers: dict = {}

            def json(self):
                return _payload()

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: _Response())
        record = portal.fetch("98340039", "a.b.c")
        assert record["success"] is True
        assert record["dimensions"]["height_mm"] == 190.0


class TestFetchMany:
    def test_empty_input(self):
        assert portal.fetch_many([], "a.b.c") == []

    def test_dedupes_input(self, monkeypatch):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)

            class _Response:
                status_code = 404
                headers: dict = {}

            return _Response()

        monkeypatch.setattr(portal.httpx, "get", fake_get)
        records = portal.fetch_many(["1", "1", "2"], "a.b.c")
        assert len(records) == 2
        assert len(calls) == 2

    def test_stops_after_the_token_is_rejected(self, monkeypatch):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)

            class _Response:
                status_code = 401
                headers: dict = {}

            return _Response()

        monkeypatch.setattr(portal.httpx, "get", fake_get)
        # 10 个待读、4 个并发：令牌失效后不该把剩下的一路打完，否则只是
        # 拿回一片 401，白白撞人家的鉴权。
        records = portal.fetch_many([str(i) for i in range(10)], "a.b.c", workers=4)
        assert len(records) == 10
        assert len(calls) < 10
        assert any(r.get("portal_access_blocked") for r in records)


class TestTokenState:
    def test_no_token(self, db):
        assert portal.token_state(db)["configured"] is False

    def test_saved_token_reports_expiry(self, db):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.sig"
        portal.save_token(db, token)
        state = portal.token_state(db)
        assert state["configured"] is True
        assert state["expires_at"] == 9999999999
        assert state["expired"] is False

    def test_expired_token_is_flagged(self, db):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.sig"
        portal.save_token(db, token)
        assert portal.token_state(db)["expired"] is True

    def test_clearing_removes_it(self, db):
        portal.save_token(db, "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.sig")
        portal.save_token(db, "")
        assert portal.token_state(db)["configured"] is False


class TestProbe:
    def test_without_a_token_it_says_so(self, db):
        report = portal.probe(db, "98340039")
        assert report["ok"] is False
        assert "没有配置" in report["reason"]

    def test_expired_token_short_circuits(self, db):
        portal.save_token(db, "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.sig")
        report = portal.probe(db, "98340039")
        assert report["ok"] is False
        assert "过期" in report["reason"]

    def test_successful_probe_reports_dims_and_image(self, db, monkeypatch):
        portal.save_token(db, "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.sig")

        class _Response:
            status_code = 200
            headers: dict = {}

            def json(self):
                return _payload()

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: _Response())
        report = portal.probe(db, "98340039")
        assert report["ok"] is True
        assert report["image_count"] == 2
        assert report["dimensions"]["length_mm"] == 88.0


class TestQuota:
    """门户接口是 200 次/60 秒的硬配额。这些用例锁住「按配额推进、不判死」。"""

    def test_429_is_deferred_not_failed(self, monkeypatch):
        """429 是配额问题，等窗口就好——绝不能记成永久失败。

        上一版把 429 塞进 ACCESS_STOP_STATUS 且没有重试，结果 1804 个商品在第
        201 个开始整片放弃，一个重尺都没补上。
        """
        calls = []

        class _Response:
            status_code = 429
            headers: dict = {}
            text = '{"message":"Rate Limit Exceeded"}'

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: (calls.append(1), _Response())[1])
        # 退避会让它等满一个窗口，这里只验证「重试后推迟」这个行为
        monkeypatch.setattr(portal.QUOTA, "back_off", lambda: None)
        record = portal.fetch("98340039", "a.b.c")

        assert record["success"] is False
        assert record["budget_deferred"] is True
        assert record["portal_rate_limited"] is True
        assert not record.get("portal_access_blocked")
        # 重试过一次才放弃
        assert len(calls) == portal.MAX_429_RETRIES + 1

    def test_429_in_a_batch_does_not_stop_the_whole_batch(self, monkeypatch):
        """一批里有一条 429，其余照常读——不像 401 那样整批收手。"""
        seen = {"n": 0}

        class _Ok:
            status_code = 200
            headers: dict = {}

            def json(self):
                return _payload()

        class _Limited:
            status_code = 429
            headers: dict = {}

        def fake_get(url, **kwargs):
            seen["n"] += 1
            return _Limited() if seen["n"] == 1 else _Ok()

        monkeypatch.setattr(portal.httpx, "get", fake_get)
        # 关掉退避，否则这条用例要睡满 60 秒
        monkeypatch.setattr(portal.QUOTA, "back_off", lambda: None)
        records = portal.fetch_many(["1", "2", "3"], "a.b.c", workers=1, budget_seconds=20)

        assert len(records) == 3
        # 那条 429 重试后成功了，另外两条不受影响——不像 401 会整批收手
        assert all(r.get("success") for r in records)
        assert not any(r.get("portal_access_blocked") for r in records)
        assert seen["n"] == 4

    def test_server_remaining_exhaustion_makes_it_step_aside(self, monkeypatch):
        """服务端说 remaining 见底时，我们主动让开一个窗口而不是继续打。

        浏览器里同时开着门户页面时，配额是共享的——只看自己的计数会打空。
        """
        monkeypatch.setattr(portal.QUOTA, "observe", portal.QUOTA.observe)
        portal.QUOTA.observe({"x-ratelimit-remaining": "1"})

        waited = []
        monkeypatch.setattr(portal, "_sleep", lambda s: waited.append(s))

        # 算出来的等待时间应该接近一个窗口
        assert portal.QUOTA.wait_seconds() > portal.RATE_LIMIT_WINDOW_SECONDS

        # 预算只有 1 秒，等不到 → 直接推迟，不硬闯、也不白等
        assert portal.QUOTA.acquire(deadline=portal.time.monotonic() + 1.0) is False
        assert waited == [], "预算不够就别白等，直接留给下一轮"

    def test_acquire_respects_the_rolling_window(self, monkeypatch):
        """自己这 60 秒已经发满 188 个时，不再继续发。"""
        monkeypatch.setattr(portal, "RATE_LIMIT_LIMIT", 20)
        monkeypatch.setattr(portal, "RATE_LIMIT_RESERVE", 2)
        monkeypatch.setattr(portal, "_sleep", lambda s: None)

        granted = sum(1 for _ in range(30)
                      if portal.QUOTA.acquire(deadline=portal.time.monotonic() + 0.5))
        assert granted == 18

    def test_observe_tolerates_garbage(self):
        portal.QUOTA.observe({"x-ratelimit-remaining": "not-a-number"})
        portal.QUOTA.observe({})
        portal.QUOTA.observe(None)


# --------------------------------------------------------------- 品牌（蓝色品牌名）
#
# 有品牌的商品要清出选品池（授权/侵权风险），所以品牌必须抓得到。它在 mpv 里
# 不在顶层，藏在 `attributes.brand.value`——原来只读 `payload["brand"]`，
# 结果 1804 条只有 22 条有品牌，那 22 条还是走公开页拿到的。

def test_brand_comes_from_attributes():
    payload = {"attributes": {"brand": {"name": "Brand", "value": "HUAOI",
                                        "isDisplay": True}}}
    assert portal.extract_brand(payload) == "HUAOI"


def test_brand_strips_whitespace_and_prefers_top_level():
    assert portal.extract_brand({"brand": "  Mix Box  "}) == "Mix Box"
    assert portal.extract_brand({"brand": "Amigo",
                                 "attributes": {"brand": {"value": "别的"}}}) == "Amigo"


def test_brand_is_none_when_missing():
    """没品牌就是没品牌，不能返回空串冒充——导入时要靠 None/空 判断能不能留。"""
    assert portal.extract_brand({}) is None
    assert portal.extract_brand({"attributes": {}}) is None
    assert portal.extract_brand({"attributes": {"brand": {"value": "   "}}}) is None
    assert portal.extract_brand({"attributes": {"brand": "not-a-dict"}}) is None
