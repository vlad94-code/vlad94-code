"""SMTP изолирован: тесты не открывают ни одного соединения."""
import smtplib

import pytest

import mailer


class FakeSMTP:
    sent = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        FakeSMTP.user = user

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.mail.ru")
    monkeypatch.setenv("SMTP_USER", "help@cncrussia.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SUPPORT_EMAIL", "help@cncrussia.com")


def test_send_delivers_to_support_by_default():
    assert mailer.send("Вопрос №1", "текст") is True
    message = FakeSMTP.sent[0]
    assert message["To"] == "help@cncrussia.com"
    assert message["Subject"] == "Вопрос №1"


def test_send_reports_failure_instead_of_raising(monkeypatch):
    def explode(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad password")

    monkeypatch.setattr(smtplib, "SMTP_SSL", explode)
    assert mailer.send("тема", "текст") is False


def test_not_configured_without_password(monkeypatch):
    monkeypatch.delenv("SMTP_PASSWORD")
    assert mailer.configured() is False
    assert mailer.send("тема", "текст") is False


def test_attachment_is_included():
    mailer.send("тема", "текст", attachments=[("акт.docx", b"PK\x03\x04")])
    names = [part.get_filename() for part in FakeSMTP.sent[0].iter_attachments()]
    assert "акт.docx" in names
