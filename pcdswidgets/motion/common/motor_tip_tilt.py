"""
Shared behavior for MotorTipTiltFull and MotorTipTiltDouble.

The two widgets are functionally identical; they only differ in which
Designer-generated base class supplies their widget layout (the "full" vs
"double" .ui files). MotorTipTiltMixin holds all of that shared behavior, so
motor_tip_tilt_full.py/motor_tip_tilt_double.py just declare the concrete
class and pick up their own generated base. Add overrides there if the two
ever need to diverge.
"""

import logging

from pydm.widgets import PyDMPushButton, PyDMRelatedDisplayButton, PyDMShellCommand
from pydm.widgets.channel import PyDMChannel
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QCheckBox, QWidget

from pcdswidgets.builder.designer_options import DesignerOptions
from pcdswidgets.builder.icon_options import IconOptions
from pcdswidgets.motion.common.motor_style import MotorStyle
from pcdswidgets.motion.common.svg_multi_state_led import SvgMultiStateLED

logger = logging.getLogger(__name__)


class MotorTipTiltMixin:
    # some type hinting
    vertical_invert: QCheckBox
    horizontal_invert: QCheckBox
    step_up: PyDMPushButton
    step_down: PyDMPushButton
    step_left: PyDMPushButton
    step_right: PyDMPushButton
    stop: PyDMPushButton
    vertical_status_led: SvgMultiStateLED
    horizontal_status_led: SvgMultiStateLED
    vertical_expert_screen_motor: PyDMShellCommand
    horizontal_expert_screen_motor: PyDMShellCommand
    vertical_expert_screen_smaract: PyDMRelatedDisplayButton
    horizontal_expert_screen_smaract: PyDMRelatedDisplayButton

    designer_options = DesignerOptions(
        group="ECS Motion Common",
        is_container=False,
        icon=IconOptions.NONE,
    )

    # Signal shared by both stop channels. Emitting writes to both at once.
    _stop_signal = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._motor_style = MotorStyle.MotorRecord
        self.vertical_invert.stateChanged.connect(self._invert_vertical)
        self.horizontal_invert.stateChanged.connect(self._invert_horizontal)
        self._stop_channels = {
            "vertical_motor": PyDMChannel(value_signal=self._stop_signal),
            "horizontal_motor": PyDMChannel(value_signal=self._stop_signal),
        }
        self.stop.clicked.connect(self._stop_all)
        self._last_position: dict[str, float | None] = {"vertical": None, "horizontal": None}
        self._direction_channels = {
            "vertical": PyDMChannel(value_slot=lambda v: self._on_position_changed("vertical", v)),
            "horizontal": PyDMChannel(value_slot=lambda v: self._on_position_changed("horizontal", v)),
        }
        self._apply_expert_screen_visibility()
        self._refresh_axis("vertical")
        self._refresh_axis("horizontal")

    def channels(self) -> list[PyDMChannel]:
        """Let pydm discover and (dis)connect our manually created stop/direction channels."""
        return list(self._stop_channels.values()) + list(self._direction_channels.values())

    def after_set_macro(self, macro_name: str, value: str) -> None:
        """Keep the stop channel and the style-dependent axis channels in sync with the current motor."""
        channel = self._stop_channels.get(macro_name)
        if channel is not None:
            if channel.address:
                channel.disconnect()
            channel.address = f"ca://{value}.STOP"
            channel.connect()

        if macro_name in ("vertical_motor", "horizontal_motor"):
            self._refresh_axis(macro_name.removesuffix("_motor"))

    def _stop_all(self) -> None:
        """Stop motion on both axes by writing 1 to each axis's .STOP field."""
        self._stop_signal.emit(1)

    def _apply_expert_screen_visibility(self) -> None:
        """Show the expert-screen button that matches the current style, hide the other."""
        is_smaract = self._motor_style == MotorStyle.Smaract
        self.vertical_expert_screen_motor.setVisible(not is_smaract)
        self.horizontal_expert_screen_motor.setVisible(not is_smaract)
        self.vertical_expert_screen_smaract.setVisible(is_smaract)
        self.horizontal_expert_screen_smaract.setVisible(is_smaract)

    def _step_suffixes(self, motor_pv: str) -> tuple[str, str]:
        """Return the (forward, reverse) tweak PVs for motor_pv, based on the current style."""
        if self._motor_style == MotorStyle.Smaract:
            return (f"{motor_pv}:STEP_FORWARD.PROC", f"{motor_pv}:STEP_REVERSE.PROC")
        return (f"{motor_pv}.TWF", f"{motor_pv}.TWR")

    def _step_size_pv(self, motor_pv: str) -> str:
        """Return the step-size PV for motor_pv, based on the current style."""
        if self._motor_style == MotorStyle.Smaract:
            return f"{motor_pv}:STEP_COUNT"
        return f"{motor_pv}.TWV"

    def _position_pv(self, motor_pv: str) -> str:
        """Return the position/total-step PV for motor_pv, based on the current style."""
        if self._motor_style == MotorStyle.Smaract:
            return f"{motor_pv}:TOTAL_STEP_COUNT"
        return f"{motor_pv}.RBV"

    def _invert_axis_channel(self, axis: str) -> None:
        """
        Invert the forward/reverse channel connections for a particular
        axis in the directional pad

        Parameters
        ----------
        axis : str
            One of ['vertical', 'horizontal']
        """
        checkbox: QCheckBox
        widget: PyDMPushButton

        if axis not in ["vertical", "horizontal"]:
            # Don't be silly please
            return

        motor_pv = self.get_macro(f"{axis}_motor")

        if not motor_pv:
            logger.debug(f"Macro for {axis}_motor does not yet exist")
            return

        checkbox = getattr(self, f"{axis}_invert")
        directions = ["up", "down"] if axis == "vertical" else ["right", "left"]
        forward_pv, reverse_pv = self._step_suffixes(motor_pv)

        # didn't want to use dir/s as an iterator which is normally for directory
        for d in directions:
            if d in ["up", "right"]:
                pv = reverse_pv if checkbox.isChecked() else forward_pv
            else:
                pv = forward_pv if checkbox.isChecked() else reverse_pv
            widget = getattr(self, f"step_{d}")
            widget.set_channel(f"ca://{pv}")

    def _refresh_axis(self, axis: str) -> None:
        """
        Recompute every PV this axis drives: position, step size, and the tweak buttons.

        Before a real motor is assigned, shows the ${axis_motor}-templated PV as
        placeholder text on the position label, matching how the old style-specific
        .ui files looked in Designer before their macros were substituted.
        """
        motor_pv = self.get_macro(f"{axis}_motor")
        display_pv = motor_pv or f"${{{axis}_motor}}"
        position_pv = self._position_pv(display_pv)
        is_smaract = self._motor_style == MotorStyle.Smaract

        position_widget = getattr(self, f"{axis}_position")
        step_size_widget = getattr(self, f"{axis}_step_size")

        # SmarAct's integer PVs never send their own precision update, so a stale
        # PREC left over from a prior motor_record connection would otherwise stick.
        position_widget.precisionFromPV = not is_smaract
        step_size_widget.precisionFromPV = not is_smaract

        position_widget.setText(f"ca://{position_pv}")

        if not motor_pv:
            logger.debug(f"Macro for {axis}_motor does not yet exist")
            return

        position_widget.set_channel(f"ca://{position_pv}")
        step_size_widget.set_channel(f"ca://{self._step_size_pv(motor_pv)}")

        direction_channel = self._direction_channels[axis]
        if direction_channel.address:
            direction_channel.disconnect()
        self._last_position[axis] = None
        direction_channel.address = f"ca://{position_pv}"
        direction_channel.connect()

        self._invert_axis_channel(axis)

    def set_motors(self, horizontal_motor: str, vertical_motor: str) -> None:
        """
        Reassign which motor PVs drive the horizontal and vertical axes.

        Parameters
        ----------
        horizontal_motor : str
            PV to assign to the horizontal axis.
        vertical_motor : str
            PV to assign to the vertical axis.
        """
        self.set_macro("horizontal_motor", horizontal_motor)
        self.set_macro("vertical_motor", vertical_motor)

    def _on_position_changed(self, axis: str, value: float) -> None:
        """
        Derive jog direction from consecutive position readbacks. Neither
        .TDIR nor MSTA's DIRECTION bit update on the SmarAct setup this was
        tested against, so the position delta is used instead.
        """
        last = self._last_position[axis]
        self._last_position[axis] = value

        if last is None or value == last:
            return

        self._apply_led_direction(axis, positive=value > last)

    def _apply_led_direction(self, axis: str, positive: bool) -> None:
        """
        Translate a position-readback delta into this axis's on-screen
        up/down or left/right arrow, respecting the invert checkbox.

        positive=True means the readback increased since the last update.
        """
        inverted = getattr(self, f"{axis}_invert").isChecked()
        show_positive_side = positive != inverted

        if axis == "vertical":
            direction = "up" if show_positive_side else "down"
        else:
            direction = "right" if show_positive_side else "left"

        getattr(self, f"{axis}_status_led").set_move_direction(direction)

    def _invert_vertical(self) -> None:
        """Swap the forward and reverse buttons for the vertical axis"""
        self._invert_axis_channel("vertical")

    def _invert_horizontal(self) -> None:
        """Swap the forward and reverse buttons for the horizontal axis"""
        self._invert_axis_channel("horizontal")
