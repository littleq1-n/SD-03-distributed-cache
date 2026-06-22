import pytest

from distcache import protocol
from distcache.protocol import Parser


def test_encode_responses():
    assert protocol.encode_simple("OK") == b"+OK\r\n"
    assert protocol.encode_error("ERR boom") == b"-ERR boom\r\n"
    assert protocol.encode_bulk(b"bar") == b"$3\r\nbar\r\n"
    assert protocol.encode_bulk(None) == b"$-1\r\n"


def test_encode_command_resp_array():
    assert protocol.encode_command(b"SET", b"foo", b"bar") == (
        b"*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n"
    )


def test_parse_inline():
    p = Parser()
    p.feed(b"SET foo bar\r\n")
    assert p.parse_one() == [b"SET", b"foo", b"bar"]
    assert p.parse_one() is None


def test_parse_inline_bare_newline():
    p = Parser()
    p.feed(b"PING\n")
    assert p.parse_one() == [b"PING"]


def test_parse_resp_array():
    p = Parser()
    p.feed(b"*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n")
    assert p.parse_one() == [b"SET", b"foo", b"bar"]


def test_half_packet_waits():
    p = Parser()
    p.feed(b"*3\r\n$3\r\nSET\r\n$3\r\nfo")  # 不完整
    assert p.parse_one() is None
    p.feed(b"o\r\n$3\r\nbar\r\n")  # 补齐
    assert p.parse_one() == [b"SET", b"foo", b"bar"]


def test_pipelined_commands_split():
    p = Parser()
    p.feed(b"PING\r\nGET foo\r\n")
    assert p.parse_one() == [b"PING"]
    assert p.parse_one() == [b"GET", b"foo"]
    assert p.parse_one() is None


def test_bulk_value_across_reads():
    p = Parser()
    p.feed(b"*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$5\r\nhe")
    assert p.parse_one() is None
    p.feed(b"llo\r\n")
    assert p.parse_one() == [b"SET", b"foo", b"hello"]


def test_resp_carries_binary_and_spaces():
    payload = b"a b\x00c"
    cmd = protocol.encode_command(b"SET", b"k", payload)
    p = Parser()
    p.feed(cmd)
    args = p.parse_one()
    assert args == [b"SET", b"k", payload]


def test_decode_resp_array():
    payload = protocol.encode_command(b"DEL", b"x")
    assert protocol.decode_resp_array(payload) == [b"DEL", b"x"]


def test_invalid_multibulk_raises():
    p = Parser()
    p.feed(b"*abc\r\n")
    with pytest.raises(protocol.ProtocolError):
        p.parse_one()
