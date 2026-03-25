"""Inter-process messaging protocol for JuGeo worker architecture.

All inter-process communication between the coordinator and workers uses
length-prefixed JSON frames carried over TCP (or Unix-domain) sockets.

Frame format
------------
::

    +-----------+---------------------+
    | 4 bytes   | <length> bytes      |
    | big-endian| UTF-8 JSON payload  |
    | uint32    |                     |
    +-----------+---------------------+

Classes
-------
- :class:`MessageSerializer` — encode/decode :class:`~jugeo.scaling.workers.models.Message` objects.
- :class:`MessageChannel` — send/receive over a single connected socket.
- :class:`MessageBus` — server-side accept loop plus broadcast helpers.
"""

from __future__ import annotations

import json
import logging
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from jugeo.scaling.workers.models import Message

logger = logging.getLogger(__name__)

# Header is a 4-byte big-endian unsigned integer holding the payload length.
_HEADER_FMT = ">I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # == 4

_RECV_CHUNK = 4096  # bytes read per recv() call
_DEFAULT_TIMEOUT = 30.0  # seconds


# ---------------------------------------------------------------------------
# MessageSerializer
# ---------------------------------------------------------------------------

class MessageSerializer:
    """Encode and decode :class:`~jugeo.scaling.workers.models.Message` objects.

    The wire format is a 4-byte big-endian length prefix followed by UTF-8
    encoded JSON.

    Examples
    --------
    >>> from jugeo.scaling.workers.models import Message
    >>> s = MessageSerializer()
    >>> msg = Message.create("heartbeat", "worker-1", "coordinator", {})
    >>> raw = s.serialize(msg)
    >>> recovered = s.deserialize(raw)
    >>> recovered.kind == "heartbeat"
    True
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def serialize(self, message: Message) -> bytes:
        """Serialise *message* to a length-prefixed JSON byte string."""
        body = json.dumps(
            message.to_dict(), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        header = self._encode_header(len(body))
        return header + body

    def deserialize(self, data: bytes) -> Message:
        """Deserialise a length-prefixed byte string to a :class:`Message`.

        Parameters
        ----------
        data:
            Raw bytes as produced by :meth:`serialize`.  May include the
            header prefix or may be the bare JSON payload.
        """
        if len(data) < _HEADER_SIZE:
            raise ValueError(
                f"Data too short to contain header: {len(data)} bytes"
            )
        declared_length = self._decode_header(data[:_HEADER_SIZE])
        body = data[_HEADER_SIZE: _HEADER_SIZE + declared_length]
        if len(body) < declared_length:
            raise ValueError(
                f"Truncated payload: expected {declared_length} bytes, "
                f"got {len(body)}"
            )
        return Message.from_dict(json.loads(body.decode("utf-8")))

    # ------------------------------------------------------------------
    # Header helpers
    # ------------------------------------------------------------------

    def _encode_header(self, length: int) -> bytes:
        """Pack *length* as a 4-byte big-endian unsigned integer."""
        return struct.pack(_HEADER_FMT, length)

    def _decode_header(self, header: bytes) -> int:
        """Unpack a 4-byte big-endian unsigned integer from *header*."""
        if len(header) < _HEADER_SIZE:
            raise ValueError(
                f"Header must be {_HEADER_SIZE} bytes, got {len(header)}"
            )
        (length,) = struct.unpack(_HEADER_FMT, header[:_HEADER_SIZE])
        return length


# ---------------------------------------------------------------------------
# MessageChannel
# ---------------------------------------------------------------------------

class MessageChannel:
    """Bidirectional message channel wrapping a single connected socket.

    Parameters
    ----------
    sock:
        A *connected* :class:`socket.socket` (SOCK_STREAM).

    Notes
    -----
    This class is **not** thread-safe.  If multiple threads need to share a
    channel, callers must provide external locking.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._serializer = MessageSerializer()
        self._closed = False
        self._recv_buffer = b""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, message: Message) -> None:
        """Serialise and send *message* over the socket.

        Raises
        ------
        OSError
            If the socket is closed or the send fails.
        """
        if self._closed:
            raise OSError("Channel is closed")
        raw = self._serializer.serialize(message)
        total_sent = 0
        while total_sent < len(raw):
            sent = self._sock.send(raw[total_sent:])
            if sent == 0:
                raise OSError("Socket connection broken during send")
            total_sent += sent

    def receive(self, timeout: Optional[float] = None) -> Optional[Message]:
        """Receive the next message from the channel.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait.  ``None`` means block indefinitely.
            If the timeout expires before a complete message is available,
            ``None`` is returned.

        Returns
        -------
        :class:`~jugeo.scaling.workers.models.Message` or ``None``
        """
        if self._closed:
            return None

        deadline = (time.monotonic() + timeout) if timeout is not None else None

        # Accumulate until we have a full header + body.
        while True:
            # Try to extract a complete frame from the buffer.
            msg = self._try_extract()
            if msg is not None:
                return msg

            # Calculate remaining time.
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                sock_timeout = min(remaining, 1.0)
            else:
                sock_timeout = 1.0

            # Wait for data.
            try:
                ready, _, _ = select.select([self._sock], [], [], sock_timeout)
            except (OSError, ValueError):
                return None

            if not ready:
                continue

            # Read a chunk of data.
            try:
                chunk = self._sock.recv(_RECV_CHUNK)
            except OSError:
                return None

            if not chunk:
                # Remote end closed the connection.
                return None

            self._recv_buffer += chunk

    def close(self) -> None:
        """Close the underlying socket cleanly."""
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_extract(self) -> Optional[Message]:
        """Return one :class:`Message` if the buffer contains a full frame."""
        if len(self._recv_buffer) < _HEADER_SIZE:
            return None
        length = self._serializer._decode_header(self._recv_buffer[:_HEADER_SIZE])
        total_needed = _HEADER_SIZE + length
        if len(self._recv_buffer) < total_needed:
            return None
        frame = self._recv_buffer[:total_needed]
        self._recv_buffer = self._recv_buffer[total_needed:]
        return self._serializer.deserialize(frame)


# ---------------------------------------------------------------------------
# MessageBus
# ---------------------------------------------------------------------------

class MessageBus:
    """Server-side TCP message bus.

    Creates a listening socket on *address*:*port* and manages accepted
    :class:`MessageChannel` connections.

    Parameters
    ----------
    address:
        Hostname or IP to bind to.  Use ``"localhost"`` or ``"127.0.0.1"``
        for local-only communication.
    port:
        TCP port to listen on.  Use ``0`` to let the OS pick a free port
        (retrieve the actual port via :attr:`port` after calling
        :meth:`start_server`).

    Notes
    -----
    :meth:`start_server` must be called before :meth:`accept` or
    :meth:`broadcast`.
    """

    def __init__(self, address: str, port: int) -> None:
        self._address = address
        self._port = port
        self._server_sock: Optional[socket.socket] = None
        self._channels: list[MessageChannel] = []
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        """The actual bound port (useful when *port* was ``0``)."""
        if self._server_sock is not None:
            return self._server_sock.getsockname()[1]
        return self._port

    @property
    def address(self) -> str:
        return self._address

    def start_server(self) -> None:
        """Bind and start listening on *address*:*port*."""
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._address, self._port))
        sock.listen(128)
        self._server_sock = sock
        self._running = True
        logger.debug("MessageBus listening on %s:%d", self._address, self.port)

    def connect(self) -> MessageChannel:
        """Open a client connection to this bus and return a :class:`MessageChannel`.

        The caller is responsible for closing the returned channel.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self._address, self.port))
        return MessageChannel(sock)

    def accept(self, timeout: float = 5.0) -> Optional[MessageChannel]:
        """Accept one incoming connection.

        Parameters
        ----------
        timeout:
            Seconds to wait for an incoming connection.  Returns ``None``
            if no connection arrives within the timeout.
        """
        if self._server_sock is None:
            raise RuntimeError("start_server() has not been called")
        self._server_sock.settimeout(timeout)
        try:
            conn, addr = self._server_sock.accept()
        except (socket.timeout, OSError):
            return None
        channel = MessageChannel(conn)
        with self._lock:
            self._channels.append(channel)
        logger.debug("MessageBus accepted connection from %s", addr)
        return channel

    def broadcast(self, message: Message, channels: list[MessageChannel]) -> None:
        """Send *message* to every channel in *channels*.

        Channels that fail to receive the message are silently skipped
        (they will be detected as dead by the heartbeat monitor).
        """
        for ch in channels:
            if ch.is_closed:
                continue
            try:
                ch.send(message)
            except OSError as exc:
                logger.warning("Broadcast to channel failed: %s", exc)

    def close(self) -> None:
        """Shut down the server socket and all registered channels."""
        self._running = False
        with self._lock:
            for ch in self._channels:
                try:
                    ch.close()
                except Exception:
                    pass
            self._channels.clear()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

    def remove_channel(self, channel: MessageChannel) -> None:
        """Remove a channel from the internal registry."""
        with self._lock:
            try:
                self._channels.remove(channel)
            except ValueError:
                pass

    @property
    def channel_count(self) -> int:
        with self._lock:
            return len(self._channels)
