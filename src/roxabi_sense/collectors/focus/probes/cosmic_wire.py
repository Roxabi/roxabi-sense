"""Small read-only Wayland observer for ext-foreign-toplevel and COSMIC info.

Only the registry, display sync and the two toplevel information protocols are
bound. The observer never requests compositor control, input, or image data.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import time
from typing import Any
from uuid import uuid4

_U32 = struct.Struct("=I")
_HEADER = struct.Struct("=II")
_MAX_MESSAGE = 65532
_MAX_BUFFER = 2 * 1024 * 1024


def _word(value: int) -> bytes:
    return _U32.pack(value)


def _string(value: str) -> bytes:
    raw = value.encode() + b"\0"
    return _word(len(raw)) + raw + bytes(-len(raw) % 4)


def _read_string(data: bytes, offset: int = 0) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise ValueError("short string length")
    size = _U32.unpack_from(data, offset)[0]
    start = offset + 4
    end = start + size
    padded = (end + 3) & ~3
    if not size or padded > len(data) or data[end - 1] != 0:
        raise ValueError("invalid string")
    return data[start:end - 1].decode("utf-8", errors="replace"), padded


class CosmicWire:
    """A single connection and its staged/committed metadata."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.sock.setblocking(False)
        self.deadline = time.monotonic() + 0.25
        self.epoch = uuid4().hex
        self.buffer = bytearray()
        self.next_id = 2
        self.objects: dict[int, str] = {1: "display"}
        self.globals: dict[str, tuple[int, int]] = {}
        self.global_names: set[int] = set()
        self.registry = self._new("registry")
        self.manager = 0
        self.foreign = 0
        self.handles: dict[int, dict[str, Any]] = {}
        self.extensions: dict[int, int] = {}
        self.ready = False
        self.dirty = False
        self.ack_at = 0.0
        self.pending_sync: int | None = None
        self._send(1, 1, _word(self.registry))
        self.commit_at = 0.0

    @classmethod
    def connect(cls, env: dict[str, str]) -> CosmicWire:
        display = env.get("WAYLAND_DISPLAY", "wayland-0")
        path = (
            display if os.path.isabs(display)
            else os.path.join(env.get("XDG_RUNTIME_DIR", ""), display)
        )
        if not os.path.isabs(path):
            raise OSError("missing Wayland runtime directory")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.25)
        try:
            sock.connect(path)
            wire = cls(sock)
            wire.deadline = time.monotonic() + 1.0
            wire.roundtrip(0.5)
            info = wire.globals.get("zcosmic_toplevel_info_v1")
            foreign = wire.globals.get("ext_foreign_toplevel_list_v1")
            if not info or info[1] < 2 or not foreign:
                raise OSError("COSMIC toplevel protocols unavailable")
            wire.manager = wire._bind(info[0], "zcosmic_toplevel_info_v1", 2, "manager")
            wire.foreign = wire._bind(foreign[0], "ext_foreign_toplevel_list_v1", 1, "foreign")
            wire.global_names = {info[0], foreign[0]}
            wire.roundtrip(0.5)
            wire.roundtrip(0.5)
            return wire
        except BaseException:
            sock.close()
            raise

    def _new(self, kind: str) -> int:
        ident = self.next_id
        self.next_id += 1
        if ident >= 0xff000000:
            raise ValueError("Wayland object IDs exhausted")
        self.objects[ident] = kind
        return ident

    def _send(self, obj: int, opcode: int, data: bytes = b"") -> None:
        msg = _HEADER.pack(obj, ((len(data) + 8) << 16) | opcode) + data
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Wayland I/O budget exhausted")
        _, writable, _ = select.select([], [self.sock], [], remaining)
        if not writable:
            raise TimeoutError("Wayland write timeout")
        self.sock.sendall(msg)

    def _bind(self, name: int, interface: str, version: int, kind: str) -> int:
        obj = self._new(kind)
        self._send(self.registry, 0, _word(name) + _string(interface) + _word(version) + _word(obj))
        return obj

    def ping(self) -> None:
        if self.pending_sync is None:
            self.pending_sync = self._new("callback")
            self._send(1, 0, _word(self.pending_sync))

    def roundtrip(self, timeout: float) -> None:
        outer_deadline = self.deadline
        self.deadline = min(outer_deadline, time.monotonic() + timeout)
        try:
            self.ping()
            while self.pending_sync is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Wayland sync timeout")
                self.receive(remaining)
        finally:
            self.deadline = outer_deadline

    def receive(self, timeout: float = 0.0) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Wayland I/O budget exhausted")
        readable, _, _ = select.select([self.sock], [], [], min(timeout, remaining))
        if not readable:
            return
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("Wayland connection closed")
        self.buffer.extend(data)
        if len(self.buffer) > _MAX_BUFFER:
            raise ValueError("Wayland buffer limit")
        while len(self.buffer) >= 8:
            if time.monotonic() >= self.deadline:
                raise TimeoutError("Wayland dispatch budget exhausted")
            obj, header = _HEADER.unpack_from(self.buffer)
            size, opcode = header >> 16, header & 0xffff
            if size < 8 or size % 4 or size > _MAX_MESSAGE:
                raise ValueError("invalid Wayland message size")
            if len(self.buffer) < size:
                break
            payload = bytes(self.buffer[8:size])
            del self.buffer[:size]
            self.dispatch(obj, opcode, payload)

    def dispatch(self, obj: int, opcode: int, data: bytes) -> None:
        kind = self.objects.get(obj)
        if kind is None:
            raise ValueError("unknown Wayland object")
        if kind == "display":
            if opcode == 0:
                raise ConnectionError("Wayland protocol error")
            if opcode == 1:
                return
            raise ValueError("unknown display event")
        if kind == "callback":
            if opcode != 0 or len(data) != 4:
                raise ValueError("invalid sync callback")
            if obj == self.pending_sync:
                self.pending_sync = None
                self.ack_at = time.monotonic()
            self.objects.pop(obj, None)
            return
        if kind == "registry":
            if opcode == 0:
                if len(data) < 12:
                    raise ValueError("short global")
                name = _U32.unpack_from(data)[0]
                interface, offset = _read_string(data, 4)
                if offset + 4 != len(data):
                    raise ValueError("invalid global")
                self.globals[interface] = (name, _U32.unpack_from(data, offset)[0])
            elif opcode == 1:
                if len(data) != 4:
                    raise ValueError("invalid global removal")
                if _U32.unpack(data)[0] in self.global_names:
                    raise ConnectionError("toplevel global removed")
            else:
                raise ValueError("unknown registry event")
            return
        if kind == "foreign":
            if opcode == 1:
                raise ConnectionError("toplevel list finished")
            if opcode != 0 or len(data) != 4:
                raise ValueError("invalid toplevel event")
            handle = _U32.unpack(data)[0]
            if handle in self.objects or len(self.handles) >= 4096:
                raise ValueError("invalid toplevel ID")
            self.objects[handle] = "handle"
            extension = self._new("extension")
            self.extensions[extension] = handle
            self.handles[handle] = {
                "extension": extension, "pending": {}, "current": {}, "state": None
            }
            self.dirty = True
            self._send(self.manager, 1, _word(extension) + _word(handle))
            return
        if kind == "manager":
            if opcode == 1:
                raise ConnectionError("COSMIC manager finished")
            if opcode != 2 or data:
                raise ValueError("invalid manager event")
            if all(
                h["state"] is not None and h.get("metadata_ready")
                for h in self.handles.values()
            ):
                for handle in self.handles.values():
                    handle["committed"] = dict(handle["current"], active=handle["state"])
                self.ready = True
                self.commit_at = time.monotonic()
                self.dirty = False
            return
        if kind == "extension":
            handle = self.handles[self.extensions[obj]]
            if opcode == 8:
                if len(data) < 4:
                    raise ValueError("short state array")
                size = _U32.unpack_from(data)[0]
                if size % 4 or 4 + size != len(data):
                    raise ValueError("invalid state array")
                states = struct.unpack_from(f"={size // 4}I", data, 4)
                handle["state"] = 2 in states
                self.dirty = True
            elif opcode not in (4, 5, 6, 7, 9):
                raise ValueError("unknown COSMIC handle event")
            return
        if kind == "handle":
            handle = self.handles[obj]
            if opcode == 0:
                extension = handle["extension"]
                self._send(extension, 0)
                self._send(obj, 0)
                self.extensions.pop(extension, None)
                self.objects.pop(extension, None)
                self.objects.pop(obj, None)
                self.handles.pop(obj, None)
                self.dirty = True
                self.ready = False
            elif opcode == 1:
                if data:
                    raise ValueError("invalid toplevel done")
                handle["current"].update(handle["pending"])
                handle["pending"].clear()
                handle["metadata_ready"] = True
                self.dirty = True
            elif opcode in (2, 3, 4):
                text, end = _read_string(data)
                if end != len(data):
                    raise ValueError("invalid toplevel property")
                handle["pending"][{2: "title", 3: "app", 4: "identifier"}[opcode]] = text
                self.dirty = True
            else:
                raise ValueError("unknown toplevel event")
            return
        raise ValueError("unexpected Wayland object")

    def close(self) -> None:
        self.sock.close()
