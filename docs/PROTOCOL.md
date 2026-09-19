# Blitz Brigade 1.3.1.13 — Revival protocol notes

Source build: `GAMELOFTSA.BlitzBrigade_1.3.1.13_x86__0pp20fcewvvtj.Appx`
Main executable: `InfamousHeroesEntry.exe`

These notes document compatibility-oriented reverse engineering of the original Windows x86 client. The companion tools contain no Gameloft assets or game code.

## 1. LAN transport

- IPv4 / UDP (`AF_INET=2`, `SOCK_DGRAM=2`, `IPPROTO_UDP=17`)
- LAN port: **7891**
- **LAN application ID:** `0x0AB25571` (179459441), passed directly by the `LocalWifiJoin` / `LocalHost` callers.
- `strings.gla` separately advertises `GGI: 27151` for the XPlay/online stack; **27151 is not the LAN discovery ID**.
- Local state paths include `LocalHost`, `LocalWifiJoin`, `WifiClientWaitingRoom`, `StartMPLevelServer` and `InfamousHeroesServer`.

### 13-byte transport header

The application payload begins at offset `0x0D`.

```text
offset  size  encoding       meaning
0x00    1     byte           reliability flag (0/1)
0x01    4     little-endian  outgoing sequence number
0x05    4     little-endian  latest received sequence
0x09    4     little-endian  ACK bitmap/window
0x0D    ...                  application message
```

The ACK bitmap covers up to 32 sequence positions. Bit 0 acknowledges `latest_received`; subsequent bits correspond to successively older sequence numbers.

The initial LAN discovery packet uses a zeroed 13-byte transport header.

## 2. Application framing

The executable contains a size table for application message IDs **0..140**.

- Fixed-size message: `[id:u8][body...]`; the table value is the **total application-message size including the ID**.
- Variable-size message: `[id:u8][total_length:u16be][body...]`; `total_length` includes the ID and the two length bytes.
- Variable IDs observed in the table: `2, 3, 5, 14, 21, 37, 88, 98, 99, 128, 136`.

Important base/session IDs identified so far:

| ID | Total size | Meaning / status |
|---:|---:|---|
| 1 | 5 | `DISCOVER` |
| 2 | variable | `SERVER_DETAILS` |
| 3 | variable | `CLIENT_DETAILS` / join |
| 4 | 1 | lobby/base control; exact semantic not yet named |
| 5 | variable | `LOBBY_LIST` |
| 6 | 2 | `REQ_CLOCK` |
| 7 | 6 | `SEND_CLOCK` |
| 8 | 9 | `TIMESYNC` |
| 11 | 1 | quit/bye path (context-dependent handling) |
| 13 | 9 | ping/control timing path |
| 14 | variable | per-player state list; exact semantic pending |
| 15 | 1 | empty low-level control message |
| 16 | 9 | `START_LOADING` |
| 17 | 1 | `FINISHED_LOADING` |
| 18 | 13 | `START_GAME` |

## 3. Discovery and join

### ID 1 — DISCOVER

```text
13 bytes  zero transport header
u8        0x01
u32be     LAN application ID = 0x0AB25571
```

Exact 18-byte datagram:

``text
00 00 00 00 00 00 00 00 00 00 00 00 00 01 0a b2 55 71
```

### ID 2 — SERVER_DETAILS

Variable application message:

```text
u8        0x02
u16be     total application-message length
u32be     LAN application ID
u8        details_length
bytes     details[details_length]
bytes     host/device name, fixed 20-byte field
```

The game's host-side setup strongly indicates a normal four-byte details block:

```text
details[0]  map index / map ID
details[1]  current players
details[2]  maximum players
details[3]  game mode ID/index   (strong inference from host setup)
```

`LocalWifiJoin` directly reads the first three bytes to render map and `%d/%d`; the fourth byte is retained for later session setup.

With a four-byte details block, the type-2 application message is 32 bytes and the UDP datagram is **45 bytes** including the 13-byte transport header.

The discovery response uses the non-reliable transport path; in the initial state its 13-byte header is all zero.

### ID 3 — CLIENT_DETAILS / JOIN

Variable application message:

```text
u8        0x03
u16be     total application-message length
u32be     LAN application ID
u8        details_length
bytes     client_details[details_length]
bytes     client/device name, fixed 20-byte field
```

The client sends this reliably after selecting a discovered server. The server checks state/capacity, allocates a client slot and records the client details/name.

## 4. Waiting room

### ID 5 — LOBBY_LIST

Server sender log: `Server sends lobby list to all clients!`

```text
u8        0x05
u16be     total length
u32be     lobby revision
u8        host_details_length
bytes     host_details
u8        client_count
repeat client_count times:
  bytes   client name, fixed 20 bytes
  u8      client_details_length
  bytes   client_details
  u8       slot ID
```

The client-side parser validates the revision, copies host details, clears/rebuilds the lobby client list, and logs `Client received lobby players list from server`.

The mock server can emit a minimal reliable type-5 response after a real type-3 JOIN with `--send-lobby`. This remains an **experimental compatibility packet** until tested against the original client.

## 5. Clock/loading/start sequence

### ID 6 — REQ_CLOCK

Fixed 2 bytes: message ID + one byte parameter.

### ID 7 — SEND_CLOCK

Fixed 6 bytes:

```text
u8      0x07
u8      sample ID
u32be   client tick (GetTickCount timeline)
```

The client echoes the `sample ID` received in `REQ_CLOCK (6)` and attaches its current tick. The original server uses repeated samples to estimate RTT and clock offset.

### ID 8 — TIME_SYNC

Fixed 9 bytes:

```text
u8      0x08
u32be   ping/RTT estimate in milliseconds
u32be   server-clock minus client-clock offset (modulo u32)
```

The client stores the negation of the received offset as its local clock base. Subsequent synchronized time calculations therefore evaluate as `GetTickCount - base`, equivalent to `GetTickCount + offset` modulo 2^32.

### ID 16 — START_LOADING

Fixed 9 bytes. The host serializes:

```text
u8      0x10
u8      assigned player/network index
u8      map ID/index                    (strong inference)
u32be   time limit in minutes
u8      game mode ID/index              (strong inference)
u8      maximum players                 (strong inference)
```

The map/mode/max-player interpretations follow from the host routine that separately constructs server details as `[map, 1, maxPlayers, mode]`. The 32-bit field is the match time limit in minutes; the game converts it internally with `time_limit * 60000` for millisecond comparisons.

The receiving client enters a loading state and logs `Received MP_MESSAGE_STARTLOADING!` and `I am player %d`.

### ID 17 — FINISHED_LOADING

Fixed 1 byte. The client sends it after loading and logs `Finished loading! -> waiting for clock sync with server`. The server marks that client as finished.

### ID 18 — START_GAME

Fixed 13 bytes:

```text
u8      0x12
u32be   synchronized start-window remaining milliseconds
u32be   server synchronized time at send
u32be   shared global RNG state/seed
```

The first field is approximately `2000` on the initial send and counts down if the message is regenerated/re-sent later. The receiving client combines it with `time_sent` and its synchronized clock to reconstruct the same future start marker. The third field is written directly into the game's global LCG state, keeping pseudo-random events deterministic across peers.

## 6. Reliable UDP behavior reconstructed

For reliable datagrams:

- byte 0 of the transport header is nonzero;
- `sequence` is the sender's outgoing sequence;
- `latest_received` identifies the newest peer sequence being acknowledged;
- ACK bitmap bit 0 acknowledges `latest_received`;
- duplicate/history handling happens before the application dispatcher;
- ACK processing runs for received packets independently of the application message ID.

A minimal server replying reliably to a client's JOIN sequence `N` can therefore use:

```text
reliable = 1
server sequence = server-side next sequence
latest_received = N
ack_bitmap = 0x00000001
```

## 7. Online infrastructure strings

`strings.gla` includes:

```text
XPlayMPURL: socket://alpha01.gameloft.com:7000
GGI: 27151
XPPHPVerNo: 4
GAME_PARAM_COUNT: 9
GAME_PARAM_TYPE: 0|0|0|0|0|0|0|0|0
```

The executable also references `eve.gameloft.com:20001`, `/locate`, `/config/`, `/datacenters/`, `/urls`, authentication/profile/room routes and Federation-related code.

The practical revival strategy remains to reproduce the LAN/session protocol first, then place compatible matchmaking/service-discovery infrastructure in front of it.

## 8. Companion tools

### `blitz_lan_probe.py`

Dependency-free decoder/discovery probe:

```text
python blitz_lan_probe.py discover
python blitz_lan_probe.py discover --target 192.168.1.255
python blitz_lan_probe.py listen
```

It understands the 13-byte transport header, fixed/variable application framing, IDs 1/2/3/5 and selected timing/start messages.

### `blitz_lan_mock_server.py`

Dependency-free experimental compatibility server:

```text
python blitz_lan_mock_server.py
python blitz_lan_mock_server.py --send-lobby
python blitz_lan_mock_server.py --map 0 --mode 0 --max-players 12 --send-lobby
```

Implemented:

```text
DISCOVER (1)      -> SERVER_DETAILS (2)
CLIENT_DETAILS(3) -> decode/log
CLIENT_DETAILS(3) -> optional minimal LOBBY_LIST (5)
```

Default advertised details are `[map=0, players=1, max=12, mode=0]` and the host name is `REVIVAL TEST`.

## 9. Current experimental milestone

The local self-test now reproduces the reconstructed framing consistently:

```text
18-byte DISCOVER
  -> 45-byte SERVER_DETAILS
     variable total = 32
     LAN ID = 0x0AB25571
     details = 00 01 0c 00
     name = REVIVAL TEST
```

This validates our encoder/decoder against the statically reconstructed format. It **does not yet prove acceptance by the original game client**. The next decisive experiment is to run the mock on a second machine/bridged VM and use the game's `Local Wifi Join` screen to discover and select it.


## 10. Clock synchronization — reconstructed flow

Static analysis of the base-message dispatcher and send routines closes the loading/clock path:

```text
server -> client  REQ_CLOCK (6):  sample_id:u8
client -> server  SEND_CLOCK (7): sample_id:u8 + client_tick:u32be
server -> client  TIME_SYNC (8):  ping_ms:u32be + clock_offset:u32be
```

The server samples the client clock repeatedly in the original implementation. For each reply it estimates round-trip time and a server-clock/client-clock offset. `TIME_SYNC` then sends a selected RTT/ping value plus the offset. The client stores the negation of the offset as its local clock base, so later `GetTickCount - base` evaluates to the server-synchronized timeline (modulo2 2^32).

`FINISHED_LOADING (17)` is a reliable, body-less fixed message. On the server it resets the per-client clock-sync state; this is what starts the synchronization phase after map loading.

For the revival mock, a one-sample compatibility approximation is now implemented: one `REQ_CLOCK`, one `SEND_CLOCK`, midpoint RTT/offset estimation, then one reliable `TIME_SYNC`. This is intentionally simpler than the original multi-sample estimator and still needs validation against the real client.

## 11. START_GAME fields closed

The original sender for message 18 serializes exactly three `u32be` values after the ID:

```text
u32 #1  sync_remaining_ms
u32 #2  time_sent_ms
u32 #3  rng_state
```

The first field is derived from a roughly 2000 ms synchronized-start window. On first send it is approximately `2000`; on retransmission/re-send it decreases as the server's start marker ages. The receiving client combines this value with `time_sent_ms` and its synchronized clock base to reconstruct the same start marker locally.

The third field is conclusively the global pseudo-random generator state. The same global is used by an LCG with constants `0x0019660D` and `0x3C6EF35F`, and the client writes the received third START_GAME value back into that RNG state before gameplay. This keeps deterministic/randomized game events aligned across peers.

## 12. Match configuration offsets confirmed

The configuration block used by `START_LOADINGg is now mapped more strongly:

```text
+0x30FC  game mode ID/index
+0x3100  map ID/index
+0x3104  time limit, minutes
+0x3108  score/frag/goal limit
+0x3110  maximum players
```

A setup routine takes `(mode, map)` and fills the remaining limits from the game's map/mode configuration tables. `START_LOADING` transmits map, time limit, mode and max players; the score/goal limit is not present in message 16 and is therefore derived locally from the same content/config tables.

## 13. Current experimental session state machine

`blitz_lan_mock_server.py --auto-session` now implements:

```text
DISCOVER (1)
  -> SERVER_DETAILS (2)
CLIENT_DETAILS (3)
  -> LOBBY_LIST (5, reliable)
  -> START_LOADING (16, reliable)
FINISHED_LOADING (17, reliable)
  -> REQ_CLOCK (6, unreliable)
SEND_CLOCK (7, unreliable)
  -> TIME_SYNC (8, reliable)
  -> START_GAME (18, reliable)
```

The mock tracks a per-peer reliable sequence, acknowledges the latest reliable client sequence in outgoing packets, computes a modulo-u32 clock offset, and uses a deterministic configurable RNG seed for repeatable tests.
