""" A script to run integration tests for DLT-Meta."""

# Import necessary modules
import uuid
import argparse
import os
import webbrowser
from dataclasses import dataclass
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.pipelines import PipelineLibrary, NotebookLibrary
from databricks.sdk.service import jobs, compute
from src.__about__ import __version__
from databricks.sdk.service.workspace import ImportFormat, Language
from databricks.sdk.service.catalog import SchemasAPI, VolumeInfo, VolumeType
from src.install import WorkspaceInstaller

import json

# Dictionary mapping cloud providers to node types
cloud_node_type_id_dict = {"aws": "i3.xlarge", "azure": "Standard_D3_v2", "gcp": "n1-highmem-4"}

DLT_META_RUNNER_NOTEBOOK = """
# Databricks notebook source
# MAGIC %pip install {remote_wheel}
# dbutils.library.restartPython()
# COMMAND ----------
layer = spark.conf.get("layer", None)
from src.dataflow_pipeline import DataflowPipeline
DataflowPipeline.invoke_dlt_pipeline(spark, layer)
"""


@dataclass
class DLTMetaRunnerConf:
    """
    A class to hold information required for running integration tests.

    Attributes:
    -----------
    run_id : str
        The ID of the test run.
    username : str, optional
        The username to use for the test run.
    uc_catalog_name : str, optional
        The name of the unified catalog to use for the test run.
    onboarding_file_path : str, optional
        The path to the onboarding file to use for the test run.
    dbfs_tmp_path : str, optional
        The temporary DBFS path to use for the test run.
    int_tests_dir : str, optional
        The directory containing the integration tests.
    dlt_meta_schema : str, optional
        The name of the DLT meta schema to use for the test run.
    bronze_schema : str, optional
        The name of the bronze schema to use for the test run.
    silver_schema : str, optional
        The name of the silver schema to use for the test run.
    runners_nb_path : str, optional
        The path to the runners notebook.
    runners_full_local_path : str, optional
        The full local path to the runners notebook.
    source : str, optional
        The source to use for the test run.
    cloudfiles_template : str, optional
        The cloudfiles template to use for the test run.
    eventhub_template : str, optional
        The eventhub template to use for the test run.
    kafka_template : str, optional
        The Kafka template to use for the test run.
    env : str, optional
        The environment to use for the test run.
    whl_path : str, optional
        The path to the whl file to use for the test run.
    volume_info : VolumeInfo, optional
        The volume information to use for the test run.
    uc_volume_path : str, optional
        The path to the unified volume to use for the test run.
    uc_target_whl_path : str, optional
        The path to the unified catalog target whl file to use for the test run.
    dbfs_whl_path : str, optional
        The path to the DBFS whl file to use for the test run.
    node_type_id : str, optional
        The node type ID to use for the test run.
    dbr_version : str, optional
        The Databricks runtime version to use for the test run.
    bronze_pipeline_id : str, optional
        The ID of the bronze pipeline to use for the test run.
    silver_pipeline_id : str, optional
        The ID of the silver pipeline to use for the test run.
    cluster_id : str, optional
        The ID of the cluster to use for the test run.
    job_id : str, optional
        The ID of the job to use for the test run.
    """
    run_id: str
    username: str = None
    run_name: str = None
    uc_catalog_name: str = None
    uc_volume_name: str = "dlt_meta_files"
    onboarding_file_path: str = "integration_tests/conf/onboarding.json"
    onboarding_A2_file_path: str = "integration_tests/conf/onboarding_A2.json"
    onboarding_fanout_file_path: str = "integration_tests/conf/onboarding.json"
    int_tests_dir: str = "integration_tests"
    dlt_meta_schema: str = None
    bronze_schema: str = None
    silver_schema: str = None
    runners_nb_path: str = None
    runners_full_local_path: str = None
    source: str = None
    cloudfiles_template: str = "integration_tests/conf/cloudfiles-onboarding.template"
    cloudfiles_A2_template: str = "integration_tests/conf/cloudfiles-onboarding_A2.template"
    #onboarding_fanout_templates: str = None
    eventhub_template: str = "integration_tests/conf/eventhub-onboarding.template",
    eventhub_input_data: str = None
    eventhub_append_flow_input_data: str = None
    kafka_template: str = "integration_tests/conf/kafka-onboarding.template"
    env: str = "it"
    whl_path: str = None
    volume_info: VolumeInfo = None
    uc_volume_path: str = None
    uc_target_whl_path: str = None
    remote_whl_path: str = None
    dbfs_whl_path: str = None
    node_type_id: str = None
    dbr_version: str = None
    bronze_pipeline_id: str = None
    bronze_pipeline_A2_id: str = None
    silver_pipeline_id: str = None
    cluster_id: str = None
    job_id: str = None
    test_output_file_path: str = None


class DLTMETARunner:
    """
    A class to run integration tests for DLT-Meta.

    Attributes:
    - args: command line arguments
    - workspace_client: Databricks workspace client
    - runner_conf: test information
    """
    def __init__(self, args: dict[str: str], ws, base_dir):
        self.args = args
        self.ws = ws
        self.wsi = WorkspaceInstaller(ws)
        self.base_dir = base_dir

    def init_runner_conf(self) -> DLTMetaRunnerConf:
        """
        Initialize the runner configuration for running integration tests.

        Returns:
        -------
        DLTMetaRunnerConf
            The initialized runner configuration.
        """
        run_id = uuid.uuid4().hex
        runner_conf = DLTMetaRunnerConf(
            run_id=run_id,
            username=self.wsi._my_username,
            uc_catalog_name=self.args["uc_catalog_name"],
            dlt_meta_schema=f"dlt_meta_dataflowspecs_it_{run_id}",
            bronze_schema=f"dlt_meta_bronze_it_{run_id}",
            silver_schema=f"dlt_meta_silver_it_{run_id}",
            runners_nb_path=f"/Users/{self.wsi._my_username}/dlt_meta_int_tests/{run_id}",
            source=self.args["source"],
            node_type_id=cloud_node_type_id_dict[self.args["cloud_provider_name"]],
            test_output_file_path=(
                f"/Users/{self.wsi._my_username}/dlt_meta_int_tests/"
                f"{run_id}/integration-test-output.csv"
            ),
        )

        # Set the proper directory location for the notebooks that need to be uploaded to run and
        # validate the integration tests
        source_paths = {
            "cloudfiles": "./integration_tests/notebooks/cloudfile_runners/",
            "eventhub": "./integration_tests/notebooks/eventhub_runners/",
            "kafka": "./integration_tests/notebooks/kafka_runners/",
        }
        try:
            runner_conf.runners_full_local_path = source_paths[runner_conf.source]
        except KeyError:
            raise Exception("Given source is not support. Support source are: cloudfiles, eventhub, or kafka")

        return runner_conf

    def _install_folder(self):
        return f"/Users/{self.wsi._my_username}/dlt-meta"

    def _my_username(self, ws):
        if not hasattr(ws, "_me"):
            _me = ws.current_user.me()
        return _me.user_name

    def create_dlt_meta_pipeline(self,
                                 pipeline_name: str,
                                 layer: str,
                                 group: str,
                                 target_schema: str,
                                 runner_conf: DLTMetaRunnerConf
                                 ):
        """
        Create a DLT pipeline.

        Parameters:
        ----------
        pipeline_name : str
            The name of the pipeline.
        layer : str
            The layer of the pipeline.
        target_schema : str
            The target schema of the pipeline.
        runner_conf : DLTMetaRunnerConf
            The runner configuration.

        Returns:
        -------
        str
            The ID of the created pipeline.

        Raises:
        ------
        Exception
            If the pipeline creation fails.
        """
        configuration = {
            "layer": layer,
            f"{layer}.group": group,
            "dlt_meta_whl": runner_conf.remote_whl_path,
        }
        created = None
        if runner_conf.uc_catalog_name:
            configuration[f"{layer}.dataflowspecTable"] = (
                f"{runner_conf.uc_catalog_name}.{runner_conf.dlt_meta_schema}.{layer}_dataflowspec_cdc"
            )
            created = self.ws.pipelines.create(
                catalog=runner_conf.uc_catalog_name,
                name=pipeline_name,
                serverless=True,
                configuration=configuration,
                libraries=[
                    PipelineLibrary(
                        notebook=NotebookLibrary(
                            path=f"{runner_conf.runners_nb_path}/runners/init_dlt_meta_pipeline"
                        )
                    )
                ],
                target=target_schema
            )
        else:
            configuration[f"{layer}.dataflowspecTable"] = (
                f"{runner_conf.dlt_meta_schema}.{layer}_dataflowspec_cdc"
            )
            created = self.ws.pipelines.create(
                name=pipeline_name,
                serverless=True,
                channel="PREVIEW",
                configuration=configuration,
                libraries=[
                    PipelineLibrary(
                        notebook=NotebookLibrary(
                            path=f"{runner_conf.runners_nb_path}/runners/init_dlt_meta_pipeline"
                        )
                    )
                ],
                target=target_schema
            )
        if created is None:
            raise Exception("Pipeline creation failed")
        return created.pipeline_id

    def create_cloudfiles_workflow_spec(self, runner_conf: DLTMetaRunnerConf):
        """
        Create the CloudFiles workflow specification.

        Parameters:
        ----------
        runner_conf : DLTMetaRunnerConf
            The runner configuration.

        Returns:
        -------
        Job
            The created job.

        Raises:
        ------
        Exception
            If the job creation fails.
        """
        dltmeta_environments = [
            jobs.JobEnvironment(
                environment_key="dl_meta_int_env",
                spec=compute.Environment(client=f"dlt_meta_int_test_{__version__}",
                                         dependencies=[runner_conf.remote_whl_path]
                                         )
            )
        ]
        return self.ws.jobs.create(
            name=f"dlt-meta-{runner_conf.run_id}",
            environments=dltmeta_environments,
            tasks=[
                jobs.Task(
                    task_key="setup_dlt_meta_pipeline_spec",
                    environment_key="dl_meta_int_env",
                    description="test",
                    timeout_seconds=0,
                    python_wheel_task=jobs.PythonWheelTask(
                        package_name="dlt_meta",
                        entry_point="run",
                        named_parameters={
                            "onboard_layer": "bronze_silver",
                            "database": f"{runner_conf.uc_catalog_name}.{runner_conf.dlt_meta_schema}",
                            "onboarding_file_path":
                            f"{runner_conf.uc_volume_path}/{self.base_dir}/conf/onboarding.json",
                            "silver_dataflowspec_table": "silver_dataflowspec_cdc",
                            "silver_dataflowspec_path": f"{runner_conf.uc_volume_path}/data/dlt_spec/silver",
                            "bronze_dataflowspec_table": "bronze_dataflowspec_cdc",
                            "import_author": "Ravi",
                            "version": "v1",
                            "bronze_dataflowspec_path": f"{runner_conf.uc_volume_path}/data/dlt_spec/bronze",
                            "overwrite": "True",
                            "env": runner_conf.env,
                            "uc_enabled": "True"
                        },
                    )
                ),
                jobs.Task(
                    task_key="bronze_dlt_pipeline",
                    depends_on=[jobs.TaskDependency(task_key="setup_dlt_meta_pipeline_spec")],
                    pipeline_task=jobs.PipelineTask(
                        pipeline_id=runner_conf.bronze_pipeline_id
                    ),
                ),
                jobs.Task(
                    task_key="onboard_spec_A2",
                    depends_on=[jobs.TaskDependency(task_key="bronze_dlt_pipeline")],
                    description="test",
                    environment_key="dl_meta_int_env",
                    timeout_seconds=0,
                    python_wheel_task=jobs.PythonWheelTask(
                        package_name="dlt_meta",
                        entry_point="run",
                        named_parameters={
                            "onboard_layer": "bronze",
                            "database": f"{runner_conf.uc_catalog_name}.{runner_conf.dlt_meta_schema}",
                            "onboarding_file_path":
                            f"{runner_conf.uc_volume_path}/{self.base_dir}/conf/onboarding_A2.json",
                            "bronze_dataflowspec_table": "bronze_dataflowspec_cdc",
                            "import_author": "Ravi",
                            "version": "v1",
                            "overwrite": "False",
                            "env": runner_conf.env,
                            "uc_enabled": "True"
                        },
                    )
                ),
                jobs.Task(
                    task_key="bronze_A2_dlt_pipeline",
                    depends_on=[jobs.TaskDependency(task_key="onboard_spec_A2")],
                    pipeline_task=jobs.PipelineTask(
                        pipeline_id=runner_conf.bronze_pipeline_A2_id
                    ),
                ),
                jobs.Task(
                    task_key="silver_dlt_pipeline",
                    depends_on=[jobs.TaskDependency(task_key="bronze_A2_dlt_pipeline")],
                    pipeline_task=jobs.PipelineTask(
                        pipeline_id=runner_conf.silver_pipeline_id
                    )
                ),
                jobs.Task(
                    task_key="validate_results",
                    description="test",
                    depends_on=[jobs.TaskDependency(task_key="silver_dlt_pipeline")],
                    notebook_task=jobs.NotebookTask(
                        notebook_path=f"{runner_conf.runners_nb_path}/runners/validate",
                        base_parameters={
                            "uc_enabled": "True",
                            "uc_catalog_name": f"{runner_conf.uc_catalog_name}",
                            "bronze_schema": f"{runner_conf.bronze_schema}",
                            "silver_schema": f"{runner_conf.silver_schema}",
                            "output_file_path": f"/Workspace{runner_conf.test_output_file_path}",
                            "run_id": runner_conf.run_id
                        }
                    )

                ),
            ]
        )

    def create_eventhub_workflow_spec(self, runner_conf: DLTMetaRunnerConf):
        """Create Job specification."""
        dltmeta_environments = [
            jobs.JobEnvironment(
                environment_key="dl_meta_int_env",
                spec=compute.Environment(client=f"dlt_meta_int_test_{__version__}",
                                         dependencies=[runner_conf.remote_whl_path]
                                         )
            )
        ]
        return self.ws.jobs.create(
            name=f"dlt-meta-{runner_conf.run_id}",
            environments=dltmeta_environments,
            tasks=[
                jobs.Task(
                    task_key="setup_dlt_meta_pipeline_spec",
                    description="setup_dlt_meta_pipeline_spec",
                    environment_key="dl_meta_int_env",
                    timeout_seconds=0,
                    python_wheel_task=jobs.PythonWheelTask(
                        package_name="dlt_meta",
                        entry_point="run",
                        named_parameters={
                            "onboard_layer": "bronze",
                            "database": f"{runner_conf.uc_catalog_name}.{runner_conf.dlt_meta_schema}",
                            "onboarding_file_path":
                            f"{runner_conf.uc_volume_path}/{self.base_dir}/conf/onboarding.json",
                            "silver_dataflowspec_table": "silver_dataflowspec_cdc",
                            "bronze_dataflowspec_table": "bronze_dataflowspec_cdc",
                            "import_author": "Ravi",
                            "version": "v1",
                            "overwrite": "True",
                            "env": runner_conf.env,
                            "uc_enabled": "True"
                        }
                    )
                ),
                jobs.Task(
                    task_key="publish_events",
                    description="test",
                    depends_on=[jobs.TaskDependency(task_key="setup_dlt_meta_pipeline_spec")],
                    existing_cluster_id=runner_conf.cluster_id,
                    notebook_task=jobs.NotebookTask(
                        notebook_path=f"{runner_conf.runners_nb_path}/runners/publish_events",
                        base_parameters={
                            "eventhub_name": self.args["eventhub_name"],
                            "eventhub_name_append_flow": self.args["eventhub_name_append_flow"],
                            "eventhub_namespace": self.args["eventhub_namespace"],
                            "eventhub_secrets_scope_name": self.args["eventhub_secrets_scope_name"],
                            "eventhub_accesskey_name": self.args["eventhub_producer_accesskey_name"],
                            "eventhub_input_data":
                            f"/{runner_conf.uc_volume_path}/{self.base_dir}/resources/data/iot/iot.json",
                            "eventhub_append_flow_input_data":
                            f"/{runner_conf.uc_volume_path}/{self.base_dir}/resources/data/iot_eventhub_af/iot.json",
                        }
                    )
                ),
                jobs.Task(
                    task_key="bronze_dlt_pipeline",
                    depends_on=[jobs.TaskDependency(task_key="publish_events")],
                    pipeline_task=jobs.PipelineTask(
                        pipeline_id=runner_conf.bronze_pipeline_id
                    ),
                ),
                jobs.Task(
                    task_key="validate_results",
                    description="test",
                    depends_on=[jobs.TaskDependency(task_key="bronze_dlt_pipeline")],
                    notebook_task=jobs.NotebookTask(
                        notebook_path=f"{runner_conf.runners_nb_path}/runners/validate",
                        base_parameters={
                            "run_id": runner_conf.run_id,
                            "uc_enabled": "True",
                            "uc_catalog_name": runner_conf.uc_catalog_name,
                            "bronze_schema": runner_conf.bronze_schema,
                            "output_file_path": f"/Workspace{runner_conf.test_output_file_path}"
                        }
                    )
                )
            ]
        )

    def create_kafka_workflow_spec(self, runner_conf: DLTMetaRunnerConf):
        """Create Job specification."""
        dltmeta_environments = [
            jobs.JobEnvironment(
                environment_key="dl_meta_int_env",
                spec=compute.Environment(client=f"dlt_meta_int_test_{__version__}",
                                         dependencies=[runner_conf.remote_whl_path]
                                         )
            )
        ]
        dbfs_path = runner_conf.dbfs_tmp_path.replace("dbfs:/", "/dbfs/")
        return self.ws.jobs.create(
            name=f"dlt-meta-{runner_conf.run_id}",
            environemnts=dltmeta_environments,
            tasks=[
                jobs.Task(
                    task_key="setup_dlt_meta_pipeline_spec",
                    description="test",
                    environment_key="dl_meta_int_env",
                    timeout_seconds=0,
                    python_wheel_task=jobs.PythonWheelTask(
                        package_name="dlt_meta",
                        entry_point="run",
                        named_parameters={
                            "onboard_layer": "bronze",
                            "database": f"{runner_conf.uc_catalog_name}.{runner_conf.dlt_meta_schema}",
                            "onboarding_file_path":
                            f"{runner_conf.dbfs_tmp_path}/{self.base_dir}/conf/onboarding.json",
                            "silver_dataflowspec_table": "silver_dataflowspec_cdc",
                            "silver_dataflowspec_path": f"{self._install_folder()}/dltmeta/data/dlt_spec/silver",
                            "bronze_dataflowspec_table": "bronze_dataflowspec_cdc",
                            "import_author": "Ravi",
                            "version": "v1",
                            "bronze_dataflowspec_path": f"{self._install_folder()}/dltmeta/data/dlt_spec/bronze",
                            "overwrite": "True",
                            "env": runner_conf.env,
                            "uc_enabled": "True"
                        }
                    )
                ),
                jobs.Task(
                    task_key="publish_events",
                    description="test",
                    depends_on=[jobs.TaskDependency(task_key="setup_dlt_meta_pipeline_spec")],
                    notebook_task=jobs.NotebookTask(
                        notebook_path=f"{runner_conf.runners_nb_path}/runners/publish_events",
                        base_parameters={
                            "kafka_topic": self.args["kafka_topic_name"],
                            "kafka_broker": self.args["kafka_broker"],
                            "kafka_input_data": f"/{dbfs_path}/{self.base_dir}/resources/data/iot/iot.json"
                        }
                    )
                ),
                jobs.Task(
                    task_key="bronze_dlt_pipeline",
                    depends_on=[jobs.TaskDependency(task_key="publish_events")],
                    pipeline_task=jobs.PipelineTask(
                        pipeline_id=runner_conf.bronze_pipeline_id
                    ),
                ),
                jobs.Task(
                    task_key="validate_results",
                    description="test",
                    depends_on=[jobs.TaskDependency(task_key="bronze_dlt_pipeline")],
                    notebook_task=jobs.NotebookTask(
                        notebook_path=f"{runner_conf.runners_nb_path}/runners/validate",
                        base_parameters={
                            "run_id": runner_conf.run_id,
                            "uc_enabled": "True" ,
                            "uc_catalog_name": runner_conf.uc_catalog_name,
                            "bronze_schema": runner_conf.bronze_schema,
                            "output_file_path": f"/Workspace{runner_conf.test_output_file_path}"
                        }
                    )
                ),
            ]
        )

    def create_kafka_onboarding(self, runner_conf: DLTMetaRunnerConf):
        """Create kafka onboarding file."""
        with open(f"{runner_conf.kafka_template}") as f:
            onboard_obj = json.load(f)
        kafka_topic = self.args["kafka_topic_name"].lower()
        kafka_bootstrap_servers = self.args["kafka_broker"].lower()
        for data_flow in onboard_obj:
            for key, value in data_flow.items():
                if key == "source_details":
                    for source_key, source_value in value.items():
                        if 'dbfs_path' in source_value:
                            data_flow[key][source_key] = source_value.format(dbfs_path=runner_conf.dbfs_tmp_path)
                        if 'kafka_topic' in source_value:
                            data_flow[key][source_key] = source_value.format(kafka_topic=kafka_topic)
                        if 'kafka_bootstrap_servers' in source_value:
                            data_flow[key][source_key] = source_value.format(
                                kafka_bootstrap_servers=kafka_bootstrap_servers)
                if 'dbfs_path' in value:
                    data_flow[key] = value.format(dbfs_path=runner_conf.dbfs_tmp_path)
                elif 'run_id' in value:
                    data_flow[key] = value.format(run_id=runner_conf.run_id)
                elif 'uc_catalog_name' in value and 'bronze_schema' in value:
                    if runner_conf.uc_catalog_name:
                        data_flow[key] = value.format(
                            uc_catalog_name=runner_conf.uc_catalog_name,
                            bronze_schema=runner_conf.bronze_schema
                        )
                    else:
                        data_flow[key] = value.format(
                            uc_catalog_name=f"bronze_{runner_conf.run_id}",
                            bronze_schema=""
                        ).replace(".", "")
        with open(runner_conf.onboarding_file_path, "w") as onboarding_file:
            json.dump(onboard_obj, onboarding_file)

    def create_eventhub_onboarding(self, runner_conf: DLTMetaRunnerConf):
        """Create eventhub onboarding file."""
        with open(f"{runner_conf.eventhub_template}") as f:
            onboard_obj = json.load(f)
        eventhub_name = self.args["eventhub_name"].lower()
        eventhub_name_append_flow = self.args["eventhub_name_append_flow"].lower()
        eventhub_accesskey_name = self.args["eventhub_consumer_accesskey_name"].lower()
        eventhub_accesskey_secret_name = self.args["eventhub_accesskey_secret_name"].lower()
        eventhub_secrets_scope_name = self.args["eventhub_secrets_scope_name"].lower()
        eventhub_namespace = self.args["eventhub_namespace"].lower()
        eventhub_port = self.args["eventhub_port"].lower()
        for data_flow in onboard_obj:
            for key, value in data_flow.items():
                if key == "source_details":
                    for source_key, source_value in value.items():
                        if 'dbfs_path' in source_value:
                            data_flow[key][source_key] = source_value.format(dbfs_path=runner_conf.dbfs_tmp_path)
                        if 'uc_volume_path' in source_value:
                            data_flow[key][source_key] = source_value.format(uc_volume_path=runner_conf.uc_volume_path)
                        if 'eventhub_name' in source_value:
                            data_flow[key][source_key] = source_value.format(eventhub_name=eventhub_name)
                        if 'eventhub_accesskey_name' in source_value:
                            data_flow[key][source_key] = source_value.format(
                                eventhub_accesskey_name=eventhub_accesskey_name)
                        if 'eventhub_secrets_scope_name' in source_value:
                            data_flow[key][source_key] = source_value.format(
                                eventhub_secrets_scope_name=eventhub_secrets_scope_name)
                        if 'eventhub_accesskey_secret_name' in source_value:
                            data_flow[key][source_key] = source_value.format(
                                eventhub_accesskey_secret_name=eventhub_accesskey_secret_name)
                        if 'eventhub_nmspace' in source_value:
                            data_flow[key][source_key] = source_value.format(eventhub_nmspace=eventhub_namespace)
                        if 'eventhub_port' in source_value:
                            data_flow[key][source_key] = source_value.format(eventhub_port=eventhub_port)
                if key == 'bronze_append_flows':
                    counter = 0
                    for flows in value:
                        for flow_key, flow_value in flows.items():
                            if flow_key == "source_details":
                                for source_key, source_value in flows[flow_key].items():
                                    if 'dbfs_path' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            dbfs_path=runner_conf.dbfs_tmp_path)
                                    if 'uc_volume_path' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            uc_volume_path=runner_conf.uc_volume_path)
                                    if 'eventhub_name_append_flow' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            eventhub_name_append_flow=eventhub_name_append_flow)
                                    if 'eventhub_accesskey_name' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            eventhub_accesskey_name=eventhub_accesskey_name)
                                    if 'eventhub_secrets_scope_name' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            eventhub_secrets_scope_name=eventhub_secrets_scope_name)
                                    if 'eventhub_accesskey_secret_name' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            eventhub_accesskey_secret_name=eventhub_accesskey_secret_name)
                                    if 'eventhub_nmspace' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            eventhub_nmspace=eventhub_namespace)
                                    if 'eventhub_port' in source_value:
                                        data_flow[key][counter][flow_key][source_key] = source_value.format(
                                            eventhub_port=eventhub_port)
                        counter += 1
                if 'dbfs_path' in value:
                    data_flow[key] = value.format(dbfs_path=runner_conf.dbfs_tmp_path)
                elif 'uc_volume_path' in value:
                    data_flow[key] = value.format(uc_volume_path=runner_conf.uc_volume_path)
                elif 'run_id' in value:
                    data_flow[key] = value.format(run_id=runner_conf.run_id)
                elif 'uc_catalog_name' in value and 'bronze_schema' in value:
                    if runner_conf.uc_catalog_name:
                        data_flow[key] = value.format(
                            uc_catalog_name=runner_conf.uc_catalog_name,
                            bronze_schema=runner_conf.bronze_schema
                        )
                    else:
                        data_flow[key] = value.format(
                            uc_catalog_name=f"bronze_{runner_conf.run_id}",
                            bronze_schema=""
                        ).replace(".", "")

        with open(runner_conf.onboarding_file_path, "w") as onboarding_file:
            json.dump(onboard_obj, onboarding_file)

    def replace_eventhub_source_details_values(self,
                                               runner_conf,
                                               eventhub_name,
                                               eventhub_name_append_flow,
                                               eventhub_accesskey_name,
                                               eventhub_accesskey_secret_name,
                                               eventhub_secrets_scope_name,
                                               eventhub_namespace,
                                               eventhub_port,
                                               data_flow,
                                               key,
                                               source_key,
                                               source_value):
        if 'dbfs_path' in source_value:
            data_flow[key][source_key] = source_value.format(dbfs_path=runner_conf.dbfs_tmp_path)
        if 'eventhub_name' in source_value:
            data_flow[key][source_key] = source_value.format(eventhub_name=eventhub_name)
        if 'eventhub_name_append_flow' in source_value:
            data_flow[key][source_key] = source_value.format(eventhub_name_append_flow=eventhub_name_append_flow)
        if 'eventhub_accesskey_name' in source_value:
            data_flow[key][source_key] = source_value.format(
                eventhub_accesskey_name=eventhub_accesskey_name)
        if 'eventhub_secrets_scope_name' in source_value:
            data_flow[key][source_key] = source_value.format(
                eventhub_secrets_scope_name=eventhub_secrets_scope_name)
        if 'eventhub_accesskey_secret_name' in source_value:
            data_flow[key][source_key] = source_value.format(
                eventhub_accesskey_secret_name=eventhub_accesskey_secret_name)
        if 'eventhub_nmspace' in source_value:
            data_flow[key][source_key] = source_value.format(eventhub_nmspace=eventhub_namespace)
        if 'eventhub_port' in source_value:
            data_flow[key][source_key] = source_value.format(eventhub_port=eventhub_port)

    def initialize_uc_resources(self, runner_conf):
        '''Create UC schemas and volumes needed to run the integration tests'''
        SchemasAPI(self.ws.api_client).create(catalog_name=runner_conf.uc_catalog_name,
                                              name=runner_conf.dlt_meta_schema,
                                              comment="dlt_meta framework schema")
        SchemasAPI(self.ws.api_client).create(catalog_name=runner_conf.uc_catalog_name,
                                              name=runner_conf.bronze_schema,
                                              comment="bronze_schema")
        if runner_conf.source == "cloudfiles":
            SchemasAPI(self.ws.api_client).create(catalog_name=runner_conf.uc_catalog_name,
                                                  name=runner_conf.silver_schema,
                                                  comment="silver_schema")
        volume_info = self.ws.volumes.create(catalog_name=runner_conf.uc_catalog_name,
                                             schema_name=runner_conf.dlt_meta_schema,
                                             name=runner_conf.uc_volume_name,
                                             volume_type=VolumeType.MANAGED)
        runner_conf.volume_info = volume_info
        runner_conf.uc_volume_path = (f"/Volumes/{runner_conf.volume_info.catalog_name}/"
                                      f"{runner_conf.volume_info.schema_name}/{runner_conf.volume_info.name}/"
                                      )

    def generate_onboarding_file(self, runner_conf: DLTMetaRunnerConf):
        """Generate onboarding file from template."""
        match runner_conf.source:
            case "cloudfiles":
                self.create_cloudfiles_onboarding(runner_conf)
            case "eventhub":
                self.create_eventhub_onboarding(runner_conf)
            case "kafka":
                self.create_kafka_onboarding(runner_conf)

    def create_cloudfiles_onboarding(self, runner_conf: DLTMetaRunnerConf):
        """Create onboarding file when the source is cloudfiles by filling out the templates."""

        string_subs = {
            "{uc_volume_path}": runner_conf.uc_volume_path,
            "{uc_catalog_name}": runner_conf.uc_catalog_name,
            "{bronze_schema}": runner_conf.bronze_schema,
            "{silver_schema}": runner_conf.silver_schema,
            # "{run_id}": runner_conf.run_id,
        }

        # Open the onboarding templates and sub in the proper table locations, paths, etc.
        with open(f"{runner_conf.cloudfiles_template}", "r") as f:
            onboard_json = f.read()

        with open(f"{runner_conf.cloudfiles_A2_template}") as f:
            onboard_json_a2 = f.read()

        for key, val in string_subs.items():
            onboard_json = onboard_json.replace(key, val)
            onboard_json_a2 = onboard_json_a2.replace(key, val)

        with open(runner_conf.onboarding_file_path, "w") as onboarding_file:
            json.dump(json.loads(onboard_json), onboarding_file, indent=4)

        with open(runner_conf.onboarding_A2_file_path, "w") as onboarding_file_a2:
            json.dump(json.loads(onboard_json_a2), onboarding_file_a2, indent=4)

    def upload_files_to_databricks(self, runner_conf: DLTMetaRunnerConf):
        """
        Upload all necessary data, configuration files, wheels, and notebooks to run the
        integration tests
        """
        uc_vol_full_path = f"{runner_conf.uc_volume_path}/{runner_conf.int_tests_dir}"
        print(f"Integration test file upload to {uc_vol_full_path} starting...")
        # Upload the entire resources directory containing ddl and test data
        for root, dirs, files in os.walk(f"{runner_conf.int_tests_dir}/resources"):
            for file in files:
                with open(os.path.join(root, file), "rb") as content:
                    self.ws.files.upload(
                        file_path=f"{runner_conf.uc_volume_path}/{root}/{file}",
                        contents=content,
                        overwrite=True,
                    )

        # Upload all the JSONs in the conf directory, that is the generated onboarding JSONs and
        # the DQE JSONS
        for root, dirs, files in os.walk(f"{runner_conf.int_tests_dir}/conf"):
            if file.endswith("json"):
                for file in files:
                    with open(os.path.join(root, file), "rb") as content:
                        self.ws.files.upload(
                            file_path=f"{runner_conf.uc_volume_path}/{root}/{file}",
                            contents=content,
                            overwrite=True,
                        )
        print(f"Integration test file upload to {uc_vol_full_path} complete!!!")

        # Upload required notebooks for the given source
        print(f"Notebooks upload to {runner_conf.runners_nb_path} started...")
        self.ws.workspace.mkdirs(f"{runner_conf.runners_nb_path}/runners")

        for notebook in os.listdir(runner_conf.runners_full_local_path):
            local_path = os.path.join(runner_conf.runners_full_local_path, notebook)
            with open(local_path, "rb") as nb_file:
                self.ws.workspace.upload(
                    path=f"{runner_conf.runners_nb_path}/runners/{notebook}",
                    format=ImportFormat.SOURCE,
                    language=Language.PYTHON,
                    content=nb_file.read(),
                )
        print(f"Notebooks upload to {runner_conf.runners_nb_path} complete!!!")

        print("Python wheel upload starting...")
        # Upload the wheel to both the workspace and the uc volume
        runner_conf.remote_whl_path = f"{self.wsi._upload_wheel(uc_volume_path=runner_conf.uc_volume_path)}"
        print(f"Python wheel upload to {runner_conf.remote_whl_path} completed!!!")

    def init_dltmeta_runner_conf(self, runner_conf: DLTMetaRunnerConf):
        """Create testing metadata including schemas, volumes, and uploading necessary notebooks"""

        # Generate uc schemas, volumes and upload onboarding files
        self.initialize_uc_resources(runner_conf)
        self.generate_onboarding_file(runner_conf)
        self.upload_files_to_databricks(runner_conf)


    def create_cluster(self, runner_conf: DLTMetaRunnerConf):
        print("Cluster creation started...")
        if runner_conf.uc_catalog_name:
            mode = compute.DataSecurityMode.SINGLE_USER
            spark_confs = {}
        else:
            mode = compute.DataSecurityMode.NONE
            spark_confs = {}
        clstr = self.ws.clusters.create(
            cluster_name=f"dlt-meta-onboarding-cluster-{runner_conf.run_id}",
            spark_version=runner_conf.dbr_version,
            node_type_id=runner_conf.node_type_id,
            driver_node_type_id=runner_conf.node_type_id,
            num_workers=2,
            spark_conf=spark_confs,
            autotermination_minutes=30,
            spark_env_vars={
                "PYSPARK_PYTHON": "/databricks/python3/bin/python3",
                "WSFS_ENABLE": "false"
            },
            data_security_mode=mode
        ).result()
        print(f"Cluster creation finished. clusters={clstr}")
        print(f"Cluster creation finished. cluster_id={clstr.cluster_id}")
        print(f"host: {self.ws.config.host}, workspace_id: {self.ws.get_workspace_id()}")
        runner_conf.cluster_id = clstr.cluster_id
        webbrowser.open(f"{self.ws.config.host}/compute/clusters/{clstr.cluster_id}?o={self.ws.get_workspace_id()}")

    def download_test_results(self, runner_conf: DLTMetaRunnerConf):
        ws_output_file = self.ws.workspace.download(runner_conf.test_output_file_path)
        with open(f"integration_test_output_{runner_conf.run_id}.csv", "wb") as output_file:
            output_file.write(ws_output_file.read())

    def create_bronze_silver_dlt(self, runner_conf: DLTMetaRunnerConf):
        runner_conf.bronze_pipeline_id = self.create_dlt_meta_pipeline(
            f"dlt-meta-bronze-{runner_conf.run_id}",
            "bronze",
            "A1",
            runner_conf.bronze_schema,
            runner_conf)

        if runner_conf.source and runner_conf.source == "cloudfiles":
            runner_conf.bronze_pipeline_A2_id = self.create_dlt_meta_pipeline(
                f"dlt-meta-bronze-A2-{runner_conf.run_id}",
                "bronze",
                "A2",
                runner_conf.bronze_schema,
                runner_conf)

            runner_conf.silver_pipeline_id = self.create_dlt_meta_pipeline(
                f"dlt-meta-silver-{runner_conf.run_id}",
                "silver",
                "A1",
                runner_conf.silver_schema,
                runner_conf)

    def launch_workflow(self, runner_conf: DLTMetaRunnerConf):
        if runner_conf.source == "cloudfiles":
            created_job = self.create_cloudfiles_workflow_spec(runner_conf)
        elif runner_conf.source == "eventhub":
            created_job = self.create_eventhub_workflow_spec(runner_conf)
        elif runner_conf.source == "kafka":
            created_job = self.create_kafka_workflow_spec(runner_conf)
        runner_conf.job_id = created_job.job_id
        print(f"Job created successfully. job_id={created_job.job_id}, started run...")
        webbrowser.open(f"{self.ws.config.host}/jobs/{created_job.job_id}?o={self.ws.get_workspace_id()}")
        print(f"Waiting for job to complete. job_id={created_job.job_id}")
        run_by_id = self.ws.jobs.run_now(job_id=created_job.job_id).result()
        print(f"Job run finished. run_id={run_by_id}")
        return created_job

    def open_job_url(self, runner_conf, created_job):
        runner_conf.job_id = created_job.job_id
        url = f"{self.ws.config.host}/jobs/{created_job.job_id}?o={self.ws.get_workspace_id()}"
        self.ws.jobs.run_now(job_id=created_job.job_id)
        webbrowser.open(url)
        print(f"Job created successfully. job_id={created_job.job_id}, url={url}")

    def clean_up(self, runner_conf: DLTMetaRunnerConf):
        print("Cleaning up...")
        if runner_conf.job_id:
            self.ws.jobs.delete(runner_conf.job_id)
        if runner_conf.bronze_pipeline_id:
            self.ws.pipelines.delete(runner_conf.bronze_pipeline_id)
        if runner_conf.silver_pipeline_id:
            self.ws.pipelines.delete(runner_conf.silver_pipeline_id)
        if runner_conf.cluster_id:
            self.ws.clusters.delete(runner_conf.cluster_id)
        if runner_conf.dbfs_tmp_path:
            self.ws.dbfs.delete(runner_conf.dbfs_tmp_path, recursive=True)
        if runner_conf.uc_catalog_name:
            test_schema_list = [runner_conf.dlt_meta_schema, runner_conf.bronze_schema, runner_conf.silver_schema]
            schema_list = self.ws.schemas.list(catalog_name=runner_conf.uc_catalog_name)
            for schema in schema_list:
                if schema.name in test_schema_list:
                    print(f"Deleting schema: {schema.name}")
                    vol_list = self.ws.volumes.list(
                        catalog_name=runner_conf.uc_catalog_name,
                        schema_name=schema.name
                    )
                    for vol in vol_list:
                        print(f"Deleting volume:{vol.full_name}")
                        self.ws.volumes.delete(vol.full_name)
                    tables_list = self.ws.tables.list(
                        catalog_name=runner_conf.uc_catalog_name,
                        schema_name=schema.name
                    )
                    for table in tables_list:
                        print(f"Deleting table:{table.full_name}")
                        self.ws.tables.delete(table.full_name)
                    self.ws.schemas.delete(schema.full_name)
        print("Cleaning up complete!!!")

    def run(self, runner_conf: DLTMetaRunnerConf):

        self.init_dltmeta_runner_conf(runner_conf)
        exit()
        self.create_bronze_silver_dlt(runner_conf)
        self.launch_workflow(runner_conf)
        self.download_test_results(runner_conf)

        # try:
        #    self.init_dltmeta_runner_conf(runner_conf)
        #    exit()
        #    self.create_bronze_silver_dlt(runner_conf)
        #    self.launch_workflow(runner_conf)
        #    self.download_test_results(runner_conf)
        # except Exception as e:
        #    print(e)
        # finally:
        #     print("Cleaning up...")
        #     self.clean_up(runner_conf)


def get_workspace_api_client(profile=None) -> WorkspaceClient:
    """Get api client with config."""
    if profile:
        workspace_client = WorkspaceClient(profile=profile)
    else:
        workspace_client = WorkspaceClient(host=input('Databricks Workspace URL: '), token=input('Token: '))
    return workspace_client

def process_arguments() -> dict[str: str]:
    """
    Get, process, and validate the command line arguements

    Returns:
        A dictionary where the argument names are the keys and the values aredictionary values
    """

    print("Processing comand line arguments...")

    # Possible input arguments, organized as elements in a list like:
    # [argument, help message, type, required, choices (if applicable)]
    input_args = [
        # Generic arguments
        [
            "profile",
            "Provide databricks cli profile name, if not provide databricks_host and token",
            str,
            False,
            [],
        ],
        [
            "uc_catalog_name",
            "Provide databricks uc_catalog name, this is required to create volume, schema, table",
            str,
            True,
            [],
        ],
        [
            "cloud_provider_name",
            "Provide cloud provider name. Supported values are aws , azure , gcp",
            str.lower,
            True,
            ["aws", "azure", "gcp"],
        ],
        [
            "source",
            "Provide source type: cloudfiles, eventhub, kafka",
            str.lower,
            True,
            ["cloudfiles", "eventhub", "kafka"],
        ],
        # Eventhub arguments
        ["eventhub_name", "Provide eventhub_name e.g: iot", str, False, []],
        [
            "eventhub_name_append_flow",
            "Provide eventhub_name_append_flow e.g: iot_af",
            str,
            False,
            [],
        ],
        [
            "eventhub_producer_accesskey_name",
            "Provide access key that has write permission on the eventhub",
            str,
            False,
            [],
        ],
        [
            "eventhub_consumer_accesskey_name",
            "Provide access key that has read permission on the eventhub",
            str,
            False,
            [],
        ],
        [
            "eventhub_secrets_scope_name",
            "Provide eventhub_secrets_scope_name e.g: eventhubs_creds",
            str,
            False,
            [],
        ],
        [
            "eventhub_accesskey_secret_name",
            "Provide eventhub_accesskey_secret_name e.g: RootManageSharedAccessKey",
            str,
            False,
            [],
        ],
        [
            "eventhub_namespace",
            "Provide eventhub_namespace e.g: topic-standar",
            str,
            False,
            [],
        ],
        [
            "eventhub_port",
            "Provide eventhub_port e.g: 9093",
            str,
            False,
            [],
        ],
        # Kafka arguments
        [
            "kafka_topic_name",
            "Provide kafka topic name e.g: iot",
            str,
            False,
            [],
        ],
        ["kafka_broker", "Provide kafka broker e.g 127.0.0.1:9092", str, False, []],
    ]

    # Build cli parser
    parser = argparse.ArgumentParser()
    for arg in input_args:
        if arg[4]:
            parser.add_argument(f"--{arg[0]}", help=arg[1], type=arg[2], required=arg[3], choices=arg[4])
        else:
            parser.add_argument(f"--{arg[0]}", help=arg[1], type=arg[2], required=arg[3])
    args = vars(parser.parse_args())

    def check_cond_mandatory_arg(args, mandatory_args):
        """Post argument parsing check for conditionally required arguments"""
        for mand_arg in mandatory_args:
            if args[mand_arg] is None:
                raise Exception(f"Please provide '--{mand_arg}'")

    # Check for arguments that are required depending on the selected source
    if args["source"] == "eventhub":
        check_cond_mandatory_arg(
            args,
            [
                "eventhub_name",
                "eventhub_name_append_flow",
                "eventhub_producer_accesskey_name",
                "eventhub_consumer_accesskey_name",
                "eventhub_secrets_scope_name",
                "eventhub_namespace",
                "eventhub_port",
            ],
        )
    elif args["source"] == "kafka":
        check_cond_mandatory_arg(
            args,
            ["kafka_topic_name", "kafka_broker"],
        )

    print(f"Processing comand line arguments Complete: {args}")
    return args


def main():
    """Entry method to run integration tests."""
    args = process_arguments()
    workspace_client = get_workspace_api_client(args["profile"])
    integration_test_runner = DLTMETARunner(args, workspace_client, "integration_tests")
    runner_conf = integration_test_runner.init_runner_conf()
    integration_test_runner.run(runner_conf)

if __name__ == "__main__":
    main()
