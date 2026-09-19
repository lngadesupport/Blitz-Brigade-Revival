# Blitz Brigade Revival — LAN compatibility test guide

Target: Blitz Brigade Windows x86 build `1.3.1.13`.

The purpose of this test is to make the unmodified game client discover an external compatibility server and then capture the client's real JOIN packet. No proprietary game assets are included in the test tools.

## Recommended topology

Use two systems on the same IPv4 LAN whenever possible:

```text
PC/VM A                         PC/VM B
--------------------            -------------------------
Python mock server              Blitz Brigade 1.3.1.13
UDP :7891                       Local Wifi Join screen
192.168.x.A                     192.168.x.B
```

A bridged VM is also suitable. Two separate network endpoints avoid UDP/7891 bind conflicts and make broadcast behavior much easier to reason about.

## 1. Start the compatibility server

On PC/VM A, copy `blitz_lan_mock_server.py` and run:

```text
py blitz_lan_mock_server.py --send-lobby
```

or:

```text
python blitz_lan_mock_server.py --send-lobby
```

Expected startup information includes:

```text
UDP 0.0.0.0:7891
LAN_ID = 0x0AB25571
name = REVIVAL TEST
details = [map=0, players=1, max=12, mode=0]
```

Allow Python through Windows Firewall for **Private networks**, or create an inbound UDP rule for port **7891**.

## 2. Optional network sanity check

From another machine, run:

```text
py blitz_lan_probe.py discover --target <IP_DO_SERVIDOR> --port 7891
```

A valid response should decode approximately as:

```text
type: 2 (SERVER_DETAILS)
LAN app ID: 179459441 (0x0ab25571)
details[4]: 00 01 0c 00
server: map_id=0 players=1/12 mode_id=0
device: 'REVIVAL TEST'
```

For broadcast discovery testing, use the subnet broadcast address, for example:

```text
py blitz_lan_probe.py discover --target 192.168.1.255
```

## 3. Test with the original game

On PC/VM B:

1. Start Blitz Brigade 1.3.1.13.
2. Open the local multiplayer / `Local Wifi Join` path.
3. Refresh the server list if the UI exposes a refresh action.
4. Look for the host name **REVIVAL TEST**.
5. If it appears, select/join it.

### What success looks like

First milestone:

```text
Game sends DISCOVER
Mock logs DISCOVER
Game displays REVIVAL TEST
```

Second milestone after clicking Join:

```text
Mock logs:
JOIN/CLIENT_DETAILS ... name='...' details[N]=...
```

That `details[...]` byte sequence is especially important because it gives us the **real client-details layout** used by this build.

With `--send-lobby`, the mock then sends a minimal reliable `LOBBY_LIST`. The client may still stop or reject the synthetic room; that is acceptable at this stage. Capturing a real type-3 JOIN already advances the reverse engineering substantially.

## 4. If the server does not appear

Check in this order:

- PC A and PC B are on the same IPv4 subnet.
- Windows Firewall allows UDP/7891 to Python.
- The game is using `Local Wifi Join`, not the online/XPlay menu.
- The mock terminal is actually receiving a `DISCOVER` packet.
- If no broadcast reaches the mock, test direct connectivity with `blitz_lan_probe.py discover --target <server IP>`.
- Some Wi-Fi access points enable client/AP isolation; disable it or test over Ethernet/bridged VM networking.

Do **not** change the LAN application ID to `27151`. `27151` belongs to the separate XPlay/online configuration. LAN uses `0x0AB25571`.

## 5. Capture to save after the test

Save the mock terminal output, especially lines containing:

```text
DISCOVER válido ...
JOIN/CLIENT_DETAILS ...
pacote ... id=...
```

A packet capture (`pcapng`) of UDP port 7891 is also useful, but the mock's hex/decoded output is enough for the first JOIN experiment.

## 6. Next compatibility milestone

Once a real client JOIN is captured, the next server state machine will be:

```text
DISCOVER
  -> SERVER_DETAILS
JOIN / CLIENT_DETAILS
  -> reliable LOBBY_LIST
  -> clock exchange (REQ_CLOCK / SEND_CLOCK / TIMESYNC)
  -> START_LOADING
client -> FINISHED_LOADING
server -> START_GAME
```

The 32-bit field in `START_LOADING` is the match time limit in minutes; the game converts it internally with `time_limit * 60000`. The base clock path is now reconstructed as `REQ_CLOCK(sample) -> SEND_CLOCK(sample, client_tick) -> TIME_SYNC(ping, offset)`, and `START_GAME` is now decoded as a synchronized start-window value, server synchronized send time, and shared RNG state. Remaining compatibility work is centered on how the real client accepts these reconstructed packets and on the gameplay messages that follow the synchronized start.


## Automated session experiment

A newer experimental path can attempt the complete base-session handshake automatically:

```text
py blitz_lan_mock_server.py --auto-session
```

After a real client sends `CLIENT_DETAILS/JOIN`, the server will:

1. send `LOBBY_LIST`;
2. wait 1 second by default;
3. send reliable `START_LOADING`;
4. wait for reliable `FINISHED_LOADING`;
5. send one `REQ_CLOCK`;
6. use the client's `SEND_CLOCK` reply to estimate RTT and clock offset;
7. send reliable `TIME_SYNC`;
8. send reliable `START_GAME` shortly afterward.

Useful options:

```text
--map 0
--mode 0
--player-id 0
--max-players 12
--time-limit 10
--rng-seed 0x12345678
--start-delay 1.0
--start-game-delay 0.20
```

This state machine is validated against the reconstructed encoder/decoder and a synthetic client, but it is **not yet proof that the original game accepts every stage**. When testing with the actual game, preserve the complete server log; the first stage where the client stops advancing will identify the next compatibility issue.
