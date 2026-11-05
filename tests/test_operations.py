import pytest

from openlaser.document import operations as ops
from openlaser.document.commands import UndoStack, edit_document
from openlaser.document.model import Document, Layer, Shape, demo_document, make_shape, new_document
from openlaser.geometry import path as gp


def doc3():
    d = new_document(400, 400)
    lid = d.layers[0].id
    d.shapes = [make_shape(lid, gp.rectangle(0, 0, 10, 10), "a"), make_shape(lid, gp.rectangle(50, 20, 20, 10), "b"),
                make_shape(lid, gp.rectangle(100, 40, 10, 30), "c")]
    return d


def ids(d):
    return [s.id for s in d.shapes]


def test_move_and_undo():
    d = doc3()
    u = UndoStack()
    with edit_document(d, u, "mover"):
        ops.move(d, ids(d)[:1], 5, 5)
    assert d.shapes[0].bounds() == pytest.approx((5, 5, 15, 15))
    u.undo()
    assert d.shapes[0].bounds() == pytest.approx((0, 0, 10, 10))
    u.redo()
    assert d.shapes[0].bounds()[0] == pytest.approx(5)


def test_noop_edit_is_not_recorded():
    d = doc3()
    u = UndoStack()
    with edit_document(d, u, "nada"):
        pass
    assert not u.can_undo()


def test_align_and_distribute():
    d = doc3()
    ops.align(d, ids(d), "left")
    assert all(s.bounds()[0] == pytest.approx(0) for s in d.shapes)
    d = doc3()
    ops.align(d, ids(d), "vcenter")
    centers = [(s.bounds()[1] + s.bounds()[3]) / 2 for s in d.shapes]
    assert max(centers) - min(centers) < 1e-9
    d = doc3()
    ops.distribute(d, ids(d), True)
    c = [(s.bounds()[0] + s.bounds()[2]) / 2 for s in d.shapes]
    assert c[1] - c[0] == pytest.approx(c[2] - c[1])


def test_set_bounds_scale_and_keep_aspect():
    d = doc3()
    sid = ids(d)[1]
    ops.set_bounds(d, [sid], w=40, keep_aspect=True)
    assert d.shape(sid).bounds() == pytest.approx((50, 20, 90, 40))
    ops.set_bounds(d, [sid], x=0, y=0)
    assert d.shape(sid).bounds()[:2] == pytest.approx((0, 0))


def test_rotate_flip_equalize():
    d = doc3()
    sid = ids(d)[1]
    ops.rotate(d, [sid], 90)
    b = d.shape(sid).bounds()
    assert b[2] - b[0] == pytest.approx(10) and b[3] - b[1] == pytest.approx(20)
    before = d.shape(sid).bounds()
    ops.flip(d, [sid], True)
    assert d.shape(sid).bounds() == pytest.approx(before)
    d = doc3()
    ops.equalize(d, ids(d), width=True)
    assert all(s.bounds()[2] - s.bounds()[0] == pytest.approx(10) for s in d.shapes)


def test_group_ungroup_duplicate_delete():
    d = doc3()
    a, b, c = ids(d)
    gid = ops.group(d, [a, b])
    assert gid and ops.expand_groups(d, [a]) == [a, b]
    new = ops.duplicate(d, [a, b], 10, 0)
    assert len(new) == 2 and d.shape(new[0]).group_id == d.shape(new[1]).group_id != gid
    ops.ungroup(d, [a, b])
    assert d.shape(a).group_id is None
    ops.delete(d, new)
    assert len(d.shapes) == 3


def test_reorder():
    d = doc3()
    a, b, c = ids(d)
    ops.reorder(d, [a], "front")
    assert ids(d) == [b, c, a]
    ops.reorder(d, [a], "lower")
    assert ids(d) == [b, a, c]
    ops.reorder(d, [c], "back")
    assert ids(d) == [c, b, a]


def test_arrays():
    d = doc3()
    a = ids(d)[0]
    created = ops.rect_array(d, [a], 3, 2, 5, 5)
    assert len(created) == 5
    assert d.shape(created[0]).bounds()[0] == pytest.approx(15)
    d = doc3()
    created = ops.circular_array(d, [ids(d)[0]], 4, (100, 100))
    assert len(created) == 4 - 1


def test_copies_along_path():
    d = doc3()
    lid = d.layers[0].id
    guide = make_shape(lid, gp.polyline([(0, 200), (100, 200)]))
    d.shapes.append(guide)
    created = ops.copies_along_path(d, [ids(d)[0]], guide.id, 3, rotate_copies=False)
    centers = [((s.bounds()[0] + s.bounds()[2]) / 2) for s in d.shapes_by_ids(created)]
    assert centers == pytest.approx([0, 50, 100])


def test_combine_break_close_repair():
    d = doc3()
    a, b, c = ids(d)
    merged = ops.combine(d, [a, b])
    assert len(d.shapes) == 2 and len(d.shape(merged).subpaths) == 2
    parts = ops.break_apart(d, [merged])
    assert len(parts) == 2 and len(d.shapes) == 3
    lid = d.layers[0].id
    open_shape = make_shape(lid, [gp.polyline([(0, 0), (10, 0)]), gp.polyline([(10, 0), (10, 10), (0, 0)])])
    d.shapes.append(open_shape)
    ops.repair(d, [open_shape.id])
    assert d.shape(open_shape.id).subpaths[0].closed


def test_edit_node_and_simplify():
    d = doc3()
    a = ids(d)[0]
    ops.edit_node(d, a, 0, 0, gp.Node(-5, -5))
    assert d.shape(a).bounds()[:2] == pytest.approx((-5, -5))
    ops.edit_node(d, a, 0, 0, None)
    assert len(d.shape(a).subpaths[0].nodes) == 3


def test_layers_add_delete_move_copy():
    d = doc3()
    ly = ops.add_layer(d)
    assert ly.color != d.layers[0].color and ly.order == 1
    ops.set_layer(d, [ids(d)[0]], ly.id)
    d.layers[0].speed_mm_min = 1234
    ops.copy_layer_settings(d, d.layers[0].id, [ly.id])
    assert ly.speed_mm_min == 1234 and ly.color != d.layers[0].color
    ops.move_layer(d, ly.id, -1)
    assert d.sorted_layers()[0] is ly
    ops.delete_layer(d, ly.id, move_shapes_to=d.layers[0].id)
    assert len(d.layers) == 1 and len(d.shapes) == 3
    with pytest.raises(ValueError):
        ops.delete_layer(d, d.layers[0].id)


def test_layer_undo_keeps_identity():
    d = doc3()
    u = UndoStack()
    layer = d.layers[0]
    with edit_document(d, u, "velocidad"):
        layer.speed_mm_min = 50
    u.undo()
    assert d.layers[0] is layer and layer.speed_mm_min == 1000


def test_editable_ids_respects_locks():
    d = doc3()
    a, b, c = ids(d)
    ops.set_flags(d, [a], locked=True)
    ops.set_flags(d, [b], visible=False)
    assert ops.editable_ids(d, [a, b, c]) == [c]


def test_document_v1_migration():
    v1 = {"name": "x", "layers": [Layer("L", "#000", id="l").to_dict()],
          "shapes": [{"layer_id": "l", "points": [[0, 0], [1, 0], [1, 1]], "closed": True, "name": "t", "id": "s"}]}
    d = Document.from_dict(v1)
    assert d.shapes[0].subpaths[0].closed and len(d.shapes[0].subpaths[0].nodes) == 3
    assert Document.from_dict(d.to_dict()).to_dict() == d.to_dict()
    with pytest.raises(ValueError):
        Document.from_dict({"format_version": 99})


def test_demo_document_has_curves():
    d = demo_document()
    assert any(sp.is_curved() for s in d.shapes for sp in s.subpaths)
    assert isinstance(d.shapes[0], Shape)
