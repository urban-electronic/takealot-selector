"""`services.takealot_public_api` 的纯函数测试。

这些用例不联网：只测解析与取值逻辑。真实接口的形状由 `_fetch` 之外的
分支覆盖，网络行为不放进单测——否则 CI 里数据中心 IP 被 Cloudflare 挡住时
会出现「测试绿了但线上是坏的」，那种绿没有意义。
"""

import pytest

from services.takealot_public_api import (
    _category_path,
    _dimensions_and_weight,
    _first_price,
    _live_tsin,
    parse_plid,
)


class TestParsePlid:
    """PLID 少解析出一种写法，批量导入就会静默丢商品，所以逐个写法都要钉住。"""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("https://www.takealot.com/some-slug/PLID102501867", 102501867),
            ("https://www.takealot.com/some-slug/plid102501867", 102501867),
            ("https://www.takealot.com/some-slug?plid=102501867", 102501867),
            ("https://www.takealot.com/some-slug?PLID=102501867&x=1", 102501867),
            ("https://www.takealot.com/p/102501867", 102501867),
            ("PLID102501867", 102501867),
            ("102501867", 102501867),
            (102501867, 102501867),
        ],
    )
    def test_reads_plid_from_every_known_form(self, value, expected):
        assert parse_plid(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "no-id-here", "PLIDabc"])
    def test_returns_none_when_no_plid(self, value):
        assert parse_plid(value) is None


class TestDimensionsAndWeight:
    def _payload(self, name, text):
        return {"product_information": {"items": [
            {"display_name": name, "displayable_text": text}
        ]}}

    def test_converts_cm_dimensions_to_mm(self):
        # 商品属性写的是 cm，数据库存 mm，差一个数量级会让体积重算成 1/1000
        result = _dimensions_and_weight(self._payload("Assembled Dimensions", "22 x 7.2 x 17 cm"))
        assert result == {"length_mm": 220.0, "width_mm": 72.0, "height_mm": 170.0}

    def test_keeps_mm_dimensions_as_is(self):
        result = _dimensions_and_weight(self._payload("Assembled Dimensions", "220 x 72 x 170 mm"))
        assert result["length_mm"] == 220.0

    def test_converts_grams_to_kg(self):
        assert _dimensions_and_weight(self._payload("Weight", "380 g")) == {"weight_kg": 0.38}

    def test_reads_kilograms(self):
        assert _dimensions_and_weight(self._payload("Weight", "1.2 kg")) == {"weight_kg": 1.2}

    def test_accepts_comma_decimal(self):
        assert _dimensions_and_weight(self._payload("Weight", "1,2 kg")) == {"weight_kg": 1.2}

    def test_ignores_load_limit_attribute(self):
        """`maximum weight recommendation` 是承重上限，不是商品自重。

        置物架标 0.9 Kg 指的是能放多重的东西；当成自重会把运费算高、
        把利润算低，而且看起来完全合理，很难事后发现。
        """
        result = _dimensions_and_weight(
            self._payload("maximum weight recommendation", "0.9 Kg")
        )
        assert result == {}

    def test_ignores_capacity_attributes(self):
        assert _dimensions_and_weight(self._payload("Capacity", "3 ml")) == {}

    def test_returns_empty_when_no_usable_attribute(self):
        assert _dimensions_and_weight({"product_information": {"items": []}}) == {}


class TestCategoryPath:
    def test_joins_with_same_separator_as_scraper(self):
        """`fee_category_matcher` 按这个字符串做子串匹配，分隔符一变规则全失效。"""
        payload = {"breadcrumbs": {"items": [
            {"name": "Automotive"},
            {"name": "Automotive Accessories"},
            {"name": "Automotive Tools"},
        ]}}
        assert _category_path(payload) == "Automotive > Automotive Accessories > Automotive Tools"

    def test_returns_none_when_no_breadcrumbs(self):
        assert _category_path({}) is None


class TestLiveTsin:
    def test_prefers_buybox_tsin(self):
        assert _live_tsin({"buybox": {"tsin": 104438993}}) == "104438993"

    def test_falls_back_to_data_layer(self):
        assert _live_tsin({"data_layer": {"tsin": "TSIN104438993"}}) == "104438993"

    def test_returns_none_when_absent(self):
        assert _live_tsin({}) is None


class TestFirstPrice:
    def test_reads_price_from_first_buybox_item(self):
        assert _first_price({"buybox": {"items": [{"price": 3299.0}]}}) == 3299.0

    def test_returns_none_when_no_numeric_price(self):
        assert _first_price({"buybox": {"items": [{"price": None}]}}) is None
        assert _first_price({}) is None


class TestRetryPolicy:
    """隔离传输：受限即停；只有暂时性故障允许有限重试。"""

    @pytest.fixture(autouse=True)
    def _fresh_pacer(self, monkeypatch):
        """PACER 是模块级单例，用例之间必须复位，否则前一个用例吃到的 429
        会把后一个用例的节奏带慢（甚至影响真实跑批）。"""
        from services import takealot_public_api as api

        api.PACER.reset()
        monkeypatch.setattr(api.PACER, "wait", lambda stop_event=None, deadline=None: None)
        yield
        api.PACER.reset()

    @staticmethod
    def _fake_urlopen(responses):
        """按顺序吐出预置响应；元素是异常就抛，是 bytes 就当成响应体。"""
        calls = {"n": 0}

        class _Response:
            def __init__(self, body): self._body = body
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return self._body

        def opener(url, timeout=25.0):
            index = min(calls["n"], len(responses) - 1)
            calls["n"] += 1
            item = responses[index]
            if isinstance(item, BaseException):
                raise item
            return _Response(item)

        return opener, calls

    def test_retries_transient_503_then_succeeds(self, monkeypatch):
        import urllib.error
        from services import takealot_public_api as api

        opener, calls = self._fake_urlopen([
            urllib.error.HTTPError("u", 503, "Unavailable", None, None),
            urllib.error.HTTPError("u", 503, "Unavailable", None, None),
            b'{"ok": 1}',
        ])
        monkeypatch.setattr(api.urllib.request, "urlopen", opener)
        monkeypatch.setattr(api.time, "sleep", lambda seconds: None)

        assert api._http_json("https://example.invalid") == {"ok": 1}
        assert calls["n"] == 3

    def test_gives_up_after_the_last_retry(self, monkeypatch):
        import urllib.error
        from services import takealot_public_api as api

        opener, calls = self._fake_urlopen([
            urllib.error.HTTPError("u", 503, "Unavailable", None, None),
        ])
        monkeypatch.setattr(api.urllib.request, "urlopen", opener)
        monkeypatch.setattr(api.time, "sleep", lambda seconds: None)

        with pytest.raises(urllib.error.HTTPError):
            api._http_json("https://example.invalid")
        assert calls["n"] == 1 + api.MAX_RETRIES

    def test_does_not_retry_a_plain_404(self, monkeypatch):
        import urllib.error
        from services import takealot_public_api as api

        opener, calls = self._fake_urlopen([
            urllib.error.HTTPError("u", 404, "Not Found", None, None),
        ])
        monkeypatch.setattr(api.urllib.request, "urlopen", opener)
        monkeypatch.setattr(api.time, "sleep", lambda seconds: None)

        with pytest.raises(urllib.error.HTTPError):
            api._http_json("https://example.invalid")
        assert calls["n"] == 1


    @pytest.mark.parametrize("status", [401, 403, 429])
    def test_access_restriction_stops_without_retry(self, monkeypatch, status):
        import urllib.error
        from services import takealot_public_api as api
        opener, calls = self._fake_urlopen([
            urllib.error.HTTPError("https://example.invalid/private-marker", status,
                                   "private-marker", None, None),
            b'{"core":{"title":"Must not be fetched"}}',
        ])
        sleeps = []
        monkeypatch.setattr(api.urllib.request, "urlopen", opener)
        monkeypatch.setattr(api.time, "sleep", sleeps.append)
        result = api.fetch_product(123)
        assert result["success"] is False
        assert result["http_status"] == status
        assert calls["n"] == 1
        assert sleeps == []
        assert str(status) in result["warnings"][0]
        assert "private-marker" not in str(result)
        assert "安全验证" not in str(result)  # bare 403 does not prove CAPTCHA

    def test_truthful_request_headers(self, monkeypatch):
        from services import takealot_public_api as api
        seen = []
        opener, _ = self._fake_urlopen([b'{}'])
        def capture(request, timeout):
            seen.append(dict((k.lower(), v) for k, v in request.header_items()))
            return opener(request, timeout)
        monkeypatch.setattr(api.urllib.request, "urlopen", capture)
        api._http_json("https://example.invalid")
        assert seen == [{"user-agent": "takealot-selector/1.0", "accept": "application/json"}]

    @pytest.mark.parametrize("status", [401, 403, 429])
    def test_batch_stops_queued_reads_and_preserves_order(self, monkeypatch, status):
        import urllib.error
        from services import takealot_public_api as api
        opener, calls = self._fake_urlopen([
            urllib.error.HTTPError("u", status, "denied", None, None),
        ])
        monkeypatch.setattr(api.urllib.request, "urlopen", opener)
        monkeypatch.setattr(api.time, "sleep", lambda _: None)
        rows = api.fetch_many([123, 123, 456, 789, 987], workers=1)
        assert calls["n"] == 1
        assert [row["plid"] for row in rows] == [123, 456, 789, 987]
        assert rows[0]["http_status"] == status
        assert all(not row["success"] for row in rows)
        assert all(row["public_access_blocked"] and "http_status" not in row for row in rows[1:])
        assert all("停止" in row["warnings"][0] for row in rows[1:])

    def test_invalid_json_does_not_retry(self, monkeypatch):
        import json
        from services import takealot_public_api as api
        opener, calls = self._fake_urlopen([b'not-json', b'{}'])
        monkeypatch.setattr(api.urllib.request, 'urlopen', opener)
        monkeypatch.setattr(api.time, 'sleep', lambda _: pytest.fail('parse errors must not sleep'))
        with pytest.raises(json.JSONDecodeError):
            api._http_json('https://example.invalid')
        assert calls['n'] == 1

    def test_timeout_retries_are_bounded(self, monkeypatch):
        from services import takealot_public_api as api
        opener, calls = self._fake_urlopen([TimeoutError()])
        sleeps = []
        monkeypatch.setattr(api.urllib.request, 'urlopen', opener)
        monkeypatch.setattr(api.time, 'sleep', sleeps.append)
        with pytest.raises(TimeoutError):
            api._http_json('https://example.invalid')
        assert calls['n'] == 1 + api.MAX_RETRIES
        assert sleeps == [api._retry_delay(None,i) for i in range(api.MAX_RETRIES)]

    def test_batch_keeps_success_before_access_denied(self, monkeypatch):
        import urllib.error
        from services import takealot_public_api as api
        opener, calls = self._fake_urlopen([
            b'{"core":{"title":"Existing success"}}',
            urllib.error.HTTPError('u', 403, 'denied', None, None),
        ])
        monkeypatch.setattr(api.urllib.request, 'urlopen', opener)
        rows = api.fetch_many([123,456,789],workers=1)
        assert calls['n'] == 2
        assert rows[0]['success'] and rows[0]['product_name'] == 'Existing success'
        assert not rows[1]['success'] and not rows[2]['success']
        assert [row['plid'] for row in rows] == [123,456,789]

    def test_other_worker_denial_cancels_transient_retry(self, monkeypatch):
        from threading import Event
        import urllib.error
        from services import takealot_public_api as api
        backoff_started = Event()
        class ObservedStop(Event):
            def wait(self, timeout=None):
                backoff_started.set()
                return super().wait(2)  # 给另一线程调度余量，仍有明确上限。
        calls = []
        def opener(request, timeout=25):
            plid = request.full_url.rsplit('PLID',1)[1]
            calls.append(plid)
            if plid == '123':
                if calls.count(plid)>1:
                    return self._fake_urlopen([b'{"core":{"title":"must not retry"}}'])[0](request)
                raise urllib.error.HTTPError('u',503,'unavailable',None,None)
            assert plid == '456'
            assert backoff_started.wait(2), 'first worker did not enter backoff'
            raise urllib.error.HTTPError('u',403,'denied',None,None)
        monkeypatch.setattr(api, 'Event', ObservedStop)
        monkeypatch.setattr(api.urllib.request,'urlopen',opener)
        rows = api.fetch_many([123,456,789],workers=2)
        assert sorted(calls) == ['123','456']
        assert rows[0]['public_access_blocked'] is True
        assert not any(row['success'] for row in rows)
        assert [row['plid'] for row in rows] == [123,456,789]


    def test_429_penalizes_pace_but_does_not_retry(self, monkeypatch):
        import urllib.error
        from services import takealot_public_api as api
        opener,calls=self._fake_urlopen([urllib.error.HTTPError('u',429,'limited',None,None),b'{}'])
        monkeypatch.setattr(api.urllib.request,'urlopen',opener)
        before=api.PACER.interval
        with pytest.raises(urllib.error.HTTPError):api._http_json('https://example.invalid')
        assert calls['n']==1
        assert api.PACER.interval == pytest.approx(before*2)


class TestRetryDelay:
    """暂时性故障退避为0.6s/1.8s，Retry-After优先；401/403/429不进入重试。"""
    def test_503_uses_the_long_ramp(self):
        import urllib.error
        from services import takealot_public_api as api

        error = urllib.error.HTTPError("u", 503, "x", None, None)
        assert api._retry_delay(error, 0) == api.RETRY_DELAYS[0]
        assert api._retry_delay(error, 1) == api.RETRY_DELAYS[1]

    def test_503_clamps_at_the_last_step(self):
        """重试次数配得比退避表长时不能 IndexError。"""
        import urllib.error
        from services import takealot_public_api as api

        error = urllib.error.HTTPError("u", 503, "x", None, None)
        assert api._retry_delay(error, 99) == api.RETRY_DELAYS[-1]

    def test_other_status_uses_the_short_ramp(self):
        import urllib.error
        from services import takealot_public_api as api

        error = urllib.error.HTTPError("u", 403, "x", None, None)
        assert api._retry_delay(error, 0) == api.RETRY_DELAYS[0]

    def test_honours_retry_after(self):
        import urllib.error
        from services import takealot_public_api as api

        error = urllib.error.HTTPError("u", 503, "x", {"Retry-After": "7"}, None)
        assert api._retry_delay(error, 0) == 7.0

    def test_clamps_absurd_retry_after(self):
        """服务端给 3600 时不能真等一小时——一次点击会把整个请求挂死。"""
        import urllib.error
        from services import takealot_public_api as api

        error = urllib.error.HTTPError("u", 503, "x", {"Retry-After": "3600"}, None)
        assert api._retry_delay(error, 0) == 30.0

    def test_ignores_unparsable_retry_after(self):
        import urllib.error
        from services import takealot_public_api as api

        error = urllib.error.HTTPError(
            "u", 503, "x", {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, None
        )
        assert api._retry_delay(error, 0) == api.RETRY_DELAYS[0]


class TestPacer:
    """节流器：请求之间强制间隔，被限流就放慢且不再调回去。"""

    @staticmethod
    def _freeze(monkeypatch, api, start=100.0):
        """把 monotonic 钉死、sleep 改成推进假时钟，用例才能断言「等了多久」。"""
        clock = {"t": start}
        slept = []

        def fake_sleep(seconds):
            slept.append(seconds)
            clock["t"] += seconds

        monkeypatch.setattr(api.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(api.time, "sleep", fake_sleep)
        return slept

    def test_first_request_does_not_wait(self, monkeypatch):
        from services import takealot_public_api as api

        slept = self._freeze(monkeypatch, api)
        api._Pacer(interval=0.5).wait()
        assert slept == []

    def test_keeps_a_minimum_gap_between_requests(self, monkeypatch):
        from services import takealot_public_api as api

        slept = self._freeze(monkeypatch, api)
        pacer = api._Pacer(interval=0.5)
        pacer.wait()
        pacer.wait()
        pacer.wait()
        assert slept == [pytest.approx(0.5), pytest.approx(0.5)]

    def test_penalize_doubles_and_caps(self, monkeypatch):
        from services import takealot_public_api as api

        pacer = api._Pacer(interval=0.2, max_interval=0.5)
        pacer.penalize()
        assert pacer.interval == pytest.approx(0.4)
        pacer.penalize()
        assert pacer.interval == pytest.approx(0.5)  # 撞上限就停住
        pacer.penalize()
        assert pacer.interval == pytest.approx(0.5)

    def test_reset_restores_the_initial_pace(self, monkeypatch):
        from services import takealot_public_api as api

        pacer = api._Pacer(interval=0.2)
        pacer.penalize()
        pacer.reset()
        assert pacer.interval == pytest.approx(0.2)


class TestPacerStop:
    def test_cancelled_pacing_does_not_send_transport(self, monkeypatch):
        from threading import Event
        from services import takealot_public_api as api
        stop = Event()
        def reject_during_pacing(event):
            assert event is stop
            event.set()
        monkeypatch.setattr(api.PACER, 'wait', reject_during_pacing)
        monkeypatch.setattr(api, '_request_json', lambda *a: pytest.fail('request sent after pacing stop'))
        with pytest.raises(api.PublicReadStopped):
            api._http_json('https://example.invalid', stop_event=stop)

    def test_stop_interrupts_reserved_pacing_slot(self, monkeypatch):
        from threading import Event
        from services import takealot_public_api as api
        monkeypatch.setattr(api.time, 'monotonic', lambda: 100.0)
        pacer=api._Pacer(interval=0.5)
        pacer.wait()
        stop=Event()
        delays=[]
        def cancel(delay):
            delays.append(delay)
            stop.set()
            return True
        monkeypatch.setattr(stop,'wait',cancel)
        monkeypatch.setattr(api.time,'sleep',lambda _: pytest.fail('pacing sleep not interruptible'))
        with pytest.raises(api.PublicReadStopped):pacer.wait(stop)
        assert delays == [pytest.approx(0.5)]


class TestFetchBudget:
    """一轮请求能抓多少个。切这一刀是为了别让单个请求跑到被代理掐断。"""

    def test_normal_pace_fits_a_full_chunk(self):
        from services import takealot_public_api as api

        api.PACER.reset()
        assert api.fetch_budget(45.0) == 225   # 0.2 秒一个

    def test_throttled_pace_shrinks_the_batch(self):
        """间隔被放大到 2 秒时，45 秒只够 22 个——这才是真实的可用量。"""
        from services import takealot_public_api as api

        api.PACER.interval = 2.0
        try:
            assert api.fetch_budget(45.0) == 22
        finally:
            api.PACER.reset()

    def test_never_returns_zero(self):
        """间隔大到离谱时也得给 1 个，否则前端会以为「没有要抓的」直接停。"""
        from services import takealot_public_api as api

        api.PACER.interval = 9999.0
        try:
            assert api.fetch_budget(45.0) == 1
        finally:
            api.PACER.reset()


class TestHeaderValue:
    """响应头读取要不区分大小写。

    HTTP/2 里响应头是小写的（`retry-after`），urllib 给的是 `email.Message`
    （本身不区分大小写，但测试和未来换库时未必）。只按 `Retry-After` 查会漏，
    于是服务端说了「等 12 秒」，我们还按 3 秒硬撞。
    """

    def test_case_insensitive(self):
        from services import takealot_public_api as api

        assert api._header_value({"retry-after": "5"}, "Retry-After") == "5"
        assert api._header_value({"Retry-After": "5"}, "retry-after") == "5"

    def test_missing_or_none(self):
        from services import takealot_public_api as api

        assert api._header_value(None, "Retry-After") is None
        assert api._header_value({}, "Retry-After") is None

    def test_accepts_email_message(self):
        from email.message import Message

        from services import takealot_public_api as api

        message = Message()
        message["Retry-After"] = "9"
        assert api._header_value(message, "retry-after") == "9"


class TestBatchDeadline:
    def test_deadline_returns_completed_rows_without_waiting_for_running_read(self, monkeypatch):
        from threading import Event
        import time
        from services import takealot_public_api as api
        release = Event()
        entered = Event()
        calls = []
        exited = Event()
        def read(plid, **kwargs):
            calls.append(plid)
            if plid == 123:
                return {"plid": plid, "success": True, "product_name": "kept"}
            entered.set()
            assert release.wait(2)
            exited.set()
            return {"plid": plid, "success": True, "product_name": "late"}
        monkeypatch.setattr(api, 'fetch_product', read)
        monkeypatch.setattr(api, 'BATCH_BUDGET_SECONDS', 0.05, raising=False)
        started = time.monotonic()
        try:
            rows = api.fetch_many([123,456,789], workers=1)
            assert entered.is_set()
            assert time.monotonic() - started < 0.5
            assert rows[0]['success'] and rows[0]['product_name'] == 'kept'
            assert all(row['budget_deferred'] and not row['success'] for row in rows[1:])
            assert all(not row.get('public_access_blocked') for row in rows)
            second = api.fetch_many([456,789], workers=1)
            assert all(row['budget_deferred'] for row in second)
            assert calls == [123,456]  # 旧线程退出之前不能再开新批读取。
        finally:
            release.set()
            assert exited.wait(1)
        assert calls == [123,456]  # 排队的789不得在返回后再发出。

    def test_deadline_caps_transport_and_prevents_retry(self, monkeypatch):
        from services import takealot_public_api as api
        now = [100.0]
        monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
        monkeypatch.setattr(api.PACER, 'wait', lambda *a, **k: None)
        calls = []
        def request(url, timeout):
            calls.append(timeout)
            now[0] = 103.0
            raise TimeoutError()
        monkeypatch.setattr(api, '_request_json', request)
        with pytest.raises(api.PublicBudgetExpired):
            api._http_json('https://example.invalid', deadline=103.0)
        assert calls == [pytest.approx(3.0)]

    def test_pacing_does_not_wait_past_deadline(self, monkeypatch):
        from services import takealot_public_api as api
        now = [100.0]
        monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
        delays = []
        def sleep(delay):
            delays.append(delay)
            now[0] += delay
        monkeypatch.setattr(api.time, 'sleep', sleep)
        pacer = api._Pacer(interval=20)
        pacer.wait()
        with pytest.raises(api.PublicBudgetExpired):
            pacer.wait(deadline=103.0)
        assert delays == [pytest.approx(3.0)]


    def test_retry_backoff_is_capped_by_shared_deadline(self, monkeypatch):
        from services import takealot_public_api as api
        import urllib.error
        now = [100.0]
        delays = []
        monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
        monkeypatch.setattr(api.PACER, 'wait', lambda *a, **k: None)
        def sleep(delay):
            delays.append(delay)
            now[0] += delay
        monkeypatch.setattr(api.time, 'sleep', sleep)
        calls = []
        def request(url, timeout):
            calls.append(timeout)
            raise urllib.error.HTTPError('u', 503, 'unavailable', {'Retry-After':'30'}, None)
        monkeypatch.setattr(api, '_request_json', request)
        with pytest.raises(api.PublicBudgetExpired):
            api._http_json('https://example.invalid', deadline=103.0)
        assert calls == [pytest.approx(3.0)]
        assert delays == [pytest.approx(3.0)]


    def test_late_denial_stops_next_batch_before_new_transport(self, monkeypatch):
        from threading import Event
        from services import takealot_public_api as api
        release = Event()
        finished = Event()
        calls = []
        def read(plid, **kwargs):
            calls.append(plid)
            assert release.wait(2)
            return {"plid": plid, "success": False, "http_status": 403}
        monkeypatch.setattr(api, 'fetch_product', read)
        monkeypatch.setattr(api, 'BATCH_BUDGET_SECONDS', 0.02)
        # 回调全部执行后再继续，不能靠sleep猜线程已退出。
        original_release = api._BATCH_LEASE
        class Lease:
            def acquire(self, **kwargs): return original_release.acquire(**kwargs)
            def release(self):
                original_release.release()
                finished.set()
        monkeypatch.setattr(api, '_BATCH_LEASE', Lease())
        try:
            first = api.fetch_many([123], workers=1)
            assert first[0]['budget_deferred']
            assert api.fetch_many([123], workers=1)[0]['budget_deferred']
            release.set()
            assert finished.wait(1)
            later = api.fetch_many([123,456], workers=1)
            assert all(row['public_access_blocked'] for row in later)
            assert calls == [123]
        finally:
            release.set()
            api._LATE_ACCESS_DENIAL.clear()

    def test_lease_covers_result_delivery_before_clearing_denial(self, monkeypatch):
        from threading import Event, Thread, current_thread
        from services import takealot_public_api as api
        held = Event()
        resume = Event()
        calls = []
        results = []
        errors = []
        original_wait = api.wait
        def controlled_wait(*args, **kwargs):
            result = original_wait(*args, **kwargs)
            if current_thread().name == 'held-coordinator':
                held.set()
                assert resume.wait(2)
            return result
        def read(plid, **kwargs):
            calls.append(plid)
            return {'plid': plid, 'success': False, 'http_status': 403}
        monkeypatch.setattr(api, 'wait', controlled_wait)
        monkeypatch.setattr(api, 'fetch_product', read)
        def run():
            try: results.extend(api.fetch_many([123], workers=1))
            except BaseException as error: errors.append(error)
        thread = Thread(target=run, name='held-coordinator')
        thread.start()
        try:
            assert held.wait(1)
            # future已退出但A尚未交付结果：B/C都必须被租约挡住。
            for plid in [456,789]:
                assert api.fetch_many([plid], workers=1)[0]['budget_deferred']
            assert calls == [123]
        finally:
            resume.set()
            thread.join(1)
        assert not thread.is_alive() and not errors
        assert results[0]['http_status'] == 403
        assert not api._LATE_ACCESS_DENIAL.is_set()
