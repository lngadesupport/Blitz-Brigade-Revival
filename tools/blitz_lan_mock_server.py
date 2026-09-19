#!/usr/bin/env python3
"""Experimental LAN compatibility server for Blitz Brigade 1.3.1.13 (Windows x86).

Implemented:
  DISCOVER (ID 1) -> SERVER_DETAILS (ID 2)
  CLIENT_DETAILS/JOIN (ID 3) decoding/logging
Optional experiment:
  --send-lobby sends a minimal reliable LOBBY_LIST (ID 5) after JOIN.

The program is intentionally small and dependency-free. It does not contain
Gameloft assets or game code.
"""
from __future__ import annotations

import argparse
import socket
import struct
import time
from dataclasses import dataclass

PORT = 7891
LAN_ID = 0x0AB25571
HEADER_SIZE = 13


@dataclass
class Header:
    reliable: int
    seq: int
    latest: int
    ack: int


def fixed20(text: str) -> bytes:
    raw = text.encode("utf-8", errors="replace")[:19]
    return raw + b"\x00" * (20 - len(raw))


def varbytes(blob: bytes) -> bytes:
    if len(blob) > 255:
        raise ValueError("varbytes suporta no máximo 255 bytes")
    return bytes((len(blob),)) + blob


def variable_message(msg_id: int, body: bytes) -> bytes:
    # Serializer do jogo stores TOTAL application-message length, including
    # ID + the two length bytes themselves.
    total = 3 + len(body)
    if total > 0xFFFF:
        raise ValueError("mensagem variável excede u16")
    return bytes((msg_id,)) + struct.pack(">H", total) + body


def transport_header(reliable: int = 0, seq: int = 0, latest: int = 0, ack: int = 0) -> bytes:
    return bytes((reliable & 0xFF,)) + struct.pack("<III", seq & 0xFFFFFFFF, latest & 0xFFFFFFFF, ack & 0xFFFFFFFF)


def server_details_blob(map_id: int, players: int, max_players: int, mode_id: int) -> bytes:
    # The game host builds this block as [map, current_players, max_players, mode].
    # LocalWifiJoin only reads the first three bytes for its list UI, while the
    # fourth byte is preserved for later session setup.
    return bytes((map_id & 0xFF, players0& 0xFF, max_players & 0xFF, mode_id & 0xFF))


def make_server_details(lan_id: int, name: str, details: bytes) -> bytes:
    body = struct.pack(">I", lan_id) + varbytes(details) + fixed20(name)
    # Original discovery response uses the non-reliable path. For the initial
    # peer state the 13-byte header is consequently all zeroes.
    return transport_header() + variable_message(2, body)


def make_lobby_list(
    server_seq: int,
    client_seq: int,
    revision: int,
    host_details: bytes,
    client_name: str,
    client_details: bytes,
    slot_id: int = 0,
) -> bytes:
    body = (
        struct.pack(">I", revision)
        + varbytes(host_details)
        + bytes((1,))
        + fixed20(client_name)
        + varbytes(client_details)
        + bytes((slot_id & 0xFF,))
    )
    app = variable_message(5, body)
    # ID 3 is sent by the client reliably. The game's ACK bitmap uses bit 0
    # for `latest_received`, so acknowledge that JOIN while delivering the
    # first reliable server message.
    hdr = transport_header(reliable=1, seq=server_seq, latest=client_seq, ack=1)
    return hdr + app


def parse_header(data: bytes) -> Header | None:
    if len(data) < HEADER_SIZE:
        return None
    seq, latest, ack = struct.unpack_from("<III", data, 1)
    return Header(data[0], seq, latest, ack)


def parse_variable_frame(data: bytes, expected_id: int) -> tuple[bytes | None, str | None]:
    if len(data) < HEADER_SIZE + 3:
        return None, "curto demais"
    if data[HEADER_SIZE] != expected_id:
        return None, f"ID {data[HEADER_SIZE]}, esperado {expected_id}"
    total = struct.unpack_from(">H", data, HEADER_SIZE + 1)[0]
    if total < 3:
        return None, f"tamanho variável inválido {total}"
    end = HEADER_SIZE + total
    if end > len(data):
        return None, f"truncado: declara {total}, recebeu {len(data)-HEADER_SIZE}"
    return data[HEADER_SIZE + 3 : end], None


def parse_discover(data: bytes, expected_lan_id: int) -> tuple[bool, int | None]:
    # ID 1 is fixed-length 5, so it has no u16 variable frame prefix.
    if len(data) < 18 or data[13] != 1:
        return False, None
    lan_id = struct.unpack_from(">I", data, 14)[0]
    return lan_id == expected_lan_id, lan_id


def parse_client_details(data: bytes, expected_lan_id: int) -> tuple[dict | None, str | None]:
    body, err = parse_variable_frame(data, 3)
    if body is None:
        return None, err
    if len(body) < 4 + 1 + 20:
        return None, "CLIENT_DETAILS curto demais"
    p = 0
    lan_id = struct.unpack_from(">I", body, p)[0]
    p += 4
    if lan_id != expected_lan_id:
        return None, f"LAN ID 0x{lan_id:08x} diferente do esperado"
    n = body[p]
    p += 1
    if p + n + 20 > len(body):
        return None, f"details declara {n} bytes, payload insuficiente"
    details = body[p : p + n]
    p += n
    raw_name = body[p : p + 20]
    name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    h = parse_header(data)
    return {
        "lan_id": lan_id,
        "details": details,
        "name": name,
        "header": h,
    }, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Servidor LAN experimental do Blitz Brigade 1.3.1.13")
    ap.add_argument("--bind", default="0.0.0.0", help="IP local para bind (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--ggi", type=int, default=LAN_ID, help="LAN application ID (opção mantém nome legado)")
    ap.add_argument("--name", default="REVIVAL TEST", help="nome mostrado na lista (max. 19 bytes UTF-8)")
    ap.add_argument("--map", dest="map_id", type=int, default=0, help="índice de mapa anunciado")
    ap.add_argument("--players", type=int, default=1)
    ap.add_argument("--max-players", type=int, default=12)
    ap.add_argument("--mode", dest="mode_id", type=int, default=0, help="índice/modo de jogo anunciado")
    ap.add_argument(
        "--send-lobby",
        action="store_true",
        help="EXPERIMENTAL: após CLIENT_DETAILS, envia uma LOBBY_LIST mínima (ID 5)",
    )
    args = ap.parse_args()

    if not (
        0 <= args.map_id <= 255
        and 0 <= args.players <= 255
        and 0 <= args.max_players <= 255
        and 0 <= args.mode_id <= 255
    ):
        ap.error("--map/--players/--max-players/--mode devem estar entre 0 e 255")

    advertised = server_details_blob(args.map_id, args.players, args.max_players, args.mode_id)
    reply = make_server_details(args.ggi, args.name, advertised)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind((args.bind, args.port))

    print(f"Blitz LAN mock em UDP {args.bind}:{args.port}")
    print(f"LAN_ID={args.ggi} (0x{args.ggi:08x}) name={args.name!r} details=[map={args.map_id}, players={args.players}, max={args.max_players}, mode={args.mode_id}]")
    print(f"SERVER_DETAILS correto ({len(reply)} bytes): {reply.hex(' ')}")
    print(f"send_lobby={args.send_lobby}")
    print("Aguardando DISCOVER/CLIENT_DETAILS... Ctrl+C para encerrar.")

    server_seq_by_addr: dict[tuple[str, int], int] = {}
    try:
        while True:
            data, addr = s.recvfrom(65535)
            stamp = time.strftime("%H:%M:%S")
            msg_id = data[13] if len(data) > 13 else None

            ok, packet_lan_id = parse_discover(data, args.ggi)
            if ok:
                server_seq_by_addr.setdefault(addr, 0)
                print(f"[{stamp}] DISCOVER válido de {addr[0]}:{addr[1]} LAN_ID=0x{packet_lan_id:08x} -> SERVER_DETAILS")
                s.sendto(reply, addr)
                continue

            if msg_id == 3:
                parsed, err = parse_client_details(data, args.ggi)
                if parsed is None:
                    print(f"[{stamp}] CLIENT_DETAILS inválido de {addr}: {err}")
                    continue
                h: Header = parsed["header"]
                print(
                    f"[{stamp}] JOIN/CLIENT_DETAILS de {addr[0]}:{addr[1]} "
                    f"reliable={h.reliable} seq={h.seq} latest={h.latest} ack=0x{h.ack:08x} "
                    f"name={parsed['name']!r} details[{len(parsed['details'])}]={parsed['details'].hex(' ')}"
                )
                if args.send_lobby:
                    server_seq = server_seq_by_addr.get(addr, 0)
                    # Reflect one joined client in host details for this synthetic waiting room.
                    lobby_host_details = server_details_blob(args.map_id, 1, args.max_players, args.mode_id)
                    lobby = make_lobby_list(
                        server_seq=server_seq,
                        client_seq=h.seq,
                        revision=1,
                        host_details=lobby_host_details,
                        client_name=parsed["name"],
                        client_details=parsed["details"],
                        slot_id=0,
                    )
                    s.sendto(lobby, addr)
                    server_seq_by_addr[addr] = (server_seq + 1) & 0xFFFFFFFF
                    print(f"[{stamp}] -> LOBBY_LIST experimental ({len(lobby)} bytes), seq={server_seq}, ACK JOIN seq={h.seq}")
                continue

            h = parse_header(data)
            if h and msg_id is not None:
                print(
                    f"[{stamp}] pacote de {addr[0]}:{addr[1]} len={len(data)} id={msg_id} "
                    f"reliable={h.reliable} seq={h.seq} latest={h.latest} ack=0x{h.ack:08x}"
                )
            else:
                print(f"[{stamp}] pacote ignorado de {addr[0]}:{addr[1]} len={len(data)}")
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
