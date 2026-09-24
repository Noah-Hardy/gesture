"""ThreadedOSCSender against a fake client: packets (messages or bundles)
go out via client.send(), drop-oldest eviction on a full queue, stats, and
clean stop()/join - with and without flushing what's still queued."""
import threading
import time

from pythonosc.osc_bundle_builder import IMMEDIATELY, OscBundleBuilder
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from src.osc_sender import ThreadedOSCSender


class FakeClient:
    def __init__(self, block_until=None):
        self.sent = []
        self.lock = threading.Lock()
        self._block_until = block_until  # threading.Event to hold sends open

    def send(self, packet):
        if self._block_until is not None:
            self._block_until.wait(timeout=2.0)
        with self.lock:
            self.sent.append(packet)

    def addresses(self):
        with self.lock:
            return [p.address for p in self.sent]


def _msg(address, *args):
    builder = OscMessageBuilder(address=address)
    for a in args:
        builder.add_arg(a)
    return builder.build()


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_send_message_is_delivered_as_built_osc_message():
    client = FakeClient()
    sender = ThreadedOSCSender(client, queue_size=10)
    try:
        sender.send_message('/pose/raw', '{"x":1}')
        assert _wait_until(lambda: len(client.sent) == 1)
        packet = client.sent[0]
        assert isinstance(packet, OscMessage)
        assert packet.address == '/pose/raw'
        assert packet.params == ['{"x":1}']
        assert sender.get_stats()['sent'] == 1
    finally:
        sender.stop()


def test_send_message_list_value_is_one_arg_per_element():
    client = FakeClient()
    sender = ThreadedOSCSender(client, queue_size=10)
    try:
        sender.send_message('/mp/heartbeat', [30.0, 1, 2, 3])
        assert _wait_until(lambda: len(client.sent) == 1)
        assert client.sent[0].params == [30.0, 1, 2, 3]
    finally:
        sender.stop()


def test_send_packet_accepts_a_bundle():
    client = FakeClient()
    sender = ThreadedOSCSender(client, queue_size=10)
    try:
        builder = OscBundleBuilder(IMMEDIATELY)
        builder.add_content(_msg('/a', 1))
        builder.add_content(_msg('/b', 2))
        bundle = builder.build()
        sender.send_packet(bundle)
        assert _wait_until(lambda: len(client.sent) == 1)
        assert client.sent[0].dgram == bundle.dgram
    finally:
        sender.stop()


def test_full_queue_evicts_oldest_and_delivers_newest():
    hold = threading.Event()  # never set: worker blocks forever on the first packet
    client = FakeClient(block_until=hold)
    sender = ThreadedOSCSender(client, queue_size=1)
    try:
        # First packet is picked up by the worker and blocks in send().
        sender.send_packet(_msg('/a', 1))
        assert _wait_until(lambda: sender.message_queue.empty())
        # Queue now has room for exactly one more; fill it, then overflow it.
        # Drop-oldest semantics: '/b' (the stale queued entry) is evicted to
        # make room for '/c' (the newest arrival), not the other way around.
        sender.send_packet(_msg('/b', 2))
        sender.send_packet(_msg('/c', 3))
        assert sender.get_stats()['dropped'] == 1
        hold.set()
        assert _wait_until(lambda: len(client.sent) == 2)
        assert client.addresses() == ['/a', '/c']
    finally:
        hold.set()
        sender.stop()


def test_get_stats_reports_queued_count():
    hold = threading.Event()
    client = FakeClient(block_until=hold)
    sender = ThreadedOSCSender(client, queue_size=5)
    try:
        sender.send_packet(_msg('/a', 1))  # picked up immediately, blocks worker
        assert _wait_until(lambda: sender.message_queue.empty())
        sender.send_packet(_msg('/b', 2))
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


def test_stop_flush_sends_everything_still_queued():
    gate = threading.Event()
    client = FakeClient(block_until=gate)
    sender = ThreadedOSCSender(client, queue_size=32)
    sender.send_packet(_msg('/first', 0))
    assert _wait_until(lambda: sender.message_queue.empty())  # worker holding /first
    for i in range(5):
        sender.send_packet(_msg(f'/clear/{i}', i))
    gate.set()
    sender.stop(flush=True)
    assert client.addresses() == ['/first'] + [f'/clear/{i}' for i in range(5)]
    assert not sender.thread.is_alive()


def test_stop_without_flush_abandons_the_queue():
    gate = threading.Event()
    client = FakeClient(block_until=gate)
    sender = ThreadedOSCSender(client, queue_size=32)
    sender.send_packet(_msg('/first', 0))
    assert _wait_until(lambda: sender.message_queue.empty())
    for i in range(5):
        sender.send_packet(_msg(f'/late/{i}', i))
    sender.running = False  # what stop() does first, before the worker is released
    gate.set()
    sender.stop()
    assert client.addresses() == ['/first']
