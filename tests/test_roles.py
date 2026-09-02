"""Проверка доступа по ролям — ARCHITECTURE.md §3.

Роли читаются из переменных окружения при импорте core.roles, поэтому тесты
подставляют окружение и переимпортируют модуль, а не мутируют его множества
напрямую.
"""
import importlib
import os


def _reload_roles():
    import core.roles as roles

    return importlib.reload(roles)


def test_priority_admin_over_engineer_and_manager():
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["ENGINEER_USER_IDS"] = "111"
    os.environ["MANAGER_USER_IDS"] = "111"
    roles = _reload_roles()
    assert roles.get_role(111) is roles.Role.ADMIN


def test_priority_director_below_admin_and_above_engineer():
    """Руководитель попадёт и в другие списки — техдиректор вполне может быть
    заведён инженером. Роль всё равно одна, и она должна быть старшей."""
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = "111,222"
    os.environ["ENGINEER_USER_IDS"] = "222"
    os.environ["MANAGER_USER_IDS"] = ""
    roles = _reload_roles()
    assert roles.get_role(111) is roles.Role.ADMIN
    assert roles.get_role(222) is roles.Role.DIRECTOR


def test_director_title_comes_from_env_next_to_the_id():
    """У основателя и у техдиректора подписи разные, а роль одна — поэтому
    подпись живёт рядом с ID, а не в коде."""
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = "222:Руководитель CNC Electric,333"
    os.environ["ENGINEER_USER_IDS"] = ""
    os.environ["MANAGER_USER_IDS"] = ""
    roles = _reload_roles()
    assert roles.get_role(222) is roles.Role.DIRECTOR
    assert roles.DIRECTOR_TITLES[222] == "Руководитель CNC Electric"


def test_director_without_a_title_is_still_a_director():
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = "222:Руководитель CNC Electric,333"
    os.environ["ENGINEER_USER_IDS"] = ""
    os.environ["MANAGER_USER_IDS"] = ""
    roles = _reload_roles()
    assert roles.get_role(333) is roles.Role.DIRECTOR
    assert 333 not in roles.DIRECTOR_TITLES


def test_engineer_role():
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = ""
    os.environ["ENGINEER_USER_IDS"] = "222"
    os.environ["MANAGER_USER_IDS"] = "333"
    roles = _reload_roles()
    assert roles.get_role(222) is roles.Role.ENGINEER


def test_manager_role():
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = ""
    os.environ["ENGINEER_USER_IDS"] = "222"
    os.environ["MANAGER_USER_IDS"] = "333"
    roles = _reload_roles()
    assert roles.get_role(333) is roles.Role.MANAGER


def test_unknown_id_gets_unknown_role():
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = ""
    os.environ["ENGINEER_USER_IDS"] = "222"
    os.environ["MANAGER_USER_IDS"] = "333"
    roles = _reload_roles()
    assert roles.get_role(999) is roles.Role.UNKNOWN
    assert roles.get_role(None) is roles.Role.UNKNOWN


def test_unknown_message_shows_telegram_id():
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["DIRECTOR_USER_IDS"] = ""
    os.environ["ENGINEER_USER_IDS"] = ""
    os.environ["MANAGER_USER_IDS"] = ""
    roles = _reload_roles()
    text = roles.rejection_text(424242)
    assert "424242" in text
    assert "администратор" in text.lower()


def test_malformed_id_is_ignored_not_crashed():
    os.environ["ADMIN_USER_IDS"] = "111, not-a-number, 222"
    os.environ["DIRECTOR_USER_IDS"] = ""
    os.environ["ENGINEER_USER_IDS"] = ""
    os.environ["MANAGER_USER_IDS"] = ""
    roles = _reload_roles()
    assert roles.get_role(111) is roles.Role.ADMIN
    assert roles.get_role(222) is roles.Role.ADMIN
    assert roles.get_role(333) is roles.Role.UNKNOWN
