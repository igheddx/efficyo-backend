from app.core.password_policy import password_policy_error


def test_password_shorter_than_10_fails():
    assert password_policy_error("Short9!x") == "Use at least 10 characters."


def test_password_at_10_chars_passes():
    assert password_policy_error("GoodPass1!") is None


def test_password_longer_than_64_fails():
    assert password_policy_error("A" * 65) == "Password cannot be longer than 64 characters."


def test_password_with_spaces_and_special_characters_passes():
    assert password_policy_error("Good pass!") is None
