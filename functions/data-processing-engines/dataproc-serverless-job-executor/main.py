# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import google.auth
import functions_framework
import requests
import datetime
import os
import json
from google.cloud import storage
from google.auth.transport.requests import Request

# --- Authentication Setup ---
credentials, project = google.auth.default()

function_name = os.environ.get('K_SERVICE')
BIGQUERY_PROJECT = os.environ.get('BIGQUERY_PROJECT')
# --- GCS Client ---
storage_client = storage.Client()


@functions_framework.http
def main(request):
    """
    Main function, likely triggered by an HTTP request. Extracts parameters, executes a dataproc serverless job
    , and reports the result status or job ID.

    Args:
        request: The incoming HTTP request object.

    Returns:
        str: The status of the query execution or the job ID (if asynchronous).
    """

    request_json = request.get_json(silent=True)
    print("event:" + str(request_json))

    try:
        workflow_properties = request_json.get('workflow_properties', None)
        workflow_name = request_json.get('workflow_name')
        job_name = request_json.get('job_name')
        query_variables = request_json.get('query_variables', None)
        job_id = request_json.get('job_id', None)

        jobs_definitions_bucket = request_json.get("workflow_properties", {}).get("jobs_definitions_bucket")
        extracted_params = {}
        if jobs_definitions_bucket:
            extracted_params = extract_params(
                bucket_name=jobs_definitions_bucket,
                job_name=job_name,
                function_name=function_name
            )

        status_or_job_id = execute_job_or_get_status(job_id, workflow_name, job_name, query_variables,
                                                     workflow_properties, extracted_params)

        if status_or_job_id.startswith('aef-'):
            print(f"Running Job, track it with Job ID: {status_or_job_id}")
        else:
            print(f"Call finished with status: {status_or_job_id}")

        return status_or_job_id
    except Exception as error:
        err_message = "Exception: " + repr(error)
        print(err_message)
        response = {
            "error": error.__class__.__name__,
            "message": repr(error)
        }
        return response


def execute_job_or_get_status(job_id, workflow_name, job_name, query_variables, workflow_properties, extracted_params):
    if job_id:
        return get_job_status(job_id, extracted_params)
    else:
        return create_batch_job(workflow_name, job_name, query_variables, workflow_properties, extracted_params)


def extract_params(bucket_name, job_name, function_name, encoding='utf-8'):
    """Extracts parameters from a JSON file.

    Args:
        bucket_name: Bucket containing the JSON parameters file .

    Returns:
        A dictionary containing the extracted parameters.
    """

    json_file_path = f'gs://{bucket_name}/{function_name}/{job_name}.json'

    parts = json_file_path.replace("gs://", "").split("/")
    bucket_name = parts[0]
    object_name = "/".join(parts[1:])
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    try:
        json_data = blob.download_as_bytes()
        params = json.loads(json_data.decode(encoding))
        return params
    except (google.cloud.exceptions.NotFound, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Error reading JSON file: {e}")
        return None


def create_batch_job(workflow_name, job_name, query_variables, workflow_properties, extracted_params):
    """
    calls a dataproc serverless job.

    Args:
        request_json (dict) : event dictionary
    Returns:
        str: Id or status of the dataproc serverless batch job
    """

    dataproc_serverless_project_id = extracted_params.get('dataproc_serverless_project_id')
    dataproc_serverless_region = extracted_params.get('dataproc_serverless_region')
    jar_file_location = extracted_params.get('jar_file_location')
    spark_history_server_cluster = extracted_params.get('spark_history_server_cluster')
    spark_app_main_class = extracted_params.get('spark_app_main_class')
    spark_args = extracted_params.get('spark_args')
    dataproc_serverless_runtime_version = extracted_params.get('dataproc_serverless_runtime_version')
    dataproc_service_account = extracted_params.get('dataproc_service_account')
    spark_app_properties = extracted_params.get('spark_app_properties')
    subnetwork = extracted_params.get("subnetwork")

    if isinstance(spark_app_properties, str):
        spark_app_properties = json.loads(spark_app_properties)

    credentials.refresh(Request())
    headers = {"Authorization": f"Bearer {credentials.token}"}

    curr_dt = datetime.datetime.now()
    timestamp = int(round(curr_dt.timestamp()))

    params = {
        "spark_batch": {
            "jar_file_uris": [jar_file_location],
            "main_class": spark_app_main_class,
            "args": spark_args
        },
        "runtime_config": {
            "version": dataproc_serverless_runtime_version,
            "properties": spark_app_properties
        },
        "environment_config": {
            "execution_config": {
                "service_account": dataproc_service_account,
                "subnetwork_uri": f"projects/{dataproc_serverless_project_id}/{subnetwork}"
            }
        }
    }

    # Check if spark_history_server_cluster is present and not null
    if spark_history_server_cluster:
        spark_history_server_cluster_path = f"projects/{dataproc_serverless_project_id}/regions/{dataproc_serverless_region}/clusters/{spark_history_server_cluster}"
        params["environment_config"]["peripherals_config"] = {
            "spark_history_server_config": {
                "dataproc_cluster": spark_history_server_cluster_path
            },
        }

    print(params)

    batch_id = f"aef-{timestamp}"

    url = (f"https://dataproc.googleapis.com/v1/projects/{dataproc_serverless_project_id}/"
           f"locations/{dataproc_serverless_region}/batches?batchId={batch_id}")

    response = requests.post(url, json=params, headers=headers)

    if response.status_code == 200:
        print("response::" + str(response))
        return batch_id
    else:
        error_message = f"Dataproc API CREATE request failed. Status code:{response.status_code}"
        print(error_message)
        print(response.text)
        raise Exception(error_message)


def get_job_status(job_id, extracted_params):
    """
    gets the status of a dataproc serverless job

    Args:
        request_json (dict) : event dictionary
    Returns:
        str: status of the dataproc serverless batch job
    """

    dataproc_serverless_project_id = extracted_params.get('dataproc_serverless_project_id')
    dataproc_serverless_region = extracted_params.get('dataproc_serverless_region')

    credentials.refresh(Request())
    headers = {"Authorization": f"Bearer {credentials.token}"}

    url = (f"https://dataproc.googleapis.com/v1/projects/{dataproc_serverless_project_id}/"
           f"locations/{dataproc_serverless_region}/batches/{job_id}")
    print("Url::::" + url)

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        print("response::" + str(response))
        return response.json().get("state")
    else:
        error_message = f"Dataproc API GET request failed. Status code:{response.status_code}"
        print(error_message)
        print(response.text)
        raise Exception(error_message)
