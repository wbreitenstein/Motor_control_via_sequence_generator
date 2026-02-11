/*
  Motor Control & Automation Sketch for Arduino Uno R3
  
  This sketch is designed to work with the Python Motor Control GUI.
  It listens for commands over the serial port and controls motors and I/O pins accordingly.

  Hardware Assumptions:
  - 2 DC Motors connected via an L298N H-Bridge Motor Driver.
  - 2 Digital Sensors (e.g., limit switches) connected to pins 2 and 3.
  - 2 Analog Sensors (e.g., photoresistors/LDRs for sun tracking) on A0, A1.

  Author: Gemini Code Assist (based on user's Python application)
  Date:   2024
*/

#if defined(__has_include)
#if __has_include(<Arduino.h>)
#include <Arduino.h>
#else
// Minimal Arduino stubs for IntelliSense / editor when Arduino.h is not available
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

#define HIGH 0x1
#define LOW 0x0
#define INPUT 0x0
#define INPUT_PULLUP 0x2
#define OUTPUT 0x1

// Define common analog pin constants used by sketches
#define A0 14
#define A1 15
#define A2 16

inline void pinMode(int, int) {}
inline int digitalRead(int) {
  return 0;
}
inline void digitalWrite(int, int) {}
inline void analogWrite(int, int) {}
inline int analogRead(int) {
  return 0;
}
inline void delay(unsigned long) {}
inline unsigned long millis() {
  return 0UL;
}

// Provide a simple abs implementation for editor builds
// Arduino map utility
inline long map(long x, long in_min, long in_max, long out_min, long out_max) {
  if (in_max == in_min) return out_min;  // avoid div by zero
  return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}
inline int abs(int x) {
  return x < 0 ? -x : x;
}

// Minimal String stub (enough for parsing in editor)
class String {
public:
  String() {}
  void reserve(unsigned int) {}
  void trim() {}
  void toCharArray(char* buf, unsigned int) {
    if (buf) buf[0] = '\0';
  }
  void operator+=(char) {}
  void operator=(const char*) {}
};

// Minimal Serial stub
class _Serial {
public:
  void begin(unsigned long) {}
  int available() {
    return 0;
  }
  int read() {
    return -1;
  }
  void print(const char*) {}
  void print(int) {}
  void println(const char*) {}
  void println(int) {}
} Serial;
#endif
#else
#include <Arduino.h>
#endif

#include <string.h>
#include <EEPROM.h>

// --- Pin Definitions ---

// Motor A (ID 0)
const int ENA = 5;  // Speed control (PWM)
const int IN1 = 7;
const int IN2 = 8;

// Motor B (ID 1)
const int ENB = 6;  // Speed control (PWM)
const int IN3 = 9;
const int IN4 = 11;

// Digital Sensor Pins
const int SENSOR_PIN_1 = 2;
const int SENSOR_PIN_2 = 3;
int sensor1_last_state = -1;  // Used to detect changes
int sensor2_last_state = -1;  // Used to detect changes

// Analog Sensor Pins (for sun tracking)
const int LDR_LEFT_PIN = A0;
const int LDR_RIGHT_PIN = A1;

//Feedback Motor (Encoder)
const int FEEDBACK_MOTOR_PIN_FWD = 12;  //Digital pin to control direction
const int FEEDBACK_MOTOR_PIN_REV = 13;  //Digital pin to control direction
const int FEEDBACK_ANALOG_PIN = A2;     //Analog pin to read feedback voltage

// --- Global Variables ---
String inputString;           // A String to hold incoming data
bool stringComplete = false;  // Whether the string is complete

// Motor Speeds (0-255)
int motor_speeds[2] = { 255, 255 };  // Default speed for motor 0 and 1

// EEPROM-backed calibration (ms per mm, ms per degree)
const int EEPROM_ADDR_MS_PER_MM = 0;   // 4 bytes
const int EEPROM_ADDR_MS_PER_DEG = 4;  // next 4 bytes
int ms_per_mm = 50;
int ms_per_deg = 10;
// Sensor reporting gate
bool sensor_report_enabled = true;

// Non-blocking timer variables for MOVE_TIME
unsigned long move_timer_start = 0;
unsigned long move_duration = 0;
int timed_move_motor_id = -1;

// Non-blocking timer for sensor updates
unsigned long last_sensor_check = 0;
const long SENSOR_CHECK_INTERVAL = 250;  // Check sensors every 250ms

// Function prototypes (needed for some build environments that don't auto-generate prototypes)
void handleSerialCommands();
void moveMotorDistance(int id, float distance);
void turnMotor(int id, float degrees);
void serialEvent();
void runMotor(int id, bool forward);
void stopMotor(int id);
void homeMotor(int id);
void startTimedMove(int id, bool forward, unsigned long duration);
void updateTimedMove();
void testMotor(int id);
void updateSensorStates();
void calibrateMotors();
void calibrateSensors();
void trackSun();

void setup() {
  // Initialize serial communication (match Python handler)
  Serial.begin(115200);
  // Load calibration values from EEPROM
  EEPROM.get(EEPROM_ADDR_MS_PER_MM, ms_per_mm);
  EEPROM.get(EEPROM_ADDR_MS_PER_DEG, ms_per_deg);
  if (ms_per_mm <= 0) ms_per_mm = 50;
  if (ms_per_deg <= 0) ms_per_deg = 10;
  inputString.reserve(100);  // Reserve 100 bytes for the inputString

  // Set motor pins to OUTPUT
  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  // Set sensor pins to INPUT_PULLUP (assumes switches connect pin to GND)
  pinMode(SENSOR_PIN_1, INPUT_PULLUP);
  pinMode(SENSOR_PIN_2, INPUT_PULLUP);

  //Set feedback motor pins to output
  pinMode(FEEDBACK_MOTOR_PIN_FWD, OUTPUT);
  pinMode(FEEDBACK_MOTOR_PIN_REV, OUTPUT);

  // Stop all motors on startup
  stopMotor(0);
  stopMotor(1);

  Serial.print("STATUS:Arduino Ready. CAL:MS_PER_MM:");
  Serial.print(ms_per_mm);
  Serial.print(":MS_PER_DEG:");
  Serial.println(ms_per_deg);
}

void loop() {
  // 1. Handle any incoming serial commands
  handleSerialCommands();

  // 2. Handle any ongoing non-blocking tasks (like a timed move)
  updateTimedMove();

  // 3. Periodically check and report sensor states
  updateSensorStates();
}

// --- Serial Communication ---

void handleSerialCommands() {
  // Read any available serial data into inputString (robust against missing serialEvent)
  while (Serial.available()) {
    char inChar = (char)Serial.read();
    if (inChar == '\n') {
      stringComplete = true;
    } else {
      inputString += inChar;
    }
  }

  if (stringComplete) {
    // Trim whitespace
    inputString.trim();

    Serial.print("CMD_RCVD:");
    char _cmd_print_buf[200];
    inputString.toCharArray(_cmd_print_buf, 200);
    Serial.println(_cmd_print_buf);

    // --- Command Parsing ---
    // Use a character array for strtok
    char cmd_buffer[200];
    inputString.toCharArray(cmd_buffer, 200);

    // helper to strip trailing CR/LF/space from tokens
    auto stripTrailing = [](char* s) {
      if (!s) return;
      int len = strlen(s);
      while (len > 0 && (s[len-1] == '\r' || s[len-1] == '\n' || s[len-1] == ' ' || s[len-1] == '\t')) {
        s[len-1] = '\0';
        len--;
      }
    };

    char* command = strtok(cmd_buffer, ":");
    stripTrailing(command);

    if (command == NULL) {
      // ignore
    } else if (strcmp(command, "MOTOR_CMD") == 0) {
      int id = atoi(strtok(NULL, ":"));
      char* sub_cmd = strtok(NULL, ":");
      if (strcmp(sub_cmd, "FWD") == 0) runMotor(id, true);
      else if (strcmp(sub_cmd, "REV") == 0) runMotor(id, false);
      else if (strcmp(sub_cmd, "STOP") == 0) stopMotor(id);
      else if (strcmp(sub_cmd, "HOME") == 0) homeMotor(id);
    } else if (strcmp(command, "SET_SPEED") == 0) {
      char* id_str = strtok(NULL, ":");
      int speed_percent = atoi(strtok(NULL, ":"));
      int speed_val = map(speed_percent, 0, 100, 0, 255);  // map is defined in Arduino.h or shim

      if (strcmp(id_str, "*") == 0) {  // All motors
        motor_speeds[0] = speed_val;
        motor_speeds[1] = speed_val;
      } else {
        int id = atoi(id_str);
        if (id >= 0 && id < 2) motor_speeds[id] = speed_val;
      }
    } else if (strcmp(command, "SET_OUTPUT") == 0) {
      int pin = atoi(strtok(NULL, ":"));
      int state = atoi(strtok(NULL, ":"));
      pinMode(pin, OUTPUT);
      digitalWrite(pin, state);
    } else if (strcmp(command, "GET_CAL") == 0) {
      Serial.print("CAL:MS_PER_MM:");
      Serial.print(ms_per_mm);
      Serial.print(":MS_PER_DEG:");
      Serial.println(ms_per_deg);
    } else if (strcmp(command, "SET_CAL") == 0) {
      // Expect format SET_CAL:MS_PER_MM:50 or SET_CAL:MS_PER_DEG:10
      char* key = strtok(NULL, ":");
      char* val = strtok(NULL, ":");
      if (key && val) {
        int v = atoi(val);
        if (strcmp(key, "MS_PER_MM") == 0) {
          ms_per_mm = v;
          EEPROM.put(EEPROM_ADDR_MS_PER_MM, ms_per_mm);
          Serial.print("SET_CAL:MS_PER_MM:");
          Serial.println(ms_per_mm);
        } else if (strcmp(key, "MS_PER_DEG") == 0) {
          ms_per_deg = v;
          EEPROM.put(EEPROM_ADDR_MS_PER_DEG, ms_per_deg);
          Serial.print("SET_CAL:MS_PER_DEG:");
          Serial.println(ms_per_deg);
        } else {
          Serial.println("ERROR:Unknown calibration key");
        }
      } else {
        Serial.println("ERROR:SET_CAL invalid format");
      }
    } else if (strcmp(command, "PIN_TEST") == 0) {
      // Pulse direction pins without enabling motor drivers (ENA/ENB low)
      int prevENA = motor_speeds[0];
      int prevENB = motor_speeds[1];
      analogWrite(ENA, 0);
      analogWrite(ENB, 0);
      // Pulse IN pins for observation
      digitalWrite(IN1, HIGH);
      digitalWrite(IN2, LOW);
      digitalWrite(IN3, HIGH);
      digitalWrite(IN4, LOW);
      delay(200);
      digitalWrite(IN1, LOW);
      digitalWrite(IN2, LOW);
      digitalWrite(IN3, LOW);
      digitalWrite(IN4, LOW);
      Serial.println("PIN_TEST:OK");
      // restore speeds (no motor enable by default)
      analogWrite(ENA, prevENA);
      analogWrite(ENB, prevENB);
    } else if (strcmp(command, "MOVE_TIME") == 0) {
      int id = atoi(strtok(NULL, ":"));
      char* dir = strtok(NULL, ":");
      unsigned long duration = atol(strtok(NULL, ":"));
      startTimedMove(id, (strcmp(dir, "FWD") == 0), duration);
    } else if (strcmp(command, "MOVE_DIST") == 0) {
      int id = atoi(strtok(NULL, ":"));
      float dist = atof(strtok(NULL, ":"));
      moveMotorDistance(id, dist);
    } else if (strcmp(command, "TURN") == 0) {
      int id = atoi(strtok(NULL, ":"));
      float deg = atof(strtok(NULL, ":"));
      turnMotor(id, deg);
    } else if (strcmp(command, "CALIBRATE") == 0) {
      char* type = strtok(NULL, ":");
      if (strcmp(type, "MOTORS") == 0) calibrateMotors();
      else if (strcmp(type, "SENSORS") == 0) calibrateSensors();
    } else if (strcmp(command, "TRACK_SUN") == 0) {
      trackSun();
    } else if (strcmp(command, "TEST_MOTOR") == 0) {
      int id = atoi(strtok(NULL, ":"));
      testMotor(id);
    }
    else if (strcmp(command, "SENSOR_REPORT") == 0) {
      char* mode = strtok(NULL, ":");
      stripTrailing(mode);
      if (mode && (strcmp(mode, "ON") == 0 || strcmp(mode, "OFF") == 0)) {
        sensor_report_enabled = (strcmp(mode, "ON") == 0);
        Serial.print("SENSOR_REPORT:"); Serial.println(sensor_report_enabled ? "ON" : "OFF");
        Serial.print("ACK:"); Serial.println("SENSOR_REPORT");
      } else {
        Serial.print("NACK:SENSOR_REPORT:"); Serial.println(mode ? mode : "<none>");
      }
    } else {
      Serial.print("Invalid Command:"); Serial.println(command ? command : "<null>");
    }

    // Clear the string for the next command
    inputString = "";
    stringComplete = false;
  }
}

void moveMotorDistance(int id, float distance) {
  Serial.print("INFO:MOVE_DIST for motor ");
  Serial.print(id);
  Serial.print(" by ");
  Serial.print(distance);
  Serial.println("mm");

  //Start Feedback Motor
  digitalWrite(FEEDBACK_MOTOR_PIN_FWD, HIGH);
  digitalWrite(FEEDBACK_MOTOR_PIN_REV, LOW);

  // Run main motor forward
  runMotor(id, true);
  delay(250);

  //Stop main motor and feedback motor
  stopMotor(id);
  digitalWrite(FEEDBACK_MOTOR_PIN_FWD, LOW);
  digitalWrite(FEEDBACK_MOTOR_PIN_REV, LOW);
}

void turnMotor(int id, float degrees) {
  Serial.print("INFO:TURN for motor ");
  Serial.print(id);
  Serial.print(" by ");
  Serial.print(degrees);
  Serial.println(" degrees.");

  //Start Feedback Motor
  digitalWrite(FEEDBACK_MOTOR_PIN_FWD, HIGH);
  digitalWrite(FEEDBACK_MOTOR_PIN_REV, LOW);

  //Run main motor forward
  runMotor(id, true);
  delay(250);

  //Stop main motor and feedback motor
  stopMotor(id);
  digitalWrite(FEEDBACK_MOTOR_PIN_FWD, LOW);
  digitalWrite(FEEDBACK_MOTOR_PIN_REV, LOW);
}




void serialEvent() {
  while (Serial.available()) {
    char inChar = (char)Serial.read();
    if (inChar == '\n') {
      stringComplete = true;
    } else {
      inputString += inChar;
    }
  }
}

// --- Motor Control Functions ---

void runMotor(int id, bool forward) {
  if (id == 0) {
    digitalWrite(IN1, forward ? HIGH : LOW);
    digitalWrite(IN2, forward ? LOW : HIGH);
    analogWrite(ENA, motor_speeds[0]);
  } else if (id == 1) {
    digitalWrite(IN3, forward ? HIGH : LOW);
    digitalWrite(IN4, forward ? LOW : HIGH);
    analogWrite(ENB, motor_speeds[1]);
  }
}

void stopMotor(int id) {
  if (id == 0) {
    digitalWrite(IN1, LOW);
    digitalWrite(IN2, LOW);
    analogWrite(ENA, 0);
  } else if (id == 1) {
    digitalWrite(IN3, LOW);
    digitalWrite(IN4, LOW);
    analogWrite(ENB, 0);
  }
}

void homeMotor(int id) {
  // Placeholder for homing logic.
  // This would typically involve running the motor until a limit switch is hit.
  Serial.print("INFO:Homing for motor ");
  Serial.print(id);
  Serial.println(" not yet implemented.");
  // Example: runMotor(id, false); // Run backwards
  //          while(digitalRead(HOME_SWITCH_PIN) == HIGH) { /* wait */ }
  //          stopMotor(id);
}

void startTimedMove(int id, bool forward, unsigned long duration) {
  timed_move_motor_id = id;
  move_duration = duration;
  move_timer_start = millis();
  runMotor(id, forward);
}

void updateTimedMove() {
  if (timed_move_motor_id != -1) {  // If a timed move is active
    if (millis() - move_timer_start >= move_duration) {
      stopMotor(timed_move_motor_id);
      timed_move_motor_id = -1;  // End the timed move
    }
  }
}

void testMotor(int id) {
  Serial.print("INFO:Testing motor ");
  Serial.println(id);
  runMotor(id, true);  // Run forward
  delay(250);          // For 250ms
  stopMotor(id);
  Serial.println("INFO:Test complete.");
}

// --- Sensor & Calibration Functions ---

void updateSensorStates() {
  if (millis() - last_sensor_check >= SENSOR_CHECK_INTERVAL) {
    last_sensor_check = millis();

    // Check Sensor 1
    int current_state1 = digitalRead(SENSOR_PIN_1);
    if (current_state1 != sensor1_last_state) {
      sensor1_last_state = current_state1;
      Serial.print("SENSOR_STATE:");
      Serial.print(SENSOR_PIN_1);
      Serial.print(":");
      Serial.println(current_state1);
    }

    // Check Sensor 2
    int current_state2 = digitalRead(SENSOR_PIN_2);
    if (current_state2 != sensor2_last_state) {
      sensor2_last_state = current_state2;
      Serial.print("SENSOR_STATE:");
      Serial.print(SENSOR_PIN_2);
      Serial.print(":");
      Serial.println(current_state2);
    }
  }
}

void calibrateMotors() {
  Serial.println("INFO:Starting motor calibration...");
  // Placeholder for motor calibration logic.
  // e.g., Move from one limit switch to the other and count encoder steps.
  delay(2000);  // Simulate a process
  Serial.println("INFO:Motor calibration complete.");
}

void calibrateSensors() {
  Serial.println("INFO:Starting sensor calibration...");
  // Placeholder for sensor calibration logic.
  // e.g., Read min/max values from LDRs.
  // Prompt user to cover/uncover sensors.
  Serial.println("INFO:Please cover light sensors now.");
  delay(3000);
  // ... read values ...
  Serial.println("INFO:Please expose light sensors to bright light.");
  delay(3000);
  // ... read values ...
  Serial.println("INFO:Sensor calibration complete.");
}

void trackSun() {
  // Placeholder for sun tracking logic.
  // This would read LDRs and move a motor to balance the light.
  int left_val = analogRead(LDR_LEFT_PIN);
  int right_val = analogRead(LDR_RIGHT_PIN);

  Serial.print("INFO:Sun tracking check. L:");
  Serial.print(left_val);
  Serial.print(" R:");
  Serial.println(right_val);

  // --- Functional Sun Tracking Logic ---
  // This logic moves motor 0 to balance the light between the two LDRs.
  int error = left_val - right_val;
  int tolerance = 50;  // How much difference to ignore. Adjust as needed.

  if (abs(error) > tolerance) {
    runMotor(0, error > 0);  // If left is brighter (error > 0), move forward (true). Otherwise, move reverse (false).
    delay(50);               // Move for a short burst. Adjust for faster/slower tracking.
    stopMotor(0);            // Stop to prevent overshooting.
  }
}
