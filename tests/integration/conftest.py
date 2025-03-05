from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest

from terraland.domain.file_system.entities import ListDirOutput
from terraland.domain.operation_system.entities import OperationSystem, Variable
from terraland.domain.terraform.core.entities import TerraformVersion
from terraland.domain.terraform.workspaces.entities import WorkspaceListOutput, Workspace
from terraland.presentation.cli.di_container import DiContainer
from terraland.presentation.cli.screens.main.main import TerraLand


@pytest.fixture
def workspaces_list():
    """
    Returns a list of workspaces with their respective statuses.

    :rtype: list[Workspace]
    :return: A list of workspaces with their respective statuses.
    """
    return [
        Workspace(name="default", is_active=False, uuid="id-default"),
        Workspace(name="development", is_active=True, uuid="id-development"),
        Workspace(name="production", is_active=False, uuid="id-production"),
    ]


@pytest.fixture
def workspace_service(workspaces_list) -> mock.Mock:
    """
    This pytest fixture creates a mock object for the workspace service. The returned
    mock object simulates the behavior of a workspace service, including providing a
    list of workspaces with their statuses and handling workspace switching operations.
    This allows testing code that depends on a workspace service without relying on
    external dependencies.

    :rtype: mock.Mock
    :return: A mock object that provides methods for listing workspaces with their
        respective statuses and switching between workspaces. Specifically, it includes:

        - list: Returns a predefined ``WorkspaceListOutput`` object containing mock data
          about workspaces and the executed command.
        - switch: Simulates switching a workspace and returns ``None``.
    """
    workspace_service = mock.Mock()
    workspace_service.list.return_value = WorkspaceListOutput(
        workspaces=workspaces_list,
        command="terraform workspace list",
    )
    workspace_service.switch.return_value = None
    return workspace_service


@pytest.fixture
def workspace_service_with_switch_sideeffect(workspace_service):
    workspace_service.switch.side_effect = Exception("Failed to switch workspace")
    return workspace_service


@pytest.fixture
def terraform_core_service() -> mock.Mock:
    """
    Fixture for providing a mock Terraform core service used in unit testing. The mocked
    service replicates behavior of Terraform core utility methods, including returning
    specific versions, platform details, and results of running Terraform commands
    like `terraform format`.

    The fixture ensures to mimic a controlled Terraform environment for testing purposes,
    allowing developers to verify functionality and interactions without relying on
    external systems.

    :return: A mocked Terraform core service mimicking behavior of utility methods.
    :rtype: mock.Mock
    """
    terraform_core_service = mock.Mock()
    terraform_core_service.version.return_value = TerraformVersion(
        terraform_version="v1.0.0",
        platform="darwin",
        terraform_outdated=False,
        provider_selections={},
        command="terraform version",
    )
    return terraform_core_service


@pytest.fixture
def file_system_service(tmp_path) -> mock.Mock:
    """
    Returns a mock object simulating a file system service.

    This pytest fixture creates and configures a mock object that simulates the
    behavior of a file system service. The mock is pre-configured to return a
    specific list of state files and a pre-defined mock result for a grep-like
    functionality.

    :rtype: mock.Mock
    :return: A mock object simulating a file system service.
    """
    file_system_service = mock.Mock()

    file_system_service.list_state_files.return_value = ["test1.tfstate", "subfolder/test2.tfstate"]
    file_system_service.grep.return_value = mock.Mock(
        total=2,
        output=[
            mock.Mock(file="main.tf", line_number=5, line="resource aws_s3"),
            mock.Mock(file="file.tf", line_number=1, line="resource aws_instance"),
        ],
        pattern="resource",
    )
    file_system_service.list_dir.return_value = ListDirOutput(
        directories=[tmp_path / Path("test/work/dir/folder1")], files=[tmp_path / Path("test/work/dir/file1.txt")]
    )
    return file_system_service


@pytest.fixture
def operation_system_service() -> mock.Mock:
    """
    Provides a pytest fixture for a mock object simulating an Operation System Service.

    This fixture facilitates the creation of a mock object to be used in testing
    scenarios where an OS-based service needs to be simulated. The returned mock
    object allows for the manipulation and observation of interactions during
    testing, ensuring the service can be adequately tested without relying on
    actual OS-level functionality.

    Returns:
        mock.Mock: A mock object representing an Operation System Service.
    """

    operation_system_service = mock.Mock()
    operation_system_service.get_operation_system.return_value = OperationSystem(
        name="Darwin",
        version="20.6.0",
    )
    operation_system_service.list_environment_variables.return_value = [
        Variable(name="TF_VAR_region", value="us-west-2"),
        Variable(name="AWS_PROFILE", value="default"),
    ]

    return operation_system_service


@pytest.fixture
def app(
    tmp_path: Path,
    workspace_service: mock.Mock,
    terraform_core_service: mock.Mock,
    file_system_service: mock.Mock,
    operation_system_service: mock.Mock,
):
    """
    Creates and initializes an TerraLand instance configured with the provided mock
    services and temporary path. This pytest fixture is responsible for setting up
    dependency injection and overriding the necessary services within the DI
    container to facilitate unit testing.

    :param tmp_path: A temporary file system path used to represent work directory.
    :param workspace_service: A mocked service for workspace-related operations.
    :param terraform_core_service: A mocked service for Terraform's core functionalities.
    :param file_system_service: A mocked service for file system-related operations.
    :param operation_system_service: A mocked service for OS-related operations.
    :return: An instance of TerraLand configured for testing.
    """

    cache_mock = MagicMock()
    cache_mock.get.return_value = []
    di_container = DiContainer()
    di_container.config.work_dir.from_value(tmp_path)
    di_container.config.animation_enabled.from_value(True)
    di_container.cache.override(cache_mock)
    di_container.wire(packages=["terraland.presentation.cli", "tests"])

    with (
        di_container.workspace_service.override(workspace_service),
        di_container.file_system_service.override(file_system_service),
        di_container.operation_system_service.override(operation_system_service),
        di_container.terraform_core_service.override(terraform_core_service),
    ):
        return TerraLand(tmp_path)
