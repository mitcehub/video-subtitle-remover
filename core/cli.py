"""命令行入口：解析参数并启动字幕去除。"""

import argparse
import logging
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.config import config, tr, TRANSLATION_FILE
from infra.utils import VIDEO_EXTENSIONS
from core.subtitle_remover import SubtitleRemover

logger = logging.getLogger('cli')


def parse_args():
    """解析命令行参数并校验合法性。"""
    parser = argparse.ArgumentParser(description='video subtitle remover CLI')
    parser.add_argument('input', help='Input video path')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('--subtitle-area', type=str, default=None,
                        help='Subtitle area coords: ymin,ymax,xmin,xmax (values in [0,1])')
    args = parser.parse_args()

    # 校验输入文件存在
    args.input = os.path.realpath(args.input)
    if not os.path.isfile(args.input):
        print(f'Error: input file not found: {args.input}')
        sys.exit(-1)

    # 校验输出路径目录可写
    if args.output:
        args.output = os.path.realpath(args.output)
        out_dir = os.path.dirname(args.output)
        if out_dir and not os.path.isdir(out_dir):
            print(f'Error: output directory not found: {out_dir}')
            sys.exit(-1)

    # 校验 subtitle-area：必须恰好 4 个值，且均在 [0,1]
    if args.subtitle_area:
        try:
            parts = [float(x.strip()) for x in args.subtitle_area.split(',')]
        except ValueError:
            print(f'Error: --subtitle-area values must be numbers, got: {args.subtitle_area!r}')
            sys.exit(-1)
        if len(parts) != 4:
            print(f'Error: --subtitle-area requires exactly 4 values (ymin,ymax,xmin,xmax), got {len(parts)}')
            sys.exit(-1)
        if any(not (0.0 <= v <= 1.0) for v in parts):
            print(f'Error: --subtitle-area values must be in [0,1], got: {parts}')
            sys.exit(-1)
        args.subtitle_area_coords = [(parts[0], parts[1], parts[2], parts[3])]
    else:
        args.subtitle_area_coords = []
    return args


def main():
    """CLI 主入口：配置日志、加载配置、启动字幕去除。"""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)-5s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('torch').setLevel(logging.WARNING)

    multiprocessing.set_start_method("spawn")
    args = parse_args()
    logger.info('cli_start: input=%s', args.input)
    config.set(config.interface, 'en')
    tr.read(TRANSLATION_FILE, encoding='utf-8')

    ext = os.path.splitext(args.input)[-1].lower()
    if ext not in VIDEO_EXTENSIONS:
        print(f'Error: {args.input} is not a supported video file.')
        sys.exit(-1)

    sr = SubtitleRemover(args.input)
    sr.sub_areas = args.subtitle_area_coords
    if args.output:
        sr.video_out_path = args.output
    sr.run()
    logger.info('cli_end: output=%s', sr.video_out_path)


if __name__ == '__main__':
    main()
