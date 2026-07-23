"""STTN 去字幕模型定义与推理引擎。"""

import os
import math
import time
import gc
import logging
from typing import List

import cv2
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from tqdm import tqdm

from infra.config import config
from infra.utils import is_frame_in_sections
from core.inpaint.mask import get_inpaint_area_by_mask, get_tight_inpaint_area, create_mask
from core.video_io.video_reader import FramePrefetcher, create_video_capture
from infra.hardware import HardwareAccelerator

logger = logging.getLogger(__name__)

# STTN 模型输入尺寸
MODEL_W = 640
MODEL_H = 120
# split_h 计算系数：split_h = int(W * SPLIT_H_RATIO / SPLIT_H_DIVISOR)
SPLIT_H_NUMERATOR = 3
SPLIT_H_DENOMINATOR = 16
# GC 间隔：每 N 个 chunk 执行一次 gc.collect + empty_cache
GC_INTERVAL = 10


# ============================================================================
# 模型组件
# ============================================================================

def _init_weights(module, gain=0.02):
    classname = module.__class__.__name__
    if 'InstanceNorm2d' in classname:
        if hasattr(module, 'weight') and module.weight is not None:
            nn.init.constant_(module.weight.data, 1.0)
        if hasattr(module, 'bias') and module.bias is not None:
            nn.init.constant_(module.bias.data, 0.0)
    elif hasattr(module, 'weight') and ('Conv' in classname or 'Linear' in classname):
        nn.init.normal_(module.weight.data, 0.0, gain)
        if hasattr(module, 'bias') and module.bias is not None:
            nn.init.constant_(module.bias.data, 0.0)


class Attention(nn.Module):
    def forward(self, query, key, value, m=None):
        scores = torch.matmul(query, key.transpose(-2, -1)) / math.sqrt(query.size(-1))
        if m is not None:
            scores.masked_fill(m, torch.finfo(scores.dtype).min)
        p_attn = F.softmax(scores, dim=-1)
        return torch.matmul(p_attn, value), p_attn


class MultiHeadedAttention(nn.Module):
    def __init__(self, patch_size, d_model):
        super().__init__()
        self.patch_size = patch_size
        self.query_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.value_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.key_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.output_linear = nn.Sequential(
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True))
        self.attention = Attention()

    def forward(self, x, m=None, b=1, c=256):
        bt, _, h, w = x.size()
        t = bt // b
        d_k = c // len(self.patch_size)
        output = []
        _query = self.query_embedding(x)
        _key = self.key_embedding(x)
        _value = self.value_embedding(x)
        for (width, height), query, key, value in zip(self.patch_size,
                                                       torch.chunk(_query, len(self.patch_size), dim=1),
                                                       torch.chunk(_key, len(self.patch_size), dim=1),
                                                       torch.chunk(_value, len(self.patch_size), dim=1)):
            out_w, out_h = w // width, h // height
            if m is not None:
                mm = m.view(b, t, 1, out_h, height, out_w, width)
                mm = mm.permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, height * width)
                mm = (mm.mean(-1) > 0.5 + 1e-7).unsqueeze(1).expand(-1, t * out_h * out_w, -1)
            else:
                mm = None
            query = query.view(b, t, d_k, out_h, height, out_w, width).permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, d_k * height * width)
            key = key.view(b, t, d_k, out_h, height, out_w, width).permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, d_k * height * width)
            value = value.view(b, t, d_k, out_h, height, out_w, width).permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, d_k * height * width)
            y, _ = self.attention(query, key, value, mm)
            y = y.view(b, t, out_h, out_w, d_k, height, width).permute(0, 1, 4, 2, 5, 3, 6).contiguous().view(bt, d_k, h, w)
            output.append(y)
        return self.output_linear(torch.cat(output, 1))


class FeedForward(nn.Module):
    def __init__(self, d_model):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=2, dilation=2),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True))

    def forward(self, x):
        return self.conv(x)


class TransformerBlock(nn.Module):
    def __init__(self, patchsize, hidden=256):
        super().__init__()
        self.attention = MultiHeadedAttention(patchsize, d_model=hidden)
        self.feed_forward = FeedForward(hidden)

    def forward(self, x):
        x_val, b, c = x['x'], x['b'], x['c']
        m = x.get('m')
        x_val = x_val + self.attention(x_val, m, b, c)
        x_val = x_val + self.feed_forward(x_val)
        return {'x': x_val, 'm': m, 'b': b, 'c': c}


class Deconv(nn.Module):
    def __init__(self, in_ch, out_ch, kernel_size=3, padding=0):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size=kernel_size, stride=1, padding=padding)

    def forward(self, x):
        return self.conv(F.interpolate(x, scale_factor=2, mode='bilinear', align_corners=True))


class TextPriorHead(nn.Module):
    def __init__(self, in_dim=256, hidden_dim=64):
        super().__init__()
        self.conv1 = nn.Conv2d(in_dim + 3, hidden_dim, 3, 1, 1)
        self.conv2 = nn.Conv2d(hidden_dim, hidden_dim, 3, 1, 1)
        self.conv3 = nn.Conv2d(hidden_dim, hidden_dim, 3, 1, 1)
        self.deconv1 = Deconv(hidden_dim, hidden_dim // 2, padding=1)
        self.deconv2 = Deconv(hidden_dim // 2, 1, padding=1)

    def forward(self, x, rgb=None):
        if rgb is not None:
            rgb = F.interpolate(rgb, size=x.shape[-2:], mode='bilinear', align_corners=False)
            x = torch.cat([x, rgb], dim=1)
        x = F.leaky_relu(self.conv1(x), 0.2)
        x = F.leaky_relu(self.conv2(x), 0.2)
        x = F.leaky_relu(self.conv3(x), 0.2)
        return self.deconv2(self.deconv1(x))


class SubtitleRemovalGenerator(nn.Module):
    def __init__(self):
        super().__init__()
        channel = 256
        patch_size = [(80, 15), (32, 6), (10, 5), (5, 3)]
        self.transformer = nn.Sequential(*[TransformerBlock(patch_size, hidden=channel) for _ in range(8)])
        self.encoder = nn.Sequential(
            nn.Conv2d(3, 64, 3, 2, 1), nn.LeakyReLU(0.2, True),
            nn.Conv2d(64, 64, 3, 1, 1), nn.LeakyReLU(0.2, True),
            nn.Conv2d(64, 128, 3, 2, 1), nn.LeakyReLU(0.2, True),
            nn.Conv2d(128, channel, 3, 1, 1), nn.LeakyReLU(0.2, True),
        )
        self.decoder = nn.Sequential(
            Deconv(channel, 128, 3, 1), nn.LeakyReLU(0.2, True),
            nn.Conv2d(128, 64, 3, 1, 1), nn.LeakyReLU(0.2, True),
            Deconv(64, 64, 3, 1), nn.LeakyReLU(0.2, True),
            nn.Conv2d(64, 3, 3, 1, 1)
        )
        self.prior_head = TextPriorHead(in_dim=channel, hidden_dim=64)
        self.apply(_init_weights)


# ============================================================================
# 推理引擎
# ============================================================================

class STTNInpaint:
    def __init__(self, device, model_path):
        self.device = device
        self.model = SubtitleRemovalGenerator().to(device)
        ckpt = torch.load(model_path, map_location='cpu', weights_only=True)
        self.model.load_state_dict(ckpt.get('netG', ckpt), strict=False)
        self.model.eval()
        self.neighbor_stride = config.sttnNeighborStride.value
        self.ref_length = config.sttnReferenceLength.value
        logger.info('sttn_model_loaded: device=%s, neighbor_stride=%d, ref_length=%d',
                 device, self.neighbor_stride, self.ref_length)

    def __call__(self, input_frames: List[np.ndarray], input_mask: np.ndarray,
                 tight_areas: List[tuple] | None = None):
        """执行 STTN 推理。

        Args:
            input_frames: 原始帧列表 (BGR)
            input_mask: 二值遮罩
            tight_areas: 可选紧凑裁剪区域列表 [(ymin, ymax, xmin, xmax)]。
                         提供时使用 tight crop 替代全宽 crop。
        """
        _, mask = cv2.threshold(input_mask, 127, 1, cv2.THRESH_BINARY)
        mask = mask[:, :, None]
        H, W = mask.shape[:2]

        if tight_areas:
            inpaint_area = tight_areas
            use_tight = True
            logger.info('sttn_inpaint: using tight crop, %d areas', len(tight_areas))
        else:
            split_h = int(W * SPLIT_H_NUMERATOR / SPLIT_H_DENOMINATOR)
            inpaint_area = get_inpaint_area_by_mask(W, H, split_h, mask)
            use_tight = False

        frames_hr = [f.copy() for f in input_frames]
        frames_scaled = {}
        comps = {}

        for j, image in enumerate(frames_hr):
            for k, area in enumerate(inpaint_area):
                if use_tight:
                    crop = image[area[0]:area[1], area[2]:area[3], :]
                else:
                    crop = image[area[0]:area[1], :, :]
                frames_scaled.setdefault(k, []).append(cv2.resize(crop, (MODEL_W, MODEL_H)))

        for k in frames_scaled:
            comps[k] = self._inpaint(frames_scaled[k])

        if not inpaint_area:
            return frames_hr

        result = []
        for j, frame in enumerate(frames_hr):
            for k, area in enumerate(inpaint_area):
                area_h = area[1] - area[0]
                if use_tight:
                    area_w = area[3] - area[2]
                    comp = cv2.resize(comps[k][j], (area_w, area_h))
                    comp = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR)
                    m = mask[area[0]:area[1], area[2]:area[3], :]
                    frame[area[0]:area[1], area[2]:area[3], :] = m * comp + (1 - m) * frame[area[0]:area[1], area[2]:area[3], :]
                else:
                    comp = cv2.resize(comps[k][j], (W, area_h))
                    comp = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR)
                    m = mask[area[0]:area[1], :]
                    frame[area[0]:area[1], :, :] = m * comp + (1 - m) * frame[area[0]:area[1], :, :]
            result.append(frame)
        return result

    def _get_ref_index(self, neighbor_ids, length):
        return [i for i in range(0, length, self.ref_length) if i not in neighbor_ids]

    def _inpaint(self, frames: List[np.ndarray]):
        frame_length = len(frames)
        rgb_frames = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 for f in frames]
        chunk_t = torch.from_numpy(np.stack(rgb_frames)).float().permute(0, 3, 1, 2).unsqueeze(0).to(self.device) * 2 - 1

        comp_frames = [None] * frame_length
        weights = [0] * frame_length

        with torch.no_grad():
            frames_flat = chunk_t.view(-1, 3, MODEL_H, MODEL_W)

            # 新模型：单次编码（不再 masking input 后二次编码）
            enc_feat = self.model.encoder(frames_flat)
            prior_logits = self.model.prior_head(enc_feat, rgb=frames_flat)
            _, c_feat, h_feat, w_feat = enc_feat.shape

            for f in range(0, frame_length, self.neighbor_stride):
                neighbor_ids = list(range(max(0, f - self.neighbor_stride), min(frame_length, f + self.neighbor_stride + 1)))
                l_t = len(neighbor_ids)
                all_ids = neighbor_ids + self._get_ref_index(neighbor_ids, frame_length)

                feat_win = enc_feat[all_ids]
                # 新模型：transformer 无 attention mask
                feat_win = self.model.transformer({'x': feat_win, 'b': 1, 'c': c_feat})['x']
                output = torch.tanh(self.model.decoder(feat_win))

                prior_np = torch.sigmoid(prior_logits[all_ids])[:, 0].cpu().numpy()
                for i in range(l_t):
                    idx = neighbor_ids[i]
                    np_out = output[i].cpu().permute(1, 2, 0).numpy() * 0.5 + 0.5
                    np_prior = (prior_np[i] > 0.7).astype(np.float32)[..., None]
                    blended = np_prior * np_out + (1 - np_prior) * rgb_frames[idx]
                    blended = blended.clip(0, 1).astype(np.float32)
                    if comp_frames[idx] is None:
                        comp_frames[idx] = blended
                        weights[idx] = 1
                    else:
                        comp_frames[idx] += blended
                        weights[idx] += 1

        return [
            (comp_frames[i] / weights[i] * 255).astype(np.uint8) if comp_frames[i] is not None
            else cv2.cvtColor(frames[i], cv2.COLOR_BGR2RGB)
            for i in range(frame_length)
        ]


class STTNAutoInpaint:
    def __init__(self, device, model_path, video_path, clip_gap=None):
        self.sttn_inpaint = STTNInpaint(device, model_path)
        self.video_path = video_path
        self.video_out_path = os.path.join(
            os.path.dirname(os.path.abspath(video_path)),
            f"{os.path.basename(video_path).rsplit('.', 1)[0]}_no_sub.mp4"
        )
        raw_max_load = config.sttnMaxLoadNum.value
        min_chunk = config.sttnNeighborStride.value * config.sttnReferenceLength.value
        self.clip_gap = max(raw_max_load, min_chunk)
        if self.clip_gap > raw_max_load:
            logger.info('sttn_max_load_num: user=%d raised to %d (min_chunk=neighbor_stride*ref_length=%d*%d=%d)',
                        raw_max_load, self.clip_gap,
                        config.sttnNeighborStride.value, config.sttnReferenceLength.value, min_chunk)

    def __call__(self, input_mask=None, input_sub_remover=None, tbar=None, gui_mode=False):
        reader = None
        writer = None
        prefetcher = None
        total_written = 0
        try:
            reader = create_video_capture(self.video_path)
            prefetcher = FramePrefetcher(reader)
            frame_info = {
                'W': int(reader.get(cv2.CAP_PROP_FRAME_WIDTH) + 0.5),
                'H': int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT) + 0.5),
                'fps': reader.get(cv2.CAP_PROP_FPS),
                'len': int(reader.get(cv2.CAP_PROP_FRAME_COUNT) + 0.5)
            }

            if input_sub_remover is not None:
                ab_sections = input_sub_remover.ab_sections
                track_data = getattr(input_sub_remover, 'track_data', None)
                sub_areas = getattr(input_sub_remover, 'sub_areas', None)
                writer = input_sub_remover.video_writer
            else:
                ab_sections = track_data = sub_areas = None
                writer = cv2.VideoWriter(self.video_out_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                         frame_info['fps'], (frame_info['W'], frame_info['H']))

            split_h = int(frame_info['W'] * SPLIT_H_NUMERATOR / SPLIT_H_DENOMINATOR)
            if input_mask is None:
                # 无 mask 时用空 mask（主流程总会传入 input_mask）
                _, global_mask = cv2.threshold(
                    np.zeros((frame_info['H'], frame_info['W']), dtype=np.uint8),
                    127, 1, cv2.THRESH_BINARY)
                global_mask = global_mask[:, :, None]
            else:
                _, global_mask = cv2.threshold(input_mask, 127, 1, cv2.THRESH_BINARY)
                global_mask = global_mask[:, :, None]

            # 如果 sub_areas 有原始坐标，优先使用紧凑裁剪（保留用户框选的 xmin/xmax）
            use_tight = bool(sub_areas)
            if use_tight:
                global_area = get_tight_inpaint_area(sub_areas, frame_info['W'], frame_info['H'])
                logger.info('sttn_auto: using tight crop from sub_areas, %d areas', len(global_area))
            else:
                global_area = get_inpaint_area_by_mask(frame_info['W'], frame_info['H'], split_h, global_mask)
                logger.info('sttn_auto: using full-width crop from mask, %d areas', len(global_area))

            effective_gap = self.clip_gap
            vram = HardwareAccelerator.instance().get_available_vram_mb()
            if vram > 0 and frame_info['W'] > 0 and frame_info['H'] > 0:
                max_frames = max(int(vram * 1024 * 1024 / (frame_info['W'] * frame_info['H'] * 12)), 10)
                capped = min(effective_gap, max_frames)
                if capped < effective_gap:
                    logger.info('sttn_max_load_num: user=%d capped by vram=%dMB (%dx%d frame)',
                                effective_gap, vram, frame_info['W'], frame_info['H'])
                    effective_gap = capped

            total_chunks = (frame_info['len'] + effective_gap - 1) // effective_gap
            total_written = 0

            for i in range(total_chunks):
                start_f = i * effective_gap
                end_f = min((i + 1) * effective_gap, frame_info['len'])
                tqdm.write(f'Processing: {start_f + 1} - {end_f} / Total: {frame_info["len"]}')

                chunk_mask = global_mask
                chunk_area = global_area
                chunk_tight = use_tight
                if track_data:
                    areas = [(t["ymin"], t["ymax"], t["xmin"], t["xmax"])
                             for t in track_data if t["start"] <= end_f and t["end"] >= start_f + 1]
                    if areas:
                        if use_tight:
                            # track_data 有原始坐标时用紧凑裁剪
                            chunk_area = get_tight_inpaint_area(areas, frame_info['W'], frame_info['H'])
                        else:
                            m = create_mask((frame_info['H'], frame_info['W']), areas)
                            _, m = cv2.threshold(m, 127, 1, cv2.THRESH_BINARY)
                            chunk_mask = m[:, :, None]
                            chunk_area = get_inpaint_area_by_mask(frame_info['W'], frame_info['H'], split_h, chunk_mask)
                    else:
                        chunk_area = []
                        chunk_tight = False

                frames_hr, frames, comps = [], {}, {k: [] for k in range(len(chunk_area))}
                valid_count = 0
                read_failed = False

                for j in range(start_f, end_f):
                    ok, image = prefetcher.read()
                    if not ok:
                        read_failed = True
                        break
                    frames_hr.append(image)
                    valid_count += 1
                    if is_frame_in_sections(j, ab_sections):
                        for k in range(len(chunk_area)):
                            a = chunk_area[k]
                            if chunk_tight:
                                frames.setdefault(k, []).append(cv2.resize(image[a[0]:a[1], a[2]:a[3], :], (MODEL_W, MODEL_H)))
                            else:
                                frames.setdefault(k, []).append(cv2.resize(image[a[0]:a[1], :], (MODEL_W, MODEL_H)))

                if valid_count == 0:
                    if read_failed:
                        break
                    continue

                for k in range(len(chunk_area)):
                    comps[k] = self.sttn_inpaint._inpaint(frames[k]) if frames.get(k) else []

                if chunk_area:
                    processed_map = {}
                    idx = 0
                    for j in range(start_f, end_f):
                        if j - start_f < valid_count and is_frame_in_sections(j, ab_sections):
                            processed_map[j - start_f] = idx
                            idx += 1

                    for j in range(valid_count):
                        original = frames_hr[j].copy() if gui_mode else None
                        frame = frames_hr[j]
                        if j in processed_map:
                            ci = processed_map[j]
                            for k in range(len(chunk_area)):
                                if ci < len(comps[k]):
                                    a = chunk_area[k]
                                    area_h = a[1] - a[0]
                                    if chunk_tight:
                                        area_w = a[3] - a[2]
                                        comp = cv2.resize(comps[k][ci], (area_w, area_h))
                                        comp = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR)
                                        m = chunk_mask[a[0]:a[1], a[2]:a[3], :]
                                        frame[a[0]:a[1], a[2]:a[3], :] = m * comp + (1 - m) * frame[a[0]:a[1], a[2]:a[3], :]
                                    else:
                                        comp = cv2.resize(comps[k][ci], (frame_info['W'], area_h))
                                        comp = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR)
                                        m = chunk_mask[a[0]:a[1], :]
                                        frame[a[0]:a[1], :, :] = m * comp + (1 - m) * frame[a[0]:a[1], :, :]
                        writer.write(frame)
                        total_written += 1
                        if input_sub_remover:
                            if tbar:
                                input_sub_remover.update_progress(tbar, 1)
                            if original is not None and gui_mode:
                                input_sub_remover._emit_preview(original, frame)
                else:
                    for j in range(valid_count):
                        writer.write(frames_hr[j])
                        total_written += 1
                        if input_sub_remover:
                            if tbar:
                                input_sub_remover.update_progress(tbar, 1)
                            if gui_mode:
                                input_sub_remover._emit_preview(frames_hr[j], frames_hr[j])

                if read_failed:
                    break
                del frames_hr, frames, comps
                if i % GC_INTERVAL == 0:
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
        except Exception as e:
            logger.error('sttn_process_error: %s', e)
            raise
        finally:
            if prefetcher is not None:
                prefetcher.release()
            if writer and input_sub_remover is None:
                writer.release()
            logger.info('sttn_process_end: written=%d', total_written)
