# -*- coding: utf-8 -*-
"""
TokosDecoder — декодер/кодировщик SSTV (Python).
Декодирование из файла (водопад). Кодирование: изображение → WAV с настройкой размера в кадре.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from threading import Thread
from queue import Queue

# Тихий режим для GUI
import sstv.common as _sstv_common
_sstv_common.log_message = lambda *a, **kw: None
_sstv_common.progress_bar = lambda *a, **kw: None

try:
    from sstv_modes_ext import register_extended_modes, SC2_180
    register_extended_modes()
except Exception as e:
    SC2_180 = None
    print("Расширенные режимы (SC2-180):", e)

import numpy as np
import sstv.spec
from sstv.decode import SSTVDecoder, draw_partial_image
from PIL import Image, ImageTk

# Режимы декодирования: (название, класс режима или None = авто)
DECODE_MODES = [
    ("Авто (по VIS)", None),
    ("Robot 36", sstv.spec.R36),
    ("Robot 72", sstv.spec.R72),
    ("Martin 1", sstv.spec.M1),
    ("Martin 2", sstv.spec.M2),
    ("Scottie 1", sstv.spec.S1),
    ("Scottie 2", sstv.spec.S2),
    ("Scottie DX", sstv.spec.SDX),
]
if SC2_180:
    DECODE_MODES.append(("Wraase SC2-180", SC2_180))

# Режимы кодирования (PySSTV): (название, класс)
ENCODE_MODES = []
try:
    from pysstv.color import (
        Robot36, MartinM1, MartinM2, ScottieS1, ScottieS2, ScottieDX,
        PD90, PD120, PD180, WraaseSC2180,
    )
    ENCODE_MODES = [
        ("Robot 36", Robot36),
        ("Robot 72", None),  # PySSTV не имеет Robot72 в color, пропуск
        ("Martin 1", MartinM1),
        ("Martin 2", MartinM2),
        ("Scottie 1", ScottieS1),
        ("Scottie 2", ScottieS2),
        ("Scottie DX", ScottieDX),
        ("PD90", PD90),
        ("PD120", PD120),
        ("PD180", PD180),
        ("Wraase SC2-180", WraaseSC2180),
    ]
    ENCODE_MODES = [(n, c) for n, c in ENCODE_MODES if c is not None]
except ImportError:
    pass


def decode_worker(path: str, result_queue: Queue, skip: float = 0.0, forced_mode=None, waterfall=True):
    def progress_cb(lines_done, total_lines, partial_data, mode):
        if not waterfall:
            return
        if lines_done % 2 == 0 or lines_done == total_lines:
            result_queue.put(("waterfall", lines_done, total_lines, partial_data, mode))

    try:
        with open(path, "rb") as f:
            with SSTVDecoder(f) as dec:
                img = dec.decode(
                    skip=skip,
                    forced_mode=forced_mode,
                    progress_callback=progress_cb if waterfall else None,
                )
        result_queue.put(("ok", img, getattr(dec.mode, "NAME", "?")))
    except Exception as e:
        result_queue.put(("err", str(e), None))


def encode_worker(image_path: str, wav_path: str, mode_class, result_queue: Queue, fit_mode: str, scale_pct: float, offset_x: float, offset_y: float):
    try:
        img = Image.open(image_path).convert("RGB")
        w, h = mode_class.WIDTH, mode_class.HEIGHT
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            raise ValueError("Некорректный размер изображения")
        if fit_mode == "fill":
            scale = max(w / iw, h / ih)
        else:
            scale = min(w / iw, h / ih)
        scale *= scale_pct / 100.0
        nw, nh = int(round(iw * scale)), int(round(ih * scale))
        if nw <= 0 or nh <= 0:
            nw, nh = w, h
        scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (w, h), (0, 0, 0))
        left = int(w / 2 - nw / 2 + offset_x)
        top = int(h / 2 - nh / 2 + offset_y)
        out.paste(scaled, (left, top))
        sstv_obj = mode_class(out, 48000, 16)
        sstv_obj.write_wav(wav_path)
        result_queue.put(("encoded", wav_path, None))
    except Exception as e:
        result_queue.put(("err", str(e), None))


class DecoderApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TokosDecoder — SSTV")
        self.root.minsize(560, 460)
        self.root.geometry("760x520")

        self.input_path = tk.StringVar()
        self.result_image = None
        self.result_mode = None
        self.result_queue = Queue()
        self.is_decoding = False
        self.is_encoding = False
        self._encode_image_path = tk.StringVar()
        self._encode_image_pil = None
        self._waterfall = None
        self._waterfall_total = 0

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        nb = ttk.Notebook(main)
        nb.pack(fill=tk.BOTH, expand=True)

        # --- Вкладка Декодирование ---
        tab_decode = ttk.Frame(nb, padding=4)
        nb.add(tab_decode, text="Декодирование")

        left = ttk.Frame(tab_decode)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        ttk.Label(left, text="WAV / аудио:").pack(anchor=tk.W)
        row = ttk.Frame(left)
        row.pack(fill=tk.X, pady=2)
        self.entry_file = ttk.Entry(row, textvariable=self.input_path, width=26)
        self.entry_file.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row, text="…", width=3, command=self._pick_file).pack(side=tk.LEFT)

        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(left, text="Режим декодирования:").pack(anchor=tk.W)
        self.decode_mode_var = tk.StringVar(value=DECODE_MODES[0][0])
        self.mode_combo = ttk.Combobox(left, textvariable=self.decode_mode_var, state="readonly", width=18)
        self.mode_combo["values"] = [m[0] for m in DECODE_MODES]
        self.mode_combo.pack(fill=tk.X, pady=2)

        self.btn_decode = ttk.Button(left, text="Декодировать", command=self._start_decode)
        self.btn_decode.pack(fill=tk.X, pady=10)

        self.lbl_status = ttk.Label(left, text="", foreground="gray", font=("", 9))
        self.lbl_status.pack(anchor=tk.W)

        right = ttk.Frame(tab_decode)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(right, text="Результат:").pack(anchor=tk.W)
        self.canvas_frame = ttk.Frame(right)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.canvas = tk.Canvas(self.canvas_frame, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_canvas())
        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=4)
        self.lbl_mode = ttk.Label(btn_row, text="", font=("", 9), foreground="gray")
        self.lbl_mode.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Сохранить PNG…", command=self._save_png).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_row, text="Сохранить JPEG…", command=self._save_jpeg).pack(side=tk.RIGHT)

        # --- Вкладка Кодирование ---
        tab_encode = ttk.Frame(nb, padding=4)
        nb.add(tab_encode, text="Кодирование")

        enc_left = ttk.Frame(tab_encode)
        enc_left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        ttk.Label(enc_left, text="Кодирование SSTV", font=("", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(enc_left, text="Изображение → WAV").pack(anchor=tk.W)
        ttk.Separator(enc_left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Label(enc_left, text="Изображение:").pack(anchor=tk.W)
        enc_row = ttk.Frame(enc_left)
        enc_row.pack(fill=tk.X, pady=2)
        self.entry_encode_img = ttk.Entry(enc_row, textvariable=self._encode_image_path, width=22)
        self.entry_encode_img.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(enc_row, text="…", width=3, command=self._pick_encode_image).pack(side=tk.LEFT)
        ttk.Label(enc_left, text="Режим:").pack(anchor=tk.W, pady=(8, 0))
        self.encode_mode_var = tk.StringVar()
        if ENCODE_MODES:
            self.encode_mode_var.set(ENCODE_MODES[0][0])
        self.encode_combo = ttk.Combobox(enc_left, textvariable=self.encode_mode_var, state="readonly", width=18)
        self.encode_combo["values"] = [m[0] for m in ENCODE_MODES]
        self.encode_combo.pack(fill=tk.X, pady=2)
        self.encode_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw_encode_preview())
        ttk.Separator(enc_left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(enc_left, text="Размер в кадре:").pack(anchor=tk.W)
        self.encode_fit_var = tk.StringVar(value="fit")
        ttk.Radiobutton(enc_left, text="Вписать", variable=self.encode_fit_var, value="fit", command=self._redraw_encode_preview).pack(anchor=tk.W)
        ttk.Radiobutton(enc_left, text="Заполнить", variable=self.encode_fit_var, value="fill", command=self._redraw_encode_preview).pack(anchor=tk.W)
        ttk.Label(enc_left, text="Масштаб %:").pack(anchor=tk.W, pady=(4, 0))
        self.encode_scale_var = tk.DoubleVar(value=100.0)
        self.encode_scale_spin = ttk.Spinbox(enc_left, from_=50, to=200, width=6, textvariable=self.encode_scale_var)
        self.encode_scale_spin.pack(anchor=tk.W, pady=2)
        self.encode_scale_var.trace_add("write", lambda *a: self._redraw_encode_preview())
        ttk.Label(enc_left, text="Смещение X:").pack(anchor=tk.W, pady=(4, 0))
        self.encode_offset_x_var = tk.IntVar(value=0)
        ttk.Spinbox(enc_left, from_=-200, to=200, width=6, textvariable=self.encode_offset_x_var).pack(anchor=tk.W, pady=2)
        self.encode_offset_x_var.trace_add("write", lambda *a: self._redraw_encode_preview())
        ttk.Label(enc_left, text="Смещение Y:").pack(anchor=tk.W, pady=(4, 0))
        self.encode_offset_y_var = tk.IntVar(value=0)
        ttk.Spinbox(enc_left, from_=-200, to=200, width=6, textvariable=self.encode_offset_y_var).pack(anchor=tk.W, pady=2)
        self.encode_offset_y_var.trace_add("write", lambda *a: self._redraw_encode_preview())
        self.btn_encode = ttk.Button(enc_left, text="Кодировать и сохранить WAV…", command=self._start_encode)
        self.btn_encode.pack(fill=tk.X, pady=10)
        self.lbl_encode_status = ttk.Label(enc_left, text="", foreground="gray", font=("", 9))
        self.lbl_encode_status.pack(anchor=tk.W)
        if not ENCODE_MODES:
            ttk.Label(enc_left, text="Установите PySSTV: pip install PySSTV", foreground="gray").pack(anchor=tk.W)
            self.btn_encode.config(state=tk.DISABLED)

        enc_right = ttk.Frame(tab_encode)
        enc_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(enc_right, text="Предпросмотр кадра:").pack(anchor=tk.W)
        self.encode_canvas_frame = ttk.Frame(enc_right)
        self.encode_canvas_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.encode_canvas = tk.Canvas(self.encode_canvas_frame, bg="#2b2b2b", highlightthickness=0)
        self.encode_canvas.pack(fill=tk.BOTH, expand=True)
        self.encode_canvas.bind("<Configure>", lambda e: self._redraw_encode_preview())

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Выберите WAV или аудио",
            filetypes=[("Аудио", "*.wav *.ogg *.flac"), ("WAV", "*.wav"), ("Все", "*.*")],
        )
        if path:
            self.input_path.set(path)
            self.lbl_status.config(text="")

    def _get_forced_mode(self):
        name = self.decode_mode_var.get()
        for n, mode in DECODE_MODES:
            if n == name:
                return mode
        return None

    def _start_decode(self):
        path = self.input_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("Файл", "Выберите существующий аудиофайл.")
            return
        if self.is_decoding:
            return
        self.is_decoding = True
        self.btn_decode.config(state=tk.DISABLED)
        self.lbl_status.config(text="Декодирование…")
        self.result_image = None
        self.result_mode = None
        self._waterfall = None
        self._redraw_canvas()
        forced = self._get_forced_mode()
        Thread(target=decode_worker, args=(path, self.result_queue, 0.0, forced), daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                msg = self.result_queue.get_nowait()
                status = msg[0]
                if status == "ok":
                    _, a, b = msg
                    self.result_image = a
                    self.result_mode = b
                    self._waterfall = None
                    self.lbl_status.config(text="Готово.")
                    self.is_decoding = False
                    self.btn_decode.config(state=tk.NORMAL)
                    self._redraw_canvas()
                    if self.result_mode:
                        self.lbl_mode.config(text="Режим: " + self.result_mode)
                elif status == "waterfall":
                    _, lines_done, total_lines, partial_data, mode = msg
                    self._waterfall = (lines_done, partial_data, mode)
                    self._waterfall_total = total_lines
                    self._redraw_canvas()
                elif status == "encoded":
                    _, a, _ = msg
                    self.is_encoding = False
                    self.btn_encode.config(state=tk.NORMAL)
                    self.lbl_encode_status.config(text="Сохранено: " + a)
                    messagebox.showinfo("Кодирование", "WAV сохранён:\n" + a)
                else:
                    _, a, _ = msg
                    self.lbl_status.config(text="Ошибка: " + str(a))
                    messagebox.showerror("Ошибка", str(a))
                    self.is_decoding = self.is_encoding = False
                    self.btn_decode.config(state=tk.NORMAL)
                    self.btn_encode.config(state=tk.NORMAL if ENCODE_MODES else tk.DISABLED)
        except Exception:
            pass
        self.root.after(200, self._poll_queue)

    def _redraw_canvas(self):
        self.canvas.delete("all")
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        if self._waterfall is not None:
            self._draw_waterfall(cw, ch)
            return
        if self.result_image is None:
            self.canvas.create_text(cw // 2, ch // 2, text="Изображение появится здесь после декодирования", fill="gray", font=("", 11))
            return
        img = self.result_image
        if img.mode != "RGB":
            img = img.convert("RGB")
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        r = min(cw / img.width, ch / img.height, 1.0)
        nw, nh = int(img.width * r), int(img.height * r)
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)

    def _draw_waterfall(self, cw, ch):
        lines_done, partial_data, mode = self._waterfall
        total = self._waterfall_total
        if total <= 0 or lines_done <= 0:
            return
        try:
            part_img = draw_partial_image(mode, partial_data, lines_done)
        except Exception:
            return
        w, h_part = part_img.width, part_img.height
        h_full = total
        noise_h = max(0, h_full - h_part)
        if noise_h > 0:
            rng = np.random.default_rng(int(lines_done) + int(total))
            rnd = rng.integers(0, 256, (w, noise_h, 3), dtype=np.uint8)
            noise_img = Image.fromarray(rnd, "RGB")
            composite = Image.new("RGB", (w, h_full), (30, 30, 35))
            composite.paste(part_img, (0, 0))
            composite.paste(noise_img, (0, h_part))
        else:
            composite = part_img.convert("RGB") if part_img.mode != "RGB" else part_img
        r = min(cw / composite.width, ch / composite.height, 1.0)
        nw, nh = int(composite.width * r), int(composite.height * r)
        composite = composite.resize((nw, nh), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(composite)
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)

    def _save_png(self):
        self._save("png")

    def _save_jpeg(self):
        self._save("jpeg")

    def _save(self, fmt):
        if self.result_image is None:
            messagebox.showinfo("Сохранение", "Сначала декодируйте сигнал.")
            return
        ext = "png" if fmt == "png" else "jpg"
        path = filedialog.asksaveasfilename(defaultextension="." + ext, filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")] if fmt == "png" else [("JPEG", "*.jpg"), ("PNG", "*.png")])
        if path:
            try:
                self.result_image.save(path)
                messagebox.showinfo("Сохранение", "Сохранено: " + path)
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))

    def _pick_encode_image(self):
        path = filedialog.askopenfilename(
            title="Выберите изображение",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.gif"), ("Все", "*.*")],
        )
        if path:
            self._encode_image_path.set(path)
            try:
                self._encode_image_pil = Image.open(path).convert("RGB")
            except Exception:
                self._encode_image_pil = None
            self.lbl_encode_status.config(text="")
            self._redraw_encode_preview()

    def _get_encode_mode_class(self):
        name = self.encode_mode_var.get()
        for n, c in ENCODE_MODES:
            if n == name:
                return c
        return None

    def _build_encode_frame_image(self, mode_class):
        if self._encode_image_pil is None or mode_class is None:
            return None
        try:
            scale_pct = float(self.encode_scale_var.get())
        except (tk.TclError, ValueError):
            scale_pct = 100.0
        try:
            offset_x = int(self.encode_offset_x_var.get())
        except (tk.TclError, ValueError):
            offset_x = 0
        try:
            offset_y = int(self.encode_offset_y_var.get())
        except (tk.TclError, ValueError):
            offset_y = 0
        img = self._encode_image_pil
        w, h = mode_class.WIDTH, mode_class.HEIGHT
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            return None
        fit_mode = self.encode_fit_var.get()
        if fit_mode == "fill":
            scale = max(w / iw, h / ih)
        else:
            scale = min(w / iw, h / ih)
        scale *= scale_pct / 100.0
        nw, nh = int(round(iw * scale)), int(round(ih * scale))
        if nw <= 0 or nh <= 0:
            nw, nh = w, h
        scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (w, h), (0, 0, 0))
        left = int(w / 2 - nw / 2 + offset_x)
        top = int(h / 2 - nh / 2 + offset_y)
        out.paste(scaled, (left, top))
        return out

    def _redraw_encode_preview(self):
        if not hasattr(self, "encode_canvas") or self.encode_canvas is None:
            return
        self.encode_canvas.delete("all")
        try:
            cw = self.encode_canvas.winfo_width()
            ch = self.encode_canvas.winfo_height()
        except Exception:
            return
        if cw <= 1 or ch <= 1:
            return
        mode_class = self._get_encode_mode_class()
        frame_img = self._build_encode_frame_image(mode_class) if mode_class else None
        if frame_img is None:
            self.encode_canvas.create_text(cw // 2, ch // 2, text="Выберите изображение и режим", fill="gray", font=("", 11))
            return
        r = min(cw / frame_img.width, ch / frame_img.height, 1.0)
        nw, nh = int(frame_img.width * r), int(frame_img.height * r)
        frame_img = frame_img.resize((nw, nh), Image.Resampling.LANCZOS)
        self._encode_photo = ImageTk.PhotoImage(frame_img)
        self.encode_canvas.create_image(cw // 2, ch // 2, image=self._encode_photo)

    def _start_encode(self):
        if not ENCODE_MODES or self.is_encoding:
            return
        path = self._encode_image_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("Изображение", "Выберите файл изображения.")
            return
        name = self.encode_mode_var.get()
        mode_class = None
        for n, c in ENCODE_MODES:
            if n == name:
                mode_class = c
                break
        if mode_class is None:
            return
        wav_path = filedialog.asksaveasfilename(
            defaultextension=".wav",
            filetypes=[("WAV", "*.wav")],
            title="Сохранить WAV",
        )
        if not wav_path:
            return
        try:
            scale_pct = float(self.encode_scale_var.get())
        except (tk.TclError, ValueError):
            scale_pct = 100.0
        try:
            offset_x = float(self.encode_offset_x_var.get())
        except (tk.TclError, ValueError):
            offset_x = 0
        try:
            offset_y = float(self.encode_offset_y_var.get())
        except (tk.TclError, ValueError):
            offset_y = 0
        fit_mode = self.encode_fit_var.get()
        self.is_encoding = True
        self.btn_encode.config(state=tk.DISABLED)
        self.lbl_encode_status.config(text="Кодирование…")
        Thread(
            target=encode_worker,
            args=(path, wav_path, mode_class, self.result_queue, fit_mode, scale_pct, offset_x, offset_y),
            daemon=True,
        ).start()

    def run(self):
        self.root.after(100, lambda: self._redraw_canvas())
        self.root.mainloop()


def main():
    app = DecoderApp()
    app.run()


if __name__ == "__main__":
    main()
