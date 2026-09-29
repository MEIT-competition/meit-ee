# Legacy / reference only

Everything in this directory belongs to previous MEIT EE prototypes and is **not part of the current runtime build**.

Archived material includes:

- ESP32 microphone capture / stereo I2S
- GCC-PHAT / TDoA direction estimation
- BLE audio streaming to the laptop
- old laptop BLE receiver and AI bridge
- old firmware host tests and microphone bring-up tools
- four-microphone / eight-motor schematics
- previous browser display/debug UI

Current runtime:

```text
iPhone(s)
-> meit-ios Windows bridge + meit-ai
-> laptop/ios_motor_bridge.py
-> BLE CMD v2
-> ESP32
-> two vibration motors
```

Do not import, build, or run legacy code together with the current runtime unless you are intentionally reproducing an older prototype.
