from __future__ import annotations

import logging
import os
from datetime import datetime


class LoggerUtil:
    _logger: logging.Logger | None = None

    def __init__(self, name: str = "us_eps_scanner") -> None:
        if LoggerUtil._logger is None:
            LoggerUtil._logger = self._create_logger(name)

    def _create_logger(self, name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)

        if logger.handlers:
            return logger

        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"us_eps_scanner_{datetime.now():%Y%m%d}.log")

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        return logger

    def get_logger(self) -> logging.Logger:
        if LoggerUtil._logger is None:
            LoggerUtil._logger = self._create_logger("us_eps_scanner")
        return LoggerUtil._logger
