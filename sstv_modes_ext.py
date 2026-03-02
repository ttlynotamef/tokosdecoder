# -*- coding: utf-8 -*-
"""
Дополнительные режимы SSTV для TokosDecoder.
Подключаются к библиотеке colaclanth/sstv через расширение VIS_MAP.

Режимы из Dayton Paper и справочников:
- Wraase SC2-180: RGB, 256 линий, 180 с (VIS 55 по таблице WB2OSZ)
- PD-семейство: YCbCr, 2 линии на кадр — требуют отдельной логики декодирования,
  здесь только VIS-коды для автоопределения (PD90/120/180 и т.д. в ряде декодеров).
"""

# Импортируем базовые константы из sstv
from sstv.spec import COL_FMT


class SC2_180(object):
    """
    Wraase SC-2 180 s — RGB, 256 линий, 180 с.
    По спецификации Wraase SC-2: порядок R, G, B, горизонтальный синхроимпульс 1200 Hz.
    """
    NAME = "Wraase SC2-180"

    COLOR = COL_FMT.RGB
    LINE_WIDTH = 320
    LINE_COUNT = 256
    # 180 с / 256 линий ≈ 0.703125 с на линию
    LINE_TIME = 180.0 / 256
    SCAN_TIME = (LINE_TIME - 0.004862 - 0.000572 - 3 * 0.000572) / 3  # ~0.232 s на канал
    SYNC_PULSE = 0.004862
    SYNC_PORCH = 0.000572
    SEP_PULSE = 0.000572

    CHAN_COUNT = 3
    CHAN_SYNC = 0
    CHAN_TIME = SEP_PULSE + SCAN_TIME

    CHAN_OFFSETS = [SYNC_PULSE + SYNC_PORCH]
    CHAN_OFFSETS.append(CHAN_OFFSETS[0] + CHAN_TIME)
    CHAN_OFFSETS.append(CHAN_OFFSETS[1] + CHAN_TIME)

    PIXEL_TIME = SCAN_TIME / LINE_WIDTH
    WINDOW_FACTOR = 2.4

    HAS_START_SYNC = False
    HAS_HALF_SCAN = False
    HAS_ALT_SCAN = False


def register_extended_modes():
    """Добавляет расширенные режимы в sstv.spec.VIS_MAP. Вызвать до декодирования."""
    import sstv.spec as spec
    # VIS 55 — Wraase SC-2 180 s (по таблице Vertical Interval Signaling, row 7 col 3)
    spec.VIS_MAP[55] = SC2_180
    return spec.VIS_MAP
