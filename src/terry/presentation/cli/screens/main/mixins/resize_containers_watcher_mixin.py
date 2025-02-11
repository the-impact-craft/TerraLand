import time

from textual.events import MouseMove, MouseUp

from terry.presentation.cli.custom.messages.move_resizing_rule import (
    MoveEvent,
    MoveResizingRule, 
    SelectResizingRule, 
    ReleaseResizingRule, 
)
from terry.presentation.cli.custom.widgets.resizable_rule import ResizingRule
from terry.settings import MIN_SECTION_DIMENSION


class ResizeContainersWatcherMixin:
    """
    Watches and handles the resizing of containers in a user interface.

    The ResizeContainersWatcher class provides functionality to manage and respond
    to events related to resizing operations. It handles events for selecting,
    moving, and releasing resizing rules, ensuring that the resizing operation is
    properly executed and the state is consistently maintained. This class also
    manages interactions like dragging and concludes resizing tasks when necessary.

    Class should be used as a mixin in classes that require resizing functionality 
    and is inherited from textual App. 
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_resizing_rule: ResizingRule | None = None

    def on_select_resizing_rule(self, event: SelectResizingRule) -> None:
        """
        Handle the selection of a resizing rule by updating the active resizing rule.
        Called when the user press a resizing rule.

        Arguments:
            event (SelectResizingRule): The event containing the resizing rule id.
        """
        self.active_resizing_rule = self.query_one(f'#{event.id}')

    def on_release_resizing_rule(self, _: ReleaseResizingRule) -> None:
        """
        Handle the release (completion) of the resizing rule.

        This method is responsible for resetting the active resizing rule
        back to None when a resizing operation is concluded. It ensures
        that the system correctly cleans up and transitions out of the
        resizing state.

        Arguments:
            _: The `ReleaseResizingRule` object that represents the
               event or the logic related to ending the resizing operation.
        """
        self.active_resizing_rule = None

    def on_move_resizing_rule(self, event: MoveResizingRule) -> None:
        """
        Handles the resizing of components during a move event and updates their styles
        accordingly. This method adjusts the width or height of the components involved
        based on the orientation and the delta value from the event, ensuring minimum
        dimensions are maintained. Refreshes the display after updating the styles.

        Arguments:
            event (MoveResizingRule): The event containing the resizing rule details.
            
        """

        prev_component = self.query_one(f'#{event.previous_component_id}')
        next_component = self.query_one(f'#{event.next_component_id}')

        delta = event.delta

        if event.orientation == "vertical":
            prev_component.styles.width = f"{max(MIN_SECTION_DIMENSION, prev_component.styles.width.value + delta)}%"
            next_component.styles.width = f"{max(MIN_SECTION_DIMENSION, next_component.styles.width.value - delta)}%"
        elif event.orientation == "horizontal":
            prev_component.styles.height = f"{max(MIN_SECTION_DIMENSION, prev_component.styles.height.value + delta)}%"
            next_component.styles.height = f"{max(MIN_SECTION_DIMENSION, next_component.styles.height.value - delta)}%"

        self.refresh()

    def on_mouse_move(self, event: MouseMove) -> None:
        """
        Handles mouse movement events to manage resizing operations based on the active resizing 
        rule. The function determines the change in position (delta) either horizontally or 
        vertically, depending on the orientation of the active resizing rule. If the resizing 
        rule is active and dragging is occurring, it updates the position accordingly.
        
        Arguments:
            event (MouseMove): The event containing the mouse movement details.
        """

        if not self.active_resizing_rule or not self.active_resizing_rule.dragging:
            return

        try:
            delta = event.delta_x if self.active_resizing_rule.orientation == "vertical" else event.delta_y
        except AttributeError:
            return  # No delta

        self.active_resizing_rule.position = MoveEvent(timestamp=time.time(), delta=delta)

    def on_mouse_up(self, _: MouseUp) -> None:
        """
        Handles the mouse-up event during a resizing rule interaction.

        This method is invoked when the mouse-up event is detected. It checks if
        there is an active resizing rule in operation. If such a rule exists, the
        method ensures proper cleanup activities are performed and then clears the
        active resizing rule, resetting it to None.

        Arguments:
            _: The `MouseUp` object that represents the mouse-up event.
        """
        if not self.active_resizing_rule:
            return
        self.active_resizing_rule.cleanup()
        self.active_resizing_rule = None
