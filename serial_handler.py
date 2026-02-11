import threading
import time
import queue as _queue

try:
    import serial as _serial_mod
except Exception:
    _serial_mod = None

# Ensure `serial` exposes a `Serial` attribute so we can call `serial.Serial`.
# If the top-level package doesn't expose `Serial`, try to import a platform
# specific backend (e.g., serial.serialwin32) and use that. Otherwise fall
# back to a minimal dummy that raises informative errors.
serial = None
SERIAL_CLASS = None
if _serial_mod is None:
    serial = None
else:
    # If the imported package already exposes Serial, use it
    if hasattr(_serial_mod, 'Serial'):
        serial = _serial_mod
        SERIAL_CLASS = getattr(_serial_mod, 'Serial')
    else:
        # Try platform-specific backend modules (win32/posix)
        try:
            import importlib
            backend = importlib.import_module('serial.serialwin32')
            serial = backend
            SERIAL_CLASS = getattr(backend, 'Serial', None)
        except Exception:
            try:
                import importlib
                backend = importlib.import_module('serial.serialposix')
                serial = backend
                SERIAL_CLASS = getattr(backend, 'Serial', None)
            except Exception:
                # Fallback: provide a dummy Serial class that raises ImportError
                class _DummySerialClass:
                    def __init__(self, *args, **kwargs):
                        raise ImportError('pyserial backend not available')

                SERIAL_CLASS = _DummySerialClass

    # Some backend modules (especially when the top-level "serial" is a namespace
    # package) expect attributes like SerialBase or SerialException to be present
    # on the top-level package. If we have a backend and the originally imported
    # package object (`_serial_mod`) exists, attach the commonly used attributes
    # to it so backend code referencing `serial.SerialBase` will find them.
    try:
        import sys as _sys
        if _serial_mod is not None and 'backend' in locals():
            # Copy public attributes from backend to top-level package when missing
            for name in dir(backend):
                if name.startswith('_'):
                    continue
                if not hasattr(_serial_mod, name):
                    try:
                        setattr(_serial_mod, name, getattr(backend, name))
                    except Exception:
                        pass
            # Ensure Serial is present on top-level
            if not hasattr(_serial_mod, 'Serial') and SERIAL_CLASS is not None:
                setattr(_serial_mod, 'Serial', SERIAL_CLASS)
            # Also copy commonly defined utility constants from serial.serialutil
            try:
                import importlib
                util = importlib.import_module('serial.serialutil')
                for name in dir(util):
                    if name.startswith('_'):
                        continue
                    if not hasattr(_serial_mod, name):
                        try:
                            setattr(_serial_mod, name, getattr(util, name))
                        except Exception:
                            pass
            except Exception:
                pass

        # Ensure a set of common constants and exception classes exist on the
        # top-level serial package and on the resolved backend. Some environments
        # install pyserial as a namespace package which can cause attribute lookup
        # failures (e.g., missing FIVEBITS). Provide sensible fallbacks.
        try:
            import importlib
            util = None
            try:
                util = importlib.import_module('serial.serialutil')
            except Exception:
                util = None

            def _ensure(mod, name, val):
                if mod is None:
                    return
                if not hasattr(mod, name):
                    try:
                        setattr(mod, name, val)
                    except Exception:
                        pass

            # Common bit/stop/parity constants
            defaults = {
                'FIVEBITS': 5,
                'SIXBITS': 6,
                'SEVENBITS': 7,
                'EIGHTBITS': 8,
                'STOPBITS_ONE': 1,
                'STOPBITS_ONE_POINT_FIVE': 1.5,
                'STOPBITS_TWO': 2,
                'PARITY_NONE': 'N',
                'PARITY_EVEN': 'E',
                'PARITY_ODD': 'O',
                'PARITY_MARK': 'M',
                'PARITY_SPACE': 'S',
            }

            # If util provides these, prefer util's values
            for k, v in list(defaults.items()):
                if util is not None and hasattr(util, k):
                    try:
                        defaults[k] = getattr(util, k)
                    except Exception:
                        pass

            # Ensure attributes on both the original imported package object and
            # the resolved `serial`/backend we will use.
            targets = []
            if _serial_mod is not None:
                targets.append(_serial_mod)
            if 'serial' in globals() and serial is not None and serial is not _serial_mod:
                targets.append(serial)

            for t in targets:
                for name, val in defaults.items():
                    _ensure(t, name, val)

            # Ensure SerialException and SerialBase exist
            class _LocalSerialException(Exception):
                pass

            for t in targets:
                if not hasattr(t, 'SerialException'):
                    _ensure(t, 'SerialException', getattr(t, 'SerialException', _LocalSerialException))
                if not hasattr(t, 'SerialBase'):
                    # Provide a minimal base class
                    class _SerialBase(object):
                        pass
                    _ensure(t, 'SerialBase', getattr(t, 'SerialBase', _SerialBase))
        except Exception:
            pass
    except Exception:
        pass

BAUD_RATE = 115200
serial_connection = None
log_queue = None
_reader_thread = None
_stop_event = threading.Event()
# Queue used for waiting for ACKs from the device
ack_queue = None
# Simple log level control (DEBUG, INFO, WARN, ERROR)
LOG_LEVEL = 'INFO'


def connect_serial(port, queue):
    """Open the serial port and start a reader thread.

    Returns True on success, False on failure. Places messages on `queue`.
    """
    global serial_connection, log_queue, _reader_thread, _stop_event
    log_queue = queue
    if serial is None:
        if log_queue:
            log_queue.put(('error', 'pyserial is not installed. Install the "pyserial" package.'))
        return False
    try:
        # Use resolved SERIAL_CLASS to avoid issues with namespace packages
        global SERIAL_CLASS
        if SERIAL_CLASS is None:
            # As a last resort try to use attribute on serial module
            SerialClass = getattr(serial, 'Serial', None)
        else:
            SerialClass = SERIAL_CLASS

        if SerialClass is None:
            if log_queue:
                log_queue.put(('error', 'No usable pyserial Serial class found.'))
            return False

        serial_connection = SerialClass(port=port, baudrate=BAUD_RATE, timeout=1)
        if log_queue:
            log_queue.put(('info', f'Serial connection established on {port}.'))
        _stop_event.clear()
        # initialize ack queue used by send_command for synchronous ACKs
        global ack_queue
        ack_queue = _queue.Queue()

        _reader_thread = threading.Thread(target=_read_loop, daemon=True)
        _reader_thread.start()
        return True
    except Exception as e:
        if log_queue:
            log_queue.put(('error', f'Failed to open serial port {port}: {e}'))
        serial_connection = None
        return False


def disconnect_serial():
    """Stop reader and close the serial connection."""
    global serial_connection, _stop_event
    _stop_event.set()
    try:
        if serial_connection and getattr(serial_connection, 'is_open', False):
            serial_connection.close()
    except Exception:
        pass
    serial_connection = None
    global ack_queue
    if ack_queue is not None:
        try:
            # drain ack queue
            while not ack_queue.empty():
                ack_queue.get_nowait()
        except Exception:
            pass
        ack_queue = None


def send_command(command, expect_ack=False, timeout=2.0, retries=1):
    """Send a command string over serial.

    If `expect_ack` is True, this will wait for a line from the device
    (up to `timeout` seconds) and return a tuple (success, message).
    If `expect_ack` is False, returns (True, '') on write success.
    """
    global serial_connection, log_queue, ack_queue
    if not serial_connection or not getattr(serial_connection, 'is_open', False):
        if log_queue:
            log_queue.put(('warn', f"Not connected: cannot send '{command}'"))
        return (False, 'not_connected')

    cmd = (str(command).strip() + '\n').encode('utf-8')
    last_err = None
    for attempt in range(max(1, retries + 1)):
        try:
            serial_connection.write(cmd)
            if not expect_ack:
                return (True, '')

            # Wait for an ACK-like response on the ack_queue (populated by _read_loop)
            if ack_queue is None:
                return (True, '')
            try:
                text = ack_queue.get(timeout=timeout)
                # Normalize
                if isinstance(text, bytes):
                    try:
                        text = text.decode('utf-8', errors='ignore')
                    except Exception:
                        text = str(text)
                text = str(text).strip()
                # Treat lines that start with ERROR as failure
                if text.upper().startswith('ERROR') or text.upper().startswith('ERR'):
                    last_err = text
                    continue
                return (True, text)
            except _queue.Empty:
                last_err = 'ack_timeout'
                continue
        except Exception as e:
            last_err = str(e)
            if log_queue:
                log_queue.put(('error', f'Failed to write to serial port: {e}'))
                log_queue.put(('disconnected_event', f'Serial connection lost: {e}'))
            try:
                if serial_connection and getattr(serial_connection, 'is_open', False):
                    serial_connection.close()
            except Exception:
                pass
            serial_connection = None
            return (False, last_err)

    # If we exited the retry loop without success
    if log_queue:
        log_queue.put(('error', f"send_command failed after retries: {last_err}"))
    return (False, last_err)


def _read_loop():
    """Background thread that reads lines from serial and posts parsed messages."""
    global serial_connection, log_queue, _stop_event
    while not _stop_event.is_set():
        try:
            if not serial_connection or not getattr(serial_connection, 'is_open', False):
                time.sleep(0.1)
                continue
            line = serial_connection.readline()
            if not line:
                continue
            try:
                text = line.decode('utf-8', errors='ignore').strip()
            except Exception:
                text = str(line)

            # Simple parsing for common message types
            if text.startswith('POS:'):
                _, val = text.split(':', 1)
                try:
                    pos = float(val)
                except Exception:
                    pos = val
                if log_queue:
                    log_queue.put(('position_update', pos))
                # Also offer raw line for ack readers
                if ack_queue:
                    ack_queue.put(text)
            elif text.startswith('SENSOR:') or text.startswith('SENSOR_STATE:'):
                parts = text.split(':')
                # Support both 'SENSOR:<pin>:<state>' and 'SENSOR_STATE:<pin>:<state>'
                if len(parts) >= 3:
                    # For SENSOR_STATE the first token is 'SENSOR_STATE'
                    # For SENSOR it's 'SENSOR' — pin is at index 1 in both cases
                    _, pin, state = parts[:3]
                    if log_queue:
                        log_queue.put(('sensor_update', (pin, state)))
                if ack_queue:
                    ack_queue.put(text)
            elif text.startswith('ANGLE:'):
                _, val = text.split(':', 1)
                try:
                    angle = float(val)
                except Exception:
                    angle = 0.0
                if log_queue:
                    log_queue.put(('angle_update', angle))
                if ack_queue:
                    ack_queue.put(text)
            else:
                if log_queue:
                    log_queue.put(('info', text))
                if ack_queue:
                    ack_queue.put(text)
        except Exception as e:
            if log_queue:
                log_queue.put(('error', f'Serial read error: {e}'))
                log_queue.put(('disconnected_event', f'Serial read error: {e}'))
            try:
                if serial_connection and getattr(serial_connection, 'is_open', False):
                    serial_connection.close()
            except Exception:
                pass
            serial_connection = None
            break
