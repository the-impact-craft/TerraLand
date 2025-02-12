import asyncio
import atexit
import os
from datetime import datetime
from pathlib import Path
from typing import List

from dependency_injector.wiring import inject, Provide
from textual import work, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, Container
from textual.css.query import NoMatches
from textual.widgets import Footer, Label
from textual.widgets import RichLog
from textual.widgets._toggle_button import ToggleButton
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from terry.domain.terraform.core.entities import (
    TerraformVersion,
)
from terry.domain.terraform.workspaces.entities import Workspace
from terry.infrastructure.file_system.exceptions import ReadFileException
from terry.infrastructure.file_system.services import FileSystemService
from terry.infrastructure.operation_system.services import OperationSystemService
from terry.infrastructure.shared.command_utils import clean_up_command_output
from terry.infrastructure.terraform.core.exceptions import (
    TerraformVersionException,
)
from terry.infrastructure.terraform.core.services import TerraformCoreService
from terry.infrastructure.terraform.workspace.exceptions import (
    TerraformWorkspaceListException,
    TerraformWorkspaceSwitchException,
)
from terry.infrastructure.terraform.workspace.services import WorkspaceService
from terry.presentation.cli.custom.messages.files_select_message import FileSelect
from terry.presentation.cli.custom.widgets.resizable_rule import ResizingRule
from terry.presentation.cli.di_container import DiContainer
from terry.presentation.cli.entities.terraform_command_executor import TerraformCommandExecutor
from terry.presentation.cli.screens.main.containers.commands_log import (
    CommandsLog,
)
from terry.presentation.cli.screens.main.containers.content import Content
from terry.presentation.cli.screens.main.containers.header import Header
from terry.presentation.cli.screens.main.containers.project_tree import ProjectTree
from terry.presentation.cli.screens.main.containers.state_files import StateFiles
from terry.presentation.cli.screens.main.containers.workspaces import Workspaces
from terry.presentation.cli.screens.main.mixins.auto_mixin import AutoMixin
from terry.presentation.cli.screens.main.mixins.resize_containers_watcher_mixin import ResizeContainersWatcherMixin
from terry.presentation.cli.screens.main.mixins.terraform_action_handler_mixin import TerraformActionHandlerMixin
from terry.presentation.cli.screens.search.main import SearchScreen
from terry.presentation.cli.themes.arctic import arctic_theme
from terry.presentation.cli.themes.github_dark import github_dark_theme
from terry.settings import (
    CommandStatus,
    TERRAFORM_MAIN_ACTIONS,
    TERRAFORM_ADDITIONAL_ACTIONS,
    SEVERITY_LEVEL_ERROR,
    SEVERITY_LEVEL_INFORMATION,
    DEFAULT_THEME,
)

STATUS_TO_COLOR: dict = {
    CommandStatus.SUCCESS: "green",
    CommandStatus.ERROR: "red",
}

STATUS_TO_ICON: dict = {
    CommandStatus.SUCCESS: "🟢",
    CommandStatus.ERROR: "🔴",
}


class Terry(App, AutoMixin, ResizeContainersWatcherMixin, TerraformActionHandlerMixin):
    """The main app for the Terry project."""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding(key="q", action="quit", description="Quit the app"),
        Binding(key="ctrl+f", action="open_modal", description="Search"),
    ]

    @inject
    def __init__(
        self,
        *args,
        work_dir: Path | str = Provide[DiContainer.config.work_dir],
        workspace_service: WorkspaceService = Provide[DiContainer.workspace_service],
        file_system_service: FileSystemService = Provide[DiContainer.file_system_service],
        terraform_core_service: TerraformCoreService = Provide[DiContainer.terraform_core_service],
        operation_system_service: OperationSystemService = Provide[DiContainer.operation_system_service],
        **kwargs,
    ):
        """
        Initialize the Terry App with a specified working directory.

        Parameters:
            work_dir (Path or str): The directory path for the application's workspace.
            workspace_service (WorkspaceService): The service for managing Terraform workspaces.
            file_system_service (FileSystemService): The service for interacting with the file system.
            operation_system_service (OperationSystemService): The service for interacting with the operating system.
            terraform_core_service (TerraformCoreService): The service for interacting with Terraform core commands.
            *args: Variable length argument list to pass to the parent App constructor.
            **kwargs: Arbitrary keyword arguments to pass to the parent App constructor.
        Attributes:
            work_dir (Path): Normalized working directory path.
            workspace (str): Initial workspace name, set to "default".
        """
        super().__init__(*args, **kwargs)
        self.work_dir: Path = work_dir if isinstance(work_dir, Path) else Path(work_dir)

        self.observer = None
        self.workspaces: List[Workspace] = []
        self.selected_workspace: Workspace | None = None
        self.terraform_version: TerraformVersion | None = None
        self.updated_events_count: int = 0
        self._tf_command_executor: TerraformCommandExecutor | None = None

        self.workspace_service: WorkspaceService = workspace_service
        self.terraform_core_service: TerraformCoreService = terraform_core_service
        self.file_system_service: FileSystemService = file_system_service
        self.operation_system_service: OperationSystemService = operation_system_service

        # containers
        # Todo: create properties for these
        self.log_component: RichLog | None = None  # type: ignore
        self.workspaces_container: Workspaces | None = None
        self.project_tree_container: ProjectTree | None = None

        self.active_resizing_rule: ResizingRule | None = None

        self.validate_env()
        self.init_env()

        atexit.register(self.cleanup)

    def compose(self) -> ComposeResult:
        """
        Compose the user interface for the Terry application.

        This method sets up the application's layout and UI components, verifying the project type
        and rendering different views based on the project's status. It creates a structured
        terminal interface with multiple sections including workspaces, project tree, state files,
        search, content display, and command logs.

        Returns:
            ComposeResult: A generator yielding Textual UI components for rendering the application

        Raises:
            None explicitly, but may raise exceptions during component initialization

        Notes:
            - Hardcoded project verification (is_tf_project = True)
            - Supports multiple workspaces: default, development, staging, production
            - Dynamically lists state files from the working directory
            - Includes Header, Sidebar (Workspaces, ProjectTree, StateFiles),
              Content area (Search, Content), CommandsLog, and Footer
        """
        is_tf_project = True

        self.workspaces_container = Workspaces(id="workspaces")
        self.workspaces_container.selected_workspace = self.selected_workspace
        self.workspaces_container.workspaces = self.workspaces

        self.project_tree_container = ProjectTree(id="project_tree", work_dir=self.work_dir)

        if not is_tf_project:
            yield Container(
                Label("💥 Failed to verify terraform project 💥 "),
                id="tf-error-message",
            )
        else:
            state_files = self.file_system_service.list_state_files()
            yield Header(TERRAFORM_MAIN_ACTIONS, TERRAFORM_ADDITIONAL_ACTIONS, id="header")
            with Horizontal(id="main_container"):
                with Vertical(id="sidebar"):
                    yield self.workspaces_container
                    yield ResizingRule(
                        id="resize-workspaces-project_tree",
                        orientation="horizontal",
                        classes="resize-handle",
                        prev_component_id="workspaces",
                        next_component_id="project_tree",
                    )
                    yield self.project_tree_container
                    yield ResizingRule(
                        id="resize-project_tree-state_files",
                        orientation="horizontal",
                        classes="resize-handle",
                        prev_component_id="project_tree",
                        next_component_id="state_files",
                    )
                    yield StateFiles(id="state_files", state_files=state_files)
                yield ResizingRule(
                    id="resize-sidebar-right_container",
                    orientation="vertical",
                    classes="resize-handle",
                    prev_component_id="sidebar",
                    next_component_id="right_container",
                )
                with Vertical(id="right_container"):
                    yield Content(id="content")
                    yield ResizingRule(
                        id="resize-content-commands_log",
                        orientation="horizontal",
                        classes="resize-handle",
                        prev_component_id="content",
                        next_component_id="commands_log",
                    )
                    yield CommandsLog(id="commands_log", content="log")
            yield Footer()

    async def on_mount(self):
        """
        Handles the mounting process for the application. This method is asynchronously invoked
        when the application is mounted and performs necessary initialization tasks such as
        starting the system events monitoring and synchronization monitoring.
        """
        self.register_theme(arctic_theme)  # pyright: ignore [reportArgumentType]
        self.register_theme(github_dark_theme)  # pyright: ignore [reportArgumentType]

        self.theme = DEFAULT_THEME
        self.start_system_events_monitoring()
        self.start_sync_monitoring()

        try:
            self.log_component: RichLog = self.query_one(f"#{CommandsLog.LOG_COMPONENT_ID}")  # type: ignore
        except NoMatches:
            return

    def action_open_modal(self) -> None:
        """
        Open the search modal for the current working directory.

        This method pushes a SearchScreen onto the application's screen stack, initializing it with the current working
        directory. The search modal allows users to search and interact with files within the project.

        Returns:
            None
        """
        self.push_screen(SearchScreen(self.work_dir))

    def write_command_log(self, message: str, status: CommandStatus, details: str = "") -> None:
        """
        Write a command log entry to the application's command log component.

        Parameters:
            message (str): The command or action being logged.
            status (CommandStatus): The execution status of the command ('SUCCESS' or 'ERROR').

        Writes two log entries to the CommandsLog component:
            1. A basic command log with the message
            2. A detailed log entry with timestamp, message, and color-coded status

        The log entries use rich text formatting to highlight the message and status:
            - Successful commands are displayed in green with a checkmark (✅)
            - Error commands are displayed in red with a cross (❌)
            - Timestamps are displayed in a neutral gray color

        Side Effects:
            - Updates the CommandsLog widget with formatted log entries
        """
        if not self.log_component:
            try:
                self.log_component: RichLog = self.query_one(f"#{CommandsLog.LOG_COMPONENT_ID}")  # type: ignore
            except NoMatches:
                return

        self.log_component.write(f"~$: [bold]{message}[/bold]")
        self.log_component.write(
            f"{STATUS_TO_ICON.get(status)} [#808080]{datetime.now()} {message} [/#808080][{status.name}]"
        )
        if details:
            self.log_component.write(f"[#808080]{details}[/#808080]")

    def increment_updated_events(self, event: FileSystemEvent):
        """
        Increments the count of updated events for internal tracking purposes. This
        method modifies the internal `updated_events_count` attribute by adding one
        each time it is invoked.

        :param event: The event object whose update triggers the increment.
        :type event: object
        """
        self.updated_events_count += 1

    def update_selected_file_content(self, event: FileSystemEvent):
        """
        Updates the content of a selected file when a modification event occurs.

        Args:
            event (FileSystemEvent): The file system event containing information about the modified file.

        Note:
            This method only processes modification events for files that are currently open in the editor.
            It ignores directory events and non-modification events.
        """
        if not isinstance(event, FileSystemEvent):
            return
        if event.is_directory:
            return
        if event.event_type != "modified":
            return

        abs_changed_file_path = Path(event.src_path.decode() if isinstance(event.src_path, bytes) else event.src_path)
        if not abs_changed_file_path.exists():
            return

        changed_file_path = str(abs_changed_file_path.relative_to(self.work_dir))
        try:
            content_tabs = self.query_one(Content)
        except NoMatches:
            return
        if changed_file_path not in content_tabs.files_contents:
            return

        content = abs_changed_file_path.read_text()
        content_tabs.update(changed_file_path, content)

    def cleanup(self):
        """Stop and cleanup the file system observer."""
        if getattr(self, "observer"):
            if self.observer is None:
                return
            self.observer.stop()
            self.observer.join()
        if self._tf_command_executor:
            self._tf_command_executor.cancel()

    # ------------------------------------------------------------------------------------------------------------------
    # Environment methods
    # ------------------------------------------------------------------------------------------------------------------

    def validate_env(self):
        """
        Validate the environment before running the application.

        This method performs environment validation checks before running the application.
        It verifies that the working directory is valid and that the project is a Terraform project.

        Raises:
            ValueError: If the working directory is invalid or the project is not a Terraform project.
        """
        self._validate_work_dir(self.work_dir)
        self._validate_terraform()

    def init_env(self):
        """
        Initializes the workspace environment. This method sets up the workspace
        by listing the directories within the provided working directory and sets
        the last synchronization date to the current date and time.
        """
        try:
            self.workspaces = self.workspace_service.list().workspaces
            self.selected_workspace = next((w for w in self.workspaces if w.is_active), None)
        except TerraformWorkspaceListException as e:
            self.notify(clean_up_command_output(str(e)), severity="error")
            self.log.error(str(e))

    def refresh_env(self):
        """
        Refreshes the workspace environments and updates the associated UI components.

        This method initiates the processing of refreshing the environment by notifying
        the user, fetching the list of available workspaces, and then updating the
        corresponding UI elements to reflect the latest environment state. Any relevant
        changes such as the last synchronization date or the workspace directory are
        reflected in the application.

        :raises RuntimeError: If the workspace directory does not exist or cannot be accessed.
        """

        # Todo: split to separate methods
        try:
            self.workspaces = self.workspace_service.list().workspaces
        except TerraformWorkspaceListException as e:
            self.notify(str(e), severity="error")
            self.log.error(str(e))
            return

        if not self.workspaces_container:
            try:
                self.workspaces_container = self.query_one(Workspaces)
            except NoMatches:
                self.notify("Workspaces container not found.")
                return

        selected_workspace = next((w for w in self.workspaces if w.is_active), None)
        if selected_workspace and (
            not self.workspaces_container.selected_workspace
            or self.workspaces_container.selected_workspace.name != selected_workspace.name
        ):
            self.workspaces_container.workspaces = self.workspaces

        if not self.project_tree_container:
            try:
                self.project_tree_container = self.query_one(ProjectTree)
            except NoMatches:
                self.notify("Project tree container not found.")
                return

        if not self.project_tree_container:
            return

        work_dir_tree = self.project_tree_container.work_dir_tree
        if work_dir_tree:
            work_dir_tree.reload()

    # ------------------------------------------------------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------------------------------------------------------

    @on(FileSelect)
    async def on_file_double_clicked_event(self, message: FileSelect) -> None:
        """
        Handle the file double-clicked event in the Terry application.

        This method is triggered when a file is double-clicked in the project tree. It performs several key operations:
        - Validates and normalizes the file path relative to the working directory
        - Checks file path validity and existence
        - Reads the file content
        - Updates the Content widget with the file's text and path
        - Handles potential errors during file reading

        Args:
            message (FileSelect): Event containing the path of the double-clicked file

        Raises:
            ValueError: If the file path is not a Path object
            FileNotFoundError: If the specified file does not exist
            Exception: If there are issues reading the file content

        Side Effects:
            - Logs an info message about content refresh
            - Notifies user of file opening errors
            - Updates the Content widget with file contents and path
        """
        file_path = message.path
        if not str(file_path).startswith(str(self.work_dir)):
            file_path = self.work_dir / file_path

        self.log.info("Refreshing content component")

        try:
            content = self.file_system_service.read(file_path)
        except ReadFileException as e:
            self.notify(str(e), severity="error")
            return
        else:
            file_path = file_path.relative_to(self.work_dir)
            await self.query_one(Content).add(str(file_path), content, message.line)

    @on(Workspaces.SelectEvent)
    def handle_workspace_select(self, message: Workspaces.SelectEvent) -> None:
        """
        Handle the workspace change event in the Terry application.

        This method updates the current workspace, simulates a workspace selection status, and provides user
        notifications. It performs the following actions:
        - Updates the application's current workspace
        - Sends a notification to the user about the workspace change
        - Logs the workspace selection command with its status

        Parameters:
            message (Workspaces.SelectEvent): An event containing the new workspace name

        Side Effects:
            - Updates `self.workspace`
            - Triggers a user notification
            - Writes a command log entry

        Note:
            This is currently a simulated implementation with random status generation.
            Future improvements should include actual Terraform workspace selection logic.
        """
        workspace = message.workspace
        try:
            self.workspace_service.switch(workspace.name)
        except TerraformWorkspaceSwitchException as e:
            self.notify(str(e), severity="error")
            status = CommandStatus.ERROR
            self.notify(
                f"Failed to switch workspace to {workspace.name}.",
                severity=SEVERITY_LEVEL_ERROR,
            )
            if not self.selected_workspace:
                return
            try:
                workspaces_container: Workspaces = self.query_one(Workspaces)
                previous_workspace_toggle: ToggleButton = self.query_one(f"#{workspace.uuid}")  # type: ignore
                selected_workspace_toggle: ToggleButton = self.query_one(f"#{self.selected_workspace.uuid}")  # type: ignore
            except NoMatches:
                return

            workspaces_container.selected_workspace = self.selected_workspace
            previous_workspace_toggle.value = False
            selected_workspace_toggle.value = True

        else:
            status = CommandStatus.SUCCESS
            self.notify(
                f"Workspace has been changed to {workspace.name}.",
                severity=SEVERITY_LEVEL_INFORMATION,
            )
            self.selected_workspace = workspace

        log_message = f"terraform workspace select {workspace.name}"
        self.write_command_log(log_message, status, log_message)
        self.init_env()

    # ------------------------------------------------------------------------------------------------------------------
    # Validation methods
    # ------------------------------------------------------------------------------------------------------------------

    def _validate_work_dir(self, path) -> None:
        """
        Validate the provided working directory path.

        This method checks if the provided path exists, is a directory, and is readable.

        Parameters:
            path (Path): The working directory path to validate.

        Raises:
            ValueError: If the path does not exist or is not a directory.
            PermissionError: If the directory is not readable.

        """
        if not path.exists():
            raise ValueError(f"Directory does not exist: {path}")

        if not path.is_dir():
            raise ValueError(f"Path is not a directory: {path}")

        if not os.access(path, os.R_OK):
            raise PermissionError(f"Directory is not readable: {path}")

    def _validate_terraform(self):
        """
        Validate the Terraform installation in the current environment.

        This method checks if the Terraform binary is available in the system PATH.

        Raises:
            RuntimeError: If the Terraform binary is not found in the system PATH.
            RuntimeError: If the version output cannot be parsed.
        """

        try:
            self.terraform_version = self.terraform_core_service.version()
        except TerraformVersionException as e:
            error_message = f"""Terraform seems to be not installed. 
                Please install Terraform to use this application. 
                Details: {str(e)}"""
            raise RuntimeError(error_message)

    # ------------------------------------------------------------------------------------------------------------------
    # Async workers
    # ------------------------------------------------------------------------------------------------------------------

    @work()
    async def start_system_events_monitoring(self):
        """
        Asynchronously starts monitoring system events within a specific directory.

        This function utilizes an observer pattern to monitor file system events such
        as creation, modification, deletion, or movement within the specified directory.
        When an event is detected, the following handlers are invoked in order:
        1. increment_updated_events: Tracks the number of file system events
        2. update_selected_file_content: Updates the UI when monitored files change

        The monitoring process runs continuously until explicitly stopped or interrupted.
        """

        class EventHandler(FileSystemEventHandler):
            def __init__(self, handlers: list[callable], *args, **kwargs):  # type: ignore
                super().__init__(*args, **kwargs)
                self.handlers = handlers

            def on_any_event(self, event: FileSystemEvent) -> None:
                for handler in self.handlers:
                    handler(event)

        event_handler = EventHandler(
            [
                self.increment_updated_events,
                self.update_selected_file_content,
            ]
        )
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.work_dir), recursive=True)
        self.observer.start()
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            self.cleanup()

    @work()
    async def start_sync_monitoring(self):
        """
        This method asynchronously starts monitoring for system updates. It checks periodically if any system
        updates have been detected. After processing the updates, it resets the update counter, updates the last
        synchronization date, and refreshes the environment settings. The monitoring loop runs indefinitely unless
        stopped by external control or exceptions. It ensures to log a warning message when the monitoring ceases.
        """
        try:
            while True:
                await asyncio.sleep(1)
                if self.updated_events_count > 0:
                    self.updated_events_count = 0
                    self.refresh_env()
        finally:
            self.log.warning("System updates monitoring stopped")
