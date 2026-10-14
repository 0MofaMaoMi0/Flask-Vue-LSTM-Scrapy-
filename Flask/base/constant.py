"""Third-party payment settings loaded from local environment configuration."""

from pathlib import Path

from config import required


def _read_pem(path_setting):
    path = Path(required(path_setting)).expanduser()
    if not path.is_file():
        raise RuntimeError("Configured key file does not exist: %s" % path_setting)
    return path.read_text(encoding="utf-8")


def get_alipay_settings():
    """Return the payment settings only when the payment endpoint is invoked."""
    return {
        "appid": required("ALIPAY_APP_ID"),
        "notify_url": required("ALIPAY_NOTIFY_URL"),
        "app_private_key_string": _read_pem("ALIPAY_APP_PRIVATE_KEY_PATH"),
        "alipay_public_key_string": _read_pem("ALIPAY_PUBLIC_KEY_PATH"),
    }
