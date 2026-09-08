############
# Standard #
############
import numpy as np
import pytest
from pydm.widgets import PyDMImageView
from pytestqt.qtbot import QtBot
from qtpy import QtCore, QtGui, QtWidgets

from pcdswidgets.imaging.common.colormap_intesity_control_full import ColormapIntesityControlFull


class _ImageParent(QtWidgets.QWidget):
    """Minimal stand-in for the parent widget that adopts the control."""

    def __init__(self):
        super().__init__()
        self.image_view = PyDMImageView(parent=self)


@pytest.fixture(scope="function")
def control(qtbot: QtBot) -> ColormapIntesityControlFull:
    # qtbot only weak-references what it tracks, so the parent has to be held
    # for the duration of the test or Qt tears the image item down with it
    parent = _ImageParent()
    qtbot.addWidget(parent)
    widget = ColormapIntesityControlFull(parent)
    widget.link_parent_widgets(parent)
    parent.show()
    widget.show()
    with qtbot.waitExposed(widget):
        pass
    yield widget


def _feed_frame(control: ColormapIntesityControlFull, mini: float, maxi: float) -> None:
    """
    Push one frame the way PyDM does with normalize enabled.

    PyDM derives the levels from each frame's own min/max, sets them on the
    image item, then calls setImage; see PyDMImageView.__updateDisplay.
    """
    image_item = control._image_view.getImageItem()
    frame = np.linspace(mini, maxi, 64 * 64).reshape(64, 64).astype(float)
    image_item.setLevels([mini, maxi])
    image_item.setImage(frame, autoLevels=False)


def test_frames_are_not_user_interaction(control: ColormapIntesityControlFull, qtbot: QtBot):
    """Live frames re-sync the histogram region, which is not a user gesture."""
    control._image_view.setColorMapLimits(10.0, 200.0)

    with qtbot.assertNotEmitted(control.state_changed):
        for i in range(20):
            _feed_frame(control, float(i), 500.0 + i * 7)
            qtbot.wait(1)

    # and the user's manual limits survive the frames
    assert (control._image_view.cm_min, control._image_view.cm_max) == (10.0, 200.0)


def test_set_levels_does_not_emit(control: ColormapIntesityControlFull, qtbot: QtBot):
    """Restoring saved levels applies them without reporting a user change."""
    with qtbot.assertNotEmitted(control.state_changed):
        control.set_levels(5.0, 90.0)
        qtbot.wait(1)

    assert control.get_levels() == (5.0, 90.0)
    assert (control._image_view.cm_min, control._image_view.cm_max) == (5.0, 90.0)


def test_user_drag_emits_once(control: ColormapIntesityControlFull, qtbot: QtBot):
    """
    A histogram drag emits state_changed exactly once and applies the levels.

    pyqtgraph emits sigLevelChangeFinished only when a drag finishes, and the
    graphics scene delivers that finish while the mouse release is dispatched,
    i.e. after the viewport event filter has already seen the release. The
    sequence below reproduces that ordering; a guard that merely checks whether
    the left button is currently held would drop the emission entirely.
    """
    _feed_frame(control, 0.0, 100.0)
    region = control._histogram.item.region
    viewport = control._histogram.viewport()

    emissions = []
    control.state_changed.connect(lambda: emissions.append(1))

    def send(event_type, trigger_button, held_buttons):
        position = QtCore.QPointF(viewport.rect().center())
        event = QtGui.QMouseEvent(event_type, position, trigger_button, held_buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(viewport, event)

    send(QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    # while the drag is in flight the region moves, but nothing is "finished" yet
    region.lines[0].setValue(25.0)
    region.lines[1].setValue(75.0)
    assert emissions == []

    send(QtCore.QEvent.MouseButtonRelease, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)
    region.lineMoveFinished()  # the scene delivers the finish after the filter ran

    assert emissions == [1]
    assert control.get_levels() == (25.0, 75.0)
    assert (control._image_view.cm_min, control._image_view.cm_max) == (25.0, 75.0)

    # the latch is released once control returns to the event loop, so the
    # frames that follow count as camera updates again
    qtbot.wait(10)
    emissions.clear()
    _feed_frame(control, 3.0, 900.0)
    qtbot.wait(1)
    assert emissions == []
    assert (control._image_view.cm_min, control._image_view.cm_max) == (25.0, 75.0)
