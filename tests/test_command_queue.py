import pytest

from openlaser.machine.command_queue import QueueError, StreamingQueue


def test_character_counting_respects_buffer():
    q = StreamingQueue(rx_buffer_size=20, max_line=20)
    for _ in range(5):
        q.enqueue("G1X10Y10")  # 9 bytes con \n
    sent = []
    while (c := q.next_sendable()) is not None:
        q.mark_sent(c, 0.0)
        sent.append(c)
    assert len(sent) == 2 and q.in_flight_bytes == 18
    q.acknowledge()
    assert q.next_sendable() is not None


def test_exclusive_waits_for_empty_buffer():
    q = StreamingQueue()
    q.enqueue("G0X1")
    q.enqueue("$H")
    q.enqueue("G0X2")
    first = q.next_sendable()
    q.mark_sent(first, 0)
    assert q.next_sendable() is None  # $H espera a que se confirme G0X1
    q.acknowledge()
    h = q.next_sendable()
    assert h.text == "$H"
    q.mark_sent(h, 0)
    assert q.next_sendable() is None  # nada tras $H hasta su ok
    q.acknowledge()
    assert q.next_sendable().text == "G0X2"


def test_ack_order_and_clear():
    q = StreamingQueue()
    a = q.enqueue("G0X1", "job")
    b = q.enqueue("G0X2", "jog")
    q.mark_sent(a, 0)
    q.mark_sent(b, 0)
    assert q.acknowledge() is a
    assert q.count_source("jog") == 1
    assert q.clear_all() == 1
    assert q.is_empty() and q.in_flight_bytes == 0
    assert q.acknowledge() is None


def test_clear_pending_by_source():
    q = StreamingQueue()
    q.enqueue("G0X1", "job")
    q.enqueue("G0X2", "frame")
    q.enqueue("G0X3", "job")
    assert q.clear_pending("job") == 2
    assert q.pending_count == 1


def test_rejects_long_or_multiline():
    q = StreamingQueue()
    with pytest.raises(QueueError):
        q.enqueue("G1X1\nG1X2")
    with pytest.raises(QueueError):
        q.enqueue("G1" + "X1" * 60)
