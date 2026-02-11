# sequence_controller.py

import time
import sys
import importlib
import queue as _queue


def _get_serial_handler():
    """Return the `serial_handler` module if available, otherwise None.

    This first checks `sys.modules` so that if the main application
    dynamically loaded `serial_handler` it will be returned here.
    """
    module = sys.modules.get('serial_handler')
    if module:
        return module
    try:
        return importlib.import_module('serial_handler')
    except Exception:
        return None


def _send(command, log_queue=None):
    """Send a command using the serial handler, returning True on success.

    If the serial handler is not available or an exception occurs, log
    an appropriate message (if `log_queue` provided) and return False.
    """
    sh = _get_serial_handler()
    if not sh:
        if log_queue:
            log_queue.put(('error', f"No serial handler available to send: {command}"))
        return False
    try:
        # Use getattr to avoid AttributeError if send_command isn't present
        send_fn = getattr(sh, 'send_command', None)
        if not callable(send_fn):
            if log_queue:
                log_queue.put(('error', f"serial_handler has no callable 'send_command' to send: {command}"))
            return False

        # Decide if we should expect an ACK for critical/movement commands
        expect_ack = False
        cmd_name = str(command).split(':', 1)[0]
        if cmd_name in ('MOTOR_CMD', 'MOVE_TIME', 'MOVE_DIST', 'TURN', 'SET_CAL', 'STOP_ALL'):
            expect_ack = True

        result = send_fn(command, expect_ack=expect_ack, timeout=3.0, retries=1)
        # send_command now returns (success, message)
        if isinstance(result, tuple):
            success, msg = result
            if not success:
                if log_queue:
                    log_queue.put(('error', f"Failed to send '{command}': {msg}"))
                return False
            else:
                if log_queue and msg:
                    log_queue.put(('debug', f"ACK: {msg}"))
                return True
        else:
            # backwards compatibility: boolean
            return bool(result)
    except Exception as e:
        if log_queue:
            log_queue.put(('error', f"Exception while sending '{command}': {e}"))
        return False

def home_motor(log_queue):
    """Sends the 'MOTOR_CMD:0:HOME' command to the Arduino."""
    log_queue.put(('info', "Requesting homing sequence for motor 0..."))
    if not _send("MOTOR_CMD:0:HOME", log_queue):
        log_queue.put(('error', "Failed to send HOME command."))

def run_motor(log_queue, direction):
    """Sends commands to run the default motor (ID 0) forward or reverse."""
    if direction == "forward":
        command = "MOTOR_CMD:0:FWD"
        msg = "Running motor 0 forward..."
    elif direction == "reverse":
        command = "MOTOR_CMD:0:REV"
        msg = "Running motor 0 in reverse..."
    else:
        log_queue.put(('error', f"Invalid motor direction: {direction}"))
        return

    log_queue.put(('info', msg))
    if not _send(command, log_queue):
        log_queue.put(('error', f"Failed to send {command} command."))

def stop_motor(log_queue):
    """Sends the 'MOTOR_CMD:0:STOP' command to the Arduino."""
    log_queue.put(('info', "Stopping motor 0..."))
    if not _send("MOTOR_CMD:0:STOP", log_queue):
        log_queue.put(('error', "Failed to send STOP command."))

def sun_tracking_loop(log_queue, stop_event):
    """
    Main loop for automatic sun tracking.
    This function is intended to be run in a background thread.
    It periodically tells the Arduino to perform a tracking adjustment.
    The Arduino is expected to handle the sensor reading and motor movement.
    """
    log_queue.put(('info', "Sun tracking sequence started."))

    # This loop just pokes the Arduino periodically to trigger a check/adjustment.
    while not stop_event.is_set():
        if not _send("TRACK_SUN", log_queue):
            log_queue.put(('error', "Failed to send TRACK_SUN command. Stopping sequence."))
            break  # Exit loop if command fails
        # Wait 10 seconds before the next tracking adjustment, but check for stop signal every second
        for _ in range(10):
            if stop_event.is_set(): break
            time.sleep(1)

    log_queue.put(('info', "Sun tracking sequence stopped."))

def run_custom_sequence(log_queue, command_list, sensor_states, settings=None):
    """
    Executes a list of commands in order, handling loops and waits.
    This now acts as a simple interpreter for the command list.
    """
    log_queue.put(('info', "Starting custom sequence..."))
    log_queue.put(('info', f"Initial sensor states: {sensor_states}"))

    pc = 0  # Program Counter
    loop_stack = []  # Stores tuples of (loop_start_index, iterations_left)
    variables = {} # Dictionary to store user-defined variables

    def _resolve_value(val_str):
        """If a value starts with $, resolve it from the variables dict."""
        if val_str.startswith('$'):
            var_name = val_str[1:]
            if var_name in variables:
                return str(variables[var_name])
            else:
                log_queue.put(('error', f"Variable '{var_name}' not found."))
                return None # Signal an error
        return val_str

    def _find_matching_endif(start_pc):
        """Find the corresponding ENDIF for an IF, respecting nested IFs."""
        if_level = 1
        search_pc = start_pc + 1
        while search_pc < len(command_list):
            cmd = command_list[search_pc]
            if cmd.startswith("IF_"):
                if_level += 1
            elif cmd == "ENDIF":
                if_level -= 1
                if if_level == 0:
                    return search_pc
            search_pc += 1
        return -1 # Not found

    while pc < len(command_list):
        command = command_list[pc]

        # --- Pre-execution processing ---
        # Skip comments
        if command.startswith('#'):
            pc += 1
            continue

        # Resolve variables in the command string itself
        parts = command.split(':')
        try:
            resolved_parts = [_resolve_value(p) for p in parts]
            if any(p is None for p in resolved_parts): # Check for resolution errors
                log_queue.put(('error', f"Halting due to unresolved variable in command: {command}"))
                break
            print(f"DEBUG: resolved_parts = {resolved_parts}")
            command = ":".join(resolved_parts)
        except Exception as e:
            log_queue.put(('error', f"Failed to resolve variables in '{command}': {e}"))
            break

        log_queue.put(('info', f"Executing [PC:{pc}]: {command}"))

        # --- Command Interpretation ---
        if command.startswith("SET_VAR:"):
            try:
                _, var_name, value = command.split(':', 2)
                variables[var_name] = value
                log_queue.put(('info', f"Set variable ${var_name} = {value}"))
            except ValueError:
                log_queue.put(('error', f"Invalid SET_VAR format: {command}. Halting sequence."))
                break

        elif command.startswith("IF_SENSOR:"):
            try:
                _, pin, expected_state_str = command.split(':')
                expected_state = '1' if expected_state_str.upper() == 'HIGH' else '0'
                
                # Check the last known state from the dictionary
                current_state = sensor_states.get(pin, '0') # Default to '0' if not seen
                condition_met = (current_state == expected_state)

                log_queue.put(('info', f"IF condition: Pin {pin} state is '{current_state}', expected '{expected_state}'. Condition is {condition_met}."))

                if not condition_met:
                    endif_pc = _find_matching_endif(pc)
                    if endif_pc == -1:
                        log_queue.put(('error', f"Mismatched IF at PC {pc} has no ENDIF. Halting."))
                        break
                    # Jump program counter to the ENDIF command, the loop will increment it past it
                    pc = endif_pc
            except ValueError:
                log_queue.put(('error', f"Invalid IF_SENSOR format: {command}. Halting sequence."))
                break

        elif command == "ENDIF":
            pass # This is just a marker, do nothing.

        elif command.startswith("LOOP_START:"):
            try:
                # We subtract 1 because the first pass counts as an iteration.
                iterations = int(command.split(':')[1]) - 1
                if iterations < 0: iterations = 0 # Ensure it's not negative
                loop_stack.append((pc, iterations))
            except (ValueError, IndexError):
                log_queue.put(('error', f"Invalid LOOP_START format: {command}. Halting sequence."))
                break

        elif command == "LOOP_END":
            if not loop_stack:
                log_queue.put(('error', "Mismatched LOOP_END found. Halting sequence."))
                break
            
            start_index, iterations_left = loop_stack[-1]  # Peek at the top
            
            if iterations_left > 0:
                loop_stack[-1] = (start_index, iterations_left - 1)  # Update count
                pc = start_index  # Jump back to the LOOP_START command
            else:
                loop_stack.pop()  # Loop is finished, pop it from the stack
        
        elif command.startswith("WAIT:"):
            try:
                duration = float(command.split(':')[1])
                log_queue.put(('info', f"Waiting for {duration} second(s)..."))
                time.sleep(duration)
            except (ValueError, IndexError):
                log_queue.put(('error', f"Invalid WAIT format: {command}. Halting sequence."))
                break
        else:  # It's a serial command for the Arduino
            # If this looks like a movement command, add stall/completion detection
            is_movement = False
            if command.startswith(('MOVE_DIST:', 'MOVE_TIME:', 'TURN:', 'MOTOR_CMD:')):
                is_movement = True

            if not _send(command, log_queue):
                log_queue.put(('error', f"Failed to send command '{command}'. Halting sequence."))
                break

            if is_movement:
                # Wait for movement evidence: the device posts 'position_update' messages
                last_update = None
                saw_update = False
                    # Allow settings to override timing behavior
                movement_timeout = 120.0
                stall_window = 3.0
                initial_wait = 5.0
                if settings and isinstance(settings, dict):
                        movement_timeout = float(settings.get('movement_timeout', movement_timeout))
                        stall_window = float(settings.get('stall_window', stall_window))
                        initial_wait = float(settings.get('initial_wait', initial_wait))

                overall_deadline = time.time() + movement_timeout
                buffered = []
                # First wait for an initial position update within initial_wait
                start_t = time.time()
                while time.time() - start_t < initial_wait:
                    try:
                        item = log_queue.get(timeout=initial_wait - (time.time() - start_t))
                    except _queue.Empty:
                        break
                    # If it's a position update, note it
                    if item[0] == 'position_update':
                        saw_update = True
                        last_update = time.time()
                    else:
                        buffered.append(item)

                    if not saw_update:
                        log_queue.put(('error', f"No movement updates observed after sending '{command}'. Possible stall."))
                        # re-queue buffered messages
                        for b in buffered:
                            log_queue.put(b)
                        break

                    # If we got updates, wait until updates stop for stall_window to consider movement complete
                    while time.time() < overall_deadline:
                        timeout = max(0.1, stall_window - (time.time() - last_update))
                        try:
                            item = log_queue.get(timeout=timeout)
                        except _queue.Empty:
                            # No new items; check if last_update is older than stall_window
                            if time.time() - last_update >= stall_window:
                                # Movement likely completed
                                log_queue.put(('info', f"Movement for '{command}' appears complete."))
                                break
                            else:
                                continue

                        if item[0] == 'position_update':
                            saw_update = True
                            last_update = time.time()
                        else:
                            buffered.append(item)

                    else:
                        log_queue.put(('error', f"Movement timeout for '{command}'. Possible stall."))
                        for b in buffered:
                            log_queue.put(b)
                        break

                    # restore buffered messages back into the queue in order
                    for b in buffered:
                        log_queue.put(b)
        
        pc += 1  # Move to the next command

    else:  # This 'else' belongs to the 'while' loop, runs if it finishes without a 'break'
        if loop_stack:
            log_queue.put(('warn', "Sequence finished, but some loops were not closed with LOOP_END."))
        else:
            log_queue.put(('info', "Custom sequence finished successfully."))
