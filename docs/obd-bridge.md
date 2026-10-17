# OBD-II bridge

The bridge reads standard OBD-II data from an ELM327 adapter and forwards readings to the VitaDrive ingest API. It can
also simulate driving without hardware, print JSON for a dry run, or keep using the local database with `--local`.

## Hardware

A USB ELM327 adapter is generally the simplest and most reliable option. Bluetooth adapters are convenient, but need
pairing and a serial-port connection on most computers. Avoid very cheap adapters advertised as **ELM327 v2.1**:
many are incomplete clones that fail to support standard commands or disconnect under load. Check that your adapter
and vehicle support the OBD-II PIDs you need before installing it permanently.

The bridge auto-scans by default. You can select a port explicitly with `--port`:

| Platform | Common port |
|---|---|
| Linux Bluetooth | `/dev/rfcomm0` |
| Linux USB | `/dev/ttyUSB0` |
| macOS Bluetooth | `/dev/tty.OBDII` (check with `ls /dev/tty.*`) |
| Windows | `COM3` (check Device Manager) |

### Pair Bluetooth

On Linux, pair and trust the adapter with `bluetoothctl`:

```text
power on
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

Then bind its serial channel (replace the address with the adapter's address):

```bash
sudo rfcomm bind /dev/rfcomm0 AA:BB:CC:DD:EE:FF 1
```

On macOS, pair through **System Settings → Bluetooth**, then inspect `/dev/tty.*` for the adapter's serial device.
On Windows, pair through **Bluetooth & devices** and use the outgoing COM port shown in Device Manager.

## API key and commands

Register the vehicle in VitaDrive, then use **API key** in the dashboard header to issue a vehicle key. It is shown only
once; store it securely. The bridge accepts `--api-key` or the `VITADRIVE_API_KEY` environment variable. It never logs
the key.

Start with a dry run; this prints one JSON reading per line and makes no network requests:

```bash
pip install 'vitadrive[obd]'
vitadrive bridge <VIN> --simulate --dry-run --interval 5 --count 10
```

Try the end-to-end path with generated driving before connecting hardware:

```bash
export VITADRIVE_API_KEY='paste-the-key-once-from-the-dashboard'
vitadrive bridge <VIN> --simulate --interval 5 --url https://vitadrive.example.com
```

Then start the live adapter, choosing the port if auto-scan does not find it:

```bash
vitadrive bridge <VIN> --port /dev/rfcomm0 --interval 5 --url https://vitadrive.example.com
```

The previous local-database command remains available as `vitadrive obd <VIN>`. It runs the bridge in `--local` mode;
`vitadrive bridge <VIN> --local` is equivalent. Use `--db` before the subcommand to select the local database.

## Offline delivery

Readings are written to a persistent SQLite FIFO before sending. The default file is `~/.vitadrive/bridge-buffer.db`;
choose another location with `--buffer`. On restart, the bridge resumes with the oldest unsent reading and flushes up
to 50 readings per tick. Network errors, server errors and rate limits are retried with exponential backoff; a
`Retry-After` response is honored. Invalid readings are dropped with the server's error message. An invalid key or
unknown VIN stops the bridge so it cannot silently discard the backlog.

The buffer holds up to 10,000 readings. If it fills, the oldest entries are dropped and a warning is logged. Monitor
available disk space and periodically confirm that the bridge can reach the server.

## Raspberry Pi service

Create a root-readable environment file containing the key:

```text
# /etc/vitadrive/bridge.env
VITADRIVE_API_KEY=the-key-from-the-dashboard
```

For example, save this as `/etc/systemd/system/vitadrive-bridge.service` and replace the VIN and URL:

```ini
[Unit]
Description=VitaDrive OBD-II bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
SupplementaryGroups=dialout
EnvironmentFile=/etc/vitadrive/bridge.env
ExecStart=/usr/local/bin/vitadrive bridge <VIN> --port /dev/rfcomm0 --url https://vitadrive.example.com
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Protect the environment file (`chmod 600 /etc/vitadrive/bridge.env`), then enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vitadrive-bridge
sudo journalctl -u vitadrive-bridge -f
```

The default buffer lives in the service user's home directory, so it persists across restarts.

## Available metrics

Standard OBD-II exposes coolant temperature, fuel level, control-module voltage, diagnostic trouble codes (DTCs), and
on some 2019-and-newer vehicles the odometer (PID `0xA6`). If the vehicle does not report the odometer, provide
`--odometer <km>` as a fallback. Fluid levels, brake-pad wear, tire pressure and high-voltage battery data are
manufacturer-specific; use the vehicle's telematics or a CAN gateway for those.

## Troubleshooting

- **The `obd` package is missing:** install the optional extra with `pip install 'vitadrive[obd]'`.
- **No adapter found:** check pairing and permissions, then specify the port with `--port`. On Linux, add the service
  user to `dialout` and re-login; for Bluetooth, recreate the `/dev/rfcomm0` binding.
- **Connection drops:** the bridge reconnects with backoff. Check adapter power, cable/port selection and vehicle
  ignition; avoid pairing the same adapter to another device at the same time.
- **No odometer:** supply `--odometer` or use a vehicle/adapter that exposes PID `0xA6`.
- **401/403/404:** confirm that the API key belongs to the VIN and that the URL points to the correct VitaDrive
  server. Rotate the key in the dashboard if it may have been exposed.
- **Readings are buffered:** restore network access and check the server. The bridge flushes the FIFO automatically
  and honors `Retry-After` on 429 responses.
- **No alerts appear:** the API returns only newly raised alerts. Check the vehicle's latest readings in the dashboard;
  not every supported sensor is available on every car.
