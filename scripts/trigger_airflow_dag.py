import json
import os
from datetime import datetime, timezone
from distutils.util import strtobool
from time import sleep

import requests
from requests.adapters import HTTPAdapter, Retry


def get_config_from_env():
    config = {
        "username": os.getenv("AIRFLOW_USERNAME"),
        "password": os.getenv("AIRFLOW_PASSWORD"),
        "base_path": os.getenv("AIRFLOW_BASE_PATH"),
        "dag_id": os.getenv("AIRFLOW_DAG_ID"),
        "poll_for_completion": bool(
            strtobool(os.getenv("POLL_FOR_COMPLETION", "true"))
        ),
        "timeout": int(os.getenv("POLLING_TIMEOUT", 10 * 60)),
        "polling_interval": int(os.getenv("POLLING_INTERVAL", 5)),
    }
    if None in config.values():
        raise Exception("Missing environment value.")
    return config


def get_session(username, password):
    retry_strategy = Retry(
        total=3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.auth = (username, password)
    session.headers.update({"Content-Type": "application/json"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_dag(session, base_path, dag_id):
    response = session.get(f"{base_path}/api/v1/dags/{dag_id}")
    if response.status_code != 200:
        raise Exception(f"DAG {dag_id} not found")
    print(f"Using dag {dag_id}")
    return response.json()


def create_new_dag_run(session, base_path, dag_id):
    body = {"execution_date": datetime.now(timezone.utc).isoformat(), "conf": {}}
    response = session.post(
        f"{base_path}/api/v1/dags/{dag_id}/dagRuns", data=json.dumps(body)
    )
    print(f"Created new dag run: {response.json()['dag_run_id']}")
    return response.json()


def poll_dag_run_for_completion(
    session,
    base_path,
    dag_id,
    dag_run_id,
    polling_interval=5,
    elapsed_time=0,
    timeout=10 * 60,
):
    response = session.get(
        f"{base_path}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
    )
    task_instances = response.json()["task_instances"]
    task_id_states = []
    for task_instance in task_instances:
        state = task_instance["state"]
        task_id = task_instance["task_id"]
        print(f"task '{task_id}' in state '{state}'")
        task_id_states.append((task_id, state))
    tasks_completed = {state for _, state in task_id_states}.issubset(
        {"failed", "success", "upstream_failed", "skipped"}
    )
    if elapsed_time > timeout:
        raise Exception(
            f"Timed out waiting for tasks to complete. See {base_path}/dags/{dag_id}/graph for current status."
        )
    if not tasks_completed:
        sleep(polling_interval)
        elapsed_time += polling_interval
        print(f"Time {elapsed_time}/{timeout}")
        poll_dag_run_for_completion(
            session,
            base_path,
            dag_id,
            dag_run_id,
            polling_interval=polling_interval,
            elapsed_time=elapsed_time,
            timeout=timeout,
        )
    if "failed" in [state for _, state in task_id_states]:
        raise Exception(
            f"A task failed. See {base_path}/dags/{dag_id}/graph for more information."
        )


def run():
    config = get_config_from_env()
    with get_session(config["username"], config["password"]) as session:
        get_dag(session, config["base_path"], config["dag_id"])
        dag_run = create_new_dag_run(session, config["base_path"], config["dag_id"])
        if config["poll_for_completion"]:
            poll_dag_run_for_completion(
                session,
                config["base_path"],
                config["dag_id"],
                dag_run["dag_run_id"],
                timeout=config["timeout"],
                polling_interval=config["polling_interval"],
            )


if __name__ == "__main__":
    run()
