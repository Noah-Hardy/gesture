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


# ============================================================================
# THREADED OSC SENDER CLASS
# ============================================================================
class ThreadedOSCSender:
    """
    Threaded OSC sender to prevent network operations from blocking frame processing
    Uses a background thread with a message queue for asynchronous sending
    """
    
    def __init__(self, client, queue_size=32):
        """
        Initialize threaded OSC sender

        Args:
            client: OSC client instance (pythonosc.udp_client.SimpleUDPClient)
            queue_size: Maximum number of queued messages. Once full, the oldest
                queued message is evicted to make room for the newest arrival -
                under sustained congestion, freshness beats completeness for
                realtime tracking data.
        """
        self.client = client
        self.message_queue = queue.Queue(maxsize=queue_size)
        self.running = True
        self.dropped_count = 0
        self.sent_count = 0
        # send_message's evict-then-put is two queue operations; the lock
        # keeps them atomic if send_message is ever called from more than
        # one thread (today it's only ever the main processing thread).
        self._send_lock = threading.Lock()
        
        # Start background thread (daemon=True means it won't prevent program exit)
        self.thread = threading.Thread(target=self._send_messages, daemon=True)
        self.thread.start()
    
    def _send_messages(self):
        """
        Background thread worker to send OSC messages
        Continuously processes messages from the queue
        """
        while self.running:
            try:
                # Get message with timeout to periodically check if still running
                address, message = self.message_queue.get(timeout=0.1)
                self.client.send_message(address, message)
                self.sent_count += 1
                self.message_queue.task_done()
                # Explicitly delete message reference to free memory
                del message
            except queue.Empty:
                # No message available, continue waiting
                continue
            except Exception as e:
                # Log error but continue processing
                print(f"OSC send error: {e}")
                self.dropped_count += 1
    
    def send_message(self, address, message):
        """
        Queue a message to be sent (non-blocking)

        If the queue is full, the oldest queued message is evicted to make
        room - under congestion, the freshest pose data is what a realtime
        receiver needs, not whatever was queued first.

        Args:
            address: OSC address string (e.g., "/pose/raw")
            message: Message data (can be any type)
        """
        with self._send_lock:
            # Bounded retry: normally one eviction makes room. Loop instead
            # of recursing so a pathological burst can't grow the call
            # stack, and cap attempts so this can't spin forever if
            # something is racing the queue from another thread.
            for _ in range(4):
                try:
                    self.message_queue.put_nowait((address, message))
                    return
                except queue.Full:
                    try:
                        stale_address, stale_message = self.message_queue.get_nowait()
                        self.message_queue.task_done()
                        self.dropped_count += 1
                        del stale_address, stale_message
                    except queue.Empty:
                        # Sender thread drained it between our put and get -
                        # just retry the put.
                        continue
            # Retries exhausted (persistent contention) - drop the newest
            # arrival rather than block the caller.
            self.dropped_count += 1
            del message
    
    def get_stats(self):
        """Get sender statistics"""
        return {
            'sent': self.sent_count,
            'dropped': self.dropped_count,
            'queued': self.message_queue.qsize()
        }
    
    def stop(self):
        """
        Stop the sender thread gracefully
        Waits up to 1 second for thread to finish
        """
        self.running = False
        self.thread.join(timeout=1.0)
