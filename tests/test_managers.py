import managers


def test_moscow_goes_to_central_district():
    m = managers.manager_for_city("Москва")
    assert m.email == "kmk@cncrussia.com"
    assert "ЦФО" in m.districts


def test_novosibirsk_and_vladivostok_go_to_ural_manager():
    for city in ("Новосибирск", "Владивосток"):
        assert managers.manager_for_city(city).email == "an@cncrussia.com"


def test_makhachkala_goes_to_south_manager():
    assert managers.manager_for_city("Махачкала").email == "aam@cncrussia.com"


def test_city_is_matched_case_and_space_insensitively():
    assert managers.manager_for_city("  санкт-петербург ").email == "ar@cncrussia.com"


def test_unknown_city_has_no_manager():
    assert managers.manager_for_city("Ереван") is None


def test_fallback_text_names_only_the_general_address():
    assert "info@cncrussia.com" in managers.FALLBACK_TEXT
    assert "ЦФО" not in managers.FALLBACK_TEXT


def test_format_manager_shows_name_phone_and_email():
    text = managers.format_manager(managers.manager_for_city("Самара"))
    assert "Искорнев" in text
    assert "+7 (917) 107-54-89" in text
    assert "is@cncrussia.com" in text


def test_telegram_ids_come_from_env(monkeypatch):
    monkeypatch.setattr(managers, "_telegram_ids", lambda: {"is@cncrussia.com": 900003})
    assert managers.manager_for_city("Самара").user_id == 900003


def test_bratsk_is_routed_and_no_city_key_contains_a_space_separated_pair():
    assert managers.manager_for_city("Братск").email == "an@cncrussia.com"
    # Ключ из двух слов через пробел — почти всегда пропущенная запятая в списке.
    # Настоящие двусловные названия пишутся через дефис или начинаются с
    # «Нижний», «Великий», «Набережные», поэтому проверяем именно
    # склейку двух самостоятельных городов.
    suspicious = [
        city for city in managers.CITY_TO_DISTRICT
        if " " in city and not city.startswith(("нижний", "великий", "набережные", "ростов"))
    ]
    assert suspicious == [], f"похоже на пропущенную запятую: {suspicious}"
