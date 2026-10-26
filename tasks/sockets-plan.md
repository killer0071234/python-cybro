# Plan: Cybro sockets (receive + send) in direct mode

Status: done, released in 1.1.0 · Repo: `python-cybro`
Sources: `docs/abus-notes.md` (spike S3), Edge Toolkit scgi_server
(`socket_service/socket_message.py`, `config/eth_config.py`, `config.ini`).

## 1. Goal

`CybroDirect` users (the HIQ integration in particular) get socket values the
moment a controller sends them, instead of on the next poll. The library can
also send socket frames to a controller, so a PLC receive socket can pick them
up. This is the "Socket events" item of `standalone-plan.md` §8.

### Decisions (approved 2026-10-26)

| Topic              | Decision                                                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| Send               | **Emit socket frames to PLCs**: the library sends a socket frame that a CyPro receive socket on the controller accepts      |
| Definitions        | **User-supplied.** Toolkit syntax `id; bits; ints; longs` (e.g. `1; req, b0; i0; l0`). Reading `cyp.zip` is not in scope |
| Value types        | **ALC type per variable.** The slot size comes from the definition, the type from the ALC (signed INT/LONG, REAL)          |
| API                | **Both layers.** Low level: decode/encode + transport subscription. High level: `CybroDirect` updates `device.vars` + listeners |

### Not in scope

- Socket definitions read from the controller's `cyp.zip`.
- Socket events in HTTP mode (`Cybro` through the scgi server's websocket).
- Push frames (none seen on the spike network, S7) and push ACK.

## 2. What we know

Frame (spike S3, toolkit `abus_message.is_socket`): `from_nad` = sender,
`to_nad` = 0, direction ACK, `msg_type` = socket id (1..255, not 0 or 0x64),
`txn` = 0. `Frame.kind == "socket"` already classifies it.

Body (toolkit `SocketMessage.create`): no layout information in the frame.
Values are packed in definition order: all BIT slots (1 byte each), then all
INT slots (2 bytes), then all LONG slots (4 bytes), little-endian. The toolkit
always decodes them unsigned (`<B`, `<H`, `<I`), so REALs come out as raw
integers. That's why we decode with the ALC type instead.

Check against captured data: c8372 socket 3 body `010100 499357c5 ff9c9746
fec1df47` (15 B) = 3 bits + 3 LONG slots. As REAL: `-3449.2053`, `19406.498`,
`114563.98`, which looks plausible. Raw u32 it would be meaningless.

**Incoming sockets (S9, `docs/abus-notes.md`):** the controller accepts a
socket with direction ACK and `msg_type` = a socket id it has. It accepts any
`to_nad` (0 or its own), unicast or broadcast, any sender NAD and any txn. The
body must have exactly the definition's length, or the frame is ignored. There
is no answer. So sending is fire and forget: `Frame(source_nad, 0, 0, ACK, id,
body)`, unicast to the controller's IP.

## 3. Design

New module `cybro/abus/sockets.py` (pure functions, no I/O):

```python
@dataclass(frozen=True)
class SocketDef:
    socket_id: int
    bits: tuple[str, ...] = ()
    ints: tuple[str, ...] = ()
    longs: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> SocketDef: ...   # toolkit syntax, ValueError if invalid
    @property
    def size(self) -> int: ...                      # bytes of the body
    def slots(self) -> Iterator[tuple[str, DataType]]:  # (name, slot type) in body order

@dataclass(frozen=True)
class SocketMessage:
    nad: int
    socket_id: int
    values: dict[str, int | float]                 # name without c<nad>. prefix
    addr: Addr

def decode_socket(definition, body, alc) -> dict[str, int | float]
def encode_socket(definition, values, alc) -> bytes
```

Type rule per slot: the ALC variable's type if its size matches the slot
(BIT→1, INT→2, LONG/REAL→4). A variable missing from the ALC gets the slot's
default (BIT/INT/LONG, signed like the rest of the library). A size mismatch or
a wrong body length raises `AbusError`. Values reuse `DataType.fmt`,
`format_value` and `parse_value` from `alc.py`.

**Receive, low level:** `AbusTransport.on_socket(callback)` is a thin filter
over `on_unsolicited` (`frame.kind == "socket"`). It returns the remove
function, like `on_unsolicited`.

**Receive, high level (`CybroDirect`):**

- `add_socket(definition: SocketDef | str)` / `remove_socket(socket_id)`.
- `on_socket(callback: Callable[[SocketMessage], None]) -> Callable[[], None]`.
- On the first `add_socket`, CybroDirect subscribes once to `transport.on_socket`
  and unsubscribes in `disconnect()`.
- Handler (synchronous, inside `datagram_received`): ignore other NADs and
  unknown socket ids. Decode with the cached ALC. If the ALC isn't loaded yet
  (no `update()` so far, or the program changed), drop the frame with a debug
  log, as the toolkit does. Otherwise write each value into `device.vars` as
  `Var(f"c{nad}.{name}", format_value(...), description)`, so the next read of
  `device.vars` already has it. Then call the listeners. A listener exception is
  logged and doesn't stop the others.
- Also update `client.ip` from the frame's source address, so IP changes are
  caught (same idea as standalone T14).

**Send:**

- `AbusClient.send_socket(socket_id: int, body: bytes) -> None`. Fire and forget:
  `transport.send_frame(frame, addr)`. No retry. The controller
  doesn't answer (S9), so a lost frame can't be detected.
- `CybroDirect.send_socket(definition: SocketDef | str, values: Mapping[str, str | int | float])`:
  checks the program (`_check_program`, so the ALC is current), validates
  every name exists in the ALC and every value fits (`parse_value`, raises
  `CybroError` like `write_var`), encodes, sends. All slots of the definition
  are required, because a socket body has no "unchanged" marker.
- `AbusTransport` gets a public `send_frame(frame, addr=None)` (broadcast when
  `addr` is None) instead of callers using `_send`.

**Test support:** `FakeAbusController` gets `receive_sockets: dict[int, SocketDef]`.
An incoming socket frame that matches the S9 rules writes into the fake's
memory, so `send_socket` round-trips can be tested. `send_socket(addr, id, body)`
already exists for the receive side. Add a helper that builds the body from a
definition + values.

## 4. Tasks

### Phase 0: Spike (hardware)

- [x] **S9 (S) Sockets on the test controller.** Done 2026-10-26, see
      `docs/abus-notes.md`. Same layout in both directions. On-change frames
      can be missed, so polling stays. The maintainer's test
      program has two sockets with the same variables (`test_sock_bool`,
      `test_sock_int`, `test_sock_long`, `test_sock_real`):
      **id 63** sends when `test_sock_bool` changes, **id 64** only receives.
      Run on the HA host (host network, scgi add-on and HIQ direct entries
      stopped, so 8442 is free):
      1. Receive: `abus_probe.py listen --seconds 120 -v`, toggle
         `test_sock_bool`. Expect an 11-byte body (1 + 2 + 4 + 4) for socket 63.
         This confirms the order bits → ints → longs and REAL in a long slot.
      2. Send: `abus_probe.py send-socket --nad N --id 64 --matrix
         test_sock_bool=1 test_sock_int=100 test_sock_long=1000
         test_sock_real=1.5`. This tries `to_nad` 0/N × unicast/broadcast and
         reads the values back. Then repeat the accepted variant with
         `--from-nad` set to another NAD, and with `--direction 0` (REQ),
         to learn whether the sender NAD and the direction matter.
      _Done when:_ results are in `docs/abus-notes.md` (socket 63 raw body,
      the frame fields that work, the sender NAD rule, the answer or none).

### Phase 1: Codec

- [x] **T1 (S) `SocketDef` + parser.** Toolkit syntax, trailing `;` and empty
      groups allowed (`10; socket_req; a, b, c;`), id 1..255 excluding 0x64,
      duplicate names rejected.
      _Done when:_ tests cover both `config.ini` examples, empty groups, bad id,
      bad separators.
- [x] **T2 (S) `decode_socket` / `encode_socket`.** Types from the ALC per the
      rule above.
      _Done when:_ the c8372 socket 3 body decodes to `[1, 1, 0, -3449.205322265625,
      19406.498046875, 114563.984375]` with an ALC of 3 BIT + 3 REAL. Encode
      round-trips decode. A missing ALC entry falls back to the slot type. A
      wrong body length or a size mismatch raises `AbusError`. An out-of-range
      value raises `ValueError`.

**Checkpoint 1:** `poetry run pytest` + pre-commit green.

### Phase 2: Receive

- [x] **T3 (S) `AbusTransport.on_socket` + `send_frame`.**
      _Done when:_ socket frames reach `on_socket` callbacks, and push,
      broadcast and response frames don't.
- [x] **T4 (M) `CybroDirect` socket receive.** `add_socket`, `remove_socket`,
      `on_socket`, `device.vars` update, IP update, unsubscribe on `disconnect()`.
      _Done when:_ with `FakeAbusController.send_socket`: values appear in
      `device.vars` formatted like `read_var()` (REAL as `str(float)`) and
      listeners get a `SocketMessage`. Frames from another NAD, an unknown id,
      a wrong length, or arriving before the first `update()` are dropped
      without an exception. Two `CybroDirect` on one transport only see their
      own NAD.

**Checkpoint 2 (passed 2026-10-26):** c10000 socket 63 shows live, typed
values with `examples/sockets.py` (installed from the branch on a LAN
machine). The socket values match the next poll. See `docs/abus-notes.md`.

### Phase 3: Send (fields from S9)

- [x] **T5 (S) `AbusClient.send_socket` + fake receive sockets.**
      _Done when:_ a frame sent to `FakeAbusController` with `receive_sockets`
      changes its memory. Frame fields match S9.
- [x] **T6 (S) `CybroDirect.send_socket`.** Validation as in `write_var`.
      _Done when:_ tests cover OK, unknown variable, value out of range, missing
      slot value, and plc status not ok. The value round-trips through the fake.

- [x] **T6a (S) Send by type, without names.** `encode_socket_values(bits,
      ints, longs, reals)` and `CybroDirect.send_socket_values(socket_id, ...)`:
      counts are the list lengths, body order bits → ints → longs → reals, no
      ALC needed. Asked for by the maintainer 2026-10-26.

**Checkpoint 3 (passed 2026-10-26):** `CybroDirect.send_socket` to socket 64
of c10000 changes all four variables (read back with `read_var`), also
negative values and the INT/LONG maximum. See `docs/abus-notes.md`.

### Phase 4: Docs + release

- [x] **T7 (S) README "Sockets" section, `examples/sockets.py`, exports** in
      `cybro.abus.__init__` (`SocketDef`, `SocketMessage`) and the changelog.
      The release is a minor version (1.1.0), because the API is only added to.

## 5. Risks

| Risk                                                                    | Impact                                     | Mitigation                                                                                          |
| ----------------------------------------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Definition doesn't match the PLC program (order, count)                 | Wrong values, silently                     | Body length check. Document that the order is bits → ints → longs, as in CyPro. Debug log of raw body |
| Sockets only arrive while bound to 8442                                 | No events with the `:0` fallback           | Document it. `CybroDirect` exposes whether the transport is on 8442, so HA can show a repair issue  |
| Callback runs inside `datagram_received`                                | A slow listener blocks the transport       | Document "don't block". HA will use `call_soon`/`async_set_updated_data`                             |
| A controller rejects foreign sockets, or needs a specific sender NAD    | Send doesn't work                          | S9 before Phase 3. `source_nad` is already configurable                                              |
| Sockets also reach the scgi server on the same LAN                      | None (receiving only)                      | –                                                                                                   |
