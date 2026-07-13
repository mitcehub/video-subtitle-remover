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
    parser = argparse.ArgumentParser(description='video subtitle remover CLI')
    parser.add_argument('input', help='Input video path')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('--subtitle-area', type=str, default=None,
                        help='Subtitle area coords: ymin,ymax,xmin,xmax')
    args = parser.parse_args()
    if args.subtitle_area:
        parts = [float(x.strip()) for x in args.subtitle_area.split(',')]
        args.subtitle_area_coords = [(parts[0], parts[1], parts[2], parts[3])]
    else:
        args.subtitle_area_coords = []
    return args


def main():
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
