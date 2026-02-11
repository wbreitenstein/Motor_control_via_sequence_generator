Motors UNO R3 — README

Quick summary
- This folder is a cleaned, R3-specific workspace.
- Firmware: sketch_jan13a.ino (configured for UNO R3, 115200 baud, EEPROM calibration, PIN_TEST, MOVE_DONE).
- App: `main_app.py` uses `serial_handler.py` / `sequence_controller.py` to control the device.

Requirements
- Arduino IDE (recommended) or VS Code + Arduino extension / PlatformIO for compiling/uploading the sketch.
- Python 3.8+ (for the GUI) and `pyserial`.

Notes about toolchain
- The Arduino IDE bundles the AVR toolchain necessary to compile and upload sketches for the UNO.
- If you prefer VS Code, install either the "Arduino" extension (by Microsoft) or "PlatformIO" — both provide the required toolchain integration.

Quick setup (Python GUI)
1. Create and activate a venv in this folder (optional but recommended):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Run the GUI (it will scan for COM ports):

```powershell
python main_app.py
```

Firmware upload (Arduino IDE)
1. Open `motors-uno-r3/sketch_jan13a.ino` in Arduino IDE.
2. Tools -> Board -> select "Arduino Uno".
3. Tools -> Port -> select the COM port attached to your Uno.
4. Upload (click the Upload arrow).

Firmware upload (VS Code + Arduino extension)
1. Install the Arduino extension and configure the board/port in the extension.
2. Open `sketch_jan13a.ino` and use the extension's upload command.

Serial settings
- Baud: 115200 (sketch prints `STATUS:Arduino Ready. ...` on startup).
- Line ending: LF (\n) — the GUI sends commands terminated with newline automatically.

How to test PIN_TEST (safe motor-pin probe)
1. Open the GUI or a serial terminal (baud 115200).
2. With the motor drivers disconnected or motor EN pins disabled, send:

```
PIN_TEST
```

3. Expected serial response: `PIN_TEST:OK` and short pulses will be applied to direction pins (IN1/IN2 and IN3/IN4) for observation with a logic probe. ENA/ENB are forced low during the test so motors should not be powered.

Calibration (EEPROM-backed)
- Read current values: send `GET_CAL` → device responds like `CAL:MS_PER_MM:50:MS_PER_DEG:10`.
- Set a value: `SET_CAL:MS_PER_MM:60` (or `SET_CAL:MS_PER_DEG:12`) → device replies `SET_CAL:...` and stores it to EEPROM.

Movement completion events
- The firmware will print `MOVE_DONE:<id>` after timed moves, distance moves, and turns. The GUI listens for these and displays completion messages.

Useful direct commands (available in GUI template dropdown)
- `MOTOR_CMD:0:FWD`, `MOTOR_CMD:0:REV`, `MOTOR_CMD:0:STOP`, `MOTOR_CMD:0:HOME`
- `MOVE_TIME:0:FWD:1000`
- `MOVE_DIST:0:10` (distance in mm)
- `TURN:0:45` (degrees)
- `TEST_MOTOR:0`
- `PIN_TEST`
- `GET_CAL`, `SET_CAL:MS_PER_MM:50`

Troubleshooting
- If the GUI cannot open the COM port: close other serial monitors (Arduino IDE Serial Monitor, other apps) and try again.
- If commands appear in the serial monitor but motors do not move while `PIN_TEST` shows pulses on direction pins, check motor driver enable (ENA/ENB) wiring and the power supply to the motor driver.
- If upload fails in VS Code, install the Arduino IDE and point the extension to the Arduino CLI/toolchain, or use PlatformIO which bundles toolchains.

What I changed in the firmware
- Baud set to 115200.
- EEPROM-backed calibration values and `GET_CAL`/`SET_CAL` commands.
- `PIN_TEST` command added for safe pin verification.
- `MOVE_DONE:<id>` printed after moves.
- Serial input reading made robust (no dependence on `serialEvent`).
- Debug prints for `runMotor`/`stopMotor` to aid logic-probing.

Next recommended step
- Upload `sketch_jan13a.ino` to your UNO R3, open the GUI, connect, and run `PIN_TEST`. Paste the last ~30 serial lines and any logic-probe observations if anything looks wrong.

File references
- Firmware: [motors-uno-r3/sketch_jan13a.ino](motors-uno-r3/sketch_jan13a.ino)
- GUI: [motors-uno-r3/main_app.py](motors-uno-r3/main_app.py)
- Serial layer: [motors-uno-r3/serial_handler.py](motors-uno-r3/serial_handler.py)
- Sequencer: [motors-uno-r3/sequence_controller.py](motors-uno-r3/sequence_controller.py)
