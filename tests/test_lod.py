"""LOD chain: redukcja, kompaktowanie, smooth-weld."""
import numpy as np

from conftest import make_flat_shaded, make_uv_sphere
from lodziarz.lod import build_lod_chain, smooth_weld_mesh


def test_chain_reduces_and_validates(log):
    mesh = make_uv_sphere()
    lods = build_lod_chain(mesh, count=4, ratio=0.5, log=log)
    assert len(lods) == 4
    assert lods[0].triangle_count == mesh.triangle_count
    for i in range(1, 4):
        assert lods[i].triangle_count < lods[i - 1].triangle_count
        lods[i].validate()


def test_smooth_weld_merges_only_normal_splits(log):
    mesh = make_flat_shaded(make_uv_sphere(8, 16))
    welded = smooth_weld_mesh(mesh, log)
    # kazdy corner byl osobnym wierzcholkiem; po weldzie pos+uv scala grupy
    assert welded.vertex_count < mesh.vertex_count
    assert welded.triangle_count == mesh.triangle_count
    welded.validate()
    # normale usrednione = jednostkowe
    ln = np.linalg.norm(welded.normals, axis=1)
    assert np.allclose(ln, 1.0, atol=1e-4)


def test_smooth_weld_noop_on_smooth_mesh(log):
    mesh = make_uv_sphere(8, 16)
    welded = smooth_weld_mesh(mesh, log)
    assert welded.vertex_count <= mesh.vertex_count


def test_smooth_weld_unlocks_flat_shaded_simplify(log):
    """Regresja 'simplifier nie schodzi' na hard-surface: flat shading
    blokuje redukcje, smooth-weld ja odblokowuje. LOD0 zostaje oryginalny."""
    mesh = make_flat_shaded(make_uv_sphere())
    plain = build_lod_chain(mesh, count=2, ratio=0.25, log=log)
    welded = build_lod_chain(mesh, count=2, ratio=0.25, log=log,
                             smooth_weld=True)
    assert welded[0].triangle_count == mesh.triangle_count
    assert welded[1].triangle_count < plain[1].triangle_count
    assert welded[1].triangle_count <= mesh.triangle_count * 0.5
    welded[1].validate()
