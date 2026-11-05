from openlaser.document.commands import SetAttribute, UndoStack
from openlaser.document.model import Layer


def test_undo_redo():
    ly = Layer("a", "#000")
    st = UndoStack()
    st.push(SetAttribute(ly, "speed_mm_min", 500))
    st.push(SetAttribute(ly, "passes", 3))
    assert ly.speed_mm_min == 500 and ly.passes == 3
    st.undo()
    assert ly.passes == 1
    st.undo()
    assert ly.speed_mm_min == 1000 and not st.can_undo()
    st.redo()
    assert ly.speed_mm_min == 500 and st.can_redo()


def test_merge_and_limit():
    ly = Layer("a", "#000")
    st = UndoStack(limit=2)
    st.push(SetAttribute(ly, "speed_mm_min", 10), merge=True)
    st.push(SetAttribute(ly, "speed_mm_min", 20), merge=True)
    st.undo()
    assert ly.speed_mm_min == 1000
    for i in range(5):
        st.push(SetAttribute(ly, "passes", i + 2))
    st.undo()
    st.undo()
    assert not st.can_undo() and ly.passes == 4


def test_new_command_clears_redo():
    ly = Layer("a", "#000")
    st = UndoStack()
    st.push(SetAttribute(ly, "passes", 2))
    st.undo()
    st.push(SetAttribute(ly, "passes", 5))
    assert not st.can_redo()
