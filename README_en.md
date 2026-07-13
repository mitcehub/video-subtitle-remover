[简体中文](README.md) | English

## Project Introduction

![python version](https://img.shields.io/badge/Python-3.12+-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows/macOS/Linux-green.svg)

Video-subtitle-remover (VSR) is an AI-based software that removes hardcoded subtitles from videos.
It mainly implements the following functionalities:
- 🎨 **Modern PyQt6 + qfluentwidgets UI** — Supports Windows 11 Mica effect and automatic system theme detection
- 🖱️ **Visual subtitle area selection** — Drag and select subtitle regions directly on the video preview
- ⏱️ **Timeline editing** — Track-based timeline for precise control over subtitle start/end ranges
- 🔄 **Real-time original/result comparison** — Preview removal results during processing with comparison mode
- 🎮 **Playback controls** — Frame-by-frame navigation, variable speed playback, keyboard shortcuts
- ⚙️ **UI settings panel** — Adjust STTN parameters through the UI without manually editing config files
- **Lossless resolution**: Removes hardcoded subtitles from videos and generates files without subtitles
- Fills in the removed subtitle text area using a powerful AI algorithm model
- Supports custom subtitle positions by only removing subtitles in the defined location
- Supports automatic removal of all text throughout the entire video
- Supports multi-selection of images for batch removal of watermark text

![Screenshot](doc/Screenshot.png)

## Source Code Usage Instructions

#### 1. Install Python

Please ensure that you have installed Python 3.12+.

- Windows: [Python official website](https://www.python.org/downloads/windows/)
- MacOS:
  ```shell
  brew install python@3.12
  ```
- Linux (Ubuntu/Debian):
  ```shell
  sudo apt update && sudo apt install python3.12 python3.12-venv python3.12-dev
  ```

#### 2. Install Dependencies

Create and activate the virtual environment:
```shell
python -m venv videoEnv
```

- Windows:
```shell
videoEnv\Scripts\activate
```
- MacOS/Linux:
```shell
source videoEnv/bin/activate
```

#### 3. Change to project directory

```shell
cd <project_directory>
```

#### 4. Install Runtime Environment

##### (1) CUDA (NVIDIA GPU)

- Install PaddlePaddle GPU version (CUDA 11.8):
  ```shell
  pip install paddlepaddle-gpu==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
  ```
- Install Torch GPU version (CUDA 11.8):
  ```shell
  pip install torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu118
  ```
- Install other dependencies:
  ```shell
  pip install -r requirements.txt
  ```

##### (2) DirectML (AMD/Intel GPU)

```shell
pip install paddlepaddle==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install -r requirements.txt
pip install torch_directml==0.2.5.dev240914
```

##### (3) CPU Only

```shell
pip install paddlepaddle==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install torch==2.7.0 torchvision==0.22.0
pip install -r requirements.txt
```

##### (4) macOS (Apple Silicon)

```shell
pip install paddlepaddle==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install torch==2.7.0 torchvision==0.22.0
pip install -r requirements.txt
```

#### 5. Run

- GUI:
```shell
python gui.py
```

## Acknowledgements

This program uses part of the code from [YaoFANGUK/video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover). Thanks to the original author for their contribution.
