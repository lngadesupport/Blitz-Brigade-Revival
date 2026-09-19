#!/usr/bin/env python3
"""Experimental LAN compatibility server for Blitz Brigade 1.3.1.13 (Windows x86).

Implemented protocol path:
  DISCOVER (1) -> SERVER_DETAILS (2)
  CLIENT_DETAILS/JOIN (3) -> LOBBY_LIST (5)
  optional --auto-session:
    START_LOADING (16)
    client FINISHED_LOADING (17)
    REQ_CLOCK (6) -> client SEND_CLOCK (7)
    TIME_SYNC (8)
    START_GAME (18)

The implementation is compatibility-oriented and contains no original game
assets or executable code.
"""
from __future__ import annotations

import argparse
import socket
import struct
import time
from dataclasses import dataclass, field

PORT = 7891
LAN_ID = 0x0AB25571
HEADER_SIZE = 13
U32_MASK = 0xFFFFFFFF


@dataclass
class Header:
    reliable: int
    seq: int
    latest: int
    ack: int


@dataclass
class PeerState:
    addr: tuple[str, int]
    server_seq: int = 0
    latest_client_reliable: int | None = None
    client_name: str = ""
    client_details: bytes = b""
    phase: str = "discovered"
    joined_at: float = 0.0
    start_loading_due: float | None = None
    clock_probe_sent_at: float | None = None
    clock_probe_server_ms: int | None = None
    start_game_due: float | None = None
    clock_offset_u32: int | None = None
    rtt_ms: int | None = None


def fixed20(text: str) -> bytes:
    raw = text.encode("utf-8", errors="replace")[:19]
    return raw + b"\x00" * (20 - len(raw))


def varbytes(blob: bytes) -> bytes:
    if len(blob) > 255:
        raise ValueError("varbytes suporta no máximo 255 bytes")
    return bytes((len(blob),)) + blob


def variable_message(msg_id: int, body: bytes) -> bytes:
    # The game stores TOTAL application-message length, including ID + u16.
    total = 3 + len(body)
    if total > 0xFFFF:
        raise ValueError("mensagem variável excede u16")
    return bytes((msg_id,)) + struct.pack(">H", total) + body


def fixed_message(msg_id: int, body: bytes = b"") -> bytes:
    return bytes((msg_id & 0xFF,)) + body


def transport_header(reliable: int = 0, seq: int = 0, latest: int = 0, ack: int = 0) -> bytes:
    return bytes((reliable & 0xFF,)) + struct.pack(
        "<III", seq & U32_MASK, latest & U32_MASK, ack & U32_MASK
    )


def ack_fields(peer: PeerState) -> tuple[int, int]:
    if peer.latest_client_reliable is None:
        return 0, 0
    return peer.latest_client_reliable, 1


def wrap_for_peer(peer: PeerState, app: bytes, reliable: bool) -> tuple[bytes, int | None]:
    latest, ack = ack_fields(peer)
    if reliable:
        seq = peer.server_seq
        peer.server_seq = (peer.server_seq + 1) & U32_MASK
        return transport_header(1, seq, latest, ack) + app, seq
    return transport_header(0, 0, latest, ack) + app, None


def server_details_blob(map_id: int, players: int, max_players: int, mode_id: int) -> bytes:
    # Original host layout: [map, current_players, max_players, mode].
    return bytes((map_id & 0xFF, players & 0xFF, max_players & 0xFF, mode_id & 0xFF))


def make_server_details(lan_id: int, name: str, details: bytes) -> bytes:
    body = struct.pack(">I", lan_id) + varbytes(details) + fixed20(name)
    # Discovery happens before a peer exists and uses a zeroed transport header.
    return transport_header() + variable_message(2, body)


def make_lobby_app(
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
    return variable_message(5, body)


def make_start_loading_app(
    player_id: int,
    map_id: int,
    time_limit_minutes: int,
    mode_id: int,
    max_players: int,
) -> bytes:
    # Fixed length 9: ID + u8 player + u8 map + u32be time limit + u8 mode + u8 max.
    return fixed_message(
        16,
        bytes((player_id & 0xFF, map_id & 0xFF))
        + struct.pack(">I", time_limit_minutes & U32_MASK)
        + bytes((mode_id & 0xFF, max_players & 0xFF)),
    )


def make_req_clock_app(sample_id: int) -> bytes:
    # Fixed length 2: ID + sample index. Original server sends this unreliably.
    return fixed_message(6, bytes((sample_id & 0xFF,)))


def make_time_sync_app(ping_ms: int, clock_offset_u32: int) -> bytes:
    # Fixed length 9. Client applies the second value as a signed/modulo-2^32
    # clock offset by storing its negation as the synchronized clock base.
    return fixed_message(8, struct.pack(">II", ping_ms & U32_MASK, clock_offset_u32 & U32_MASK))


def make_start_game_app(sync_remaining_ms: int, time_sent_ms: int, rng_state: int) -> bytes:
    # Fixed length 13. Static analysis identifies:
    #   u32 #1 = remaining part of the server's ~2000 ms synchronized start window
    #   u32 #2 = server synchronized time when sent
    #   u32 #3 = global RNG state (LCG seed/state copied to clients)
    return fixed_message(
        18,
        struct.pack(
            ">III",
            sync_remaining_ms & U32_MASK,
            time_sent_ms & U32_MASK,
            rng_state & U32_MASK,
        ),
    )


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
    return {"lan_id": lan_id, "details": details, "name": name, "header": parse_header(data)}, None


def parse_send_clock(data: bytes) -> tuple[int, int] | None:
    # ID 7 fixed length 6: ID + sample_id:u8 + client_tick:u32be.
    if len(data) < HEADER_SIZE + 6 or data[HEADER_SIZE] != 7:
        return None
    return data[HEADER_SIZE + 1], struct.unpack_from(">I", data, HEADER_SIZE + 2)[0]


def server_elapsed_ms(epoch: float) -> int:
    return int((time.monotonic() - epoch) * 1000.0) & U32_MASK


def signed32(value: int) -> int:
    value &= U32_MASK
    return value - 0x100000000 if value & 0x80000000 else value


def parse_int_auto(text: str) -> int:
    return int(text, 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="Servidor LAN experimental do Blitz Brigade 1.3.1.13")
    ap.add_argument("--bind", default="0.0.0.0", help="IP local para bind (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--ggi", type=parse_int_auto, default=LAN_ID, help="LAN application ID (default 0x0AB25571)")
    ap.add_argument("--name", default="REVIVAL TEST", help="nome mostrado na lista (max. 19 bytes UTF-8)")
    ap.add_argument("--map", dest="map_id", type=int, default=0, help="índice de mapa anunciado")
    ap.add_argument("--players", type=int, default=1)
    ap.add_argument("--max-players", type=int, default=12)
    ap.add_argument("--mode", dest="mode_id", type=int, default=0, help="índice/modo de jogo anunciado")
    ap.add_argument("--player-id", type=int, default=0, help="player/network index enviado em START_LOADING")
    ap.add_argument("--time-limit", type=int, default=10, help="limite da partida em minutos")
    ap.add_argument("--rng-seed", type=parse_int_auto, default=0x12345678, help="estado RNG enviado em START_GAME")
    ap.add_argument(
        "--send-lobby",
        action="store_true",
        help="após CLIENT_DETAILS, envia uma LOBBY_LIST mínima (ID 5)",
    )
    ap.add_argument(
        "--auto-session",
        action="store_true",
        help="EXPERIMENTAL: tenta avançar automaticamente lobby -> loading -> clock sync -> START_GAME",
    )
    ap.add_argument("--start-delay", type=float, default=1.0, help="segundos entre JOIN e START_LOADING em --auto-session")
    ap.add_argument("--start-game-delay", type=float, default=0.20, help="segundos entre TIME_SYNC e START_GAME")
    args = ap.parse_args()

    if not all(
        0 <= v <= 255
        for v in (args.map_id, args.players, args.max_players, args.mode_id, args.player_id)
    ):
        ap.error("--map/--players/--max-players/--mode/--player-id devem estar entre 0 e 255")
    if not 0 <= args.time_limit <= U32_MASK:
        ap.error("--time-limit fora de u32")

    if args.auto_session:
        args.send_lobby = True

    advertised = server_details_blob(args.map_id, args.players, args.max_players, args.mode_id)
    discovery_reply = make_server_details(args.ggi, args.name, advertised)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.05)

    epoch = time.monotonic()
    peers: dict[tuple[str, int], PeerState] = {}

    print(f"Blitz LAN mock em UDP {args.bind}:{args.port}")
    print(
        f"LAN_ID={args.ggi} (0x{args.ggi:08x}) name={args.name!r} "
        f"details=[map={args.map_id}, players={args.players}, max={args.max_players}, mode={args.mode_id}]"
    )
    print(f"SERVER_DETAILS ({len(discovery_reply)} bytes): {discovery_reply.hex(' ')}")
    print(
        f"send_lobby={args.send_lobby} auto_session={args.auto_session} "
        f"time_limit={args.time_limit}m rng=0x{args.rng_seed & U32_MASK:08x}"
    )
    print("Aguardando cliente... Ctrl+C para encerrar.")

    def get_peer(addr: tuple[str, int]) -> PeerState:
        return peers.setdefault(addr, PeerState(addr=addr))

    def send_reliable(peer: PeerState, app: bytes, label: str) -> None:
        packet, seq = wrap_for_peer(peer, app, reliable=True)
        sock.sendto(packet, peer.addr)
        latest, _ = ack_fields(peer)
        print(
            f"[{time.strftime('%H:%M:%S')}] -> {label} para {peer.addr[0]}:{peer.addr[1]} "
            f"seq={seq} ack_client={latest if peer.latest_client_reliable is not None else '-'} len={len(packet)}"
        )

    def send_unreliable(peer: PeerState, app: bytes, label: str) -> None:
        packet, _ = wrap_for_peer(peer, app, reliable=False)
        sock.sendto(packet, peer.addr)
        print(f"[{time.strftime('%H:%M:%S')}] -> {label} para {peer.addr[0]}:{peer.addr[1]} len={len(packet)}")

    def process_timers(now: float) -> None:
        for peer in list(peers.values()):
            if peer.start_loading_due is not None and now >= peer.start_loading_due:
                peer.start_loading_due = None
                send_reliable(
                    peer,
                    make_start_loading_app(
                        args.player_id,
                        args.map_id,
                        args.time_limit,
                        args.mode_id,
                        args.max_players,
                    ),
                    "START_LOADING(16)",
                )
                peer.phase = "loading"

            if peer.start_game_due is not None and now >= peer.start_game_due:
                peer.start_game_due = None
                send_reliable(
                    peer,
                    make_start_game_app(
                        sync_remaining_ms=2000,
                        time_sent_ms=server_elapsed_ms(epoch),
                        rng_state=args.rng_seed,
                    ),
                    "START_GAME(18)",
                )
                peer.phase = "start_game_sent"

    try:
        while True:
            process_timers(time.monotonic())
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue

            stamp = time.strftime("%H:%M:%S")
            msg_id = data[HEADER_SIZE] if len(data) > HEADER_SIZE else None

            ok, packet_lan_id = parse_discover(data, args.ggi)
            if ok:
                peer = get_peer(addr)
                print(
                    f"[{stamp}] DISCOVER válido de {addr[0]}:{addr[1]} "
                    f"LAN_ID=0x{packet_lan_id:08x} -> SERVER_DETAILS"
                )
                sock.sendto(discovery_reply, addr)
                continue

            peer = get_peer(addr)
            h = parse_header(data)
            if h and h.reliable:
                peer.latest_client_reliable = h.seq

            if msg_id == 3:
                parsed, err = parse_client_details(data, args.ggi)
                if parsed is None:
                    print(f"[{stamp}] CLIENT_DETAILS inválido de {addr}: {err}")
                    continue
                h = parsed["header"]
                peer.client_name = parsed["name"]
                peer.client_details = parsed["details"]
                peer.joined_at = time.monotonic()
                peer.phase = "joined"
                print(
                    f"[{stamp}] JOIN/CLIENT_DETAILS de {addr[0]}:{addr[1]} "
                    f"reliable={h.reliable} seq={h.seq} latest={h.latest} ack=0x{h.ack:08x} "
                    f"name={peer.client_name!r} details[{len(peer.client_details)}]={peer.client_details.hex(' ')}"
                )
                if args.send_lobby:
                    lobby_host_details = server_details_blob(args.map_id, 1, args.max_players, args.mode_id)
                    send_reliable(
                        peer,
                        make_lobby_app(
                            revision=1,
                            host_details=lobby_host_details,
                            client_name=peer.client_name,
                            client_details=peer.client_details,
                            slot_id=args.player_id,
                        ),
                        "LOBBY_LIST(5)",
                    )
                    peer.phase = "lobby"
                if args.auto_session:
                    peer.start_loading_due = time.monotonic() + max(0.0, args.start_delay)
                continue

            if msg_id == 17:
                # FINISHED_LOADING is fixed length 1 and sent reliably by the client.
                peer.phase = "finished_loading"
                print(
                    f"[{stamp}] <- FINISHED_LOADING(17) de {addr[0]}:{addr[1]} "
                    f"seq={h.seq if h else '?'}; iniciando clock sync"
                )
                if args.auto_session:
                    peer.clock_probe_sent_at = time.monotonic()
                    peer.clock_probe_server_ms = server_elapsed_ms(epoch)
                    send_unreliable(peer, make_req_clock_app(0), "REQ_CLOCK(6) sample=0")
                    peer.phase = "clock_probe"
                continue

            if msg_id == 7:
                parsed_clock = parse_send_clock(data)
                if parsed_clock is None:
                    print(f"[{stamp}] SEND_CLOCK(7) inválido de {addr}")
                    continue
                sample_id, client_tick = parsed_clock
                now = time.monotonic()
                rtt_ms = 0
                if peer.clock_probe_sent_at is not None:
                    rtt_ms = max(0, int((now - peer.clock_probe_sent_at) * 1000.0))
                # Estimate server time at the midpoint of the exchange, then derive
                # server_clock - client_tick. The client later adds this modulo-u32
                # offset to its local GetTickCount-derived clock.
                server_mid_ms = (server_elapsed_ms(epoch) - (rtt_ms // 2)) & U32_MASK
                clock_offset = (server_mid_ms - client_tick) & U32_MASK
                peer.rtt_ms = rtt_ms
                peer.clock_offset_u32 = clock_offset
                print(
                    f"[{stamp}] <- SEND_CLOCK(7) sample={sample_id} client_tick={client_tick} "
                    f"rtt≈{rtt_ms}ms offset={signed32(clock_offset)} (0x{clock_offset:08x})"
                )
                if args.auto_session:
                    send_reliable(
                        peer,
                        make_time_sync_app(rtt_ms, clock_offset),
                        f"TIME_SYNC(8) ping={rtt_ms} offset={signed32(clock_offset)}",
                    )
                    peer.phase = "time_synced"
                    peer.start_game_due = time.monotonic() + max(0.0, args.start_game_delay)
                continue

            if h and msg_id is not None:
                print(
                    f"[{stamp}] pacote de {addr[0]}:{addr[1]} len={len(data)} id={msg_id} "
                    f"reliable={h.reliable} seq={h.seq} latest={h.latest} ack=0x{h.ack:08x} phase={peer.phase}"
                )
            else:
                print(f"[{stamp}] pacote ignorado de {addr[0]}:{addr[1]} len={len(data)}")
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
