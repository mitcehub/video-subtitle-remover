import logging
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# mask 扩展像素（向四周扩展，确保覆盖字幕边缘）
MASK_DEVIATION = 10
# 过滤小连通域的最小面积阈值
MIN_ISLAND_AREA = 10


def create_mask(size, coords_list):
    """根据坐标列表生成 mask。

    Args:
        size: mask 尺寸 (H, W)
        coords_list: 坐标列表，每个元素为 (ymin, ymax, xmin, xmax)
    """
    mask = np.zeros(size, dtype="uint8")
    if coords_list:
        for coords in coords_list:
            ymin, ymax, xmin, xmax = coords
            x1 = max(0, xmin - MASK_DEVIATION)
            y1 = max(0, ymin - MASK_DEVIATION)
            x2 = xmax + MASK_DEVIATION
            y2 = ymax + MASK_DEVIATION
            cv2.rectangle(mask, (x1, y1), (x2, y2), (255, 255, 255), thickness=-1)
    area_px = int(np.sum(mask > 0))
    total_px = size[0] * size[1]
    coverage = area_px / total_px if total_px > 0 else 0.0
    logger.info('create_mask: boxes=%d, area=%d, coverage=%.6f', len(coords_list), area_px, coverage)
    return mask


# 紧凑裁剪参数（参考 sttn-auto get_crop_region）
TIGHT_PADDING = 30       # bbox 四周扩展像素数
TIGHT_MIN_ASPECT = 3.0   # 最小宽高比（防止窄框导致极端变形）
TIGHT_ALIGN = 8          # 对齐倍数（兼容 encoder 4x 下采样 + patchsize）


def get_tight_inpaint_area(coords_list, W, H, padding=TIGHT_PADDING,
                            min_aspect=TIGHT_MIN_ASPECT, align=TIGHT_ALIGN):
    """围绕用户选择框计算紧凑裁剪区域（参考 sttn-auto get_crop_region）。

    与全宽条带策略不同，此函数保留用户选择的 xmin/xmax 信息，
    生成紧凑的 (ymin, ymax, xmin, xmax) 裁剪区域。

    Args:
        coords_list: 坐标列表 [(ymin, ymax, xmin, xmax), ...]
        W: 视频帧宽度
        H: 视频帧高度
        padding: 选择框四周扩展像素数，默认 30
        min_aspect: 最小宽高比，默认 3.0
        align: 尺寸对齐倍数，默认 8

    Returns:
        紧凑裁剪区域列表 [(ymin, ymax, xmin, xmax), ...]
    """
    if not coords_list:
        return []

    areas = []
    for ymin, ymax, xmin, xmax in coords_list:
        # 1. Padding 扩展（参考 sttn-auto padding=30）
        x1 = max(0, xmin - padding)
        y1 = max(0, ymin - padding)
        x2 = min(W, xmax + padding)
        y2 = min(H, ymax + padding)

        # 2. 最小宽高比保证（参考 sttn-auto min_aspect=3.0）
        crop_w = x2 - x1
        crop_h = y2 - y1
        if crop_w < crop_h * min_aspect and crop_h > 0:
            target_w = int(crop_h * min_aspect)
            cx = (x1 + x2) // 2
            x1_new = max(0, cx - target_w // 2)
            x2_new = x1_new + target_w
            if x2_new > W:
                x2_new = W
                x1_new = max(0, x2_new - target_w)
            x1, x2 = x1_new, x2_new

        # 3. 高度对齐（两侧均匀收缩，避免整体偏移）
        crop_h = y2 - y1
        remainder = crop_h % align
        if remainder != 0:
            half = remainder // 2
            y1 = min(H - align, y1 + half)
            y2 = max(y1 + align, y2 - (remainder - half))

        # 4. 宽度对齐（两侧均匀收缩）
        crop_w = x2 - x1
        remainder_w = crop_w % align
        if remainder_w != 0:
            half = remainder_w // 2
            x1 = min(W - align, x1 + half)
            x2 = max(x1 + align, x2 - (remainder_w - half))

        # 5. 最终边界检查
        y1 = max(0, min(y1, H - 1))
        y2 = max(y1 + align, min(y2, H))
        x1 = max(0, min(x1, W - 1))
        x2 = max(x1 + align, min(x2, W))

        areas.append((y1, y2, x1, x2))

    logger.info('get_tight_inpaint_area: input=%d boxes, output=%d areas, padding=%d, min_aspect=%.1f',
                len(coords_list), len(areas), padding, min_aspect)
    return areas


def get_inpaint_area_by_mask(W, H, h, mask, multiple=1):
    """
    获取字幕去除区域，根据mask来确定需要填补的区域和高度，
    并根据模型要求调整区域大小为指定倍数

    Args:
        W: 图像宽度
        H: 图像高度
        h: 检测区域高度
        mask: 遮罩图像
        multiple: 区域尺寸需要满足的倍数，默认为1

    Returns:
        调整后的绘画区域列表，格式为[(ymin, ymax, xmin, xmax), ...]
    """
    inpaint_area = []

    if np.all(mask == 0):
        return inpaint_area

    # 确保 mask 是 2D（主流程可能传入 (H,W,1)）
    binary_mask = (mask > 0).astype(np.uint8) * 255
    if binary_mask.ndim == 3:
        binary_mask = binary_mask[:, :, 0]

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)

    island_info = []
    for i in range(1, num_labels):
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w = stats[i, cv2.CC_STAT_WIDTH]
        height = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]

        if area < MIN_ISLAND_AREA:
            continue

        center_y = int(centroids[i][1])
        island_info.append((y, y + height, center_y, area, i))

    if not island_info:
        return inpaint_area

    island_info.sort(key=lambda x: x[2])

    merged_islands = []
    current_group = [island_info[0]]
    cur_min_y = island_info[0][0]
    cur_max_y = island_info[0][1]

    for i in range(1, len(island_info)):
        top_y, bottom_y, center_y, _, _ = island_info[i]

        new_min_y = min(cur_min_y, top_y)
        new_max_y = max(cur_max_y, bottom_y)

        has_connection = False
        if cur_max_y < top_y:
            middle_region = binary_mask[cur_max_y:top_y, :]
            if np.any(middle_region > 0):
                has_connection = True
        else:
            has_connection = True

        if new_max_y - new_min_y <= h and has_connection:
            current_group.append(island_info[i])
            cur_min_y = new_min_y
            cur_max_y = new_max_y
        else:
            merged_islands.append(current_group)
            current_group = [island_info[i]]
            cur_min_y = top_y
            cur_max_y = bottom_y

    merged_islands.append(current_group)

    for group in merged_islands:
        min_y = min([island[0] for island in group])
        max_y = max([island[1] for island in group])

        center_y = sum([island[2] for island in group]) // len(group)

        half_h = h // 2

        ymin = max(0, center_y - half_h)
        ymax = ymin + h

        if ymax > H:
            ymax = H
            ymin = max(0, H - h)

        if ymin > min_y or ymax < max_y:
            if max_y - min_y <= h:
                ymin = min_y
                ymax = ymin + h
                if ymax > H:
                    ymax = H
                    ymin = max(0, H - h)
            else:
                ymin = min_y
                ymax = max_y
                if ymax > H:
                    ymax = H
                if ymin < 0:
                    ymin = 0

        xmin = 0
        xmax = W

        if multiple > 1:
            height = ymax - ymin
            remainder = height % multiple

            if remainder != 0:
                adjust_pixels = multiple - remainder
                center_y = (ymin + ymax) / 2

                if ymin - adjust_pixels/2 >= 0 and ymax + adjust_pixels/2 <= H:
                    ymin = int(center_y - height/2 - adjust_pixels/2)
                    ymax = int(center_y + height/2 + adjust_pixels/2)
                elif height > multiple:
                    ymin = int(center_y - (height - remainder)/2)
                    ymax = int(center_y + (height - remainder)/2)
                else:
                    if ymax + adjust_pixels <= H:
                        ymax += adjust_pixels
                    elif ymin - adjust_pixels >= 0:
                        ymin -= adjust_pixels
                    elif height > multiple:
                        ymax = ymin + height - remainder

            width = xmax - xmin
            remainder_w = width % multiple

            if remainder_w != 0:
                adjust_pixels_w = multiple - remainder_w
                center_x = (xmin + xmax) / 2
                xmin = int(center_x - (width - remainder_w)/2)
                xmax = int(center_x + (width - remainder_w)/2)

        area = (int(ymin), int(ymax), int(xmin), int(xmax))
        if area not in inpaint_area:
            inpaint_area.append(area)

    return inpaint_area
