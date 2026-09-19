# Blitz Brigade Revival

Compatibility/revival research for the Windows x86 build of **Blitz Brigade 1.3.1.13**.

The current milestone is an independent LAN compatibility layer: protocol documentation, a UDP discovery/join probe, and an experimental mock server that targets the original client's `Local Wifi Join` flow.

## Current status

- Windows 8.1 / x86 APPX analyzed.
- LAN transport identified as IPv4/UDP on port `7891`.
- LAN application ID identified as `0x0AB25571`.
- 13-byte reliable-UDP transport header reconstructed.
- Discovery (`DISCOVER` / `SERVER_DETAILS`) framing reconstructed.
- Join (`CLIENT_DETAILS`) and lobby (`LOBBY_LIST`) framing partially reconstructed.
- Clock/loading/start message IDs mapped through `START_GAME`.
- Experimental dependency-free Python mock server and probe available under `tools/`.

## Repository layout

```text
docs/
  REVIVAL_MASTER.md          Project status and roadmap
  PROTOCOL.md                Reverse-engineered protocol notes
  TEST_GUIDE.md              Test procedure against the original client
  APPX_INVENTORY.txt         File inventory of the analyzed APPX
  HASHES_SHA256.txt          Reference hashes for the analyzed build

tools/
  blitz_lan_probe.py         LAN discovery/listener/decoder
  blitz_lan_mock_server.py   Experimental UDP compatibility server
```

## Quick test

On the machine running the compatibility server:

```bash
python tools/blitz_lan_mock_server.py --send-lobby
```

From another machine on the same IPv4 LAN:

```bash
python tools/blitz_lan_probe.py discover --target <SERVER_IP>
```

Then open the original game's local Wi-Fi multiplayer screen and look for `REVIVAL TEST`.

See [`docs/TEST_GUIDE.md`](docs/TEST_GUIDE.md) for the complete procedure.

## Original game files

This repository intentionally does **not** contain the original APPX, executable, `.gla` assets, or other proprietary game files. The inventory and hashes are included only to identify the build used for compatibility research.

Analyzed package SHA-256:

```text
60ed922aa0705e60fa35ff03ef7c344b2c0b5abdc5734dbf3adcb3a5ae89784e
```

## Project direction

The immediate sequence is:

```text
LAN discovery
  -> join
  -> waiting room
  -> clock sync
  -> map loading
  -> synchronized game start
  -> gameplay message mapping
  -> online matchmaking/service compatibility
```

The implementation is being kept independent of original game assets and code.
