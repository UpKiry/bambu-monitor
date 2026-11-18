"""Account forms accepted by the selected cloud region; no account persistence."""
import re


def is_email(value):
    return (isinstance(value, str) and 0 < len(value) <= 254
            and value.count("@") == 1 and all(value.split("@"))
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value))


def is_cn_phone(value):
    # Current local support is domestic-format CN numbers; country-code forms
    # require separate protocol evidence and live validation.
    return isinstance(value, str) and re.fullmatch(r"1[3-9][0-9]{9}", value) is not None


def valid_account(value, region):
    return is_email(value) or region == "cn" and is_cn_phone(value)
