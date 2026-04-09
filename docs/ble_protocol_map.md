# BLE Communication Protocol Map: ESP32-C3 Remote ↔ Raspberry Pi

## Overview

The ESP32-C3 SuperMini is the BLE **peripheral** (GATT server).
The Raspberry Pi is the BLE **central** (GATT client).

The remote advertises, the Pi scans, connects, subscribes to notifications,
and receives single-byte ASCII button events. There is no pairing, bonding, or
encryption — it's a plaintext GATT connection.

---

## Protocol Constants

| Item | Value | Notes |
|------|-------|-------|
| Device name | `EasyPlay` | Set by `NimBLEDevice::init("EasyPlay")` |
| Address type | `BLE_OWN_ADDR_PUBLIC` | Forced public so bleak can match by MAC |
| ESP32-C3 MAC | *board-specific* | Printed to Serial on boot; stored in `easyplay_config.json` on Pi |
| Service UUID (NUS) | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` | Nordic UART Service |
| TX characteristic | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | ESP→Pi notifications (READ \| NOTIFY) |
| RX characteristic | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` | Pi→ESP writes (WRITE_NR) — not currently used |
| Advertising interval | 250 ms (400 × 0.625 ms units) | Fast, constant; no sleep |
| Advertising includes | Service UUID + device name | So BlueZ can match by UUID or name |

## Button Event Format

Single byte, ASCII:

| Button | Press (key-down) | Release (key-up) |
|--------|-----------------|-------------------|
| Left | `L` (0x4C) | `l` (0x6C) |
| Right | `R` (0x52) | `r` (0x72) |
| Up | `U` (0x55) | `u` (0x75) |
| Down | `D` (0x44) | `d` (0x64) |
| On/Off | `O` (0x4F) | `o` (0x6F) |

Sent via `pTxChar->setValue(&data, 1); pTxChar->notify();`
Received on Pi as `bytearray` of length 1 in the notification handler.

---

## Full Sequence: Power-On to Button Event

### Phase 0: Hardware prerequisites

```
[ ] 0.1  ESP32-C3 powered on (2× 18650 batteries or USB)
[ ] 0.2  Pi has a working BT adapter (hci0)
         — verify: `hciconfig hci0` shows "UP RUNNING"
         — if using external dongle: `dtoverlay=disable-bt` in config.txt,
           internal disabled, USB dongle is hci0
[ ] 0.3  Pi's bluetooth.service is active
         — verify: `systemctl is-active bluetooth`
[ ] 0.4  No RF-kill blocking BT
         — verify: `rfkill list` shows no "Soft blocked: yes" for bluetooth
         — fix: `sudo rfkill unblock bluetooth`
[ ] 0.5  Pi's BlueZ is not in a stale state
         — if issues: `sudo hciconfig hci0 reset` (EasyPlay does this on startup)
```

### Phase 1: ESP32-C3 boot and advertising

```
[ ] 1.1  Serial.begin(115200), 2s delay for serial monitor
[ ] 1.2  NeoPixel init — brief blue flash (200ms)
[ ] 1.3  GPIO pins 0-4 configured as INPUT_PULLUP
[ ] 1.4  NimBLEDevice::init("EasyPlay")
[ ] 1.5  Address type set to BLE_OWN_ADDR_PUBLIC
[ ] 1.6  MAC printed to Serial
         — note this MAC! Pi needs it for connection
[ ] 1.7  GATT server created:
         └─ Service: 6E400001-...
            ├─ TX char: 6E400003-... (READ | NOTIFY)  ← ESP sends to Pi
            └─ RX char: 6E400002-... (WRITE_NR)       ← Pi sends to ESP
[ ] 1.8  Service started
[ ] 1.9  Advertising started:
         — name = "EasyPlay"
         — interval = 250ms
         — includes service UUID
[ ] 1.10 Serial prints "Advertising as 'EasyPlay' — waiting for connection..."
         — ESP is now discoverable and connectable
```

**Verify Phase 1:**
- Serial monitor shows MAC + "Advertising" message
- From any BT scanner (phone, `bluetoothctl scan le` on Pi), "EasyPlay" should appear
- NeoPixel is off (blue flash already passed)

### Phase 2: Pi scans for the remote

EasyPlay's `_ble_uart_thread` runs in a background thread:

```
[ ] 2.1  start_ble_listener() called during EasyPlay startup
[ ] 2.2  get_bt_remote() reads MAC from easyplay_config.json
         — if no MAC stored: BLE listener silently does nothing
         — to set: use the hidden setup menu (hold DOWN) in EasyPlay UI,
           or manually edit easyplay_config.json:
           {"bluetooth_remote_addr": "XX:XX:XX:XX:XX:XX",
            "bluetooth_remote_name": "EasyPlay"}
[ ] 2.3  _ble_reset_adapter() called: `sudo hciconfig hci0 reset`
         — clears stale scan/connection state in BlueZ
         — 150ms settle delay
[ ] 2.4  BleakScanner starts with detection_callback, adapter="hci0"
[ ] 2.5  Scanner looks for a device with matching MAC (case-insensitive)
         — up to 10s scan timeout, up to 3 attempts
         — if BlueZ reports "InProgress" or "NotReady": adapter reset + retry
[ ] 2.6  Device found → scanner stops, returns the BLEDevice object
```

**Verify Phase 2:**
- EasyPlay log: `[BLE]` lines show scanning activity
- If scanner doesn't find the device:
  - Check MAC matches exactly (stored vs printed on Serial)
  - Check ESP32 is advertising (Serial says "Advertising")
  - Check BT adapter is up: `hciconfig hci0`
  - Check distance/interference (BLE range ~10m typical)
  - Try manual scan: `sudo bluetoothctl scan le` — look for "EasyPlay"

### Phase 3: Pi connects to the remote

```
[ ] 3.1  BleakClient(device, timeout=10.0, adapter="hci0") created
         — uses the BLEDevice object from scan (NOT raw MAC string)
         — passing the device object avoids BlueZ cache issues
[ ] 3.2  BleakClient.__aenter__: GATT connection established
         — BLE link-layer connection
         — MTU negotiation
         — GATT service discovery (BlueZ enumerates services + characteristics)
[ ] 3.3  ESP32's ServerCallbacks::onConnect fires:
         — sets deviceConnected = true
         — NeoPixel turns green
         — Serial prints "Pi connected!"
[ ] 3.4  Pi confirms connection:
         — print("[BLE] connected to EasyPlay Remote")
```

**Verify Phase 3:**
- ESP32 Serial: "Pi connected!" + green LED
- EasyPlay log: "[BLE] connected to EasyPlay Remote"
- If connection fails:
  - "InProgress": BlueZ thinks a prior connection is still active → adapter reset
  - "le-connection-abort": BLE link layer failed → retry
  - Timeout: check distance, check ESP32 isn't already connected to another device

### Phase 4: Pi subscribes to notifications

```
[ ] 4.1  client.start_notify(TX_UUID, notification_handler) called
         — TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
         — This writes to the CCCD (Client Characteristic Configuration
           Descriptor) on the ESP32, enabling notifications
[ ] 4.2  NimBLE on ESP32 registers the subscription internally
         — pTxChar->notify() will now deliver data to the Pi
[ ] 4.3  Pi confirms:
         — print("[BLE] subscribed to notifications on 6E400003-...")
[ ] 4.4  Pi enters wait loop: `await disconnect_event.wait()`
         — Stays connected indefinitely, processing notifications as they arrive
```

**Verify Phase 4:**
- EasyPlay log: "[BLE] subscribed to notifications on 6E400003-..."
- If subscription fails:
  - Check the TX characteristic UUID matches exactly (case-insensitive)
  - Check the characteristic supports NOTIFY property
  - Try enumerating services manually:
    `python3 -c "import asyncio; from bleak import BleakClient; ..."`

### Phase 5: Button press → notification → action

```
[ ] 5.1  User presses a button on the remote
         — GPIO pin reads LOW (active-low, INPUT_PULLUP)
[ ] 5.2  ESP32 debounce check: 50ms since last change on this button
[ ] 5.3  ESP32 calls bleSend(downChar):
         — pTxChar->setValue(&data, 1)    // e.g. 'U' = 0x55
         — pTxChar->notify()
[ ] 5.4  BLE notification delivered over the air to the Pi
         — NimBLE handles the ATT notification PDU
         — BLE latency typically 7.5ms–30ms depending on connection interval
[ ] 5.5  Pi's notification_handler(sender, data) fires:
         — data = bytearray(b'U')
         — char = data.decode().strip()  → "U"
         — _ble_key_queue.put(char)
[ ] 5.6  EasyPlay main loop reads from _ble_key_queue (polled every frame)
         — Translates "U" to a pygame KEYDOWN event for the UP action
         — Triggers the corresponding UI action (confirm/play)
[ ] 5.7  User releases the button
         — Same path: bleSend('u') → notification → _ble_key_queue.put('u')
         — EasyPlay interprets as KEYUP
```

**Verify Phase 5:**
- ESP32 Serial: "BTN DOWN: UP" / "BTN UP: UP"
- EasyPlay log: `[BLE] button received: 'U'`
- UI responds to the button action

### Phase 6: Disconnection and reconnection

```
[ ] 6.1  Connection drops (distance, interference, ESP32 reset, ...)
[ ] 6.2  ESP32's ServerCallbacks::onDisconnect fires:
         — NeoPixel turns off
         — NimBLEDevice::startAdvertising() called → back to Phase 1.9
         — Serial: "Disconnected (reason=XX)\nAdvertising restarted"
[ ] 6.3  Pi's on_disconnect callback fires:
         — disconnect_event.set()
         — BleakClient context exits cleanly
[ ] 6.4  Pi sets needs_reset = True
[ ] 6.5  Pi waits 0.5s, then calls _ble_reset_adapter()
         — `sudo hciconfig hci0 reset`
[ ] 6.6  Pi returns to Phase 2.4 (scan loop)
         — Finds ESP32 advertising again
         — Reconnects → Phase 3 → Phase 4 → Phase 5
```

**Verify Phase 6:**
- ESP32 Serial: "Disconnected" + "Advertising restarted"
- EasyPlay log: "[BLE] disconnected — will reconnect"
- Connection re-establishes within ~5-15 seconds

---

## Common Failure Points (with diagnostics)

### F1: Pi never finds the remote during scan

| Check | Command | Expected |
|-------|---------|----------|
| BT adapter up? | `hciconfig hci0` | "UP RUNNING" |
| RF-kill? | `sudo /usr/sbin/rfkill list` | No "Soft blocked: yes" |
| ESP32 advertising? | Check Serial monitor | "Advertising as 'EasyPlay'" |
| MAC in config? | `cat ~/Desktop/EasyPlay/easyplay_config.json` | `bluetooth_remote_addr` set |
| Can BlueZ see it? | `sudo bluetoothctl scan le` | "EasyPlay" appears with matching MAC |
| Distance/walls? | Move closer | BLE is ~10m line-of-sight |

### F2: Scan finds device but connection fails

| Check | Command | Expected |
|-------|---------|----------|
| BlueZ stuck? | `sudo hciconfig hci0 reset` | Clears "InProgress" state |
| Stale GATT cache? | `sudo rm -rf /var/lib/bluetooth/*/cache/*` + restart bluetooth | Forces fresh service discovery |
| ESP32 already connected? | Check ESP32 Serial | Should say "Advertising", not "Pi connected" |
| Address type mismatch? | ESP32 must use `BLE_OWN_ADDR_PUBLIC` | Random addresses confuse bleak |

### F3: Connected but no notifications

| Check | Command | Expected |
|-------|---------|----------|
| Subscribed? | Look for "[BLE] subscribed to notifications" in log | Must appear after connect |
| TX UUID correct? | Compare `6E400003-...` on both sides | Must match exactly |
| ESP32 sending? | Press button, check Serial for "BTN DOWN" | Proves firmware is running |
| Notification handler error? | Check for "[BLE] notification error" in log | Handler might be throwing |

### F4: Notifications received but UI doesn't respond

| Check | Where | Expected |
|-------|-------|----------|
| Queue delivery? | `[BLE] button received: 'U'` in log | Proves data reached the queue |
| Main loop polling? | EasyPlay must be in carousel/playback mode | Setup menu may not poll BLE |
| Key mapping? | `U`→UP, `D`→DOWN, `L`→LEFT, `R`→RIGHT, `O`→ON/OFF | Case matters |

---

## Difference: EasyPlay vs pi-ble-remote receiver

| Aspect | EasyPlay (easyplay56.py) | pi-ble-remote (ble_receiver.py) |
|--------|--------------------------|--------------------------------|
| Scan method | Match by MAC only (stored in config) | Match by name, UUID, or MAC |
| Adapter reset | `hciconfig hci0 reset` | `bluetoothctl power off/on` + nuclear cache clear |
| Cache clear | No explicit cache clear | Nuclear: stop bluetooth → delete cache → restart |
| Connection | Pass BLEDevice object from scan | Pass BLEDevice object from scan |
| Profiles supported | EasyPlay only (NUS) | Both BLE-Remote (custom UUID) and EasyPlay (NUS) |
| Reconnect | Scan → connect loop with adapter reset | Same, plus exponential backoff + nuclear clear after 3 fails |
| Button handling | Queue → pygame key events | Print to stdout |

---

## Diagnostic Test Script

Run this on the Pi to test each phase independently:

```bash
# Phase 0: Hardware
hciconfig hci0
sudo /usr/sbin/rfkill list
systemctl is-active bluetooth

# Phase 1-2: Can the Pi see the remote?
sudo timeout 10 bluetoothctl scan le   # look for "EasyPlay" + MAC

# Phase 2-4: Can bleak connect and subscribe?
python3 -c "
import asyncio
from bleak import BleakClient, BleakScanner

REMOTE_MAC = 'XX:XX:XX:XX:XX:XX'  # <-- fill in
TX_UUID = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'

async def test():
    print('Scanning...')
    device = await BleakScanner.find_device_by_address(REMOTE_MAC, timeout=10)
    if not device:
        print('NOT FOUND'); return
    print(f'Found: {device}')

    async with BleakClient(device, timeout=10) as client:
        print(f'Connected, MTU={client.mtu_size}')
        for svc in client.services:
            for c in svc.characteristics:
                print(f'  {c.uuid} ({", ".join(c.properties)})')

        def on_notify(sender, data):
            print(f'NOTIFY: {data} = {chr(data[0])!r}')

        await client.start_notify(TX_UUID, on_notify)
        print('Subscribed. Press buttons on remote (Ctrl+C to stop)...')
        await asyncio.sleep(60)

asyncio.run(test())
"
```

---

## Architecture Diagram

```
  ┌──────────────────────────┐          BLE (over the air)           ┌─────────────────────────────┐
  │   ESP32-C3 SuperMini     │  ◄──────────────────────────────────► │   Raspberry Pi (LuckyPi)    │
  │                          │                                       │                             │
  │ NimBLE GATT Server       │         advertising (250ms)           │ bleak (asyncio BLE client)  │
  │ ┌──────────────────────┐ │  ─────────────────────────────────►  │                             │
  │ │ NUS Service          │ │                                       │ _ble_uart_thread:           │
  │ │ 6E400001-...         │ │         scan response                │  ┌───────────────────┐     │
  │ │                      │ │  ◄──────────────────────────────────  │  │ BleakScanner      │     │
  │ │ TX: 6E400003-...     │─┼──── notify (1 byte: 'U','d',etc) ──►│  │ (match by MAC)    │     │
  │ │ (READ | NOTIFY)      │ │                                       │  └───────────────────┘     │
  │ │                      │ │                                       │  ┌───────────────────┐     │
  │ │ RX: 6E400002-...     │◄┼──── write (not used yet) ───────────│  │ BleakClient       │     │
  │ │ (WRITE_NR)           │ │                                       │  │ start_notify()    │     │
  │ └──────────────────────┘ │                                       │  └─────────┬─────────┘     │
  │                          │                                       │            │               │
  │ GPIO 0-4 (INPUT_PULLUP)  │                                       │  notification_handler()    │
  │ ┌─┐ ┌─┐ ┌─┐ ┌─┐ ┌─┐    │                                       │            │               │
  │ │L│ │D│ │R│ │U│ │O│    │                                       │  _ble_key_queue.put(char)  │
  │ └─┘ └─┘ └─┘ └─┘ └─┘    │                                       │            │               │
  │                          │                                       │  main loop (pygame):       │
  │ NeoPixel (GPIO 8)        │                                       │    queue.get() → KEYDOWN   │
  │  blue flash = boot       │                                       │    → UI action             │
  │  green = connected       │                                       │                             │
  │  off = disconnected      │                                       │  easyplay_config.json:     │
  └──────────────────────────┘                                       │    bluetooth_remote_addr   │
                                                                     └─────────────────────────────┘
```
