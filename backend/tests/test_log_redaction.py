"""A credential in a logged URL never reaches the log stream."""

from __future__ import annotations

import logging

from app.logging_setup import JsonFormatter, redact


def test_query_string_credentials_are_scrubbed():
    line = 'HTTP Request: GET https://api.example.com/a/b/7?api_key=s3cret&page=2 "HTTP/1.1 200 OK"'
    assert redact(line) == ('HTTP Request: GET https://api.example.com/a/b/7?api_key=***&page=2 '
                            '"HTTP/1.1 200 OK"')
    assert redact("?access_token=abc") == "?access_token=***"


def test_words_that_merely_end_in_key_are_left_alone():
    assert redact("monkey=1 turnkey=2") == "monkey=1 turnkey=2"


def test_the_formatter_scrubs_messages_and_tracebacks():
    try:
        raise RuntimeError("fetching https://api.example.com/x?api_key=s3cret failed")
    except RuntimeError:
        record = logging.LogRecord("httpx", logging.ERROR, __file__, 1,
                                   "GET %s", ("https://api.example.com/x?api_key=s3cret",),
                                   exc_info=True)
        import sys
        record.exc_info = sys.exc_info()
    assert "s3cret" not in JsonFormatter().format(record)
