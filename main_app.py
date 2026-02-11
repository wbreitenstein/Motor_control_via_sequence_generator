import tkinter as tk
from tkinter import scrolledtext, messagebox, simpledialog, filedialog
import queue
import json
import threading
import time
import os
import sys
import importlib.util
import importlib

from sympy import true
import sequence_controller

# Try normal import first, fall back to loading serial_handler.py from the same directory
try:
    import serial_handler
except ImportError:
    module_path = os.path.join(os.path.dirname(__file__), "serial_handler.py")
    if os.path.isfile(module_path):
        spec = importlib.util.spec_from_file_location("serial_handler", module_path)
        serial_handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serial_handler)
        sys.modules["serial_handler"] = serial_handler
    else:
        serial_handler = None

try:
    from serial.tools import list_ports  # Corrected import statement
except Exception:
    list_ports = None




class CustomInputDialog(tk.Toplevel):
    """
    A custom dialog to get a string input from the user.
    This is more robust for ensuring the entry widget gets focus.
    """
    def __init__(self, parent, title=None, prompt=None):
        super().__init__(parent)
        self.transient(parent)
        self.parent = parent
        self.result = None

        if title:
            self.title(title)

        body_frame = tk.Frame(self)
        self.initial_focus = self.create_body(body_frame, prompt)
        body_frame.pack(padx=15, pady=15)

        self.create_buttonbox()

        self.grab_set() # Make the window modal

        self.protocol("WM_DELETE_WINDOW", self.cancel)

        # Center the dialog relative to the parent window
        self.geometry(f"+{parent.winfo_rootx()+50}+{parent.winfo_rooty()+50}")

        self.initial_focus.focus_set() # Explicitly set focus

        self.wait_window(self) # Wait until the dialog is destroyed

    def create_body(self, master, prompt):
        if prompt:
            tk.Label(master, text=prompt, justify=tk.LEFT).pack(pady=(0, 10))
        self.entry = tk.Entry(master, width=40)
        self.entry.pack()
        self.entry.bind("<Return>", self.ok)
        self.entry.bind("<Escape>", self.cancel)
        return self.entry

    def create_buttonbox(self):
        box = tk.Frame(self)
        tk.Button(box, text="OK", width=10, command=self.ok, default=tk.ACTIVE).pack(side=tk.LEFT, padx=5, pady=5)
        tk.Button(box, text="Cancel", width=10, command=self.cancel).pack(side=tk.LEFT, padx=5, pady=5)
        box.pack()

    def ok(self, event=None):
        self.result = self.entry.get()
        self.parent.focus_set()
        self.destroy()

    def cancel(self, event=None):
        self.parent.focus_set()
        self.destroy()

class MotorControlGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DC Motor Control")
        self.log_queue = queue.Queue()
        self.current_position = tk.StringVar(value="Position: N/A") # Variable to hold and display position
        self.current_angle = tk.StringVar(value="Angle: N/A")      # Variable for sun tracking angle
        self.tracking_active = threading.Event()
        self.tracking_thread = None
        self.show_manual_controls_var = tk.BooleanVar(value=True)
        self.show_comport_buttons_var = tk.BooleanVar(value=True)
        self.show_sequence_builder_var = tk.BooleanVar(value=False)
        self.sequence_thread = None
        self.motor_definitions = { "0": "Default Motor (0)", "1": "Auxiliary Motor (1)" }
        self.sensor_states = {} # Dictionary to hold the last known state of sensors
        # Load application settings (persisted)
        self.settings_file = os.path.join(os.path.dirname(__file__), 'settings.json')
        self.settings = self.load_settings()
        self.debug_log_fh = None
        # Background debug writer queue and thread
        self.debug_write_queue = queue.Queue(maxsize=1000)
        self._debug_writer_stop = threading.Event()
        self.debug_writer_thread = threading.Thread(target=self._debug_writer_loop, daemon=True)
        self.debug_writer_thread.start()

        # --- Create Menu Bar ---
        self.menubar = tk.Menu(self.root)
        self.root.config(menu=self.menubar)

        # File Menu
        file_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Connect", command=self.connect)
        file_menu.add_command(label="Disconnect", command=self.disconnect)
        file_menu.add_separator()
        file_menu.add_command(label="Load Sequence...", command=self.load_sequence)
        file_menu.add_command(label="Save Sequence As...", command=self.save_sequence)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.exit_app)

        # Controls Menu
        self.controls_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Controls", menu=self.controls_menu)

        # Sub-menu for Manual Controls
        self.manual_control_menu = tk.Menu(self.controls_menu, tearoff=0)
        self.controls_menu.add_cascade(label="Manual Control", menu=self.manual_control_menu)
        self.manual_control_menu.add_command(label="Home Motor", command=self.home_motor)
        self.manual_control_menu.add_command(label="Run Forward", command=self.run_forward)
        self.manual_control_menu.add_command(label="Run Reverse", command=self.run_reverse)

        self.controls_menu.add_command(label="Stop Motor", command=self.stop_motor)
        self.controls_menu.add_separator()
        self.controls_menu.add_command(label="Calibration...", command=self.show_calibration_dialog)
        self.controls_menu.add_separator()
        self.controls_menu.add_command(label="Start/Stop Sun Tracking", command=self.toggle_sun_tracking)
        self.controls_menu.add_separator()
        self.controls_menu.add_checkbutton(
            label="Show Custom Sequencer",
            onvalue=True, offvalue=False,
            variable=self.show_sequence_builder_var,
            command=self.toggle_sequence_builder_view
        )

        # View Menu
        view_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_checkbutton(
            label="Show Manual Control Buttons",
            onvalue=True, offvalue=False,
            variable=self.show_manual_controls_var,
            command=self.toggle_manual_controls_view
        )
        view_menu.add_checkbutton(
            label="Show COM Port Controls",
            onvalue=True, offvalue=False,
            variable=self.show_comport_buttons_var,
            command=self.toggle_comport_view
        )

        # Settings Menu
        settings_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Motor Configuration...", command=self.show_motor_config_dialog)
        settings_menu.add_command(label="Log Level...", command=self.set_log_level_dialog)
        settings_menu.add_command(label="Toggle Sensor Reports", command=self.toggle_sensor_reporting)
        settings_menu.add_separator()
        settings_menu.add_command(label="App Settings...", command=self.show_app_settings)
        settings_menu.add_command(label="Reset Settings", command=self.reset_settings)
        settings_menu.add_command(label="Clear Console", command=self.clear_console)

        # Status Menu
        status_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Status", menu=status_menu)
        status_menu.add_command(label="Get Calibration", command=self.get_device_calibration)
        status_menu.add_command(label="Run Diagnostics (PIN_TEST)", command=self.run_pin_test)

        # Help Menu
        help_menu = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Function Guide", command=self.show_functions_dialog)
        help_menu.add_separator()
        help_menu.add_command(label="About", command=self.show_about_dialog)

        # --- Create Frames ---
        self.connection_frame = tk.Frame(root, padx=10, pady=5, relief=tk.GROOVE, borderwidth=2)
        self.connection_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        self.control_frame = tk.Frame(root, padx=10, pady=10)
        self.control_frame.pack(side=tk.TOP, fill=tk.X)

        status_frame = tk.Frame(root, padx=10, pady=5)
        status_frame.pack(side=tk.TOP, fill=tk.X)

        log_frame = tk.Frame(root, padx=10, pady=10)
        log_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)

        # This frame is packed last to appear just above the log_frame
        self.sequence_builder_frame = tk.Frame(root, padx=10, pady=5, relief=tk.GROOVE, borderwidth=2)
        # It will be packed/unpacked by its toggle function

        # --- Custom Sequence Widgets (inside the builder frame) ---
        seq_display_frame = tk.Frame(self.sequence_builder_frame)
        seq_display_frame.pack(fill=tk.X, pady=(0, 5))
        tk.Label(seq_display_frame, text="Sequence:").pack(side=tk.LEFT)
        self.sequence_text = scrolledtext.ScrolledText(seq_display_frame, height=8, wrap=tk.WORD)
        self.sequence_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # --- Command Button Frames ---
        seq_cmd_container = tk.Frame(self.sequence_builder_frame)
        seq_cmd_container.pack(fill=tk.X, pady=2)

        # Basic Commands (using a LabelFrame for grouping)
        basic_cmd_frame = tk.LabelFrame(seq_cmd_container, text="Default Motor (ID 0)", padx=5, pady=5)
        basic_cmd_frame.pack(fill=tk.X, pady=2)
        tk.Button(basic_cmd_frame, text="Run FWD", command=lambda: self.add_to_sequence("MOTOR_CMD:0:FWD")).pack(side=tk.LEFT)
        tk.Button(basic_cmd_frame, text="Run REV", command=lambda: self.add_to_sequence("MOTOR_CMD:0:REV")).pack(side=tk.LEFT)
        tk.Button(basic_cmd_frame, text="STOP", command=lambda: self.add_to_sequence("MOTOR_CMD:0:STOP")).pack(side=tk.LEFT, padx=5)
        tk.Button(basic_cmd_frame, text="HOME", command=lambda: self.add_to_sequence("MOTOR_CMD:0:HOME")).pack(side=tk.LEFT)

        # Multi-Motor & I/O Commands
        advanced_cmd_frame = tk.LabelFrame(seq_cmd_container, text="Multi-Motor & I/O", padx=5, pady=5)
        advanced_cmd_frame.pack(fill=tk.X, pady=2)
        tk.Button(advanced_cmd_frame, text="Motor Cmd", command=self.add_motor_command).pack(side=tk.LEFT)
        tk.Button(advanced_cmd_frame, text="Set Speed", command=self.add_set_speed).pack(side=tk.LEFT, padx=5)
        tk.Button(advanced_cmd_frame, text="Set Output", command=self.add_set_output).pack(side=tk.LEFT)

        # Precise Movement
        precise_cmd_frame = tk.LabelFrame(seq_cmd_container, text="Precise Movement", padx=5, pady=5)
        precise_cmd_frame.pack(fill=tk.X, pady=2)
        tk.Button(precise_cmd_frame, text="Move Time", command=self.add_timed_move).pack(side=tk.LEFT)
        tk.Button(precise_cmd_frame, text="Move Dist", command=self.add_move_distance).pack(side=tk.LEFT, padx=5)
        tk.Button(precise_cmd_frame, text="Turn Deg", command=self.add_turn_degrees).pack(side=tk.LEFT)

        # Looping & Logic
        loop_cmd_frame = tk.LabelFrame(seq_cmd_container, text="Logic & Flow Control", padx=5, pady=5)
        loop_cmd_frame.pack(fill=tk.X, pady=2)
        tk.Button(loop_cmd_frame, text="Start Loop", command=self.add_loop_start).pack(side=tk.LEFT)
        tk.Button(loop_cmd_frame, text="End Loop", command=lambda: self.add_to_sequence("LOOP_END")).pack(side=tk.LEFT, padx=5)
        tk.Button(loop_cmd_frame, text="Wait", command=self.add_custom_wait).pack(side=tk.LEFT)
        tk.Button(loop_cmd_frame, text="IF Sensor", command=self.add_if_sensor).pack(side=tk.LEFT, padx=5)
        tk.Button(loop_cmd_frame, text="ENDIF", command=lambda: self.add_to_sequence("ENDIF")).pack(side=tk.LEFT)
        tk.Button(loop_cmd_frame, text="Set Var", command=self.add_set_variable).pack(side=tk.LEFT, padx=5)
        tk.Button(loop_cmd_frame, text="Comment", command=self.add_comment).pack(side=tk.LEFT)

        # --- Sequence Action Frame ---
        seq_action_frame = tk.Frame(self.sequence_builder_frame)
        seq_action_frame.pack(fill=tk.X, pady=(5, 0))
        self.run_sequence_button = tk.Button(seq_action_frame, text="Run Sequence", command=self.execute_sequence)
        self.run_sequence_button.pack(side=tk.LEFT)
        tk.Button(seq_action_frame, text="Clear", command=self.clear_sequence).pack(side=tk.LEFT, padx=5)


        # --- Connection Widgets ---
        conn_left_frame = tk.Frame(self.connection_frame)
        conn_left_frame.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(conn_left_frame, text="COM Port:").pack(side=tk.LEFT, padx=(5, 0))
        self.port_variable = tk.StringVar(root)
        self.port_options = ["-"] # Default/placeholder
        self.port_dropdown = tk.OptionMenu(conn_left_frame, self.port_variable, *self.port_options)
        self.port_dropdown.pack(side=tk.LEFT, padx=5)

        self.connect_button = tk.Button(conn_left_frame, text="Connect", command=self.connect)
        self.connect_button.pack(side=tk.LEFT, padx=5)

        self.disconnect_button = tk.Button(conn_left_frame, text="Disconnect", command=self.disconnect, state=tk.DISABLED)
        self.disconnect_button.pack(side=tk.LEFT, padx=5)

        self.refresh_ports_button = tk.Button(conn_left_frame, text="Refresh", command=self.populate_com_ports)
        self.refresh_ports_button.pack(side=tk.LEFT, padx=5)

        # --- Direct Command Line ---
        direct_cmd_frame = tk.Frame(self.connection_frame)
        direct_cmd_frame.pack(side=tk.RIGHT, padx=10, fill=tk.X, expand=True)
        tk.Label(direct_cmd_frame, text="Direct Command:").pack(side=tk.LEFT)
        self.direct_command_var = tk.StringVar()
        self.direct_cmd_entry = tk.Entry(direct_cmd_frame, textvariable=self.direct_command_var)
        self.direct_cmd_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.direct_cmd_entry.bind("<Return>", self.send_direct_command)
        # Dropdown with common command templates
        self.command_templates = [
            "MOTOR_CMD:0:FWD",
            "MOTOR_CMD:0:REV",
            "MOTOR_CMD:0:STOP",
            "MOTOR_CMD:0:HOME",
            "MOVE_TIME:0:FWD:1000",
            "MOVE_DIST:0:10",
            "TURN:0:45",
            "TEST_MOTOR:0",
            "PIN_TEST",
            "SENSOR_REPORT:ON",
            "SENSOR_REPORT:OFF",
            "GET_CAL",
            "SET_CAL:MS_PER_MM:50",
        ]
        self.selected_template = tk.StringVar(value=self.command_templates[0])
        self.template_dropdown = tk.OptionMenu(direct_cmd_frame, self.selected_template, *self.command_templates)
        self.template_dropdown.pack(side=tk.LEFT, padx=(5,0))

        self.insert_template_button = tk.Button(direct_cmd_frame, text="Insert", command=self.insert_selected_template)
        self.insert_template_button.pack(side=tk.LEFT, padx=(5,0))

        self.send_direct_cmd_button = tk.Button(direct_cmd_frame, text="Send", command=self.send_direct_command)
        self.send_direct_cmd_button.pack(side=tk.LEFT)

        # --- Control Widgets ---
        self.home_button = tk.Button(self.control_frame, text="Home Motor", command=self.home_motor)
        self.home_button.pack(side=tk.LEFT, padx=5)

        self.run_fwd_button = tk.Button(self.control_frame, text="Run Forward", command=self.run_forward)
        self.run_fwd_button.pack(side=tk.LEFT, padx=5)

        self.run_rev_button = tk.Button(self.control_frame, text="Run Reverse", command=self.run_reverse)
        self.run_rev_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = tk.Button(self.control_frame, text="Stop Motor", command=self.stop_motor)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # Add a separator and the new tracking button
        separator = tk.Frame(self.control_frame, height=20, width=10) # Visual spacer
        separator.pack(side=tk.LEFT)
        self.track_button = tk.Button(self.control_frame, text="Start Sun Tracking", command=self.toggle_sun_tracking)
        self.track_button.pack(side=tk.LEFT, padx=5)

        # --- Status Widgets ---
        self.position_label = tk.Label(status_frame, textvariable=self.current_position, font=('Arial', 12))
        self.position_label.pack(side=tk.LEFT, padx=5)
        self.angle_label = tk.Label(status_frame, textvariable=self.current_angle, font=('Arial', 12))
        self.angle_label.pack(side=tk.LEFT, padx=5)

        # --- Log Widget ---
        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', wrap=tk.WORD, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # --- App Logic ---
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.populate_com_ports() # Populate ports on startup
        self.disable_controls() # Start with all controls disabled
        self.process_log_queue() # Start processing log messages

    def populate_com_ports(self):
        """Scans for available COM ports and updates the dropdown menu."""
        from serial.tools.list_ports_windows import comports as comports

        ports = comports()
        self.port_options = [port.device for port in ports]
        menu = self.port_dropdown["menu"]
        menu.delete(0, "end")
        if self.port_options:
            for port_name in self.port_options:
                menu.add_command(label=port_name, command=lambda value=port_name: self.port_variable.set(value))
            self.port_variable.set(self.port_options[0]) # Set default selected port
            self.connect_button.config(state=tk.NORMAL)
        else:
            self.port_variable.set("No Ports Found")
            self.connect_button.config(state=tk.DISABLED)

    def connect(self):
        selected_port = self.port_variable.get()
        if not selected_port or "No Ports" in selected_port:
            self.log_queue.put(('error', "No valid COM port selected."))
            return

        if serial_handler.connect_serial(selected_port, self.log_queue):
            self.enable_controls()
            self.connect_button.config(state=tk.DISABLED)
            self.disconnect_button.config(state=tk.NORMAL)
            self.refresh_ports_button.config(state=tk.DISABLED)
            self.port_dropdown.config(state=tk.DISABLED)
            # Apply persisted settings to device (e.g., sensor reporting)
            try:
                self.apply_settings_to_device()
            except Exception:
                pass
        else:
            # Error message is already put on the queue by serial_handler
            self.disable_controls()

    def disconnect(self):
        """User-initiated disconnection from the serial port."""
        serial_handler.disconnect_serial()
        self.handle_disconnect_event() # GUI updates are handled here

    def process_log_queue(self):
        """Checks the queue for messages and updates the GUI."""
        try:
            while True:
                level, message = self.log_queue.get_nowait() # Get all available messages
                if level == 'disconnected_event':
                    self.log_queue.put(('error', message)) # Log the message
                    self.handle_disconnect_event()
                elif level == 'position_update':
                    self.current_position.set(f"Position: {message}")
                elif level == 'sensor_update':
                    pin, state = message
                    self.sensor_states[str(pin)] = str(state) # Store sensor state
                elif level == 'angle_update':
                    self.current_angle.set(f"Angle: {message:.1f}°") # Format to one decimal place
                else:
                    # Special handling for MOVE_DONE messages sent by firmware
                    try:
                        if level == 'info' and isinstance(message, str) and message.startswith('MOVE_DONE:'):
                            mid = message.split(':', 1)[1]
                            self.log_text.configure(state='normal')
                            self.log_text.insert(tk.END, f"[INFO] Movement complete for motor {mid}\n")
                            self.log_text.configure(state='disabled')
                            self.log_text.see(tk.END)
                            # Optionally, show a non-modal popup for completion
                            # messagebox.showinfo("Movement Complete", f"Motor {mid} movement complete.", parent=self.root)
                            continue
                    except Exception:
                        pass

                    self.log_text.configure(state='normal')
                    self.log_text.insert(tk.END, f"[{level.upper()}] {message}\n")
                    self.log_text.configure(state='disabled')
                    self.log_text.see(tk.END) # Auto-scroll
                    # Write to debug log file if enabled
                    try:
                        if self.settings.get('debug_log_enabled'):
                            ts = time.strftime('%Y-%m-%d %H:%M:%S')
                            line = f"{ts} [{level.upper()}] {message}\n"
                            try:
                                # Enqueue to background writer; drop if queue is full
                                self.debug_write_queue.put_nowait(line)
                            except Exception:
                                # Queue full or other issue; drop the log write
                                pass
                    except Exception:
                        pass
        except queue.Empty:
            pass # No messages in queue
        finally:
            self.root.after(100, self.process_log_queue) # Schedule next check after 100ms

    def home_motor(self):
        # Run the motor control function in a separate thread to keep GUI responsive
        threading.Thread(target=sequence_controller.home_motor, args=(self.log_queue,), daemon=True).start()

    def run_forward(self):
        threading.Thread(target=sequence_controller.run_motor, args=(self.log_queue, "forward"), daemon=True).start()

    def run_reverse(self):
        threading.Thread(target=sequence_controller.run_motor, args=(self.log_queue, "reverse"), daemon=True).start()

    def stop_motor(self):
        threading.Thread(target=sequence_controller.stop_motor, args=(self.log_queue,), daemon=True).start()

    def toggle_sun_tracking(self):
        if self.tracking_thread and self.tracking_thread.is_alive():
            # Stop the tracking
            self.tracking_active.set() # Signal the thread to stop
            self.tracking_thread.join(timeout=2) # Wait for it to finish
            self.tracking_thread = None
            self.track_button.config(text="Start Sun Tracking")
            self.enable_controls() # Re-enable all controls
        else:
            # Start the tracking
            self.tracking_active.clear()
            self.tracking_thread = threading.Thread(
                target=sequence_controller.sun_tracking_loop,
                args=(self.log_queue, self.tracking_active),
                daemon=True
            )
            self.tracking_thread.start()
            self.track_button.config(text="Stop Sun Tracking")
            self.disable_manual_controls() # Disable manual controls during tracking

    def add_loop_start(self):
        """Adds a LOOP_START:N command to the sequence."""
        reps = self._get_user_input("Start Loop", "Enter number of repetitions:")
        if reps and reps.isdigit() and int(reps) > 0:
            self.add_to_sequence(f"LOOP_START:{reps}")
        else:
            self.log_queue.put(('warn', f"Invalid number of repetitions: {reps}"))

    def _get_user_input(self, title, prompt):
        """Helper function to show a simple dialog and get user input."""
        dialog = CustomInputDialog(self.root, title, prompt)
        return dialog.result

    def add_motor_command(self):
        """Adds a generic MOTOR_CMD:<id>:<cmd> command."""
        motor_id = self._get_user_input("Motor Command", "Enter Motor ID (e.g., 1):")
        if not (motor_id and motor_id.isdigit()):
            return
        cmd = self._get_user_input("Motor Command", "Enter Command (FWD, REV, STOP, HOME):")
        if cmd and cmd.upper() in ["FWD", "REV", "STOP", "HOME"]:
            self.add_to_sequence(f"MOTOR_CMD:{motor_id}:{cmd.upper()}")

    def add_timed_move(self):
        """Adds a timed motor command like MOVE_TIME:<id>:<dir>:<ms>."""
        motor_id = self._get_user_input("Timed Move", "Enter Motor ID:")
        if not (motor_id and motor_id.isdigit()):
            return
        direction = self._get_user_input("Timed Move", "Enter Direction (FWD or REV):")
        if direction and direction.upper() in ["FWD", "REV"]:
            ms = self._get_user_input("Timed Move", "Enter duration in milliseconds (e.g., 500):")
            if ms and ms.isdigit():
                self.add_to_sequence(f"MOVE_TIME:{motor_id}:{direction.upper()}:{ms}")

    def add_move_distance(self):
        """Adds a MOVE_DIST:<id>:<dist_mm> command."""
        motor_id = self._get_user_input("Move Distance", "Enter Motor ID:")
        if motor_id and motor_id.isdigit():
            dist = self._get_user_input("Move Distance", "Enter distance (e.g., in mm):")
            if dist: # Allow float/negative
                self.add_to_sequence(f"MOVE_DIST:{motor_id}:{dist}")

    def add_turn_degrees(self):
        """Adds a TURN:<id>:<degrees> command."""
        motor_id = self._get_user_input("Turn Degrees", "Enter Motor ID:")
        if motor_id and motor_id.isdigit():
            deg = self._get_user_input("Turn Degrees", "Enter degrees (e.g., 90, -90):")
            if deg: # Allow float/negative
                self.add_to_sequence(f"TURN:{motor_id}:{deg}")

    def add_set_speed(self):
        """Adds a SET_SPEED:<id>:<%> command."""
        motor_id = self._get_user_input("Set Speed", "Enter Motor ID ('*' for all):")
        if motor_id:
            speed = self._get_user_input("Set Speed", "Enter speed percentage (0-100):")
            if speed and speed.isdigit() and 0 <= int(speed) <= 100:
                self.add_to_sequence(f"SET_SPEED:{motor_id}:{speed}")

    def add_custom_wait(self):
        """Adds a custom WAIT command to the sequence."""
        duration = self._get_user_input("Wait", "Enter wait duration in seconds (e.g., 2.5):")
        if duration:
            try:
                # Validate that it's a valid float
                float(duration)
                self.add_to_sequence(f"WAIT:{duration}")
            except ValueError:
                self.log_queue.put(('warn', f"Invalid wait duration: {duration}"))

    def add_set_output(self):
        """Adds a SET_OUTPUT command for controlling a pin (e.g., a relay or LED)."""
        pin = self._get_user_input("Set Output Pin", "Enter digital pin number:")
        if pin and pin.isdigit():
            state = self._get_user_input("Set Pin State", "Enter state (1 for HIGH, 0 for LOW):")
            if state in ('0', '1'):
                self.add_to_sequence(f"SET_OUTPUT:{pin}:{state}")

    def add_if_sensor(self):
        """Adds an IF_SENSOR:<pin>:<state> command."""
        pin = self._get_user_input("IF Condition", "Enter sensor pin number (e.g., 2):")
        if pin and pin.isdigit():
            state = self._get_user_input("IF Condition", "Enter desired state (HIGH or LOW):")
            if state and state.upper() in ("HIGH", "LOW"):
                self.add_to_sequence(f"IF_SENSOR:{pin}:{state.upper()}")

    def add_set_variable(self):
        """Adds a SET_VAR:<name>:<value> command."""
        var_name = self._get_user_input("Set Variable", "Enter variable name (e.g., my_speed):")
        if var_name and var_name.isalnum():
            value = self._get_user_input("Set Variable", f"Enter value for ${var_name}:")
            if value is not None:
                self.add_to_sequence(f"SET_VAR:{var_name}:{value}")

    def add_comment(self):
        """Adds a comment line to the sequence."""
        comment_text = self._get_user_input("Add Comment", "Enter comment text:")
        if comment_text:
            self.add_to_sequence(f"# {comment_text}")

    def add_to_sequence(self, command):
        """Appends a command to the custom sequence string."""
        self.sequence_text.insert(tk.END, command + '\n')

    def clear_sequence(self):
        """Clears the custom sequence string."""
        self.sequence_text.delete('1.0', tk.END)

    def execute_sequence(self):
        """Parses and runs the custom sequence in a background thread."""
        sequence_content = self.sequence_text.get("1.0", tk.END).strip()
        if not sequence_content:
            self.log_queue.put(('warn', "Custom sequence is empty."))
            return

        # Add pre-run check for connection status
        if not serial_handler.serial_connection or not serial_handler.serial_connection.is_open:
            messagebox.showerror(
                "Connection Error",
                "Cannot run sequence: Not connected to a serial port.",
                parent=self.root
            )
            return

        if self.sequence_thread and self.sequence_thread.is_alive():
            self.log_queue.put(('warn', "A sequence is already running."))
            return

        # Each line is a command, ignore empty lines
        command_list = [line.strip() for line in sequence_content.split('\n') if line.strip()]
        self.disable_controls()

        self.sequence_thread = threading.Thread(
            target=self._execute_sequence_worker, # Pass sensor states to the worker
            args=(command_list,),
            daemon=True
        )
        self.sequence_thread.start()

    def _execute_sequence_worker(self, command_list):
        """Helper that runs in the thread; re-enables controls when done."""
        try:
            sequence_controller.run_custom_sequence(self.log_queue, command_list, self.sensor_states, settings=self.settings)
        finally:
            self.root.after(0, self.enable_controls) # Schedule GUI update on main thread

    def send_direct_command(self, event=None):
        """Sends a single command from the direct command entry box."""
        command = self.direct_command_var.get().strip()
        if not command:
            return  # Do nothing if the entry is empty
        # Normalize the command (map aliases, validate format)
        normalized = self._normalize_command(command)
        if normalized is None:
            messagebox.showerror("Command Error", f"Invalid command format: {command}", parent=self.root)
            return

        command_to_send, expect_ack = normalized

        try:
            res = serial_handler.send_command(command_to_send, expect_ack=expect_ack)
        except Exception:
            res = (False, 'exception')

        if isinstance(res, tuple):
            ok, info = res
        else:
            ok = bool(res)
            info = ''

        if ok:
            self.direct_command_var.set("")
        else:
            if not getattr(serial_handler, 'serial_connection', None) or not getattr(serial_handler.serial_connection, 'is_open', False):
                messagebox.showerror("Connection Error", "Cannot send command: Not connected to a serial port.", parent=self.root)
            else:
                messagebox.showerror("Command Error", f"Command failed: {info}", parent=self.root)

    def insert_selected_template(self):
        self.direct_command_var.set(self.selected_template.get())

    def _normalize_command(self, command_text):
        """Normalize user-entered command into (command, expect_ack) or None if invalid."""
        if not command_text:
            return None
        cmd = command_text.strip()
        parts = cmd.split(':')
        base = parts[0].upper()

        # Map aliases
        if base in ('FORWARD', 'FWD'):
            return ("MOTOR_CMD:0:FWD", True)
        if base in ('REVERSE', 'REV'):
            return ("MOTOR_CMD:0:REV", True)

        ack_commands = ('MOTOR_CMD','MOVE_TIME','MOVE_DIST','TURN','SET_CAL','STOP_ALL','GET_CAL','TEST_MOTOR','PIN_TEST')

        if base == 'MOTOR_CMD':
            if len(parts) < 3:
                return None
            motor_id = parts[1]
            sub = parts[2].upper()
            if sub in ('FWD','REV','STOP','HOME'):
                return (f"MOTOR_CMD:{motor_id}:{sub}", True)
            return None

        if base in ('MOVE_TIME','MOVE_DIST','TURN'):
            return (cmd, True)

        if base == 'TEST_MOTOR':
            if len(parts) < 2:
                return None
            return (cmd, True)

        if base == 'PIN_TEST':
            return (cmd, True)

        if base == 'SENSOR_REPORT':
            if len(parts) < 2:
                return None
            v = parts[1].upper()
            if v in ('ON','OFF'):
                return (f"SENSOR_REPORT:{v}", True)
            return None

        if base in ('GET_CAL','SET_CAL','STOP_ALL'):
            return (cmd, True)

        # Default: send as-is without ACK
        return (cmd, False)

    def get_device_calibration(self):
        try:
            res = serial_handler.send_command('GET_CAL', expect_ack=True)
        except Exception:
            res = (False, 'exception')
        if isinstance(res, tuple) and res[0]:
            self.log_queue.put(('info', f'Calibration response: {res[1]}'))
        else:
            self.log_queue.put(('error', f'Failed to get calibration: {res[1] if isinstance(res, tuple) else res}'))

    def run_pin_test(self):
        try:
            res = serial_handler.send_command('PIN_TEST', expect_ack=True)
        except Exception:
            res = (False, 'exception')
        if isinstance(res, tuple) and res[0]:
            self.log_queue.put(('info', 'PIN_TEST invoked on device.'))
        else:
            self.log_queue.put(('error', f'Failed to run PIN_TEST: {res[1] if isinstance(res, tuple) else res}'))

    def show_motor_config_dialog(self):
        config_window = tk.Toplevel(self.root)
        config_window.title("Motor Configuration")
        config_window.transient(self.root)
        config_window.grab_set()

        main_frame = tk.Frame(config_window, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Define names for motor IDs and test them.").pack(pady=(0, 10))

        entries = {}
        for motor_id, motor_name in self.motor_definitions.items():
            row_frame = tk.Frame(main_frame)
            row_frame.pack(fill=tk.X, pady=5)

            tk.Label(row_frame, text=f"Motor ID: {motor_id}", width=12).pack(side=tk.LEFT)
            
            name_entry = tk.Entry(row_frame, width=30)
            name_entry.insert(0, motor_name)
            name_entry.pack(side=tk.LEFT, padx=5)
            entries[motor_id] = name_entry

            # The lambda for the command needs to capture the motor_id
            tk.Button(row_frame, text="Test", command=lambda mid=motor_id: self.test_motor(mid)).pack(side=tk.LEFT, padx=5)

        def save_config():
            for motor_id, entry_widget in entries.items():
                self.motor_definitions[motor_id] = entry_widget.get()
            self.log_queue.put(('info', 'Motor definitions updated.'))
            config_window.destroy()

        tk.Button(main_frame, text="Save and Close", command=save_config).pack(pady=(15,0))

    def set_log_level_dialog(self):
        """Prompt the user to choose a log level and apply it to the serial handler if available."""
        level = simpledialog.askstring("Log Level", "Enter log level (DEBUG, INFO, WARN, ERROR):", parent=self.root)
        if not level:
            return
        level = level.strip().upper()
        valid = ('DEBUG', 'INFO', 'WARN', 'ERROR')
        if level not in valid:
            messagebox.showerror("Invalid Level", f"Invalid log level: {level}", parent=self.root)
            return
        # Apply to serial_handler if it supports it
        try:
            if hasattr(serial_handler, 'LOG_LEVEL'):
                serial_handler.LOG_LEVEL = level
            self.log_queue.put(('info', f'Log level set to {level}'))
        except Exception as e:
            self.log_queue.put(('error', f'Failed to set log level: {e}'))

    def toggle_sensor_reporting(self):
        """Toggle sensor reporting on the device via a serial command."""
        # Ask the device current state by toggling: if it's on, we'll turn it off and vice versa
        # Query the serial handler's last known state from GUI sensor_states isn't reliable; simply ask user
        choice = messagebox.askyesno("Sensor Reports", "Enable sensor reporting? (Yes=Enable, No=Disable)", parent=self.root)
        cmd = f"SENSOR_REPORT:{'ON' if choice else 'OFF'}"
        try:
            res = serial_handler.send_command(cmd, expect_ack=True)
        except Exception:
            res = (False, 'exception')
        if isinstance(res, tuple) and res[0]:
            self.log_queue.put(('info', f"Sensor reporting set {'ON' if choice else 'OFF'}"))
            # update setting
            self.settings['sensor_report'] = bool(choice)
            self.save_settings()
        else:
            self.log_queue.put(('error', f"Failed to set sensor reporting: {res[1] if isinstance(res, tuple) else res}"))

    def test_motor(self, motor_id):
        try:
            res = serial_handler.send_command(f"TEST_MOTOR:{motor_id}")
        except Exception:
            res = (False, 'exception')

    def show_calibration_dialog(self):
        """Opens a dialog for hardware calibration routines."""
        cal_window = tk.Toplevel(self.root)
        cal_window.title("Hardware Calibration")
        cal_window.geometry("400x320")
        cal_window.transient(self.root)
        cal_window.grab_set() # Modal behavior
        cal_window.resizable(False, False)

        main_frame = tk.Frame(cal_window, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Use these functions to calibrate hardware components. Follow any prompts that appear in the main log window.", wraplength=370, justify=tk.LEFT).pack(pady=(0, 15))

        # Motor Calibration Frame
        motor_frame = tk.LabelFrame(main_frame, text="Motor Calibration", padx=10, pady=10)
        motor_frame.pack(fill=tk.X, pady=5)
        tk.Label(motor_frame, text="Calibrates motor travel distance using encoders or limit switches. Ensure the track is clear before starting.", wraplength=350, justify=tk.LEFT).pack()
        tk.Button(motor_frame, text="Start Motor Calibration", command=self.start_motor_calibration).pack(pady=(10, 5))

        # Sensor Calibration Frame
        sensor_frame = tk.LabelFrame(main_frame, text="Optical Sensor Calibration", padx=10, pady=10)
        sensor_frame.pack(fill=tk.X, pady=5)
        tk.Label(sensor_frame, text="Calibrates the minimum/maximum range for the light sensors used in sun tracking.", wraplength=350, justify=tk.LEFT).pack()
        tk.Button(sensor_frame, text="Start Sensor Calibration", command=self.start_sensor_calibration).pack(pady=(10, 5))

        tk.Button(main_frame, text="Close", command=cal_window.destroy).pack(side=tk.BOTTOM, pady=(15, 0))

    def start_motor_calibration(self):
        """Sends the motor calibration command to the Arduino."""
        self.log_queue.put(('info', "Requesting motor calibration sequence..."))
        try:
            res = serial_handler.send_command("CALIBRATE:MOTORS")
        except Exception:
            res = (False, 'exception')

        ok = res[0] if isinstance(res, tuple) else bool(res)
        if not ok:
            self.log_queue.put(('error', "Failed to send motor calibration command."))
            messagebox.showerror("Command Error", "Failed to send motor calibration command. Is the device connected?", parent=self.root)
        else:
            messagebox.showinfo("Calibration Started", "Motor calibration command sent. Monitor the log for progress and prompts.", parent=self.root)

    def start_sensor_calibration(self):
        """Sends the sensor calibration command to the Arduino."""
        self.log_queue.put(('info', "Requesting sensor calibration sequence..."))
        try:
            res = serial_handler.send_command("CALIBRATE:SENSORS")
        except Exception:
            res = (False, 'exception')

        ok = res[0] if isinstance(res, tuple) else bool(res)
        if not ok:
            self.log_queue.put(('error', "Failed to send sensor calibration command."))
            messagebox.showerror("Command Error", "Failed to send sensor calibration command. Is the device connected?", parent=self.root)
        else:
            messagebox.showinfo("Calibration Started", "Sensor calibration command sent. Monitor the log for progress and prompts.", parent=self.root)

    def show_about_dialog(self):
        """Displays a simple 'About' dialog with credits and contact info."""
        about_window = tk.Toplevel(self.root)
        about_window.title("About")
        about_window.geometry("450x250")
        about_window.transient(self.root)
        about_window.grab_set()
        about_window.resizable(False, False)

        main_frame = tk.Frame(about_window, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Motor Control & Automation GUI", font=('Arial', 14, 'bold')).pack(pady=(0, 5))
        tk.Label(main_frame, text="Version: 1.2").pack()

        tk.Label(main_frame, text="\nDesign & Development:", font=('Arial', 10, 'bold')).pack()
        tk.Label(main_frame, text="Wiley Breitenstein DBA Wiley").pack()
        tk.Label(main_frame, text="Contact: Wbreitenst@gmail.com").pack()

        tk.Label(main_frame, text="\nAI-Assisted by:", font=('Arial', 10, 'bold')).pack()
        tk.Label(main_frame, text="Gemini Code Assist").pack()

        button_frame = tk.Frame(main_frame)
        button_frame.pack(side=tk.BOTTOM, pady=(15, 0))

        tk.Button(button_frame, text="View Licenses", command=self.show_licenses_dialog).pack(side=tk.LEFT, padx=10)
        tk.Button(button_frame, text="Close", command=about_window.destroy).pack(side=tk.LEFT, padx=10)

    def show_licenses_dialog(self):
        """Displays this project's license and licenses of third-party packages."""
        license_window = tk.Toplevel(self.root)
        license_window.title("Licenses")
        license_window.geometry("600x600")
        license_window.transient(self.root)
        license_window.grab_set()

        text_widget = scrolledtext.ScrolledText(license_window, wrap=tk.WORD, padx=10, pady=10)
        text_widget.pack(fill=tk.BOTH, expand=True)

        # --- This Project's License ---
        text_widget.insert(tk.END, "--- This Project's License ---\n\n")
        try:
            with open('LICENSE.txt', 'r') as f:
                text_widget.insert(tk.END, f.read())
        except FileNotFoundError:
            text_widget.insert(tk.END, "LICENSE.txt not found in the application directory.")

        # --- Third-Party Licenses ---
        text_widget.insert(tk.END, "\n\n\n--- Licenses ---\n\n")
        pyserial_license = """This application uses the 'pyserial' package, which is licensed under the BSD 3-Clause License:

Copyright (c) 2001-2020, Chris Liechti. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
* Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""
        text_widget.insert(tk.END, pyserial_license)

        text_widget.config(state='disabled') # Make it read-only

    # ----------------- Settings Persistence & UI -----------------
    def load_settings(self):
        defaults = {
            'sensor_report': True,
            'sensor_default': 'low',
            'movement_timeout': 120.0,
            'initial_wait': 5.0,
            'stall_window': 3.0,
            'debug_log_enabled': False,
            'debug_log_path': os.path.join(os.path.dirname(__file__), 'motor_debug.log')
        }
        try:
            if os.path.isfile(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    s = json.load(f)
                # Merge defaults
                for k, v in defaults.items():
                    if k not in s:
                        s[k] = v
                return s
        except Exception:
            pass
        return defaults

    def save_settings(self):
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
            self.log_queue.put(('info', 'Settings saved.'))
        except Exception as e:
            self.log_queue.put(('error', f'Failed to save settings: {e}'))

    def reset_settings(self):
        # Reset to defaults and save
        try:
            if os.path.isfile(self.settings_file):
                os.remove(self.settings_file)
        except Exception:
            pass
        self.settings = self.load_settings()
        self.save_settings()
        self.log_queue.put(('info', 'Settings reset to defaults.'))

    def show_app_settings(self):
        win = tk.Toplevel(self.root)
        win.title('Application Settings')
        win.transient(self.root)
        win.grab_set()

        frm = tk.Frame(win, padx=10, pady=10)
        frm.pack(fill=tk.BOTH, expand=True)

        # Sensor reporting
        sensor_var = tk.BooleanVar(value=self.settings.get('sensor_report', False))
        tk.Checkbutton(frm, text='Enable Sensor Reporting', variable=sensor_var).pack(anchor='w')

        # Sensor default level
        tk.Label(frm, text='Sensor Default Level:').pack(anchor='w', pady=(8,0))
        sd_var = tk.StringVar(value=self.settings.get('sensor_default', 'Low'))
        tk.OptionMenu(frm, sd_var, 'HIGH', 'LOW').pack(anchor='w')

        # Movement timeout
        tk.Label(frm, text='Movement Timeout (s):').pack(anchor='w', pady=(8,0))
        mt_var = tk.StringVar(value=str(self.settings.get('movement_timeout', 120.0)))
        tk.Entry(frm, textvariable=mt_var).pack(anchor='w')

        # Debug file logging
        tk.Label(frm, text='Debug File Logging:', pady=6).pack(anchor='w')
        debug_var = tk.BooleanVar(value=self.settings.get('debug_log_enabled', False))
        tk.Checkbutton(frm, text='Enable detailed debug log to external file', variable=debug_var).pack(anchor='w')
        tk.Label(frm, text='Log File Path:').pack(anchor='w', pady=(8,0))
        dl_var = tk.StringVar(value=self.settings.get('debug_log_path', 'motor_debug.log'))
        path_row = tk.Frame(frm)
        path_row.pack(fill=tk.X, anchor='w')
        path_entry = tk.Entry(path_row, textvariable=dl_var, width=48)
        path_entry.pack(side=tk.LEFT, padx=(0,5))
        def browse_log_path():
            p = filedialog.asksaveasfilename(title='Select debug log file', defaultextension='.log', initialfile=dl_var.get())
            if p:
                dl_var.set(p)
        tk.Button(path_row, text='Browse', command=browse_log_path).pack(side=tk.LEFT)

        def on_save():
            try:
                self.settings['sensor_report'] = bool(sensor_var.get())
                self.settings['sensor_default'] = sd_var.get()
                self.settings['movement_timeout'] = float(mt_var.get())
                self.settings['debug_log_enabled'] = bool(debug_var.get())
                self.settings['debug_log_path'] = dl_var.get()
                # keep initial_wait and stall_window unchanged unless user changes further
                self.save_settings()
                # apply to device immediately
                self.apply_settings_to_device()
                # (re)open or close debug file as requested
                if self.settings.get('debug_log_enabled'):
                    self.open_debug_log()
                else:
                    self.close_debug_log()
                win.destroy()
            except Exception as e:
                messagebox.showerror('Error', f'Invalid settings: {e}', parent=win)

        def on_reset():
            if messagebox.askyesno('Reset', 'Reset settings to defaults?', parent=win):
                self.reset_settings()
                win.destroy()

        btn_frame = tk.Frame(frm)
        btn_frame.pack(fill=tk.X, pady=(12,0))
        tk.Button(btn_frame, text='Save', command=on_save).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text='Reset to Defaults', command=on_reset).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text='Cancel', command=win.destroy).pack(side=tk.RIGHT, padx=5)

    def clear_console(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state='disabled')
        self.log_queue.put(('info', 'Console cleared.'))

    def apply_settings_to_device(self):
        # Send SENSOR_REPORT command according to settings
        if not getattr(serial_handler, 'serial_connection', None) or not getattr(serial_handler.serial_connection, 'is_open', False):
            return
        try:
            cmd = f"SENSOR_REPORT:{'ON' if self.settings.get('sensor_report', False) else 'OFF'}"
            serial_handler.send_command(cmd, expect_ack=True)
        except Exception:
            pass

    def show_functions_dialog(self):
        """Displays a detailed, scrollable guide of all application functions."""
        # Create a Toplevel window
        help_window = tk.Toplevel(self.root)
        help_window.title("Function Guide")
        help_window.geometry("650x550")
        help_window.transient(self.root) # Keep it on top of the main window
        help_window.grab_set() # Modal behavior

        # Add a ScrolledText widget
        help_text_widget = scrolledtext.ScrolledText(help_window, wrap=tk.WORD, padx=10, pady=10)
        help_text_widget.pack(fill=tk.BOTH, expand=True)

        # Define the help text
        functions_text = """Motor Control & Automation GUI - Function Guide

This application provides comprehensive control over connected motors and devices via a serial interface.

---
**File Menu**
---
- **Connect/Disconnect**: Manage the serial connection to your microcontroller.
- **Load Sequence...**: Load a command sequence from a .txt file into the sequencer.
- **Save Sequence As...**: Save the current commands in the sequencer to a .txt file.
- **Exit**: Close the application.

---
**Controls Menu**
---
- **Manual Control**: Directly control the default motor (ID 0).
- **Stop Motor**: Immediately stops the default motor (ID 0).
- **Start/Stop Sun Tracking**: Toggles the automatic sun tracking mode.
- **Show Custom Sequencer**: Toggles the visibility of the sequence builder panel.

---
**View Menu**
---
- **Show Manual Control Buttons**: Toggles the main row of control buttons.
- **Show COM Port Controls**: Toggles the connection bar.

---
**Custom Sequencer Commands**
---
The sequencer executes commands line by line.

**Motor & I/O Commands:**
- `MOTOR_CMD:<id>:<cmd>`: Send a basic command (FWD, REV, STOP, HOME) to a motor.
- `MOVE_TIME:<id>:<dir>:<ms>`: Move a motor in a direction (FWD/REV) for a duration in milliseconds.
- `MOVE_DIST:<id>:<dist>`: Move a motor a specific distance (requires encoder).
- `TURN:<id>:<deg>`: Turn a motor a specific number of degrees (requires encoder).
- `SET_SPEED:<id>:<%>`: Set motor speed (0-100). Use '*' for all motors.
- `SET_OUTPUT:<pin>:<state>`: Set a digital output pin to a state (1 or 0).

**Logic & Flow Control:**
- `LOOP_START:<reps>`: Start a loop that repeats <reps> times.
- `LOOP_END`: Marks the end of a loop block.
- `IF_SENSOR:<pin>:<state>`: Starts a conditional block. Executes if the sensor on <pin> matches the <state> (HIGH or LOW).
- `ENDIF`: Marks the end of an IF block.
- `WAIT:<seconds>`: Pause the sequence for a number of seconds (can be a float).
- `SET_VAR:<name>:<value>`: Create a variable. Use it in other commands with `$name`.
- `# <comment>`: Lines starting with '#' are ignored and can be used for notes."""

        help_text_widget.insert('1.0', functions_text)
        help_text_widget.config(state='disabled')

    def save_sequence(self):
        """Opens a dialog to save the current sequence to a file."""
        sequence_str = self.sequence_text.get("1.0", tk.END).strip()
        if not sequence_str:
            messagebox.showwarning("Save Sequence", "Sequence is empty. Nothing to save.", parent=self.root)
            return

        filepath = filedialog.asksaveasfilename(
            title="Save Sequence As",
            defaultextension=".txt",
            filetypes=[("Sequence Files", "*.txt"), ("All Files", "*.*")]
        )

        if not filepath:
            # User cancelled the dialog
            return

        try:
            with open(filepath, 'w') as f:
                f.write(sequence_str)
            self.log_queue.put(('info', f"Sequence saved to {filepath}"))
        except Exception as e:
            self.log_queue.put(('error', f"Failed to save sequence: {e}"))
            messagebox.showerror("Save Error", f"Could not save file:\n{e}", parent=self.root)

    def load_sequence(self):
        """Opens a dialog to load a sequence from a file."""
        filepath = filedialog.askopenfilename(
            title="Load Sequence",
            filetypes=[("Sequence Files", "*.txt"), ("All Files", "*.*")]
        )

        if not filepath:
            # User cancelled the dialog
            return

        try:
            with open(filepath, 'r') as f:
                sequence_str = f.read()
            self.clear_sequence()
            self.sequence_text.insert('1.0', sequence_str)
            self.log_queue.put(('info', f"Sequence loaded from {filepath}"))
        except Exception as e:
            self.log_queue.put(('error', f"Failed to load sequence: {e}"))
            messagebox.showerror("Load Error", f"Could not load file:\n{e}", parent=self.root)

    def toggle_manual_controls_view(self):
        """Shows or hides the manual control button frame based on the View menu setting."""
        if self.show_manual_controls_var.get():
            self.control_frame.pack(side=tk.TOP, fill=tk.X)
        else:
            self.control_frame.pack_forget()

    def toggle_comport_view(self):
        """Shows or hides the COM port connection frame based on the View menu setting."""
        if self.show_comport_buttons_var.get():
            self.connection_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        else:
            self.connection_frame.pack_forget()

    def toggle_sequence_builder_view(self):
        """Shows or hides the custom sequence builder frame."""
        if self.show_sequence_builder_var.get():
            self.sequence_builder_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0,5))
        else:
            self.sequence_builder_frame.pack_forget()

    def disable_manual_controls(self):
        """Disables only the manual movement buttons, leaving tracking active."""
        self.home_button.config(state=tk.DISABLED)
        self.run_fwd_button.config(state=tk.DISABLED)
        self.run_rev_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        # Also disable corresponding menu items
        self.manual_control_menu.entryconfig("Home Motor", state=tk.DISABLED)
        self.manual_control_menu.entryconfig("Run Forward", state=tk.DISABLED)
        self.manual_control_menu.entryconfig("Run Reverse", state=tk.DISABLED)
        self.controls_menu.entryconfig("Stop Motor", state=tk.DISABLED)

    def handle_disconnect_event(self):
        """Resets the GUI to a disconnected state, called by user or on error."""
        if self.tracking_thread and self.tracking_thread.is_alive():
            self.tracking_active.set()
            self.track_button.config(text="Start Sun Tracking")
            # We don't join here to avoid blocking the GUI thread if called from queue

        self.disable_controls()
        self.connect_button.config(state=tk.NORMAL)
        self.disconnect_button.config(state=tk.DISABLED)
        self.refresh_ports_button.config(state=tk.NORMAL)
        self.port_dropdown.config(state=tk.NORMAL)
        self.current_position.set("Position: N/A")
        self.current_angle.set("Angle: N/A")

    def disable_controls(self):
        """Disables all control buttons, e.g., when not connected."""
        self.disable_manual_controls()
        self.track_button.config(state=tk.DISABLED)
        self.run_sequence_button.config(state=tk.DISABLED)
        self.send_direct_cmd_button.config(state=tk.DISABLED)
        self.direct_cmd_entry.config(state=tk.DISABLED)
        self.menubar.entryconfig("Controls", state=tk.DISABLED)

    def enable_controls(self):
        """Enables all control buttons."""
        self.home_button.config(state=tk.NORMAL)
        self.run_fwd_button.config(state=tk.NORMAL)
        self.run_rev_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.track_button.config(state=tk.NORMAL)
        self.run_sequence_button.config(state=tk.NORMAL)
        self.send_direct_cmd_button.config(state=tk.NORMAL)
        self.direct_cmd_entry.config(state=tk.NORMAL)
        self.menubar.entryconfig("Controls", state=tk.NORMAL)
        # Also enable all items within the controls menu
        # Check if there are any items in the menu before trying to get index(tk.END)
        end_index = self.controls_menu.index(tk.END)
        max_range = (end_index + 1) if end_index is not None else 0
        manual_control_end_index = self.manual_control_menu.index(tk.END)
        manual_control_max_range = (manual_control_end_index + 1) if manual_control_end_index is not None else 0
        for i in range(max_range):
            try: # This will skip separators
                self.controls_menu.entryconfig(i, state=tk.NORMAL)
            except tk.TclError: # Catch error if index is out of bounds or item is not configurable
                pass
        # Also enable all items within the manual controls sub-menu
        for i in range(manual_control_max_range):
            try: # This will skip separators
                self.manual_control_menu.entryconfig(i, state=tk.NORMAL)
            except tk.TclError:
                pass

    def exit_app(self):
        """Handle window close event."""
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            # Stop the tracking thread if it's running
            if self.tracking_thread and self.tracking_thread.is_alive():
                self.tracking_active.set()

            serial_handler.disconnect_serial()
            # close debug file if open
            try:
                self.close_debug_log()
            except Exception:
                pass
            self.root.destroy() # This will stop the .after loop and close the app

    def open_debug_log(self):
        try:
            path = self.settings.get('debug_log_path')
            if not path:
                return
            # Close existing if different
            if getattr(self, 'debug_log_fh', None):
                try:
                    self.debug_log_fh.close()
                except Exception:
                    pass
            # Open in append mode
            self.debug_log_fh = open(path, 'a', encoding='utf-8')
            self.log_queue.put(('info', f'Debug log opened: {path}'))
        except Exception as e:
            self.log_queue.put(('error', f'Failed to open debug log: {e}'))

    def close_debug_log(self):
        try:
            if getattr(self, 'debug_log_fh', None):
                try:
                    self.debug_log_fh.close()
                except Exception:
                    pass
                self.debug_log_fh = None
                self.log_queue.put(('info', 'Debug log closed.'))
        except Exception:
            pass

    def _debug_writer_loop(self):
        """Background thread: write queued debug lines to the debug file handle."""
        while not self._debug_writer_stop.is_set():
            try:
                line = self.debug_write_queue.get(timeout=0.5)
            except Exception:
                continue
            try:
                if getattr(self, 'debug_log_fh', None):
                    try:
                        self.debug_log_fh.write(line)
                        self.debug_log_fh.flush()
                    except Exception:
                        # If file handle failed, drop the write
                        pass
            finally:
                try:
                    self.debug_write_queue.task_done()
                except Exception:
                    pass

if __name__ == "__main__":
    root = tk.Tk()
    app = MotorControlGUI(root)
    root.mainloop()
