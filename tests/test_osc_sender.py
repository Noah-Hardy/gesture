"""ThreadedOSCSender against a fake client: drop-oldest eviction on a full
queue, stats, and clean stop()/join."""
import threading
import time

from src.osc_sender import ThreadedOSCSender


class FakeClient:
    def __init__(self, block_until=None):
        self.sent = []
        self.lock = threading.Lock()
        self._block_until = block_until  # threading.Event to hold sends open

    def send_message(self, address, message):
        if self._block_until is not None:
            self._block_until.wait(timeout=2.0)
        with self.lock:
            self.sent.append((address, message))


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_send_message_is_delivered():
    client = FakeClient()
    sender = ThreadedOSCSender(client, queue_size=10)
    try:
        sender.send_message('/pose/raw', {'x': 1})
        assert _wait_until(lambda: len(client.sent) == 1)
        assert client.sent[0] == ('/pose/raw', {'x': 1})
        assert sender.get_stats()['sent'] == 1
    finally:
        sender.stop()


def test_full_queue_evicts_oldest_and_delivers_newest():
    hold = threading.Event()  # never set: worker blocks forever on the first message
    client = FakeClient(block_until=hold)
    sender = ThreadedOSCSender(client, queue_size=1)
    try:
        # First message is picked up by the worker and blocks in send_message.
        sender.send_message('/a', 1)
        assert _wait_until(lambda: sender.message_queue.empty())
        # Queue now has room for exactly one more; fill it, then overflow it.
        # Drop-oldest semantics: '/b' (the stale queued entry) is evicted to
        # make room for '/c' (the newest arrival), not the other way around.
        sender.send_message('/b', 2)
        sender.send_message('/c', 3)
        assert sender.get_stats()['dropped'] == 1
        hold.set()
        assert _wait_until(lambda: len(client.sent) == 2)
        assert client.sent == [('/a', 1), ('/c', 3)]
    finally:
        hold.set()
        sender.stop()


def test_get_stats_reports_queued_count():
    hold = threading.Event()
    client = FakeClient(block_until=hold)
    sender = ThreadedOSCSender(client, queue_size=5)
    try:
        sender.send_message('/a', 1)  # picked up immediately, blocks worker
        assert _wait_until(lambda: sender.message_queue.empty())
        sender.send_message('/b', 2)
        assert _wait_until(lambda: sender.get_stats()['queued'] == 1)
    finally:
        hold.set()
        sender.stop()


def test_stop_joins_the_worker_thread():
    client = FakeClient()
    sender = ThreadedOSCSender(client)
    sender.stop()
    assert sender.running is False
    assert not sender.thread.is_alive()
