# GazeLink

**GazeLink** is an assistive eye-gaze communication system designed to help people with severe speech or motor impairments communicate using only their eye movements.

The system uses a standard laptop or desktop webcam to track the user's eyes, determine gaze direction, and allow interaction with a large on-screen keyboard and communication interface.

## Features

* Real-time eye and iris tracking using a standard webcam
* Gaze-based cursor/selection control
* 9-point calibration for individual users
* Dwell-time based selection without requiring a mouse
* Large, patient-friendly virtual keyboard
* Word and phrase suggestions
* Emergency communication phrases such as **Help, Water, Food, Toilet, and Pain**
* Text-to-speech output
* English and Kannada language support
* Offline/local operation for core functionality
* Adjustable interaction settings
* Persistent calibration and user settings

## Technology Stack

* **Python**
* **OpenCV**
* **MediaPipe Face Mesh**
* **NumPy**
* **Tkinter**
* **Text-to-Speech**
* **PyInstaller**

## How It Works

1. The webcam captures the user's face and eye movements.
2. MediaPipe detects facial and eye landmarks.
3. Gaze direction is estimated from the eye and iris positions.
4. The user completes a calibration process to adapt the system to their webcam and position.
5. The user looks at a keyboard key or interface option.
6. After maintaining their gaze for the required dwell time, the selected option is activated.
7. Typed text or predefined phrases can be converted into speech.

## Download for Windows

The latest Windows version is available from the **Releases** section of this repository.

Download:

**GazeLink-Windows-v1.0.zip**

After downloading:

1. Extract the ZIP file.
2. Open the extracted `GazeLink` folder.
3. Run `GazeLink.exe`.
4. Complete the calibration process on the laptop where GazeLink will be used.
5. Start communicating using gaze-based interaction.

> **Important:** Calibration is device and setup dependent. Users should calibrate GazeLink on their own laptop or desktop before use.

## System Requirements

* Windows PC or laptop
* Working webcam
* Python is **not required** for the packaged Windows release
* Sufficient lighting for reliable eye tracking
* The user should be positioned at a suitable distance from the webcam

## Project Structure

```text
GazeLink/
├── core/
│   ├── eye_tracker.py
│   ├── smoother.py
│   ├── calibration.py
│   ├── word_predictor.py
│   └── __init__.py
│
├── ui/
│   ├── calibration_screen.py
│   ├── keyboard.py
│   ├── output_box.py
│   └── __init__.py
│
├── utils/
│   ├── kannada_neural_tts.py
│   ├── language_manager.py
│   ├── runtime.py
│   ├── tts.py
│   └── __init__.py
│
├── config.py
├── main.py
├── requirements.txt
└── user_settings.json
```

## Development

To run the project from source, install the dependencies listed in `requirements.txt` and run:

```bash
python main.py
```

The packaged Windows release does not require a separate Python installation.

## Calibration

GazeLink uses a calibration process to map the user's eye movements to the application's interaction areas.

For reliable operation:

* Perform calibration on the same device that will be used for communication.
* Maintain a comfortable and consistent sitting position.
* Keep the face visible to the webcam.
* Use suitable lighting.
* Avoid significant changes in camera position after calibration.

## Project Goal

The goal of GazeLink is to provide a practical, accessible, and low-cost communication method for users who may have difficulty communicating through conventional speech or physical input devices.

The system is designed around commonly available computer hardware rather than specialized eye-tracking equipment.

## License

This project is developed as an academic major project.
