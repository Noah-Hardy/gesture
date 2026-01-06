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
    
    def __init__(self, client, queue_size=10):
        """
        Initialize threaded OSC sender
        
        Args:
            client: OSC client instance (pythonosc.udp_client.SimpleUDPClient)
            queue_size: Maximum number of queued messages (older messages dropped if full)
        """
        self.client = client
        self.message_queue = queue.Queue(maxsize=queue_size)
        self.running = True
        self.dropped_count = 0
        self.sent_count = 0
        
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
        
        Args:
            address: OSC address string (e.g., "/pose/raw")
            message: Message data (can be any type)
        """
        try:
            # Non-blocking put - if queue is full, skip message to maintain performance
            self.message_queue.put_nowait((address, message))
        except queue.Full:
            # Drop message if queue is full to prevent blocking and memory buildup
            self.dropped_count += 1
            # Explicitly delete the message that wasn't queued
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
