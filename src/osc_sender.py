"""
Threaded OSC sender module for non-blocking network operations.
"""
import threading
import queue


class ThreadedOSCSender:
    """Threaded OSC sender to prevent network operations from blocking frame processing"""
    
    def __init__(self, client, queue_size=10):
        self.client = client
        self.message_queue = queue.Queue(maxsize=queue_size)  # Configurable queue size
        self.running = True
        self.thread = threading.Thread(target=self._send_messages, daemon=True)
        self.thread.start()
    
    def _send_messages(self):
        """Background thread to send OSC messages"""
        while self.running:
            try:
                # Get message with timeout to allow checking if still running
                address, message = self.message_queue.get(timeout=0.1)
                self.client.send_message(address, message)
                self.message_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"OSC send error: {e}")
    
    def send_message(self, address, message):
        """Queue a message to be sent"""
        try:
            # Non-blocking put - if queue is full, skip this message to maintain performance
            self.message_queue.put_nowait((address, message))
        except queue.Full:
            # Drop message if queue is full to prevent blocking
            pass
    
    def stop(self):
        """Stop the sender thread"""
        self.running = False
        self.thread.join(timeout=1.0)
