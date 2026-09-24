#!/usr/bin/env python3
"""
Threaded OSC Sender Module
Non-blocking OSC message transmission for real-time performance
"""

# ============================================================================
# IMPORTS
# ============================================================================
import threading
import queue

from pythonosc.osc_message_builder import OscMessageBuilder


# ============================================================================
# THREADED OSC SENDER CLASS
# ============================================================================
class ThreadedOSCSender:
    """
    Threaded OSC sender to prevent network operations from blocking frame processing
    Uses a background thread with a packet queue for asynchronous sending
    Each queued packet (an OscMessage or an OscBundle) is one UDP datagram
    """

    def __init__(self, client, queue_size=32):
        """
        Initialize threaded OSC sender

        Args:
            client: OSC client instance (pythonosc.udp_client.SimpleUDPClient,
                or anything with send(packet))
            queue_size: Maximum number of queued packets. Once full, the oldest
                queued packet is evicted to make room for the newest arrival -
                under sustained congestion, freshness beats completeness for
                realtime tracking data.
        """
        self.client = client
        self.message_queue = queue.Queue(maxsize=queue_size)
        self.running = True
        self._draining = False  # stop(flush=True): send what's queued, then exit
        self.dropped_count = 0
        self.sent_count = 0
        # send_packet's evict-then-put is two queue operations; the lock
        # keeps them atomic if send_packet is ever called from more than
        # one thread (today it's only ever the main processing thread).
        self._send_lock = threading.Lock()

        # Start background thread (daemon=True means it won't prevent program exit)
        self.thread = threading.Thread(target=self._send_messages, daemon=True)
        self.thread.start()

    def _send_messages(self):
        """
        Background thread worker to send OSC packets
        Continuously processes packets from the queue
        """
        while self.running:
            try:
                # Get packet with timeout to periodically check if still running
                packet = self.message_queue.get(timeout=0.1)
            except queue.Empty:
                if self._draining:
                    # Flushing stop() and nothing left - done
                    break
                continue
            try:
                self.client.send(packet)
                self.sent_count += 1
            except Exception as e:
                # Log error but continue processing
                print(f"OSC send error: {e}")
                self.dropped_count += 1
            finally:
                self.message_queue.task_done()

    def send_packet(self, packet):
        """
        Queue a built OscMessage or OscBundle to be sent (non-blocking)

        If the queue is full, the oldest queued packet is evicted to make
        room - under congestion, the freshest pose data is what a realtime
        receiver needs, not whatever was queued first.

        Args:
            packet: pythonosc OscMessage or OscBundle
        """
        with self._send_lock:
            # Bounded retry: normally one eviction makes room. Loop instead
            # of recursing so a pathological burst can't grow the call
            # stack, and cap attempts so this can't spin forever if
            # something is racing the queue from another thread.
            for _ in range(4):
                try:
                    self.message_queue.put_nowait(packet)
                    return
                except queue.Full:
                    try:
                        self.message_queue.get_nowait()
                        self.message_queue.task_done()
                        self.dropped_count += 1
                    except queue.Empty:
                        # Sender thread drained it between our put and get -
                        # just retry the put.
                        continue
            # Retries exhausted (persistent contention) - drop the newest
            # arrival rather than block the caller.
            self.dropped_count += 1

    def send_message(self, address, value):
        """
        Queue a single message to be sent (non-blocking)
        Same argument rules as SimpleUDPClient.send_message: one value is one
        arg, a list/tuple is one arg per element, None is no args

        Args:
            address: OSC address string (e.g., "/pose/raw")
            value: Message argument(s)
        """
        builder = OscMessageBuilder(address=address)
        if value is None:
            pass
        elif isinstance(value, (list, tuple)):
            for v in value:
                builder.add_arg(v)
        else:
            builder.add_arg(value)
        self.send_packet(builder.build())

    def get_stats(self):
        """Get sender statistics"""
        return {
            'sent': self.sent_count,
            'dropped': self.dropped_count,
            'queued': self.message_queue.qsize()
        }

    def stop(self, flush=False, timeout=1.0):
        """
        Stop the sender thread

        Args:
            flush: True sends everything already queued (e.g. the shutdown
                clear messages) before exiting; False abandons the queue
            timeout: Maximum seconds to wait for the thread
        """
        if flush:
            self._draining = True
            self.thread.join(timeout=timeout)
        self.running = False
        self.thread.join(timeout=timeout)
