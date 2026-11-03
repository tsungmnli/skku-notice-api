"""The dev mailer reports the verify/reset link at INFO; uvicorn would drop it without setup."""

import logging

from app.main import create_app
from tests.conftest import make_settings


def test_app_info_logs_are_visible_and_not_duplicated():
    create_app(make_settings())
    create_app(make_settings())
    app_logger = logging.getLogger("app")
    assert app_logger.level == logging.INFO
    assert app_logger.isEnabledFor(logging.INFO)
    assert len(app_logger.handlers) == 1
