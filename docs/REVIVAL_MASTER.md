# Blitz Brigade 1.3.1.13 — Revival master notes

## Estado do material original

- Pacote de origem: `GAMELOFTSA.BlitzBrigade_1.3.1.13_x86__0pp20fcewvvtj.Appx`
- Plataforma: Windows 8.1 / x86
- Executável: `InfamousHeroesEntry.exe`
- APPX recuperado integralmente do RAR multipart.
- O APPX contém 148 entradas e pode ser lido diretamente como contêiner ZIP.
- SHA-256 do APPX: `60ed922aa0705e60fa35ff03ef7c344b2c0b5abdc5734dbf3adcb3a5ae89784e`

## Arquitetura observada

O cliente contém caminhos separados para:

1. LAN (`LocalHost`, `LocalWifiJoin`, `WifiClientWaitingRoom`)
2. Multiplayer online/XPlay (`alpha01.gameloft.com:7000`, `GGI: 27151`)
3. Descoberta/configuração de serviços (`eve.gameloft.com:20001`, `/locate`, `/config/`, `/datacenters/`, `/urls`)
4. Serviços de conta/perfil/lobby relacionados a Gaia/Hestia/Federation e componentes correlatos.

A estratégia atual é usar a LAN como referência do protocolo de partida e só depois reproduzir o backend online.

## Transporte LAN reconstruído

- IPv4 / UDP
- Porta: `7891`
- LAN application ID: `0x0AB25571` (179459441)
- `GGI 27151` pertence ao XPlay online e não ao discovery LAN.

Cabeçalho de transporte: 13 bytes.

```text
offset  size  encoding       campo
0x00    1     byte           reliable flag
0x01    4     little-endian  sequence
0x05    4     little-endian  latest_received
0x09    4     little-endian  ACK bitmap
0x0D    ...                  application message
```

Mensagens variáveis usam:

```text
[id:u8][total_length:u16 big-endian][body...]
```

## Mensagens-base identificadas

| ID | Nome | Situação |
|---:|---|---|
| 1 | DISCOVER | reconstruído |
| 2 | SERVER_DETAILS | reconstruído |
| 3 | CLIENT_DETAILS / JOIN | estrutura-base reconstruída; payload real ainda deve ser capturado |
| 5 | LOBBY_LIST | envelope reconstruído |
| 6 | REQ_CLOCK | identificado |
| 7 | SEND_CLOCK | identificado |
| 8 | TIMESYNC | identificado |
| 16 | START_LOADING | estrutura reconstruída |
| 17 | FINISHED_LOADING | identificado |
| 18 | START_GAME | tamanho/framing identificado; semântica dos 3 u32 ainda em mapeamento |

### DISCOVER

Datagrama exato de 18 bytes:

```text
00 00 00 00 00 00 00 00 00 00 00 00 00 01 0A B2 55 71
```

### SERVER_DETAILS

O host anuncia normalmente quatro bytes de detalhes:

```text
[map_id, current_players, max_players, mode_id]
```

seguido de um nome de dispositivo/host em campo fixo de 20 bytes.

### START_LOADING

Estrutura atual:

```text
u8      0x10
u8      player_id
u8      map_id
u32be   time_limit_minutes
u8      mode_id
u8      max_players
```

O cliente converte o limite para milissegundos com `time_limit * 60000`.

## Ferramentas produzidas

- `blitz_lan_probe.py`: discovery, listener e decoder do framing conhecido.
- `blitz_lan_mock_server.py`: mock UDP/7891 que responde a DISCOVER e pode emitir LOBBY_LIST experimental após JOIN.
- `REVIVAL_TEST_GUIDE.md`: procedimento de teste contra o cliente original.
- `REVIVAL_PROTOCOL_NOTES.md`: notas detalhadas do protocolo.
- `APPX_INVENTORY.txt`: inventário das 148 entradas do APPX.
- `HASHES_SHA256.txt`: hashes do APPX e dos principais arquivos extraídos para análise.

## Próximos marcos

1. Executar o cliente original em uma máquina/VM e o mock em outro endpoint IPv4.
2. Confirmar que `REVIVAL TEST` aparece em Local/Wi-Fi Join.
3. Capturar o `CLIENT_DETAILS` real ao selecionar o servidor.
4. Ajustar o `LOBBY_LIST` até a waiting room ser aceita pelo cliente.
5. Reproduzir REQ_CLOCK/SEND_CLOCK/TIMESYNC.
6. Emitir START_LOADING, receber FINISHED_LOADING e validar START_GAME.
7. Capturar e nomear as mensagens de gameplay após o início da partida.
8. Só então implementar matchmaking/XPlay e os serviços de conta/perfil necessários ao modo online.

## Observação sobre os assets

Os grandes `.gla` (`weapons.gla`, `sprites.gla`, mapas, sons etc.) permanecem dentro do APPX original. Eles não foram duplicados no workpack porque o objetivo do pacote consolidado é conter apenas ferramentas, notas e metadados técnicos do revival.
