"""ctypes binding do meshoptimizer.dll (simplifyWithAttributes)."""
from __future__ import annotations

import ctypes
from ctypes import (POINTER, c_char_p, c_float, c_size_t, c_ubyte, c_uint)

import numpy as np

from .core import bin_dir

_dll = None


def _lib() -> ctypes.CDLL:
    global _dll
    if _dll is None:
        path = bin_dir() / "meshoptimizer.dll"
        if not path.exists():
            raise FileNotFoundError(
                f"brak {path} — zbuduj native/build_native.ps1")
        _dll = ctypes.CDLL(str(path))
        f = _dll.meshopt_simplifyWithAttributes
        f.restype = c_size_t
        f.argtypes = [
            POINTER(c_uint),            # destination
            POINTER(c_uint),            # indices
            c_size_t,                   # index_count
            POINTER(c_float),           # vertex_positions
            c_size_t,                   # vertex_count
            c_size_t,                   # vertex_positions_stride
            POINTER(c_float),           # vertex_attributes
            c_size_t,                   # vertex_attributes_stride
            POINTER(c_float),           # attribute_weights
            c_size_t,                   # attribute_count
            POINTER(c_ubyte),           # vertex_lock (NULL ok)
            c_size_t,                   # target_index_count
            c_float,                    # target_error
            c_uint,                     # options
            POINTER(c_float),           # result_error
        ]
        s = _dll.meshopt_simplifyScale
        s.restype = c_float
        s.argtypes = [POINTER(c_float), c_size_t, c_size_t]
    return _dll


# meshopt_SimplifyLockBorder itd. — enum options
SIMPLIFY_LOCK_BORDER = 1 << 0
SIMPLIFY_SPARSE = 1 << 1
SIMPLIFY_ERROR_ABSOLUTE = 1 << 2


def simplify_with_attributes(
    indices: np.ndarray,          # (T,3) u32
    positions: np.ndarray,        # (V,3) f32
    attributes: np.ndarray,       # (V,A) f32 — normale, uv...
    attribute_weights: np.ndarray,  # (A,) f32
    target_ratio: float,
    target_error: float = 0.01,
    lock_border: bool = False,
) -> tuple[np.ndarray, float]:
    """Zwraca (nowe indices (T',3) u32, osiagniety error)."""
    lib = _lib()
    idx = np.ascontiguousarray(indices, dtype=np.uint32).ravel()
    pos = np.ascontiguousarray(positions, dtype=np.float32)
    attr = np.ascontiguousarray(attributes, dtype=np.float32)
    wgt = np.ascontiguousarray(attribute_weights, dtype=np.float32)
    index_count = idx.size
    vertex_count = len(pos)
    target = max(3, int(index_count * target_ratio) // 3 * 3)
    dest = np.empty(index_count, dtype=np.uint32)
    err = c_float(0.0)
    options = SIMPLIFY_LOCK_BORDER if lock_border else 0
    n = lib.meshopt_simplifyWithAttributes(
        dest.ctypes.data_as(POINTER(c_uint)),
        idx.ctypes.data_as(POINTER(c_uint)),
        index_count,
        pos.ctypes.data_as(POINTER(c_float)),
        vertex_count,
        pos.strides[0],
        attr.ctypes.data_as(POINTER(c_float)) if attr.size else None,
        attr.strides[0] if attr.size else 0,
        wgt.ctypes.data_as(POINTER(c_float)) if wgt.size else None,
        len(wgt),
        None,
        target,
        c_float(target_error),
        options,
        ctypes.byref(err),
    )
    return dest[:n].reshape(-1, 3).copy(), float(err.value)
