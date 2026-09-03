"""Cache: roundtrip, tozsamosc packed ORM, odmowa czytania innej wersji."""
import pickle

import numpy as np
import pytest
from PIL import Image

from conftest import make_uv_sphere
from lodziarz.cache import CACHE_VERSION, load_cache, save_cache
from lodziarz.core import MaterialData


def test_roundtrip_preserves_orm_identity(tmp_path):
    shared = Image.new("RGB", (8, 8), (100, 150, 200))
    mat = MaterialData(name="M_Test", roughness_tex=shared,
                       metallic_tex=shared,
                       base_color_tex=Image.new("RGB", (8, 8), (255, 0, 0)))
    lods = [make_uv_sphere(6, 12)]
    path = tmp_path / "c.pkl"
    save_cache(path, asset_name="Test", baked_lods=lods, orig_lods=lods,
               materials=[mat], texture_files={"basecolor": "T_bc.png"},
               embed=False, per_lod=False, thresholds=[500.0, 1000.0])
    data = load_cache(path)
    assert data["asset_name"] == "Test"
    assert data["thresholds"] == [500.0, 1000.0]
    m = data["materials"][0]
    # detekcja packed ORM opiera sie na `is` — tozsamosc musi przezyc pickle
    assert m.roughness_tex is m.metallic_tex
    assert m.base_color_tex is not m.roughness_tex
    got = data["baked_lods"][0]
    assert np.array_equal(got.indices, lods[0].indices)


def test_wrong_version_rejected(tmp_path):
    path = tmp_path / "old.pkl"
    with open(path, "wb") as f:
        pickle.dump({"version": CACHE_VERSION - 1,
                     "materials": {"images": [], "materials": []}}, f)
    with pytest.raises(ValueError, match="innej wersji"):
        load_cache(path)
