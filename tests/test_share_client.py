"""share_client — доступ к файлам «Ориентира» (25.lgprk.ru).

Ссылка из таблицы номенклатуры ведёт не на файл, а на SPA-страницу с кнопкой
«Скачать файл»: Telegram по ней покажет HTML, а не картинку. Настоящий файл
достаётся в два шага — POST /api/share/verify отдаёт presigned-ссылку на S3
(живёт час), и уже по ней качаются байты.

Тесты гоняют настоящий httpx через MockTransport, а не подменяют сам клиент:
проверять надо разбор ответа чужого сервиса, а не то, что мок был вызван.
"""
import asyncio

import httpx
import pytest

import share_client

_SHARE = "https://25.lgprk.ru/share?token=20644a7a-1661-48f1-b4cd-c881987105c246c638fa"
_TOKEN = "20644a7a-1661-48f1-b4cd-c881987105c246c638fa"
_DOWNLOAD = "https://s3.lgprk.ru/attachments/cb1e4c7b?X-Amz-Signature=af1235"


def _verify_payload(name="YCM3YP-100.png", size=592210, granted=True):
    """Форма ответа /api/share/verify, снятая с живого сервиса 29.08.2026."""
    return {
        "access_granted": granted,
        "entity_type": "file",
        "entity_data": {
            "id": "cb1e4c7b-cbb4-4542-a3e1-635b8fd34d8f",
            "name": name,
            "fileType": "png",
            "version": 1,
            "byteSize": size,
            "downloadUrl": _DOWNLOAD,
        },
    }


def _serve(handler):
    """Подсунуть share_client транспорт, отвечающий по правилам теста."""
    def _factory(timeout=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
    return _factory


@pytest.fixture
def transport(monkeypatch):
    def _install(handler):
        monkeypatch.setattr(share_client, "_client", _serve(handler))
    return _install


# --- Разбор ссылки -----------------------------------------------------------

def test_token_is_taken_from_the_share_link():
    assert share_client.token_of(_SHARE) == _TOKEN


def test_a_link_to_a_plain_file_has_no_share_token():
    assert share_client.token_of("https://cncrussia.com/uploads/passports/ycm3_pasport.pdf") is None


def test_a_share_link_without_a_token_is_not_a_share_link():
    assert share_client.token_of("https://25.lgprk.ru/share") is None


# --- resolve -----------------------------------------------------------------

def test_resolve_returns_the_real_download_url_and_file_name(transport):
    def handler(request):
        assert request.url.path == "/api/share/verify"
        assert b'"token"' in request.content and _TOKEN.encode() in request.content
        return httpx.Response(200, json=_verify_payload())

    transport(handler)
    resolved = asyncio.run(share_client.resolve(_SHARE))
    assert resolved.download_url == _DOWNLOAD
    assert resolved.name == "YCM3YP-100.png"
    assert resolved.size == 592210


def test_resolve_is_silent_when_the_share_is_closed(transport):
    """Шару могли отозвать или закрыть паролем — это не ошибка бота."""
    transport(lambda request: httpx.Response(200, json={"access_granted": False, "entity_type": "file"}))
    assert asyncio.run(share_client.resolve(_SHARE)) is None


def test_resolve_is_silent_when_the_service_is_down(transport):
    transport(lambda request: httpx.Response(502, text="bad gateway"))
    assert asyncio.run(share_client.resolve(_SHARE)) is None


def test_resolve_is_silent_when_the_answer_carries_no_download_url(transport):
    payload = _verify_payload()
    del payload["entity_data"]["downloadUrl"]
    transport(lambda request: httpx.Response(200, json=payload))
    assert asyncio.run(share_client.resolve(_SHARE)) is None


# --- fetch -------------------------------------------------------------------

def test_fetch_returns_the_bytes_behind_the_share_page(transport):
    def handler(request):
        if request.url.path == "/api/share/verify":
            return httpx.Response(200, json=_verify_payload())
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n binary")

    transport(handler)
    assert asyncio.run(share_client.fetch(_SHARE)) == (b"\x89PNG\r\n\x1a\n binary", "YCM3YP-100.png")


def test_fetch_refuses_a_file_over_the_limit_without_downloading_it(transport):
    """Размер известен ещё из verify — качать 60 МБ, чтобы потом их выбросить,
    незачем: Telegram всё равно откажет."""
    def handler(request):
        if request.url.path == "/api/share/verify":
            return httpx.Response(200, json=_verify_payload(size=60 * 1024 * 1024))
        raise AssertionError("скачивать файл сверх лимита не нужно")

    transport(handler)
    assert asyncio.run(share_client.fetch(_SHARE, size_limit=50 * 1024 * 1024)) is None


def test_fetch_is_silent_when_the_presigned_link_has_expired(transport):
    def handler(request):
        if request.url.path == "/api/share/verify":
            return httpx.Response(200, json=_verify_payload())
        return httpx.Response(403, content=b"")

    transport(handler)
    assert asyncio.run(share_client.fetch(_SHARE)) is None


def test_fetch_downloads_a_plain_link_directly(transport):
    """Следующая версия таблицы может прийти с прямыми ссылками — тогда
    промежуточный шаг не нужен, а поведение должно остаться прежним."""
    def handler(request):
        assert request.url.host == "cncrussia.com"
        return httpx.Response(200, content=b"%PDF-1.4")

    transport(handler)
    url = "https://cncrussia.com/uploads/passports/ycm3_pasport.pdf"
    assert asyncio.run(share_client.fetch(url)) == (b"%PDF-1.4", "ycm3_pasport.pdf")


def test_fetch_gives_up_instead_of_hanging_on_a_slow_service(monkeypatch):
    """timeout httpx считается на операцию: сервис, отдающий файл по байту,
    держал бы обработчик бесконечно. Бюджет на всю загрузку — общий."""
    async def never_ends(url, size_limit):
        await asyncio.sleep(5)
        return b"", "late.png"

    monkeypatch.setattr(share_client, "_fetch", never_ends)
    monkeypatch.setattr(share_client, "TOTAL_TIMEOUT", 0.05)
    assert asyncio.run(share_client.fetch(_SHARE)) is None
