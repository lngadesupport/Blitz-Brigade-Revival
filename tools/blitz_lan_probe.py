#!/usr/bin/env python3
"""Blitz Brigade 1.3.1.13 LAN protocol probe.

Static reverse engineering of InfamousHeroesEntry.exe indicates:
- UDP/IPv4 LAN transport
- destination port 7891
- 13-byte transport header
- application message starts at offset 13
- variable-length application messages carry a u16 big-endian TOTAL message
  length immediately after the message ID
- LocalHost/LocalWifiJoin use LAN application ID 0x0AB25571 (179459441)

This tool does not modify the game. It can decode UDP datagrams or send the
original LAN discovery datagram and print replies.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from dataclasses import dataclass

DEFAULT_PORT = 7891
DEFAULT_GGI = 0x0AB25571  # LAN application ID used by LocalHost/LocalWifiJoin
HEADER_SIZE = 13
VARIABLE_SENTINEL = 0x00FFFFFF

# Table reconstructed from the executable (message IDs 0..140). A value of
# 0x00FFFFFF means the message is variable and carries u16be total length.
MESSAGE_LENGTHS = [
    0, 5, VARIABLE_SENTINEL, VARIABLE_SENTINEL, 1, VARIABLE_SENTINEL, 2, 6,
    9, 2, 1, 1, 4, 9, VARIABLE_SENTINEL, 1, 9, 1, 13, 14, 1,
    VARIABLE_SENTINEL, 30, 10, 6, 7, 5, 5, 7, 7, 29, 30, 30, 3, 42, 10,
    5, VARIABLE_SENTINEL, 3, 4, 5, 2, 2, 2, 2, 3, 11, 2, 6, 2, 7, 13, 29,
    16, 11, 1, 3, 7, 2, 4, 4, 1, 2, 18, 3, 1026, 9, 257, 3, 3, 6, 3,
    4, 7, 3, 4, 3, 5, 11, 4, 3, 3, 4, 13, 22, 14, 19, 15,
    VARIABLE_SENTINEL, 4, 5, 3, 3, 3, 3, 8, 3, 4, VARIABLE_SENTINEL,
    VARIABLE_SENTINEL, 2, 2, 2, 2, 8, 3, 1, 3, 5, 5, 2, 5, 2, 2, 5, 1, 2,
    3, 3, 11, 217, 2, 1, 5, 1, 1, 1, 2, VARIABLE_SENTINEL, 6, 6, 4, 3, 3,
    3, 3, VARIABLE_SENTINEL, 2, 4, 5, 2,
]

MESSAGE_NAMES = {
    1: "DISCOVER",
    2: "SERVER_DETAILS",
    3: "CLIENT_DETAILS",
    4: "LOBBY_CONTROL_4",
    5: "LOBBY_LIST",
    6: "REQ_CLOCK",
    7: "SEND_CLOCK",
    8: "TIME_SYNC",
    11: "BYE",
    13: "PING",
    15: "CONTROL_15",
    16: "START_LOADING",
    17: "FINISHED_LOADING",
    18: "START_GAME",
}


@dataclass
class TransportHeader:
    reliable: int
    sequence: int
    latest_received: int
    ack_bitmap: int


@dataclass
class AppFrame:
    msg_type: int
    start: int
    body_start: int
    end: int
    declared_length: int
    variable: bool


def parse_header(data: bytes) -> TransportHeader | None:
    if len(data) < HEADER_SIZE:
        return None
    # Native x86 stores these transport dwords little-endian.
    reliable = data[0]
    sequence, latest, ack_bitmap = struct.unpack_from("<III", data, 1)
    return TransportHeader(reliable, sequence, latest, ack_bitmap)


def parse_app_frame(data: bytes, offset: int = HEADER_SIZE) -> tuple[AppFrame | None, str | None]:
    if offset >= len(data):
        return None, "sem payload de aplicação"
    msg_type = data[offset]
    if msg_type >= len(MESSAGE_LENGTHS):
        return None, f"ID de mensagem fora da tabela: {msg_type}"

    table_len = MESSAGE_LENGTHS[msg_type]
    if table_len == VARIABLE_SENTINEL:
        if offset + 3 > len(data):
            return None, "mensagem variável truncada antes do u16 de tamanho"
        declared = struct.unpack_from(">H", data, offset + 1)[0]
        if declared < 3:
            return None, f"tamanho variável inválido: {declared}"
        end = offset + declared
        if end > len(data):
            return None, f"mensagem variável truncada: declara {declared} bytes, há {len(data)-offset}"
        return AppFrame(msg_type, offset, offset + 3, end, declared, True), None

    if table_len <= 0:
        return None, f"tamanho fixo inválido/desconhecido para ID {msg_type}: {table_len}"
    end = offset + table_len
    if end > len(data):
        return None, f"mensagem fixa truncada: ID {msg_type} requer {table_len} bytes"
    return AppFrame(msg_type, offset, offset + 1, end, table_len, False), None


def clean_text(raw: bytes) -> str:
    raw = raw.split(b"\x00", 1)[0]
    return raw.decode("utf-8", errors="replace")


def parse_varbytes(data: bytes, offset: int, limit: int) -> tuple[bytes, int, str | None]:
    if offset >= limit:
        return b"", offset, "varbytes sem byte de comprimento"
    n = data[offset]
    offset += 1
    end = offset + n
    if end > limit:
        return data[offset:limit], limit, f"varbytes truncado: declara {n} bytes"
    return data[offset:end], end, None


def decode_packet(data: bytes) -> dict:
    out: dict = {"length": len(data), "raw_hex": data.hex(" ")}
    h = parse_header(data)
    if h is None:
        out["error"] = "datagrama menor que o cabeçalho de 13 bytes"
        return out
    out["header"] = h

    frame, err = parse_app_frame(data)
    if frame is None:
        out["error"] = err
        return out

    out["type"] = frame.msg_type
    out["type_name"] = MESSAGE_NAMES.get(frame.msg_type, "UNKNOWN")
    out["frame_length"] = frame.declared_length
    out["variable"] = frame.variable
    p = frame.body_start
    limit = frame.end

    if frame.msg_type in (1, 2, 3):
        if p + 4 > limit:
            out["error"] = "faltam 4 bytes para LAN application ID"
            return out
        out["ggi"] = struct.unpack_from(">I", data, p)[0]
        p += 4

    if frame.msg_type in (2, 3):
        details, p, verr = parse_varbytes(data, p, limit)
        out["details_len"] = len(details)
        out["details_hex"] = details.hex(" ")
        if verr:
            out["error"] = verr
        if frame.msg_type == 2 and len(details) >= 3:
            out["server_details"] = {
                "map_id": details[0],
                "players": details[1],
                "max_players": details[2],
            }
            if len(details) >= 4:
                out["server_details"]["mode_id"] = details[3]
        if p + 20 <= limit:
            out["device_name"] = clean_text(data[p : p + 20])
            p += 20
        elif p < limit:
            out["device_name"] = clean_text(data[p:limit])
            out["error"] = "campo de nome fixo de 20 bytes truncado"
            p = limit

    elif frame.msg_type == 5:
        # Minimal decoder for the base lobby-list envelope.
        if p + 4 <= limit:
            out["lobby_revision"] = struct.unpack_from(">I", data, p)[0]
            p += 4
            server_details, p, verr = parse_varbytes(data, p, limit)
            out["lobby_server_details_hex"] = server_details.hex(" ")
            if verr:
                out["error"] = verr
            if p < limit:
                count = data[p]
                p += 1
                out["lobby_count"] = count
                entries = []
                for _ in range(count):
                    if p + 20 > limit:
                        out["error"] = "entrada de lobby truncada no nome"
                        break
                    name = clean_text(data[p : p + 20])
                    p += 20
                    details, p, verr = parse_varbytes(data, p, limit)
                    if verr or p >= limit:
                        out["error"] = verr or "entrada de lobby sem slot ID"
                        break
                    slot_id = data[p]
                    p += 1
                    entries.append({"name": name, "details_hex": details.hex(" "), "slot_id": slot_id})
                out["lobby_entries"] = entries

    elif frame.msg_type == 6:
        if p < limit:
            out["clock_peer_id"] = data[p]
            p += 1

    elif frame.msg_type == 7:
        if p + 5 <= limit:
            out["clock_peer_id"] = data[p]
            p += 1
            out["clock_timestamp"] = struct.unpack_from(">I", data, p)[0]
            p += 4

    elif frame.msg_type == 16:
        if p + 8 <= limit:
            out["start_loading"] = {
                "player_id": data[p],
                "map_id": data[p + 1],
                "time_limit_minutes": struct.unpack_from(">I", data, p + 2)[0],
                "mode_id": data[p + 6],
                "max_players": data[p + 7],
            }
            p += 8

    elif frame.msg_type in (8, 13, 18):
        vals = []
        while p + 4 <= limit:
            vals.append(struct.unpack_from(">I", data, p)[0])
            p += 4
        out["u32be_values"] = vals

    if p < limit:
        out["remaining_hex"] = data[p:limit].hex(" ")
    if limit < len(data):
        out["next_or_trailing_hex"] = data[limit:].hex(" ")
    return out


def print_packet(data: bytes, addr: tuple[str, int] | None = None) -> None:
    d = decode_packet(data)
    src = f"{addr[0]}:{addr[1]}" if addr else "?"
    print(f"\n[{time.strftime('%H:%M:%S')}] {src}  len={d['length']}")

    h = d.get("header")
    if h:
        print(
            "  header: "
            f"reliable={h.reliable} seq={h.sequence} latest={h.latest_received} "
            f"ack=0x{h.ack_bitmap:08x}"
        )
    if "type" in d:
        extra = f" variable total={d['frame_length']}" if d.get("variable") else f" fixed={d['frame_length']}"
        print(f"  type: {d['type']} ({d['type_name']}){extra}")
    if "ggi" in d:
        marker = " OK" if d["ggi"] == DEFAULT_GGI else ""
        print(f"  LAN app ID: {d['ggi']} (0x{d['ggi']:08x}){marker}")
    if "details_len" in d:
        print(f"  details[{d['details_len']}]: {d['details_hex']}")
    if "server_details" in d:
        sd = d["server_details"]
        mode = f" mode_id={sd['mode_id']}" if 'mode_id' in sd else ""
        print(f"  server: map_id={sd['map_id']} players={sd['players']}/{sd['max_players']}{mode}")
    if d.get("device_name"):
        print(f"  device: {d['device_name']!r}")
    if "lobby_revision" in d:
        print(f"  lobby revision={d['lobby_revision']} count={d.get('lobby_count', '?')}")
        print(f"  lobby server details: {d.get('lobby_server_details_hex', '')}")
        for e in d.get("lobby_entries", []):
            print(f"    slot={e['slot_id']} name={e['name']!r} details={e['details_hex']}")
    if "clock_peer_id" in d:
        if "clock_timestamp" in d:
            print(f"  clock: peer_id={d['clock_peer_id']} timestamp={d['clock_timestamp']}")
        else:
            print(f"  clock request: peer_id={d['clock_peer_id']}")
    if "start_loading" in d:
        sl = d["start_loading"]
        print(
            "  start_loading: "
            f"player_id={sl['player_id']} map_id={sl['map_id']} "
            f"time={sl['time_limit_minutes']}min mode_id={sl['mode_id']} max={sl['max_players']}"
        )
    if "u32be_values" in d:
        print(f"  u32be: {d['u32be_values']}")
    if d.get("remaining_hex"):
        print(f"  remaining: {d['remaining_hex']}")
    if d.get("next_or_trailing_hex"):
        print(f"  trailing/next: {d['next_or_trailing_hex']}")
    if d.get("error"):
        print(f"  erro: {d['error']}")
    print(f"  raw: {d['raw_hex']}")


def make_discovery(ggi: int) -> bytes:
    # ID 1 is fixed-length 5: no u16 frame-length field.
    return (b"\x00" * HEADER_SIZE) + bytes([1]) + struct.pack(">I", ggi)


def discover(target: str, port: int, ggi: int, timeout: float) -> int:
    packet = make_discovery(ggi)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 0))
    s.settimeout(0.25)

    local = s.getsockname()
    print(f"Socket local: {local[0]}:{local[1]}")
    print(f"Enviando DISCOVER LAN_ID={ggi} (0x{ggi:08x}) para {target}:{port}")
    print(f"Pacote: {packet.hex(' ')}")
    s.sendto(packet, (target, port))

    end = time.monotonic() + timeout
    count = 0
    while time.monotonic() < end:
        try:
            data, addr = s.recvfrom(65535)
        except socket.timeout:
            continue
        print_packet(data, addr)
        count += 1

    print(f"\nRespostas recebidas: {count}")
    return 0


def listen(bind: str, port: int) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        s.bind((bind, port))
    except OSError as e:
        print(f"Falha ao bindar {bind}:{port}: {e}", file=sys.stderr)
        return 2

    print(f"Escutando UDP em {bind}:{port}. Ctrl+C para encerrar.")
    try:
        while True:
            data, addr = s.recvfrom(65535)
            print_packet(data, addr)
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe/sniffer LAN do Blitz Brigade 1.3.1.13")
    sub = ap.add_subparsers(dest="mode", required=True)

    d = sub.add_parser("discover", help="envia o broadcast de descoberta original")
    d.add_argument("--target", default="255.255.255.255", help="broadcast/IP de destino")
    d.add_argument("--port", type=int, default=DEFAULT_PORT)
    d.add_argument("--ggi", type=int, default=DEFAULT_GGI, help="LAN application ID (nome legado da opção)")
    d.add_argument("--timeout", type=float, default=4.0)

    l = sub.add_parser("listen", help="escuta e decodifica datagramas na porta LAN")
    l.add_argument("--bind", default="0.0.0.0")
    l.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = ap.parse_args()
    if args.mode == "discover":
        return discover(args.target, args.port, args.ggi, args.timeout)
    return listen(args.bind, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
