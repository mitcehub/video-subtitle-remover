[简体中文](README.md) | English

## Project Introduction

![python version](https://img.shields.io/badge/Python-3.12+-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows/macOS/Linux-green.svg)

Video-subtitle-remover (VSR) is an AI-based software that removes hardcoded subtitles from videos.
It mainly implements the following functionalities:
- Lossless resolution removal of hardcoded subtitles from videos
- AI-powered inpainting to fill removed subtitle areas
- Custom subtitle position selection
- Batch watermark removal from images
- Visual subtitle area selection by dragging on video preview
- Real-time original/result comparison during processing
- Timeline editing with track-based precise control
- Playback controls with frame-by-frame navigation and variable speed
- Modern PyQt6 UI with automatic system theme support
- UI settings panel for STTN parameter adjustment without editing config files

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
